from copy import deepcopy
from datetime import date, timedelta

import pytest
from langchain.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import ValidationError

from fionaa.domain.ingestion import SubmissionManifest
from fionaa.evidence_readiness import bank_coverage
from fionaa.graph import build_graph
from fionaa.testing.document_fixtures import (
    GOODAI_APPLICATION, GOODAI_ANNUAL_ACCOUNTS_HAPPY, GOODAI_BANK_STATEMENTS_RECENT,
)
from fionaa.testing.fakes import FakeStore, FakeRuntime, FakePolicyDocs, FakeTool, make_fake_create_agent
from fionaa.workflow import companies_house, policy, financial, web_search, decision, validation
from fionaa.workflow.loading import load_application
from fionaa.workflow.state import AgentContext
from fionaa.workflow.triage import triage_application, prepare_incomplete_review


@pytest.fixture
def submission():
    application = {**GOODAI_APPLICATION, "vat_registered": False, "has_existing_borrowing": False}
    data = {
        "director_id": {"document_kind": "passport", "holder_name": "Steve Goodman"},
        "proof_of_address": {"holder_name": "Steve Goodman", "address": application["director_residential_address"]},
        "annual_accounts": {**GOODAI_ANNUAL_ACCOUNTS_HAPPY, "company_name": application["company_name"], "accounting_year": "2026-08-31"},
        "bank_statement": {**GOODAI_BANK_STATEMENTS_RECENT[0], "account_owner": application["company_name"],
                           "start_date": "2026-06-01", "end_date": "2026-08-31"},
    }
    manifest = {"schema_version": 1, "submission_id": "submission-1", "submission_date": "2026-09-01",
                "inventory_complete": True, "documents": []}
    store = FakeStore({"input/application.json": application, "ingestion/submission.json": manifest})
    for kind, payload in data.items():
        key = f"ingestion/documents/{kind}.json"
        manifest["documents"].append({"document_type": kind, "source_key": f"input/documents/{kind}.pdf",
                                      "extraction_key": key, "processing_status": "processed"})
        store.data[key] = payload
    return store


def runtime(store):
    return FakeRuntime(AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))


def load_and_triage(store):
    state = load_application({}, runtime(store))
    state.update(prepare_incomplete_review(state))
    state.update(triage_application(state, runtime(store)))
    return state


def remove_document(store, kind):
    manifest = store.data["ingestion/submission.json"]
    manifest["documents"] = [doc for doc in manifest["documents"] if doc["document_type"] != kind]


def test_complete_submission_and_quarterly_statement(submission):
    state = load_and_triage(submission)
    assert state["triage"]["route"] == "underwriter"
    assert state["triage"]["submission_id"] == "submission-1"
    facts = validation._facts(state, date(2026, 9, 1))["derived_facts"]
    assert facts["bankStatementsMonthsCount"] == 3
    assert facts["hasRecentBankStatements"] is True
    assert facts["hasValidDirectorID"] is True
    assert facts["hasProofOfDirectorAddress"] is True
    assert submission.data["decision/result.json"]["triage"] == submission.data["decision/triage.json"]


@pytest.mark.parametrize("kind", ["director_id", "proof_of_address", "bank_statement", "annual_accounts"])
def test_missing_essential_is_documented(submission, kind):
    remove_document(submission, kind)
    triage = load_and_triage(submission)["triage"]
    assert triage["route"] == "return_to_applicant"
    assert any(item["status"] == "missing" and item["correction"] for item in triage["findings"])


@pytest.mark.parametrize("status,route", [("pending", "internal_hold"), ("failed", "internal_hold"), ("unreadable", "return_to_applicant")])
def test_processing_failure_differs_from_unreadable_source(submission, status, route):
    submission.data["ingestion/submission.json"]["documents"][0]["processing_status"] = status
    assert load_and_triage(submission)["triage"]["route"] == route


