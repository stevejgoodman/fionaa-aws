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

WEBSEARCH_TOOL_NAME = "websearch-target___WebSearch"

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

    response = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=f"Company: {company_name}\n\n"
                    f"APPLICATION FORM DETAILS:\n{json.dumps(application_context)}\n\n"
                    f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}"
                )
            ]
        }
    )

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
