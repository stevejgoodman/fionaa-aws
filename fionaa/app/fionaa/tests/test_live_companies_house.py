"""Live tests for the `check_companies_house` node (graph.py).

These call the real `check_companies_house` node against the real
AgentCore Gateway — real Companies House data, real Bedrock model — no
fakes on the tool/model side. Only `store`/`policy_docs` are faked (see
live_helpers.py); the node's own S3 write isn't what's under test here.

Opt-in and skipped by default: run with
    pytest --run-live tests/test_live_companies_house.py
after populating fionaa/agentcore/.env.local with real Gateway config and
having AWS credentials able to read the Gateway's client secret (e.g.
`AWS_PROFILE=AIOps`, or whatever profile `agentcore deploy` used).

Test data note: "Goodman's Consulting Limited" / company number 08139267 is
a real, active UK company already used as the smoke-test fixture in
stevetesting.ipynb — safe to keep reusing here. The "not actively trading"
case is left as a documented placeholder (see below) since it needs a real
dissolved/dormant company + a real officer name on file for it, which isn't
something to fabricate — fill it in with a genuine record before relying on
that test.
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
            "company_name": "Goodman's Consulting Limited",
            "company_number": "08139267",
            "applicant_name": "Steve Goodman",
            "registered_address": "Manor Road, Ruislip, Middlesex, England",
        },
        id="goodmans-consulting-exact",
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
            # missing apostrophe, "Ltd" instead of "Limited"
            "company_name": "Goodmans Consulting Ltd",
            "applicant_name": "S Goodman",
            "registered_address": "Manor Rd, Ruislip",
        },
        id="goodmans-consulting-misspelled-no-number",
    ),
    pytest.param(
        {
            # right number, name reordered/truncated
            "company_name": "Goodman Consulting",
            "company_number": "08139267",
            "applicant_name": "Steven Goodman",
        },
        id="goodmans-consulting-truncated-name",
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
# TODO: replace with a genuine dissolved/dormant/liquidated UK company (and,
# if you want the officer-match part of COMPANIES_HOUSE_PROMPT to actually
# exercise, a real officer name from its Companies House filing history).
# Left unpopulated rather than guessed, since asserting behaviour against a
# fabricated "real" company would just be testing a fiction.
# ---------------------------------------------------------------------------

NOT_ACTIVE_REAL_APPLICATIONS = [
    pytest.param(
        {
            "company_name": "REPLACE_ME — real dissolved/dormant company name",
            "company_number": "REPLACE_ME",
            "applicant_name": "REPLACE_ME — a real officer on file",
        },
        id="not-actively-trading-placeholder",
        marks=pytest.mark.skip(reason="fill in a real dissolved/dormant company (see module docstring) before enabling"),
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
