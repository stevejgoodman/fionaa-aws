"""Unit tests for groundedness.py -- plain functions, no agent/runtime/S3."""

import json

import groundedness as gr


def test_found_false_is_always_grounded():
    """Asymmetric by design -- see module docstring. Never checked, never
    overridden, regardless of tool_calls."""
    result = gr.check_companies_house_grounding({"company_number": "123"}, False, [])
    assert result.grounded is True
    assert result.reasons == []


def test_found_true_with_no_tool_calls_is_ungrounded():
    result = gr.check_companies_house_grounding({"company_number": "123"}, True, [])
    assert result.grounded is False
    assert "no CompaniesHouse tool call evidence" in result.reasons[0]


def test_found_true_with_only_non_companies_house_tool_calls_is_ungrounded():
    tool_calls = [{"tool": "geo-target___CheckSameArea", "result": "true"}]
    result = gr.check_companies_house_grounding({"company_number": "123"}, True, tool_calls)
    assert result.grounded is False


def test_found_true_with_companies_house_call_and_no_number_claim_is_grounded():
    """No company_number in the application -- nothing to cross-check, and
    a CompaniesHouse tool was actually called, so this is grounded (fuzzy
    name-only matches are left to the model's own judgment, see docstring)."""
    tool_calls = [
        {"tool": "CompaniesHouse___getCompanyProfile", "result": json.dumps({"company_name": "Acme Ltd"})}
    ]
    result = gr.check_companies_house_grounding({}, True, tool_calls)
    assert result.grounded is True


def test_found_true_with_matching_company_number_is_grounded():
    tool_calls = [
        {"tool": "CompaniesHouse___getCompanyProfile", "result": json.dumps({"company_number": "12345678"})}
    ]
    result = gr.check_companies_house_grounding({"company_number": "12345678"}, True, tool_calls)
    assert result.grounded is True


def test_found_true_with_mismatched_company_number_is_ungrounded():
    tool_calls = [
        {"tool": "CompaniesHouse___getCompanyProfile", "result": json.dumps({"company_number": "00000000"})}
    ]
    result = gr.check_companies_house_grounding({"company_number": "12345678"}, True, tool_calls)
    assert result.grounded is False
    assert "12345678" in result.reasons[0]
    assert "00000000" in result.reasons[0]


def test_company_number_match_ignores_case_and_whitespace():
    tool_calls = [
        {"tool": "CompaniesHouse___getCompanyProfile", "result": json.dumps({"company_number": " 1234 5678 "})}
    ]
    result = gr.check_companies_house_grounding({"company_number": "12345678"}, True, tool_calls)
    assert result.grounded is True


def test_company_number_nested_inside_search_results_is_found():
    """Different CompaniesHouse___* tools nest company_number at different
    depths (e.g. a search endpoint's `items` list vs. a profile endpoint's
    top level) -- the check walks the whole structure, not just top-level keys."""
    tool_calls = [
        {
            "tool": "CompaniesHouse___searchCompanies",
            "result": json.dumps({"items": [{"company_number": "12345678", "title": "Acme Ltd"}]}),
        }
    ]
    result = gr.check_companies_house_grounding({"company_number": "12345678"}, True, tool_calls)
    assert result.grounded is True


def test_non_json_tool_result_is_skipped_not_errored():
    """A tool result that isn't valid JSON (e.g. a plain-text error message)
    must not crash the check -- just contributes no company_number evidence."""
    tool_calls = [{"tool": "CompaniesHouse___getCompanyProfile", "result": "Service temporarily unavailable"}]
    result = gr.check_companies_house_grounding({"company_number": "12345678"}, True, tool_calls)
    # No parseable company_number anywhere -- falls back to "at least one
    # CompaniesHouse tool call exists", which is satisfied here.
    assert result.grounded is True
