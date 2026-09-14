"""Validation workflow stage."""
from __future__ import annotations

import asyncio
from typing import Literal
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command
from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.domain.applications import LoanType
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
    checked against one claim). Used directly for validate_final_decision's
    already-atomic `proposed_decision`, and as the primitive
    _validate_policy_check fans out over for policy_check's compound claim."""
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
    application since the runtime Automated Reasoning check went live: every
    boundary case that's ever cleanly resolved valid/invalid
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


async def _validate_policy_check(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    """Runs every atomic claim from _policy_check_claims concurrently (same
    facts/policy each time, only the claim varies) and aggregates: an
    `invalid` finding on ANY single claim still fails the whole check
    closed, same as before decomposition. `status` is 'valid' only if every
    sub-check cleanly resolved valid; 'inconclusive' if none failed but at
    least one couldn't be confirmed either way; 'not_validated' otherwise.
    `claims` keeps a full per-assertion audit trail -- strictly more
    evidence than the single blob it replaces, since a caller can now see
    exactly which clause (if any) is the problem instead of one tooComplex
    covering the whole application."""
    claims = _policy_check_claims(state["policy_check"])
    results = await asyncio.gather(*(_check_claim(state, runtime, claim) for claim in claims))
    passed = all(r["passed"] for r in results)
    all_valid = all(r["status"] == "valid" for r in results)
    status = "valid" if all_valid else "inconclusive" if passed else "not_validated"
    return {
        "passed": passed,
        "status": status,
        "policy_sha256": results[0]["policy_sha256"],
        "claims": [{"claim": claim, **result} for claim, result in zip(claims, results)],
    }


def _refer_validation(runtime, stage, validation):
    decision = {
        "outcome": "referred",
        "reason": "policy_consistency_not_validated",
        "rationale": "Human review required: runtime policy consistency validation did not pass.",
        "validation_stage": stage,
        "validation": validation,
    }
    runtime.context.store.put_json("decision/result.json", decision)
    return decision


async def validate_policy_assessment(
    state: ApplicationState, runtime: Runtime[AgentContext]
) -> Command[Literal["companies_house", "__end__"]]:
    validation = await _validate_policy_check(state, runtime)
    runtime.context.store.put_json("policy_check/validation.json", validation)
    update = {"policy_validation": validation}
    if not validation["passed"]:
        update["final_decision"] = _refer_validation(runtime, "policy_check", validation)
        return Command(update=update, goto=END)
    return Command(update=update, goto="companies_house")


async def validate_final_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    proposed = state["proposed_decision"]
    # Already an atomic claim (outcome/reason/rationale) -- no decomposition
    # needed here, and no real evidence yet on how it behaves under
    # Automated Reasoning: this call has never actually executed since the
    # runtime check went live, because validate_policy_assessment referred
    # every real application before the graph ever reached this node.
    validation = await _check_claim(state, runtime, proposed)
    # A final checker result cannot bypass missing/failed upstream validation.
    if not state.get("policy_validation", {}).get("passed", False):
        validation = {**validation, "passed": False, "status": "upstream_not_validated"}
    runtime.context.store.put_json("decision/validation.json", validation)
    if not validation["passed"]:
        return {"final_decision": _refer_validation(runtime, "decision", validation)}
    final = {**proposed, "validation": validation}
    runtime.context.store.put_json("decision/result.json", final)
    return {"final_decision": final}
