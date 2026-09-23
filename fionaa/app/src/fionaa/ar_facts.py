"""Deterministic evidence for the Automated Reasoning claim checker.

validate_final_decision (workflow/validation.py) hands each atomic claim to
Bedrock's Automated Reasoning guardrail alongside a "facts" blob, for it to
translate into the policy's own formal variables (see
agentcore/automated-reasoning/*-definition.json, e.g. loanAmount,
isUKBased, businessType, tradingHistoryMonths). Handing it only the raw,
self-reported JSON (field names like loan_amount/trading_start_date that
don't match the policy's own variable names, and no reference "today" for
the several rules that are date-relative) makes that translation
unreliable -- most claims came back "inconclusive" even when the underlying
assertion was straightforwardly true. This module precomputes the same
facts deterministically, using the policy's own variable names, so AR only
has to translate them, not derive them.

Every value here is omitted rather than guessed when the input needed to
compute it isn't present -- the policy's own variable descriptions say
missing evidence is unknown, not false (see e.g. hasRequiredDocuments'
description in unsecured-business-loans-definition.json).
"""
from __future__ import annotations

import re
from datetime import date

from fionaa.check_tools import check_bank_statements_recent_and_sufficient
from fionaa.evidence_checks import _director_name, _normalized, months_before
from fionaa.evidence_checks import director_id as _check_director_id
from fionaa.evidence_checks import proof_of_address as _check_proof_of_address

# DirectorIdSchema.document_kind ("passport" | "driving_licence") -> the AR
# policy's own DirectorIDType enum values (see
# unsecured-business-loans-definition.json).
_DIRECTOR_ID_TYPE = {
    "passport": "DirectorIDType_PASSPORT",
    "driving_licence": "DirectorIDType_DRIVERS_LICENCE",
}


def _business_type_from_name(name: str) -> str | None:
    name = name.lower()
    # LLP checked before ltd/limited/plc -- an LLP's name may also contain
    # "limited" (e.g. "... Limited Liability Partnership").
    if "llp" in name.split():
        return "BusinessType_PARTNERSHIP"
    if re.search(r"\bplc\b", name) or re.search(r"\bltd\b", name) or "limited" in name:
        return "BusinessType_LIMITED_COMPANY"
    return None


def _business_type(company_name: str | None, companies_house_summary: str | None = None) -> str | None:
    """Self-reported company_name is checked first (an applicant naming their
    own business as "... Ltd" is authoritative), but a trading name that
    drops the legal suffix (e.g. "GoodAI Consulting" for the registered
    "GOODAI CONSULTING LTD") would otherwise leave businessType permanently
    unknown for a company that plainly has one -- every AR claim in a
    fullapp scenario transitively depends on isSubstantivelyEligible /
    approvalMeetsCoveredPolicy, both gated on businessType, so a missing
    value here makes every claim inconclusive (the AR engine can satisfy
    both true and false scenarios) even when the application is otherwise
    completely unambiguous. COMPANIES_HOUSE_PROMPT's summary always states
    the registered company name when found=True (see its Step 2), so the
    same deterministic suffix pattern is applied there as a fallback --
    still pattern-matching on text, not inferring/guessing a value from
    nothing, same as the company_name check above."""
    if company_name:
        found = _business_type_from_name(company_name)
        if found is not None:
            return found
    if companies_house_summary:
        return _business_type_from_name(companies_house_summary)
    return None


def _applicant_age(year_of_birth, today: date) -> int | None:
    try:
        return today.year - int(year_of_birth)
    except (TypeError, ValueError):
        return None


def _trading_history_months(trading_start_date, today: date) -> int | None:
    if not trading_start_date:
        return None
    try:
        start = date.fromisoformat(str(trading_start_date))
    except ValueError:
        return None
    months = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        months -= 1
    return max(months, 0)


def _most_recent_annual_accounts(annual_accounts: list[dict]) -> dict | None:
    if not annual_accounts:
        return None
    return max(annual_accounts, key=lambda doc: doc.get("accounting_year") or "")


def _last_accounting_date_within_past_12_months(annual_accounts: list[dict], today: date) -> bool | None:
    most_recent = _most_recent_annual_accounts(annual_accounts)
    if most_recent is None or not most_recent.get("accounting_year"):
        return None
    try:
        accounting_date = date.fromisoformat(str(most_recent["accounting_year"]))
    except ValueError:
        return None
    return months_before(today, 12) <= accounting_date <= today


def _has_proof_of_director_address(proof_of_address: list[dict], application: dict, today: date) -> bool | None:
    """Whether a recent, name-and-address-matching proof-of-address document
    was supplied -- delegates to evidence_checks.proof_of_address (the same
    check triage runs) rather than reimplementing the matching logic here.

    Previously this compared bank_statements' business address against
    annual_accounts' registered address -- neither of which is proof of the
    *director's residential* address at all, so it returned False for a
    completely valid application that had genuinely supplied a matching
    proof-of-address document (a director's home address and a company's
    trading address are unrelated by design; there is no reason they should
    ever match). No documents supplied is "unknown", same as every other
    fact in this module -- only a definite failure (stale/incomplete/
    unreadable) or a definite pass is reported as False/True."""
    if not proof_of_address:
        return None
    # A non-empty placeholder ref, not a real store key -- EvidenceFinding
    # requires evidence_refs on a "satisfied" status, but this module only
    # ever reads the .status off the result, never the refs themselves.
    status = _check_proof_of_address(proof_of_address, ["proof_of_address"], application, today).status
    return status == "satisfied"


