"""Unit tests for the graph nodes ("agent actions") in fionaa_scoped_agent.py.

None of these touch AWS or Bedrock. `ApplicationStore`/`PolicyDocStore` are
replaced with in-memory fakes; `create_agent` (used by the policy-check and
web-search nodes) is monkeypatched to a fake agent that returns a canned
message instead of calling a real model; and the two remaining stubs
(_invoke_assessment_model, _call_gateway_tool) are monkeypatched per test —
they're unimplemented today, so there's nothing real to call yet, but the
node functions can still be exercised end-to-end.
"""

import json

import jwt
import pytest
from langgraph.checkpoint.memory import MemorySaver

import fionaa_scoped_agent as fsa


# ---------------------------------------------------------------------------
# Fakes for the storage layer
# ---------------------------------------------------------------------------

class FakeStore:
    """Stands in for ApplicationStore: same get_json/put_json surface, backed
    by a plain dict instead of S3."""

    def __init__(self, initial: dict | None = None) -> None:
        self.data: dict[str, dict] = dict(initial or {})
        self.puts: list[tuple[str, dict]] = []

    def get_json(self, relative_key):
        return self.data.get(relative_key)

    def put_json(self, relative_key, payload):
        self.data[relative_key] = payload
        self.puts.append((relative_key, payload))
        return f"fake://{relative_key}"


class FakePolicyDocs:
    def __init__(self, docs: dict[str, bytes] | None = None) -> None:
        self.docs = docs or {}

    def load(self, doc_key):
        return self.docs[doc_key]


class FakeMessage:
    def __init__(self, content):
        self.content = content


def make_fake_create_agent(response_content, calls: list):
    """Stands in for langchain.agents.create_agent: records the
    (tools, system_prompt) it was built with and the message content it was
    invoked with, and returns `response_content` as the final AI message —
    no real model or tool call involved."""

    def fake_create_agent(*, model, tools, system_prompt):
        calls.append({"tools": tools, "system_prompt": system_prompt})

        class FakeAgent:
            async def ainvoke(self, input):
                calls.append({"message_content": input["messages"][0].content})
                return {"messages": [FakeMessage(response_content)]}

        return FakeAgent()

    return fake_create_agent


@pytest.fixture
def identity():
    return fsa.CustomerIdentity(customer_id="a" * 64, application_id="app-123")


# ---------------------------------------------------------------------------
# CustomerIdentity / hashing
# ---------------------------------------------------------------------------

def test_hash_customer_id_normalizes_case_and_whitespace():
    assert fsa._hash_customer_id(" Person@Example.com ") == fsa._hash_customer_id("person@example.com")


def test_customer_identity_rejects_unsafe_tag_values():
    with pytest.raises(ValueError):
        fsa.CustomerIdentity(customer_id="not safe; drop table", application_id="app-123")


def test_checkpoint_config_derives_from_identity(identity):
    config = fsa.checkpoint_config(identity)
    assert config == {
        "configurable": {"thread_id": "app-123", "actor_id": "a" * 64}
    }


# ---------------------------------------------------------------------------
# identity_from_request_context
# ---------------------------------------------------------------------------

class FakeRequestContext:
    def __init__(self, headers):
        self.request_headers = headers


def _bearer_token(claims):
    # Signature is never verified by identity_from_request_context, so any
    # signing key/algorithm here is fine.
    return jwt.encode(claims, "unused-signing-key", algorithm="HS256")


def test_identity_from_request_context_derives_customer_id_from_email():
    token = _bearer_token({"email": "Person@Example.com"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})

    identity = fsa.identity_from_request_context(context=context, application_id="app-456")

    assert identity.customer_id == fsa._hash_customer_id("person@example.com")
    assert identity.application_id == "app-456"


def test_identity_from_request_context_falls_back_to_custom_email_claim():
    token = _bearer_token({"custom:email": "person@example.com"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})

    identity = fsa.identity_from_request_context(context=context, application_id="app-456")

    assert identity.customer_id == fsa._hash_customer_id("person@example.com")


def test_identity_from_request_context_requires_authorization_header():
    context = FakeRequestContext({})
    with pytest.raises(ValueError, match="no Authorization header"):
        fsa.identity_from_request_context(context=context, application_id="app-456")


def test_identity_from_request_context_requires_email_claim():
    token = _bearer_token({"sub": "no-email-here"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})
    with pytest.raises(ValueError, match="no email claim"):
        fsa.identity_from_request_context(context=context, application_id="app-456")


