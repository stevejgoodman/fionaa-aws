"""DeepEval web-search node evals -- see README.md and
test_companies_house.py's module docstring for the overall approach.

Same shape as test_companies_house.py: search_web (graph.py:312) only reads
response["messages"][-1].content, discarding the ToolMessages in
response["messages"] -- so the agent call is rebuilt here rather than
calling the node function, purely to keep that list reachable for
ToolPrefixCorrectness.

web-search scenarios have no `expected_response` in the dataset (free-text
research prose has no single correct wording to match against) -- only
`assertions`, so correctness_metric is skipped here whenever
golden.expected_output is unset, same guard test_policy_check.py uses.

`companies_house` findings are passed as None here, same as
run_node_evals.py's run_web_search -- these scenarios test search_web in
isolation, not the full graph's companies_house -> web_search handoff.

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps deepeval test run deepeval_evals/test_web_search.py
"""

from __future__ import annotations

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

GOLDENS = load_goldens(prefix="web-search-")


async def _run_web_search(company_name: str, tools: list) -> tuple[str, list[ToolCall]]:
    agent = create_agent(
        model=g.model,
        tools=g.tools_for(tools, "websearch-target___WebSearch"),
        system_prompt=g.WEB_SEARCH_PROMPT,
    )
    response = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(content=f"Company: {company_name}\n\nCOMPANIES HOUSE FINDINGS:\nnull")
            ]
        }
    )
    actual_output = response["messages"][-1].content
    tool_calls = [ToolCall(name=m.name) for m in response["messages"] if isinstance(m, ToolMessage)]
    return actual_output, tool_calls


@pytest.mark.parametrize(
    "golden", GOLDENS, ids=[golden.additional_metadata["scenario_id"] for golden in GOLDENS]
)
@pytest.mark.asyncio
async def test_web_search_scenario(golden):
    company_name = golden.input.removeprefix("Company: ").strip()
    tools = await real_gateway_tools()
    actual_output, tool_calls = await _run_web_search(company_name, tools)

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
