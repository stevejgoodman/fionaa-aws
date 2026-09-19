"""Unit tests for the submission-completeness triage step.

Two layers, both pure: `evidence_checks.build_findings` (state -> per-requirement
findings) and `triage.assess` (findings -> route + applicant note). Neither
touches AWS, a model, or a graph, so everything here is a plain function call
against a literal state dict.

Graph wiring -- that both endings reach triage and that the conditional edge
lands on the right terminal node -- is covered in test_graph.py.
"""

from datetime import date, timedelta

import pytest

from fionaa import evidence_checks
from fionaa.triage import CHECKLIST, EvidenceFinding, assess

# Fixed so staleness assertions don't drift as the calendar moves. Everything
# below is dated relative to this.
TODAY = date(2024, 6, 1)

DIRECTOR = "Jane Smith"
ADDRESS = "14 Oak Avenue, Uxbridge, UB8 1AA"

APPLICATION = {
    "applicant_name": DIRECTOR, "year_of_birth": "1980",
    "company_name": "Acme Ltd", "company_address": "1 High St, London",
    "loan_type": "unsecured-business-loans", "loan_purpose": "Expansion",
    "loan_amount": 50000, "loan_term": 24,
    "director_first_name": "Jane", "director_surname": "Smith",
    "director_residential_address": ADDRESS,
    "trading_start_date": "2018-01-01", "annual_turnover": 500000, "annual_profit": 60000,
}

BANK_STATEMENTS = [
    {"account_owner": "Acme Ltd", "bank_name": "Big Bank", "account_number": "12345678",
     "address": ADDRESS, "start_date": f"2024-0{m}-01", "end_date": end,
     "balance": 12000.0, "payments_in": 5000.0, "payments_out": 3200.0}
    for m, end in ((3, "2024-03-31"), (4, "2024-04-30"), (5, "2024-05-31"))
]

ANNUAL_ACCOUNTS = [{
    "company_name": "Acme Ltd", "director": DIRECTOR,
    "registered_address": "1 High St, London", "registration_number": "12345678",
    "accounting_year": "2023-12-31",
    "turnover_current_year": 500000, "operating_profit_current_year": 80000,
    "profit_current_year": 60000,
    "turnover_last_year": 450000, "operating_profit_last_year": 70000, "profit_last_year": 50000,
    "tangible_fixed_assets_current_year": 20000, "debtors_current_year": 15000,
    "cash_at_bank_current_year": 30000, "tangible_fixed_assets_last_year": 18000,
    "debtors_last_year": 12000, "cash_at_bank_last_year": 25000,
}]

DIRECTOR_ID = [{"document_kind": "passport", "holder_name": DIRECTOR, "expiry_date": "2030-01-01"}]

PROOF_OF_ADDRESS = [{"document_kind": "utility bill", "holder_name": DIRECTOR,
                     "address": ADDRESS, "issue_date": "2024-05-01"}]


def complete_state(**overrides) -> dict:
    """A submission with nothing outstanding; override one key per test."""
    return {
        "application": APPLICATION,
        "bank_statements": BANK_STATEMENTS,
        "annual_accounts": ANNUAL_ACCOUNTS,
        "director_id": DIRECTOR_ID,
        "proof_of_address": PROOF_OF_ADDRESS,
        "document_errors": [],
        **overrides,
    }


def triage(**overrides):
    return assess(evidence_checks.build_findings(complete_state(**overrides), TODAY))


def status_of(result, requirement: str) -> str:
    return next(item.status for item in result.findings if item.requirement == requirement)


# ---------------------------------------------------------------------------
# The routing rule itself
# ---------------------------------------------------------------------------

def test_complete_submission_goes_to_the_underwriter():
    result = triage()

    assert result.route == "underwriter"
    assert [item.status for item in result.findings] == ["satisfied"] * 5
    # Nothing is outstanding, so there is nothing to tell the applicant.
    assert result.applicant_note == ""
    assert all(item.correction is None for item in result.findings)


DOCUMENT_REQUIREMENTS = ["director_id", "proof_of_address", "bank_statements", "annual_accounts"]


@pytest.mark.parametrize("requirement", DOCUMENT_REQUIREMENTS)
def test_each_missing_document_returns_to_the_applicant(requirement):
    result = triage(**{requirement: []})

    assert result.route == "return_to_applicant"
    assert status_of(result, requirement) == "missing"
    # Everything else still assessed and still fine -- the note must name only
    # what is actually outstanding.
    assert [item.requirement for item in result.findings if item.status != "satisfied"] == [requirement]
    assert CHECKLIST[requirement] in result.applicant_note


def test_incomplete_application_details_return_to_the_applicant():
    result = triage(application={k: v for k, v in APPLICATION.items() if k != "loan_amount"})

    assert result.route == "return_to_applicant"
    assert status_of(result, "application_details") == "incomplete"
    assert "loan amount" in result.applicant_note


