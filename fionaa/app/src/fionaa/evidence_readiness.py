"""Deterministic readiness assessment of validated ingestion records."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from fionaa.triage import EvidenceFinding, ReadinessInput, assess_readiness


def months_before(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def finding(status, explanation, refs=(), correction=None):
    return EvidenceFinding(status=status, explanation=explanation,
                           evidence_refs=list(refs), correction=correction)


def statement_coverage_facts(statements: list[dict], submission_date: date) -> dict:
    """Count distinct covered days per account, never duplicate/overlapping files.

    Three calendar months are measured backwards from the latest statement's
    inclusive end date. Coverage can span several statements; separate accounts
    cannot be added together to manufacture three months of history.
    """
    if not statements:
        return {"months": 0, "age_days": None, "sufficient": False}
    accounts = defaultdict(list)
    for data in statements:
        if not data["bank_name"].strip() or not data["account_number"].strip():
            raise ValueError("Statement account details are incomplete")
        start, end = date.fromisoformat(data["start_date"]), date.fromisoformat(data["end_date"])
        if start > end or end > submission_date:
            raise ValueError("Statement dates conflict with the submission timeline")
        accounts[(data["bank_name"].strip().casefold(), data["account_number"].replace(" ", ""))].append((start, end))
    coverage = []
    for intervals in accounts.values():
        intervals.sort()
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        end = merged[-1][1]
        covered_days = sum((b - a).days + 1 for a, b in merged)
        boundary = end + timedelta(days=1)
        months = 0
        while months < (boundary.year - 1) * 12 + boundary.month - 1 and (
            boundary - months_before(boundary, months + 1)
        ).days <= covered_days:
            months += 1
        age = (submission_date - end).days
        coverage.append({"months": months, "age_days": age, "sufficient": months >= 3 and age < 90})
    # Use a single account's count and age together. Prefer a recent account;
    # an old large history cannot supply the count for a different new account.
    return max(coverage, key=lambda item: (item["age_days"] < 90, item["months"], -item["age_days"]))


def bank_coverage(documents: list[dict], submission_date: date) -> EvidenceFinding:
    refs = [doc["source_key"] for doc in documents]
    if not documents:
        return finding("missing", "No business bank statements were supplied.")
    try:
        coverage = statement_coverage_facts([doc["data"] for doc in documents], submission_date)
    except (ValueError, KeyError, TypeError, OverflowError):
        return finding("unable_to_verify", "Statement dates or account details need internal verification.", refs)
    if coverage["age_days"] >= 90:
        return finding("stale", "Supplied statements are at least 90 days old at submission.", refs,
                       f"Supply a business bank statement ending less than 90 days before {submission_date.isoformat()}.")
    if coverage["sufficient"]:
        return finding("satisfied", "At least three months of distinct statement coverage with a recent statement.", refs)
    return finding("incomplete", "Supplied statements do not establish three months of coverage for an account.", refs,
                   "Supply at least three months of business bank statement coverage; duplicate and overlapping periods count only once.")


def _normalized(value):
    return " ".join(str(value or "").casefold().split())


def _application_finding(application):
    required = ("applicant_name", "company_name", "company_address", "loan_purpose",
                "loan_amount", "loan_term", "director_first_name", "director_surname",
                "director_residential_address", "year_of_birth", "trading_start_date",
                "annual_turnover", "annual_profit")
    missing = [key for key in required if application.get(key) is None or not str(application[key]).strip()]
    if missing:
        labels = ", ".join(key.replace("_", " ") for key in missing)
        return finding("incomplete", "Required application fields are absent.", ["input/application.json"],
                       f"Complete these application fields: {labels}.")
    if any(type(application[key]) is not int for key in ("loan_amount", "loan_term")):
        return finding("unable_to_verify", "Loan amount or term has an invalid input type.", ["input/application.json"])
    return finding("satisfied", "Core application details are present.", ["input/application.json"])


def assess_submission(application: dict, manifest: dict, documents: list[dict]):
    submission_date = date.fromisoformat(manifest["submission_date"])
    groups = defaultdict(list)
    for doc in documents:
        groups[doc["document_type"]].append(doc)

    def assess_group(types, check):
        items = [doc for kind in types for doc in groups[kind]]
        refs = [doc["source_key"] for doc in items]
        if any(doc["processing_status"] in {"pending", "failed"} for doc in items):
            return finding("unable_to_verify", "Document processing is incomplete or failed.", refs)
        processed = [doc for doc in items if doc["processing_status"] == "processed"]
        result = check(processed)
        # A usable alternative satisfies the requirement even if an additional
        # source is unreadable (e.g. management information instead of accounts).
        if result.status != "satisfied" and any(doc["processing_status"] == "unreadable" for doc in items):
            return finding("unreadable", "The source document was confirmed unreadable during ingestion.", refs,
                           "Supply a readable replacement for the referenced document(s).")
        return result

    def matched(items, field, expected):
        if not items:
            return finding("missing", "No document was supplied for this requirement.")
        refs = [doc["source_key"] for doc in items]
        if not expected:
            return finding("incomplete", "Application details needed to reconcile the evidence are absent.", refs,
                           "Complete the applicant and business identity and address fields on the application.")
        if not any(_normalized(doc["data"].get(field)) == _normalized(expected) for doc in items):
            return finding("unable_to_verify", "Evidence identity needs human reconciliation with the application.", refs)
        return finding("satisfied", "Usable evidence matches the application details.", refs)

    director = " ".join(str(application.get(key) or "") for key in ("director_first_name", "director_surname")).strip()

    def accounts(items):
        if not items:
            return finding("missing", "Neither accounts nor management information were supplied.")
        usable = []
        for doc in items:
            data = doc["data"]
            if doc["document_type"] == "management_information":
                if date.fromisoformat(data["period_start"]) > date.fromisoformat(data["period_end"]) or date.fromisoformat(data["period_end"]) > submission_date:
                    return finding("unable_to_verify", "Management information dates need internal verification.", [doc["source_key"]])
                usable.append(doc)
            else:
                accounting_date = date.fromisoformat(data["accounting_year"])
                if accounting_date > submission_date:
                    return finding("unable_to_verify", "Accounting date is after submission.", [doc["source_key"]])
                if accounting_date >= months_before(submission_date, 12):
                    usable.append(doc)
        if not usable:
            return finding("stale", "Filed accounts are older than 12 months at submission.",
                           [doc["source_key"] for doc in items],
                           "Supply accounts with an accounting date within the 12 months before submission, or management information.")
        return matched(usable, "company_name", application.get("company_name"))

    def address(items):
        name_check = matched(items, "holder_name", director)
        if name_check.status != "satisfied":
            return name_check
        same_person = [doc for doc in items if _normalized(doc["data"]["holder_name"]) == _normalized(director)]
        return matched(same_person, "address", application.get("director_residential_address"))

    def bank_statements(items):
        ownership = matched(items, "account_owner", application.get("company_name"))
        if ownership.status != "satisfied":
            return ownership
        matching = [doc for doc in items if _normalized(doc["data"]["account_owner"]) == _normalized(application.get("company_name"))]
        if len(matching) != len(items):
            return finding("unable_to_verify", "Some statement owners need reconciliation with the application.",
                           [doc["source_key"] for doc in items])
        return bank_coverage(matching, submission_date)

    checks = {
        "application_details": _application_finding(application),
        "director_id": assess_group(["director_id"], lambda docs: matched(docs, "holder_name", director)),
        "proof_of_address": assess_group(["proof_of_address"], address),
        "bank_statements": assess_group(["bank_statement"], bank_statements),
        "accounts_or_management_information": assess_group(["annual_accounts", "management_information"], accounts),
        "vat_returns": assess_group(["vat_returns"], lambda docs: matched(docs, "company_name", application.get("company_name"))),
        "existing_borrowing": assess_group(["existing_borrowing"], lambda docs: matched(docs, "company_name", application.get("company_name"))),
        "personal_guarantee": assess_group(["personal_guarantee"], lambda docs: matched(
            [doc for doc in docs if doc["data"]["executed"]], "guarantor_name", director)),
    }
    return assess_readiness(ReadinessInput(
        loan_type=application["loan_type"], submission_date=submission_date,
        loan_amount=application.get("loan_amount"),
        vat_registered=application.get("vat_registered"),
        has_existing_borrowing=application.get("has_existing_borrowing"), findings=checks,
    ))
