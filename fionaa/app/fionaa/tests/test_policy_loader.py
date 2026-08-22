"""Unit tests for policy_loader.py."""

import policy_loader as pl
from schemas import LoanType


def test_load_check_tool_names_for_invoice_factoring():
    assert pl.load_check_tool_names(LoanType.invoice_factoring) == ["compute_invoice_factoring_advance"]


def test_load_check_tool_names_for_invoice_discounting():
    assert pl.load_check_tool_names(LoanType.invoice_discounting) == ["compute_invoice_discounting_advance"]


def test_load_check_tool_names_returns_amount_range_check_per_fixed_range_type():
    # These three loan types have a fixed min/max amount (as opposed to
    # turnover/invoice-value-relative ones like invoice-factoring), so each
    # declares its own deterministic range-check tool -- see check_tools.py.
    for loan_type, expected_tool in (
        (LoanType.unsecured_business_loans, "check_unsecured_business_loan_amount_in_range"),
        (LoanType.secured_business_loans, "check_secured_business_loan_amount_in_range"),
        (LoanType.revolving_credit_facility, "check_revolving_credit_facility_amount_in_range"),
    ):
        assert pl.load_check_tool_names(loan_type) == [expected_tool]


def test_load_policy_text_strips_checks_comment():
    text = pl.load_policy_text(LoanType.invoice_factoring)
    assert "<!--" not in text
    assert "checks:" not in text
    assert "Invoice Factoring" in text


def test_load_policy_text_includes_general_rules():
    text = pl.load_policy_text(LoanType.secured_business_loans)
    assert "General rules" in text
    assert "Secured Business Loans" in text
