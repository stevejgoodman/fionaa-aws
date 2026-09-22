"""Unit tests for graph.py: state, nodes, checkpointing, and graph wiring.

`ApplicationStore`/`PolicyDocStore` are replaced with in-memory fakes, and
`create_agent` (used by every node except load_application/reject_no_company)
is monkeypatched to a fake agent that returns a canned message instead of
calling a real model.
"""

import json
from datetime import date, timedelta

import pytest
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver

import fionaa.graph as g
from fionaa.workflow import loading, validation
from fionaa.workflow import policy as policy_stage, companies_house as company_stage, financial as financial_stage, web_search as web_stage, decision as decision_stage
import fionaa.security as sec
from fionaa.testing.fakes import FakeMessage, FakePolicyDocs, FakeRuntime, FakeStore, FakeTool, make_fake_create_agent


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

    # No documents staged -- every document type is always present as a
    # (possibly empty) list, not a missing key, since downstream nodes read
    # them with state.get(...) expecting a list to iterate. An empty
    # document_errors is likewise always present for triage to read.
    assert result == {
        "application": application,
        "document_errors": [],
        "annual_accounts": [],
        "bank_statements": [],
        "director_id": [],
        "proof_of_address": [],
    }


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
    "address": "1 High St, London",
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
    monkeypatch.setattr(loading, "DOCUMENT_SPECS", g.DOCUMENT_SPECS + [conditional_spec])

    result = g.load_application({}, runtime)

    assert "security_valuation" not in result
    assert result["annual_accounts"] == [ANNUAL_ACCOUNTS_2023]


def test_load_application_records_invalid_document_instead_of_raising():
    """A malformed document is recorded in document_errors, not raised and not
    silently dropped. It used to raise ValueError and abort the run; now that
    the graph ends in triage, an unreadable upload is an applicant-fixable
    submission defect, so it's surfaced as an `unreadable` triage finding that
    routes the application back rather than as a 500."""
    application = {"company_name": "Acme Ltd"}
    invalid_accounts = {k: v for k, v in ANNUAL_ACCOUNTS_2023.items() if k != "turnover_current_year"}
    store = FakeStore(
        {
            "input/application.json": application,
            "input/annual_accounts_2023.json": invalid_accounts,
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.load_application({}, runtime)

    assert result["annual_accounts"] == []
    assert result["document_errors"] == [
        {"key": "input/annual_accounts_2023.json", "state_key": "annual_accounts"}
    ]


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
    fake_result = {
        "eligible": "eligible",
        "clause_findings": ["loan amount within range"],
        "documentation_gaps": [],
        "summary": "policy check passed",
    }
    calls = []

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_against_policy(state, runtime)

    assert result == {"policy_check": fake_result}
    assert store.data["policy_check/result.json"] == {**fake_result, "tool_calls": []}
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
    # today computed here, not frozen -- see check_against_policy, which
    # computes it fresh per invocation so it can't go stale in a
    # long-lived process.
    # Two blocks, not one string -- check_against_policy puts an explicit
    # cachePoint after POLICY so Bedrock caches it independently of the
    # per-application tail (see workflow/policy.py).
    expected_content = [
        {"type": "text", "text": f"POLICY:\n{g.load_policy_text(g.LoanType.unsecured_business_loans)}"},
        {"cachePoint": {"type": "default"}},
        {
            "type": "text",
            "text": (
                f"\n\nAPPLICATION:\n{json.dumps(application)}\n\n"
                f"COMPANIES HOUSE FINDINGS:\n{json.dumps(None)}\n\n"
                f"ANNUAL ACCOUNTS:\n[]\n\n"
                f"BANK STATEMENTS:\n[]\n\n"
                f"BANK STATEMENT END DATES:\n[]\n\n"
                f"ASSESSMENT REFERENCE DATE: {date.today().isoformat()}"
            ),
        },
    ]
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

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent("passed", calls))

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

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent("passed", calls))

    await g.check_against_policy(state, runtime)

    # message_content is now a list of blocks (see the cachePoint split in
    # workflow/policy.py) -- the dates live in the second, dynamic block.
    assert '"2026-06-30", "2026-07-31", "2026-08-25"' in calls[1]["message_content"][2]["text"]


