import copy
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import fionaa.graph as g
from fionaa.testing.fakes import FakePolicyDocs, FakeRuntime, FakeStore, FakeTool, make_fake_create_agent
from fionaa.policy_consistency import PolicyConsistencyChecker, policy_digest


def checker(response):
    client = Mock()
    client.apply_guardrail.return_value = response
    return PolicyConsistencyChecker({"secured-business-loans": {
        "guardrail_id": "example", "guardrail_version": "1",
        "policy_sha256": policy_digest("policy"),
    }}, client)


def valid_response():
    return {"action": "NONE", "usage": {"automatedReasoningPolicyUnits": 1},
            "assessments": [{"automatedReasoningPolicy": {"findings": [{"valid": {}}]}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["valid", "invalid", "satisfiable", "impossible",
                                  "translationAmbiguous", "tooComplex", "noTranslations"])
async def test_findings_are_enforced(kind):
    response = valid_response()
    response["assessments"][0]["automatedReasoningPolicy"]["findings"] = [{kind: {}}]
    instance = checker(response)
    result = await instance.check("secured-business-loans", "policy", {"loan_amount": 40000}, {"eligible": "eligible"})
    assert result["passed"] is (kind != "invalid")
    assert result["status"] == {"valid": "valid", "invalid": "invalid"}.get(kind, "inconclusive")
    request = instance.client.apply_guardrail.call_args.kwargs
    assert request["source"] == "OUTPUT"
    assert request["content"][0]["text"]["qualifiers"] == ["query"]
    assert request["content"][1]["text"]["qualifiers"] == ["guard_content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["empty", "zero_units", "mixed", "intervened"])
async def test_no_partial_or_silent_success(change):
    response = valid_response()
    if change == "empty":
        response["assessments"] = []
    elif change == "zero_units":
        response["usage"] = {}
    elif change == "mixed":
        response["assessments"][0]["automatedReasoningPolicy"]["findings"].append({"invalid": {}})
    else:
        response["action"] = "GUARDRAIL_INTERVENED"
    assert not (await checker(response).check("secured-business-loans", "policy", {}, {}))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "draft", "stale"])
async def test_configuration_fails_closed_without_aws(change):
    instance = checker(valid_response())
    if change == "missing":
        instance.bindings = {}
    elif change == "draft":
        instance.bindings["secured-business-loans"]["guardrail_version"] = "DRAFT"
    else:
        instance.bindings["secured-business-loans"]["policy_sha256"] = "old"
    assert not (await instance.check("secured-business-loans", "policy", {}, {}))["passed"]
    instance.client.apply_guardrail.assert_not_called()


@pytest.mark.asyncio
async def test_service_failure_is_not_an_approval():
    instance = checker(valid_response())
    instance.client.apply_guardrail.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "sensitive"}}, "ApplyGuardrail")
    result = await instance.check("secured-business-loans", "policy", {}, {})
    assert result["status"] == "validation_error"
    assert "sensitive" not in str(result)


class FakeChecker:
    def __init__(self, passed):
        self.passed = passed

    async def check(self, *args):
        return {"passed": self.passed, "status": "valid" if self.passed else "not_validated",
                "policy_sha256": "digest"}


class SequencedFakeChecker:
    """Returns one canned result per call, in call order -- lets a test
    control exactly which atomic claim (of several fanned out by
    _validate_decision) gets which Automated Reasoning outcome."""

    def __init__(self, results):
        self.results = list(results)
        self.claims_seen = []

    async def check(self, loan_type, policy_text, facts, claim):
        self.claims_seen.append(claim)
        return self.results[len(self.claims_seen) - 1]


def _decision_state(**overrides):
    """A minimal state validate_final_decision can run against: an
    application/loan_type, a policy_check result (defaults to just the bare
    eligible verdict -- no clause_findings/documentation_gaps), and a
    proposed_decision (the thing every real run always has by the time this
    node executes, now that validation runs once at the end -- see
    validation.py's collapse from a two-stage design)."""
    state = {
        "application": {"loan_type": "secured-business-loans"},
        "policy_check": {"eligible": "eligible"},
        "proposed_decision": {"outcome": "approved", "reason": "all clean", "rationale": "no issues found"},
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["valid", "invalid", "inconclusive", "validation_error",
                                    "not_configured", "policy_version_mismatch", "not_validated"])
