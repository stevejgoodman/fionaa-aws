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