@pytest.mark.asyncio
async def test_check_against_policy_persists_tool_calls_as_evidence(monkeypatch):
    """Tool calls the agent makes are harvested off the response (ToolMessage
    entries), not written by the tools themselves — check_tools.py stays
    plain functions with no runtime/store access."""
    application = {"loan_type": "invoice-factoring", "invoices_owed": 148000}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    fake_result = {
        "eligible": "eligible",
        "clause_findings": ["advance amount computed via tool"],
        "documentation_gaps": [],
        "summary": "eligible",
    }

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
                    FakeMessage(fake_result["summary"]),
                ],
                "structured_response": g.PolicyCheckResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.check_against_policy(state, runtime)

    assert result == {"policy_check": fake_result}
    assert store.data["policy_check/result.json"] == {
        **fake_result,
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

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_companies_house(state, runtime)

    assert result.update == {"companies_house": fake_result, "companies_house_found": True}
    assert result.goto == "policy_check"
    # tool_calls is added to the S3 evidence artifact only -- companies_house
    # state (asserted above) keeps the plain CompaniesHouseResult shape so
    # downstream prompts are unaffected. make_fake_create_agent auto-emits a
    # harmless CompaniesHouse___ ToolMessage here (see its docstring) so
    # found=True passes the runtime groundedness check below -- see
    # test_check_companies_house_persists_tool_calls_as_evidence for the
    # manually-constructed, more detailed tool_calls coverage.
    assert store.data["companies_house/result.json"] == {
        **fake_result,
        "tool_calls": [{"tool": "CompaniesHouse___getCompanyProfile", "result": "{}"}],
        "grounded": True,
        "grounding_reasons": [],
    }
    assert calls[0]["tools"] == fake_tools
    # Includes TODAY'S DATE so the model doesn't misjudge a genuine, recent
    # Companies House date against its own training-era sense of "now" --
    # see companies_house.py and COMPANIES_HOUSE_PROMPT's "Trust TODAY'S
    # DATE" section.
    assert calls[1]["message_content"] == (
        f"{json.dumps(application)}\n\nTODAY'S DATE: {date.today().isoformat()}"
    )


@pytest.mark.asyncio
async def test_check_companies_house_persists_tool_calls_as_evidence(monkeypatch):
    """Tool calls the agent makes (CompaniesHouse___*, geo-target___CheckSameArea)
    are harvested off the response the same way check_against_policy does, and
    added to the S3 evidence artifact only -- the companies_house state value
    (asserted separately above) stays a plain CompaniesHouseResult, since
    check_financial_assessment/synthesize_decision read it as such."""
    application = {"company_number": "12345678"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": True, "confidence": "high", "summary": "active company"}

    class FakeAgentWithToolCall:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content='{"status": "active"}',
                        name="CompaniesHouse___getCompanyProfile",
                        tool_call_id="call-1",
                    ),
                ],
                "structured_response": g.CompaniesHouseResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.check_companies_house(state, runtime)

    assert result.update == {"companies_house": fake_result, "companies_house_found": True}
    assert store.data["companies_house/result.json"] == {
        **fake_result,
        "tool_calls": [
            {"tool": "CompaniesHouse___getCompanyProfile", "result": '{"status": "active"}'}
        ],
        "grounded": True,
        "grounding_reasons": [],
    }


@pytest.mark.asyncio
async def test_check_companies_house_redacts_address_lines_in_persisted_tool_calls(monkeypatch):
    """The raw CompaniesHouse___* tool result persisted as S3 evidence
    bypasses COMPANIES_HOUSE_PROMPT's own "never write the street-level
    address into your summary" instruction entirely, since it's the tool's
    verbatim return value, not the model's summary -- redact_tool_calls
    (redaction.py) closes that gap at the point of write."""
    application = {"company_number": "12345678"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": True, "confidence": "high", "summary": "active company"}

    class FakeAgentWithToolCall:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content=json.dumps({
                            "registered_office_address": {
                                "address_line_1": "14 Oak Avenue",
                                "locality": "Uxbridge",
                            }
                        }),
                        name="CompaniesHouse___getCompanyProfile",
                        tool_call_id="call-1",
                    ),
                ],
                "structured_response": g.CompaniesHouseResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    await g.check_companies_house(state, runtime)

    persisted_tool_call = store.data["companies_house/result.json"]["tool_calls"][0]
    persisted_address = json.loads(persisted_tool_call["result"])["registered_office_address"]
    assert persisted_address["address_line_1"] == "[address redacted]"
    assert persisted_address["locality"] == "Uxbridge"


