"""Loading workflow stage."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from json import JSONDecodeError
from typing import Any
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.documents import AnnualAccountsSchema, BankStatementSchema
from fionaa.domain.ingestion import (
    SubmissionManifest, DirectorIdEvidence, AddressEvidence,
    ManagementInformationEvidence, VatEvidence, BorrowingEvidence, GuaranteeEvidence,
)
from fionaa.storage import ApplicationStore
from fionaa.evidence_readiness import assess_submission


@dataclass(frozen=True)
class DocumentSpec:
    """Declares one document type load_application loads from the store.

    `applies_to` decides whether this document type is expected for a given
    application -- defaults to always True (annual_accounts/bank_statements
    are mandatory for every loan type today). A future document type that's
    only relevant to certain loan types (e.g. a security valuation for
    secured-business-loans) can gate on `application` here without
    load_application itself growing loan_type branching -- just add another
    DocumentSpec to DOCUMENT_SPECS below."""

    state_key: str
    key_prefix: str
    schema: type[BaseModel]
    applies_to: Callable[[dict[str, Any]], bool] = lambda application: True


DOCUMENT_SPECS = [
    DocumentSpec("annual_accounts", "input/annual_accounts", AnnualAccountsSchema),
    DocumentSpec("bank_statements", "input/bank_statement", BankStatementSchema),
]

INGESTION_SCHEMAS = {
    "annual_accounts": AnnualAccountsSchema, "bank_statement": BankStatementSchema,
    "director_id": DirectorIdEvidence, "proof_of_address": AddressEvidence,
    "management_information": ManagementInformationEvidence, "vat_returns": VatEvidence,
    "existing_borrowing": BorrowingEvidence, "personal_guarantee": GuaranteeEvidence,
}


def _load_submission(store, payload):
    """Load a complete trusted inventory, preserving failed extraction records.

    Storage/authorization exceptions deliberately propagate. A JSON/schema error
    is a processing failure, never evidence that an applicant omitted a document.
    """
    try:
        manifest = SubmissionManifest.model_validate(payload)
        if manifest.submission_date > date.today():
            raise ValueError("Submission date cannot be in the future")
    except (ValidationError, ValueError):
        return {"ingestion_errors": ["Submission manifest is invalid."],
                "submission_manifest": None, "ingested_documents": [],
                "annual_accounts": [], "bank_statements": [], "management_information": []}
    documents, errors = [], []
    for item in manifest.documents:
        record = item.model_dump(mode="json")
        if item.processing_status == "processed":
            try:
                extracted = store.get_json(item.extraction_key)
                data = INGESTION_SCHEMAS[item.document_type].model_validate(extracted)
                record["data"] = data.model_dump(mode="json")
            except (ValidationError, JSONDecodeError):
                record["processing_status"] = "failed"
                errors.append(f"Invalid or absent extraction: {item.extraction_key}")
        if record["processing_status"] in {"pending", "failed"}:
            errors.append(f"Processing unfinished: {item.source_key}")
        documents.append(record)
    def data_for(kind):
        return [doc["data"] for doc in documents if doc["document_type"] == kind and doc["processing_status"] == "processed"]
    return {
        "submission_manifest": manifest.model_dump(mode="json"),
        "submission_date": manifest.submission_date.isoformat(),
        "ingested_documents": documents, "ingestion_errors": errors,
        "annual_accounts": data_for("annual_accounts"),
        "bank_statements": data_for("bank_statement"),
        "management_information": data_for("management_information"),
    }


def _load_validated_documents(
    store: ApplicationStore, key_prefix: str, schema: type[BaseModel]
) -> list[dict[str, Any]]:
    """Loads every JSON document under `key_prefix` (same input/ location as
    application.json), validating each against `schema`. There's no
    manifest of how many documents exist per type -- store.list_keys
    discovers them by prefix match (annual_accounts*/bank_statement*), since
    multiple documents per type are expected (several years of accounts,
    several months of statements). A malformed document fails loudly
    (ValueError) rather than being silently dropped -- these feed
    FINANCIAL_ASSESSMENT_PROMPT's numbers, so a bad document should stop the
    run, not quietly disappear from the assessment."""
    docs = []
    for key in sorted(store.list_keys(key_prefix)):
        payload = store.get_json(key)
        if payload is None:
            continue
        try:
            validated = schema.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{key} failed {schema.__name__} validation") from exc
        docs.append(validated.model_dump(mode="json"))
    return docs


def load_application(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    store = runtime.context.store
    application = store.get_json("input/application.json")
    if application is None:
        raise FileNotFoundError("application.json not found for this application_id")

    if application.get("loan_type") == "unsecured-business-loans":
        try:
            manifest = store.get_json("ingestion/submission.json")
        except JSONDecodeError:
            manifest = {}  # Retained as an internal ingestion failure below.
        if manifest is not None:
            loaded = {"application": application, "company_lookup_failed": False,
                      "readiness_assessment": None, "submission_date": None,
                      **_load_submission(store, manifest)}
            if loaded.get("submission_manifest"):
                try:
                    loaded["readiness_assessment"] = assess_submission(
                        application, loaded["submission_manifest"], loaded["ingested_documents"],
                    ).model_dump(mode="json")
                except (ValidationError, ValueError, TypeError, KeyError):
                    loaded["ingestion_errors"].append("Readiness inputs need internal verification.")
            return loaded

    documents = {
        spec.state_key: _load_validated_documents(store, spec.key_prefix, spec.schema)
        for spec in DOCUMENT_SPECS
        if spec.applies_to(application)
    }

    result = {"application": application, **documents}
    if application.get("loan_type") == "unsecured-business-loans":
        result.update(submission_manifest=None, submission_date=None, readiness_assessment=None,
                      ingested_documents=[], ingestion_errors=[], management_information=[], company_lookup_failed=False)
    return result
