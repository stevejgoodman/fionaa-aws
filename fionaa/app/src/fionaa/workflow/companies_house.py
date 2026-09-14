"""Companies house workflow stage."""
from __future__ import annotations

import json
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

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=json.dumps(application))]}
    )
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

    # save result back to application store
    runtime.context.store.put_json(
        "companies_house/result.json",
        {**companies_house_result, "tool_calls": tool_calls, "grounded": grounding.grounded,
         "grounding_reasons": grounding.reasons},
    )

    found = companies_house_result["found"]
    return Command(
        update={"companies_house": companies_house_result, "companies_house_found": found},
        goto="policy_check" if found else "reject_no_company",
    )