def test_bad_extraction_is_preserved_as_failed_and_not_missing(submission):
    submission.data["ingestion/documents/director_id.json"] = {"wrong": "shape"}
    state = load_and_triage(submission)
    assert state["triage"]["route"] == "internal_hold"
    assert state["ingested_documents"][0]["processing_status"] == "failed"
    assert state["triage"]["findings"][1]["status"] == "unable_to_verify"


def test_missing_extraction_is_a_processing_failure(submission):
    del submission.data["ingestion/documents/bank_statement.json"]
    assert load_and_triage(submission)["triage"]["route"] == "internal_hold"


def test_legacy_submission_without_manifest_is_not_told_to_resubmit(submission):
    del submission.data["ingestion/submission.json"]
    state = load_and_triage(submission)
    assert state["triage"]["route"] == "internal_hold"
    assert state["triage"]["submission_date"] is None
    assert state["triage"]["findings"] == []


@pytest.mark.parametrize("field,value", [("inventory_complete", False), ("submission_date", "invalid"), ("submission_date", "9999-01-01")])
def test_bad_manifest_holds(submission, field, value):
    submission.data["ingestion/submission.json"][field] = value
    assert load_and_triage(submission)["triage"]["route"] == "internal_hold"


def test_rejects_duplicate_manifest_records_and_path_traversal(submission):
    manifest = deepcopy(submission.data["ingestion/submission.json"])
    manifest["documents"].append(manifest["documents"][0])
    with pytest.raises(ValidationError):
        SubmissionManifest.model_validate(manifest)
    manifest["documents"] = [dict(manifest["documents"][0], extraction_key="input/../other-customer.json")]
    with pytest.raises(ValidationError):
        SubmissionManifest.model_validate(manifest)


def test_storage_access_errors_are_not_swallowed(submission, monkeypatch):
    original = submission.get_json
    def get_json(key):
        if key == "ingestion/documents/director_id.json":
            raise PermissionError("denied")
        return original(key)
    monkeypatch.setattr(submission, "get_json", get_json)
    with pytest.raises(PermissionError, match="denied"):
        load_application({}, runtime(submission))


def test_management_information_is_an_alternative_to_stale_accounts(submission):
    submission.data["ingestion/documents/annual_accounts.json"]["accounting_year"] = "2020-01-01"
    assert load_and_triage(submission)["triage"]["route"] == "return_to_applicant"
    submission.data["ingestion/submission.json"]["documents"].append({
        "document_type": "management_information", "source_key": "input/documents/mi.pdf",
        "extraction_key": "ingestion/documents/mi.json", "processing_status": "processed",
    })
    submission.data["ingestion/documents/mi.json"] = {
        "company_name": GOODAI_APPLICATION["company_name"], "period_start": "2026-01-01",
        "period_end": "2026-08-31", "turnover": 150000, "profit": -50000,
    }
    state = load_and_triage(submission)
    assert state["triage"]["route"] == "underwriter"
    assert state["management_information"][0]["profit"] == -50000


def test_applicant_declarations_control_conditions(submission):
    application = submission.data["input/application.json"]
    application.update(vat_registered=True, loan_amount=30000)
    assert load_and_triage(submission)["triage"]["route"] == "underwriter"
    application["has_existing_borrowing"] = True
    assert load_and_triage(submission)["triage"]["route"] == "return_to_applicant"
    application["has_existing_borrowing"] = None
    assert load_and_triage(submission)["triage"]["route"] == "return_to_applicant"
    application["has_existing_borrowing"] = "false"
    assert load_and_triage(submission)["triage"]["route"] == "internal_hold"


def statement(start, end, account="1"):
    return {"source_key": "input/documents/bank.pdf", "data": {
        "start_date": start, "end_date": end, "account_number": account, "bank_name": "Example Bank",
    }}


def test_duplicate_overlapping_statements_do_not_manufacture_coverage():
    doc = statement("2026-08-01", "2026-08-31")
    assert bank_coverage([doc, doc, doc], date(2026, 9, 1)).status == "incomplete"
    assert bank_coverage([doc, statement("2026-08-15", "2026-08-31")], date(2026, 9, 1)).status == "incomplete"