@pytest.mark.asyncio
async def test_check_companies_house_overrides_ungrounded_found_true_with_no_tool_calls(monkeypatch):
    """found=True with zero supporting tool calls is exactly the "invent a
    plausible-sounding match" failure mode -- the runtime groundedness check
    (groundedness.py) must override it to found=False and route to
    reject_no_company, not trust the model's unsupported claim."""
    application = {"company_number": "00000001"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": True, "confidence": "high", "summary": "Zorbex Quantum Widgets Ltd is active"}

    class FakeAgentNoToolCalls:
        async def ainvoke(self, input):
            return {
                "messages": [input["messages"][0]],
                "structured_response": g.CompaniesHouseResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentNoToolCalls())

    result = await g.check_companies_house(state, runtime)

    assert result.update["companies_house_found"] is False
    assert result.update["companies_house"]["found"] is False
    assert "runtime groundedness check overrode" in result.update["companies_house"]["summary"]
    assert result.goto == "reject_no_company"
    evidence = store.data["companies_house/result.json"]
    assert evidence["found"] is False
    assert evidence["grounded"] is False
    assert "no CompaniesHouse tool call evidence" in evidence["grounding_reasons"][0]


@pytest.mark.asyncio
async def test_check_companies_house_overrides_ungrounded_found_true_with_wrong_company(monkeypatch):
    """found=True where the only tool evidence is for a *different*
    company_number than the one the application asked to verify -- the
    model matched the wrong company."""
    application = {"company_number": "12345678"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": True, "confidence": "high", "summary": "company is active"}

    class FakeAgentWrongCompany:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content=json.dumps({"company_number": "00000000"}),
                        name="CompaniesHouse___getCompanyProfile",
                        tool_call_id="call-1",
                    ),
                ],
                "structured_response": g.CompaniesHouseResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWrongCompany())

    result = await g.check_companies_house(state, runtime)

    assert result.update["companies_house_found"] is False
    assert result.goto == "reject_no_company"
    assert store.data["companies_house/result.json"]["grounded"] is False


@pytest.mark.asyncio
async def test_check_companies_house_routes_to_reject_when_not_found(monkeypatch):
    application = {"company_number": "00000000"}
    store = FakeStore()
    state = {"application": application}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    fake_result = {"found": False, "confidence": "low", "summary": "no matching company"}
    calls = []

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_companies_house(state, runtime)

    assert result.goto == "reject_no_company"
    assert result.update["companies_house_found"] is False


# ---------------------------------------------------------------------------
# Node: reject_no_company
# ---------------------------------------------------------------------------

def test_reject_no_company_prepares_report_for_triage():
    store = FakeStore()
    state = {
        "policy_check": "policy check passed",
        "companies_house": {"found": False, "confidence": "low", "summary": "no match"},
    }
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.reject_no_company(state, runtime)

    assert result["final_decision"]["outcome"] == "pending_human_review"
    assert result["final_decision"]["ai_recommendation"]["reason"] == "companies_house_no_match"
    assert result["final_decision"]["ai_recommendation"]["outcome"] == "referred"
    assert result["final_decision"]["validation"]["status"] == "not_checked"
    assert "decision/result.json" not in store.data


