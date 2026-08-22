"""DeepEval financial-assessment node evals -- see README.md and
test_companies_house.py's module docstring for the overall approach.

**No scenarios exist for this node yet.** `fionaa_eval_dataset.jsonl`
currently has no `financial-assessment-*` entries, so
`test_financial_assessment_scenario` collects zero test cases until some are
added -- this file is scaffolding, not a validated harness like
test_companies_house.py/test_policy_check.py/test_web_search.py.

check_financial_assessment (graph.py:268) takes more context than the other
nodes: `application`, plus the *prior* nodes' own results
(`companies_house`, `policy_check`) -- per the cross-node context wiring
added in ab7db18. The other three node dataset prefixes only ever needed
`application` (or a bare company name) as input, so `turns[0].input` there
is just that one JSON value. financial-assessment scenarios need more than
one input value, so -- once scenarios exist -- `turns[0].input` should be a
JSON object of the form:

    {"application": {...}, "companies_house": {...}, "policy_check": "..."}

rather than the bare `application` dict `dataset.py`'s other consumers get.
`_run_financial_assessment` below already expects that shape.

Like check_against_policy, check_financial_assessment captures its own
tool_calls into `store` -- no need to duplicate the agent construction here.

Usage (once scenarios exist):
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
    application: dict, companies_house: dict | None, policy_check: str | None
) -> tuple[str, list[ToolCall]]:
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    state = {
        "application": application,
        "companies_house": companies_house,
        "policy_check": policy_check,
    }
    result = await g.check_financial_assessment(state, runtime)
    actual_output = result["financial_assessment"]
    tool_calls = [
        ToolCall(name=c["tool"])
        for c in store.data["financial_assessment/result.json"]["tool_calls"]
    ]
    return actual_output, tool_calls


@pytest.mark.parametrize(
    "golden", GOLDENS, ids=[golden.additional_metadata["scenario_id"] for golden in GOLDENS]
)
@pytest.mark.asyncio
async def test_financial_assessment_scenario(golden):
    payload = json.loads(golden.input)
    actual_output, tool_calls = await _run_financial_assessment(
        payload["application"], payload.get("companies_house"), payload.get("policy_check")
    )

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

    assert_test(test_case, metrics)
