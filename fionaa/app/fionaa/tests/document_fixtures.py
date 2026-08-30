"""Mocked annual_accounts/bank_statement documents for GoodAI Consulting and
Goodman's Consulting Limited -- the two companies already used in
fionaa_eval_dataset.jsonl's fullapp-active-company-unsecured-loan and
fullapp-dissolved-company-unsecured-loan scenarios (see graph.py's
load_application/DOCUMENT_SPECS and schemas.py's AnnualAccountsSchema/
BankStatementSchema).

Five scenarios, one fixture (or fixture pair) each:

  1. GOODAI_ANNUAL_ACCOUNTS_HAPPY -- happy path: turnover_current_year
     matches GOODAI_APPLICATION's annual_turnover.
  2. GOODAI_ANNUAL_ACCOUNTS_TURNOVER_MISMATCH -- exception: materially
     different from GOODAI_APPLICATION's annual_turnover.
  3. GOODAI_BANK_STATEMENTS_RECENT -- happy path: most recent statement is
     within BANK_STATEMENT_FRESHNESS_WINDOW of TODAY.
  4. GOODAI_BANK_STATEMENTS_STALE -- exception: most recent statement falls
     outside that window.
  5. GOODMANS_ANNUAL_ACCOUNTS / GOODMANS_BANK_STATEMENTS -- both predate
     GOODMANS_INSOLVENCY_DATE (28 Dec 2017), the only documents that could
     plausibly exist for a company dissolved since -- and, relative to
     TODAY, inherently far outside (3)'s freshness window too.

check_against_policy/check_financial_assessment (graph.py) now consume
annual_accounts/bank_statements for real -- verified both locally (this
module's own schema/scenario-property tests in test_document_fixtures.py)
and against a real deployed Runtime run (2026-08-30, see EVALS.md's
"Follow-up (post-Path-2)" section): the real check_bank_statements_recent_and_sufficient
tool call and the real financial_assessment turnover cross-check both fired
as designed. That same real run is also what surfaced GOODAI_ANNUAL_ACCOUNTS_HAPPY's
original accounting_year as chronologically impossible against the real
company's actual incorporation date -- see that fixture's own comment for
the fix and why GOODAI_APPLICATION's trading_start_date was deliberately
left as-is.
"""

from datetime import date, timedelta

# Pinned rather than date.today() -- keeps "is this within 3 months"
# comparisons deterministic regardless of when tests actually run. Matches
# this fixture set's authoring date.
TODAY = date(2026, 8, 30)
BANK_STATEMENT_FRESHNESS_WINDOW = timedelta(days=90)  # ~3 months
GOODMANS_INSOLVENCY_DATE = date(2017, 12, 28)


# ---------------------------------------------------------------------------
# GoodAI Consulting (17161121) -- active company.
# See fionaa_eval_dataset.jsonl's fullapp-active-company-unsecured-loan.
# ---------------------------------------------------------------------------

GOODAI_APPLICATION = {
    "applicant_name": "Steve Goodman",
    "year_of_birth": "1979",
    "company_name": "GoodAI Consulting",
    "company_address": "Manor Road, Ruislip",
    "loan_type": "unsecured-business-loans",
    "loan_purpose": "working capital",
    "loan_amount": 20000,
    "loan_term": 24,
    "companies_house_registered": True,
    "industry": "Management consultancy",
    "trading_start_date": "2019-04-01",
    "annual_turnover": 250000,
    "annual_profit": 62000,
    "income_decrease_expected": False,
    "accepts_card_payments": True,
    "invoices_owed": 8000,
    "monthly_expenses": 4200,
    "monthly_rent_or_mortgage": 1800,
    "num_dependants": 1,
    "monthly_childcare_expenses": 400,
    "monthly_non_business_income": 0,
    "monthly_other_household_income": 0,
    "director_first_name": "Steve",
    "director_surname": "Goodman",
    "director_percentage_control": 100.0,
    "director_mobile_phone": "+44 7700 900123",
    "director_residential_status": "Owner With Mortgage",
    "director_residential_address": "12 Manor Road, Ruislip",
}

