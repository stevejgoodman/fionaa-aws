"""Unit tests for policy_loader.py."""

import policy_loader as pl
from schemas import LoanType


def test_load_check_tool_names_for_invoice_factoring():
    assert pl.load_check_tool_names(LoanType.invoice_factoring) == ["compute_invoice_factoring_advance"]


def test_load_check_tool_names_for_invoice_discounting():
    assert pl.load_check_tool_names(LoanType.invoice_discounting) == ["compute_invoice_discounting_advance"]


def test_load_check_tool_names_empty_for_types_with_no_checks():
    for loan_type in (
        LoanType.unsecured_business_loans,
        LoanType.secured_business_loans,
        LoanType.revolving_credit_facility,
    ):
        assert pl.load_check_tool_names(loan_type) == []


def test_load_policy_text_strips_checks_comment():
    text = pl.load_policy_text(LoanType.invoice_factoring)
    assert "<!--" not in text
    assert "checks:" not in text
    assert "Invoice Factoring" in text


def test_load_policy_text_includes_general_rules():
    text = pl.load_policy_text(LoanType.secured_business_loans)
    assert "General rules" in text
    assert "Secured Business Loans" in text
