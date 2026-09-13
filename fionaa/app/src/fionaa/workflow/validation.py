"""Validation workflow stage."""
from __future__ import annotations

from typing import Literal
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command
from fionaa.policy_loader import load_policy_text
from fionaa.policy_consistency import PolicyConsistencyChecker
from fionaa.domain.applications import LoanType
from fionaa.workflow.state import ApplicationState, AgentContext


async def _validate_assessment(state, runtime, claim):
    application = state["application"]
    loan_type = LoanType(application["loan_type"])
    checker = runtime.context.policy_checker or PolicyConsistencyChecker.from_environment()
    # Evidence comes from graph inputs, not a model-authored restatement.
    # Source labels retain the distinction between self-report and findings.
    facts = {
        "application_self_reported": application,
        "annual_accounts": state.get("annual_accounts", []),
        "bank_statements": state.get("bank_statements", []),
        "companies_house_findings": state.get("companies_house"),
        "financial_assessment": state.get("financial_assessment"),
    }
    return await checker.check(loan_type.value, load_policy_text(loan_type), facts, claim)


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
    validation = await _validate_assessment(state, runtime, state["policy_check"])
    runtime.context.store.put_json("policy_check/validation.json", validation)
    update = {"policy_validation": validation}
    if not validation["passed"]:
        update["final_decision"] = _refer_validation(runtime, "policy_check", validation)
        return Command(update=update, goto=END)
    return Command(update=update, goto="companies_house")


async def validate_final_decision(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict:
    proposed = state["proposed_decision"]
    validation = await _validate_assessment(state, runtime, proposed)
    # A final checker result cannot bypass missing/failed upstream validation.
    if not state.get("policy_validation", {}).get("passed", False):
        validation = {**validation, "passed": False, "status": "upstream_not_validated"}
    runtime.context.store.put_json("decision/validation.json", validation)
    if not validation["passed"]:
        return {"final_decision": _refer_validation(runtime, "decision", validation)}
    final = {**proposed, "validation": validation}
    runtime.context.store.put_json("decision/result.json", final)
    return {"final_decision": final}
