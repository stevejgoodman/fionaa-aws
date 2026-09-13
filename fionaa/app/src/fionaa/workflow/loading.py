"""Loading workflow stage."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError
from fionaa.workflow.state import ApplicationState, AgentContext
from fionaa.domain.documents import AnnualAccountsSchema, BankStatementSchema
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


DOCUMENT_SPECS = [
    DocumentSpec("annual_accounts", "input/annual_accounts", AnnualAccountsSchema),
    DocumentSpec("bank_statements", "input/bank_statement", BankStatementSchema),
]


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

    documents = {
        spec.state_key: _load_validated_documents(store, spec.key_prefix, spec.schema)
        for spec in DOCUMENT_SPECS
        if spec.applies_to(application)
    }

    return {"application": application, **documents}