def test_accounts_cannot_be_added_together_for_coverage():
    docs = [statement("2026-08-01", "2026-08-31", str(i)) for i in range(3)]
    assert bank_coverage(docs, date(2026, 9, 1)).status == "incomplete"


def test_blank_account_cannot_establish_coverage():
    assert bank_coverage([statement("2026-06-01", "2026-08-31", " ")], date(2026, 9, 1)).status == "unable_to_verify"


def test_unrelated_statement_needs_reconciliation(submission):
    submission.data["ingestion/submission.json"]["documents"].append({
        "document_type": "bank_statement", "source_key": "input/documents/other-bank.pdf",
        "extraction_key": "ingestion/documents/other-bank.json", "processing_status": "processed",
    })
    submission.data["ingestion/documents/other-bank.json"] = {
        **submission.data["ingestion/documents/bank_statement.json"], "account_owner": "Another business",
    }
    assert load_and_triage(submission)["triage"]["route"] == "internal_hold"


@pytest.mark.parametrize("age,status", [(89, "satisfied"), (90, "stale")])
def test_bank_recency_boundary(age, status):
    end = date(2026, 6, 30)
    assert bank_coverage([statement("2026-04-01", end.isoformat())], end + timedelta(days=age)).status == status


@pytest.mark.parametrize("start,end", [("bad", "2026-08-31"), ("2026-09-01", "2026-08-31"), ("2026-06-01", "2026-09-02")])
def test_bad_statement_dates_are_internal_issues(start, end):
    assert bank_coverage([statement(start, end)], date(2026, 9, 1)).status == "unable_to_verify"


def patch_agents(monkeypatch, recommendation="approved", company_found=True, tool_error=False):
    calls = []
    for stage in (companies_house, policy, financial, web_search, decision):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent("ok", calls))
    monkeypatch.setattr(decision, "create_agent", make_fake_create_agent({
        "outcome": recommendation, "reason": "Assessment findings.", "rationale": "Advisory only.",
    }, calls))
    if not company_found or tool_error:
        factory = make_fake_create_agent({"found": company_found, "confidence": "high", "summary": "Company lookup result."}, calls)
        def company_factory(**kwargs):
            agent = factory(**kwargs)
            original = agent.ainvoke
            async def invoke(payload):
                result = await original(payload)
                if tool_error:
                    result["messages"].append(ToolMessage(content="Service unavailable", name="CompaniesHouse___getCompanyProfile", tool_call_id="error", status="error"))
                return result
            agent.ainvoke = invoke
            return agent
        monkeypatch.setattr(companies_house, "create_agent", company_factory)
    async def check(*args):
        return {"status": "valid", "policy_sha256": "digest"}
    monkeypatch.setattr(validation.PolicyConsistencyChecker, "check", check)
    return calls


