"""Financial workflow stage."""
from __future__ import annotations

import json
from typing import Any
from langgraph.runtime import Runtime
from langchain.agents import create_agent
from langchain.messages import HumanMessage, ToolMessage
from fionaa.model.load import load_model
from fionaa.check_tools import CHECK_TOOLS_POOL, cross_check_financial_figures
from fionaa.redaction import redact_tool_calls
from fionaa.prompts import FINANCIAL_ASSESSMENT_PROMPT
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.assessments import FinancialAssessmentResult, FinancialCrossCheckSummary
from .common import tools_for


async def check_financial_assessment(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Runs after companies_house (only reached on the `found` branch) so it
    has both the application form and an independent Companies House lookup
    to cross-check, plus compute_monthly_repayment from CHECK_TOOLS_POOL for
    a deterministic affordability figure, and
    check_tools.cross_check_financial_figures for a deterministic
    turnover/profit comparison against annual_accounts (see that function's
    docstring) -- the agent copies the latter through rather than computing
    its own delta. Also reads annual_accounts/bank_statements (loaded by
    load_application) as further cross-source evidence for the
    consistency/affordability checks -- see FINANCIAL_ASSESSMENT_PROMPT.
    Whether the bank statements are sufficiently numerous/recent per general
    policy is check_against_policy's job, not this node's -- it's given
    whatever documents load_application found, however many/recent that
    turns out to be."""
    application = state["application"]
    companies_house = state.get("companies_house")
    policy_check = state.get("policy_check")
    annual_accounts = state.get("annual_accounts", [])
    bank_statements = state.get("bank_statements", [])

    # Deterministic turnover/profit comparison, computed here rather than
    # left to the model's own arithmetic-over-prose reasoning — see
    # check_tools.cross_check_financial_figures and
    # FINANCIAL_ASSESSMENT_PROMPT's "Consistency checks" section. The agent
    # copies this through into its `cross_check` output field verbatim.
    cross_check = cross_check_financial_figures(application, annual_accounts, companies_house)
    cross_check_payload = [
        FinancialCrossCheckSummary(**vars(comparison)).model_dump()
        for comparison in cross_check.comparisons
    ]

    agent = create_agent(
        model=runtime.context.model if runtime.context.model is not None else load_model(),
        tools=tools_for(CHECK_TOOLS_POOL, "compute_monthly_repayment"),
        system_prompt=FINANCIAL_ASSESSMENT_PROMPT,
        response_format=FinancialAssessmentResult,
    )

    response = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=f"APPLICATION:\n{json.dumps(application)}\n\n"
                    f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
                    f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}\n\n"
                    f"ANNUAL ACCOUNTS:\n{json.dumps(annual_accounts)}\n\n"
                    f"BANK STATEMENTS:\n{json.dumps(bank_statements)}\n\n"
                    f"CROSS-CHECK RESULT:\n{json.dumps(cross_check_payload)}"
                )
            ]
        }
    )
    messages = response["messages"]
    result: FinancialAssessmentResult = response["structured_response"]
    financial_assessment_result = result.model_dump()

    tool_calls = redact_tool_calls([
        {"tool": m.name, "result": m.content}
        for m in messages
        if isinstance(m, ToolMessage)
    ])

    runtime.context.store.put_json(
        "financial_assessment/result.json",
        {**financial_assessment_result, "tool_calls": tool_calls},
    )
    return {"financial_assessment": financial_assessment_result}
