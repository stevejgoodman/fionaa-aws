"""Customer-scoped AgentCore checkpoint construction."""
from __future__ import annotations
from typing import Any
import boto3
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph_checkpoint_aws import AgentCoreMemorySaver
from security import CustomerIdentity
from config import required_env

def build_checkpointer(session: boto3.Session, memory_id: str | None = None) -> BaseCheckpointSaver:
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
        memory_id=memory_id or required_env("FIONAA_CHECKPOINT_MEMORY_ID"),
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
