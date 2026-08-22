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


# ---------------------------------------------------------------------------
# Loan repayment — affordability calculation
# ---------------------------------------------------------------------------
#
# Deliberately the simplest possible model (straight-line, no interest/APR
# amortisation) — see FINANCIAL_ASSESSMENT_PROMPT, which calls this out as a
# working assumption rather than a full repayment schedule. Kept here as a
# single tool covering both secured and unsecured loans rather than split
# per loan type, since the formula itself doesn't vary between them.

@tool
def compute_monthly_repayment(amount_borrowed: int, term_months: int) -> float:
    """Monthly repayment for a secured or unsecured business loan: amount
    borrowed divided by term in months (a straight-line estimate, not a full
    amortisation schedule). Call this with the application's `loan_amount`
    and `loan_term` rather than estimating the repayment yourself."""
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    return round(amount_borrowed / term_months, 2)


# ---------------------------------------------------------------------------
# Loan amount range checks
# ---------------------------------------------------------------------------
#
# The three loan types with a fixed min/max amount (rather than a
# turnover/invoice-value-relative one) each get their own tool with the
# range hardcoded, same convention as the invoice advance-rate tools above —
# an eval run surfaced the policy-check agent misreading "£25,000 –
# £20,000,000" out of secured-business-loans/policy.md as a much smaller
# ceiling (stated "£40,000 exceeds the maximum threshold" against the real
# £20,000,000 max) and wrongly rejecting a valid application. Same
# motivating failure mode as compute_invoice_factoring_advance: don't trust
# the agent to read and compare a large comma-grouped figure out of prose,
# give it a tool that does the comparison exactly.
#
# Docstrings below say "the application's loan_amount" — that's
# ApplicationFormSchema's actual field (see schemas.py), matching the
# convention compute_monthly_repayment already uses. The
# secured-loan-uses-secured-policy-not-unsecured eval scenario that
# surfaced this bug names its fixture field requested_amount instead, which
# doesn't match ApplicationFormSchema at all — a separate, pre-existing
# dataset-authoring inconsistency (see fionaa_eval_dataset.jsonl), not
# something this fix should paper over by matching the wrong name.

SECURED_BUSINESS_LOAN_AMOUNT_MIN = 25_000
SECURED_BUSINESS_LOAN_AMOUNT_MAX = 20_000_000

UNSECURED_BUSINESS_LOAN_AMOUNT_MIN = 1_000
UNSECURED_BUSINESS_LOAN_AMOUNT_MAX = 500_000

REVOLVING_CREDIT_FACILITY_AMOUNT_MIN = 10_000
REVOLVING_CREDIT_FACILITY_AMOUNT_MAX = 1_000_000


@tool
def check_secured_business_loan_amount_in_range(requested_amount: int) -> bool:
    """True if requested_amount falls within the secured-business-loans
    policy's GBP 25,000-20,000,000 range, per
    policies/secured-business-loans/policy.md. Call this with the
    application's loan_amount rather than judging the comparison
    yourself."""
    return SECURED_BUSINESS_LOAN_AMOUNT_MIN <= requested_amount <= SECURED_BUSINESS_LOAN_AMOUNT_MAX


@tool
def check_unsecured_business_loan_amount_in_range(requested_amount: int) -> bool:
    """True if requested_amount falls within the unsecured-business-loans
    policy's GBP 1,000-500,000 range, per
    policies/unsecured-business-loans/policy.md. Call this with the
    application's loan_amount rather than judging the comparison
    yourself."""
    return UNSECURED_BUSINESS_LOAN_AMOUNT_MIN <= requested_amount <= UNSECURED_BUSINESS_LOAN_AMOUNT_MAX


@tool
def check_revolving_credit_facility_amount_in_range(requested_amount: int) -> bool:
    """True if requested_amount falls within the revolving-credit-facility
    policy's GBP 10,000-1,000,000 range, per
    policies/revolving-credit-facility/policy.md. Call this with the
    application's loan_amount rather than judging the comparison
    yourself."""
    return REVOLVING_CREDIT_FACILITY_AMOUNT_MIN <= requested_amount <= REVOLVING_CREDIT_FACILITY_AMOUNT_MAX


# All tools in the pool, keyed by name — this is what `graph.tools_for` is
# subset against using the names each policy.md declares.
CHECK_TOOLS_POOL = [
    compute_invoice_factoring_advance,
    compute_invoice_discounting_advance,
    compute_monthly_repayment,
    check_secured_business_loan_amount_in_range,
    check_unsecured_business_loan_amount_in_range,
    check_revolving_credit_facility_amount_in_range,
]
