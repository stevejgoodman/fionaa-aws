import copy
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import graph as g
from fakes import FakePolicyDocs, FakeRuntime, FakeStore, make_fake_create_agent
from policy_consistency import PolicyConsistencyChecker, policy_digest


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
    assert result["passed"] is (kind == "valid")
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
        return {"passed": self.passed, "status": "valid" if self.passed else "not_validated"}


@pytest.mark.asyncio
async def test_policy_failure_refers_and_stops_graph():
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], FakeChecker(False)))
    command = await g.validate_policy_assessment({
        "application": {"loan_type": "secured-business-loans"},
        "policy_check": {"eligible": "eligible"},
    }, runtime)
    assert command.goto == g.END
    assert command.update["final_decision"]["outcome"] == "referred"
    assert not store.data["policy_check/validation.json"]["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("passed,upstream", [(False, True), (True, False), (True, True)])
async def test_final_publication_requires_both_checks(passed, upstream):
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], FakeChecker(passed)))
    proposed = {"outcome": "approved", "reason": "eligible"}
    original = copy.deepcopy(proposed)
    result = await g.validate_final_decision({
        "application": {"loan_type": "secured-business-loans"},
        "policy_validation": {"passed": upstream}, "proposed_decision": proposed,
    }, runtime)
    assert result["final_decision"]["outcome"] == ("approved" if passed and upstream else "referred")
    assert proposed == original
    assert store.data["decision/result.json"] == result["final_decision"]


@pytest.mark.asyncio
async def test_unconfigured_full_graph_refers_before_external_research(monkeypatch):
    calls = []
    monkeypatch.setattr(g, "create_agent", make_fake_create_agent("candidate assessment", calls))
    store = FakeStore({"input/application.json": {"loan_type": "secured-business-loans"}})
    context = g.AgentContext(store, FakePolicyDocs(), [], PolicyConsistencyChecker({}))
    result = await g.build_graph().ainvoke({}, context=context)
    assert result["final_decision"]["outcome"] == "referred"
    assert "companies_house/result.json" not in store.data
    assert "decision/proposed.json" not in store.data
    assert len(calls) == 2  # Only policy agent construction and invocation.
