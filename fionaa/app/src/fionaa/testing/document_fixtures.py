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
  6. GOODAI_DIRECTOR_ID / GOODAI_PROOF_OF_ADDRESS -- happy path: identity
     documents for GoodAI's director, matching the name/address declared in
     GOODAI_APPLICATION, so triage's director_id/proof_of_address checks
     have something to find rather than reporting them as missing.

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
    # 1972, not 1979 -- corrected 2026-09-22 against a real check_companies_house
    # run: the live Companies House record for 17161121 shows Dr Steven Goodman
    # born October 1972. The original 1979 value caused a genuine 7-year DOB
    # mismatch that correctly routed the graph to reject_no_company before
    # policy_check/financial_assessment/web_search ever ran -- same class of
    # issue as GOODAI_ANNUAL_ACCOUNTS_HAPPY's incorporation-date fix above.
    "year_of_birth": "1972",
    "company_name": "GoodAI Consulting",
    "company_address": "Manor Road, Ruislip",
    "loan_type": "unsecured-business-loans",
    "loan_purpose": "working capital",
    "loan_amount": 20000,
    "loan_term": 24,
    "companies_house_registered": True,
    "industry": "Management consultancy",
    # 2026-04-16, not 2019-04-01 -- corrected 2026-09-22 to match the real
    # Companies House incorporation date for 17161121 (see
    # GOODAI_ANNUAL_ACCOUNTS_HAPPY's comment below, which already made this
    # same fix for accounting_year but left this field as the deliberately
    # out-of-scope "2019" value). The company cannot have traded before it
    # was incorporated -- companies_house/policy_check both correctly
    # flagged the 7-year gap as a discrepancy. Matching reality here means
    # the real ~5-month trading history now surfaces honestly instead of a
    # fabricated multi-year one, which does flip policy_check's trading-
    # history clause to ineligible -- that's the real answer, not a bug.
    "trading_start_date": "2026-04-16",
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
    # 25%, not 100.0 -- also corrected against the live record: Companies
    # House shows Dr Steven Goodman holding 25-50% shares/voting rights
    # (Yi Fang, the other director/PSC, holds the remaining 75-100%), not
    # sole 100% control as originally assumed here.
    "director_percentage_control": 25.0,
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
# year exists yet for a company this young. GOODAI_APPLICATION's own
# trading_start_date was subsequently also corrected to match (2026-09-22,
# against a real full-pipeline run) -- it now reads 2026-04-16, not the
# original 2019-04-01. That does flip the "6-12 months minimum trading
# history" general-policy check to INELIGIBLE, given the real company is
# only ~5 months old as of TODAY -- that's the correct, real answer once
# the fixture stops claiming 7 years of trading history it doesn't have,
# not a regression to work around.
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
#
# The first statement's start_date was widened from 2026-06-01 to 2026-05-01
# (2026-09-22, against a real full-pipeline/triage run): evidence_checks.py's
# statement_coverage_facts measures 3 months as distinct calendar-day
# coverage counting back from the latest end_date, not a count of monthly
# files. With start_date=06-01 the three statements only spanned 86 days
# (2026-06-01 to 2026-08-25), which is a few days short of the 3-calendar-
# month mark measured back from 2026-08-26 (2026-05-26) -- triage correctly
# reported "2 months of distinct coverage" even though 3 separate monthly
# files were supplied. Starting a month earlier closes that gap for real.
GOODAI_BANK_STATEMENTS_RECENT = [
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2026-05-01", "end_date": "2026-06-30",
        "balance": 18342.50, "payments_in": 22000.0, "payments_out": 19500.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2026-07-01", "end_date": "2026-07-31",
        "balance": 20811.75, "payments_in": 24500.0, "payments_out": 22000.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2026-08-01", "end_date": "2026-08-25",
        "balance": 23120.40, "payments_in": 19800.0, "payments_out": 17500.0,
    },
]