def test_reject_no_company_distinguishes_lookup_failure_from_no_match():
    """company_lookup_failed=True means the lookup itself didn't complete
    (throttled/timed out/circuit open) -- a different signal to a reviewer
    than Companies House actually confirming no match, so this must not
    collapse to the same companies_house_no_match reason as the test above."""
    store = FakeStore()
    state = {
        "policy_check": "policy check passed",
        "companies_house": {"found": False, "confidence": "low", "summary": "Company lookup was unavailable; internal verification is required."},
        "company_lookup_failed": True,
    }
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.reject_no_company(state, runtime)

    assert result["final_decision"]["ai_recommendation"]["reason"] == "companies_house_lookup_unavailable"
    assert result["final_decision"]["ai_recommendation"]["outcome"] == "referred"
    assert result["final_decision"]["validation"]["check_status"] == "lookup_unavailable"


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
    fake_result = {
        "verdict": "consistent",
        "discrepancies": [],
        "monthly_repayment": 416.67,
        "affordability_basis": "application_self_reported",
        "affordability_verdict": "affordable",
        "cross_check": [],
        "summary": "consistent",
    }
    calls = []

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.check_financial_assessment(state, runtime)

    assert result == {"financial_assessment": fake_result}
    assert store.data["financial_assessment/result.json"] == {**fake_result, "tool_calls": []}
    # Only the deterministic repayment tool is scoped in — no MCP tools here.
    assert [t.name for t in calls[0]["tools"]] == ["compute_monthly_repayment"]
    # No "annual_accounts"/"bank_statements" keys in state -- state.get(..., [])
    # defaults to empty, same as load_application would return when none
    # were staged. cross_check_financial_figures also sees an empty
    # annual_accounts list, so CROSS-CHECK RESULT is an empty list too.
    expected_content = (
        f"APPLICATION:\n{json.dumps(application)}\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}\n\n"
        f"POLICY CHECK RESULT:\n{json.dumps(policy_check)}\n\n"
        f"ANNUAL ACCOUNTS:\n[]\n\n"
        f"BANK STATEMENTS:\n[]\n\n"
        f"CROSS-CHECK RESULT:\n[]\n\n"
        f"TODAY'S DATE: {date.today().isoformat()}"
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
    # A dict, not a plain string -- FinancialAssessmentResult has list/float
    # fields fakes.py's generic string-to-schema helper can't fill in.
    fake_result = {
        "verdict": "inconsistent",
        "discrepancies": [],
        "monthly_repayment": None,
        "affordability_basis": "application_self_reported",
        "affordability_verdict": "not assessed",
        "cross_check": [],
        "summary": "turnover mismatch",
    }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    await g.check_financial_assessment(state, runtime)

    assert json.dumps(annual_accounts) in calls[1]["message_content"]
    assert json.dumps(bank_statements) in calls[1]["message_content"]
    # annual_turnover=250000 vs turnover_current_year=95000 is a >15% delta
    # -- cross_check_financial_figures flags it material and it's passed
    # through in the human message for the agent to copy verbatim.
    assert '"material": true' in calls[1]["message_content"]


@pytest.mark.asyncio
async def test_check_financial_assessment_persists_tool_calls_as_evidence(monkeypatch):
    application = {"loan_amount": 10000, "loan_term": 24}
    companies_house = {"found": True, "confidence": "high", "summary": "company is active"}
    store = FakeStore()
    state = {"application": application, "companies_house": companies_house}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    fake_result = {
        "verdict": "consistent",
        "discrepancies": [],
        "monthly_repayment": 416.67,
        "affordability_basis": "application_self_reported",
        "affordability_verdict": "affordable",
        "cross_check": [],
        "summary": "consistent, affordable",
    }

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
                    FakeMessage(fake_result["summary"]),
                ],
                "structured_response": g.FinancialAssessmentResult(**fake_result),
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.check_financial_assessment(state, runtime)

    assert result == {"financial_assessment": fake_result}
    assert store.data["financial_assessment/result.json"] == {
        **fake_result,
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
    application = {
        "company_name": "Acme Ltd",
        "applicant_name": "Jane Smith",
        "company_address": "1 High St, London",
        "director_residential_address": "2 Elm Rd, London",
    }
    state = {"application": application, "companies_house": companies_house}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=fake_tools))
    fake_result = "no adverse findings"
    calls = []

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.search_web(state, runtime)

    assert result == {"web_search": fake_result}
    # tool_calls is added to the S3 evidence artifact only -- web_search
    # state (asserted above) stays the bare result string synthesize_decision
    # expects. FakeAgent never emits ToolMessages, so tool_calls is empty
    # here; see test_search_web_persists_tool_calls_as_evidence below.
    assert store.data["web_search/result.json"] == {"result": fake_result, "tool_calls": [], "grounded": False}
    assert calls[0]["tools"] == fake_tools
    application_context = {
        "applicant_name": "Jane Smith",
        "company_name": "Acme Ltd",
        "company_address": "1 High St, London",
        "director_residential_address": "2 Elm Rd, London",
    }
    expected_content = (
        f"Company: Acme Ltd\n\n"
        f"APPLICATION FORM DETAILS:\n{json.dumps(application_context)}\n\n"
        f"COMPANIES HOUSE FINDINGS:\n{json.dumps(companies_house)}"
    )
    assert calls[1]["message_content"] == expected_content


