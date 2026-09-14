"""Validation workflow stage."""
from __future__ import annotations

import asyncio
from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.domain.applications import LoanType
from langgraph.runtime import Runtime
from fionaa.workflow.state import ApplicationState, AgentContext


def _facts(state: ApplicationState) -> dict:
    application = state["application"]
    # Evidence comes from graph inputs, not a model-authored restatement.
    # Source labels retain the distinction between self-report and findings.
    return {
        "application_self_reported": application,
        "annual_accounts": state.get("annual_accounts", []),
        "bank_statements": state.get("bank_statements", []),
        "companies_house_findings": state.get("companies_house"),
        "financial_assessment": state.get("financial_assessment"),
    }


async def _check_claim(state: ApplicationState, runtime: Runtime[AgentContext], claim: dict) -> dict:
    """Single-claim Automated Reasoning check -- the only shape that's
    actually been proven to translate cleanly (every case in
    evals/automated_reasoning/live-results.json is exactly one fact-set
    checked against one claim)."""
    application = state["application"]
    loan_type = LoanType(application["loan_type"])
    checker = runtime.context.policy_checker or PolicyConsistencyChecker.from_environment()
    return await checker.check(loan_type.value, load_policy_text(loan_type), _facts(state), claim)


def _policy_check_claims(policy_check: dict) -> list[dict]:
    """Splits a PolicyCheckResult's compound claim -- the eligible verdict
    plus every clause_finding plus every documentation_gap, historically
    all bundled into one ApplyGuardrail call -- into one atomic claim per
    assertion.

    Bundling ~13 independent compound English assertions into a single call
    is what produced tooComplex/translationAmbiguous on every real
    application when this ran right after policy_check: every boundary case
    that's ever cleanly resolved valid/invalid
    (evals/automated_reasoning/live-results.json) checked exactly one claim
    against a small fact set. Checking each assertion in its own call keeps
    every individual request in that proven shape.

    Reuses PolicyCheckResult's own "summary"-shaped free text rather than
    inventing new claim keys -- that's the exact shape the live boundary
    tests wrap single-sentence claims in.
    """
    claims = [{"summary": f"This application's overall eligibility under "
                           f"the covered policy is: {policy_check['eligible']}."}]
    claims += [{"summary": finding} for finding in policy_check.get("clause_findings", [])]
    claims += [{"summary": gap} for gap in policy_check.get("documentation_gaps", [])]
    return claims


def _final_decision_claims(proposed: dict) -> list[dict]:
    """The final decision's own atomic assertions -- outcome, reason,
    rationale -- as separate claims, same convention as
    _policy_check_claims. Deliberately does NOT pass `proposed` itself as
    a single claim: synthesize_decision's `proposed_decision` re-embeds the
    full policy_check/companies_house/financial_assessment/web_search
    sub-dicts alongside outcome/reason/rationale (see decision.py), so
    passing it whole as one claim smuggles all of that evidence into the
    *claim* being checked rather than the `facts` supporting it -- which is
    likely why it hit tooComplex even as an ostensibly "atomic" single
    call. Only the decision's own three assertions belong in the claim;
    everything else is already covered via _facts / the policy_check
    claims above."""
    return [
        {"summary": f"The final decision on this application is: {proposed['outcome']}."},
        {"summary": proposed["reason"]},
        {"summary": proposed["rationale"]},
    ]


async def _validate_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Single, comprehensive Automated Reasoning check, run once at the end
    of the graph rather than split across an early policy_check-stage gate
    and a separate final-decision-stage gate.

    Collapsed here rather than gating right after policy_check because that
    early gate could never actually confirm most of its claims:
    companies_house_findings/financial_assessment don't exist yet at that
    point in the graph (regardless of whether companies_house or
    policy_check runs first -- see graph.py), so clauses like "UK-based" or
    "meets minimum trading history" reliably bottomed out in
    noTranslations/translationAmbiguous for lack of evidence, not because
    anything was actually wrong. Gating there also bought no real cost
    savings: the graph runs financial_assessment -> web_search ->
    synthesize_decision regardless, except on the companies_house
    found=False branch (reject_no_company, which never reaches this node at
    all) -- so there was no fail-fast benefit worth weighing against
    checking with structurally incomplete evidence.

    Checks every atomic claim from both _policy_check_claims (now checked
    with the FULL fact set -- companies_house/financial_assessment are
    finally real by this point) and _final_decision_claims concurrently,
    and aggregates: an `invalid` finding on any single claim fails the
    whole check closed; overall status is 'valid' only if every claim is,
    'inconclusive' if none failed but at least one couldn't be confirmed
    either way, 'not_validated' otherwise. `claims` keeps a full
    per-assertion audit trail, tagged by `source` so it's clear which
    predate the final decision and which are the decision's own."""
    proposed = state["proposed_decision"]
    claims = (
        [{"source": "policy_check", "claim": c} for c in _policy_check_claims(state["policy_check"])]
        + [{"source": "final_decision", "claim": c} for c in _final_decision_claims(proposed)]
    )
    results = await asyncio.gather(*(_check_claim(state, runtime, c["claim"]) for c in claims))
    passed = all(r["passed"] for r in results)
    all_valid = all(r["status"] == "valid" for r in results)
    status = "valid" if all_valid else "inconclusive" if passed else "not_validated"
    return {
        "passed": passed,
        "status": status,
        "policy_sha256": results[0]["policy_sha256"],
        "claims": [{**c, **r} for c, r in zip(claims, results)],
    }


def _refer_validation(runtime, validation):
    decision = {
        "outcome": "referred",
        "reason": "policy_consistency_not_validated",
        "rationale": "Human review required: runtime policy consistency validation did not pass.",
        "validation": validation,
    }
    runtime.context.store.put_json("decision/result.json", decision)
    return decision


async def validate_final_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    proposed = state["proposed_decision"]
    validation = await _validate_decision(state, runtime)
    runtime.context.store.put_json("decision/validation.json", validation)
    if not validation["passed"]:
        return {"final_decision": _refer_validation(runtime, validation)}
    final = {**proposed, "validation": validation}
    runtime.context.store.put_json("decision/result.json", final)
    return {"final_decision": final}
