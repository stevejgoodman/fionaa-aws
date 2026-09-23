"""Attach policy checks to claims in the report for a human reviewer."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date

from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.ar_facts import build_derived_facts
from fionaa.ar_claims import (
    APPROVAL_VARIABLE,
    DOCUMENTATION_VARIABLE,
    ELIGIBILITY_VARIABLE,
    approval_assertion,
    approval_premise_facts,
    documentation_assertion,
    eligibility_assertion,
    facts_for_claim,
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
    director_id = state.get("director_id", [])
    proof_of_address = state.get("proof_of_address", [])
    derived_facts = build_derived_facts(
        application, annual_accounts, bank_statements, companies_house, today,
        director_id=director_id, proof_of_address=proof_of_address,
        # No default: a key load_application never set means this product's
        # policy doesn't ask for the document, which is "unknown", not
        # "none supplied". See _was_supplied in ar_facts.
        vat_returns=state.get("vat_returns"),
        existing_borrowing=state.get("existing_borrowing"),
        security_assets=state.get("security_assets"),
    )
    facts = {
        # Precomputed in the Automated Reasoning policy's own variable
        # names (loanAmount, isUKBased, tradingHistoryMonths, etc.) -- AR's
        # NL-to-logic translator was coming back "inconclusive" on most
        # claims when only given raw, differently-named self-reported JSON
        # to derive these from itself. See ar_facts.py.
        "derived_facts": derived_facts,
        # Keep self-reported inputs distinguishable from external findings.
        "application_self_reported": application,
        "annual_accounts": annual_accounts,
        "bank_statements": bank_statements,
        "companies_house_findings": companies_house,
        "financial_assessment": state.get("financial_assessment"),
    }
    return facts


def _claim(source: str, field: str, text: str) -> dict:
    return {
        "source": source,
        "source_path": f"/{source}/{field}",
        "claim": {"summary": text},
    }


def _deterministic_claims(policy_check: dict, proposed: dict) -> list[tuple[dict, str]]:
    """Code-generated, deterministic AR assertions -- never LLM prose. Each
    entry pairs a source_path (a JSON pointer into decision/result.json, for
    the dashboard) with one of ar_claims.py's templated sentences, and names
    the policy variable the assertion is about so its facts can be scoped to
    that claim alone. A candidate is omitted (None) when its source field has
    no clean boolean to assert -- see each ar_claims.py function's
    docstring."""
    candidates = [
        ("policy_check", "eligible", ELIGIBILITY_VARIABLE, eligibility_assertion(policy_check)),
        ("policy_check", "documentation_gaps", DOCUMENTATION_VARIABLE,
         documentation_assertion(policy_check)),
        ("ai_recommendation", "outcome", APPROVAL_VARIABLE, approval_assertion(proposed)),
    ]
    return [(_claim(source, field, text), variable)
            for source, field, variable, text in candidates if text is not None]


async def _validate_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Check each assessment field separately; no result determines the loan outcome.

    source_path is a JSON pointer into decision/result.json. Evidence is the
    shared input snapshot, not a claim that the checker used every source.
    """
    candidates = _deterministic_claims(state["policy_check"], state["proposed_decision"])
    claims = [claim for claim, _ in candidates]
    loan_type = LoanType(state["application"]["loan_type"])
    checker = runtime.context.policy_checker or PolicyConsistencyChecker.from_environment()
    policy = load_policy_text(loan_type)
    reference_date = date.today()
    facts = _facts(state, reference_date)
    # Each claim gets only the facts its own rules reach. Sending a loan
    # type's whole fact set to every claim exceeded what the AR engine will
    # solve (17 premises -> "tooComplex"), which left every claim on a
    # complete application undecidable. The approval claim is composed from
    # the other two claims' subjects rather than from their combined facts,
    # which would be larger still -- see ar_claims.approval_premise_facts.
    claim_facts = [
        approval_premise_facts(state["policy_check"]) if variable == APPROVAL_VARIABLE
        else facts_for_claim(loan_type, variable, facts["derived_facts"])
        for _, variable in candidates
    ]
    results = await asyncio.gather(*(
        checker.check(loan_type.value, policy, sent, item["claim"]["summary"])
        for item, sent in zip(claims, claim_facts)
    ))
    annotations = []
    for claim, sent, result in zip(claims, claim_facts, results):
        # What this claim was actually given, so a reviewer reading one
        # annotation doesn't have to reconstruct the scoping to know which
        # facts the answer rests on.
        claim = {**claim, "facts_sent": sorted(sent)}
        # The checker's legacy passed flag is not a decision or report gate.
        details = {key: value for key, value in result.items() if key != "passed"}
        status = details["status"]
        if status not in {"valid", "invalid", "inconclusive"}:
            details.update(status="not_checked", check_status=status)
        annotations.append({**claim, **details})
    counts = Counter(item["status"] for item in annotations)
    # Union across claims, so a reviewer (and the evals) can see in one
    # place which policy variables left this run undecided, rather than
    # reading each claim's diagnostics. Empty when nothing was undecided.
    unbound = sorted({
        name for item in annotations
        for name in item.get("diagnostics", {}).get("unbound_variables", [])
    })
    return {
        "policy_sha256": results[0]["policy_sha256"],
        "counts": {status: counts[status]
                   for status in ("valid", "inconclusive", "invalid", "not_checked")},
        "unbound_variables": unbound,
        "evidence": facts,
        "claims": annotations,
    }


async def validate_final_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Prepare the annotated report; triage persists the final result."""
    validation = await _validate_decision(state, runtime)
    runtime.context.store.put_json("decision/validation.json", validation)
    report = human_review_report(
        {**state["proposed_decision"], "policy_check": state["policy_check"]}, validation,
    )
    return {"final_decision": report}
