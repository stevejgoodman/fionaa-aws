from datetime import date

from fionaa.ar_facts import build_derived_facts


def test_goodmans_scenario_derives_facts_matching_ar_policy_variable_names():
    """Regression for the "18/19 inconclusive" bug: without these
    precomputed, policy-variable-named facts, Bedrock's Automated Reasoning
    checker couldn't reliably translate raw self-reported JSON into its own
    formal variables. See ar_facts.py's module docstring."""
    application = {
        "loan_amount": 15000,
        "loan_term": 24,
        "year_of_birth": "1972",
        "company_name": "Goodman's Consulting Limited",
        "trading_start_date": "2012-07-11",
        "annual_turnover": 180000,
        "director_first_name": "Steven",
        "director_surname": "Goodman",
        "director_residential_address": "3 Manor Road, Ruislip",
    }
    annual_accounts = [{"accounting_year": "2016-12-31", "registered_address": "3 Manor Road, Ruislip"}]
    bank_statements = [
        {"end_date": "2017-11-30", "address": "3 Manor Road, Ruislip"},
        {"end_date": "2017-10-31", "address": "3 Manor Road, Ruislip"},
        {"end_date": "2017-09-30", "address": "3 Manor Road, Ruislip"},
    ]
    director_id = [{"document_kind": "passport", "holder_name": "Steven Goodman", "expiry_date": "2030-01-01"}]
    proof_of_address = [{
        "document_kind": "utility bill", "holder_name": "Steven Goodman",
        "address": "3 Manor Road, Ruislip", "issue_date": "2026-08-01",
    }]
    companies_house = {"found": True, "confidence": "high", "summary": "..."}
    today = date(2026, 9, 16)

    facts = build_derived_facts(
        application, annual_accounts, bank_statements, companies_house, today,
        director_id=director_id, proof_of_address=proof_of_address,
    )

    assert facts["loanAmount"] == 15000
    assert facts["loanTermMonths"] == 24
    assert facts["applicantAge"] == 54
    assert facts["businessType"] == "BusinessType_LIMITED_COMPANY"
    assert facts["hasCompaniesHouseMatch"] is True
    assert facts["isRegisteredInUK"] is True
    assert facts["hasUKAddress"] is True
    assert facts["isUKBased"] is True
    assert facts["tradingHistoryMonths"] == 170
    assert facts["hasAnnualAccounts"] is True
    assert facts["hasAccountsOrManagementInformation"] is True
    assert facts["lastAccountingDateWithinPast12Months"] is False
    assert facts["bankStatementsMonthsCount"] == 3
    assert facts["mostRecentStatementAgeDays"] > 90
    assert facts["hasRecentBankStatements"] is False
    assert facts["hasProofOfDirectorAddress"] is True
    assert facts["hasValidDirectorID"] is True
    assert facts["directorIDType"] == "DirectorIDType_PASSPORT"


def test_llp_and_plc_suffixes_map_to_the_correct_business_type():
    today = date(2026, 1, 1)
    llp = build_derived_facts({"company_name": "Example Advisers LLP"}, [], [], None, today)
    plc = build_derived_facts({"company_name": "Example Group plc"}, [], [], None, today)
    sole_trader = build_derived_facts({"company_name": "Jane Smith Consulting"}, [], [], None, today)

    assert llp["businessType"] == "BusinessType_PARTNERSHIP"
    assert plc["businessType"] == "BusinessType_LIMITED_COMPANY"
    assert "businessType" not in sole_trader


def test_unconfirmed_companies_house_asserts_nothing_negative():
    """found=False (or missing) means unconfirmed, not proof the business
    isn't UK-based -- omit rather than assert False (general.md's rule is a
    one-directional implication: a match proves UK-based, no match doesn't
    prove the opposite)."""
    today = date(2026, 1, 1)

    facts_not_found = build_derived_facts({}, [], [], {"found": False}, today)
    facts_missing = build_derived_facts({}, [], [], None, today)

    for facts in (facts_not_found, facts_missing):
        assert "isUKBased" not in facts
        assert "isRegisteredInUK" not in facts
        assert "hasUKAddress" not in facts
        assert "hasCompaniesHouseMatch" not in facts


def test_proof_of_address_matching_declared_address_is_true():
    """hasProofOfDirectorAddress checks the director's actual proof-of-address
    document against their declared residential address -- not, as a prior
    buggy implementation did, whether the bank statement's business address
    happens to match the annual accounts' registered address (those are
    unrelated by design; a director's home and a company's trading address
    have no reason to ever match)."""
    today = date(2026, 1, 1)
    application = {
        "director_first_name": "Jane", "director_surname": "Smith",
        "director_residential_address": "1 High Street, London",
    }
    proof_of_address = [{
        "document_kind": "utility bill", "holder_name": "Jane Smith",
        "address": "1 High Street, London", "issue_date": "2025-12-01",
    }]

    facts = build_derived_facts(application, [], [], None, today, proof_of_address=proof_of_address)

    assert facts["hasProofOfDirectorAddress"] is True


def test_proof_of_address_not_matching_declared_address_is_false():
    today = date(2026, 1, 1)
    application = {
        "director_first_name": "Jane", "director_surname": "Smith",
        "director_residential_address": "1 High Street, London",
    }
    proof_of_address = [{
        "document_kind": "utility bill", "holder_name": "Jane Smith",
        "address": "9 Other Road, Manchester", "issue_date": "2025-12-01",
    }]

    facts = build_derived_facts(application, [], [], None, today, proof_of_address=proof_of_address)

    assert facts["hasProofOfDirectorAddress"] is False


def test_no_proof_of_address_document_leaves_fact_unknown_not_false():
    today = date(2026, 1, 1)
    bank_statements = [{"address": "1 High Street, London"}]

    facts = build_derived_facts({}, [], bank_statements, None, today)

    assert "hasProofOfDirectorAddress" not in facts


