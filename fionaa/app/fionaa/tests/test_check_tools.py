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


# ---------------------------------------------------------------------------
# cross_check_financial_figures — deterministic turnover/profit comparison
# ---------------------------------------------------------------------------
# Plain function, not an @tool -- see check_tools.py's module comment for
# why (it must run unconditionally, not be agent tool-choice).

def test_cross_check_financial_figures_no_discrepancy_for_matching_figures():
    application = {"annual_turnover": 100000, "annual_profit": 20000}
    annual_accounts = [{"turnover_current_year": 100000, "profit_current_year": 20000}]

    result = ct.cross_check_financial_figures(application, annual_accounts, companies_house=None)

    assert result.any_material_discrepancy is False
    assert all(c.delta_pct == 0 for c in result.comparisons)
    assert all(c.material is False for c in result.comparisons)


def test_cross_check_financial_figures_few_percent_delta_is_non_material():
    application = {"annual_turnover": 100000, "annual_profit": 20000}
    annual_accounts = [{"turnover_current_year": 105000, "profit_current_year": 20000}]

    result = ct.cross_check_financial_figures(application, annual_accounts, companies_house=None)

    turnover_comparison = next(c for c in result.comparisons if c.field.startswith("annual_turnover"))
    assert turnover_comparison.delta_pct == pytest.approx(5.0)
    assert turnover_comparison.material is False
    assert result.any_material_discrepancy is False


def test_cross_check_financial_figures_tens_of_percent_delta_is_material():
    application = {"annual_turnover": 100000, "annual_profit": 20000}
    annual_accounts = [{"turnover_current_year": 60000, "profit_current_year": 20000}]

    result = ct.cross_check_financial_figures(application, annual_accounts, companies_house=None)

    turnover_comparison = next(c for c in result.comparisons if c.field.startswith("annual_turnover"))
    assert turnover_comparison.delta_pct == pytest.approx(40.0)
    assert turnover_comparison.material is True
    assert result.any_material_discrepancy is True


def test_cross_check_financial_figures_handles_no_annual_accounts():
    result = ct.cross_check_financial_figures(
        {"annual_turnover": 100000, "annual_profit": 20000}, [], companies_house=None
    )

    assert result.comparisons == []
    assert result.any_material_discrepancy is False


def test_cross_check_financial_figures_picks_most_recent_annual_accounts():
    application = {"annual_turnover": 100000, "annual_profit": 20000}
    annual_accounts = [
        {"accounting_year": "2024-12-31", "turnover_current_year": 40000, "profit_current_year": 5000},
        {"accounting_year": "2025-12-31", "turnover_current_year": 100000, "profit_current_year": 20000},
    ]

    result = ct.cross_check_financial_figures(application, annual_accounts, companies_house=None)

    assert result.any_material_discrepancy is False


def test_cross_check_financial_figures_handles_zero_application_turnover():
    result = ct.cross_check_financial_figures(
        {"annual_turnover": 0, "annual_profit": 0},
        [{"turnover_current_year": 0, "profit_current_year": 0}],
        companies_house=None,
    )

    assert result.any_material_discrepancy is False

    result_with_mismatch = ct.cross_check_financial_figures(
        {"annual_turnover": 0, "annual_profit": 0},
        [{"turnover_current_year": 50000, "profit_current_year": 0}],
        companies_house=None,
    )

    turnover_comparison = next(
        c for c in result_with_mismatch.comparisons if c.field.startswith("annual_turnover")
    )
    assert turnover_comparison.material is True
    assert result_with_mismatch.any_material_discrepancy is True
