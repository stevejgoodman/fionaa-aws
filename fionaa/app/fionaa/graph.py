"""FIONAA — LangGraph state, runtime context, nodes, and graph wiring.

Each node writes its own evidence artifact under a fixed subpath via the
`ApplicationStore` handed in on `AgentContext` — see that class's docstring
for why storage/tools live in Runtime context instead of checkpointed state.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, TypedDict

import boto3
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command
from langgraph_checkpoint_aws import AgentCoreMemorySaver
from langchain.agents import create_agent
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field
from model.load import load_model

from prompts import COMPANIES_HOUSE_PROMPT, POLICY_CHECK_PROMPT, WEB_SEARCH_PROMPT
from security import CustomerIdentity
from storage import ApplicationStore, PolicyDocStore

log = logging.getLogger("fionaa")

CHECKPOINT_MEMORY_ID = os.environ["FIONAA_CHECKPOINT_MEMORY_ID"]

model = load_model()


# ---------------------------------------------------------------------------
# Checkpointing — actor/thread scoping derived from the verified identity
# ---------------------------------------------------------------------------

def build_checkpointer(session: boto3.Session) -> BaseCheckpointSaver:
    """Built fresh per invocation from the customer-scoped session — never at
    module load from the Runtime's broad execution-role credentials.

    AgentCoreMemorySaver is a managed AWS service (Bedrock AgentCore Memory),
    not a table we own — much less to stand up than the DynamoDB + optional
    S3-offload approach. Trade-off, and it's a real one: unlike the S3/DynamoDB
    isolation in storage.py, there's no IAM condition here scoping *which*
    actor_id a session may write/read — AgentCoreMemorySaver just calls
    CreateEvent/ListEvents with whatever actor_id it's given. Isolation for
    checkpoint data rests on this code always deriving actor_id from the
    verified identity (never the payload), the same discipline as
    customer_id elsewhere, but application-enforced rather than IAM-enforced.
    Acceptable here because checkpoints are operational-recovery state, not
    the compliance record — the evidence artifacts under each node's own
    subpath (`policy_check/result.json`, etc.) are that record, and those
    live in S3 under the IAM-enforced prefix.

    AgentCoreMemorySaver builds its own boto3 client internally (it takes
    **boto3_kwargs, not a Session), so the customer-scoped credentials have to
    be unpacked from `session` and passed through explicitly — otherwise it
    would silently fall back to the ambient execution role.
    """
    creds = session.get_credentials().get_frozen_credentials()
    return AgentCoreMemorySaver(
        memory_id=CHECKPOINT_MEMORY_ID,
        aws_access_key_id=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        aws_session_token=creds.token,
        region_name=session.region_name,
    )


def checkpoint_config(identity: CustomerIdentity) -> dict[str, Any]:
    """actor_id/thread_id for AgentCoreMemorySaver, derived from the verified
    identity — never accepted from the caller (same rule as customer_id
    itself). actor_id=customer_id is what actually scopes checkpoint data to
    this customer; thread_id=application_id doesn't need to embed customer_id
    itself anymore, since actor_id already carries that."""
    return {"configurable": {"thread_id": identity.application_id, "actor_id": identity.customer_id}}


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

def _last_write_wins(_old: Any, new: Any) -> Any:
    return new


class CompaniesHouseResult(BaseModel):
    """Forced structured output for `check_companies_house` — routing on free
    LLM text is brittle, so the agent's final turn is constrained to this
    schema and `found` becomes the actual branch condition."""

    found: bool = Field(
        description="True if the applicant's company was confirmed as a "
        "genuine, active UK company with the named applicant as an officer "
        "or PSC. False if no such company could be confirmed."
    )
    confidence: str = Field(description="high, medium, or low")
    summary: str = Field(description="Brief explanation of the finding, including any partial matches considered.")


class ApplicationState(TypedDict, total=False):
    application: dict[str, Any]
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    companies_house_found: Annotated[bool, _last_write_wins]
    web_search: Annotated[dict[str, Any], _last_write_wins]
    final_decision: Annotated[dict[str, Any], _last_write_wins]


@dataclass(frozen=True)
class AgentContext:
    """Per-invocation dependencies threaded via LangGraph's Runtime context
    API (`StateGraph(..., context_schema=AgentContext)`), not graph state.

    `store`/`policy_docs` wrap a live boto3 S3 client and `tools` holds live
    MCP `StructuredTool` objects — none of that is msgpack-serializable, and
    it used to live in `ApplicationState`, which a real checkpointer
    serializes on every superstep (`TypeError: Type is not msgpack
    serializable`). Runtime context is passed via `graph.ainvoke(state,
    context=...)`, kept immutable for the run, and is never part of the
    checkpointed state — so it never reaches the checkpointer's serde at all.
    """

    store: ApplicationStore
    policy_docs: PolicyDocStore
    tools: list[Any]


# ---------------------------------------------------------------------------
# Nodes — each writes its own artifact under a fixed subpath
# ---------------------------------------------------------------------------

def load_application(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    application = runtime.context.store.get_json("input/application.json")
    if application is None:
        raise FileNotFoundError("application.json not found for this application_id")
    return {"application": application}


async def check_against_policy(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    application = state["application"]

    agent = create_agent(
        model=model,
        tools=runtime.context.tools,
        system_prompt=POLICY_CHECK_PROMPT,
    )

    # MCP-backed tools only implement async invocation (no sync `func`, only
    # a `coroutine`) — agent.invoke() raises NotImplementedError as soon as
    # the model calls one, so this has to be ainvoke().
    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=json.dumps(application))]}
    )
    loan_result = response["messages"][-1].content
    runtime.context.store.put_json("policy_check/result.json", loan_result)
    return {"policy_check": loan_result}


async def check_companies_house(
    state: ApplicationState, runtime: Runtime[AgentContext]
) -> Command[Literal["web_search", "reject_no_company"]]:
    application = state["application"]

    agent = create_agent(
        model=model,
        tools=runtime.context.tools,
        system_prompt=COMPANIES_HOUSE_PROMPT,
        response_format=CompaniesHouseResult,
    )

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=json.dumps(application))]}
    )
    result: CompaniesHouseResult = response["structured_response"]
    companies_house_result = result.model_dump()

    runtime.context.store.put_json("companies_house/result.json", companies_house_result)

    return Command(
        update={"companies_house": companies_house_result, "companies_house_found": result.found},
        goto="web_search" if result.found else "reject_no_company",
    )


def reject_no_company(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Terminal node for the companies_house branch that found no matching
    company. Writes a single artifact recording why the run stopped early —
    including the policy_check outcome gathered before this point, so a
    reviewer doesn't have to reconstruct the reasoning from separate
    per-node artifacts."""
    final_decision = {
        "outcome": "rejected",
        "reason": "companies_house_no_match",
        "policy_check": state.get("policy_check"),
        "companies_house": state.get("companies_house"),
    }
    runtime.context.store.put_json("decision/result.json", final_decision)
    return {"final_decision": final_decision}


