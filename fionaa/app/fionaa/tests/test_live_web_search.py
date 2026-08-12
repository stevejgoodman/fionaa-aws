"""Live tests for the `search_web` node (graph.py).

Calls the real `search_web` node against the real AgentCore Gateway's
websearch tool and the real Bedrock model — no fakes on the tool/model side
(only `store`/`policy_docs`, see live_helpers.py).

`search_web` returns free-text prose rather than a structured result, so
these tests use an LLM-judge (`judge_evidence_found`) instead of brittle
keyword matching — see live_helpers.py for why.

Opt-in and skipped by default: run with
    pytest --run-live tests/test_live_web_search.py
after populating fionaa/agentcore/.env.local with real Gateway config and
having AWS credentials able to read the Gateway's client secret.
"""

import pytest

import graph as g

from live_helpers import judge_evidence_found, live_runtime, real_gateway_tools

pytestmark = pytest.mark.live


async def _run(company_name: str) -> str:
    tools = await real_gateway_tools()
    runtime = live_runtime(tools)
    result = await g.search_web({"application": {"company_name": company_name}}, runtime)
    return result["web_search"]


# ---------------------------------------------------------------------------
# 1. Real, well-known company — should turn up a company website, LinkedIn
#    page, or similar. A large well-known retailer is used rather than the
#    small consultancy used for the Companies House tests, since a personal
#    consultancy's web footprint is genuinely too thin to reliably assert on
#    — a null result there wouldn't distinguish a bug from a real absence.
# ---------------------------------------------------------------------------

REAL_COMPANIES_WITH_WEB_PRESENCE = [
    "Tesco PLC",
    "Sainsbury's",
]


@pytest.mark.parametrize("company_name", REAL_COMPANIES_WITH_WEB_PRESENCE)
async def test_finds_evidence_for_real_company(company_name):
    result_text = await _run(company_name)

    verdict = await judge_evidence_found(company_name, result_text)

    assert verdict.evidence_found, (
        f"expected evidence of '{company_name}' online, judge said no "
        f"({verdict.reasoning!r}); raw result: {result_text!r}"
    )


# ---------------------------------------------------------------------------
# 2. Fictitious company — should turn up nothing, and the report should say
#    so rather than fabricating a plausible-sounding hit.
# ---------------------------------------------------------------------------

FICTITIOUS_COMPANIES = [
    "Quixotic Purple Yak Consulting Limited",
    "Xzqvorn Zibberflux Trading Co (UK) Ltd",
]


@pytest.mark.parametrize("company_name", FICTITIOUS_COMPANIES)
async def test_finds_no_evidence_for_fictitious_company(company_name):
    result_text = await _run(company_name)

    verdict = await judge_evidence_found(company_name, result_text)

    assert not verdict.evidence_found, (
        f"expected no evidence for the fictitious company '{company_name}', "
        f"judge said evidence was found ({verdict.reasoning!r}); "
        f"raw result: {result_text!r}"
    )
