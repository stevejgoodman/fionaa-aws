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
from fionaa.evidence_checks import months_before


def _business_type(company_name: str | None) -> str | None:
    if not company_name:
        return None
    name = company_name.lower()
    # LLP checked before ltd/limited/plc -- an LLP's name may also contain
    # "limited" (e.g. "... Limited Liability Partnership").
    if "llp" in name.split():
        return "BusinessType_PARTNERSHIP"
    if re.search(r"\bplc\b", name) or re.search(r"\bltd\b", name) or "limited" in name:
        return "BusinessType_LIMITED_COMPANY"
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


def _addresses_reconcile(bank_statements: list[dict], annual_accounts: list[dict]) -> bool | None:
    statement_addresses = {s["address"].strip().lower() for s in bank_statements if s.get("address")}
    accounts_addresses = {
        doc["registered_address"].strip().lower()
        for doc in annual_accounts if doc.get("registered_address")
    }
    if not statement_addresses or not accounts_addresses:
        return None
    return bool(statement_addresses & accounts_addresses)


def build_derived_facts(
    application: dict,
    annual_accounts: list[dict],
    bank_statements: list[dict],
    companies_house: dict | None,
    today: date,
) -> dict:
    """AR-policy-variable-named facts, computed deterministically from
    already-available state -- see module docstring."""
    # general.md: a confirmed Companies House match is itself proof of both
    # halves of UK-based -- but the absence of a match is only "unconfirmed",
    # not proof of the negative, so only assert these true, never false.
    companies_house_confirmed = (companies_house or {}).get("found") is True
    statement_check = check_bank_statements_recent_and_sufficient.func(
        statement_end_dates=[s["end_date"] for s in bank_statements if s.get("end_date")],
        reference_date=today.isoformat(),
    )
    facts = {
        "today": today.isoformat(),
        "loanAmount": application.get("loan_amount"),
        "loanTermMonths": application.get("loan_term"),
        "applicantAge": _applicant_age(application.get("year_of_birth"), today),
        "businessType": _business_type(application.get("company_name")),
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
        "hasProofOfDirectorAddress": _addresses_reconcile(bank_statements, annual_accounts),
        "annualTurnover": application.get("annual_turnover"),
    }
    return {key: value for key, value in facts.items() if value is not None}