async def search_web(state: ApplicationState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    company_name = state["application"]["company_name"]

    agent = create_agent(
        model=model,
        tools=runtime.context.tools,
        system_prompt=WEB_SEARCH_PROMPT,
    )

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=f"Company: {company_name}")]}
    )

    web_search_result = response["messages"][-1].content

    runtime.context.store.put_json("web_search/result.json", web_search_result)
    return {"web_search": web_search_result}

# policy check is agentic rag (but essentially a single tool)
# companies house is agentic search (i suppose could involve multiple hops)
# websearch is agentic search looking for website, person linkedin etc.


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------

def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """Compiled per invocation, not once at module load. The checkpointer (if
    any) has to be built from that invocation's customer-scoped session — see
    `build_checkpointer` — so it can't be baked into a module-level singleton
    the way an uncheckpointed graph could be.

    `context_schema=AgentContext` registers `store`/`policy_docs`/`tools` as
    run-scoped runtime context rather than checkpointed state — see
    `AgentContext`'s docstring for why that split is what makes checkpointing
    actually work here.
    """
    g = StateGraph(ApplicationState, context_schema=AgentContext)
    g.add_node("load_application", load_application)
    g.add_node("policy_check", check_against_policy)
    g.add_node("companies_house", check_companies_house)
    g.add_node("reject_no_company", reject_no_company)
    g.add_node("web_search", search_web)

    g.add_edge(START, "load_application")
    g.add_edge("load_application", "policy_check")
    # policy_check deliberately does not short-circuit: a failed policy check
    # is a business outcome, not a dead end. Its result is already carried in
    # state (`policy_check`), so it flows into whichever artifact ends up
    # documenting the run's outcome instead of only living in
    # policy_check/result.json.
    g.add_edge("policy_check", "companies_house")
    # companies_house routes dynamically via the Command it returns —
    # "web_search" if the company was confirmed, "reject_no_company"
    # otherwise — so no static edge to either is declared here.
    g.add_edge("reject_no_company", END)
    g.add_edge("web_search", END)
    return g.compile(checkpointer=checkpointer)
