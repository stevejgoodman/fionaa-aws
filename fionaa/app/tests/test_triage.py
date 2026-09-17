from datetime import date

import pytest
from pydantic import ValidationError

from fionaa.triage import CHECKLIST, EvidenceFinding, ReadinessInput, assess_readiness


def application(**changes):
    payload = dict(
        loan_type="unsecured-business-loans", submission_date=date(2026, 9, 17),
        loan_amount=30_000, vat_registered=True, has_existing_borrowing=True,
        findings={key: EvidenceFinding(status="satisfied", evidence_refs=[f"evidence/{key}"],
                                      explanation="Requirement checked against supplied evidence.")
                  for key in CHECKLIST},
    )
    payload.update(changes)
    return ReadinessInput(**payload)


def test_complete_evidence_routes_to_underwriter_and_preserves_audit_details():
    result = assess_readiness(application())
    assert result.route == "underwriter"
    assert result.submission_date == date(2026, 9, 17)
    assert all(item.evidence_refs and item.policy_ref for item in result.findings)
    assert "ai_recommendation" not in ReadinessInput.model_fields
    assert "eligible" not in ReadinessInput.model_fields


@pytest.mark.parametrize("requirement", [key for key, (essential, _) in CHECKLIST.items() if essential])
def test_each_missing_essential_returns_actionable_gap(requirement):
    inputs = application()
    inputs.findings[requirement] = EvidenceFinding(status="missing", explanation="No evidence supplied.")
    result = assess_readiness(inputs)
    assert result.route == "return_to_applicant"
    gap = next(item for item in result.findings if item.requirement == requirement)
    assert gap.blocks_underwriting and gap.correction


def test_vat_and_guarantee_can_follow_during_underwriting():
    inputs = application()
    for key in ("vat_returns", "personal_guarantee"):
        inputs.findings[key] = EvidenceFinding(status="missing", explanation="Not yet supplied.")
    result = assess_readiness(inputs)
    assert result.route == "underwriter"
    assert sum(item.status == "missing" and bool(item.correction) for item in result.findings) == 2


@pytest.mark.parametrize("field", ["vat_registered", "has_existing_borrowing", "loan_amount"])
def test_unknown_applicability_requires_clarification(field):
    result = assess_readiness(application(**{field: None}))
    assert result.route == "return_to_applicant"
    assert any(item.status == "incomplete" and item.correction for item in result.findings)


def test_explicit_negative_declarations_and_guarantee_boundary():
    inputs = application(vat_registered=False, has_existing_borrowing=False, loan_amount=25_000)
    for key in ("vat_returns", "existing_borrowing", "personal_guarantee"):
        del inputs.findings[key]
    result = assess_readiness(inputs)
    assert result.route == "underwriter"
    assert sum(item.status == "not_applicable" for item in result.findings) == 3


@pytest.mark.parametrize("status", ["incomplete", "stale", "unreadable"])
def test_defects_preserve_specific_applicant_correction(status):
    inputs = application()
    inputs.findings["bank_statements"] = EvidenceFinding(
        status=status, explanation="Statement evidence is unusable.",
        correction="Supply a readable statement for August 2026.", evidence_refs=["input/statement"],
    )
    result = assess_readiness(inputs)
    assert result.route == "return_to_applicant"
    assert next(item for item in result.findings if item.requirement == "bank_statements").correction == "Supply a readable statement for August 2026."


@pytest.mark.parametrize("requirement", ["director_id", "vat_returns"])
def test_unassessed_evidence_holds_even_with_known_applicant_gap(requirement):
    inputs = application()
    del inputs.findings[requirement]
    inputs.findings["bank_statements"] = EvidenceFinding(status="missing", explanation="Absent.")
    result = assess_readiness(inputs)
    assert result.route == "internal_hold"
    assert next(item for item in result.findings if item.requirement == requirement).correction is None


def test_required_evidence_cannot_be_skipped_as_not_applicable():
    inputs = application()
    inputs.findings["director_id"] = EvidenceFinding(status="not_applicable", explanation="Skipped.")
    assert assess_readiness(inputs).route == "internal_hold"


def test_unsupported_loan_type_is_not_assessed_with_unsecured_rules():
    with pytest.raises(ValidationError):
        application(loan_type="invoice-factoring")


def test_satisfied_evidence_needs_reference_and_defect_needs_correction():
    with pytest.raises(ValidationError):
        EvidenceFinding(status="satisfied", explanation="Looks fine.")
    with pytest.raises(ValidationError):
        EvidenceFinding(status="stale", explanation="Too old.")
