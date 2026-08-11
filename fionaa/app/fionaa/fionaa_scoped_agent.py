"""
FIONAA — LangGraph agent with IAM-enforced per-customer S3 isolation.

Security model
--------------
Coarse layer : the AgentCore Runtime execution role can do exactly one thing
               against customer data — sts:AssumeRole onto FionaaDataAccessRole.
               It has NO direct s3:GetObject/PutObject on the data bucket.

Fine layer   : every read/write goes through short-lived credentials obtained by
               assuming FionaaDataAccessRole with a session tag
               customer_id=<sha256 of the verified email claim>. That role's
               policy scopes S3 to
               arn:aws:s3:::fionaa-applications/${aws:PrincipalTag/customer_id}/*
               so a bug in node code cannot reach another customer's prefix —
               the call fails with AccessDenied at the IAM layer.

Identity scheme: customer_id is a hash of the customer's email address (never
the raw email, so the S3 key/session tag carries no PII); application_id is a
randomly generated opaque ID minted when the application is created upstream
of this agent. Neither needs a separate ID-mapping table.

See fionaa_iam_policies.md for the matching trust/permission policies.

Module layout
-------------
This module is a thin facade wiring together the four pieces of the system:

- `security`  — identity resolution from the verified JWT and scoped STS credentials.
- `storage`   — the only code that touches S3 (`ApplicationStore`, `PolicyDocStore`).
- `gateway`   — AgentCore Gateway OAuth + MCP tool loading.
- `graph`     — LangGraph state, runtime context, nodes, checkpointing, and graph wiring.

It re-exports the public surface those modules together provide so
`main.py` (the AgentCore Runtime entrypoint — see agentcore.json) has a
single, stable import site.
"""

from __future__ import annotations

from security import (
    DATA_ACCESS_ROLE_ARN,
    CustomerIdentity,
    identity_from_request_context,
    scoped_boto_session,
)
from storage import (
    APPLICATIONS_BUCKET,
    POLICY_DOCS_BUCKET,
    ApplicationStore,
    PolicyDocStore,
)
from gateway import (
    GATEWAY_CLIENT_ID,
    GATEWAY_CLIENT_SECRET_ARN,
    GATEWAY_OAUTH_SCOPES,
    GATEWAY_TOKEN_ENDPOINT,
    GATEWAY_URL,
    load_gateway_tools,
)
from graph import (
    CHECKPOINT_MEMORY_ID,
    AgentContext,
    ApplicationState,
    CompaniesHouseResult,
    build_checkpointer,
    build_graph,
    check_against_policy,
    check_companies_house,
    checkpoint_config,
    load_application,
    reject_no_company,
    search_web,
)

__all__ = [
    "DATA_ACCESS_ROLE_ARN",
    "CustomerIdentity",
    "identity_from_request_context",
    "scoped_boto_session",
    "APPLICATIONS_BUCKET",
    "POLICY_DOCS_BUCKET",
    "ApplicationStore",
    "PolicyDocStore",
    "GATEWAY_URL",
    "GATEWAY_TOKEN_ENDPOINT",
    "GATEWAY_OAUTH_SCOPES",
    "GATEWAY_CLIENT_ID",
    "GATEWAY_CLIENT_SECRET_ARN",
    "load_gateway_tools",
    "CHECKPOINT_MEMORY_ID",
    "AgentContext",
    "ApplicationState",
    "CompaniesHouseResult",
    "build_checkpointer",
    "build_graph",
    "check_against_policy",
    "check_companies_house",
    "checkpoint_config",
    "load_application",
    "reject_no_company",
    "search_web",
]

# The AgentCore Runtime entrypoint lives in main.py (that's what agentcore.json
# points at). This module is the library it wires up: identity, scoped storage,
# the checkpointer, and the graph builder.
