"""Calibration for the judge-model change in metrics.py.

metrics.py used to score every GEval metric with the same Sonnet 4.5 model
the agent itself runs on (self-judging). It now defaults to Claude Haiku 4.5
for correctness_metric/assertions_metric and Amazon Nova Lite for
injection_resistance_metric -- cheaper, and independent of the agent model
for the injection check specifically (see metrics.py's module docstring for
the full reasoning).

This script re-scores each dataset scenario's already-generated actual_output
with BOTH the new default judge and the old Sonnet judge (metrics.py's
_LEGACY_SONNET_JUDGE_MODEL), side by side, and reports where they disagree on
pass/fail -- so the cheaper judges can be trusted (or not) based on evidence
against this project's own goldens, not just the general "judging is easier
than generating" argument.

Deliberately NOT a pytest file (no test_ prefix) -- this is a one-off/
periodic calibration report to read, not a gate. It reuses the agent-
invocation helpers from test_companies_house.py/test_policy_check.py/
test_web_search.py so it doesn't re-implement the agent call, and pays for
that call only once per scenario (both judges score the same actual_output).
financial_assessment is skipped -- its dataset has zero scenarios currently
(see deepeval-ci.yml), so there's nothing to calibrate against yet.

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps ../app/fionaa/.venv/bin/python deepeval_evals/calibrate_judges.py

Costs real Bedrock calls: one live agent run per scenario (Sonnet, same as
the real suite), plus two judge calls per GEval metric per scenario (old +
new). Run it deliberately, not as part of every CI run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
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

from deepeval.metrics import GEval  # noqa: E402
from deepeval.test_case import LLMTestCase, ToolCall  # noqa: E402

from live_helpers import real_gateway_tools  # noqa: E402
from fakes import FakePolicyDocs, FakeRuntime, FakeStore  # noqa: E402
import graph as g  # noqa: E402

from metrics import (  # noqa: E402
    ToolPrefixCorrectness,
    _LEGACY_SONNET_JUDGE_MODEL,
    assertions_metric,
    correctness_metric,
    injection_resistance_metric,
)

# Import the test modules for their _run_* helpers and GOLDENS -- module-
# level code only sets env var defaults and loads goldens, it doesn't run
# any pytest test, so importing is safe here.
import test_companies_house as tch  # noqa: E402
import test_policy_check as tpc  # noqa: E402
import test_web_search as tws  # noqa: E402


@dataclass
class MetricComparison:
    scenario_id: str
    domain: str
    metric_name: str
    old_score: float | None
    new_score: float | None
    old_pass: bool | None
    new_pass: bool | None
    old_reason: str = ""
    new_reason: str = ""

    @property
    def agree(self) -> bool:
        return self.old_pass == self.new_pass


@dataclass
class DomainResult:
    comparisons: list[MetricComparison] = field(default_factory=list)


async def _score(metric: GEval, test_case: LLMTestCase) -> tuple[float, bool, str]:
    await metric.a_measure(test_case)
    return metric.score, metric.is_successful(), metric.reason or ""


async def _compare_metric(
    scenario_id: str,
    domain: str,
    metric_name: str,
    old_metric: GEval,
    new_metric: GEval,
    test_case: LLMTestCase,
) -> MetricComparison:
    old_score, old_pass, old_reason = await _score(old_metric, test_case)
    new_score, new_pass, new_reason = await _score(new_metric, test_case)
    return MetricComparison(
        scenario_id=scenario_id,
        domain=domain,
        metric_name=metric_name,
        old_score=old_score,
        new_score=new_score,
        old_pass=old_pass,
        new_pass=new_pass,
        old_reason=old_reason,
        new_reason=new_reason,
    )


async def _compare_scenario(
    domain: str,
    scenario_id: str,
    meta: dict,
    test_case: LLMTestCase,
    assertions_context: list[str] | None = None,
) -> list[MetricComparison]:
    """Builds the same metric set each test_*.py file would (always
    injection_resistance; correctness/assertions/ToolPrefixCorrectness
    conditionally), scored once under the legacy Sonnet judge and once
    under the new default judge.

    `assertions_context` mirrors test_policy_check.py's policy_text --
    policy_check is the only domain that currently has a ground-truth
    document to check citations against, so this is None (no CONTEXT param
    added) for the other two domains."""
    comparisons = []

    comparisons.append(
        await _compare_metric(
            scenario_id,
            domain,
            "injection_resistance",
            injection_resistance_metric(scenario_id, model=_LEGACY_SONNET_JUDGE_MODEL),
            injection_resistance_metric(scenario_id),
            test_case,
        )
    )

    if test_case.expected_output:
        comparisons.append(
            await _compare_metric(
                scenario_id,
                domain,
                "correctness",
                correctness_metric(scenario_id, model=_LEGACY_SONNET_JUDGE_MODEL),
                correctness_metric(scenario_id),
                test_case,
            )
        )

    assertions = meta.get("assertions")
    if assertions:
        comparisons.append(
            await _compare_metric(
                scenario_id,
                domain,
                "assertions",
                assertions_metric(
                    scenario_id,
                    assertions,
                    context=assertions_context,
                    model=_LEGACY_SONNET_JUDGE_MODEL,
                ),
                assertions_metric(scenario_id, assertions, context=assertions_context),
                test_case,
            )
        )

    # ToolPrefixCorrectness is deterministic (no judge model at all) --
    # nothing to calibrate, deliberately skipped here.

    return comparisons


async def _run_companies_house_domain() -> list[MetricComparison]:
    if not tch.GOLDENS:
        return []
    tools = await real_gateway_tools()
    out = []
    for golden in tch.GOLDENS:
        application = json.loads(golden.input)
        actual_output, tool_calls = await tch._run_companies_house(application, tools)
        meta = golden.additional_metadata
        test_case = LLMTestCase(
            input=golden.input,
            actual_output=actual_output,
            expected_output=golden.expected_output,
            tools_called=tool_calls,
            expected_tools=[ToolCall(name=t) for t in meta["expected_trajectory"]],
        )
        out += await _compare_scenario("companies_house", meta["scenario_id"], meta, test_case)
    return out


async def _run_policy_check_domain() -> list[MetricComparison]:
    if not tpc.GOLDENS:
        return []
    out = []
    for golden in tpc.GOLDENS:
        application = json.loads(golden.input)
        actual_output, tool_calls = await tpc._run_policy_check(application)
        # Same policy text check_against_policy itself loaded (graph.py:173)
        # -- see test_policy_check.py's matching comment.
        policy_text = g.load_policy_text(g.LoanType(application["loan_type"]))
        meta = golden.additional_metadata
        test_case = LLMTestCase(
            input=golden.input,
            actual_output=actual_output,
            expected_output=golden.expected_output,
            context=[policy_text],
            tools_called=tool_calls,
            expected_tools=[ToolCall(name=t) for t in meta["expected_trajectory"]],
        )
        out += await _compare_scenario(
            "policy_check", meta["scenario_id"], meta, test_case, assertions_context=[policy_text]
        )
    return out


async def _run_web_search_domain() -> list[MetricComparison]:
    if not tws.GOLDENS:
        return []
    tools = await real_gateway_tools()
    out = []
    for golden in tws.GOLDENS:
        company_name = golden.input.removeprefix("Company: ").strip()
        actual_output, tool_calls = await tws._run_web_search(company_name, tools)
        meta = golden.additional_metadata
        test_case = LLMTestCase(
            input=golden.input,
            actual_output=actual_output,
            expected_output=golden.expected_output,
            tools_called=tool_calls,
            expected_tools=[ToolCall(name=t) for t in meta["expected_trajectory"]],
        )
        out += await _compare_scenario("web_search", meta["scenario_id"], meta, test_case)
    return out


def _print_report(comparisons: list[MetricComparison]) -> int:
    """Prints a comparison table and returns the count of disagreements."""
    disagreements = [c for c in comparisons if not c.agree]

    header = f"{'domain':<16}{'scenario':<40}{'metric':<20}{'old':>6}{'new':>6}  agree"
    print(header)
    print("-" * len(header))
    for c in comparisons:
        old_s = f"{c.old_score:.2f}" if c.old_score is not None else "err"
        new_s = f"{c.new_score:.2f}" if c.new_score is not None else "err"
        flag = "OK" if c.agree else "!! DISAGREE"
        print(
            f"{c.domain:<16}{c.scenario_id:<40}{c.metric_name:<20}"
            f"{old_s:>6}{new_s:>6}  {flag}"
        )

    print()
    print(f"{len(comparisons)} metric comparisons, {len(disagreements)} pass/fail disagreements")
    if disagreements:
        print()
        print("Disagreements in detail:")
        for c in disagreements:
            print(f"\n[{c.domain}/{c.scenario_id}] {c.metric_name}")
            print(f"  legacy Sonnet judge -> pass={c.old_pass} score={c.old_score:.2f}")
            print(f"    reason: {c.old_reason}")
            print(f"  new judge          -> pass={c.new_pass} score={c.new_score:.2f}")
            print(f"    reason: {c.new_reason}")
    return len(disagreements)


async def main() -> int:
    all_comparisons: list[MetricComparison] = []
    for domain_runner in (
        _run_companies_house_domain,
        _run_policy_check_domain,
        _run_web_search_domain,
    ):
        all_comparisons += await domain_runner()

    if not all_comparisons:
        print("No scenarios found -- check dataset/goldens are loadable.")
        return 1

    disagreement_count = _print_report(all_comparisons)
    return 1 if disagreement_count else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
