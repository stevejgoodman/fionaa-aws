"""Deterministic, code-generated Automated Reasoning claim sentences.

Replaces per-run LLM-authored prose as the text sent to Bedrock's
ApplyGuardrail. Every sentence here is templated from already-known,
already-deterministic values (ar_facts.py's derived facts, or a small
enum/list-emptiness check on policy_check/proposed_decision) -- never from
freeform LLM prose -- so identical input state always produces byte-identical
wording, run after run. See PolicyConsistencyChecker.check() for how these
are sent, and workflow/validation.py for how they're assembled per run.

LOAN_TYPE_LEAF_VARIABLES only lists what ar_facts.py currently computes.
Several variables each policy declares aren't derived anywhere yet --
KNOWN_UNCOVERED_VARIABLES below names every one of them, per loan type,
and test_ar_claims.py asserts that list is exactly right against the real
policy definitions. That expansion is deliberately deferred, incremental
work -- this module's filter-then-assert mechanism picks new facts up
automatically once ar_facts.py grows to compute them; it doesn't need to
be redesigned.
"""
from __future__ import annotations

from fionaa.domain.applications import LoanType

# The three compound outcome variables every loan type's AR policy declares
# identically (confirmed against all five agentcore/automated-reasoning/
# *-definition.json files) -- see each file's "rules" for how they compose
# from the leaf variables below. Kept honest by test_ar_claims.py's
# drift-guard test.
ELIGIBILITY_VARIABLE = "isSubstantivelyEligible"
DOCUMENTATION_VARIABLE = "hasRequiredDocuments"
APPROVAL_VARIABLE = "approvalMeetsCoveredPolicy"

# Leaf AR-policy variable names ar_facts.build_derived_facts() computes,
# grouped by which loan types actually declare them (see each
# *-definition.json's "variables" list). Hand-maintained here because
# agentcore/automated-reasoning/ is not bundled into the deployed runtime
# package (agentcore.json's codeLocation is "app/" only) -- same reasoning
# as policy_loader.py's local policies/ bundle. Kept honest by
# test_ar_claims.py's drift-guard test, which reads those files directly.
_SHARED_LEAF_VARIABLES = frozenset({
    "applicantAge", "bankStatementsMonthsCount", "mostRecentStatementAgeDays",
    "businessType", "directorIDType", "hasCompaniesHouseMatch",
    "hasRecentBankStatements", "hasUKAddress", "hasValidDirectorID",
    "isRegisteredInUK", "isUKBased",
})
LOAN_TYPE_LEAF_VARIABLES: dict[LoanType, frozenset[str]] = {
    LoanType.unsecured_business_loans: _SHARED_LEAF_VARIABLES | {
        "hasAccountsOrManagementInformation", "hasExistingBorrowing",
        "hasProofOfDirectorAddress", "isVATRegistered", "loanAmount",
        "loanTermMonths", "tradingHistoryMonths",
    },
    LoanType.secured_business_loans: _SHARED_LEAF_VARIABLES | {
        "hasAnnualAccounts", "lastAccountingDateWithinPast12Months",
        "loanAmount", "loanTermMonths", "tradingHistoryMonths",
    },
    # No hasExistingBorrowing here: this policy requires hasBorrowingDetails
    # unconditionally, so it declares no such variable to gate it.
    LoanType.revolving_credit_facility: _SHARED_LEAF_VARIABLES | {
        "hasAccountsOrManagementInformation", "hasProofOfDirectorAddress",
        "isVATRegistered", "loanAmount", "tradingHistoryMonths",
    },
    LoanType.invoice_discounting: _SHARED_LEAF_VARIABLES | {"annualTurnover"},
    LoanType.invoice_factoring: _SHARED_LEAF_VARIABLES | {"annualTurnover"},
}


# Leaf variables each policy declares that ar_facts.py does NOT compute, so
# they reach the AR engine unbound. The engine is then free to choose their
# values, and any claim whose truth depends on one can only come back
# "satisfiable" -- reported as "inconclusive", however clear-cut the
# application is. This is the deferred expansion the module docstring
# describes, written down per loan type so the gap is countable rather than
# prose: test_ar_claims.py derives the same set from the real policy
# definitions and asserts an exact match, so closing one (or a policy
# declaring a new one) must be reflected here.
#
# A variable is a "leaf" when no rule defines it from other variables --
# the solver derives the rest (isEligibleBusinessType, loanAmountInRange,
# personalGuaranteeRequirementMet ...) and they are never sent as facts.
#
# At runtime, ar_findings.diagnose() reports which of these actually left a
# given check undecided; this list is the static, per-product view.
KNOWN_UNCOVERED_VARIABLES: dict[LoanType, frozenset[str]] = {
    LoanType.unsecured_business_loans: frozenset({
        "hasBorrowingDetails", "hasPersonalGuarantee", "hasVATReturns",
    }),
    LoanType.secured_business_loans: frozenset({
        "collateralAssetType", "hasProofOfCollateralOwnershipValuation",
    }),
    LoanType.revolving_credit_facility: frozenset({
        "hasBorrowingDetails", "hasSecurityAssetDetails", "hasVATReturns",
        "isSecuredFacility",
    }),
    LoanType.invoice_discounting: frozenset({
        "advanceRatePercent", "contractTermMonths", "hasAgedDebtReports",
        "hasAgreedExitNoticePeriod", "hasBusinessRegistrationDocuments",
        "hasCreditControlProcedures", "hasCustomerBaseDetails",
        "hasRecentFinancialStatements",
    }),
    LoanType.invoice_factoring: frozenset({
        "advanceRatePercent", "contractTermMonths", "hasAgedDebtReports",
        "hasAgreedExitNoticePeriod", "hasBusinessRegistrationDocuments",
        "hasCustomerBaseDetails", "hasRecentFinancialStatements",
    }),
}


