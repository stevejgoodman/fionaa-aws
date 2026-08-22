"""DeepEval policy-check node evals -- see README.md and
test_companies_house.py's module docstring for the overall approach (real
Bedrock model, real node functions, faked S3 storage -- fionaa's actual
entrypoint takes {"application_id": ...} + a JWT, not a chat message, so
nothing dataset-driven can feed it directly).

Unlike companies_house/web_search, check_against_policy (graph.py:166)
already captures tool_calls itself -- into `store` alongside the result --
so there's no need to duplicate the agent construction here just to expose
`response["messages"]`; the node function is called directly and its own
store write is read back for tool-call evidence.

Policy-check scenarios currently have no `expected_trajectory` in the
dataset (most loan types declare no CHECK_TOOLS_POOL tools in their
policy.md -- see policy_loader.load_check_tool_names), so
ToolPrefixCorrectness only applies for scenarios that do declare one, same
as the other node test files.

Known gap: the invoice-factoring scenario's assertion about an *exact*
calculated_advance figure (round(invoices_owed * 0.80)) is checked here via
assertions_metric's GEval judge reading it out of the response prose, not a
deterministic comparison against graph state -- an LLM judge is a soft check
for what should be an exact arithmetic assertion. A follow-up deterministic
metric (reading store.data["policy_check/result.json"] or wherever the
figure lands) would be a firmer check than a text-based judge call.

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps deepeval test run deepeval_evals/test_policy_check.py
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

GOLDENS = load_goldens(prefix="policy-check-")


async def _run_policy_check(application: dict) -> tuple[str, list[ToolCall]]:
    store = FakeStore()
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))
    result = await g.check_against_policy({"application": application}, runtime)
    actual_output = result["policy_check"]
    tool_calls = [
        ToolCall(name=c["tool"]) for c in store.data["policy_check/result.json"]["tool_calls"]
    ]
    return actual_output, tool_calls


@pytest.mark.parametrize(
    "golden", GOLDENS, ids=[golden.additional_metadata["scenario_id"] for golden in GOLDENS]
)
@pytest.mark.asyncio
async def test_policy_check_scenario(golden):
    application = json.loads(golden.input)
    actual_output, tool_calls = await _run_policy_check(application)

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
