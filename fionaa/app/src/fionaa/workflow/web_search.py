"""Web search workflow stage."""
from __future__ import annotations

import json
from typing import Any
from langgraph.runtime import Runtime
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.messages import HumanMessage, ToolMessage
from fionaa.model.load import load_model
from fionaa.redaction import redact_tool_calls
from fionaa.prompts import WEB_SEARCH_PROMPT
from fionaa.workflow.state import ApplicationState, AgentContext
from .common import tools_for
from .resilience import CircuitBreaker, ConcurrencyLimiter, TRANSIENT_ERRORS, ainvoke_resilient

WEBSEARCH_TOOL_NAME = "websearch-target___WebSearch"

# One breaker/limiter per process for this node's external dependency (the
# websearch-target Gateway tool) -- see resilience.py's
# CircuitBreaker/ConcurrencyLimiter docstrings for why neither needs to be
# shared across workers. Capped lower than companies_house's default (5 vs
# 10): a single web_search node run can itself issue up to
# WEBSEARCH_RUN_LIMIT=20 sequential WebSearch calls, so it already puts more
# sustained load on its dependency per concurrent application than a
# companies_house lookup does.
_BREAKER = CircuitBreaker(name="web_search")
_LIMITER = ConcurrencyLimiter(name="web_search", env_var="FIONAA_WEB_SEARCH_MAX_CONCURRENT", default=5)

# web_search never gates a hard routing decision the way companies_house's
# `found` does (see the groundedness comment below), so on an unrecoverable
# failure the node degrades to this string rather than failing the whole
# graph run -- synthesize_decision/validate_final_decision still see a
# web_search value, just one flagged as unavailable for a human reviewer.
WEB_SEARCH_UNAVAILABLE = "Web search was unavailable; no online corroboration could be gathered."

# Caps runaway searching for a thin-web-presence applicant (e.g. a common
# name with no matching online profile) from turning into dozens of
# ever-broader retries -- exit_behavior="continue" blocks further
# WebSearch calls once the limit is hit but lets the model still produce a
# final summary from whatever it already found, rather than erroring the
# node out entirely.
WEBSEARCH_RUN_LIMIT = 20


async def search_web(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    application = state["application"]
    company_name = application["company_name"]
    companies_house = state.get("companies_house")

    # Application-form fields are self-reported and may differ from (or add
    # detail missing from) Companies House's own records -- e.g. a director's
    # current residential address vs. a company's registered office, or a
    # trading name Companies House doesn't know about. Included as additional
    # disambiguating context, not a replacement for the Companies House
    # findings above. Pulled via .get() since not every stored application
    # populates every optional field.
    application_context = {
        "applicant_name": application.get("applicant_name"),
        "company_name": company_name,
        "company_address": application.get("company_address"),
        "director_residential_address": application.get("director_residential_address"),
    }

    agent = create_agent(
        model=runtime.context.model if runtime.context.model is not None else load_model(),
        tools=tools_for(runtime.context.tools, WEBSEARCH_TOOL_NAME),
        system_prompt=WEB_SEARCH_PROMPT,
        middleware=[
            ToolCallLimitMiddleware(
                tool_name=WEBSEARCH_TOOL_NAME,
                run_limit=WEBSEARCH_RUN_LIMIT,
                exit_behavior="continue",
            )
        ],
    )

    try:
        response = await ainvoke_resilient(
            agent,
            {
                "messages": [
                    HumanMessage(
                        content=f"Company: {company_name}\n\n"
                        f"APPLICATION FORM DETAILS:\n{json.dumps(application_context)}\n\n"
                        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}"
                    )
                ]
            },
            breaker=_BREAKER,
            limiter=_LIMITER,
        )
    except TRANSIENT_ERRORS as exc:
        runtime.context.store.put_json(
            "web_search/result.json",
            {"result": WEB_SEARCH_UNAVAILABLE, "tool_calls": [], "grounded": False,
             "grounding_reasons": [f"lookup_unavailable: {exc}"]},
        )
        return {"web_search": WEB_SEARCH_UNAVAILABLE}

    web_search_result = response["messages"][-1].content

    # Same tool-call extraction as check_companies_house, for the same
    # reason -- websearch-target___WebSearch invocations are evidence too.
    # Kept out of the web_search state value (still the bare result string,
    # as synthesize_decision expects); only added to the S3 evidence
    # artifact, which changes shape here from a bare string to a dict.
    tool_calls = redact_tool_calls([
        {"tool": m.name, "result": m.content}
        for m in response["messages"]
        if isinstance(m, ToolMessage)
    ])

    # Audit-only groundedness signal, not enforced (unlike
    # check_companies_house_grounding): a zero-tool-call web_search_result
    # means the model produced a summary without ever actually searching --
    # worth surfacing in evidence, but web_search never gates a hard branch
    # decision the way companies_house's `found` does, so there's no
    # downstream routing to override here.
    runtime.context.store.put_json(
        "web_search/result.json",
        {"result": web_search_result, "tool_calls": tool_calls, "grounded": bool(tool_calls)},
    )
    return {"web_search": web_search_result}
