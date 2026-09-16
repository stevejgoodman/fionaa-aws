"""Attach policy checks to claims in the report for a human reviewer."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.ar_facts import build_derived_facts
from fionaa.ar_claims import (
    approval_assertion,
    documentation_assertion,
    eligibility_assertion,
    leaf_facts_for_loan_type,
)
from fionaa.domain.applications import LoanType
from langgraph.runtime import Runtime
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.workflow.decision import human_review_report


def _facts(state: ApplicationState, today: date) -> dict:
    application = state["application"]
    annual_accounts = state.get("annual_accounts", [])
    bank_statements = state.get("bank_statements", [])
    companies_house = state.get("companies_house")
    return {
        # Precomputed in the Automated Reasoning policy's own variable
        # names (loanAmount, isUKBased, tradingHistoryMonths, etc.) -- AR's
        # NL-to-logic translator was coming back "inconclusive" on most
        # claims when only given raw, differently-named self-reported JSON
        # to derive these from itself. See ar_facts.py.
        "derived_facts": build_derived_facts(application, annual_accounts, bank_statements, companies_house, today),
        # Keep self-reported inputs distinguishable from external findings.
        "application_self_reported": application,
        "annual_accounts": annual_accounts,
        "bank_statements": bank_statements,
        "companies_house_findings": companies_house,
        "financial_assessment": state.get("financial_assessment"),
    }


def _claim(source: str, field: str, text: str) -> dict:
    return {
        "source": source,
        "source_path": f"/{source}/{field}",
        "claim": {"summary": text},
    }


def _deterministic_claims(policy_check: dict, proposed: dict) -> list[dict]:
    """Code-generated, deterministic AR assertions -- never LLM prose. Each
    entry pairs a source_path (a JSON pointer into decision/result.json, for
    the dashboard) with one of ar_claims.py's templated sentences. A
    candidate is omitted (None) when its source field has no clean boolean
    to assert -- see each ar_claims.py function's docstring."""
    candidates = [
        ("policy_check", "eligible", eligibility_assertion(policy_check)),
        ("policy_check", "documentation_gaps", documentation_assertion(policy_check)),
        ("ai_recommendation", "outcome", approval_assertion(proposed)),
    ]
    return [_claim(source, field, text) for source, field, text in candidates if text is not None]


async def _validate_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Check each assessment field separately; no result determines the loan outcome.

    source_path is a JSON pointer into decision/result.json. Evidence is the
    shared input snapshot, not a claim that the checker used every source.
    """
    claims = _deterministic_claims(state["policy_check"], state["proposed_decision"])
    loan_type = LoanType(state["application"]["loan_type"])
    checker = runtime.context.policy_checker or PolicyConsistencyChecker.from_environment()
    policy = load_policy_text(loan_type)
    # Computed fresh per invocation, not frozen -- see check_against_policy,
    # which does the same for the same reason.
    facts = _facts(state, date.today())
    leaf_facts = leaf_facts_for_loan_type(loan_type, facts["derived_facts"])
    results = await asyncio.gather(*(
        checker.check(loan_type.value, policy, leaf_facts, item["claim"]["summary"]) for item in claims
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
