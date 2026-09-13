"""Decision workflow stage."""
from __future__ import annotations

import json
from typing import Any
from langgraph.runtime import Runtime
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from fionaa.model.load import load_model
from fionaa.prompts import DECISION_SYNTHESIS_PROMPT
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.assessments import FinalDecisionResult


def reject_no_company(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Terminal node for the companies_house branch that found no matching
    company. Writes a single artifact recording why the run stopped early —
    including the policy_check outcome gathered before this point, so a
    reviewer doesn't have to reconstruct the reasoning from separate
    per-node artifacts."""
    final_decision = {
        "outcome": "rejected",
        "reason": "companies_house_no_match",
        "policy_check": state.get("policy_check"),
        "companies_house": state.get("companies_house"),
    }
    runtime.context.store.put_json("decision/result.json", final_decision)
    return {"final_decision": final_decision}


async def synthesize_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Last node on the success path, after web_search. Without this node the
    graph terminated at END having gathered policy_check/companies_house/
    financial_assessment/web_search as separate evidence artifacts but never
    rolled them up into an outcome — only reject_no_company ever wrote a
    final_decision. This node closes that gap by weighing all four earlier
    findings (already in state; nothing is re-fetched or re-assessed here)
    into a single approved/rejected/referred verdict."""
    application = state["application"]
    policy_check = state.get("policy_check")
    companies_house = state.get("companies_house")
    financial_assessment = state.get("financial_assessment")
    web_search = state.get("web_search")

    agent = create_agent(
        model=runtime.context.model if runtime.context.model is not None else load_model(),
        tools=[],
        system_prompt=DECISION_SYNTHESIS_PROMPT,
        response_format=FinalDecisionResult,
    )

    response = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content=f"APPLICATION:\n{json.dumps(application)}\n\n"
                    f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}\n\n"
                    f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
                    f"FINANCIAL ASSESSMENT:\n{json.dumps(financial_assessment)}\n\n"
                    f"WEB SEARCH FINDINGS:\n{json.dumps(web_search)}"
                )
            ]
        }
    )
    result: FinalDecisionResult = response["structured_response"]

    # Mirrors reject_no_company's shape: the decision plus the upstream
    # findings that produced it, so a reviewer doesn't have to reconstruct
    # the reasoning from separate per-node artifacts.
    final_decision = {
        **result.model_dump(),
        "policy_check": policy_check,
        "companies_house": companies_house,
        "financial_assessment": financial_assessment,
        "web_search": web_search,
    }
    runtime.context.store.put_json("decision/proposed.json", final_decision)
    return {"proposed_decision": final_decision}
