
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
    # ge=0/ge=1 below reject an obviously-invalid negative/zero amount at the
    # schema boundary rather than letting it flow through to LLM prose review
    # (the only place it would otherwise be caught, if at all). annual_profit
    # is deliberately NOT bounded -- a loss is a legitimate value
    # financial_assessment needs to see, not a validation error.
    loan_amount: int | None = Field(default=None, ge=0, description="amount to be borrowed optional depending on "
                                     "loan type - UK Pounds")
    loan_term: int | None = Field(default=None, ge=1, descrption="term in months - optional depending on loan type")

    companies_house_registered: bool = Field(description="Whether the company is registered with Companies House")
    industry: str = Field(description="The applicant's industry / business sector")
    trading_start_date: date = Field(description="The date trading began")

    annual_turnover: int = Field(ge=0, description="Typical annual turnover in UK Pounds")
    annual_profit: int = Field(description="Profit for the last 12 months in UK Pounds")
    income_decrease_expected: bool = Field(description="Whether the applicant expects income to decrease "
                                            "in the next 12 months")
    accepts_card_payments: bool = Field(description="Whether the business accepts card payments")
    invoices_owed: int = Field(ge=0, description="Amount owed to the business in unpaid invoices, UK Pounds")

    monthly_expenses: int = Field(ge=0, description="Monthly business expense amount, not including mortgage or "
                                   "rent repayments, UK Pounds")
    monthly_rent_or_mortgage: int = Field(ge=0, description="Monthly rent or mortgage payment, UK Pounds")
    num_dependants: int = Field(ge=0, description="Number of dependants the applicant has")
    monthly_childcare_expenses: int = Field(ge=0, description="Typical monthly spend on childcare expenses, UK Pounds")
    monthly_non_business_income: int = Field(ge=0, description="Non-business income received each month, UK Pounds")
    monthly_other_household_income: int = Field(ge=0, description="Other monthly household income, UK Pounds")

    director_first_name: str = Field(description="First name of the director / beneficial owner "
                                      "with significant control")
    director_surname: str = Field(description="Surname of the director / beneficial owner with significant control")
    director_percentage_control: float = Field(ge=0, le=100, description="Percentage of control held by the director / "
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
    
    # balance is deliberately NOT bounded -- an overdrawn account is a
    # legitimate, meaningful negative value financial_assessment needs to
    # see, not something schema validation should reject before it's seen.
    balance: float = Field(description="The current balance of the bank account.",
                          title="Bank Balance")
    payments_in: float = Field(ge=0, description="total payments in during statement period",
                          title="Payments In")
    payments_out: float = Field(ge=0, description="total payments out during statement period",
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
    # A dict conforming to PolicyCheckResult's shape (see below), not a bare
    # LLM string -- check_against_policy forces structured output the same
    # way check_companies_house already does for companies_house.
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    companies_house_found: Annotated[bool, _last_write_wins]
    # A dict conforming to FinancialAssessmentResult's shape (see below), not
    # a bare LLM string -- see policy_check's comment above.
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
        "When a company was found, must include the director/PSC names and the registered "
        "office's town/city and postcode area — search_web uses these to disambiguate the "
        "company online, not just the summary verdict. Do not include the building/house "
        "number or street name (the first line of the address): state location at "
        "town/postcode level only, to keep street-level address PII out of this free-text "
        "field, which flows into evidence storage, traces, and the dashboard."
    )


class PolicyCheckResult(BaseModel):
    """Forced structured output for `check_against_policy` — the eligibility
    counterpart to `CompaniesHouseResult`/`FinalDecisionResult`, constraining
    the model to an actual verdict plus itemized clause findings rather than
    free-text prose downstream nodes can only re-read via another LLM call."""

    eligible: Literal["eligible", "ineligible", "inconclusive"] = Field(
        description="Overall verdict against the loan policy's substantive criteria, assessed "
        "separately from documentation completeness (see documentation_gaps)."
    )
    clause_findings: list[str] = Field(
        description="One entry per quantifiable or explicitly-stated policy requirement checked "
        "(loan amount range, advance-rate band, turnover/trading-history thresholds, term limits, "
        "security/collateral requirements, etc.) — each entry names the clause and states whether "
        "the application satisfies it."
    )
    documentation_gaps: list[str] = Field(
        default_factory=list,
        description="Missing supporting documents or documentation-only shortfalls (e.g. "
        "insufficient/stale bank statements per check_bank_statements_recent_and_sufficient) — "
        "kept separate from eligible so a documentation gap alone never forces "
        "eligible='inconclusive' when the substantive criteria could already be assessed.",
    )
    summary: str = Field(description="Brief overall summary of the assessment.")


class FinancialCrossCheckSummary(BaseModel):
    """JSON-serializable mirror of check_tools.FieldComparison — the LLM-
    facing/evidence-artifact shape of a single deterministic cross-check
    comparison. Field names/values are computed in code
    (check_tools.cross_check_financial_figures) and must be copied through
    verbatim, not recomputed."""

    field: str
    application_value: float | None
    reference_value: float | None
    source: str
    delta_pct: float | None
    material: bool


class FinancialAssessmentResult(BaseModel):
    """Forced structured output for `check_financial_assessment`. `cross_check`
    is the authoritative, code-computed turnover/profit comparison
    (check_tools.cross_check_financial_figures) — the model copies it through
    rather than re-deriving its own delta percentages; `discrepancies` is for
    narrative mismatches cross_check doesn't cover (company name, time
    trading, etc.)."""

    verdict: Literal["consistent", "inconsistent", "inconclusive"] = Field(
        description="Overall cross-source consistency verdict. inconsistent means a genuine "
        "cross-source contradiction was found (e.g. a material cross_check discrepancy, or a "
        "conflicting fact across sources) -- not merely that evidence is missing. inconclusive "
        "means there wasn't enough evidence to assess consistency at all (e.g. no annual_accounts "
        "and no bank_statements to compare against); never use inconsistent for that case, since "
        "synthesize_decision weighs inconsistent much more heavily toward rejection than a "
        "documentation gap warrants on its own."
    )
    discrepancies: list[str] = Field(
        default_factory=list,
        description="Non-numeric or otherwise not-already-covered-by-cross_check mismatches "
        "across sources (company name, time trading, etc.) — do not restate a cross_check entry "
        "here, its material flag already captures that.",
    )
    monthly_repayment: float | None = Field(
        default=None,
        description="compute_monthly_repayment's result, if the loan type/fields make one "
        "applicable. Never estimated by the model itself.",
    )
    affordability_basis: Literal["bank_statements", "application_self_reported", "not_applicable"] = Field(
        description="Which evidence the affordability_verdict is based on -- bank_statements is "
        "preferred over application_self_reported when both are available."
    )
    affordability_verdict: str = Field(description="The affordability judgement itself.")
    cross_check: list[FinancialCrossCheckSummary] = Field(
        description="The deterministic turnover/profit comparison result, copied through "
        "verbatim from the CROSS-CHECK RESULT provided in the human message."
    )
    summary: str = Field(description="Brief overall summary of the assessment.")


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