# Scenario 1 (happy path): turnover_current_year (250000) matches
# GOODAI_APPLICATION["annual_turnover"] (250000).
#
# accounting_year is a shortened first accounting reference period ending
# 2026-06-30, not a full prior year -- a real end-to-end run against the
# deployed Runtime (2026-08-30, see EVALS.md's "Follow-up (post-Path-2)"
# section) surfaced that the real GoodAI Consulting (17161121) used
# throughout this eval suite was actually incorporated 2026-04-16 per its
# live Companies House record, not "trading since 2019" as originally
# assumed here. The original accounting_year=2025-12-31 predated the
# company's own incorporation -- financial_assessment correctly flagged
# that as a chronological impossibility and rejected what was meant to be
# the happy-path scenario. Fixed by moving accounting_year to just after
# incorporation and nulling out every *_last_year figure, since no prior
# year exists yet for a company this young. (GOODAI_APPLICATION's own
# trading_start_date below is left as 2019 -- it's never staged against the
# real deployed Runtime, only used in local fake-store tests/ad hoc
# real-Bedrock checks that don't call the live Companies House lookup, so
# it doesn't hit this same conflict. Matching it to reality would also flip
# the "6-12 months minimum trading history" general-policy check to
# INELIGIBLE, given the real company is only ~4.5 months old as of TODAY --
# a separate, deliberately out-of-scope fix.)
GOODAI_ANNUAL_ACCOUNTS_HAPPY = {
    "company_name": "GoodAI Consulting",
    "director": "Steve Goodman",
    "registered_address": "Manor Road, Ruislip",
    "registration_number": "17161121",
    "accounting_year": "2026-06-30",
    "turnover_current_year": 250000,
    "operating_profit_current_year": 68000,
    "profit_current_year": 62000,
    "turnover_last_year": None,
    "operating_profit_last_year": None,
    "profit_last_year": None,
    "tangible_fixed_assets_current_year": 15000,
    "debtors_current_year": 18000,
    "cash_at_bank_current_year": 42000,
    "tangible_fixed_assets_last_year": None,
    "debtors_last_year": None,
    "cash_at_bank_last_year": None,
}

# Scenario 2 (exception): turnover_current_year (95000) is ~62% below
# GOODAI_APPLICATION["annual_turnover"] (250000) -- materially different,
# not just noisy rounding.
GOODAI_ANNUAL_ACCOUNTS_TURNOVER_MISMATCH = {
    **GOODAI_ANNUAL_ACCOUNTS_HAPPY,
    "turnover_current_year": 95000,
}

# Scenario 3 (happy path): most recent statement (end_date 2026-08-25) is
# 5 days before TODAY -- well within BANK_STATEMENT_FRESHNESS_WINDOW.
GOODAI_BANK_STATEMENTS_RECENT = [
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2026-06-01", "end_date": "2026-06-30",
        "balance": 18342.50, "payments_in": 22000.0, "payments_out": 19500.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2026-07-01", "end_date": "2026-07-31",
        "balance": 20811.75, "payments_in": 24500.0, "payments_out": 22000.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2026-08-01", "end_date": "2026-08-25",
        "balance": 23120.40, "payments_in": 19800.0, "payments_out": 17500.0,
    },
]

# Scenario 4 (exception): most recent statement (end_date 2025-10-31) is
# 303 days before TODAY -- well outside BANK_STATEMENT_FRESHNESS_WINDOW.
GOODAI_BANK_STATEMENTS_STALE = [
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2025-08-01", "end_date": "2025-08-31",
        "balance": 14500.00, "payments_in": 20500.0, "payments_out": 18200.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2025-09-01", "end_date": "2025-09-30",
        "balance": 16210.60, "payments_in": 21300.0, "payments_out": 19100.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "start_date": "2025-10-01", "end_date": "2025-10-31",
        "balance": 17890.25, "payments_in": 22100.0, "payments_out": 19700.0,
    },
]


