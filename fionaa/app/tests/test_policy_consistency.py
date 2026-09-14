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
    # `invalid` is the only finding type that's an actual proven
    # contradiction of the policy -- it fails closed. Every other non-valid
    # kind means Automated Reasoning couldn't fully confirm the claim (a
    # translation/tooling limitation, not evidence the claim is wrong), so
    # it's `inconclusive` and allowed to proceed rather than referred.
    response = valid_response()
    response["assessments"][0]["automatedReasoningPolicy"]["findings"] = [{kind: {}}]
    instance = checker(response)
    result = await instance.check("secured-business-loans", "policy", {"loan_amount": 40000}, {"eligible": "eligible"})
    assert result["passed"] is (kind != "invalid")
    assert result["status"] == {"valid": "valid", "invalid": "not_validated"}.get(kind, "inconclusive")
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
async def test_decision_failure_refers():
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], FakeChecker(False)))
    result = await g.validate_final_decision(_decision_state(), runtime)
    assert result["final_decision"]["outcome"] == "referred"
    assert not store.data["decision/validation.json"]["passed"]
    assert store.data["decision/result.json"] == result["final_decision"]


@pytest.mark.asyncio
async def test_decision_success_publishes_proposed_decision():
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], FakeChecker(True)))
    proposed = {"outcome": "approved", "reason": "all clean", "rationale": "no issues found"}
    original = copy.deepcopy(proposed)
    result = await g.validate_final_decision(_decision_state(proposed_decision=proposed), runtime)
    assert result["final_decision"]["outcome"] == "approved"
    assert proposed == original  # not mutated
    assert store.data["decision/result.json"] == result["final_decision"]


@pytest.mark.asyncio
async def test_decision_checks_policy_check_and_final_decision_claims_individually():
    """_validate_decision must fan out over every atomic assertion from
    BOTH the policy_check result (eligible verdict + each clause_finding +
    each documentation_gap) AND the final decision's own
    outcome/reason/rationale -- the collapsed design (see validation.py's
    docstring) checks everything once, at the end, rather than splitting
    across an early policy_check-stage gate and a final-decision-stage
    gate that could never see full evidence."""
    store = FakeStore()
    checker = SequencedFakeChecker([
        {"passed": True, "status": "valid", "policy_sha256": "digest"} for _ in range(5)
    ])
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], checker))
    state = _decision_state(
        policy_check={"eligible": "eligible", "clause_findings": ["Loan amount is in range."]},
        proposed_decision={"outcome": "approved", "reason": "clean", "rationale": "no issues"},
    )

    result = await g.validate_final_decision(state, runtime)

    # eligible verdict + 1 clause_finding (policy_check) + outcome + reason +
    # rationale (final decision) = 5 atomic claims.
    assert len(checker.claims_seen) == 5
    assert any("Loan amount is in range." in c["summary"] for c in checker.claims_seen)
    assert any("approved" in c["summary"] for c in checker.claims_seen)
    assert result["final_decision"]["outcome"] == "approved"
    validation = store.data["decision/validation.json"]
    assert validation["passed"] and validation["status"] == "valid"
    assert len(validation["claims"]) == 5
    assert {c["source"] for c in validation["claims"]} == {"policy_check", "final_decision"}


@pytest.mark.asyncio
async def test_decision_one_invalid_claim_among_many_still_fails_closed():
    """An `invalid` finding on a single clause among several must still
    refer the whole application -- fanning out into many atomic claims must
    not let a genuine contradiction on one of them slip through because its
    siblings passed."""
    store = FakeStore()
    checker = SequencedFakeChecker([
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": False, "status": "not_validated", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
    ])
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], checker))
    state = _decision_state(policy_check={"eligible": "eligible", "clause_findings": ["A contradicted clause."]})

    result = await g.validate_final_decision(state, runtime)

    assert result["final_decision"]["outcome"] == "referred"
    validation = store.data["decision/validation.json"]
    assert not validation["passed"]
    assert validation["status"] == "not_validated"


@pytest.mark.asyncio
async def test_decision_inconclusive_claim_does_not_block_publication():
    """A non-invalid, non-valid finding (tooComplex/translationAmbiguous/...)
    on one claim must not block publication -- see
    PolicyConsistencyChecker's own passed=True-for-inconclusive contract --
    but the aggregate status must still surface as 'inconclusive', not
    silently 'valid'."""
    store = FakeStore()
    checker = SequencedFakeChecker([
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": True, "status": "inconclusive", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
        {"passed": True, "status": "valid", "policy_sha256": "digest"},
    ])
    runtime = FakeRuntime(g.AgentContext(store, FakePolicyDocs(), [], checker))
    state = _decision_state(policy_check={"eligible": "eligible", "clause_findings": ["A tooComplex clause."]})

    result = await g.validate_final_decision(state, runtime)

    assert result["final_decision"]["outcome"] == "approved"
    validation = store.data["decision/validation.json"]
    assert validation["passed"]
    assert validation["status"] == "inconclusive"


@pytest.mark.asyncio
async def test_unconfigured_full_graph_runs_to_completion_then_refers(monkeypatch):
    """Without Automated Reasoning bindings configured,
    PolicyConsistencyChecker.check() always fails closed (see
    test_configuration_fails_closed_without_aws) -- but since validation now
    runs only once, at the very end (see validation.py's collapse from a
    two-stage design), there's no early gate left to short-circuit the
    graph. companies_house -> policy_check -> financial_assessment ->
    web_search -> synthesize_decision must all still run to completion;
    only the final validate_final_decision step refers."""
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

    assert result["final_decision"]["outcome"] == "referred"
    # Unlike the old two-stage design, an unconfigured checker no longer
    # short-circuits the graph early -- every node still runs, and only the
    # final validation step (which nothing downstream of it exists to skip)
    # refers.
    assert "companies_house/result.json" in store.data
    assert "policy_check/result.json" in store.data
    assert "financial_assessment/result.json" in store.data
    assert "web_search/result.json" in store.data
    assert "decision/proposed.json" in store.data