def test_valid_director_id_reports_its_type():
    today = date(2026, 1, 1)
    application = {"director_first_name": "Jane", "director_surname": "Smith"}
    director_id = [{"document_kind": "driving_licence", "holder_name": "Jane Smith", "expiry_date": "2030-01-01"}]

    facts = build_derived_facts(application, [], [], None, today, director_id=director_id)

    assert facts["hasValidDirectorID"] is True
    assert facts["directorIDType"] == "DirectorIDType_DRIVERS_LICENCE"


def test_expired_director_id_is_false():
    today = date(2026, 1, 1)
    application = {"director_first_name": "Jane", "director_surname": "Smith"}
    director_id = [{"document_kind": "passport", "holder_name": "Jane Smith", "expiry_date": "2025-01-01"}]

    facts = build_derived_facts(application, [], [], None, today, director_id=director_id)

    assert facts["hasValidDirectorID"] is False
    assert "directorIDType" not in facts


def test_no_director_id_document_leaves_facts_unknown_not_false():
    today = date(2026, 1, 1)

    facts = build_derived_facts({}, [], [], None, today)

    assert "hasValidDirectorID" not in facts
    assert "directorIDType" not in facts


def test_no_bank_statements_is_a_known_negative_not_unknown():
    today = date(2026, 1, 1)

    facts = build_derived_facts({}, [], [], None, today)

    assert facts["bankStatementsMonthsCount"] == 0
    assert facts["hasRecentBankStatements"] is False
    assert "mostRecentStatementAgeDays" not in facts


import pytest


def _minimal(**application):
    """Only the fields these facts read; everything else stays unknown."""
    return build_derived_facts(
        {"annual_turnover": 180000, **application}, [], [], None, date(2026, 9, 23))


@pytest.mark.parametrize("declared,expected", [(True, True), (False, False)])
def test_vat_and_borrowing_declarations_become_facts(declared, expected):
    """Both gate a conditional document rule, so a false declaration is as
    useful as a true one -- it satisfies the rule outright."""
    facts = _minimal(vat_registered=declared, has_existing_borrowing=declared)

    assert facts["isVATRegistered"] is expected
    assert facts["hasExistingBorrowing"] is expected


def test_undeclared_vat_and_borrowing_stay_unknown_not_false():
    """Unknown is not no: both fields are optional on the form, and an
    absent declaration must not be read as "not VAT registered" -- that
    would discharge the VAT-return requirement for a business that has
    one."""
    facts = _minimal()

    assert "isVATRegistered" not in facts
    assert "hasExistingBorrowing" not in facts


@pytest.mark.parametrize("declared,expected", [
    ("property", "CollateralAssetType_PROPERTY"),
    ("intangible_assets", "CollateralAssetType_INTANGIBLE_ASSETS"),
])
def test_declared_collateral_asset_type_maps_to_the_policy_enum(declared, expected):
    assert _minimal(collateral_asset_type=declared)["collateralAssetType"] == expected


def test_unmapped_collateral_asset_type_is_unknown_not_other():
    """CollateralAssetType_OTHER would be a definite statement that the
    asset is ineligible as security. An absent or unrecognised declaration
    says nothing at all."""
    assert "collateralAssetType" not in _minimal()
    assert "collateralAssetType" not in _minimal(collateral_asset_type="racehorse")


@pytest.mark.parametrize("declared,expected", [(True, True), (False, False)])
def test_facility_terms_are_taken_from_the_declaration(declared, expected):
    facts = _minimal(personal_guarantee_agreed=declared, facility_is_secured=declared)

    assert facts["hasPersonalGuarantee"] is expected
    assert facts["isSecuredFacility"] is expected


def test_undeclared_facility_terms_stay_unknown():
    facts = _minimal()

    assert "hasPersonalGuarantee" not in facts and "isSecuredFacility" not in facts


def test_supporting_documents_bind_their_policy_variables():
    """One security_asset document serves both policies that ask about
    collateral: the secured loan's proof of ownership/valuation and a
    secured revolving facility's security/asset details."""
    facts = build_derived_facts(
        {"annual_turnover": 180000}, [], [], None, date(2026, 9, 23),
        vat_returns=[{"business_name": "Example Ltd"}],
        existing_borrowing=[{"lender_name": "Example Bank"}],
        security_assets=[{"asset_type": "property"}],
    )

    assert facts["hasVATReturns"] is True
    assert facts["hasBorrowingDetails"] is True
    assert facts["hasProofOfCollateralOwnershipValuation"] is True
    assert facts["hasSecurityAssetDetails"] is True


def test_a_document_type_that_was_never_loaded_is_unknown():
    """None means this product's policy doesn't ask for the document, so
    nothing was looked for -- distinct from looking and finding none."""
    facts = _minimal()

    for variable in ("hasVATReturns", "hasBorrowingDetails",
                     "hasProofOfCollateralOwnershipValuation", "hasSecurityAssetDetails"):
        assert variable not in facts


def test_a_document_type_that_was_loaded_and_empty_is_a_known_negative():
    """The store is the complete record of what was submitted, so "looked
    for and not there" is knowledge, not absence of it. Reporting it as
    unknown would leave a missing document unprovable -- the documentation
    claim could be confirmed but never refuted."""
    facts = build_derived_facts(
        {"annual_turnover": 180000}, [], [], None, date(2026, 9, 23),
        vat_returns=[], existing_borrowing=[], security_assets=[])

    assert facts["hasVATReturns"] is False
    assert facts["hasBorrowingDetails"] is False
    assert facts["hasProofOfCollateralOwnershipValuation"] is False
    assert facts["hasSecurityAssetDetails"] is False
