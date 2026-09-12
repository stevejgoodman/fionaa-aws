"""Unit tests for schemas.py's numeric bounds -- ApplicationFormSchema/
AnnualAccountsSchema/BankStatementSchema fields that can never legitimately
be negative (or, for director_percentage_control, over 100)."""

import pytest
from pydantic import ValidationError

from document_fixtures import (
    GOODAI_ANNUAL_ACCOUNTS_HAPPY,
    GOODAI_APPLICATION,
    GOODAI_BANK_STATEMENTS_RECENT,
)
from schemas import AnnualAccountsSchema, ApplicationFormSchema, BankStatementSchema


def test_goodai_application_fixture_still_validates():
    """Real fixture already used across the eval suite -- confirms the new
    bounds don't break it."""
    ApplicationFormSchema.model_validate(GOODAI_APPLICATION)


def test_goodai_annual_accounts_fixture_still_validates():
    AnnualAccountsSchema.model_validate(GOODAI_ANNUAL_ACCOUNTS_HAPPY)


def test_goodai_bank_statement_fixture_still_validates():
    BankStatementSchema.model_validate(GOODAI_BANK_STATEMENTS_RECENT[0])


@pytest.mark.parametrize(
    "field",
    [
        "loan_amount",
        "annual_turnover",
        "invoices_owed",
        "monthly_expenses",
        "monthly_rent_or_mortgage",
        "num_dependants",
        "monthly_childcare_expenses",
        "monthly_non_business_income",
        "monthly_other_household_income",
    ],
)
def test_application_rejects_negative_amount(field):
    application = {**GOODAI_APPLICATION, field: -1}
    with pytest.raises(ValidationError):
        ApplicationFormSchema.model_validate(application)


def test_application_rejects_zero_or_negative_loan_term():
    with pytest.raises(ValidationError):
        ApplicationFormSchema.model_validate({**GOODAI_APPLICATION, "loan_term": 0})


def test_application_rejects_director_percentage_control_over_100():
    with pytest.raises(ValidationError):
        ApplicationFormSchema.model_validate({**GOODAI_APPLICATION, "director_percentage_control": 101})


def test_application_rejects_negative_director_percentage_control():
    with pytest.raises(ValidationError):
        ApplicationFormSchema.model_validate({**GOODAI_APPLICATION, "director_percentage_control": -1})


def test_application_allows_a_loss_for_annual_profit():
    """annual_profit is deliberately unbounded -- a loss is a legitimate
    value, not a validation error."""
    ApplicationFormSchema.model_validate({**GOODAI_APPLICATION, "annual_profit": -5000})


@pytest.mark.parametrize(
    "field",
    [
        "turnover_current_year",
        "turnover_last_year",
        "tangible_fixed_assets_current_year",
        "tangible_fixed_assets_last_year",
        "debtors_current_year",
        "debtors_last_year",
    ],
)
def test_annual_accounts_rejects_negative_amount(field):
    accounts = {**GOODAI_ANNUAL_ACCOUNTS_HAPPY, field: -1}
    with pytest.raises(ValidationError):
        AnnualAccountsSchema.model_validate(accounts)


@pytest.mark.parametrize(
    "field", ["profit_current_year", "profit_last_year", "operating_profit_current_year",
              "operating_profit_last_year", "cash_at_bank_current_year", "cash_at_bank_last_year"]
)
def test_annual_accounts_allows_a_loss_or_overdraft(field):
    """These are deliberately unbounded -- see schemas.py's comment."""
    AnnualAccountsSchema.model_validate({**GOODAI_ANNUAL_ACCOUNTS_HAPPY, field: -1000})


@pytest.mark.parametrize("field", ["payments_in", "payments_out"])
def test_bank_statement_rejects_negative_amount(field):
    statement = {**GOODAI_BANK_STATEMENTS_RECENT[0], field: -1}
    with pytest.raises(ValidationError):
        BankStatementSchema.model_validate(statement)


def test_bank_statement_allows_negative_balance():
    """balance is deliberately unbounded -- an overdrawn account is
    legitimate, see schemas.py's comment."""
    BankStatementSchema.model_validate({**GOODAI_BANK_STATEMENTS_RECENT[0], "balance": -100.0})