@pytest.mark.asyncio
async def test_search_web_caps_tool_calls_with_limit_middleware(monkeypatch):
    """A thin-web-presence applicant (common name, no matching profile) can
    otherwise send the model into ever-broader retries -- ToolCallLimitMiddleware
    caps that at web_search.WEBSEARCH_RUN_LIMIT calls to websearch-target___WebSearch
    specifically, per invocation."""
    store = FakeStore()
    state = {"application": {"company_name": "Acme Ltd"}, "companies_house": None}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    calls = []

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent("no adverse findings", calls))

    await g.search_web(state, runtime)

    [limiter] = calls[0]["middleware"]
    assert isinstance(limiter, ToolCallLimitMiddleware)
    assert limiter.tool_name == web_stage.WEBSEARCH_TOOL_NAME
    assert limiter.run_limit == web_stage.WEBSEARCH_RUN_LIMIT
    assert limiter.exit_behavior == "continue"


@pytest.mark.asyncio
async def test_search_web_persists_tool_calls_as_evidence(monkeypatch):
    """Tool calls the agent makes (websearch-target___WebSearch) are harvested
    off the response the same way check_against_policy does, and added to the
    S3 evidence artifact only -- the web_search state value (asserted
    separately above) stays the bare result string."""
    store = FakeStore()
    state = {"application": {"company_name": "Acme Ltd"}, "companies_house": None}
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    class FakeAgentWithToolCall:
        async def ainvoke(self, input):
            return {
                "messages": [
                    input["messages"][0],
                    ToolMessage(
                        content="Acme Ltd linkedin.com/company/acme-ltd",
                        name="websearch-target___WebSearch",
                        tool_call_id="call-1",
                    ),
                    FakeMessage("no adverse findings"),
                ]
            }

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", lambda **kwargs: FakeAgentWithToolCall())

    result = await g.search_web(state, runtime)

    assert result == {"web_search": "no adverse findings"}
    assert store.data["web_search/result.json"] == {
        "result": "no adverse findings",
        "tool_calls": [
            {
                "tool": "websearch-target___WebSearch",
                "result": "Acme Ltd linkedin.com/company/acme-ltd",
            }
        ],
        "grounded": True,
    }


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

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

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
    assert result == {"proposed_decision": expected_final_decision}
    assert store.data["decision/proposed.json"] == expected_final_decision
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

    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(fake_result, calls))

    result = await g.synthesize_decision(state, runtime)

    assert result["proposed_decision"]["outcome"] == "rejected"


# ---------------------------------------------------------------------------
# Full graph wiring
# ---------------------------------------------------------------------------

