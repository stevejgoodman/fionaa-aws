"""Direct, no-agent test of the `websearch-target___WebSearch` MCP tool.

Every other live web-search test (test_live_web_search.py) goes through the
`search_web` node — a full agent loop with `WEB_SEARCH_PROMPT`, which decides
its own query text and how many times to call the tool. That's the right
thing to test end-to-end, but it conflates two very different possible
failure modes when a real company's web presence goes unfound:

  1. The underlying `websearch-target___WebSearch` Gateway tool itself
     returns nothing useful for a reasonable query (a problem in the
     borrowed, out-of-repo Gateway target -- see cdk-stack.ts, which points
     this tool at a different team's "ClaimsAgent" stack).
  2. The tool is fine, but the agent's query construction/prompt causes it
     to search badly (e.g. a bare company name query with no disambiguator).

This module isolates (1): it calls the MCP tool directly with a hand-built
query built from known facts (company name, director/applicant name,
registered address) -- no LLM, no prompt, no query-formulation guesswork --
and asserts the raw tool result contains the real-world evidence we already
know exists (goodaiconsulting.co.uk is Google's #1 result for "GoodAI
Consulting", and the company has a real LinkedIn company page). If this
still comes back empty, the fault is in the tool/Gateway, not the prompt.

Opt-in and skipped by default: run with
    pytest --run-live tests/test_live_web_search_tool_direct.py
after populating fionaa/agentcore/.env.local with real Gateway config.
"""

import json

import pytest

from fionaa.testing.live_helpers import real_gateway_tools
from fionaa.workflow.web_search import WEBSEARCH_TOOL_NAME

pytestmark = pytest.mark.live


async def _get_websearch_tool():
    tools = await real_gateway_tools()
    for tool in tools:
        if tool.name == WEBSEARCH_TOOL_NAME:
            return tool
    pytest.fail(
        f"{WEBSEARCH_TOOL_NAME!r} not found among Gateway tools: "
        f"{[t.name for t in tools]}"
    )


def _tool_args_summary(tool) -> str:
    """Best-effort description of the tool's input schema, for failure
    messages -- the schema lives on the borrowed Gateway, not in this repo,
    so we don't know it ahead of time."""
    schema = getattr(tool, "args_schema", None)
    try:
        if schema is not None:
            model_json = getattr(schema, "model_json_schema", None)
            if callable(model_json):
                return json.dumps(model_json(), indent=2)
        return json.dumps(getattr(tool, "args", {}), indent=2)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return f"<couldn't introspect schema: {exc!r}>"


async def _invoke_with_query(tool, query: str) -> str:
    """Calls the raw MCP tool with `query`, trying the couple of plausible
    input-field names a WebSearch-shaped tool might use. Fails loudly with
    the tool's schema if none of them are accepted, rather than silently
    passing a malformed call through."""
    last_exc = None
    for field in ("query", "q", "search_query", "searchQuery"):
        try:
            return await tool.ainvoke({field: query})
        except Exception as exc:  # noqa: BLE001 - trying multiple field names
            last_exc = exc
    pytest.fail(
        f"couldn't invoke {tool.name!r} with query={query!r} under any of "
        f"the field names tried; last error: {last_exc!r}\n\n"
        f"tool schema:\n{_tool_args_summary(tool)}"
    )


# ---------------------------------------------------------------------------
# GoodAI Consulting (17161121) -- real, active company. Known facts used to
# build the query below, exactly as a competent researcher would: company
# name, applicant/director name, registered address. See
# test_live_companies_house.py's REAL_ACTIVE_APPLICATIONS for the same facts
# used against the Companies House tool.
# ---------------------------------------------------------------------------

GOODAI_QUERY = '"GoodAI Consulting" "Steve Goodman" Ruislip'

# Lower-cased substrings we expect a competent search backend to surface
# somewhere in the raw result for a disambiguated query like the above --
# these are real, independently-verifiable facts (the site ranks #1 on
# Google for "GoodAI Consulting"; the LinkedIn company page exists at
# linkedin.com/company/111329467), not assumptions being tested for the
# first time here.
EXPECTED_EVIDENCE_SUBSTRINGS = [
    "goodaiconsulting.co.uk",
    "linkedin.com/company/111329467",
]


async def test_raw_tool_finds_goodai_consulting_website_or_linkedin():
    tool = await _get_websearch_tool()
    raw_result = await _invoke_with_query(tool, GOODAI_QUERY)
    result_text = str(raw_result).lower()

    found = [s for s in EXPECTED_EVIDENCE_SUBSTRINGS if s in result_text]
    assert found, (
        f"raw {WEBSEARCH_TOOL_NAME!r} result for query {GOODAI_QUERY!r} "
        f"contained none of {EXPECTED_EVIDENCE_SUBSTRINGS} -- the tool "
        f"itself is failing to surface known-real results, independent of "
        f"any agent/prompt behaviour.\n\nraw result:\n{raw_result!r}"
    )


async def test_raw_tool_finds_something_at_all_for_goodai_consulting():
    """A weaker sanity check that still isolates the tool from the agent:
    even if the exact domain/LinkedIn URL isn't echoed back verbatim (some
    search APIs return titles/snippets without the bare URL), the raw
    result shouldn't be empty for a real, disambiguated company query."""
    tool = await _get_websearch_tool()
    raw_result = await _invoke_with_query(tool, GOODAI_QUERY)

    assert raw_result, f"raw {WEBSEARCH_TOOL_NAME!r} returned nothing at all for {GOODAI_QUERY!r}"
    result_text = str(raw_result).strip()
    assert result_text and result_text.lower() not in ("[]", "none", "null", "no results", "no results found"), (
        f"raw {WEBSEARCH_TOOL_NAME!r} returned an effectively-empty result "
        f"for {GOODAI_QUERY!r}: {raw_result!r}"
    )
