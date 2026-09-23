"""Business models for documents."""
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BankStatementSchema(BaseModel):
    account_owner: str = Field(description="The name of the account "
                               "owner(s).", title="Account Owner")
    bank_name: str = Field(description="The name of the bank.",
                           title="Bank Name")
    account_number: str = Field(description="The bank account number.",
                                title="Account Number")
    address: str = Field(description="The account owner's address as printed "
                          "on the statement.", title="Address")
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


class DirectorIdSchema(BaseModel):
    """Extracted fields from a director's photo ID.

    general.md: "Director ID means a Drivers licence or Passport" -- hence the
    closed `document_kind`, so an extraction that produced something else fails
    validation rather than silently satisfying the requirement.

    The document *number* is deliberately not extracted. Triage only needs to
    know the right kind of document exists, is in date, and belongs to the
    director named on the application; a passport number would be checkpointed
    and persisted for no gain, and not collecting it beats redacting it later
    (see redaction.py's module docstring for the same reasoning applied to
    bank account numbers).
    """

    document_kind: Literal["passport", "driving_licence"] = Field(
        description="Which form of photo ID this is.", title="Document Kind")
    holder_name: str = Field(description="Full name of the person the ID was issued to, "
                             "as printed on the document.", title="Holder Name")
    expiry_date: date = Field(description="The document's expiry date.", title="Expiry Date")


class ProofOfAddressSchema(BaseModel):
    """Extracted fields from a document evidencing the director's home address.

    `issue_date` is what makes this checkable -- a utility bill from four years
    ago evidences where someone used to live, so triage treats an old one as
    stale rather than satisfied.
    """

    document_kind: str = Field(description="What the document is, e.g. utility bill, "
                               "council tax statement, bank statement.", title="Document Kind")
    holder_name: str = Field(description="Full name of the person the document is addressed to.",
                             title="Holder Name")
    address: str = Field(description="The address as printed on the document.", title="Address")
    issue_date: date = Field(description="The date the document was issued.", title="Issue Date")


class VATReturnSchema(BaseModel):
    """Extracted fields from a filed VAT return.

    Required only of a VAT-registered business (`vat_registered` on the
    application form): the policies read "VAT returns (if registered)", and
    the AR rule is `(or (not isVATRegistered) hasVATReturns)` -- a business
    that declares it isn't registered needs none.

    The VAT registration *number* is deliberately not extracted, for the
    same reason DirectorIdSchema skips the document number: nothing checks
    it, and not collecting an identifier beats redacting it later.
    """

    business_name: str = Field(description="The registered business name the return was "
                               "filed for.", title="Business Name")
    period_start: date = Field(description="First day of the VAT period covered.",
                               title="Period Start")
    period_end: date = Field(description="Last day of the VAT period covered.",
                             title="Period End")
    # Unbounded: a return claiming more input VAT than it owes is a
    # legitimate refund position, not a validation error -- same reasoning
    # as AnnualAccountsSchema's profit fields.
    vat_due: float = Field(description="Net VAT due to (positive) or reclaimable from "
                           "(negative) HMRC for the period.", title="VAT Due")


class ExistingBorrowingSchema(BaseModel):
    """One existing borrowing facility, as evidenced by a statement or
    agreement the applicant supplies.

    Required only when the applicant declares existing borrowing
    (`has_existing_borrowing`) for unsecured loans; the
    revolving-credit-facility policy requires borrowing details
    unconditionally -- see its `hasRequiredDocuments` rule, which has no
    `hasExistingBorrowing` guard.
    """

    lender_name: str = Field(description="The lender or finance provider.", title="Lender")
    facility_type: str = Field(description="What kind of borrowing this is, e.g. term loan, "
                               "overdraft, asset finance, credit card.", title="Facility Type")
    outstanding_balance: float = Field(ge=0, description="Amount still owed, UK Pounds.",
                                       title="Outstanding Balance")
    monthly_repayment: float = Field(ge=0, description="Contractual monthly repayment, UK Pounds.",
                                     title="Monthly Repayment")
    as_at_date: date = Field(description="The date the balance was stated.", title="As At Date")


class SecurityAssetSchema(BaseModel):
    """Evidence of an asset offered as security: what it is, what it's
    worth, and that the applicant owns it.

    Serves two policies from one document type. For secured business loans
    it evidences `hasProofOfCollateralOwnershipValuation`; for a revolving
    facility the applicant has declared is secured, it is the "security/
    asset details" that policy asks for. The asset *type* is not read from
    here -- that is a declared term of the deal
    (`collateral_asset_type` on the application form), so an applicant who
    names an asset but hasn't yet supplied proof is ineligible-pending-
    documents rather than unknown.

    No recency rule is applied to `valuation_date`. None of the three
    policies states one, and inventing a staleness threshold here would be
    making policy rather than checking it.
    """

    asset_type: Literal["property", "equipment", "vehicles", "invoices",
                        "intangible_assets"] = Field(
        description="What kind of asset is offered as security.", title="Asset Type")
    description: str = Field(description="What the asset is, e.g. freehold warehouse, "
                             "CNC milling machine.", title="Description")
    estimated_value: float = Field(ge=0, description="Assessed or estimated value, UK Pounds.",
                                   title="Estimated Value")
    valuation_date: date = Field(description="The date of the valuation.", title="Valuation Date")
    owner_name: str = Field(description="The name the asset is registered to, as printed on the "
                            "ownership evidence.", title="Owner Name")


class DocumentType(str, Enum):
    bank_statement = "bank_statement"
    annual_company_report = "annual_company_report"
    director_id = "director_id"
    proof_of_address = "proof_of_address"
    vat_return = "vat_return"
    existing_borrowing = "existing_borrowing"
    security_asset = "security_asset"

    # Descriptions for each value
    def describe(self) -> str:
        descriptions = {
            "bank_statement": "A checking or savings account statement "
            "with balances and transactions. Usually the period shown is for 1 month duration.",

            "annual_company_report": "A company annual accounts or annual report "
            "showing income statement (or P&L statement) and "
            "balance sheet and other company information from the year such as a directors report ",

            "director_id": "A director's photo identity document — a UK passport "
            "or a driving licence — showing the holder's name and the document's expiry date.",

            "proof_of_address": "A document evidencing the director's residential "
            "address, such as a utility bill, council tax statement or personal bank "
            "statement, showing the addressee's name, the address and an issue date.",

            "vat_return": "A filed VAT return, showing the business name, the VAT "
            "period it covers and the net VAT due or reclaimable.",

            "existing_borrowing": "A statement or agreement evidencing an existing "
            "borrowing facility — lender, kind of facility, outstanding balance and "
            "monthly repayment.",

            "security_asset": "Evidence of an asset offered as security: what it is, "
            "a valuation, and proof the applicant owns it.",
        }
        return descriptions[self.value]


class DocType(BaseModel):
    type: DocumentType = Field(
        description="The type of document being analyzed.",
        title="Document Type",
    )
