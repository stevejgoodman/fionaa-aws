"""Attach policy checks to claims in the report for a human reviewer."""
from __future__ import annotations

import asyncio
from collections import Counter

from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.domain.applications import LoanType
from langgraph.runtime import Runtime
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.workflow.decision import human_review_report


def _facts(state: ApplicationState) -> dict:
    # Keep self-reported inputs distinguishable from external findings.
    return {
        "application_self_reported": state["application"],
        "annual_accounts": state.get("annual_accounts", []),
        "bank_statements": state.get("bank_statements", []),
        "companies_house_findings": state.get("companies_house"),
        "financial_assessment": state.get("financial_assessment"),
    }


def _claim(source: str, field: str, text: str) -> dict:
    return {
        "source": source,
        "source_path": f"/{source}/{field}",
        "claim": {"summary": text},
    }


def _policy_check_claims(policy_check: dict) -> list[dict]:
    claims = [_claim("policy_check", "eligible",
                     "This application's overall eligibility under "
                     f"the covered policy is: {policy_check['eligible']}.")]
    for field in ("clause_findings", "documentation_gaps"):
        claims.extend(_claim("policy_check", f"{field}/{index}", value)
                      for index, value in enumerate(policy_check.get(field, [])))
    if policy_check.get("summary"):
        claims.append(_claim("policy_check", "summary", policy_check["summary"]))
    return claims


def _final_decision_claims(proposed: dict) -> list[dict]:
    return [
        _claim("ai_recommendation", "outcome",
               f"The recommended outcome on this application is: {proposed['outcome']}."),
        _claim("ai_recommendation", "reason", proposed["reason"]),
        _claim("ai_recommendation", "rationale", proposed["rationale"]),
    ]


async def _validate_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Check each assessment field separately; no result determines the loan outcome.

    source_path is a JSON pointer into decision/result.json. Evidence is the
    shared input snapshot, not a claim that the checker used every source.
    Free-text fields can contain multiple assertions; raw findings retain the
    checker's details for those assertions.
    """
    claims = (_policy_check_claims(state["policy_check"])
              + _final_decision_claims(state["proposed_decision"]))
    loan_type = LoanType(state["application"]["loan_type"])
    checker = runtime.context.policy_checker or PolicyConsistencyChecker.from_environment()
    policy = load_policy_text(loan_type)
    facts = _facts(state)
    results = await asyncio.gather(*(
        checker.check(loan_type.value, policy, facts, item["claim"]) for item in claims
    ))
    annotations = []
    for claim, result in zip(claims, results):
        # The checker's legacy passed flag is not a decision or report gate.
        details = {key: value for key, value in result.items() if key != "passed"}
        status = details["status"]
        if status not in {"valid", "invalid", "inconclusive"}:
            details.update(status="not_checked", check_status=status)
        annotations.append({**claim, **details})
    counts = Counter(item["status"] for item in annotations)
    return {
        "policy_sha256": results[0]["policy_sha256"],
        "counts": {status: counts[status]
                   for status in ("valid", "inconclusive", "invalid", "not_checked")},
        "evidence": facts,
        "claims": annotations,
    }


async def validate_final_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Save the AI recommendation and claim annotations for human review."""
    validation = await _validate_decision(state, runtime)
    runtime.context.store.put_json("decision/validation.json", validation)
    report = human_review_report(
        {**state["proposed_decision"], "policy_check": state["policy_check"]}, validation,
    )
    runtime.context.store.put_json("decision/result.json", report)
    return {"final_decision": report}
