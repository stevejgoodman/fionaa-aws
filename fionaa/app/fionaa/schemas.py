
# Schemas for the input documents
# could be used for OCR? Ned to eche the AWS BDT docs

# todo add more schmas for passportid, driverslic.
#
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from storage import ApplicationStore, PolicyDocStore


class LoanType(str, Enum):
    unsecured_business_loans = "unsecured-business-loans"
    revolving_credit_facility = "revolving-credit-facility"
    secured_business_loans = "secured-business-loans"
    invoice_discounting = "invoice-discounting"
    invoice_factoring = "invoice-factoring"


class ApplicationFormSchema(BaseModel):
    applicant_name: str = Field(description="Name of person applying- should be person with sigificant control")
    year_of_birth: str = Field(description="applicant birth year")
    company_name: str = Field(description="Comany registered name")
    company_address: str = Field(description="registered address")

    loan_type: LoanType = Field(description="The type of loan being applied for")
    loan_purpose: str = Field(description="The stated purpose of the loan")
    loan_amount: int | None = Field(default=None, description="amount to be borrowed optional depending on "
                                     "loan type - UK Pounds")
    loan_term: int | None = Field(default=None, descrption="term in months - optional depending on loan type")

    companies_house_registered: bool = Field(description="Whether the company is registered with Companies House")
    industry: str = Field(description="The applicant's industry / business sector")
    trading_start_date: date = Field(description="The date trading began")

    annual_turnover: int = Field(description="Typical annual turnover in UK Pounds")
    annual_profit: int = Field(description="Profit for the last 12 months in UK Pounds")
    income_decrease_expected: bool = Field(description="Whether the applicant expects income to decrease "
                                            "in the next 12 months")
    accepts_card_payments: bool = Field(description="Whether the business accepts card payments")
    invoices_owed: int = Field(description="Amount owed to the business in unpaid invoices, UK Pounds")

    monthly_expenses: int = Field(description="Monthly business expense amount, not including mortgage or "
                                   "rent repayments, UK Pounds")
    monthly_rent_or_mortgage: int = Field(description="Monthly rent or mortgage payment, UK Pounds")
    num_dependants: int = Field(description="Number of dependants the applicant has")
    monthly_childcare_expenses: int = Field(description="Typical monthly spend on childcare expenses, UK Pounds")
    monthly_non_business_income: int = Field(description="Non-business income received each month, UK Pounds")
    monthly_other_household_income: int = Field(description="Other monthly household income, UK Pounds")

    director_first_name: str = Field(description="First name of the director / beneficial owner "
                                      "with significant control")
    director_surname: str = Field(description="Surname of the director / beneficial owner with significant control")
    director_percentage_control: float = Field(description="Percentage of control held by the director / "
                                                 "beneficial owner")
    director_mobile_phone: str = Field(description="Mobile phone number of the director")
    director_residential_status: str = Field(description="Director's residential status, e.g. Owner With "
                                               "Mortgage, Renting")
    director_residential_address: str = Field(description="Director's residential address")

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
    
    balance: float = Field(description="The current balance of the bank account.", 
                          title="Bank Balance")
    payments_in: float = Field(description="total payments in during statement period", 
                          title="Payments In")
    payments_out: float = Field(description="total payments out during statement period", 
                        title="Payments Out")

# ---------------------------------------------------------
# Schema for Annual Accounts 
# ---------------------------------------------------------
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

    turnover_current_year: int = Field(description = "Turnover for current year")
    operating_profit_current_year: int = Field(description = "operating_profit for current financial year")
    profit_current_year: int = Field(description="Annual profit for current financial year")

    turnover_last_year: int | None = Field(description = "Turnover for last year")
    operating_profit_last_year: int | None = Field(description = "operating_profit for last year")
    profit_last_year: int | None = Field(description="Annual profit for last year")

    # balance sheet

    tangible_fixed_assets_current_year: int | None = Field(description = "tangible fixed assets for current  financial year")
    debtors_current_year: int | None = Field(description="debtors current financial year")
    cash_at_bank_current_year: int | None  = Field(description="cash at band or in hand current financial year")               

    tangible_fixed_assets_last_year: int | None = Field(description = "tangible fixed assets for last year")
    debtors_last_year: int | None = Field(description="debtors last year")
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


# Langgraph States
def _last_write_wins(_old: Any, new: Any) -> Any:
    return new


class ApplicationState(TypedDict, total=False):
    """Checkpointed state captures application inputs and decision outputs"""
    application: dict[str, Any]
    # Loaded by load_application alongside application itself -- see
    # graph.py's load_application docstring for the naming convention
    # (annual_accounts*/bank_statement* under the same input/ location as
    # application.json) and why each entry is schema-validated up front.
    # A list, not a single dict: there may be multiple documents per type
    # (e.g. two years of annual accounts, several months of statements).
    annual_accounts: Annotated[list[dict[str, Any]], _last_write_wins]
    bank_statements: Annotated[list[dict[str, Any]], _last_write_wins]
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    companies_house_found: Annotated[bool, _last_write_wins]
    financial_assessment: Annotated[dict[str, Any], _last_write_wins]
    web_search: Annotated[dict[str, Any], _last_write_wins]
    final_decision: Annotated[dict[str, Any], _last_write_wins]


@dataclass(frozen=True)
class AgentContext:
    """Per-invocation dependencies threaded via LangGraph's Runtime context
    API (`StateGraph(..., context_schema=AgentContext)`), not graph state.

    `store`/`policy_docs` wrap  boto3 S3 client and `tools` holds live
    MCP `StructuredTool` objects — neither is msgpack-serializable, 
    so can't live in  `ApplicationState`, which is checkpointered
    Runtime context is passed via `graph.ainvoke(state,
    context=...)`, kept immutable for the run, and is never part of the
    checkpointed state.
    """

    store: ApplicationStore
    policy_docs: PolicyDocStore
    tools: list[Any]


# ---------------------------------------------------------------------------
# Forced structured-output schemas for graph.py's agentic nodes
# ---------------------------------------------------------------------------

class CompaniesHouseResult(BaseModel):
    """Forced structured output for `check_companies_house` —
    agent's final turn is constrained to this
    schema and `found` becomes a branch condition in the graph."""

    found: bool = Field(
        description="True if the applicant's company was confirmed as a "
        "genuine UK company with the named applicant as an officer "
        "or PSC. False if no such company could be confirmed."
    )
    confidence: str = Field(description="high, medium, or low")
    summary: str = Field(
        description="Explanation of the finding, including any partial matches considered. "
        "When a company was found, must include the registered office address and the "
        "director/PSC names — search_web uses these to disambiguate the company online, "
        "not just the summary verdict."
    )


class FinalDecisionResult(BaseModel):
    """Forced structured output for `synthesize_decision` — the success-path
    counterpart to `reject_no_company`'s hand-written `final_decision` dict.
    Constrains the model to an actual outcome rather than a free-text
    write-up that never resolves to approve/reject/refer."""

    outcome: Literal["approved", "rejected", "referred"] = Field(
        description="approved: no material issues across the four assessments. rejected: a clear, "
        "material failure (ineligible on policy, insolvent/dissolved company, unaffordable "
        "repayment, serious unresolved discrepancy). referred: issues a human underwriter should "
        "review, but nothing rising to automatic rejection."
    )
    reason: str = Field(
        description="Which of the four assessments (policy_check/companies_house/"
        "financial_assessment/web_search) drove this outcome, and what in each."
    )
    rationale: str = Field(description="Brief overall rationale weighing the four assessments together.")
