"""Shared setup for tests/test_live_*.py.

Unlike fakes.py (used by the offline unit tests), nothing here is a double —
`real_gateway_tools()` returns actual MCP `StructuredTool` objects wired to
the real AgentCore Gateway, and `run_node` drives the real node functions
against them with the real Bedrock model from graph.py. Only the storage
side is faked (`FakeStore`/`FakePolicyDocs`), since these tests are about
node behaviour against live external data, not S3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel, Field

import gateway
import graph as g

from fakes import FakePolicyDocs, FakeRuntime, FakeStore

# fionaa/agentcore/.env.local — the same file `agentcore dev` loads locally,
# holding real Gateway OAuth config pointed at the deployed dev Gateway.
# tests/conftest.py already claimed these env var names with placeholder
# values via os.environ.setdefault, so they need `override=True` here to
# actually take effect.
_ENV_LOCAL = Path(__file__).resolve().parents[3] / "agentcore" / ".env.local"


def _load_real_gateway_env() -> None:
    if _ENV_LOCAL.exists():
        load_dotenv(_ENV_LOCAL, override=True)


async def real_gateway_tools() -> list:
    """Loads real MCP tools from the AgentCore Gateway, same as main.py does
    per invocation. Skips (rather than fails) the test if real config/creds
    aren't available locally — these tests are opt-in (`--run-live`) but
    still shouldn't hard-fail someone's run just because they haven't set up
    `fionaa/agentcore/.env.local` or an AWS session."""
    _load_real_gateway_env()
    if not _ENV_LOCAL.exists():
        pytest.skip(f"no {_ENV_LOCAL} — copy it locally with real Gateway config to run live tests")
    try:
        return await gateway.load_gateway_tools()
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any live-config problem should skip, not error
        pytest.skip(f"couldn't load real Gateway tools ({exc!r}) — check AWS credentials and .env.local")


def live_agent_context(tools: list) -> g.AgentContext:
    """AgentContext for live node tests: real tools, faked storage (these
    tests assert on node return values, not what lands in S3)."""
    return g.AgentContext(store=FakeStore(), policy_docs=FakePolicyDocs(), tools=tools)


def live_runtime(tools: list) -> FakeRuntime:
    return FakeRuntime(live_agent_context(tools))


# ---------------------------------------------------------------------------
# LLM-judge for search_web's free-text output
# ---------------------------------------------------------------------------
#
# `search_web` (unlike check_companies_house) has no structured
# response_format — it returns free-text prose from the research agent, so
# assertions can't just check a `found` field. Keyword-matching that prose
# ("no results", "couldn't find") is brittle against phrasing changes.
# Instead, a second, narrowly-scoped LLM call classifies the *first* agent's
# output — same model already loaded in graph.py, so no extra config needed.

class _EvidenceVerdict(BaseModel):
    evidence_found: bool = Field(
        description="True only if the text cites concrete evidence (a company "
        "website, a LinkedIn profile/page, news coverage, a registry listing, "
        "etc.) that the company or person genuinely exists online. False if "
        "the text reports it could not find such evidence, or is vague/"
        "speculative with nothing concrete cited."
    )
    reasoning: str = Field(description="One sentence explaining the verdict.")


async def judge_evidence_found(company_name: str, search_result_text: str) -> _EvidenceVerdict:
    """Classifies whether `search_web`'s output actually cites evidence for
    `company_name`, using the same Bedrock model as the graph rather than
    fragile keyword matching against free-text prose."""
    judge = g.model.with_structured_output(_EvidenceVerdict)
    prompt = (
        f"A web-research agent was asked to find evidence online (company "
        f"websites, LinkedIn pages, etc.) for the company '{company_name}'. "
        f"Here is its report:\n\n{search_result_text}\n\n"
        "Does this report actually cite concrete evidence that the company "
        "exists online, or does it report finding none?"
    )
    return await judge.ainvoke(prompt)
