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
    }
    annual_accounts = [{"accounting_year": "2016-12-31", "registered_address": "3 Manor Road, Ruislip"}]
    bank_statements = [
        {"end_date": "2017-11-30", "address": "3 Manor Road, Ruislip"},
        {"end_date": "2017-10-31", "address": "3 Manor Road, Ruislip"},
        {"end_date": "2017-09-30", "address": "3 Manor Road, Ruislip"},
    ]
    companies_house = {"found": True, "confidence": "high", "summary": "..."}
    today = date(2026, 9, 16)

    facts = build_derived_facts(application, annual_accounts, bank_statements, companies_house, today)

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


def test_mismatched_addresses_do_not_reconcile():
    today = date(2026, 1, 1)
    annual_accounts = [{"registered_address": "1 High Street, London"}]
    bank_statements = [{"address": "9 Other Road, Manchester"}]

    facts = build_derived_facts({}, annual_accounts, bank_statements, None, today)

    assert facts["hasProofOfDirectorAddress"] is False


def test_missing_source_leaves_proof_of_address_unknown_not_false():
    today = date(2026, 1, 1)
    bank_statements = [{"address": "1 High Street, London"}]

    facts = build_derived_facts({}, [], bank_statements, None, today)

    assert "hasProofOfDirectorAddress" not in facts


def test_no_bank_statements_is_a_known_negative_not_unknown():
    today = date(2026, 1, 1)

    facts = build_derived_facts({}, [], [], None, today)

    assert facts["bankStatementsMonthsCount"] == 0
    assert facts["hasRecentBankStatements"] is False
    assert "mostRecentStatementAgeDays" not in facts