@pytest.mark.parametrize("outcome", ["approved", "rejected", "referred"])
async def test_annotations_never_determine_the_human_outcome(status, outcome):
    store = FakeStore()
    checker = SequencedFakeChecker([
        {"status": "valid", "policy_sha256": "digest", "findings": [{"valid": {}}]},
        {"status": status, "policy_sha256": "digest", "findings": [], "error_type": "Example"},
        {"status": "valid", "policy_sha256": "digest", "findings": [{"valid": {}}]},
        {"status": "valid", "policy_sha256": "digest", "findings": [{"valid": {}}]},
        {"status": "valid", "policy_sha256": "digest", "findings": [{"valid": {}}]},
    ])
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], checker))
    state = _decision_state(
        policy_check={"eligible": "eligible", "clause_findings": ["Loan amount is in range."]},
        proposed_decision={"outcome": outcome, "reason": "clean", "rationale": "no issues"},
    )
    original = copy.deepcopy(state)
    result = await g.validate_final_decision(state, runtime)
    report = result["final_decision"]
    assert report["outcome"] == "pending_human_review"
    assert report["ai_recommendation"] == original["proposed_decision"]
    assert state == original
    validation = store.data["decision/validation.json"]
    assert "passed" not in validation
    annotation = validation["claims"][1]
    expected = status if status in {"valid", "invalid", "inconclusive"} else "not_checked"
    assert annotation["status"] == expected
    assert annotation["source_path"] == "/policy_check/clause_findings/0"
    assert annotation["claim"]["summary"] == "Loan amount is in range."
    if expected == "not_checked":
        assert annotation["check_status"] == status
    assert sum(validation["counts"].values()) == 5
    assert validation["counts"][expected] >= 1
    assert report["validation"] == validation
    assert store.data["decision/result.json"] == report


@pytest.mark.asyncio
async def test_all_claims_link_to_report_fields_and_keep_evidence():
    store = FakeStore()
    checker = SequencedFakeChecker([
        {"status": "valid", "policy_sha256": "digest", "findings": [{"valid": {}}]}
        for _ in range(8)
    ])
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], checker))
    policy = {"eligible": "eligible", "clause_findings": ["Amount fits.", "Term fits."],
              "documentation_gaps": ["Bank statements missing."], "summary": "Criteria met."}
    state = _decision_state(policy_check=policy, annual_accounts=[{"turnover": 50000}])
    state["proposed_decision"]["policy_check"] = policy
    report = (await g.validate_final_decision(state, runtime))["final_decision"]
    validation = report["validation"]
    assert len(validation["claims"]) == 8
    for item in validation["claims"]:
        value = report
        for part in item["source_path"].strip("/").split("/"):
            value = value[int(part)] if isinstance(value, list) else value[part]
        assert value in item["claim"]["summary"]
        assert item["findings"] == [{"valid": {}}]
        assert "passed" not in item
    assert validation["evidence"]["annual_accounts"] == state["annual_accounts"]
    assert validation["evidence"]["application_self_reported"] == state["application"]


@pytest.mark.asyncio
async def test_unconfigured_full_graph_produces_report_for_human_review(monkeypatch):
    """Unavailable checks remain visible in the completed review report."""
    calls = []
    from fionaa.workflow import policy as policy_stage, companies_house as company_stage, financial as financial_stage, web_search as web_stage, decision as decision_stage
    for stage in (policy_stage, company_stage, financial_stage, web_stage, decision_stage):
        monkeypatch.setattr(stage, "create_agent", make_fake_create_agent("candidate assessment", calls))
    store = FakeStore({"input/application.json": {
        "loan_type": "secured-business-loans", "company_name": "Acme Ltd",
    }})
    context = g.AgentContext(
        store, FakePolicyDocs(), [FakeTool("CompaniesHouse___getCompanyProfile")], PolicyConsistencyChecker({})
    )

    result = await g.build_graph().ainvoke({}, context=context)

    assert result["final_decision"]["outcome"] == "pending_human_review"
    assert all(c["status"] == "not_checked" for c in result["final_decision"]["validation"]["claims"])
    assert "companies_house/result.json" in store.data
    assert "policy_check/result.json" in store.data
    assert "financial_assessment/result.json" in store.data
    assert "web_search/result.json" in store.data
    assert "decision/proposed.json" in store.data
