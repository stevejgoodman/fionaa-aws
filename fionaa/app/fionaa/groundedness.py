"""Runtime groundedness checks for MCP tool outputs.

The `injection_resistance`/`companies_house_correctness` evaluators
(fionaa/agentcore/evaluators/) catch a model asserting a fact its own tool
calls don't support -- but only offline, in CI, against a handful of
dataset scenarios. `graph.py`'s `tool_calls` capture (see `redaction.py`)
records what each tool actually returned, but nothing reads it back to
check the model's claim against it -- it's an audit trail, not a check.
This module closes that gap for the one place it matters most:
`check_companies_house`'s `found` field gates the entire downstream graph
(financial_assessment/web_search/synthesize_decision all only run when
`found=True`), so a fabricated match here is the single highest-leverage
hallucination this agent could produce -- letting an unverified company
through, not just describing it wrong in a narrative field.

Deliberately narrow and asymmetric, same spirit as check_tools.py's
deterministic checks: only `found=True` is checked (a false positive here
is the dangerous direction -- an unverified application proceeding as if
verified). A `found=False` claim is never overridden to `True` -- this
module only ever downgrades, never upgrades, a confidence claim, since
automatically approving a company on a heuristic would be the wrong
failure mode to introduce. And the company_number check only fires when
both the application and at least one tool result actually expose a
company_number to compare -- fuzzy name-only matches (the exact case
several eval scenarios in fionaa_eval_dataset.jsonl exist to test, e.g.
"Good AI Consulting Limited" vs registered "GoodAI Consulting") are left
to the model's own judgment, not second-guessed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GroundingResult:
    grounded: bool
    reasons: list[str] = field(default_factory=list)


def _find_all_string_values(value, key: str) -> list[str]:
    """Recursively collects every string value found under `key` anywhere in
    a parsed JSON structure -- deliberately doesn't assume one fixed shape,
    since different CompaniesHouse___* tools (search vs. profile vs.
    officers) nest `company_number` at different depths."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key and isinstance(v, str):
                found.append(v)
            found.extend(_find_all_string_values(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_all_string_values(item, key))
    return found


def _normalize_company_number(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def check_companies_house_grounding(
    application: dict,
    found: bool,
    tool_calls: list[dict],
) -> GroundingResult:
    """Checks whether a `found=True` claim from check_companies_house is
    actually supported by the CompaniesHouse___* tool calls made along the
    way, rather than trusting the model's own summary. `tool_calls` is
    graph.py's `[{"tool": ..., "result": ...}]` shape (pre- or
    post-redaction -- redact_tool_calls only touches address-line fields,
    never company_number, so either is fine to pass here).

    Two checks, both narrow enough to have no false-positive risk:
    1. found=True with zero CompaniesHouse___* tool calls at all -- the
       model asserted a match with no supporting evidence whatsoever.
    2. found=True where the application states a company_number AND at
       least one tool result exposes a company_number, but none of the
       returned numbers match the application's -- the model matched a
       different company than the one it was asked to verify.

    found=False is never checked (see module docstring) -- this function
    always returns grounded=True for it."""
    if not found:
        return GroundingResult(grounded=True)

    companies_house_calls = [
        call for call in tool_calls if call.get("tool", "").startswith("CompaniesHouse___")
    ]
    if not companies_house_calls:
        return GroundingResult(
            grounded=False,
            reasons=["found=True but no CompaniesHouse tool call evidence exists"],
        )

    application_number = application.get("company_number")
    if application_number:
        returned_numbers = set()
        for call in companies_house_calls:
            try:
                parsed = json.loads(call["result"])
            except (json.JSONDecodeError, TypeError):
                continue
            returned_numbers.update(
                _normalize_company_number(n) for n in _find_all_string_values(parsed, "company_number")
            )
        if returned_numbers and _normalize_company_number(application_number) not in returned_numbers:
            return GroundingResult(
                grounded=False,
                reasons=[
                    f"found=True but no CompaniesHouse tool result returned company_number "
                    f"matching the application's stated {application_number!r} "
                    f"(tool results returned {sorted(returned_numbers)!r})"
                ],
            )

    return GroundingResult(grounded=True)
