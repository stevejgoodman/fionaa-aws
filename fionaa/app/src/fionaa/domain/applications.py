"""Business models for applications."""
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


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
