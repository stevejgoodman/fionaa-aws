"""DeepEval financial-assessment node evals -- see README.md and
test_companies_house.py's module docstring for the overall approach.

check_financial_assessment (graph.py:319) takes more context than the other
nodes: `application`, plus the *prior* nodes' own results
(`companies_house`, `policy_check`), plus `annual_accounts`/`bank_statements`
(the documents cross_check_financial_figures and the affordability check
read). The other three node dataset prefixes only ever needed `application`
(or a bare company name) as input, so `turns[0].input` there is just that
one JSON value. financial-assessment scenarios need more than one input
value, so `turns[0].input` is a JSON object of the form:

    {"application": {...}, "companies_house": {...}, "policy_check": "...",
     "annual_accounts": [...], "bank_statements": [...]}

rather than the bare `application` dict `dataset.py`'s other consumers get.
`_run_financial_assessment` below expects that shape.

Like check_against_policy, check_financial_assessment captures its own
tool_calls into `store` -- no need to duplicate the agent construction here.

Since check_financial_assessment now forces structured output
(FinancialAssessmentResult, see graph.py), `test_financial_assessment_scenario`
adds one deterministic (no-LLM) assertion alongside the judge metrics below:
`cross_check` in the parsed actual_output must exactly match what
check_tools.cross_check_financial_figures independently computes for the
same application/annual_accounts inputs. This checks the model faithfully
copied the code-computed comparison through rather than restating its own
delta -- the same "an LLM judge is a soft check for what should be an exact
arithmetic assertion" gap test_policy_check.py's module docstring flags for
its advance-rate figure, closed here with a hard assertion instead of a
GEval judge.

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps deepeval test run deepeval_evals/test_financial_assessment.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("FIONAA_APPLICATIONS_BUCKET", "eval-harness-unused-bucket")
os.environ.setdefault("FIONAA_POLICY_DOCS_BUCKET", "eval-harness-unused-bucket")
os.environ.setdefault("FIONAA_KMS_KEY_ARN", "alias/aws/s3")
os.environ.setdefault("FIONAA_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::000000000000:role/unused")
os.environ.setdefault("FIONAA_CHECKPOINT_MEMORY_ID", "eval-harness-unused")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

APP_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "fionaa"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from deepeval import assert_test  # noqa: E402
from deepeval.test_case import LLMTestCase, ToolCall  # noqa: E402

import graph as g  # noqa: E402
from check_tools import FieldComparison, cross_check_financial_figures  # noqa: E402
from fakes import FakePolicyDocs, FakeRuntime, FakeStore  # noqa: E402

from dataset import load_goldens  # noqa: E402
from metrics import (  # noqa: E402
    ToolPrefixCorrectness,
    assertions_metric,
    correctness_metric,
    injection_resistance_metric,
)

GOLDENS = load_goldens(prefix="financial-assessment-")


async def _run_financial_assessment(
    application: dict,
    companies_house: dict | None,
    policy_check: str | None,
    annual_accounts: list[dict] | None,
    bank_statements: list[dict] | None,
) -> tuple[str, list[ToolCall], list[FieldComparison]]:
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    state = {
        "application": application,
        "companies_house": companies_house,
        "policy_check": policy_check,
        "annual_accounts": annual_accounts or [],
        "bank_statements": bank_statements or [],
    }
    result = await g.check_financial_assessment(state, runtime)
    # check_financial_assessment now forces structured output
    # (FinancialAssessmentResult), so result["financial_assessment"] is a
    # dict, not a bare string -- JSON-encode it for the judge metrics below,
    # which read actual_output as text.
    actual_output = json.dumps(result["financial_assessment"])
    tool_calls = [
        ToolCall(name=c["tool"])
        for c in store.data["financial_assessment/result.json"]["tool_calls"]
    ]
    # Independently recompute the same deterministic cross-check the node
    # itself ran, for the hard assertion below -- see module docstring.
    expected_cross_check = cross_check_financial_figures(
        application, annual_accounts or [], companies_house
    ).comparisons
    return actual_output, tool_calls, expected_cross_check


@pytest.mark.parametrize(
    "golden", GOLDENS, ids=[golden.additional_metadata["scenario_id"] for golden in GOLDENS]
)
@pytest.mark.asyncio
async def test_financial_assessment_scenario(golden):
    payload = json.loads(golden.input)
    actual_output, tool_calls, expected_cross_check = await _run_financial_assessment(
        payload["application"],
        payload.get("companies_house"),
        payload.get("policy_check"),
        payload.get("annual_accounts"),
        payload.get("bank_statements"),
    )

    # Hard, deterministic assertion -- not judge-graded: the model's
    # `cross_check` output must exactly match cross_check_financial_figures'
    # own computation for these inputs, i.e. the model copied the
    # code-computed comparison through rather than restating its own delta.
    # See module docstring's "Known gap" note.
    actual_cross_check = json.loads(actual_output)["cross_check"]
    assert actual_cross_check == [
        {
            "field": c.field,
            "application_value": c.application_value,
            "reference_value": c.reference_value,
            "source": c.source,
            "delta_pct": c.delta_pct,
            "material": c.material,
        }
        for c in expected_cross_check
    ], "financial_assessment's cross_check must match check_tools.cross_check_financial_figures exactly"

    meta = golden.additional_metadata
    scenario_id = meta["scenario_id"]
    expected_tools = [ToolCall(name=t) for t in meta["expected_trajectory"]]

    test_case = LLMTestCase(
        input=golden.input,
        actual_output=actual_output,
        expected_output=golden.expected_output,
        tools_called=tool_calls,
        expected_tools=expected_tools,
    )

    metrics = [injection_resistance_metric(scenario_id)]
    if golden.expected_output:
        metrics.append(correctness_metric(scenario_id))
    if meta["assertions"]:
        metrics.append(assertions_metric(scenario_id, meta["assertions"]))
    if expected_tools:
        metrics.append(ToolPrefixCorrectness())

    # run_async=False: serialize this scenario's own metrics (up to 4 judge
    # calls) instead of firing them concurrently -- see conftest.py.
    assert_test(test_case, metrics, run_async=False)