@pytest.mark.parametrize("recommendation", ["approved", "rejected", "referred"])
async def test_full_graph_routes_complete_applications_regardless_of_recommendation(submission, monkeypatch, recommendation):
    calls = patch_agents(monkeypatch, recommendation)
    graph = build_graph(MemorySaver())
    context = AgentContext(store=submission, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")])
    config = {"configurable": {"thread_id": "triage-test"}}
    result = await graph.ainvoke({}, config, context=context)
    assert result["triage"]["route"] == "underwriter"
    assert result["final_decision"]["ai_recommendation"]["outcome"] == recommendation
    assert result["final_decision"]["outcome"] == "pending_human_review"
    assert "ASSESSMENT REFERENCE DATE: 2026-09-01" in str(calls)
    assert submission.data["decision/validation.json"]["evidence"]["derived_facts"]["today"] == "2026-09-01"
    assert (await graph.aget_state(config)).values["triage"] == result["triage"]


@pytest.mark.parametrize("missing,expected", [(False, "underwriter"), (True, "return_to_applicant")])
async def test_no_company_branch_still_triages(submission, monkeypatch, missing, expected):
    patch_agents(monkeypatch, company_found=False)
    if missing:
        remove_document(submission, "director_id")
    context = AgentContext(store=submission, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")])
    result = await build_graph().ainvoke({}, context=context)
    assert result["triage"]["route"] == expected
    assert result["final_decision"]["ai_recommendation"]["reason"] == "companies_house_no_match"
    assert "policy_check/result.json" not in submission.data


async def test_company_service_failure_never_causes_applicant_return(submission, monkeypatch):
    patch_agents(monkeypatch, company_found=False, tool_error=True)
    remove_document(submission, "director_id")
    context = AgentContext(store=submission, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")])
    result = await build_graph().ainvoke({}, context=context)
    assert result["triage"]["route"] == "internal_hold"


async def test_company_timeout_persists_internal_hold(submission, monkeypatch):
    patch_agents(monkeypatch)
    class TimeoutAgent:
        async def ainvoke(self, payload):
            raise TimeoutError("lookup timed out")
    monkeypatch.setattr(companies_house, "create_agent", lambda **kwargs: TimeoutAgent())
    result = await build_graph().ainvoke({}, context=runtime(submission).context)
    assert result["triage"]["route"] == "internal_hold"
    assert submission.data["companies_house/result.json"]["grounding_reasons"] == ["lookup_unavailable: lookup timed out"]
    assert result["final_decision"]["ai_recommendation"]["reason"] == "companies_house_lookup_unavailable"


async def test_incomplete_form_skips_agents_and_returns_correction(submission, monkeypatch):
    calls = patch_agents(monkeypatch)
    del submission.data["input/application.json"]["loan_term"]
    result = await build_graph().ainvoke({}, context=runtime(submission).context)
    assert result["triage"]["route"] == "return_to_applicant"
    assert "loan term" in result["triage"]["findings"][0]["correction"]
    assert calls == []


async def test_final_result_is_written_only_after_triage(submission, monkeypatch):
    patch_agents(monkeypatch)
    context = AgentContext(store=submission, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")])
    await build_graph().ainvoke({}, context=context)
    writes = [payload for key, payload in submission.puts if key == "decision/result.json"]
    assert len(writes) == 1
    assert writes[0]["triage"]["route"] == "underwriter"


async def test_management_information_reaches_financial_assessment(submission, monkeypatch):
    calls = patch_agents(monkeypatch)
    remove_document(submission, "annual_accounts")
    submission.data["ingestion/submission.json"]["documents"].append({
        "document_type": "management_information", "source_key": "input/documents/mi.json",
        "extraction_key": "ingestion/documents/mi.json", "processing_status": "processed",
    })
    submission.data["ingestion/documents/mi.json"] = {
        "company_name": GOODAI_APPLICATION["company_name"], "period_start": "2026-01-01",
        "period_end": "2026-08-31", "turnover": 150000, "profit": -50000,
    }
    context = AgentContext(store=submission, policy_docs=FakePolicyDocs(), tools=[FakeTool("CompaniesHouse___getCompanyProfile")])
    result = await build_graph().ainvoke({}, context=context)
    assert result["triage"]["route"] == "underwriter"
    assert "MANAGEMENT INFORMATION (supporting evidence, not filed accounts)" in str(calls)
    assert submission.data["decision/validation.json"]["evidence"]["derived_facts"]["hasAccountsOrManagementInformation"] is True


async def test_invalid_processing_skips_assessment_and_persists_hold(submission, monkeypatch):
    calls = patch_agents(monkeypatch)
    submission.data["ingestion/documents/annual_accounts.json"] = {}
    result = await build_graph().ainvoke({}, context=runtime(submission).context)
    assert result["triage"]["route"] == "internal_hold"
    assert calls == []


def test_other_loan_types_keep_existing_review_route():
    state = {"application": {"loan_type": "invoice-factoring"}, "final_decision": {"outcome": "pending_human_review"}}
    store = FakeStore()
    assert triage_application(state, runtime(store)) == {}
    assert store.data["decision/result.json"] == state["final_decision"]
