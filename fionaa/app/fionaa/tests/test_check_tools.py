"""Unit tests for check_tools.py — plain functions, no agent/runtime involved."""

import pytest

import check_tools as ct


def test_compute_invoice_factoring_advance_uses_midpoint_rate():
    assert ct.compute_invoice_factoring_advance.invoke({"invoices_owed": 148000}) == 118400


def test_compute_invoice_discounting_advance_uses_midpoint_rate():
    assert ct.compute_invoice_discounting_advance.invoke({"invoices_owed": 148000}) == 118400


def test_compute_monthly_repayment_divides_amount_by_term():
    assert ct.compute_monthly_repayment.invoke({"amount_borrowed": 10000, "term_months": 24}) == 416.67


def test_compute_monthly_repayment_rejects_non_positive_term():
    with pytest.raises(ValueError):
        ct.compute_monthly_repayment.func(amount_borrowed=10000, term_months=0)


def test_check_tools_pool_names_are_unique():
    names = [t.name for t in ct.CHECK_TOOLS_POOL]
    assert len(names) == len(set(names))


def test_check_bank_statements_recent_and_sufficient_happy_path():
    result = ct.check_bank_statements_recent_and_sufficient.invoke(
        {
            "statement_end_dates": ["2026-06-30", "2026-07-31", "2026-08-25"],
            "reference_date": "2026-08-30",
        }
    )
    assert result == {
        "statement_count": 3,
        "count_sufficient": True,
        "days_since_most_recent_statement": 5,
        "recent_enough": True,
    }


def test_check_bank_statements_recent_and_sufficient_flags_too_few():
    result = ct.check_bank_statements_recent_and_sufficient.invoke(
        {"statement_end_dates": ["2026-08-25"], "reference_date": "2026-08-30"}
    )
    assert result["statement_count"] == 1
    assert result["count_sufficient"] is False


def test_check_bank_statements_recent_and_sufficient_flags_stale():
    result = ct.check_bank_statements_recent_and_sufficient.invoke(
        {
            "statement_end_dates": ["2025-08-31", "2025-09-30", "2025-10-31"],
            "reference_date": "2026-08-30",
        }
    )
    assert result["count_sufficient"] is True
    assert result["recent_enough"] is False


def test_check_bank_statements_recent_and_sufficient_handles_no_statements():
    result = ct.check_bank_statements_recent_and_sufficient.invoke(
        {"statement_end_dates": [], "reference_date": "2026-08-30"}
    )
    assert result == {
        "statement_count": 0,
        "count_sufficient": False,
        "days_since_most_recent_statement": None,
        "recent_enough": False,
    }
