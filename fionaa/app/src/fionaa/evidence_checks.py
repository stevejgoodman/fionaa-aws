"""Builds triage's per-requirement findings from loaded application state.

Deterministic and dependency-free by design: no `store`, no `Runtime`, no
model. Every rule here restates one line of `policies/general.md` in code
rather than asking an agent to read it, for the same reason `check_tools.py`
exists -- counting statements and comparing dates to a threshold is exactly
what an eval run already showed the model gets wrong when left to reason about
it from prose.

`explanation` strings are internal (they go into decision/triage.json for the
reviewer); `correction` strings are shown to the applicant verbatim, so they
never name a state key, a document key or a schema field.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fionaa.check_tools import (
    BANK_STATEMENT_MAX_AGE_DAYS,
    BANK_STATEMENT_MIN_COUNT,
    check_bank_statements_recent_and_sufficient,
)
from fionaa.triage import CHECKLIST, EvidenceFinding

# general.md: "Filed accounts are accounting statements or annual reports - the
# last accounting date must be in the past 12 months from date of application".
ACCOUNTS_MAX_AGE_DAYS = 365

# A proof-of-address document is evidence of where someone lived when it was
# issued, so it has to be recent to evidence where they live now. general.md
# has no rule for this (it has no proof-of-address rule at all -- see the
# module docstring in triage.py), so this is triage's own submission standard,
# set to the same 90 days the bank-statement rule already uses rather than
# inventing a second number for reviewers to remember.
ADDRESS_MAX_AGE_DAYS = 90

# The core application fields a human underwriter needs before they can begin.
# Not every ApplicationFormSchema field -- the schema already rejects a
# malformed application at load time; this catches the ones that are optional
# or nullable there but that nobody can underwrite without.
REQUIRED_APPLICATION_FIELDS = (
    "applicant_name", "year_of_birth", "company_name", "company_address",
    "loan_purpose", "loan_amount", "loan_term",
    "director_first_name", "director_surname", "director_residential_address",
    "trading_start_date", "annual_turnover", "annual_profit",
)


def _finding(status: str, explanation: str, refs=(), correction: str | None = None) -> EvidenceFinding:
    return EvidenceFinding(
        status=status, explanation=explanation, evidence_refs=list(refs), correction=correction
    )


def _normalized(value: Any) -> str:
    """Casefold and collapse whitespace so "  Jane  SMITH " matches "Jane Smith".

    Name matching here is deliberately this blunt, and a near-miss is not
    silently accepted: it becomes an `incomplete` finding asking the applicant
    to supply a document in the right name. That is the safe direction to err
    in -- the cost is an applicant re-uploading, not an unverified identity
    reaching an underwriter as though it had been checked.
    """
    return " ".join(str(value or "").casefold().split())


def _director_name(application: dict) -> str:
    return " ".join(
        str(application.get(key) or "") for key in ("director_first_name", "director_surname")
    ).strip()


def application_details(application: dict) -> EvidenceFinding:
    missing = [
        key for key in REQUIRED_APPLICATION_FIELDS
        if application.get(key) is None or not str(application[key]).strip()
    ]
    if not missing:
        return _finding("satisfied", "Core application details are present.", ["input/application.json"])
    labels = ", ".join(key.replace("_", " ") for key in missing)
    return _finding(
        "incomplete", f"Required application fields are absent: {labels}.",
        ["input/application.json"],
        # Field labels are the applicant's own form labels, so they are safe to
        # echo back -- they tell the applicant exactly which boxes to fill in.
        f"Complete these details on your application: {labels}.",
    )


def bank_statements(documents: list[dict], refs: list[str], today: date) -> EvidenceFinding:
    """general.md: at least 3 months of statements, most recent < 90 days old.

    The counting and the date comparison are delegated to
    `check_bank_statements_recent_and_sufficient`, the same deterministic tool
    the policy-check agent is required to call, so triage and policy_check can
    never disagree about whether the same statements pass the same rule.
    """
    if not documents:
        return _finding("missing", "No business bank statements were supplied.")
    try:
        end_dates = [str(doc["end_date"]) for doc in documents]
        result = check_bank_statements_recent_and_sufficient.func(
            statement_end_dates=end_dates, reference_date=today.isoformat()
        )
    except (KeyError, TypeError, ValueError):
        return _finding(
            "unreadable", "Statement period dates could not be read.", refs,
            "Supply business bank statements that clearly show the period each one covers.",
        )
    if not result["recent_enough"]:
        return _finding(
            "stale",
            f"Most recent statement is {result['days_since_most_recent_statement']} days old.", refs,
            f"Supply a business bank statement less than {BANK_STATEMENT_MAX_AGE_DAYS} days old.",
        )
    if not result["count_sufficient"]:
        return _finding(
            "incomplete", f"Only {result['statement_count']} statement(s) supplied.", refs,
            f"Supply at least {BANK_STATEMENT_MIN_COUNT} months of business bank statements.",
        )
    return _finding(
        "satisfied",
        f"{result['statement_count']} statements, most recent "
        f"{result['days_since_most_recent_statement']} days old.", refs,
    )


def annual_accounts(documents: list[dict], refs: list[str], today: date) -> EvidenceFinding:
    """general.md: the last accounting date must be within the past 12 months."""
    if not documents:
        return _finding("missing", "No filed annual accounts were supplied.")
    try:
        latest = max(date.fromisoformat(str(doc["accounting_year"])) for doc in documents)
    except (KeyError, TypeError, ValueError):
        return _finding(
            "unreadable", "Accounting dates could not be read.", refs,
            "Supply annual accounts that clearly show the accounting date.",
        )
    age_days = (today - latest).days
    if age_days > ACCOUNTS_MAX_AGE_DAYS:
        return _finding(
            "stale", f"Most recent accounting date is {age_days} days old.", refs,
            CHECKLIST["annual_accounts"],
        )
    return _finding("satisfied", f"Accounting date {latest.isoformat()} is within 12 months.", refs)


def director_id(documents: list[dict], refs: list[str], application: dict, today: date) -> EvidenceFinding:
    """Right kind of document (the schema enforces that), in date, right person."""
    if not documents:
        return _finding("missing", "No director photo ID was supplied.")
    director = _director_name(application)
    if not director:
        return _finding(
            "incomplete", "The application does not name a director to match the ID against.", refs,
            "Complete the director's name on your application.",
        )
    try:
        in_date = [doc for doc in documents if date.fromisoformat(str(doc["expiry_date"])) >= today]
    except (KeyError, TypeError, ValueError):
        return _finding(
            "unreadable", "ID expiry date could not be read.", refs,
            "Supply a passport or driving licence that clearly shows its expiry date.",
        )
    if not in_date:
        return _finding(
            "stale", "The supplied photo ID has expired.", refs,
            "Supply a passport or driving licence that has not expired.",
        )
    if not any(_normalized(doc.get("holder_name")) == _normalized(director) for doc in in_date):
        return _finding(
            "incomplete", "No in-date ID is held in the named director's name.", refs,
            "Supply photo ID in the name of the director named on your application.",
        )
    return _finding("satisfied", "In-date photo ID matching the named director.", refs)


def proof_of_address(documents: list[dict], refs: list[str], application: dict, today: date) -> EvidenceFinding:
    """Recent, addressed to the director, and showing the address they declared."""
    if not documents:
        return _finding("missing", "No proof of the director's address was supplied.")
    director = _director_name(application)
    declared = application.get("director_residential_address")
    if not director or not declared:
        return _finding(
            "incomplete", "The application lacks the director name or address to reconcile against.", refs,
            "Complete the director's name and residential address on your application.",
        )
    try:
        recent = [
            doc for doc in documents
            if (today - date.fromisoformat(str(doc["issue_date"]))).days <= ADDRESS_MAX_AGE_DAYS
        ]
    except (KeyError, TypeError, ValueError):
        return _finding(
            "unreadable", "Proof-of-address issue date could not be read.", refs,
            "Supply proof of address that clearly shows when it was issued.",
        )
    if not recent:
        return _finding(
            "stale", "The supplied proof of address is out of date.", refs,
            f"Supply proof of the director's address issued in the last {ADDRESS_MAX_AGE_DAYS} days.",
        )
    matching = [doc for doc in recent if _normalized(doc.get("holder_name")) == _normalized(director)]
    if not matching:
        return _finding(
            "incomplete", "No recent proof of address is in the named director's name.", refs,
            "Supply proof of address in the name of the director named on your application.",
        )
    if not any(_normalized(doc.get("address")) == _normalized(declared) for doc in matching):
        return _finding(
            "incomplete", "Proof of address does not show the address declared on the application.", refs,
            "Supply proof of address showing the same residential address as your application, "
            "or correct the address on your application.",
        )
    return _finding("satisfied", "Recent proof of address matching the declared address.", refs)


# state key -> CHECKLIST requirement. They match one-for-one today; the mapping
# exists so a future document type whose state key differs from its requirement
# name (several document types satisfying one requirement, say) doesn't have to
# rename state fields to keep `assess` working.
_REQUIREMENT_FOR = {
    "director_id": "director_id",
    "proof_of_address": "proof_of_address",
    "bank_statements": "bank_statements",
    "annual_accounts": "annual_accounts",
}


def _refs(document_errors: list[dict], state_key: str, loaded: int) -> list[str]:
    """Keys the applicant can recognise: the failed uploads plus a count of the
    ones that loaded. Full keys are safe here -- they are the applicant's own
    input paths, and evidence_refs never reaches the applicant note anyway."""
    failed = [entry["key"] for entry in document_errors if entry["state_key"] == state_key]
    return failed + ([f"{loaded} document(s) under input/{state_key}"] if loaded else [])


def build_findings(state: dict, today: date | None = None) -> dict[str, EvidenceFinding]:
    """Assess all five requirements against loaded state.

    A document that failed schema validation at load time (recorded in
    `document_errors` by `load_application`) makes its whole requirement
    `unreadable` regardless of what else loaded -- a submission where one of
    three statements is corrupt is not a complete submission, and the applicant
    can only fix it by replacing the file.
    """
    today = today or date.today()
    application = state.get("application") or {}
    errors = state.get("document_errors") or []

    def assess(state_key: str, check) -> EvidenceFinding:
        documents = state.get(state_key) or []
        refs = _refs(errors, state_key, len(documents))
        if any(entry["state_key"] == state_key for entry in errors):
            return _finding(
                "unreadable", "A supplied document could not be read.", refs,
                f"{CHECKLIST[_REQUIREMENT_FOR[state_key]]} One of the files supplied could not be read.",
            )
        return check(documents, refs)

    return {
        "application_details": application_details(application),
        "director_id": assess("director_id", lambda docs, refs: director_id(docs, refs, application, today)),
        "proof_of_address": assess(
            "proof_of_address", lambda docs, refs: proof_of_address(docs, refs, application, today)
        ),
        "bank_statements": assess("bank_statements", lambda docs, refs: bank_statements(docs, refs, today)),
        "annual_accounts": assess("annual_accounts", lambda docs, refs: annual_accounts(docs, refs, today)),
    }

