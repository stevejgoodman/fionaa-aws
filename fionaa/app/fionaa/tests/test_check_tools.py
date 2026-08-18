"""Unit tests for check_tools.py — plain functions, no agent/runtime involved."""

import check_tools as ct


def test_compute_invoice_factoring_advance_uses_midpoint_rate():
    assert ct.compute_invoice_factoring_advance.invoke({"invoices_owed": 148000}) == 118400


def test_compute_invoice_discounting_advance_uses_midpoint_rate():
    assert ct.compute_invoice_discounting_advance.invoke({"invoices_owed": 148000}) == 118400


def test_check_tools_pool_names_are_unique():
    names = [t.name for t in ct.CHECK_TOOLS_POOL]
    assert len(names) == len(set(names))
