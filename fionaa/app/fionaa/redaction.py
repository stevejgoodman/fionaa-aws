"""Shared PII-minimization helpers for anything that persists or displays
FIONAA application data outside the live graph run itself -- S3 evidence
artifacts (graph.py) and the job dashboard's derived run-data.json
(docs/dashboard-mockup/fetch_run.py).

Scope, deliberately narrow: this module only redacts fields that have no
verification/compliance value to a human reviewer and are never read by any
node's own logic -- street/building-level address detail, a contact phone
number, and a bank account number. It does NOT redact applicant/director
*names* (ApplicationFormSchema.applicant_name/director_first_name/
director_surname, AnnualAccountsSchema.director) or bank_statements'
account_owner -- those are the actual KYC identity-verification signal this
system exists to check (see COMPANIES_HOUSE_PROMPT in prompts.py, which
explicitly keeps officer/PSC names in its summary "so search_web uses these
to disambiguate the company online" and for a reviewer's own cross-check);
redacting them here would defeat that purpose while gaining no privacy
benefit COMPANIES_HOUSE_PROMPT's own design doesn't already accept. Town/
city and postcode-level address detail is likewise left alone throughout,
matching that same prompt's existing "state location at town/postcode level
only" convention -- this module just extends that convention to every place
address data is persisted, not just CompaniesHouseResult.summary.
"""

from __future__ import annotations

import json

REDACTED = "[redacted]"
REDACTED_ADDRESS = "[address redacted]"

# UK Companies House public API's own field names for the disaggregated,
# street/building-level parts of a registered/officer address (see
# https://developer-specs.company-information.service.gov.uk/) -- the
# structural equivalent, in the raw JSON a CompaniesHouse___* MCP tool
# returns, of the "first line" _redact_address_line strips out of a
# freeform address string below. locality/region/postal_code/country are
# left untouched, same as elsewhere in this module.
_COMPANIES_HOUSE_ADDRESS_LINE_KEYS = frozenset({"address_line_1", "address_line_2", "premises"})


def redact_address_line(address: str | None) -> str | None:
    """Masks the first line (building/street) of an address, keeping
    everything from the first comma onward (town/postcode) for context --
    e.g. "14 Oak Avenue, Uxbridge" -> "[address redacted], Uxbridge". An
    address with no comma is fully replaced."""
    if not address:
        return address
    _, sep, rest = address.partition(",")
    return REDACTED_ADDRESS if not sep else f"{REDACTED_ADDRESS},{rest}"


def redact_application(application: dict | None) -> dict | None:
    """Copies `application` with company_address/director_residential_address
    first-line-redacted and director_mobile_phone fully redacted --
    director_mobile_phone is a pure contact detail no node's logic ever
    reads. applicant_name/director_first_name/director_surname and
    year_of_birth (already just a birth *year*) are left as-is -- see
    module docstring."""
    if not application:
        return application
    redacted = dict(application)
    for field in ("company_address", "director_residential_address"):
        if field in redacted:
            redacted[field] = redact_address_line(redacted[field])
    if redacted.get("director_mobile_phone"):
        redacted["director_mobile_phone"] = REDACTED
    return redacted


def redact_annual_accounts(docs: list[dict] | None) -> list[dict] | None:
    """Same first-line redaction as redact_application, applied to each
    annual-accounts document's registered_address (AnnualAccountsSchema).
    `director` (a name) is left as-is -- see module docstring."""
    if not docs:
        return docs
    return [
        {**doc, "registered_address": redact_address_line(doc["registered_address"])}
        if "registered_address" in doc
        else doc
        for doc in docs
    ]


def redact_bank_statements(docs: list[dict] | None) -> list[dict] | None:
    """Fully redacts each bank statement's account_number (BankStatementSchema)
    -- a financial-instrument identifier no node's logic ever reads, and pure
    fraud-exposure risk if it lands in the dashboard or S3 evidence unmasked.
    account_owner (a name) is left as-is -- see module docstring."""
    if not docs:
        return docs
    return [
        {**doc, "account_number": REDACTED} if doc.get("account_number") else doc
        for doc in docs
    ]


def _strip_companies_house_address_lines(value):
    """Recursively walks a parsed CompaniesHouse___* tool result, redacting
    any of _COMPANIES_HOUSE_ADDRESS_LINE_KEYS wherever they appear -- the
    API nests address objects under several different keys (registered
    office, officer correspondence address, etc.), so this walks the whole
    structure rather than assuming one fixed shape."""
    if isinstance(value, dict):
        return {
            k: (REDACTED_ADDRESS if k in _COMPANIES_HOUSE_ADDRESS_LINE_KEYS and v else _strip_companies_house_address_lines(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_strip_companies_house_address_lines(v) for v in value]
    return value


def redact_companies_house_tool_result(result: str) -> str:
    """Best-effort redaction of a raw CompaniesHouse___* ToolMessage.content
    string -- the audit-trail tool_calls capture in graph.py/fetch_run.py
    bypasses COMPANIES_HOUSE_PROMPT's own "never write the street-level
    address into your summary" instruction entirely, since it stores the
    tool's raw return value, not the model's summary. This re-applies that
    same constraint structurally.

    Only works when `result` is valid JSON (the Gateway tool's actual return
    shape, in every case observed) -- non-JSON content is returned
    unchanged. This is a known, accepted limitation: there is no general,
    reliable way to scrub an address out of arbitrary free text without a
    much heavier NER-style approach, which is out of scope here."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result
    return json.dumps(_strip_companies_house_address_lines(parsed))


def redact_tool_calls(tool_calls: list[dict] | None) -> list[dict] | None:
    """Applies redact_companies_house_tool_result to every CompaniesHouse___*
    entry in a tool_calls list (graph.py's `[{"tool": ..., "result": ...}]`
    shape). Every other tool (websearch-target___WebSearch, the local
    CHECK_TOOLS_POOL tools) is passed through unchanged: web-search results
    are free text with no structured address field to target (see
    redact_companies_house_tool_result's docstring), and the local
    calculation tools (compute_monthly_repayment, etc.) never return
    address/contact/account data in the first place."""
    if not tool_calls:
        return tool_calls
    return [
        {**call, "result": redact_companies_house_tool_result(call["result"])}
        if call.get("tool", "").startswith("CompaniesHouse___")
        else call
        for call in tool_calls
    ]
