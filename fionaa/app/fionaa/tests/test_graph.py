"""Unit tests for graph.py: state, nodes, checkpointing, and graph wiring.

`ApplicationStore`/`PolicyDocStore` are replaced with in-memory fakes, and
`create_agent` (used by all three evidence-gathering nodes) is monkeypatched
to a fake agent that returns a canned message instead of calling a real
model.
"""

import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

import graph as g
import security as sec

from fakes import FakePolicyDocs, FakeRuntime, FakeStore, make_fake_create_agent


@pytest.fixture
def identity():
    return sec.CustomerIdentity(customer_id="a" * 64, application_id="app-123")


# ---------------------------------------------------------------------------
# checkpoint_config
# ---------------------------------------------------------------------------

def test_checkpoint_config_derives_from_identity(identity):
    config = g.checkpoint_config(identity)
    assert config == {
        "configurable": {"thread_id": "app-123", "actor_id": "a" * 64}
    }


# ---------------------------------------------------------------------------
# Node: load_application
# ---------------------------------------------------------------------------

def test_load_application_returns_stored_application():
    application = {"company_name": "Acme Ltd", "company_number": "12345678"}
    store = FakeStore({"input/application.json": application})
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.load_application({}, runtime)

    assert result == {"application": application}


def test_load_application_raises_when_missing():
    runtime = FakeRuntime(g.AgentContext(store=FakeStore(), policy_docs=FakePolicyDocs(), tools=[]))
    with pytest.raises(FileNotFoundError):
        g.load_application({}, runtime)


# ---------------------------------------------------------------------------
# Node: check_against_policy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_against_policy_persists_and_returns_result(monkeypatch):
    application = {"company_number": "12345678"}
    store = FakeStore()
    fake_tools = ["kb-target-loan-policies___Retrieve"]
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=fake_tools))
    fake_result = "policy check passed"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_against_policy(state, runtime)

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
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=fake_tools))
    fake_result = {
        "found": True,
        "confidence": "high",
        "summary": "company is active, applicant is a registered officer",
    }
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_companies_house(state, runtime)

    assert result.update == {"companies_house": fake_result, "companies_house_found": True}
    assert result.goto == "web_search"
    assert store.data["companies_house/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    assert calls[1]["message_content"] == json.dumps(application)


@pytest.mark.asyncio
async def test_check_companies_house_routes_to_reject_when_not_found(monkeypatch):
    application = {"company_number": "00000000"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": False, "confidence": "low", "summary": "no matching company"}
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_companies_house(state, runtime)

    assert result.goto == "reject_no_company"
    assert result.update["companies_house_found"] is False


# ---------------------------------------------------------------------------
# Node: reject_no_company
# ---------------------------------------------------------------------------

def test_reject_no_company_persists_final_decision():
    store = FakeStore()
    state = {
        "policy_check": "policy check passed",
        "companies_house": {"found": False, "confidence": "low", "summary": "no match"},
    }
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.reject_no_company(state, runtime)

    assert result["final_decision"]["outcome"] == "rejected"
    assert result["final_decision"]["reason"] == "companies_house_no_match"
    assert store.data["decision/result.json"] == result["final_decision"]


# ---------------------------------------------------------------------------
# Node: search_web
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_web_builds_query_from_company_name(monkeypatch):
    store = FakeStore()
    fake_tools = ["websearch-target___WebSearch"]
    state = {"application": {"company_name": "Acme Ltd"}}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=fake_tools))
    fake_result = "no adverse findings"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.search_web(state, runtime)

    assert result == {"web_search": fake_result}
    assert store.data["web_search/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    assert calls[1]["message_content"] == "Company: Acme Ltd"


# ---------------------------------------------------------------------------
# Full graph wiring
# ---------------------------------------------------------------------------

def _patch_all_integration_points(monkeypatch, agent_response="ok"):
    calls = []
    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(agent_response, calls))
    return calls


@pytest.mark.asyncio
async def test_build_graph_runs_all_nodes_in_order(monkeypatch, identity):
    application = {"company_name": "Acme Ltd", "company_number": "12345678"}
    store = FakeStore({"input/application.json": application})
    policy_docs = FakePolicyDocs({"lending/unsecured-business-v7.md": b"policy text"})
    _patch_all_integration_points(monkeypatch, agent_response="ok")

    graph = g.build_graph(checkpointer=None)
    config = g.checkpoint_config(identity)
    agent_context = g.AgentContext(store=store, policy_docs=policy_docs, tools=[])

    final_state = await graph.ainvoke({}, config, context=agent_context)

    assert final_state["policy_check"] == "ok"
    # check_companies_house forces structured output — the fake wraps the
    # plain "ok" response into a generic passing CompaniesHouseResult.
    assert final_state["companies_house"]["found"] is True
    assert final_state["companies_house_found"] is True
    assert final_state["web_search"] == "ok"
    assert set(store.data) == {
        "input/application.json",
        "policy_check/result.json",
        "companies_house/result.json",
        "web_search/result.json",
    }


@pytest.mark.asyncio
async def test_build_graph_checkpoints_successfully_with_deps_in_context(monkeypatch, identity):
    """Regression check for the original bug: `store`/`policy_docs`/`tools`
    used to live in `ApplicationState` and broke every checkpoint write —
    `TypeError: Type is not msgpack serializable`, since none of the three
    (they wrap a live boto3 client / MCP tool objects) are msgpack-encodable.
    They're now runtime context (`AgentContext`, passed via `context=`),
    excluded from checkpointed state entirely, so a real checkpointer should
    complete without error and the checkpoint should hold the plain-value
    evidence fields."""
    application = {"company_name": "Acme Ltd", "company_number": "12345678"}
    store = FakeStore({"input/application.json": application})
    policy_docs = FakePolicyDocs()
    _patch_all_integration_points(monkeypatch, agent_response="ok")

    graph = g.build_graph(checkpointer=MemorySaver())
    config = g.checkpoint_config(identity)
    agent_context = g.AgentContext(store=store, policy_docs=policy_docs, tools=[])

    final_state = await graph.ainvoke({}, config, context=agent_context)

    assert final_state["web_search"] == "ok"
    saved = await graph.aget_state(config)
    assert saved.values["policy_check"] == "ok"
    assert saved.values["web_search"] == "ok"