# The variables each claim's own rules actually reach, claim variable
# excluded -- its dependency graph, not just its leaves, so the compound
# facts ar_facts.py computes directly (isUKBased, hasRecentBankStatements,
# hasValidDirectorID) are kept for the claim they belong to and dropped
# for the one they don't.
#
# Sending a claim only these is not an optimisation, it is what makes the
# check work at all. Measured against the live guardrails on 2026-09-23
# (evals/automated_reasoning/premise-handling-probe.json): 13 premises
# decide, 17 come back "tooComplex" -- the engine declines to solve, so
# every claim on a complete application was undecidable while all of a
# loan type's facts went to every claim. Scoped this way each claim sends
# 3-8 facts today.
#
# hasCompaniesHouseMatch is in no set: it appears only in an implication
# (=> hasCompaniesHouseMatch (and isRegisteredInUK hasUKAddress)), never
# in a definition, and ar_facts.py already asserts both variables it
# would imply. Hand-maintained for the same reason as
# LOAN_TYPE_LEAF_VARIABLES, and kept honest by test_ar_claims.py, which
# recomputes these from the real policy definitions.
#
# APPROVAL_VARIABLE is deliberately absent. Its rule is
# `approvalMeetsCoveredPolicy = isSubstantivelyEligible AND
# hasRequiredDocuments`, so its dependency graph is the union of the other
# two -- 13 to 17 variables, over the ceiling on every product, and no
# scoping reduces a union below its parts. approval_premise_facts() below
# composes it from those two values instead, which is a two-premise check.
CLAIM_VARIABLES: dict[LoanType, dict[str, frozenset[str]]] = {
    LoanType.unsecured_business_loans: {
        ELIGIBILITY_VARIABLE: frozenset({
            "applicantAge", "businessType", "hasPersonalGuarantee", "hasUKAddress",
            "isApplicantAgeEligible", "isEligibleBusinessType", "isRegisteredInUK",
            "isUKBased", "loanAmount", "loanAmountInRange", "loanTermInRange",
            "loanTermMonths", "meetsMinimumTradingHistory",
            "personalGuaranteeRequirementMet", "tradingHistoryMonths",
        }),
        DOCUMENTATION_VARIABLE: frozenset({
            "bankStatementsMonthsCount", "directorIDType",
            "hasAccountsOrManagementInformation", "hasBorrowingDetails",
            "hasExistingBorrowing", "hasProofOfDirectorAddress",
            "hasRecentBankStatements", "hasVATReturns", "hasValidDirectorID",
            "isVATRegistered", "mostRecentStatementAgeDays",
        }),
    },
    LoanType.secured_business_loans: {
        ELIGIBILITY_VARIABLE: frozenset({
            "applicantAge", "businessType", "collateralAssetType", "hasSecurityAsset",
            "hasUKAddress", "isApplicantAgeEligible", "isEligibleBusinessType",
            "isRegisteredInUK", "isUKBased", "loanAmount", "loanAmountInRange",
            "loanTermMonths", "meetsMinimumTradingHistory", "tradingHistoryMonths",
        }),
        DOCUMENTATION_VARIABLE: frozenset({
            "bankStatementsMonthsCount", "hasAnnualAccounts",
            "hasProofOfCollateralOwnershipValuation", "hasRecentBankStatements",
            "lastAccountingDateWithinPast12Months", "mostRecentStatementAgeDays",
        }),
    },
    LoanType.revolving_credit_facility: {
        ELIGIBILITY_VARIABLE: frozenset({
            "applicantAge", "businessType", "hasUKAddress", "isApplicantAgeEligible",
            "isEligibleBusinessType", "isRegisteredInUK", "isUKBased", "loanAmount",
            "loanAmountInRange", "meetsMinimumTradingHistory", "tradingHistoryMonths",
        }),
        DOCUMENTATION_VARIABLE: frozenset({
            "bankStatementsMonthsCount", "directorIDType",
            "hasAccountsOrManagementInformation", "hasBorrowingDetails",
            "hasProofOfDirectorAddress", "hasRecentBankStatements",
            "hasSecurityAssetDetails", "hasVATReturns", "hasValidDirectorID",
            "isSecuredFacility", "isVATRegistered", "mostRecentStatementAgeDays",
        }),
    },
    LoanType.invoice_discounting: {
        ELIGIBILITY_VARIABLE: frozenset({
            "advanceRateInRange", "advanceRatePercent", "annualTurnover", "applicantAge",
            "businessType", "contractTermMonths", "contractTermRequirementMet",
            "hasAgreedExitNoticePeriod", "hasUKAddress", "isApplicantAgeEligible",
            "isEligibleBusinessType", "isRegisteredInUK", "isUKBased",
            "turnoverRequirementMet",
        }),
        DOCUMENTATION_VARIABLE: frozenset({
            "bankStatementsMonthsCount", "hasAgedDebtReports",
            "hasBusinessRegistrationDocuments", "hasCreditControlProcedures",
            "hasCustomerBaseDetails", "hasRecentBankStatements",
            "hasRecentFinancialStatements", "mostRecentStatementAgeDays",
        }),
    },
    LoanType.invoice_factoring: {
        ELIGIBILITY_VARIABLE: frozenset({
            "advanceRateInRange", "advanceRatePercent", "annualTurnover", "applicantAge",
            "businessType", "contractTermMonths", "contractTermRequirementMet",
            "hasAgreedExitNoticePeriod", "hasUKAddress", "isApplicantAgeEligible",
            "isEligibleBusinessType", "isRegisteredInUK", "isUKBased",
            "turnoverRequirementMet",
        }),
        DOCUMENTATION_VARIABLE: frozenset({
            "bankStatementsMonthsCount", "hasAgedDebtReports",
            "hasBusinessRegistrationDocuments", "hasCustomerBaseDetails",
            "hasRecentBankStatements", "hasRecentFinancialStatements",
            "mostRecentStatementAgeDays",
        }),
    },
}


