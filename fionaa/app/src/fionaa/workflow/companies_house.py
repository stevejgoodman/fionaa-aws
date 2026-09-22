"""Companies house workflow stage."""
from __future__ import annotations

import json
from datetime import date
from typing import Literal
from langgraph.runtime import Runtime
from langgraph.types import Command
from langchain.agents import create_agent
from langchain.messages import HumanMessage, ToolMessage
from fionaa.model.load import load_model
from fionaa.groundedness import check_companies_house_grounding
from fionaa.redaction import redact_tool_calls
from fionaa.prompts import COMPANIES_HOUSE_PROMPT
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.assessments import CompaniesHouseResult
from .common import tools_for
from .resilience import CircuitBreaker, ConcurrencyLimiter, TRANSIENT_ERRORS, ainvoke_resilient

# One breaker/limiter per process for this node's external dependency
# (Companies House, via the CompaniesHouse___* Gateway tools) -- see
# resilience.py's CircuitBreaker/ConcurrencyLimiter docstrings for why
# neither needs to be shared across workers.
_BREAKER = CircuitBreaker(name="companies_house")
_LIMITER = ConcurrencyLimiter(
    name="companies_house", env_var="FIONAA_COMPANIES_HOUSE_MAX_CONCURRENT", default=10,
)


async def check_companies_house(
    state: ApplicationState, runtime: Runtime[AgentContext]
) -> Command[Literal["policy_check", "reject_no_company"]]:
    application = state["application"]

    agent = create_agent(
        model=runtime.context.model if runtime.context.model is not None else load_model(),
        # geo-target___CheckSameArea is included alongside the CompaniesHouse
        # tools because COMPANIES_HOUSE_PROMPT explicitly instructs the agent
        # to use it (reconciling a loosely-specified applicant address
        # against the Companies House registered office address before
        # treating the difference as a red flag) — it's part of this node's
        # own verification, not a separate location check. A confirmed match
        # here already establishes "registered in the UK" (see
        # policies/general.md): Companies House is a UK-only register, so
        # there is nothing left for a separate UK-based check to establish.
        tools=tools_for(runtime.context.tools, "CompaniesHouse___", "geo-target___CheckSameArea"),
        system_prompt=COMPANIES_HOUSE_PROMPT,
        response_format=CompaniesHouseResult,
    )

    # Computed fresh per invocation, same reason check_against_policy does
    # this rather than a module-level constant -- see POLICY_CHECK_PROMPT's
    # own TODAY'S DATE usage. Without this, the model falls back to its own
    # training-era sense of "now" and can misjudge a genuine, recent
    # Companies House date (e.g. this year's incorporation) as anomalous or
    # synthetic -- see COMPANIES_HOUSE_PROMPT's "Trust TODAY'S DATE" section.
    today = date.today().isoformat()

    try:
        response = await ainvoke_resilient(
            agent,
            {"messages": [HumanMessage(
                content=f"{json.dumps(application)}\n\nTODAY'S DATE: {today}"
            )]},
            breaker=_BREAKER,
            limiter=_LIMITER,
        )
    except TRANSIENT_ERRORS as exc:
        unavailable = {"found": False, "confidence": "low", "summary": "Company lookup was unavailable; internal verification is required."}
        runtime.context.store.put_json("companies_house/result.json", {
            **unavailable, "tool_calls": [], "grounded": False,
            "grounding_reasons": [f"lookup_unavailable: {exc}"],
        })
        return Command(update={"companies_house": unavailable, "companies_house_found": False,
                               "company_lookup_failed": True}, goto="reject_no_company")
    result: CompaniesHouseResult = response["structured_response"]
    companies_house_result = result.model_dump()

    # Tool calls the agent made along the way (CompaniesHouse___*,
    # geo-target___CheckSameArea) are evidence too — same extraction
    # check_against_policy/check_financial_assessment use for their
    # (local, non-MCP) tools. Kept out of companies_house_result/state so
    # downstream prompts (check_financial_assessment, synthesize_decision)
    # keep seeing exactly the same CompaniesHouseResult shape as before —
    # tool_calls is only added to the S3 evidence artifact.
    #
    # redact_tool_calls strips street/building-level address detail out of
    # the raw CompaniesHouse___* results before they're persisted -- this
    # is the one place that detail can leak, since it bypasses
    # COMPANIES_HOUSE_PROMPT's own "never write the street-level address
    # into your summary" instruction entirely (that instruction only
    # constrains companies_house_result.summary, not this raw tool output).
    raw_tool_calls = [
        {"tool": m.name, "result": m.content}
        for m in response["messages"]
        if isinstance(m, ToolMessage)
    ]

    # Runtime groundedness check: found=True gates the entire downstream
    # graph (policy_check/financial_assessment/web_search/synthesize_decision
    # all only run on this branch), so a fabricated match here is the
    # highest-leverage hallucination this agent could produce. Checked against the
    # raw tool calls -- company_number isn't touched by redact_tool_calls
    # below, but this runs first regardless -- see groundedness.py's
    # docstring for exactly what is and isn't checked, and why this only
    # ever downgrades found=True, never upgrades found=False.
    grounding = check_companies_house_grounding(application, result.found, raw_tool_calls)
    if not grounding.grounded:
        companies_house_result["found"] = False
        companies_house_result["summary"] = (
            companies_house_result["summary"]
            + " [runtime groundedness check overrode found=True to found=False: "
            + "; ".join(grounding.reasons)
            + "]"
        )

    # Tool calls the agent made along the way (CompaniesHouse___*,
    # geo-target___CheckSameArea) are evidence too — same extraction
    # check_against_policy/check_financial_assessment use for their
    # (local, non-MCP) tools. Kept out of companies_house_result/state so
    # downstream prompts (check_financial_assessment, synthesize_decision)
    # keep seeing exactly the same CompaniesHouseResult shape as before —
    # tool_calls is only added to the S3 evidence artifact.
    #
    # redact_tool_calls strips street/building-level address detail out of
    # the raw CompaniesHouse___* results before they're persisted -- this
    # is the one place that detail can leak, since it bypasses
    # COMPANIES_HOUSE_PROMPT's own "never write the street-level address
    # into your summary" instruction entirely (that instruction only
    # constrains companies_house_result.summary, not this raw tool output).
    tool_calls = redact_tool_calls(raw_tool_calls)

    # Only an error on a *core identity* call invalidates the match -- these
    # four are what actually establish "this company and this applicant
    # exist and are linked" (see COMPANIES_HOUSE_PROMPT Step 2). The
    # remaining CompaniesHouse___* calls (insolvency, charges, filing
    # history, registered-office-address) are supplementary detail: an
    # active company with a clean record commonly 404s on e.g.
    # getCompanyInsolvency (nothing to return), which the Gateway surfaces
    # as a tool error rather than an empty result -- that is not a reason to
    # discard an otherwise well-identified match. Previously any
    # CompaniesHouse___* error forced found=False here with no explanation
    # surfaced anywhere, silently rejecting genuine, clean identity matches.
    _CORE_LOOKUP_TOOLS = (
        "CompaniesHouse___searchCompanies", "CompaniesHouse___getCompanyProfile",
        "CompaniesHouse___listCompanyOfficers", "CompaniesHouse___listPersonsWithSignificantControl",
    )
    core_tool_errored = any(
        isinstance(message, ToolMessage) and message.name in _CORE_LOOKUP_TOOLS
        and message.status == "error" for message in response["messages"]
    )
    secondary_tool_errors = sorted({
        message.name.removeprefix("CompaniesHouse___")
        for message in response["messages"]
        if isinstance(message, ToolMessage) and message.name
        and message.name.startswith("CompaniesHouse___") and message.name not in _CORE_LOOKUP_TOOLS
        and message.status == "error"
    })

    lookup_failed = (
        not grounding.grounded
        or core_tool_errored
        or not any(call["tool"].startswith("CompaniesHouse___") for call in raw_tool_calls)
    )
    if lookup_failed:
        companies_house_result["found"] = False
    elif secondary_tool_errors:
        companies_house_result["summary"] = (
            companies_house_result["summary"]
            + " [note: the following secondary Companies House checks could not be completed: "
            + ", ".join(secondary_tool_errors) + "]"
        )

    # save result back to application store
    runtime.context.store.put_json(
        "companies_house/result.json",
        {**companies_house_result, "tool_calls": tool_calls, "grounded": grounding.grounded,
         "grounding_reasons": grounding.reasons},
    )

    found = companies_house_result["found"]
    update = {"companies_house": companies_house_result, "companies_house_found": found}
    if lookup_failed:
        update["company_lookup_failed"] = True
    return Command(
        update=update,
        goto="policy_check" if found else "reject_no_company",
    )