def _director_id_facts(director_id: list[dict], application: dict, today: date) -> tuple[bool | None, str | None]:
    """(hasValidDirectorID, directorIDType) -- delegates to
    evidence_checks.director_id for validity (same check triage runs), then
    separately identifies which supplied document actually matched (that
    function only returns pass/fail, not which document) to report its
    kind. The matching predicate here (in-date + normalized holder-name
    match) intentionally mirrors evidence_checks.director_id's own, since
    it's only ever applied to documents that check has already confirmed
    satisfy the requirement."""
    if not director_id:
        return None, None
    if _check_director_id(director_id, ["director_id"], application, today).status != "satisfied":
        return False, None
    director = _normalized(_director_name(application))
    for doc in director_id:
        try:
            in_date = date.fromisoformat(str(doc["expiry_date"])) >= today
        except (KeyError, TypeError, ValueError):
            continue
        if in_date and _normalized(doc.get("holder_name")) == director:
            return True, _DIRECTOR_ID_TYPE.get(doc.get("document_kind"))
    return True, None  # satisfied but the matching doc's kind wasn't one of the two AR knows


def build_derived_facts(
    application: dict,
    annual_accounts: list[dict],
    bank_statements: list[dict],
    companies_house: dict | None,
    today: date,
    director_id: list[dict] | None = None,
    proof_of_address: list[dict] | None = None,
) -> dict:
    """AR-policy-variable-named facts, computed deterministically from
    already-available state -- see module docstring.

    director_id/proof_of_address default to None (treated as "no documents
    supplied", the same as an empty list) rather than being required
    positional args -- keeps this backward compatible for any caller that
    hasn't been updated to pass them."""
    # general.md: a confirmed Companies House match is itself proof of both
    # halves of UK-based -- but the absence of a match is only "unconfirmed",
    # not proof of the negative, so only assert these true, never false.
    companies_house_confirmed = (companies_house or {}).get("found") is True
    statement_check = check_bank_statements_recent_and_sufficient.func(
        statement_end_dates=[s["end_date"] for s in bank_statements if s.get("end_date")],
        reference_date=today.isoformat(),
    )
    has_valid_director_id, director_id_type = _director_id_facts(director_id or [], application, today)
    facts = {
        "today": today.isoformat(),
        "loanAmount": application.get("loan_amount"),
        "loanTermMonths": application.get("loan_term"),
        "applicantAge": _applicant_age(application.get("year_of_birth"), today),
        "businessType": _business_type(
            application.get("company_name"),
            (companies_house or {}).get("summary") if companies_house_confirmed else None,
        ),
        "hasCompaniesHouseMatch": companies_house_confirmed or None,
        "isRegisteredInUK": True if companies_house_confirmed else None,
        "hasUKAddress": True if companies_house_confirmed else None,
        "isUKBased": True if companies_house_confirmed else None,
        "tradingHistoryMonths": _trading_history_months(application.get("trading_start_date"), today),
        "hasAnnualAccounts": bool(annual_accounts) or None,
        "hasAccountsOrManagementInformation": bool(annual_accounts) or None,
        "lastAccountingDateWithinPast12Months": _last_accounting_date_within_past_12_months(annual_accounts, today),
        "bankStatementsMonthsCount": statement_check["statement_count"],
        "mostRecentStatementAgeDays": statement_check["days_since_most_recent_statement"],
        "hasRecentBankStatements": statement_check["count_sufficient"] and statement_check["recent_enough"],
        "hasProofOfDirectorAddress": _has_proof_of_director_address(proof_of_address or [], application, today),
        "hasValidDirectorID": has_valid_director_id,
        "directorIDType": director_id_type,
        "annualTurnover": application.get("annual_turnover"),
        # Declarations, not documents: both are optional bool fields on the
        # application form, and both gate a conditional document rule --
        # `(or (not isVATRegistered) hasVATReturns)` and the borrowing
        # equivalent. A false declaration satisfies its rule outright, so no
        # VAT return or borrowing statement is required and the document
        # variable behind it never has to be bound. Undeclared stays None
        # and is dropped below, same as every other fact here: unknown is
        # not no. hasVATReturns/hasBorrowingDetails themselves still have no
        # source -- see ar_claims.KNOWN_UNCOVERED_VARIABLES.
        "isVATRegistered": application.get("vat_registered"),
        "hasExistingBorrowing": application.get("has_existing_borrowing"),
    }
    return {key: value for key, value in facts.items() if value is not None}
