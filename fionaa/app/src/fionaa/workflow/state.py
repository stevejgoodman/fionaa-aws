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
    # Identity evidence. No assessment node reads these -- they exist for the
    # triage step at the end of the graph, which decides whether the submission
    # is complete enough to hand to a human underwriter (see triage.py).
    director_id: Annotated[list[dict[str, Any]], _last_write_wins]
    proof_of_address: Annotated[list[dict[str, Any]], _last_write_wins]
    # Documents that were supplied but failed schema validation at load time,
    # as [{"key": ..., "state_key": ...}]. Recorded rather than raised so an
    # unreadable upload routes the applicant back instead of failing the run.
    document_errors: Annotated[list[dict[str, str]], _last_write_wins]
    # A dict conforming to PolicyCheckResult's shape (see below), not a bare
    # LLM string -- check_against_policy forces structured output the same
    # way check_companies_house already does for companies_house.
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    companies_house_found: Annotated[bool, _last_write_wins]
    # Distinguishes "the lookup itself didn't complete" (timeout, circuit
    # breaker open -- see workflow/resilience.py) from "the lookup completed
    # and found no match". Only the latter is evidence about the company.
    company_lookup_failed: bool
    # A dict conforming to FinancialAssessmentResult's shape (see below), not
    # a bare LLM string -- see policy_check's comment above.
    financial_assessment: Annotated[dict[str, Any], _last_write_wins]
    web_search: Annotated[dict[str, Any], _last_write_wins]
    final_decision: Annotated[dict[str, Any], _last_write_wins]
    proposed_decision: Annotated[dict[str, Any], _last_write_wins]
    # A dict conforming to TriageResult's shape. Its `route` is the graph's
    # final branch condition -- see route_by_triage in workflow/triage.py.
    triage: Annotated[dict[str, Any], _last_write_wins]


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