# Scenario 4 (exception): most recent statement (end_date 2025-10-31) is
# 303 days before TODAY -- well outside BANK_STATEMENT_FRESHNESS_WINDOW.
GOODAI_BANK_STATEMENTS_STALE = [
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2025-08-01", "end_date": "2025-08-31",
        "balance": 14500.00, "payments_in": 20500.0, "payments_out": 18200.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2025-09-01", "end_date": "2025-09-30",
        "balance": 16210.60, "payments_in": 21300.0, "payments_out": 19100.0,
    },
    {
        "account_owner": "GoodAI Consulting", "bank_name": "Big Bank UK", "account_number": "40123456",
        "address": "3 Manor Road, Ruislip, Middlesex",
        "start_date": "2025-10-01", "end_date": "2025-10-31",
        "balance": 17890.25, "payments_in": 22100.0, "payments_out": 19700.0,
    },
]

# Scenario 5 (happy path): director ID and proof of address for GoodAI's
# director, added 2026-09-22 -- triage previously reported these as
# "missing" simply because no fixture existed for them yet, not because of
# any real gap. holder_name must normalize-match GOODAI_APPLICATION's
# director_first_name/director_surname ("Steve Goodman"); proof-of-address's
# `address` must normalize-match GOODAI_APPLICATION's
# director_residential_address ("12 Manor Road, Ruislip") exactly -- see
# evidence_checks.py's director_id/proof_of_address.
GOODAI_DIRECTOR_ID = [
    {
        "document_kind": "passport",
        "holder_name": "Steve Goodman",
        "expiry_date": "2031-04-16",
    },
]

GOODAI_PROOF_OF_ADDRESS = [
    {
        "document_kind": "utility bill",
        "holder_name": "Steve Goodman",
        "address": "12 Manor Road, Ruislip",
        # Within ADDRESS_MAX_AGE_DAYS (90) of TODAY.
        "issue_date": "2026-08-20",
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
    "company_address": "3 Manor Road, Ruislip",
    "loan_type": "unsecured-business-loans",
    "loan_purpose": "equipment purchase",
    "loan_amount": 15000,
    "loan_term": 24,
    "companies_house_registered": True,
    "industry": "Management consultancy",
    # Matches the real company's actual Companies House incorporation date
    # (11 July 2012) -- a live companies_house run against the original
    # "2010-03-01" surfaced that date as chronologically impossible (the
    # applicant claiming to trade under this legal entity before it existed)
    # and returned found=false, same class of issue GOODAI_ANNUAL_ACCOUNTS_HAPPY's
    # own comment above documents for that scenario.
    "trading_start_date": "2012-07-11",
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
    "director_residential_address": "3 Manor Road, Ruislip",
}

# Scenario 5: accounting_year (2016-12-31) predates GOODMANS_INSOLVENCY_DATE
# (2017-12-28) -- the last full-year accounts that could plausibly exist for
# a company dissolved shortly after.
GOODMANS_ANNUAL_ACCOUNTS = {
    "company_name": "Goodman's Consulting Limited",
    "director": "Steven Goodman",
    "registered_address": "3 Manor Road, Ruislip",
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
        "address": "3 Manor Road, Ruislip",
        "start_date": "2017-09-01", "end_date": "2017-09-30",
        "balance": 3210.40, "payments_in": 15200.0, "payments_out": 16100.0,
    },
    {
        "account_owner": "Goodman's Consulting Limited", "bank_name": "Big Bank UK", "account_number": "50987654",
        "address": "3 Manor Road, Ruislip",
        "start_date": "2017-10-01", "end_date": "2017-10-31",
        "balance": 2450.10, "payments_in": 14800.0, "payments_out": 15560.0,
    },
    {
        "account_owner": "Goodman's Consulting Limited", "bank_name": "Big Bank UK", "account_number": "50987654",
        "address": "3 Manor Road, Ruislip",
        "start_date": "2017-11-01", "end_date": "2017-11-30",
        "balance": 1875.65, "payments_in": 13900.0, "payments_out": 14475.0,
    },
]
