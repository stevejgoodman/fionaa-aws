"""Live tests for the `check_companies_house` node (graph.py).

These call the real `check_companies_house` node against the real
AgentCore Gateway — real Companies House data, real Bedrock model — no
fakes on the tool/model side. Only `store`/`policy_docs` are faked (see
live_helpers.py); the node's own S3 write isn't what's under test here.

Opt-in and skipped by default: run with
    pytest --run-live tests/test_live_companies_house.py
after populating fionaa/agentcore/.env.local with real Gateway config
"""

import pytest

import graph as g

from live_helpers import live_runtime, real_gateway_tools

pytestmark = pytest.mark.live


async def _run(application: dict):
    tools = await real_gateway_tools()
    runtime = live_runtime(tools)
    return await g.check_companies_house({"application": application}, runtime)


# ---------------------------------------------------------------------------
# 1. Real, active company + correct details — should be found.
# ---------------------------------------------------------------------------

REAL_ACTIVE_APPLICATIONS = [
    pytest.param(
        {
            "company_name": "GoodAI Consulting",
            "company_number": "17161121",
            "applicant_name": "Steve Goodman",
            "registered_address": "Manor Road, Ruislip",
        },
        id="goodai-consulting-exact",
    ),
]


@pytest.mark.parametrize("application", REAL_ACTIVE_APPLICATIONS)
async def test_finds_real_company_with_correct_details(application):
    result = await _run(application)

    assert result.update["companies_house_found"] is True
    assert result.goto == "web_search"
    assert result.update["companies_house"]["confidence"] in {"high", "medium"}


# ---------------------------------------------------------------------------
# 2. Real company, but minor mistakes in the input — misspellings, missing
#    company number, abbreviated/loosely-formatted address. Tests whether
#    the agent is robust to noisy input rather than doing an exact lookup.
# ---------------------------------------------------------------------------

FUZZY_REAL_APPLICATIONS = [
    pytest.param(
        {
            # casing/spacing variant of "GoodAI", no company number, minor
            # applicant name shortening ("Steve" -> "Stephen"), minor
            # street-name typo (Drive vs Road). Company/address noise is
            # tolerated fine; known to currently fail on the applicant name
            # ("Stephen Goodman" vs the officer on file, "Steve Goodman") —
            # matching logic needs to tolerate this too.
            "company_name": "Good ai Consulting",
            "applicant_name": "Stephen Goodman",
            "registered_address": "Manor Drive Ruislip",
        },
        id="goodai-consulting-casing-no-number",
    ),
    pytest.param(
        {
            # right number and applicant, but address given as "London"
            # rather than "Ruislip" — Ruislip is part of Greater London, so
            # this is the same place phrased more loosely, not a wrong
            # address. Requires the geo-target___CheckSameArea Gateway tool
            # (see agentcore/lambda/geo_area_match/) to be attached and the
            # COMPANIES_HOUSE_PROMPT update in prompts.py — without both,
            # this still fails.
            "company_name": "GoodAI Consulting",
            "company_number": "17161121",
            "applicant_name": "Steve Goodman",
            "registered_address": "Manor Road London",
        },
        id="goodai-consulting-london-vs-ruislip",
    ),
]


@pytest.mark.parametrize("application", FUZZY_REAL_APPLICATIONS)
async def test_finds_real_company_despite_minor_input_mistakes(application):
    result = await _run(application)

    assert result.update["companies_house_found"] is True, (
        f"expected the agent to tolerate the input noise and still find the "
        f"company; got: {result.update['companies_house']}"
    )
    assert result.goto == "web_search"


# ---------------------------------------------------------------------------
# 3. Real company, but not actively trading — should still be `found`, with
#    the summary/confidence reflecting that it isn't active.
#
# Goodman's Consulting Limited (08139267) — dissolved 28 December 2017, per
# Companies House. Was originally used in REAL_ACTIVE_APPLICATIONS/
# FUZZY_REAL_APPLICATIONS above under the assumption it was still active;
# moved here once a live run surfaced the dissolution. Officer name below is
# real, taken from that filing history (director appointed 12 July 2012).
# ---------------------------------------------------------------------------

NOT_ACTIVE_REAL_APPLICATIONS = [
    pytest.param(
        {
            "company_name": "Goodman's Consulting Limited",
            "company_number": "08139267",
            "applicant_name": "Steven Goodman",
        },
        id="goodmans-consulting-dissolved",
    ),
]


@pytest.mark.parametrize("application", NOT_ACTIVE_REAL_APPLICATIONS)
async def test_finds_real_company_but_flags_not_active(application):
    result = await _run(application)

    assert result.update["companies_house_found"] is True
    summary = result.update["companies_house"]["summary"].lower()
    assert any(word in summary for word in ("dissolved", "inactive", "not active", "liquidat", "dormant")), (
        f"expected the summary to flag the company as not active, got: {summary!r}"
    )


# ---------------------------------------------------------------------------
# 4. Fictitious company / applicant — should come back not found.
# ---------------------------------------------------------------------------

FICTITIOUS_APPLICATIONS = [
    pytest.param(
        {
            "company_name": "Zorbex Quantum Widgets Limited",
            "company_number": "00000001",
            "applicant_name": "Aldous Fictionwright",
            "registered_address": "1 Imaginary Lane, Nowhereshire",
        },
        id="fictitious-company-and-address",
    ),
    pytest.param(
        {
            "company_name": "Nonexistent Trading Co (UK) Ltd",
            "applicant_name": "Nobody Real",
        },
        id="fictitious-company-no-number",
    ),
]


@pytest.mark.parametrize("application", FICTITIOUS_APPLICATIONS)
async def test_does_not_find_fictitious_company(application):
    result = await _run(application)

    assert result.update["companies_house_found"] is False, (
        f"expected no match for a fictitious company, got: {result.update['companies_house']}"
    )
    assert result.goto == "reject_no_company"
