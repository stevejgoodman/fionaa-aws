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


def human_review_report(proposed: dict, validation: dict | None = None) -> dict:
    """The workflow supplies a recommendation; only a human determines the outcome."""
    report = {
        **{key: value for key, value in proposed.items()
           if key not in {"outcome", "reason", "rationale"}},
        "outcome": "pending_human_review",
        "ai_recommendation": {key: proposed[key] for key in ("outcome", "reason", "rationale")},
    }
    if validation is not None:
        report["validation"] = validation
    return report


def reject_no_company(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Terminal node for the companies_house branch that found no matching
    company. companies_house runs first in graph.py (before policy_check),
    so there's no policy_check result yet to include here -- an
    unidentified company makes the rest of the assessment moot, so nothing
    downstream (including policy_check) ever runs on this branch."""
    final_decision = {
        "outcome": "referred",
        "reason": "companies_house_no_match",
        "rationale": "Company identity could not be confirmed; human review is required.",
        "companies_house": state.get("companies_house"),
    }
    final_decision = human_review_report(final_decision, {
        "status": "not_checked", "check_status": "company_not_confirmed", "claims": [],
    })
    runtime.context.store.put_json("decision/result.json", final_decision)
    return {"final_decision": final_decision}


async def synthesize_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Combine the four assessments into an advisory AI recommendation."""
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
