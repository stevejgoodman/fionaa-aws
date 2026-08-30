"""Unit tests for graph.py: state, nodes, checkpointing, and graph wiring.

`ApplicationStore`/`PolicyDocStore` are replaced with in-memory fakes, and
`create_agent` (used by every node except load_application/reject_no_company)
is monkeypatched to a fake agent that returns a canned message instead of
calling a real model.
"""

import json
from datetime import date

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

    # No documents staged -- annual_accounts/bank_statements are always
    # present as (possibly empty) lists, not missing keys, since downstream
    # nodes read them with state.get(...) expecting a list to iterate.
    assert result == {"application": application, "annual_accounts": [], "bank_statements": []}


def test_load_application_raises_when_missing():
    runtime = FakeRuntime(g.AgentContext(store=FakeStore(), policy_docs=FakePolicyDocs(), tools=[]))
    with pytest.raises(FileNotFoundError):
        g.load_application({}, runtime)


ANNUAL_ACCOUNTS_2023 = {
    "company_name": "Acme Ltd", "director": "Jane Smith",
    "registered_address": "1 High St, London", "registration_number": "12345678",
    "accounting_year": "2023-12-31",
    "turnover_current_year": 500000, "operating_profit_current_year": 80000,
    "profit_current_year": 60000,
    "turnover_last_year": 450000, "operating_profit_last_year": 70000, "profit_last_year": 50000,
    "tangible_fixed_assets_current_year": 20000, "debtors_current_year": 15000,
    "cash_at_bank_current_year": 30000,
    "tangible_fixed_assets_last_year": 18000, "debtors_last_year": 12000,
    "cash_at_bank_last_year": 25000,
}

BANK_STATEMENT_JAN = {
    "account_owner": "Acme Ltd", "bank_name": "Big Bank", "account_number": "12345678",
    "start_date": "2024-01-01", "end_date": "2024-01-31",
    "balance": 12345.67, "payments_in": 5000.0, "payments_out": 3200.0,
}


