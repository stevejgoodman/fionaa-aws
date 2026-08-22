"""DeepEval companies-house node evals -- replaces the companies-house slice
of run_node_evals.py's custom judge with DeepEval's GEval + a small
deterministic trajectory metric, runnable via pytest or `deepeval test run`.

Still calls the real Bedrock model + real AgentCore Gateway tools directly,
not through main.py's entrypoint -- fionaa's entrypoint takes
{"application_id": ...} + a JWT and fetches application data from S3, not a
chat message, so there's no dataset-driven payload that can feed it directly
(see run_node_evals.py's docstring and ../EVALS.md for the full reasoning).
That mismatch is the whole reason this harness exists rather than pointing
`agentcore run batch-evaluation --dataset` at the deployed runtime.

Unlike run_node_evals.py, expected_trajectory IS checked here. It isn't that
tool-call info is unavailable outside a real trace -- check_companies_house
in graph.py just never reads it: `agent.ainvoke(...)` returns both
"messages" (with the ToolMessages) and "structured_response", but the node
only reads the latter. Rebuilding the same agent call here and reading
"messages" too gives real tool-call names to check against the dataset's
expected_trajectory, no OTel trace or AgentCore instrumentation required.

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps deepeval test run deepeval_evals/test_companies_house.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# --- module-level env vars graph.py's imports (storage.py/security.py) read
# at import time -- same placeholders run_node_evals.py uses; real values
# aren't needed since only the model + real Gateway tools are exercised here.
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
from langchain.agents import create_agent  # noqa: E402
from langchain.messages import HumanMessage, ToolMessage  # noqa: E402
from deepeval import assert_test  # noqa: E402
from deepeval.test_case import LLMTestCase, ToolCall  # noqa: E402

import graph as g  # noqa: E402
from live_helpers import real_gateway_tools  # noqa: E402

from dataset import load_goldens  # noqa: E402
from metrics import (  # noqa: E402
    ToolPrefixCorrectness,
    assertions_metric,
    correctness_metric,
    injection_resistance_metric,
)

GOLDENS = load_goldens(prefix="companies-house-")


async def _run_companies_house(application: dict, tools: list) -> tuple[str, list[ToolCall]]:
    """Same agent construction/invocation as graph.check_companies_house,
    duplicated here (rather than calling the node function) only so
    response["messages"] -- and the ToolMessages in it -- stays reachable.
    The node itself discards that list once it reads structured_response."""
    agent = create_agent(
        model=g.model,
        tools=g.tools_for(tools, "CompaniesHouse___", "geo-target___CheckSameArea"),
        system_prompt=g.COMPANIES_HOUSE_PROMPT,
        response_format=g.CompaniesHouseResult,
    )
    response = await agent.ainvoke({"messages": [HumanMessage(content=json.dumps(application))]})
    result = response["structured_response"].model_dump()
    tool_calls = [ToolCall(name=m.name) for m in response["messages"] if isinstance(m, ToolMessage)]
    return json.dumps(result), tool_calls


@pytest.mark.parametrize(
    "golden", GOLDENS, ids=[golden.additional_metadata["scenario_id"] for golden in GOLDENS]
)
@pytest.mark.asyncio
async def test_companies_house_scenario(golden):
    application = json.loads(golden.input)
    tools = await real_gateway_tools()
    actual_output, tool_calls = await _run_companies_house(application, tools)

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

    metrics = [correctness_metric(scenario_id), injection_resistance_metric(scenario_id)]
    if meta["assertions"]:
        metrics.append(assertions_metric(scenario_id, meta["assertions"]))
    if expected_tools:
        metrics.append(ToolPrefixCorrectness())

    # run_async=False: serialize this scenario's own metrics (up to 4 judge
    # calls) instead of firing them concurrently -- see conftest.py.
    assert_test(test_case, metrics, run_async=False)
