"""Unit tests for gateway.py: Gateway OAuth token minting and MCP tool loading."""

import json

import gateway as gw

from fakes import FakeHttpResponse


def test_gateway_client_secret_fetched_from_secrets_manager(monkeypatch):
    captured = {}

    class FakeSecretsManagerClient:
        def get_secret_value(self, SecretId):
            captured["secret_id"] = SecretId
            return {"SecretString": "fake-client-secret"}

    monkeypatch.setattr(gw.boto3, "client", lambda service: FakeSecretsManagerClient())

    secret = gw._gateway_client_secret()

    assert secret == "fake-client-secret"
    assert captured["secret_id"] == gw.GATEWAY_CLIENT_SECRET_ARN


def test_gateway_token_uses_client_credentials_flow(monkeypatch):
    captured_request = {}

    def fake_urlopen(req):
        captured_request["url"] = req.full_url
        captured_request["headers"] = dict(req.headers)
        captured_request["body"] = req.data
        return FakeHttpResponse(json.dumps({"access_token": "fake-token"}).encode())

    monkeypatch.setattr(gw.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(gw, "_gateway_client_secret", lambda: "fake-client-secret")

    token = gw._gateway_token()

    assert token == "fake-token"
    assert captured_request["url"] == gw.GATEWAY_TOKEN_ENDPOINT
    assert captured_request["headers"]["Authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in captured_request["body"]


async def test_load_gateway_tools_passes_bearer_token_to_mcp_client(monkeypatch):
    monkeypatch.setattr(gw, "_gateway_token", lambda: "fake-token")
    captured_config = {}

    class FakeMCPClient:
        def __init__(self, config):
            captured_config.update(config)

        async def get_tools(self):
            return ["fake-tool-1", "fake-tool-2"]

    monkeypatch.setattr(gw, "MultiServerMCPClient", FakeMCPClient)

    tools = await gw.load_gateway_tools()

    assert tools == ["fake-tool-1", "fake-tool-2"]
    gateway_config = captured_config["fionaa_gateway"]
    assert gateway_config["url"] == gw.GATEWAY_URL
    assert gateway_config["headers"] == {"Authorization": "Bearer fake-token"}
