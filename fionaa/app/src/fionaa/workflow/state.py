"""Checkpointed state and per-invocation dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, TYPE_CHECKING

if TYPE_CHECKING:
    from fionaa.storage import ApplicationStore, PolicyDocStore

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
    management_information: Annotated[list[dict[str, Any]], _last_write_wins]
    submission_manifest: dict[str, Any] | None
    submission_date: str | None
    ingested_documents: list[dict[str, Any]]
    ingestion_errors: list[str]
    triage: dict[str, Any]
    readiness_assessment: dict[str, Any] | None
    # A dict conforming to PolicyCheckResult's shape (see below), not a bare
    # LLM string -- check_against_policy forces structured output the same
    # way check_companies_house already does for companies_house.
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    companies_house_found: Annotated[bool, _last_write_wins]
    company_lookup_failed: bool
    # A dict conforming to FinancialAssessmentResult's shape (see below), not
    # a bare LLM string -- see policy_check's comment above.
    financial_assessment: Annotated[dict[str, Any], _last_write_wins]
    web_search: Annotated[dict[str, Any], _last_write_wins]
    final_decision: Annotated[dict[str, Any], _last_write_wins]
    proposed_decision: Annotated[dict[str, Any], _last_write_wins]


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
    policy_checker: Any = None
    model: Any = None