def test_load_application_loads_and_validates_documents():
    """There's no manifest of how many documents exist per type -- multiple
    annual_accounts*/bank_statement* documents (naming convention, same
    input/ location as application.json) must all be discovered by prefix
    and validated against their schema, not just the first one found."""
    application = {"company_name": "Acme Ltd"}
    bank_statement_feb = {**BANK_STATEMENT_JAN, "start_date": "2024-02-01", "end_date": "2024-02-29"}
    store = FakeStore(
        {
            "input/application.json": application,
            "input/annual_accounts_2023.json": ANNUAL_ACCOUNTS_2023,
            "input/bank_statement_jan.json": BANK_STATEMENT_JAN,
            "input/bank_statement_feb.json": bank_statement_feb,
            # A same-prefix key that isn't actually a document of this type
            # must not be picked up just because "input/" matches -- prefix
            # matching is scoped to "input/annual_accounts"/"input/bank_statement".
            "input/documents/other.json": {"unrelated": True},
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.load_application({}, runtime)

    assert result["annual_accounts"] == [ANNUAL_ACCOUNTS_2023]
    # Sorted by key, not insertion order -- "bank_statement_feb" < "bank_statement_jan".
    assert result["bank_statements"] == [bank_statement_feb, BANK_STATEMENT_JAN]


def test_load_application_skips_document_types_that_dont_apply(monkeypatch):
    """DOCUMENT_SPECS.applies_to lets a future document type gate on the
    application (e.g. loan_type) without load_application itself branching
    -- annual_accounts/bank_statements are unconditional today (applies_to
    defaults to True), but a spec that returns False for this application
    must be skipped entirely, not merely returned empty."""
    application = {"company_name": "Acme Ltd", "loan_type": "unsecured-business-loans"}
    store = FakeStore(
        {
            "input/application.json": application,
            "input/annual_accounts_2023.json": ANNUAL_ACCOUNTS_2023,
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    conditional_spec = g.DocumentSpec(
        "security_valuation",
        "input/security_valuation",
        g.AnnualAccountsSchema,  # any schema stands in here -- unreachable, applies_to is False
        applies_to=lambda app: app["loan_type"] == "secured-business-loans",
    )
    monkeypatch.setattr(g, "DOCUMENT_SPECS", g.DOCUMENT_SPECS + [conditional_spec])

    result = g.load_application({}, runtime)

    assert "security_valuation" not in result
    assert result["annual_accounts"] == [ANNUAL_ACCOUNTS_2023]


def test_load_application_raises_on_invalid_document():
    """A malformed document fails loudly rather than being silently dropped
    -- these feed FINANCIAL_ASSESSMENT_PROMPT's numbers, so a bad document
    should stop the run, not quietly disappear from the assessment."""
    application = {"company_name": "Acme Ltd"}
    invalid_accounts = {k: v for k, v in ANNUAL_ACCOUNTS_2023.items() if k != "turnover_current_year"}
    store = FakeStore(
        {
            "input/application.json": application,
            "input/annual_accounts_2023.json": invalid_accounts,
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    with pytest.raises(ValueError, match="input/annual_accounts_2023.json"):
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
    # loan_type (policy_loader.py), not searched for. general.md declares
    # check_bank_statements_recent_and_sufficient (every loan type gets it)
    # and unsecured-business-loans declares check_unsecured_business_loan_amount_in_range
    # (see check_tools.py/policy.md) -- the agent gets those two and no others.
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = "policy check passed"
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_against_policy(state, runtime)

    assert result == {"policy_check": fake_result}
    assert store.data["policy_check/result.json"] == {"result": fake_result, "tool_calls": []}
    # tools_for's output order follows CHECK_TOOLS_POOL's own definition
    # order, not load_check_tool_names'/tool_names' order -- see
    # check_tools.py, check_bank_statements_recent_and_sufficient is
    # appended last there.
    assert [t.name for t in calls[0]["tools"]] == [
        "check_unsecured_business_loan_amount_in_range",
        "check_bank_statements_recent_and_sufficient",
    ]
    # No "bank_statements" key in state -- state.get(..., []) defaults to
    # empty, same as load_application would return when none were staged.
    # today computed here, not frozen -- see graph.py's check_against_policy,
    # which computes it fresh per invocation rather than reusing prompts.py's
    # stale, import-time-frozen TODAYS_DATE.
    expected_content = (
        f"POLICY:\n{g.load_policy_text(g.LoanType.unsecured_business_loans)}\n\n"
        f"APPLICATION:\n{json.dumps(application)}\n\n"
        f"BANK STATEMENT END DATES:\n[]\n\n"
        f"TODAY'S DATE: {date.today().isoformat()}"
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

    # See test_check_against_policy_persists_and_returns_result -- order
    # follows CHECK_TOOLS_POOL's definition order, not tool_names' order.
    assert [t.name for t in calls[0]["tools"]] == [
        "compute_invoice_factoring_advance",
        "check_bank_statements_recent_and_sufficient",
    ]


@pytest.mark.asyncio
async def test_check_against_policy_passes_bank_statement_end_dates(monkeypatch):
    """Bank statement recency/count is check_against_policy's job (not
    check_financial_assessment's) -- it needs the actual end dates to call
    check_bank_statements_recent_and_sufficient with."""
    application = {"loan_type": "unsecured-business-loans"}
    bank_statements = [
        {"end_date": "2026-06-30"}, {"end_date": "2026-07-31"}, {"end_date": "2026-08-25"},
    ]
    store = FakeStore()
    state = {"application": application, "bank_statements": bank_statements}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent("passed", calls))

    await g.check_against_policy(state, runtime)

    assert '"2026-06-30", "2026-07-31", "2026-08-25"' in calls[1]["message_content"]


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
    # No "annual_accounts"/"bank_statements" keys in state -- state.get(..., [])
    # defaults to empty, same as load_application would return when none
    # were staged.
    expected_content = (
        f"APPLICATION:\n{json.dumps(application)}\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
        f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}\n\n"
        f"ANNUAL ACCOUNTS:\n[]\n\n"
        f"BANK STATEMENTS:\n[]"
    )
    assert calls[1]["message_content"] == expected_content


@pytest.mark.asyncio
async def test_check_financial_assessment_passes_annual_accounts_and_bank_statements(monkeypatch):
    """Turnover-consistency/affordability cross-checking against
    annual_accounts/bank_statements is this node's job (not
    check_against_policy's) -- it needs the actual documents to compare."""
    application = {"company_number": "12345678", "annual_turnover": 250000}
    annual_accounts = [{"turnover_current_year": 95000}]
    bank_statements = [{"balance": 12345.67, "payments_in": 5000.0, "payments_out": 3200.0}]
    store = FakeStore()
    state = {
        "application": application,
        "annual_accounts": annual_accounts,
        "bank_statements": bank_statements,
    }
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent("consistent", calls))

    await g.check_financial_assessment(state, runtime)

    assert json.dumps(annual_accounts) in calls[1]["message_content"]
    assert json.dumps(bank_statements) in calls[1]["message_content"]


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
# Node: synthesize_decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_decision_persists_and_returns_result(monkeypatch):
    application = {"company_number": "12345678", "loan_amount": 10000, "loan_term": 24}
    policy_check = "ELIGIBLE: meets all requirements"
    companies_house = {"found": True, "confidence": "high", "summary": "company is active"}
    financial_assessment = "CONSISTENT: repayment looks affordable"
    web_search = "no adverse findings"
    store = FakeStore()
    state = {
        "application": application,
        "policy_check": policy_check,
        "companies_house": companies_house,
        "financial_assessment": financial_assessment,
        "web_search": web_search,
    }
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"outcome": "approved", "reason": "all four assessments clean", "rationale": "no issues found"}
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.synthesize_decision(state, runtime)

    # Mirrors reject_no_company's shape: the decision plus the upstream
    # findings that produced it (see graph.py's synthesize_decision).
    expected_final_decision = {
        **fake_result,
        "policy_check": policy_check,
        "companies_house": companies_house,
        "financial_assessment": financial_assessment,
        "web_search": web_search,
    }
    assert result == {"final_decision": expected_final_decision}
    assert store.data["decision/result.json"] == expected_final_decision
    # No tools scoped in -- this node only weighs earlier findings already in
    # state, it doesn't call anything itself.
    assert calls[0]["tools"] == []
    expected_content = (
        f"APPLICATION:\n{json.dumps(application)}\n\n"
        f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
        f"FINANCIAL ASSESSMENT:\n{json.dumps(financial_assessment)}\n\n"
        f"WEB SEARCH FINDINGS:\n{json.dumps(web_search)}"
    )
    assert calls[1]["message_content"] == expected_content


@pytest.mark.asyncio
async def test_synthesize_decision_can_reject(monkeypatch):
    """Not hardcoded to always approve -- the fake can return any outcome the
    schema allows, same as check_companies_house's found=False test above."""
    store = FakeStore()
    state = {"application": {}, "policy_check": None, "companies_house": None, "financial_assessment": None, "web_search": None}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"outcome": "rejected", "reason": "policy_check found ineligible", "rationale": "fails core eligibility"}
    calls = []

    monkeypatch.setattr(g, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.synthesize_decision(state, runtime)

    assert result["final_decision"]["outcome"] == "rejected"


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
    # synthesize_decision also forces structured output — the fake wraps
    # "ok" into a generic passing FinalDecisionResult the same way it does
    # CompaniesHouseResult above (see _generic_structured_fields).
    assert final_state["final_decision"]["outcome"] == "approved"
    assert set(store.data) == {
        "input/application.json",
        "policy_check/result.json",
        "companies_house/result.json",
        "financial_assessment/result.json",
        "web_search/result.json",
        "decision/result.json",
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
    assert saved.values["final_decision"]["outcome"] == "approved"
