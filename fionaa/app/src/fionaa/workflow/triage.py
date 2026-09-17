"""Final documentation routing shared by both assessment endings."""
from pydantic import ValidationError
from langgraph.runtime import Runtime

from fionaa.evidence_readiness import assess_submission, _application_finding
from fionaa.workflow.decision import human_review_report
from fionaa.workflow.state import ApplicationState, AgentContext


def route_loaded_application(state: ApplicationState):
    if state["application"].get("loan_type") == "unsecured-business-loans" and (
        state.get("ingestion_errors") or (state.get("readiness_assessment") or {}).get("route") == "internal_hold" or (
            state.get("submission_manifest") and _application_finding(state["application"]).status != "satisfied"
        )
    ):
        return "prepare_incomplete_review"
    return "companies_house"


def prepare_incomplete_review(state: ApplicationState):
    """Avoid asking assessment agents to reason over invalid or missing inputs."""
    report = human_review_report({
        "outcome": "referred", "reason": "assessment_not_completed",
        "rationale": "Application details or evidence processing need attention before assessment.",
    }, {"status": "not_checked", "check_status": "assessment_not_completed", "claims": []})
    return {"final_decision": report}


def triage_application(state: ApplicationState, runtime: Runtime[AgentContext]):
    application = state["application"]
    if application.get("loan_type") != "unsecured-business-loans":
        # Only the unsecured checklist has been approved. Other products retain
        # their existing human-review path.
        runtime.context.store.put_json("decision/result.json", state["final_decision"])
        return {}
    manifest = state.get("submission_manifest")
    if not manifest:
        triage = {"rules_version": "unsecured-readiness-v1", "route": "internal_hold",
                  "submission_date": None, "findings": [],
                  "reason": "A valid, complete submission inventory and submission date are required."}
    else:
        try:
            triage = dict(state["readiness_assessment"]) if state.get("readiness_assessment") else assess_submission(
                application, manifest, state.get("ingested_documents", []),
            ).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError, KeyError):
            # Dates or extraction fields can be schema-shaped but uninterpretable.
            # Do not expose exception text (which can contain sensitive inputs).
            triage = {"rules_version": "unsecured-readiness-v1", "route": "internal_hold",
                      "submission_date": manifest["submission_date"], "findings": [],
                      "reason": "Readiness inputs need internal verification."}
        triage["submission_id"] = manifest["submission_id"]
    if state.get("ingestion_errors"):
        triage.update(route="internal_hold", reason="Evidence processing requires internal attention.")
    if state.get("company_lookup_failed"):
        triage.update(route="internal_hold", reason="Company verification requires internal attention.")
    report = {**state["final_decision"], "triage": triage}
    runtime.context.store.put_json("decision/triage.json", triage)
    runtime.context.store.put_json("decision/result.json", report)
    return {"triage": triage, "final_decision": report}
