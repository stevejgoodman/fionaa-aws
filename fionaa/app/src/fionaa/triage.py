"""Documentation readiness, independent of eligibility and AI recommendations.

Inputs are checked findings from a trusted evidence assessment, not applicant
assertions or a list of uploaded filenames. Missing assessments mean an internal
hold; only an explicit missing finding means an applicant omitted evidence.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Requirement = Literal[
    "application_details", "director_id", "proof_of_address", "bank_statements",
    "accounts_or_management_information", "vat_returns", "existing_borrowing",
    "personal_guarantee",
]
Status = Literal[
    "satisfied", "missing", "incomplete", "stale", "unreadable",
    "not_applicable", "unable_to_verify",
]

# Requirement text is separate from the approved readiness classification.
CHECKLIST: dict[str, tuple[bool, str]] = {
    "application_details": (True, "Complete the applicant details, requested loan amount and term."),
    "director_id": (True, "Supply the director's passport or driving licence."),
    "proof_of_address": (True, "Supply proof of the director's address."),
    "bank_statements": (True, "Supply at least three months of business bank statements, with the latest less than 90 days old at submission."),
    "accounts_or_management_information": (True, "Supply accounts or management information; filed accounts must have an accounting date within the 12 months before submission."),
    "vat_returns": (False, "Supply VAT returns if the business is VAT registered."),
    "existing_borrowing": (True, "Supply details of the business's existing borrowing."),
    "personal_guarantee": (False, "Arrange the required personal guarantee during underwriting for a loan over £25,000."),
}


class EvidenceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    correction: str | None = None

    @model_validator(mode="after")
    def require_support(self):
        if self.status == "satisfied" and not self.evidence_refs:
            raise ValueError("Satisfied findings require evidence references")
        if self.status in {"incomplete", "stale", "unreadable"} and not self.correction:
            raise ValueError("Document defects require a specific correction")
        return self


class ReadinessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_type: Literal["unsecured-business-loans"]
    submission_date: date
    loan_amount: int | None = Field(default=None, strict=True)
    vat_registered: bool | None = Field(default=None, strict=True)
    has_existing_borrowing: bool | None = Field(default=None, strict=True)
    findings: dict[Requirement, EvidenceFinding] = Field(default_factory=dict)


class RequirementFinding(EvidenceFinding):
    requirement: Requirement
    blocks_underwriting: bool
    policy_ref: str


class TriageResult(BaseModel):
    rules_version: str = "unsecured-readiness-v1"
    submission_date: date
    route: Literal["underwriter", "return_to_applicant", "internal_hold"]
    reason: str
    findings: list[RequirementFinding]


def assess_readiness(inputs: ReadinessInput) -> TriageResult:
    """Apply the approved essential/supporting checklist without deciding a loan.

    Evidence producers must check coverage/freshness against submission_date.
    In particular, a count of three files is not proof of three months' coverage.
    Unknown conditional applicability is an applicant clarification; missing
    assessment capability or failed verification is an internal exception.
    """
    findings = []
    applicability = {
        "vat_returns": inputs.vat_registered,
        "existing_borrowing": inputs.has_existing_borrowing,
        "personal_guarantee": None if inputs.loan_amount is None else inputs.loan_amount > 25_000,
    }
    clarifications = {
        "vat_returns": "Confirm whether the business is VAT registered.",
        "existing_borrowing": "Confirm whether the business has existing borrowing.",
        "personal_guarantee": "Provide the requested loan amount.",
    }
    for requirement, (essential, action) in CHECKLIST.items():
        finding = inputs.findings.get(requirement)
        if requirement in applicability and applicability[requirement] is False:
            finding = EvidenceFinding(status="not_applicable", explanation="Not required for the declared application details.")
        elif requirement in applicability and applicability[requirement] is None:
            essential = True
            finding = EvidenceFinding(status="incomplete", explanation="Applicability has not been established.", correction=clarifications[requirement])
        elif finding is None or finding.status == "not_applicable":
            finding = EvidenceFinding(status="unable_to_verify", explanation="No applicable evidence assessment is available.")

        correction = finding.correction
        if finding.status == "missing":
            correction = correction or action
        if finding.status in {"satisfied", "not_applicable", "unable_to_verify"}:
            correction = None
        findings.append(RequirementFinding(
            **{**finding.model_dump(), "correction": correction},
            requirement=requirement,
            blocks_underwriting=essential and finding.status not in {"satisfied", "not_applicable"},
            policy_ref="policies/unsecured-business-loans/policy.md"
                       + ("; policies/general.md" if requirement in {"director_id", "bank_statements", "accounts_or_management_information"} else ""),
        ))

    # Hold takes precedence to avoid sending an incomplete correction report
    # when any assessment failed, including supporting documentation checks.
    if any(item.status == "unable_to_verify" for item in findings):
        route, reason = "internal_hold", "Evidence assessment requires internal attention."
    elif any(item.blocks_underwriting for item in findings):
        route, reason = "return_to_applicant", "Essential information or documentation needs completion."
    else:
        route, reason = "underwriter", "Essential documentation is ready for human underwriting."
    return TriageResult(submission_date=inputs.submission_date, route=route, reason=reason, findings=findings)
