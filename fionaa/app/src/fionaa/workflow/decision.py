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
    """Prepare a review report for the companies_house branch with no matching
    company. companies_house runs first in graph.py (before policy_check),
    so there's no policy_check result yet to include here -- an
    unidentified company makes the rest of the assessment moot, so nothing
    assessment (including policy_check) runs on this branch. Documentation
    triage still runs afterwards using the submission inventory.

    graph.py routes here on found=False, which check_companies_house sets
    for two very different reasons -- state["company_lookup_failed"]
    distinguishes them: unset/False means the lookup actually ran and
    Companies House confirmed no match (a real negative signal about the
    company); True means the lookup itself didn't complete -- a
    ThrottlingException, exhausted retries, an open circuit
    (workflow/resilience.py), or the Gateway tool erroring -- so nothing
    was actually learned about the company one way or the other. Reporting
    both as "companies_house_no_match" would read a transient capacity/
    outage blip as if it were a real finding, which is worse for a reviewer
    than just saying the check couldn't be completed."""
    lookup_failed = bool(state.get("company_lookup_failed"))
    final_decision = {
        "outcome": "referred",
        "reason": "companies_house_lookup_unavailable" if lookup_failed else "companies_house_no_match",
        "rationale": (
            "Company lookup could not be completed; human review is required to verify company identity manually."
            if lookup_failed else
            "Company identity could not be confirmed; human review is required."
        ),
        "companies_house": state.get("companies_house"),
    }
    final_decision = human_review_report(final_decision, {
        "status": "not_checked",
        "check_status": "lookup_unavailable" if lookup_failed else "company_not_confirmed",
        "claims": [],
    })
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
