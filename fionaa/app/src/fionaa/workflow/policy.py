"""Policy workflow stage."""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from langgraph.runtime import Runtime
from langchain.agents import create_agent
from langchain.messages import HumanMessage, ToolMessage
from fionaa.model.load import load_model
from fionaa.check_tools import CHECK_TOOLS_POOL
from fionaa.policy_loader import load_check_tool_names, load_policy_text
from fionaa.redaction import redact_tool_calls
from fionaa.prompts import POLICY_CHECK_PROMPT
from fionaa.domain.applications import LoanType
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.assessments import PolicyCheckResult
from .common import tools_for


async def check_against_policy(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    application = state["application"]
    loan_type = LoanType(application["loan_type"])
    bank_statements = state.get("bank_statements", [])

    # loan_type is already known from the application — no KB search needed
    # to find "the correct loan policy document"; load it directly by key.
    # See policy_loader.py.
    policy_text = load_policy_text(loan_type)

    # Which deterministic calculation tools this agent sees is declared in
    # the policy text itself (a `<!-- checks: ... -->` comment, stripped
    # before load_policy_text returns) — scoped per loan type the same way
    # `tools_for` scopes MCP tools for the other agentic nodes, just against
    # the local CHECK_TOOLS_POOL instead of runtime.context.tools.
    # general.md declares check_bank_statements_recent_and_sufficient, so
    # every loan type gets it regardless of that type's own policy.md.
    tool_names = load_check_tool_names(loan_type)
    agent = create_agent(
        model=runtime.context.model if runtime.context.model is not None else load_model(),
        tools=tools_for(CHECK_TOOLS_POOL, *tool_names),
        system_prompt=POLICY_CHECK_PROMPT,
        response_format=PolicyCheckResult,
    )

    # Computed fresh per invocation, not at module import time, so it can't
    # go stale in a long-lived process.
    today = date.today().isoformat()

    # Explicit cachePoint after POLICY, separate from load_model()'s
    # cache_control (which only ever sees this as one message and would
    # place its own cachePoint at the very end -- after the never-repeated
    # application/bank-statement/date tail, caching nothing useful). This
    # cachePoint instead ends right after policy_text, which is identical
    # for every application of this loan_type, so it gets reused across
    # requests independently of the dynamic tail that follows it.
    response = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=[
                        {"type": "text", "text": f"POLICY:\n{policy_text}"},
                        {"cachePoint": {"type": "default"}},
                        {
                            "type": "text",
                            "text": (
                                f"\n\nAPPLICATION:\n{json.dumps(application)}\n\n"
                                f"BANK STATEMENT END DATES:\n"
                                f"{json.dumps([s['end_date'] for s in bank_statements])}\n\n"
                                f"TODAY'S DATE: {today}"
                            ),
                        },
                    ]
                )
            ]
        }
    )
    messages = response["messages"]
    result: PolicyCheckResult = response["structured_response"]
    loan_result = result.model_dump()

    # Tool calls the agent made along the way are evidence too — captured
    # here (from the response) rather than by the tools themselves, so the
    # tools in check_tools.py can stay plain, runtime-free functions.
    tool_calls = redact_tool_calls([
        {"tool": m.name, "result": m.content}
        for m in messages
        if isinstance(m, ToolMessage)
    ])

    runtime.context.store.put_json(
        "policy_check/result.json", {**loan_result, "tool_calls": tool_calls}
    )
    return {"policy_check": loan_result}
