"""Unit tests for redaction.py -- plain functions, no agent/runtime/S3."""

import json

import fionaa.redaction as r
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


def test_redact_bank_statements_redacts_address_first_line():
    docs = [{"account_owner": "Steve Goodman", "account_number": "40123456",
             "address": "3 Manor Road, Ruislip, Middlesex"}]

    redacted = r.redact_bank_statements(docs)

    assert redacted[0]["address"] == "[address redacted], Ruislip, Middlesex"
    assert redacted[0]["account_owner"] == "Steve Goodman"


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


def test_redact_companies_house_tool_result_handles_content_block_list_shape():
    """A ToolMessage's .content sometimes arrives as a list of content
    blocks ([{"type": "text", "text": "<json>"}]) rather than a bare JSON
    string -- the redactor must not silently pass this shape through
    unredacted."""
    raw = [{"type": "text", "text": json.dumps({
        "address_line_1": "Manor Road", "premises": "3", "locality": "Ruislip",
    })}]

    redacted = r.redact_companies_house_tool_result(raw)

    parsed = json.loads(redacted[0]["text"])
    assert parsed["address_line_1"] == "[address redacted]"
    assert parsed["premises"] == "[address redacted]"
    assert parsed["locality"] == "Ruislip"
    assert redacted[0]["type"] == "text"


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


def test_address_first_line_splits_on_comma():
    assert r.address_first_line("3 Manor Road, Ruislip") == "3 Manor Road"


def test_address_first_line_whole_string_when_no_comma():
    assert r.address_first_line("3 Manor Road") == "3 Manor Road"


def test_address_first_line_handles_none_and_empty():
    assert r.address_first_line(None) is None
    assert r.address_first_line("") is None


def test_sensitive_prose_values_collects_addresses_and_birth_year():
    application = {"company_address": "1 High St, London",
                    "director_residential_address": "14 Oak Avenue, Uxbridge",
                    "year_of_birth": "1972"}
    annual_accounts = [{"registered_address": "3 Manor Road, Ruislip"}]
    bank_statements = [{"address": "3 Manor Road, Ruislip"}]

    values = r.sensitive_prose_values(application, annual_accounts, bank_statements)

    assert set(values) == {"1 High St", "14 Oak Avenue", "3 Manor Road", "1972"}


def test_sensitive_prose_values_handles_none_and_missing_fields():
    assert r.sensitive_prose_values(None, None, None) == []
    assert r.sensitive_prose_values({}, [], []) == []


def test_redact_prose_values_scrubs_address_and_birth_year_from_llm_sentences():
    sensitive = r.sensitive_prose_values(
        {"year_of_birth": "1972"}, [{"registered_address": "3 Manor Road, Ruislip"}], None,
    )

    text = ("Applicant age: Steven Goodman born 1972, aged 54 at time of application. "
            "Proof of address: annual accounts show 3 Manor Road, Ruislip.")
    redacted = r.redact_prose_values(text, sensitive)

    assert "1972" not in redacted
    assert "3 Manor Road" not in redacted
    assert "[redacted]" in redacted
    # Town/postcode-level and unrelated figures are left alone.
    assert "Ruislip" in redacted
    assert "54" in redacted


def test_redact_prose_values_only_matches_numeric_values_on_word_boundary():
    redacted = r.redact_prose_values("The applicant is 19720 years old", ["1972"])

    assert redacted == "The applicant is 19720 years old"


def test_redact_prose_values_walks_nested_structures():
    sensitive = ["3 Manor Road"]
    value = {"clause_findings": ["Address is 3 Manor Road, Ruislip."], "eligible": "eligible"}

    redacted = r.redact_prose_values(value, sensitive)

    assert "3 Manor Road" not in redacted["clause_findings"][0]
    assert redacted["eligible"] == "eligible"


def test_redact_prose_values_passes_through_non_string_leaves():
    assert r.redact_prose_values(42, ["1972"]) == 42
    assert r.redact_prose_values(None, ["1972"]) is None


def test_redact_identity_documents_redacts_address_first_line_keeps_holder():
    """holder_name survives: it is the KYC signal triage checks (does the ID
    belong to the director named on the application?), the same reasoning that
    keeps bank_statements' account_owner unredacted."""
    docs = [{"document_kind": "utility bill", "holder_name": "Steve Goodman",
             "address": "3 Manor Road, Ruislip, Middlesex", "issue_date": "2026-08-01"}]

    redacted = r.redact_identity_documents(docs)

    assert redacted[0]["address"] == "[address redacted], Ruislip, Middlesex"
    assert redacted[0]["holder_name"] == "Steve Goodman"
    assert redacted[0]["issue_date"] == "2026-08-01"


def test_redact_identity_documents_leaves_director_id_alone():
    """A DirectorIdSchema document has no address field -- and deliberately no
    document number to redact either (see DirectorIdSchema's docstring)."""
    docs = [{"document_kind": "passport", "holder_name": "Steve Goodman", "expiry_date": "2030-01-01"}]

    assert r.redact_identity_documents(docs) == docs


def test_redact_identity_documents_handles_none_and_empty():
    assert r.redact_identity_documents(None) is None
    assert r.redact_identity_documents([]) == []


def test_redact_identity_documents_does_not_mutate_input():
    docs = [{"holder_name": "Steve Goodman", "address": "3 Manor Road, Ruislip"}]

    r.redact_identity_documents(docs)

    assert docs[0]["address"] == "3 Manor Road, Ruislip"