# ---------------------------------------------------------------------------
# Node: load_application
# ---------------------------------------------------------------------------

def test_load_application_returns_stored_application():
    application = {"company_name": "Acme Ltd", "company_number": "12345678"}
    state = {"store": FakeStore({"input/application.json": application})}

    result = fsa.load_application(state)

    assert result == {"application": application}


def test_load_application_raises_when_missing():
    state = {"store": FakeStore()}
    with pytest.raises(FileNotFoundError):
        fsa.load_application(state)


# ---------------------------------------------------------------------------
# Node: check_against_policy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_against_policy_persists_and_returns_result(monkeypatch):
    application = {"company_number": "12345678"}
    store = FakeStore()
    fake_tools = ["kb-target-loan-policies___Retrieve"]
    state = {"application": application, "store": store, "tools": fake_tools}
    fake_result = "policy check passed"
    calls = []

    monkeypatch.setattr(fsa, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await fsa.check_against_policy(state)

    assert result == {"policy_check": fake_result}
    assert store.data["policy_check/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    assert calls[1]["message_content"] == json.dumps(application)


# ---------------------------------------------------------------------------
# Node: check_companies_house
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_companies_house_calls_gateway_and_persists(monkeypatch):
    application = {"company_number": "12345678"}
    store = FakeStore()
    fake_tools = ["CompaniesHouse___getCompanyProfile"]
    state = {"application": application, "store": store, "tools": fake_tools}
    fake_result = "company is active, applicant is a registered officer"
    calls = []

    monkeypatch.setattr(fsa, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await fsa.check_companies_house(state)

    assert result == {"companies_house": fake_result}
    assert store.data["companies_house/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    assert calls[1]["message_content"] == json.dumps(application)


# ---------------------------------------------------------------------------
# Node: search_web
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_web_builds_query_from_company_name(monkeypatch):
    store = FakeStore()
    fake_tools = ["websearch-target___WebSearch"]
    state = {"application": {"company_name": "Acme Ltd"}, "store": store, "tools": fake_tools}
    fake_result = "no adverse findings"
    calls = []

    monkeypatch.setattr(fsa, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await fsa.search_web(state)

    assert result == {"web_search": fake_result}
    assert store.data["web_search/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    assert calls[1]["message_content"] == "Company: Acme Ltd"


# ---------------------------------------------------------------------------
# Node: reassess
# ---------------------------------------------------------------------------

def test_reassess_persists_assessment_and_audit_trail(monkeypatch, identity):
    store = FakeStore()
    state = {
        "identity": identity,
        "store": store,
        "application": {"company_number": "12345678"},
        "policy_check": {"pass": True},
        "companies_house": {"company_status": "active"},
        "web_search": {"results": []},
    }
    fake_assessment = {"decision": "approve", "model_id": "test-model"}

    monkeypatch.setattr(fsa, "_invoke_assessment_model", lambda **kwargs: fake_assessment)

    result = fsa.reassess(state)

    assert result == {"assessment": fake_assessment}
    assert store.data["assessment/final_result.json"] == fake_assessment
    audit = store.data["assessment/audit_trail.json"]
    assert audit["customer_id"] == identity.customer_id
    assert audit["application_id"] == identity.application_id
    assert audit["decision"] == "approve"


# ---------------------------------------------------------------------------
# Full graph wiring
# ---------------------------------------------------------------------------

def _patch_all_integration_points(monkeypatch, agent_response="ok"):
    calls = []
    monkeypatch.setattr(fsa, "create_agent", make_fake_create_agent(agent_response, calls))
    monkeypatch.setattr(fsa, "_invoke_assessment_model", lambda **kwargs: {"decision": "approve"})
    return calls


@pytest.mark.asyncio
async def test_build_graph_runs_all_nodes_and_reaches_reassess(monkeypatch, identity):
    application = {"company_name": "Acme Ltd", "company_number": "12345678"}
    store = FakeStore({"input/application.json": application})
    policy_docs = FakePolicyDocs({"lending/unsecured-business-v7.md": b"policy text"})
    _patch_all_integration_points(monkeypatch, agent_response="ok")

    # No checkpointer here deliberately: MemorySaver (and, we'd assume,
    # AgentCoreMemorySaver) serialize the *entire* state dict at every
    # superstep via langgraph's default msgpack serde, which only knows how
    # to encode a fixed set of types (dataclasses, pydantic models, etc) —
    # not arbitrary objects like ApplicationStore/PolicyDocStore, which hold
    # a live boto3 S3 client. Confirmed via MemorySaver: it raises
    # `TypeError: Type is not msgpack serializable`. That's worth checking
    # against a real AgentCoreMemorySaver run — if it hits the same
    # limitation, `store`/`policy_docs` can't ride in checkpointed state as
    # implemented today.
    graph = fsa.build_graph(checkpointer=None)
    config = fsa.checkpoint_config(identity)

    final_state = await graph.ainvoke(
        {"identity": identity, "store": store, "policy_docs": policy_docs, "tools": []},
        config,
    )

    assert final_state["assessment"] == {"decision": "approve"}
    assert final_state["policy_check"] == "ok"
    assert final_state["companies_house"] == "ok"
    assert final_state["web_search"] == "ok"
    assert set(store.data) == {
        "input/application.json",
        "policy_check/result.json",
        "companies_house/result.json",
        "web_search/result.json",
        "assessment/final_result.json",
        "assessment/audit_trail.json",
    }


@pytest.mark.asyncio
async def test_checkpointed_state_cannot_hold_store_objects(monkeypatch, identity):
    """Regression check: with a real checkpointer, `store`/`policy_docs` are
    part of `ApplicationState` and get serialized on every superstep. They
    aren't a serializable type as far as langgraph's default serde is
    concerned, so checkpointed runs fail. If AgentCoreMemorySaver ever swaps
    in a serde that also allowlists arbitrary objects, this test should
    start failing and can be deleted."""
    store = FakeStore({"input/application.json": {"company_number": "1", "company_name": "Acme Ltd"}})
    policy_docs = FakePolicyDocs({"lending/unsecured-business-v7.md": b"policy text"})
    _patch_all_integration_points(monkeypatch, agent_response="ok")

    graph = fsa.build_graph(checkpointer=MemorySaver())
    config = fsa.checkpoint_config(identity)

    with pytest.raises(TypeError, match="not msgpack serializable"):
        await graph.ainvoke(
            {"identity": identity, "store": store, "policy_docs": policy_docs, "tools": []},
            config,
        )


# ---------------------------------------------------------------------------
# Gateway MCP tools
# ---------------------------------------------------------------------------

class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_gateway_client_secret_fetched_from_secrets_manager(monkeypatch):
    captured = {}

    class FakeSecretsManagerClient:
        def get_secret_value(self, SecretId):
            captured["secret_id"] = SecretId
            return {"SecretString": "fake-client-secret"}

    monkeypatch.setattr(fsa.boto3, "client", lambda service: FakeSecretsManagerClient())

    secret = fsa._gateway_client_secret()

    assert secret == "fake-client-secret"
    assert captured["secret_id"] == fsa.GATEWAY_CLIENT_SECRET_ARN


def test_gateway_token_uses_client_credentials_flow(monkeypatch):
    captured_request = {}

    def fake_urlopen(req):
        captured_request["url"] = req.full_url
        captured_request["headers"] = dict(req.headers)
        captured_request["body"] = req.data
        return FakeHttpResponse(json.dumps({"access_token": "fake-token"}).encode())

    monkeypatch.setattr(fsa.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fsa, "_gateway_client_secret", lambda: "fake-client-secret")

    token = fsa._gateway_token()

    assert token == "fake-token"
    assert captured_request["url"] == fsa.GATEWAY_TOKEN_ENDPOINT
    assert captured_request["headers"]["Authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in captured_request["body"]


@pytest.mark.asyncio
async def test_load_gateway_tools_passes_bearer_token_to_mcp_client(monkeypatch):
    monkeypatch.setattr(fsa, "_gateway_token", lambda: "fake-token")
    captured_config = {}

    class FakeMCPClient:
        def __init__(self, config):
            captured_config.update(config)

        async def get_tools(self):
            return ["fake-tool-1", "fake-tool-2"]

    monkeypatch.setattr(fsa, "MultiServerMCPClient", FakeMCPClient)

    tools = await fsa.load_gateway_tools()

    assert tools == ["fake-tool-1", "fake-tool-2"]
    gateway_config = captured_config["fionaa_gateway"]
    assert gateway_config["url"] == fsa.GATEWAY_URL
    assert gateway_config["headers"] == {"Authorization": "Bearer fake-token"}