# ---------------------------------------------------------------------------
# Goodman's Consulting Limited (08139267) -- dissolved/insolvent
# 28 Dec 2017 (GOODMANS_INSOLVENCY_DATE). See fionaa_eval_dataset.jsonl's
# fullapp-dissolved-company-unsecured-loan.
# ---------------------------------------------------------------------------

GOODMANS_APPLICATION = {
    "applicant_name": "Steven Goodman",
    "year_of_birth": "1975",
    "company_name": "Goodman's Consulting Limited",
    "company_address": "14 Oak Avenue, Uxbridge",
    "loan_type": "unsecured-business-loans",
    "loan_purpose": "equipment purchase",
    "loan_amount": 15000,
    "loan_term": 24,
    "companies_house_registered": True,
    "industry": "Management consultancy",
    "trading_start_date": "2010-03-01",
    "annual_turnover": 180000,
    "annual_profit": 15000,
    "income_decrease_expected": True,
    "accepts_card_payments": False,
    "invoices_owed": 22000,
    "monthly_expenses": 5200,
    "monthly_rent_or_mortgage": 1400,
    "num_dependants": 2,
    "monthly_childcare_expenses": 600,
    "monthly_non_business_income": 0,
    "monthly_other_household_income": 0,
    "director_first_name": "Steven",
    "director_surname": "Goodman",
    "director_percentage_control": 100.0,
    "director_mobile_phone": "+44 7700 900456",
    "director_residential_status": "Owner With Mortgage",
    "director_residential_address": "14 Oak Avenue, Uxbridge",
}

# Scenario 5: accounting_year (2016-12-31) predates GOODMANS_INSOLVENCY_DATE
# (2017-12-28) -- the last full-year accounts that could plausibly exist for
# a company dissolved shortly after.
GOODMANS_ANNUAL_ACCOUNTS = {
    "company_name": "Goodman's Consulting Limited",
    "director": "Steven Goodman",
    "registered_address": "14 Oak Avenue, Uxbridge",
    "registration_number": "08139267",
    "accounting_year": "2016-12-31",
    "turnover_current_year": 180000,
    "operating_profit_current_year": 21000,
    "profit_current_year": 15000,
    "turnover_last_year": 205000,
    "operating_profit_last_year": 30000,
    "profit_last_year": 24000,
    "tangible_fixed_assets_current_year": 5000,
    "debtors_current_year": 22000,
    "cash_at_bank_current_year": 4000,
    "tangible_fixed_assets_last_year": 6000,
    "debtors_last_year": 19000,
    "cash_at_bank_last_year": 9000,
}

# Scenario 5: all three end_dates (2017-09-30 / 2017-10-31 / 2017-11-30)
# predate GOODMANS_INSOLVENCY_DATE (2017-12-28).
GOODMANS_BANK_STATEMENTS = [
    {
        "account_owner": "Goodman's Consulting Limited", "bank_name": "Big Bank UK", "account_number": "50987654",
        "start_date": "2017-09-01", "end_date": "2017-09-30",
        "balance": 3210.40, "payments_in": 15200.0, "payments_out": 16100.0,
    },
    {
        "account_owner": "Goodman's Consulting Limited", "bank_name": "Big Bank UK", "account_number": "50987654",
        "start_date": "2017-10-01", "end_date": "2017-10-31",
        "balance": 2450.10, "payments_in": 14800.0, "payments_out": 15560.0,
    },
    {
        "account_owner": "Goodman's Consulting Limited", "bank_name": "Big Bank UK", "account_number": "50987654",
        "start_date": "2017-11-01", "end_date": "2017-11-30",
        "balance": 1875.65, "payments_in": 13900.0, "payments_out": 14475.0,
    },
]
