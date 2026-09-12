"""Unit tests for redaction.py -- plain functions, no agent/runtime/S3."""

import json

import redaction as r


def test_redact_address_line_masks_first_line_keeps_town():
    assert r.redact_address_line("14 Oak Avenue, Uxbridge") == "[address redacted], Uxbridge"


def test_redact_address_line_fully_replaces_when_no_comma():
    assert r.redact_address_line("14 Oak Avenue") == "[address redacted]"


def test_redact_address_line_handles_none_and_empty():
    assert r.redact_address_line(None) is None
    assert r.redact_address_line("") == ""


def test_redact_application_masks_addresses_and_phone():
    application = {
        "company_name": "Acme Ltd",
        "applicant_name": "Steve Goodman",
        "company_address": "1 High St, London",
        "director_first_name": "Steve",
        "director_surname": "Goodman",
        "director_mobile_phone": "+44 7700 900000",
        "director_residential_address": "14 Oak Avenue, Uxbridge",
        "annual_turnover": 100000,
    }

    redacted = r.redact_application(application)

    assert redacted["company_address"] == "[address redacted], London"
    assert redacted["director_residential_address"] == "[address redacted], Uxbridge"
    assert redacted["director_mobile_phone"] == "[redacted]"
    # Identity fields deliberately untouched -- see module docstring.
    assert redacted["applicant_name"] == "Steve Goodman"
    assert redacted["director_first_name"] == "Steve"
    assert redacted["director_surname"] == "Goodman"
    # Non-PII business data untouched.
    assert redacted["company_name"] == "Acme Ltd"
    assert redacted["annual_turnover"] == 100000
    # Original left unmodified.
    assert application["company_address"] == "1 High St, London"


def test_redact_application_handles_none_and_missing_fields():
    assert r.redact_application(None) is None
    assert r.redact_application({"company_name": "Acme Ltd"}) == {"company_name": "Acme Ltd"}


def test_redact_annual_accounts_masks_address_keeps_director_name():
    docs = [{"director": "Steve Goodman", "registered_address": "14 Oak Avenue, Uxbridge", "turnover_current_year": 100000}]

    redacted = r.redact_annual_accounts(docs)

    assert redacted[0]["registered_address"] == "[address redacted], Uxbridge"
    assert redacted[0]["director"] == "Steve Goodman"
    assert redacted[0]["turnover_current_year"] == 100000


def test_redact_annual_accounts_handles_none_and_empty():
    assert r.redact_annual_accounts(None) is None
    assert r.redact_annual_accounts([]) == []


def test_redact_bank_statements_masks_account_number_keeps_owner():
    docs = [{"account_owner": "Acme Ltd", "account_number": "40123456", "balance": 100.0}]

    redacted = r.redact_bank_statements(docs)

    assert redacted[0]["account_number"] == "[redacted]"
    assert redacted[0]["account_owner"] == "Acme Ltd"
    assert redacted[0]["balance"] == 100.0


def test_redact_bank_statements_handles_none_and_empty():
    assert r.redact_bank_statements(None) is None
    assert r.redact_bank_statements([]) == []


def test_redact_companies_house_tool_result_strips_nested_address_lines():
    raw = json.dumps({
        "company_name": "GoodAI Consulting",
        "company_status": "active",
        "registered_office_address": {
            "address_line_1": "Manor Road",
            "address_line_2": "Suite 4",
            "locality": "Ruislip",
            "postal_code": "HA4 8QG",
            "country": "England",
        },
        "officers": [
            {"name": "Steve Goodman", "officer_role": "director"},
        ],
    })

    redacted = json.loads(r.redact_companies_house_tool_result(raw))

    assert redacted["registered_office_address"]["address_line_1"] == "[address redacted]"
    assert redacted["registered_office_address"]["address_line_2"] == "[address redacted]"
    assert redacted["registered_office_address"]["locality"] == "Ruislip"
    assert redacted["registered_office_address"]["postal_code"] == "HA4 8QG"
    # Names/status/role deliberately untouched -- see module docstring.
    assert redacted["company_name"] == "GoodAI Consulting"
    assert redacted["company_status"] == "active"
    assert redacted["officers"][0]["name"] == "Steve Goodman"


def test_redact_companies_house_tool_result_handles_premises_key():
    raw = json.dumps({"premises": "14", "locality": "Uxbridge"})

    redacted = json.loads(r.redact_companies_house_tool_result(raw))

    assert redacted["premises"] == "[address redacted]"
    assert redacted["locality"] == "Uxbridge"


def test_redact_companies_house_tool_result_leaves_non_json_unchanged():
    assert r.redact_companies_house_tool_result("not json at all") == "not json at all"


def test_redact_tool_calls_only_touches_companies_house_entries():
    tool_calls = [
        {
            "tool": "CompaniesHouse___getCompanyProfile",
            "result": json.dumps({"address_line_1": "14 Oak Avenue", "locality": "Uxbridge"}),
        },
        {"tool": "websearch-target___WebSearch", "result": "Acme Ltd is based on Oak Avenue"},
        {"tool": "compute_monthly_repayment", "result": "416.67"},
    ]

    redacted = r.redact_tool_calls(tool_calls)

    ch_result = json.loads(redacted[0]["result"])
    assert ch_result["address_line_1"] == "[address redacted]"
    assert ch_result["locality"] == "Uxbridge"
    # Non-CompaniesHouse tool calls pass through untouched.
    assert redacted[1] == tool_calls[1]
    assert redacted[2] == tool_calls[2]


def test_redact_tool_calls_handles_none_and_empty():
    assert r.redact_tool_calls(None) is None
    assert r.redact_tool_calls([]) == []
