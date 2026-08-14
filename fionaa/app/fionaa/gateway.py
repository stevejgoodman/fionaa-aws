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

# These are read from os.environ at call time (inside the functions below),
# not at module import time — gateway.py gets imported during test collection
# (see tests/live_helpers.py), before tests/.env.local has been loaded, so a
# module-level `os.environ[...]` read here would permanently bind to
# conftest.py's placeholder values regardless of what runs later.


def _gateway_client_secret() -> str:
    """Resolves the Gateway OAuth client secret from Secrets Manager.

    Never put this in a plain env var — AgentCore Runtime env vars are
    visible via get-agent-runtime, and this is a real credential, not
    config.
    """
    client = boto3.client("secretsmanager")
    secret_arn = os.environ["AGENTCORE_GATEWAY_CLIENT_SECRET_ARN"]
    return client.get_secret_value(SecretId=secret_arn)["SecretString"]


def _gateway_token() -> str:
    """Client-credentials OAuth token for the AgentCore Gateway.

    Minted fresh per graph build (see `load_gateway_tools`, called from
    main.py once per invocation) rather than cached at module load — the
    Cognito access token is short-lived.
    """
    client_id = os.environ["AGENTCORE_GATEWAY_CLIENT_ID"]
    creds = base64.b64encode(f"{client_id}:{_gateway_client_secret()}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": os.environ["AGENTCORE_GATEWAY_OAUTH_SCOPES"].replace(",", " "),
    }).encode()
    req = urllib.request.Request(
        os.environ["AGENTCORE_GATEWAY_TOKEN_ENDPOINT"],
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
            "url": os.environ["AGENTCORE_GATEWAY_URL"],
            "transport": "streamable_http",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })
    return await client.get_tools()
