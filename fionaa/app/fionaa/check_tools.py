"""Shared pool of deterministic, loan-policy calculation tools.

These exist so the policy-check agent never has to read a number out of
policy prose and do the arithmetic itself (see POLICY_CHECK_PROMPT) — each
tool is a plain, independently-testable Python function wrapped with
`@tool` so `check_against_policy` can hand the model a scoped subset of them.

Which tools apply to which loan type is declared in that type's `policy.md`
(a `<!-- checks: name1, name2 -->` comment — see `policy_loader.py`), not
here — this module only holds the implementations. Adding a new check: write
the function here, wrap it with `@tool`, and reference its name from the
relevant policy.md(s). No graph.py or policy_loader.py changes needed.

Kept as pure functions (no `runtime`/store access) so they stay trivially
unit-testable in isolation; the audit trail is captured by
`check_against_policy` harvesting `ToolMessage`s off the agent's response,
not by the tools writing their own artifacts.
"""

from __future__ import annotations

from langchain.tools import tool

# ---------------------------------------------------------------------------
# Invoice factoring / discounting — advance calculation
# ---------------------------------------------------------------------------
#
# Same 70-90% band today, but kept as two distinct tools (rather than one
# tool taking a rate argument) deliberately: letting the agent supply the
# percentage would reintroduce the exact risk this was built to avoid — the
# band stays hardcoded per loan type, not agent-supplied. Split the band
# apart the moment the two types' rules actually diverge (see each
# policy.md).

FACTORING_ADVANCE_RATE_LOW = 0.70
FACTORING_ADVANCE_RATE_HIGH = 0.90
FACTORING_ADVANCE_RATE_DEFAULT = 0.80  # midpoint, until risk-based pricing picks a rate

DISCOUNTING_ADVANCE_RATE_LOW = 0.70
DISCOUNTING_ADVANCE_RATE_HIGH = 0.90
DISCOUNTING_ADVANCE_RATE_DEFAULT = 0.80


@tool
def compute_invoice_factoring_advance(invoices_owed: int) -> int:
    """Advance amount for Invoice Factoring, per the 70-90% band in
    policies/invoice-factoring/policy.md. Call this with the application's
    `invoices_owed` value rather than estimating the advance yourself."""
    return round(invoices_owed * FACTORING_ADVANCE_RATE_DEFAULT)


@tool
def compute_invoice_discounting_advance(invoices_owed: int) -> int:
    """Advance amount for Invoice Discounting, per the 70-90% band in
    policies/invoice-discounting/policy.md. Call this with the application's
    `invoices_owed` value rather than estimating the advance yourself."""
    return round(invoices_owed * DISCOUNTING_ADVANCE_RATE_DEFAULT)


# All tools in the pool, keyed by name — this is what `graph.tools_for` is
# subset against using the names each policy.md declares.
CHECK_TOOLS_POOL = [
    compute_invoice_factoring_advance,
    compute_invoice_discounting_advance,
]
