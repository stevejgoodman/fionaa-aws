"""Submission-completeness triage: underwriter, or back to the applicant.

This answers one narrow question -- *is there enough usable documentation for a
human underwriter to work on?* -- and deliberately says nothing about whether
the loan is likely to be approved. An application with complete paperwork goes
to the underwriter even when every assessment upstream looks unfavourable;
that judgement is the underwriter's, not this module's.

It is a pure rules engine: no LLM, no store, no `Runtime`. The graph-facing
layer lives in `fionaa/workflow/triage.py`, and the state-to-findings mapping
in `fionaa/evidence_checks.py`, so this module stays trivially testable and the
routing rule stays readable as a rule rather than as prose an agent produced.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RULES_VERSION = "submission-readiness-v1"

Requirement = Literal[
    "application_details",
    "director_id",
    "proof_of_address",
    "bank_statements",
    "annual_accounts",
]

# `missing` means the applicant never supplied it; the other three defects mean
# something was supplied but can't be relied on. All four route the same way --
# the distinction exists so the applicant-facing note can say something more
# useful than "missing", and so a reviewer can tell the cases apart.
Status = Literal["satisfied", "missing", "incomplete", "stale", "unreadable"]

Route = Literal["underwriter", "return_to_applicant"]

# Applicant-facing text. Every string here is read by the applicant verbatim,
# so it names documents, never state keys, schema field names or node names.
CHECKLIST: dict[str, str] = {
    "application_details": "Complete the applicant details, requested loan amount and term.",
    "director_id": "Supply the director's passport or driving licence.",
    "proof_of_address": "Supply proof of the director's residential address.",
    "bank_statements": "Supply at least three months of business bank statements, "
                       "with the most recent statement less than 90 days old.",
    "annual_accounts": "Supply filed annual accounts with an accounting date within the last 12 months.",
}


class EvidenceFinding(BaseModel):
    """One requirement's assessed state, as produced by `evidence_checks`."""

    model_config = ConfigDict(extra="forbid")

    status: Status
    evidence_refs: list[str] = Field(default_factory=list)
    # Internal, for the reviewer's artifact -- may name documents and dates but
    # is never shown to the applicant. `correction` is the applicant-facing half.
    explanation: str = Field(min_length=1)
    correction: str | None = None

    @model_validator(mode="after")
    def require_support(self):
        """A satisfied finding has to point at the evidence that satisfied it,
        and a defect in a document that *was* supplied has to say what would
        fix it -- otherwise the applicant note would just say "there's a
        problem with your bank statements" with no action attached. `missing`
        is exempt: `assess` fills its correction from CHECKLIST."""
        if self.status == "satisfied" and not self.evidence_refs:
            raise ValueError("Satisfied findings require evidence references")
        if self.status in {"incomplete", "stale", "unreadable"} and not self.correction:
            raise ValueError("Document defects require a specific correction")
        return self


class RequirementFinding(EvidenceFinding):
    requirement: Requirement


class TriageResult(BaseModel):
    rules_version: str = RULES_VERSION
    route: Route
    reason: str
    findings: list[RequirementFinding]
    # Rendered from the outstanding `correction` strings only. Downstream apps
    # can show this to the applicant as-is.
    applicant_note: str = ""


def assess(findings: dict[str, EvidenceFinding]) -> TriageResult:
    """Route on documentation completeness alone.

    Every requirement in CHECKLIST is essential, so the rule is simply: all
    satisfied -> underwriter, anything else -> back to the applicant. A
    requirement with no finding supplied is treated as `missing` rather than
    passing by omission -- a checklist that silently skips what it couldn't
    assess is worse than useless here.
    """
    assessed: list[RequirementFinding] = []
    for requirement, action in CHECKLIST.items():
        finding = findings.get(requirement) or EvidenceFinding(
            status="missing", explanation="No assessment was produced for this requirement."
        )
        correction = finding.correction or (action if finding.status != "satisfied" else None)
        assessed.append(RequirementFinding(
            **{**finding.model_dump(), "correction": None if finding.status == "satisfied" else correction},
            requirement=requirement,
        ))

    outstanding = [item for item in assessed if item.status != "satisfied"]
    if not outstanding:
        return TriageResult(
            route="underwriter",
            reason="Required documentation is present and usable.",
            findings=assessed,
        )
    return TriageResult(
        route="return_to_applicant",
        reason="Required documentation is incomplete.",
        findings=assessed,
        applicant_note=render_applicant_note(outstanding),
    )


def render_applicant_note(outstanding: list[RequirementFinding]) -> str:
    """Plain-English note listing what the applicant needs to supply.

    Built only from `correction` strings, which come from CHECKLIST or from
    `evidence_checks`' own applicant-safe wording -- never from `explanation`,
    an exception message, or anything the assessment nodes produced.
    """
    lines = "\n".join(f"- {item.correction}" for item in outstanding if item.correction)
    return (
        "We can't assess your application yet. Please supply the following, "
        f"then resubmit:\n{lines}"
    )