def _patch_all_integration_points(monkeypatch, agent_response="ok"):
    calls = []
    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent(agent_response, calls))
    async def valid(*args):
        return {"passed": True, "status": "valid", "policy_sha256": "digest"}
    # Mock the external checker while exercising real claim annotation.
    monkeypatch.setattr(validation.PolicyConsistencyChecker, "check", valid)
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
    # check_companies_house's runtime groundedness check needs at least one
    # CompaniesHouse___* tool call when found=True -- see
    # fakes.make_fake_create_agent's docstring.
    agent_context = g.AgentContext(
        store=store, policy_docs=policy_docs, tools=[FakeTool("CompaniesHouse___getCompanyProfile")]
    )

    final_state = await graph.ainvoke({}, config, context=agent_context)

    # check_against_policy/check_companies_house/check_financial_assessment
    # all force structured output — the fake wraps the plain "ok" response
    # into a generic passing result for each schema (see
    # fakes._generic_structured_fields).
    assert final_state["policy_check"]["eligible"] == "eligible"
    assert final_state["companies_house"]["found"] is True
    assert final_state["companies_house_found"] is True
    assert final_state["financial_assessment"]["verdict"] == "consistent"
    assert final_state["web_search"] == "ok"
    # synthesize_decision also forces structured output — the fake wraps
    # "ok" into a generic passing FinalDecisionResult the same way it does
    # CompaniesHouseResult above (see _generic_structured_fields).
    assert final_state["final_decision"]["outcome"] == "pending_human_review"
    # No documents were staged, so triage finds the submission incomplete and
    # routes it back to the applicant -- the assessment still ran in full and
    # still recorded every artifact, which is the point of triaging at the end
    # rather than gating the graph at the front.
    assert final_state["triage"]["route"] == "return_to_applicant"
    assert set(store.data) == {
        "input/application.json",
        "policy_check/result.json",
        "companies_house/result.json",
        "financial_assessment/result.json",
        "web_search/result.json",
        "decision/result.json",
        "decision/proposed.json",
        "decision/validation.json",
        "decision/triage.json",
        "decision/to_applicant.json",
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
    agent_context = g.AgentContext(
        store=store, policy_docs=policy_docs, tools=[FakeTool("CompaniesHouse___getCompanyProfile")]
    )

    final_state = await graph.ainvoke({}, config, context=agent_context)

    assert final_state["web_search"] == "ok"
    saved = await graph.aget_state(config)
    # policy_check/financial_assessment force structured output now (see
    # test_build_graph_runs_all_nodes_in_order) -- checking their dict shape
    # msgpack-serializes/deserializes cleanly is the actual point of this
    # regression test, not any one field's value.
    assert saved.values["policy_check"]["eligible"] == "eligible"
    assert saved.values["financial_assessment"]["verdict"] == "consistent"
    assert saved.values["web_search"] == "ok"
    assert saved.values["final_decision"]["outcome"] == "pending_human_review"


# ---------------------------------------------------------------------------
# Final branch: triage -> to_underwriter / return_to_applicant
#
# The routing rules themselves are tested in test_triage.py. These cover the
# wiring: that both of the graph's endings reach triage, and that the
# conditional edge lands on the right terminal node and writes the right
# handoff artifact.
# ---------------------------------------------------------------------------

COMPLETE_APPLICATION = {
    "company_name": "Acme Ltd", "company_number": "12345678",
    "loan_type": "unsecured-business-loans",
    "applicant_name": "Jane Smith", "year_of_birth": "1980",
    "company_address": "1 High St, London", "loan_purpose": "Expansion",
    "loan_amount": 50000, "loan_term": 24,
    "director_first_name": "Jane", "director_surname": "Smith",
    "director_residential_address": "14 Oak Avenue, Uxbridge, UB8 1AA",
    "trading_start_date": "2018-01-01", "annual_turnover": 500000, "annual_profit": 60000,
}


def _complete_submission() -> dict:
    """Store contents for a submission with nothing outstanding.

    Dated relative to date.today(), not a fixed date: triage_application
    computes staleness fresh per invocation (same as check_against_policy and
    validate_final_decision), so fixed dates here would start failing as the
    calendar moved past the 90-day thresholds.
    """
    today = date.today()
    # Four contiguous ~monthly periods for one account. Coverage is measured
    # in distinct days per account, so these have to actually span more than
    # three calendar months -- three 30-day windows come to 90 days, which is
    # a day or two short of three months and would read as `incomplete`.
    statements = {
        f"input/bank_statement_{index}.json": {
            "account_owner": "Acme Ltd", "bank_name": "Big Bank", "account_number": "12345678",
            "address": "1 High St, London",
            "start_date": (today - timedelta(days=31 * index + 30)).isoformat(),
            "end_date": (today - timedelta(days=31 * index)).isoformat(),
            "balance": 12000.0, "payments_in": 5000.0, "payments_out": 3200.0,
        }
        for index in range(4)
    }
    return {
        "input/application.json": COMPLETE_APPLICATION,
        **statements,
        "input/annual_accounts_2023.json": {
            **ANNUAL_ACCOUNTS_2023, "accounting_year": (today - timedelta(days=60)).isoformat(),
        },
        "input/director_id_passport.json": {
            "document_kind": "passport", "holder_name": "Jane Smith",
            "expiry_date": (today + timedelta(days=900)).isoformat(),
        },
        "input/proof_of_address_utility.json": {
            "document_kind": "utility bill", "holder_name": "Jane Smith",
            "address": "14 Oak Avenue, Uxbridge, UB8 1AA",
            "issue_date": (today - timedelta(days=20)).isoformat(),
        },
    }


@pytest.mark.asyncio
async def test_complete_submission_routes_to_underwriter(monkeypatch, identity):
    store = FakeStore(_complete_submission())
    _patch_all_integration_points(monkeypatch, agent_response="ok")
    graph = g.build_graph(checkpointer=None)
    agent_context = g.AgentContext(
        store=store, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")]
    )

    final_state = await graph.ainvoke({}, g.checkpoint_config(identity), context=agent_context)

    assert final_state["triage"]["route"] == "underwriter"
    assert "decision/to_underwriter.json" in store.data
    assert "decision/to_applicant.json" not in store.data
    # The AI recommendation and the AR claim annotations travel with it as
    # supporting material -- the underwriter weighs them, triage doesn't.
    handoff = store.data["decision/to_underwriter.json"]
    assert handoff["assessment"]["ai_recommendation"]["outcome"] == "approved"
    assert handoff["assessment"]["validation"]["counts"]["valid"] > 0
    # decision/result.json stays the single reviewer-facing artifact, now
    # carrying the route.
    assert store.data["decision/result.json"]["triage"]["route"] == "underwriter"


@pytest.mark.asyncio
async def test_missing_director_id_returns_to_applicant(monkeypatch, identity):
    staged = {k: v for k, v in _complete_submission().items() if "director_id" not in k}
    store = FakeStore(staged)
    _patch_all_integration_points(monkeypatch, agent_response="ok")
    graph = g.build_graph(checkpointer=None)
    agent_context = g.AgentContext(
        store=store, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")]
    )

    final_state = await graph.ainvoke({}, g.checkpoint_config(identity), context=agent_context)

    assert final_state["triage"]["route"] == "return_to_applicant"
    assert "decision/to_applicant.json" in store.data
    assert "decision/to_underwriter.json" not in store.data
    applicant = store.data["decision/to_applicant.json"]
    assert [item["requirement"] for item in applicant["outstanding"]] == ["director_id"]
    assert "passport or driving licence" in applicant["applicant_note"]
    # An applicant must never be shown the bank's internal credit view of them.
    assert not {"assessment", "ai_recommendation", "policy_check", "companies_house"} & set(applicant)


@pytest.mark.asyncio
async def test_complete_submission_reaches_underwriter_despite_no_company_match(monkeypatch, identity):
    """The reject_no_company ending routes through triage too. A company
    Companies House couldn't find is a lookup a human investigates, not
    something the applicant can fix by uploading another file -- so with
    complete paperwork it still goes to the underwriter.

    tools=[] leaves the model's found=True with no supporting tool call, which
    the runtime groundedness check downgrades to found=False (see
    test_check_companies_house_overrides_ungrounded_found_true_with_no_tool_calls)
    and which the resilience layer records as a lookup that never completed
    rather than one that completed and found nothing -- either way it is the
    reject_no_company ending, and either way triage decides the route.
    """
    store = FakeStore(_complete_submission())
    _patch_all_integration_points(monkeypatch, agent_response="ok")
    graph = g.build_graph(checkpointer=None)
    agent_context = g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[])

    final_state = await graph.ainvoke({}, g.checkpoint_config(identity), context=agent_context)

    assert final_state["companies_house_found"] is False
    # The assessment nodes never ran on this branch -- their state keys exist
    # as empty channel defaults, so the artifacts they would have written are
    # the reliable evidence that they were skipped.
    assert not final_state["policy_check"]
    assert "policy_check/result.json" not in store.data
    # Complete paperwork, so it still reaches a human despite the failed lookup.
    assert final_state["triage"]["route"] == "underwriter"
    handoff = store.data["decision/to_underwriter.json"]
    assert handoff["assessment"]["ai_recommendation"]["reason"] == "companies_house_lookup_unavailable"
    assert final_state["company_lookup_failed"] is True


@pytest.mark.asyncio
async def test_unreadable_document_returns_to_applicant_instead_of_failing_the_run(monkeypatch, identity):
    staged = _complete_submission()
    staged["input/bank_statement_0.json"] = {"account_owner": "Acme Ltd"}  # fails BankStatementSchema
    store = FakeStore(staged)
    _patch_all_integration_points(monkeypatch, agent_response="ok")
    graph = g.build_graph(checkpointer=None)
    agent_context = g.AgentContext(
        store=store, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")]
    )

    final_state = await graph.ainvoke({}, g.checkpoint_config(identity), context=agent_context)

    assert final_state["document_errors"] == [
        {"key": "input/bank_statement_0.json", "state_key": "bank_statements"}
    ]
    assert final_state["triage"]["route"] == "return_to_applicant"
    outstanding = store.data["decision/to_applicant.json"]["outstanding"]
    assert [(item["requirement"], item["status"]) for item in outstanding] == [
        ("bank_statements", "unreadable")
    ]
