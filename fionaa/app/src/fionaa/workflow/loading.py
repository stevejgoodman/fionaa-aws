"""Loading workflow stage."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.applications import LoanType
from fionaa.domain.documents import (
    AnnualAccountsSchema,
    BankStatementSchema,
    DirectorIdSchema,
    ExistingBorrowingSchema,
    ProofOfAddressSchema,
    SecurityAssetSchema,
    VATReturnSchema,
)
from fionaa.storage import ApplicationStore


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


def _for_loan_types(*loan_types: LoanType) -> Callable[[dict[str, Any]], bool]:
    """An `applies_to` that expects this document only for these products.

    An unrecognised or missing loan_type matches nothing, so a malformed
    application doesn't silently pull in documents no policy asked for."""
    wanted = {loan_type.value for loan_type in loan_types}
    return lambda application: application.get("loan_type") in wanted


DOCUMENT_SPECS = [
    DocumentSpec("annual_accounts", "input/annual_accounts", AnnualAccountsSchema),
    DocumentSpec("bank_statements", "input/bank_statement", BankStatementSchema),
    # Required-to-proceed identity evidence, checked by the triage step at the
    # end of the graph rather than by any assessment node -- see triage.py.
    DocumentSpec("director_id", "input/director_id", DirectorIdSchema),
    DocumentSpec("proof_of_address", "input/proof_of_address", ProofOfAddressSchema),
    # Product-specific supporting evidence, gated on loan type -- the first
    # use of DocumentSpec.applies_to. Gated on the product alone, not also
    # on the applicant's own declaration (vat_registered and the rest): a
    # document that was supplied is loaded and reported whatever the form
    # says, and it is the policy's conditional rule, not this loader, that
    # decides whether one was required.
    DocumentSpec("vat_returns", "input/vat_return", VATReturnSchema,
                 _for_loan_types(LoanType.unsecured_business_loans,
                                 LoanType.revolving_credit_facility)),
    DocumentSpec("existing_borrowing", "input/existing_borrowing", ExistingBorrowingSchema,
                 _for_loan_types(LoanType.unsecured_business_loans,
                                 LoanType.revolving_credit_facility)),
    DocumentSpec("security_assets", "input/security_asset", SecurityAssetSchema,
                 _for_loan_types(LoanType.secured_business_loans,
                                 LoanType.revolving_credit_facility)),
]


def _load_validated_documents(
    store: ApplicationStore, spec: DocumentSpec
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Loads every JSON document under `spec.key_prefix` (same input/ location
    as application.json), validating each against `spec.schema`. There's no
    manifest of how many documents exist per type -- store.list_keys discovers
    them by prefix match (annual_accounts*/bank_statement*), since multiple
    documents per type are expected (several years of accounts, several months
    of statements).

    Returns (valid documents, validation failures). A malformed document used
    to raise ValueError and abort the run; now that the graph ends in a triage
    step, an unreadable document is an applicant-fixable submission defect, not
    a server error, so it's recorded here and surfaced by triage as an
    `unreadable` finding that routes the application back with an instruction
    to replace the file. This is still not the silent drop the raise existed to
    prevent -- the failure reaches both decision/triage.json and the applicant
    note; it just stops being a 500."""
    docs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for key in sorted(store.list_keys(spec.key_prefix)):
        payload = store.get_json(key)
        if payload is None:
            continue
        try:
            validated = spec.schema.model_validate(payload)
        except ValidationError:
            # The exception text is deliberately not kept: pydantic echoes the
            # offending input values back in its message, which would put raw
            # document contents into state, S3 and the dashboard.
            errors.append({"key": key, "state_key": spec.state_key})
            continue
        docs.append(validated.model_dump(mode="json"))
    return docs, errors


def load_application(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    store = runtime.context.store
    application = store.get_json("input/application.json")
    if application is None:
        raise FileNotFoundError("application.json not found for this application_id")

    documents: dict[str, Any] = {}
    document_errors: list[dict[str, str]] = []
    for spec in DOCUMENT_SPECS:
        if not spec.applies_to(application):
            continue
        documents[spec.state_key], errors = _load_validated_documents(store, spec)
        document_errors.extend(errors)

    return {"application": application, "document_errors": document_errors, **documents}
