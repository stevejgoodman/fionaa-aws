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
import re

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
    fraud-exposure risk if it lands in the dashboard or S3 evidence unmasked --
    and first-line-redacts address, same as redact_application does for
    company_address/director_residential_address. account_owner (a name) is
    left as-is -- see module docstring."""
    if not docs:
        return docs
    redacted_docs = []
    for doc in docs:
        redacted = dict(doc)
        if redacted.get("account_number"):
            redacted["account_number"] = REDACTED
        if "address" in redacted:
            redacted["address"] = redact_address_line(redacted["address"])
        redacted_docs.append(redacted)
    return redacted_docs


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


def redact_companies_house_tool_result(result):
    """Best-effort redaction of a raw CompaniesHouse___* ToolMessage.content
    -- the audit-trail tool_calls capture in graph.py/fetch_run.py bypasses
    COMPANIES_HOUSE_PROMPT's own "never write the street-level address into
    your summary" instruction entirely, since it stores the tool's raw
    return value, not the model's summary. This re-applies that same
    constraint structurally.

    Handles two shapes seen in practice: a bare JSON string (the Gateway
    tool's documented return shape) and a list of LangChain content blocks
    (`[{"type": "text", "text": "<json>"}]`, the shape a ToolMessage's own
    .content sometimes takes instead) -- each block's own "text" string is
    redacted the same way, recursively. Only works when the string found is
    valid JSON -- non-JSON content, or any other shape, is returned
    unchanged. This is a known, accepted limitation: there is no general,
    reliable way to scrub an address out of arbitrary free text without a
    much heavier NER-style approach, which is out of scope here."""
    if isinstance(result, list):
        return [
            {**block, "text": redact_companies_house_tool_result(block["text"])}
            if isinstance(block, dict) and isinstance(block.get("text"), str)
            else block
            for block in result
        ]
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result
    return json.dumps(_strip_companies_house_address_lines(parsed))


def address_first_line(address: str | None) -> str | None:
    """The building/street portion of an address string (before the first
    comma, if any) -- the same portion redact_address_line masks. An
    address with no comma has no town/postcode to separate out, so the
    whole string is treated as the sensitive part, same as
    redact_address_line's own no-comma behavior."""
    if not address:
        return None
    first, _, _ = address.partition(",")
    return first.strip() or None


def sensitive_prose_values(application: dict | None, annual_accounts: list[dict] | None,
                            bank_statements: list[dict] | None) -> list[str]:
    """Raw values that must never appear verbatim in free-text LLM prose
    (policy_check's clause_findings/documentation_gaps/summary,
    financial_assessment's discrepancies, the AI recommendation's
    reason/rationale, etc). Unlike the structured-field redaction above,
    these can't be fixed by editing one known field -- an LLM narrating its
    own reasoning can and does quote them verbatim inside a sentence (e.g.
    "Steven Goodman born 1972..." or "Bank statements (3 Manor Road,
    Ruislip)..."), so every occurrence has to be scrubbed out of whatever
    text it landed in. Building/street address lines and the applicant's
    birth year are the two values this covers -- see redact_prose_values."""
    values = set()
    application = application or {}
    for field in ("company_address", "director_residential_address"):
        line = address_first_line(application.get(field))
        if line:
            values.add(line)
    if application.get("year_of_birth"):
        values.add(str(application["year_of_birth"]))
    for doc in annual_accounts or []:
        line = address_first_line(doc.get("registered_address"))
        if line:
            values.add(line)
    for doc in bank_statements or []:
        line = address_first_line(doc.get("address"))
        if line:
            values.add(line)
    # Guard against scrubbing something too short/generic to be meaningfully
    # identifying on its own (e.g. a stray one- or two-character fragment).
    return [value for value in values if len(value) >= 3]


def redact_prose_values(value, sensitive_values: list[str]):
    """Recursively walks value (str/dict/list/anything else), replacing
    every case-insensitive occurrence of each sensitive_values entry found
    inside any string -- the free-text counterpart to
    redact_application/redact_annual_accounts/redact_bank_statements above,
    for the LLM-authored prose fields those don't touch. A purely-numeric
    sensitive value (e.g. a birth year) is only matched on a word boundary,
    so it can't clip a digit out of an unrelated larger number."""
    if isinstance(value, str):
        redacted = value
        for sensitive in sensitive_values:
            pattern = re.escape(sensitive)
            if sensitive.isdigit():
                pattern = rf"\b{pattern}\b"
            redacted = re.sub(pattern, REDACTED, redacted, flags=re.IGNORECASE)
        return redacted
    if isinstance(value, dict):
        return {key: redact_prose_values(item, sensitive_values) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_prose_values(item, sensitive_values) for item in value]
    return value


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
