"""FIONAA — AgentCore Gateway MCP tool loading.

The Gateway is an MCP target exposing Companies House, web search, the
loan-policy knowledge base, etc. (see GatewayClaimsGatewayUrlOutput in the
stack outputs). Cognito client-credentials flow to mint a bearer token by hand.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.parse
import urllib.request
from typing import Any

import boto3
from langchain_mcp_adapters.client import MultiServerMCPClient

GATEWAY_URL = os.environ["AGENTCORE_GATEWAY_URL"]
GATEWAY_TOKEN_ENDPOINT = os.environ["AGENTCORE_GATEWAY_TOKEN_ENDPOINT"]
GATEWAY_OAUTH_SCOPES = os.environ["AGENTCORE_GATEWAY_OAUTH_SCOPES"]
GATEWAY_CLIENT_ID = os.environ["AGENTCORE_GATEWAY_CLIENT_ID"]
# The client secret itself is never an env var — only its Secrets Manager ARN
# is. Fetched at call time in _gateway_token() via the runtime's own
# execution-role credentials (this isn't customer data, so it doesn't go
# through scoped_boto_session).
GATEWAY_CLIENT_SECRET_ARN = os.environ["AGENTCORE_GATEWAY_CLIENT_SECRET_ARN"]


def _gateway_client_secret() -> str:
    """Resolves the Gateway OAuth client secret from Secrets Manager.

    Never put this in a plain env var — AgentCore Runtime env vars are
    visible via get-agent-runtime, and this is a real credential, not
    config.
    """
    client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=GATEWAY_CLIENT_SECRET_ARN)["SecretString"]


def _gateway_token() -> str:
    """Client-credentials OAuth token for the AgentCore Gateway.

    Minted fresh per graph build (see `load_gateway_tools`, called from
    main.py once per invocation) rather than cached at module load — the
    Cognito access token is short-lived.
    """
    creds = base64.b64encode(f"{GATEWAY_CLIENT_ID}:{_gateway_client_secret()}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": GATEWAY_OAUTH_SCOPES.replace(",", " "),
    }).encode()
    req = urllib.request.Request(
        GATEWAY_TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310 - fixed HTTPS endpoint from our own env config
        return json.loads(resp.read())["access_token"]


async def load_gateway_tools() -> list[Any]:
    """MCP tools exposed by the AgentCore Gateway, ready to hand to
    `create_agent(tools=...)`. Call once per invocation (see main.py) and
    thread the result through `ApplicationState["tools"]` — same pattern as
    `store`/`policy_docs`, so nodes stay testable with a plain list of fakes
    instead of reaching for a module global.
    """
    token = await asyncio.to_thread(_gateway_token)
    client = MultiServerMCPClient({
        "fionaa_gateway": {
            "url": GATEWAY_URL,
            "transport": "streamable_http",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })
    return await client.get_tools()
