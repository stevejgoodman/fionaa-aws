"""Trusted ingestion records; these are produced by intake, never by applicants.

The manifest is a complete inventory for one immutable submission. Extracted
JSON stays separate from originals and from application self-reporting.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceType = Literal[
    "director_id", "proof_of_address", "bank_statement", "annual_accounts",
    "management_information", "vat_returns", "existing_borrowing", "personal_guarantee",
]


class IngestedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: EvidenceType
    source_key: str
    extraction_key: str | None = None
    processing_status: Literal["processed", "pending", "failed", "unreadable"]

    @field_validator("source_key", "extraction_key")
    @classmethod
    def scoped_key(cls, value):
        if value is not None and (
            not value.startswith(("input/", "ingestion/documents/"))
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or "\\" in value or "#" in value
        ):
            raise ValueError("Evidence keys must be application-relative input or ingestion document keys")
        return value

    @model_validator(mode="after")
    def processed_has_extraction(self):
        if self.processing_status == "processed" and not self.extraction_key:
            raise ValueError("Processed documents require an extraction key")
        return self


class SubmissionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    submission_id: str = Field(min_length=1)
    submission_date: date
    inventory_complete: Literal[True]
    documents: list[IngestedDocument]

    @model_validator(mode="after")
    def unique_documents(self):
        sources = [item.source_key for item in self.documents]
        extracts = [item.extraction_key for item in self.documents if item.extraction_key]
        if len(sources) != len(set(sources)) or len(extracts) != len(set(extracts)):
            raise ValueError("A submission must not contain duplicate document records")
        return self


class DirectorIdEvidence(BaseModel):
    document_kind: Literal["passport", "driving_licence"]
    holder_name: str = Field(min_length=1)


class AddressEvidence(BaseModel):
    holder_name: str = Field(min_length=1)
    address: str = Field(min_length=1)


class ManagementInformationEvidence(BaseModel):
    company_name: str = Field(min_length=1)
    period_start: date
    period_end: date
    turnover: float
    profit: float


class VatEvidence(BaseModel):
    company_name: str = Field(min_length=1)
    period_start: date
    period_end: date
    vat_due: float


class BorrowingEntry(BaseModel):
    lender: str = Field(min_length=1)
    outstanding_balance: float
    monthly_repayment: float


class BorrowingEvidence(BaseModel):
    company_name: str = Field(min_length=1)
    borrowing: list[BorrowingEntry] = Field(min_length=1)


class GuaranteeEvidence(BaseModel):
    guarantor_name: str = Field(min_length=1)
    executed: bool
