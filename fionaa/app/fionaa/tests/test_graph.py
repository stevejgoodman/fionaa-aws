"""Unit tests for graph.py: state, nodes, checkpointing, and graph wiring.

`ApplicationStore`/`PolicyDocStore` are replaced with in-memory fakes, and
`create_agent` (used by all three evidence-gathering nodes) is monkeypatched
to a fake agent that returns a canned message instead of calling a real
model.
"""

import json

import pytest
from langchain.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver

import graph as g
import security as sec

from fakes import FakeMessage, FakePolicyDocs, FakeRuntime, FakeStore, FakeTool, make_fake_create_agent


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
    application = {"company_number": "12345678", "loan_type": "unsecured-business-loans"}
    store = FakeStore()
    state = {"application": application}
    # No MCP tools in runtime.context.tools: policy text is loaded directly by
    # loan_type (policy_loader.py), not searched for. unsecured-business-loans
    # declares one check tool (check_unsecured_business_loan_amount_in_range,
    # see check_tools.py/policy.md) -- the agent gets that one and no others.
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = "policy check passed"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_against_policy(state, runtime)

    assert result == {"policy_check": fake_result}
    assert store.data["policy_check/result.json"] == {"result": fake_result, "tool_calls": []}
    assert [t.name for t in calls[0]["tools"]] == ["check_unsecured_business_loan_amount_in_range"]
    expected_content = (
        f"POLICY:\n{g.load_policy_text(g.LoanType.unsecured_business_loans)}\n\n"
        f"APPLICATION:\n{json.dumps(application)}"
    )
    assert calls[1]["message_content"] == expected_content


@pytest.mark.asyncio
async def test_check_against_policy_scopes_check_tools_by_loan_type(monkeypatch):
    """Which calculation tools the agent receives is declared in policy.md
    (`<!-- checks: ... -->`), read via policy_loader.load_check_tool_names —
    not hardcoded per node."""
    application = {"loan_type": "invoice-factoring", "invoices_owed": 148000}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent("passed", calls))

    await g.check_against_policy(state, runtime)

    assert [t.name for t in calls[0]["tools"]] == ["compute_invoice_factoring_advance"]


@pytest.mark.asyncio
async def test_check_against_policy_persists_tool_calls_as_evidence(monkeypatch):
    """Tool calls the agent makes are harvested off the response (ToolMessage
    entries), not written by the tools themselves — check_tools.py stays
    plain functions with no runtime/store access."""
    application = {"loan_type": "invoice-factoring", "invoices_owed": 148000}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    class FakeAgentWithToolCall:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content="118400",
                        name="compute_invoice_factoring_advance",
                        tool_call_id="call-1",
                    ),
                    FakeMessage("eligible"),
                ]
            }

    monkeypatch.setattr(g, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.check_against_policy(state, runtime)

    assert result == {"policy_check": "eligible"}
    assert store.data["policy_check/result.json"] == {
        "result": "eligible",
        "tool_calls": [{"tool": "compute_invoice_factoring_advance", "result": "118400"}],
    }


# ---------------------------------------------------------------------------
# Node: check_companies_house
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_companies_house_calls_gateway_and_persists(monkeypatch):
    application = {"company_number": "12345678"}
    store = FakeStore()
    # Includes geo-target___CheckSameArea alongside the CompaniesHouse tools —
    # COMPANIES_HOUSE_PROMPT instructs the agent to use it to reconcile a
    # loosely-worded applicant address against the Companies House registered
    # address, so both prefixes must come through tools_for.
    fake_tools = [
        FakeTool("CompaniesHouse___getCompanyProfile"),
        FakeTool("geo-target___CheckSameArea"),
    ]
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
    assert result.goto == "financial_assessment"
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
# Node: check_financial_assessment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_financial_assessment_persists_and_returns_result(monkeypatch):
    application = {"company_number": "12345678", "loan_amount": 10000, "loan_term": 24}
    companies_house = {"found": True, "confidence": "high", "summary": "company is active"}
    policy_check = "ELIGIBLE: meets all requirements"
    store = FakeStore()
    state = {"application": application, "companies_house": companies_house, "policy_check": policy_check}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = "consistent"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_financial_assessment(state, runtime)

    assert result == {"financial_assessment": fake_result}
    assert store.data["financial_assessment/result.json"] == {"result": fake_result, "tool_calls": []}
    # Only the deterministic repayment tool is scoped in — no MCP tools here.
    assert [t.name for t in calls[0]["tools"]] == ["compute_monthly_repayment"]
    expected_content = (
        f"APPLICATION:\n{json.dumps(application)}\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
        f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}"
    )
    assert calls[1]["message_content"] == expected_content


@pytest.mark.asyncio
async def test_check_financial_assessment_persists_tool_calls_as_evidence(monkeypatch):
    application = {"loan_amount": 10000, "loan_term": 24}
    companies_house = {"found": True, "confidence": "high", "summary": "company is active"}
    store = FakeStore()
    state = {"application": application, "companies_house": companies_house}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    class FakeAgentWithToolCall:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content="416.67",
                        name="compute_monthly_repayment",
                        tool_call_id="call-1",
                    ),
                    FakeMessage("consistent, affordable"),
                ]
            }

    monkeypatch.setattr(g, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.check_financial_assessment(state, runtime)

    assert result == {"financial_assessment": "consistent, affordable"}
    assert store.data["financial_assessment/result.json"] == {
        "result": "consistent, affordable",
        "tool_calls": [{"tool": "compute_monthly_repayment", "result": "416.67"}],
    }


# ---------------------------------------------------------------------------
# Node: search_web
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_web_builds_query_from_company_name(monkeypatch):
    store = FakeStore()
    fake_tools = [FakeTool("websearch-target___WebSearch")]
    companies_house = {
        "found": True,
        "confidence": "high",
        "summary": "Registered office: 1 High St, London. Director: Jane Smith.",
    }
    state = {"application": {"company_name": "Acme Ltd"}, "companies_house": companies_house}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=fake_tools))
    fake_result = "no adverse findings"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.search_web(state, runtime)

    assert result == {"web_search": fake_result}
    assert store.data["web_search/result.json"] == fake_result
    assert calls[0]["tools"] == fake_tools
    expected_content = (
        f"Company: Acme Ltd\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}"
    )
    assert calls[1]["message_content"] == expected_content


# ---------------------------------------------------------------------------
# Full graph wiring
# ---------------------------------------------------------------------------

def _patch_all_integration_points(monkeypatch, agent_response="ok"):
    calls = []
    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(agent_response, calls))
    return calls


@pytest.mark.asyncio
async def test_build_graph_runs_all_nodes_in_order(monkeypatch, identity):
    application = {
        "company_name": "Acme Ltd",
        "company_number": "12345678",
        "loan_type": "unsecured-business-loans",
    }
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
    assert final_state["financial_assessment"] == "ok"
    assert final_state["web_search"] == "ok"
    assert set(store.data) == {
        "input/application.json",
        "policy_check/result.json",
        "companies_house/result.json",
        "financial_assessment/result.json",
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
    application = {
        "company_name": "Acme Ltd",
        "company_number": "12345678",
        "loan_type": "unsecured-business-loans",
    }
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
    assert saved.values["financial_assessment"] == "ok"
    assert saved.values["web_search"] == "ok"