def test_route_does_not_depend_on_how_the_application_looks():
    """The whole point of the step: completeness only. A submission with every
    document present routes to the underwriter regardless of what the
    assessment nodes concluded about the business -- that judgement is the
    underwriter's, and nothing from those nodes is an input here."""
    result = triage()

    assert result.route == "underwriter"
    assert set(evidence_checks.build_findings(complete_state(), TODAY)) == set(CHECKLIST)


# ---------------------------------------------------------------------------
# Per-requirement defects
# ---------------------------------------------------------------------------

def test_too_few_bank_statements_is_incomplete():
    result = triage(bank_statements=BANK_STATEMENTS[-2:])

    assert result.route == "return_to_applicant"
    assert status_of(result, "bank_statements") == "incomplete"
    assert "at least 3 months" in result.applicant_note


def test_old_bank_statements_are_stale():
    """general.md: most recent statement must be less than 90 days old. Three
    months of perfectly good coverage, just all of it too long ago -- the
    newest ends 2023-12-31, 153 days before TODAY."""
    stale = [
        {**statement, "start_date": start, "end_date": end}
        for statement, (start, end) in zip(BANK_STATEMENTS, (
            ("2023-10-01", "2023-10-31"), ("2023-11-01", "2023-11-30"), ("2023-12-01", "2023-12-31"),
        ))
    ]

    result = triage(bank_statements=stale)

    assert status_of(result, "bank_statements") == "stale"
    assert "less than 90 days old" in result.applicant_note


def test_accounts_older_than_twelve_months_are_stale():
    result = triage(annual_accounts=[{**ANNUAL_ACCOUNTS[0], "accounting_year": "2023-01-31"}])

    assert status_of(result, "annual_accounts") == "stale"


def test_accounts_within_twelve_months_are_satisfied():
    """Boundary: 2023-06-02 is 365 days before TODAY, so it still counts."""
    result = triage(annual_accounts=[{**ANNUAL_ACCOUNTS[0], "accounting_year": "2023-06-02"}])

    assert status_of(result, "annual_accounts") == "satisfied"


def test_expired_director_id_is_stale():
    result = triage(director_id=[{**DIRECTOR_ID[0], "expiry_date": "2024-01-01"}])

    assert status_of(result, "director_id") == "stale"
    assert "has not expired" in result.applicant_note


def test_director_id_in_someone_elses_name_is_incomplete():
    result = triage(director_id=[{**DIRECTOR_ID[0], "holder_name": "Brian Other"}])

    assert status_of(result, "director_id") == "incomplete"


def test_director_id_name_match_ignores_case_and_spacing():
    result = triage(director_id=[{**DIRECTOR_ID[0], "holder_name": "  jane   SMITH "}])

    assert result.route == "underwriter"


def test_old_proof_of_address_is_stale():
    result = triage(proof_of_address=[{**PROOF_OF_ADDRESS[0], "issue_date": "2023-05-01"}])

    assert status_of(result, "proof_of_address") == "stale"


def test_proof_of_address_for_a_different_address_is_incomplete():
    result = triage(proof_of_address=[{**PROOF_OF_ADDRESS[0], "address": "9 Other Road, Leeds"}])

    assert status_of(result, "proof_of_address") == "incomplete"
    assert "same residential address" in result.applicant_note


def test_unreadable_upload_makes_its_requirement_unreadable():
    """A document that failed schema validation at load time. The other
    statements loaded fine, but the submission still isn't complete -- the
    applicant has to replace the file."""
    result = triage(
        bank_statements=BANK_STATEMENTS,
        document_errors=[{"key": "input/bank_statement_may.json", "state_key": "bank_statements"}],
    )

    assert result.route == "return_to_applicant"
    assert status_of(result, "bank_statements") == "unreadable"
    assert "could not be read" in result.applicant_note


def test_unreadable_date_in_an_otherwise_valid_document():
    """The schema accepts `end_date` as a free string, so a document can load
    cleanly and still carry a date nothing can parse."""
    result = triage(bank_statements=[{**BANK_STATEMENTS[0], "end_date": "last Tuesday"}])

    assert status_of(result, "bank_statements") == "unreadable"


# ---------------------------------------------------------------------------
# The applicant note
# ---------------------------------------------------------------------------

def test_applicant_note_lists_every_outstanding_item():
    result = triage(director_id=[], annual_accounts=[])

    assert CHECKLIST["director_id"] in result.applicant_note
    assert CHECKLIST["annual_accounts"] in result.applicant_note
    assert CHECKLIST["bank_statements"] not in result.applicant_note


def test_applicant_note_leaks_nothing_internal():
    """The note is shown to the applicant verbatim, so it must not carry state
    keys, storage keys, schema names or the internal `explanation` text."""
    result = triage(director_id=[], bank_statements=[{**BANK_STATEMENTS[0], "end_date": "nonsense"}])

    note = result.applicant_note
    assert result.route == "return_to_applicant"
    for internal in ("input/", "state_key", "Schema", "evidence_refs", "_"):
        assert internal not in note
    assert all(item.explanation not in note for item in result.findings)