def render_fact(name: str, value) -> str:
    """'isUKBased is true.' / 'businessType is BusinessType_LIMITED_COMPANY.'"""
    rendered = "true" if value is True else "false" if value is False else value
    return f"{name} is {rendered}."


def leaf_facts_for_loan_type(loan_type: LoanType, derived_facts: dict) -> dict:
    """derived_facts filtered to the variable names this loan type's AR
    policy actually declares -- ar_facts.build_derived_facts()'s output
    mixes variables from every loan type (plus "today", which isn't a
    declared AR variable at all) in one flat dict."""
    allowed = LOAN_TYPE_LEAF_VARIABLES.get(loan_type, frozenset())
    return {name: value for name, value in derived_facts.items() if name in allowed}


def eligibility_assertion(policy_check: dict) -> str | None:
    """None means "assert nothing" -- eligible == "inconclusive" is the LLM
    saying it can't tell, which has no clean boolean to assert."""
    eligible = policy_check.get("eligible")
    if eligible not in ("eligible", "ineligible"):
        return None
    return render_fact(ELIGIBILITY_VARIABLE, eligible == "eligible")


def documentation_assertion(policy_check: dict) -> str:
    """documentation_gaps is a list; only its emptiness (not its prose
    contents) feeds this assertion."""
    return render_fact(DOCUMENTATION_VARIABLE, not policy_check.get("documentation_gaps"))


def approval_assertion(proposed_decision: dict) -> str | None:
    """None for "referred" -- a referral can be driven by non-policy
    findings (web_search, financial_assessment), so it has no clean
    approvalMeetsCoveredPolicy boolean to assert."""
    outcome = proposed_decision.get("outcome")
    if outcome not in ("approved", "rejected"):
        return None
    return render_fact(APPROVAL_VARIABLE, outcome == "approved")


def facts_for_claim(loan_type: LoanType, claim_variable: str, derived_facts: dict) -> dict:
    """derived_facts filtered to what this one claim's rules reach.

    Not the same filter as leaf_facts_for_loan_type: that one keeps
    everything a loan type declares, which is more premises than the AR
    engine will solve (see CLAIM_VARIABLES). Unknown claim variable ->
    nothing, rather than everything, so a future claim added without a
    CLAIM_VARIABLES entry fails visibly instead of silently sending a
    too-large request."""
    allowed = CLAIM_VARIABLES.get(loan_type, {}).get(claim_variable, frozenset())
    return {name: value for name, value in derived_facts.items() if name in allowed}


def approval_premise_facts(policy_check: dict) -> dict:
    """The two values approvalMeetsCoveredPolicy is defined from.

    Each is the subject of its own claim, checked against its own facts,
    so composing the approval check from them keeps it to two premises
    instead of the 13-17 its full dependency graph would need. The
    eligibility half is omitted when the LLM couldn't tell (same rule as
    eligibility_assertion) -- one premise then, and an honestly
    undecidable check, rather than a guessed one."""
    facts = {DOCUMENTATION_VARIABLE: not policy_check.get("documentation_gaps")}
    eligible = policy_check.get("eligible")
    if eligible in ("eligible", "ineligible"):
        facts[ELIGIBILITY_VARIABLE] = eligible == "eligible"
    return facts
