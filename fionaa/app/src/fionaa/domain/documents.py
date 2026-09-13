"""Business models for documents."""
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class BankStatementSchema(BaseModel):
    account_owner: str = Field(description="The name of the account "
                               "owner(s).", title="Account Owner")
    bank_name: str = Field(description="The name of the bank.",
                           title="Bank Name")
    account_number: str = Field(description="The bank account number.",
                                title="Account Number")
    start_date: str = Field(description="The start date for the statement.",
                          title="Start Date")

    end_date: str = Field(description="The ending date for the statement.",
                          title="End Date")

    # balance is deliberately NOT bounded -- an overdrawn account is a
    # legitimate, meaningful negative value financial_assessment needs to
    # see, not something schema validation should reject before it's seen.
    balance: float = Field(description="The current balance of the bank account.",
                          title="Bank Balance")
    payments_in: float = Field(ge=0, description="total payments in during statement period",
                          title="Payments In")
    payments_out: float = Field(ge=0, description="total payments out during statement period",
                        title="Payments Out")


class AnnualAccountsSchema(BaseModel):
    # frontmatter
    company_name: str = Field(description="Name of Company")
    director: str = Field(description="Director, CEO or Managing Director of the company."
                               , title="Director")
    registered_address: str = Field(description="Company Registered Address "
                                  "institution.", title="Registered Address")

    registration_number: str = Field(description="Companies House Registered Number "
                                  "institution.", title="Registration Number")
    accounting_year: date = Field(description="The date  of the accounts statement",
    title="Accounting Year")

    # P&L or Income statement
    #
    # turnover_*/tangible_fixed_assets_*/debtors_* below get ge=0 (these can
    # never legitimately be negative); operating_profit_*/profit_*/
    # cash_at_bank_* are deliberately left unbounded -- a loss or an
    # overdraft is a real, meaningful negative value financial_assessment
    # needs to see, not something schema validation should reject before
    # it's seen.

    turnover_current_year: int = Field(ge=0, description = "Turnover for current year")
    operating_profit_current_year: int = Field(description = "operating_profit for current financial year")
    profit_current_year: int = Field(description="Annual profit for current financial year")

    turnover_last_year: int | None = Field(ge=0, description = "Turnover for last year")
    operating_profit_last_year: int | None = Field(description = "operating_profit for last year")
    profit_last_year: int | None = Field(description="Annual profit for last year")

    # balance sheet

    tangible_fixed_assets_current_year: int | None = Field(ge=0, description = "tangible fixed assets for current  financial year")
    debtors_current_year: int | None = Field(ge=0, description="debtors current financial year")
    cash_at_bank_current_year: int | None  = Field(description="cash at band or in hand current financial year")

    tangible_fixed_assets_last_year: int | None = Field(ge=0, description = "tangible fixed assets for last year")
    debtors_last_year: int | None = Field(ge=0, description="debtors last year")
    cash_at_bank_last_year: int | None  = Field(description="cash at band or in hand last year")


class DocumentType(str, Enum):
    bank_statement = "bank_statement"
    annual_company_report = "annual_company_report"

    # Descriptions for each value
    def describe(self) -> str:
        descriptions = {
            "bank_statement": "A checking or savings account statement "
            "with balances and transactions. Usually the period shown is for 1 month duration.",

            "annual_company_report": "A company annual accounts or annual report "
            "showing income statement (or P&L statement) and "
            "balance sheet and other company information from the year such as a directors report ",
        }
        return descriptions[self.value]


class DocType(BaseModel):
    type: DocumentType = Field(
        description="The type of document being analyzed.",
        title="Document Type",
    )
