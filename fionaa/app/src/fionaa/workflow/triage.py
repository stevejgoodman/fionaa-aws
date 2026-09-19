"""Final routing stage: forward to an underwriter, or return to the applicant.

Both of the graph's assessment endings (`validate_final_decision` and
`reject_no_company`) feed into `triage_application`, which branches on
documentation completeness alone. A company Companies House couldn't find still
goes to the underwriter if the paperwork is complete -- that lookup failure is
something a human investigates, not something the applicant can fix by
uploading another file.

Each branch writes its own handoff artifact so a downstream app can watch one
prefix and pick up only what's addressed to it, and neither ends up having to
parse a route field out of a shared document to find out if it should act.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from langgraph.runtime import Runtime

from fionaa.evidence_checks import build_findings
from fionaa.triage import assess
from fionaa.workflow.state import AgentContext, ApplicationState

UNDERWRITER_KEY = "decision/to_underwriter.json"
APPLICANT_KEY = "decision/to_applicant.json"


def triage_application(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Assess submission completeness and record it, without routing yet.

    Computed fresh per invocation rather than against a frozen date -- same as
    check_against_policy and validate_final_decision, which both call
    date.today() for the same reason: staleness is relative to when the
    assessment actually runs.
    """
    result = assess(build_findings(dict(state), date.today())).model_dump(mode="json")
    runtime.context.store.put_json("decision/triage.json", result)
    return {"triage": result}


def route_by_triage(state: ApplicationState) -> str:
    """The graph's only conditional edge. Deliberately a lookup and nothing
    more: every rule that decides the route lives in triage.assess, where it
    can be tested without building a graph."""
    return state["triage"]["route"]


def _reviewer_report(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """decision/result.json stays the single reviewer-facing artifact; the
    upstream node already wrote it, and this rewrites it with `triage` merged
    in so the route is visible without opening a second file."""
    report = {**state["final_decision"], "triage": state["triage"]}
    runtime.context.store.put_json("decision/result.json", report)
    return report


def forward_to_underwriter(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Terminal node: the submission is complete enough for human underwriting.

    This says nothing about whether the loan should be granted -- the AI
    recommendation and the Automated Reasoning claim annotations travel with it
    as supporting material for the underwriter to weigh, exactly as they do
    today.
    """
    report = _reviewer_report(state, runtime)
    runtime.context.store.put_json(UNDERWRITER_KEY, {
        "route": "underwriter",
        "reason": state["triage"]["reason"],
        "triage": state["triage"],
        "assessment": report,
    })
    return {"final_decision": report}


def return_to_applicant(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Terminal node: required documentation is missing, stale or unreadable.

    The applicant artifact carries the checklist and nothing else. No AI
    recommendation, no policy check, no financial assessment, no Companies
    House findings -- an applicant must not be shown the bank's internal credit
    view of them, and an incomplete submission hasn't been assessed on its
    merits anyway. `outstanding` is the machine-readable form of the same
    information in `applicant_note`, for a downstream app that would rather
    render its own wording than show the note verbatim.
    """
    report = _reviewer_report(state, runtime)
    triage = state["triage"]
    runtime.context.store.put_json(APPLICANT_KEY, {
        "route": "return_to_applicant",
        "reason": triage["reason"],
        "applicant_note": triage["applicant_note"],
        "outstanding": [
            {"requirement": item["requirement"], "status": item["status"], "action": item["correction"]}
            for item in triage["findings"] if item["status"] != "satisfied"
        ],
    })
    return {"final_decision": report}