def test_evidence_refs_point_at_the_failed_upload():
    """Internal, for the reviewer's artifact -- not part of the applicant note."""
    result = triage(document_errors=[{"key": "input/director_id_passport.json", "state_key": "director_id"}])

    refs = next(item.evidence_refs for item in result.findings if item.requirement == "director_id")
    assert "input/director_id_passport.json" in refs


# ---------------------------------------------------------------------------
# assess() itself, independent of evidence_checks
# ---------------------------------------------------------------------------

def test_requirement_with_no_finding_is_treated_as_missing():
    """A checklist that silently skipped what it couldn't assess would route an
    unassessed submission straight to an underwriter."""
    result = assess({})

    assert result.route == "return_to_applicant"
    assert {item.status for item in result.findings} == {"missing"}
    assert len(result.findings) == len(CHECKLIST)


def test_satisfied_finding_must_cite_evidence():
    with pytest.raises(ValueError, match="evidence references"):
        EvidenceFinding(status="satisfied", explanation="Looks fine.")


def test_document_defect_must_carry_a_correction():
    with pytest.raises(ValueError, match="specific correction"):
        EvidenceFinding(status="stale", explanation="Too old.", evidence_refs=["input/x.json"])


def test_missing_finding_falls_back_to_the_checklist_action():
    """`missing` is exempt from the correction validator because assess fills
    it in from CHECKLIST -- so no outstanding item can reach the applicant
    without an action attached."""
    result = assess({"director_id": EvidenceFinding(status="missing", explanation="None supplied.")})

    correction = next(item.correction for item in result.findings if item.requirement == "director_id")
    assert correction == CHECKLIST["director_id"]


# ---------------------------------------------------------------------------
# Statement coverage maths
#
# Carried over from the manifest-based readiness workflow this replaced --
# the arithmetic is the reason the bank-statement check counts distinct days
# of coverage per account rather than counting files.
# ---------------------------------------------------------------------------

def _statement(start, end, account="12345678", bank="Big Bank"):
    return {"account_owner": "Acme Ltd", "bank_name": bank, "account_number": account,
            "address": ADDRESS, "start_date": start, "end_date": end,
            "balance": 12000.0, "payments_in": 5000.0, "payments_out": 3200.0}


def test_duplicate_statements_do_not_manufacture_coverage():
    """Three uploads of the same month are one month of history. Counting
    files here would let an applicant satisfy the three-month rule by
    uploading the same statement three times."""
    result = triage(bank_statements=[_statement("2024-05-01", "2024-05-31")] * 3)

    assert status_of(result, "bank_statements") == "incomplete"
    assert "duplicate and overlapping periods count only once" in result.applicant_note


def test_overlapping_statements_do_not_manufacture_coverage():
    result = triage(bank_statements=[
        _statement("2024-05-01", "2024-05-31"),
        _statement("2024-05-15", "2024-06-01"),
        _statement("2024-05-20", "2024-06-01"),
    ])

    assert status_of(result, "bank_statements") == "incomplete"


def test_separate_accounts_cannot_be_added_together():
    """A month each from three different accounts is not three months of
    history for any one account."""
    result = triage(bank_statements=[
        _statement("2024-03-01", "2024-03-31", account="11111111"),
        _statement("2024-04-01", "2024-04-30", account="22222222"),
        _statement("2024-05-01", "2024-05-31", account="33333333"),
    ])

    assert status_of(result, "bank_statements") == "incomplete"


def test_blank_account_details_cannot_establish_coverage():
    result = triage(bank_statements=[
        _statement("2024-03-01", "2024-03-31", account="  "),
        _statement("2024-04-01", "2024-04-30", account="  "),
        _statement("2024-05-01", "2024-05-31", account="  "),
    ])

    assert status_of(result, "bank_statements") == "unreadable"


def test_statement_ending_after_today_is_unreadable():
    """A period that hasn't finished yet is a dating error, not evidence."""
    result = triage(bank_statements=BANK_STATEMENTS + [_statement("2024-06-01", "2024-12-31")])

    assert status_of(result, "bank_statements") == "unreadable"


@pytest.mark.parametrize("end,expected", [
    ("2024-05-31", "satisfied"),   # 1 day old
    ("2024-03-04", "satisfied"),   # 89 days old -- inside the 90-day rule
    ("2024-03-03", "stale"),       # 90 days old -- outside it
])
def test_bank_recency_boundary(end, expected):
    """general.md: "most recent statement must be less than 90 days from date
    of application" -- 90 days exactly is already too old."""
    start = date.fromisoformat(end) - timedelta(days=120)
    result = triage(bank_statements=[_statement(start.isoformat(), end)])

    assert status_of(result, "bank_statements") == expected
