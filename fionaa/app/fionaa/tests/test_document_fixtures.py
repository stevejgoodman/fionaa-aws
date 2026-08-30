"""Validates document_fixtures.py's mocked annual_accounts/bank_statement
documents against schemas.py's schemas, and checks each fixture actually
embodies the scenario property its name claims (turnover match/mismatch,
statement recency/staleness, pre-insolvency dating).

This does NOT test that graph.py detects or flags any of these -- see
document_fixtures.py's module docstring: check_financial_assessment doesn't
consume annual_accounts/bank_statements yet. These are data-integrity tests
for the fixtures themselves, run locally with no AWS/Bedrock dependency, so
a broken fixture is caught here rather than surfacing later as a confusing
failure once real consumption logic exists.
"""

from datetime import date

import pytest

import graph as g
from schemas import ApplicationFormSchema, AnnualAccountsSchema, BankStatementSchema

import document_fixtures as fx
from fakes import FakePolicyDocs, FakeRuntime, FakeStore


# ---------------------------------------------------------------------------
# Schema conformance -- every fixture must validate as-is
# ---------------------------------------------------------------------------

APPLICATIONS = {
    "goodai": fx.GOODAI_APPLICATION,
    "goodmans": fx.GOODMANS_APPLICATION,
}

ANNUAL_ACCOUNTS = {
    "goodai_happy": fx.GOODAI_ANNUAL_ACCOUNTS_HAPPY,
    "goodai_turnover_mismatch": fx.GOODAI_ANNUAL_ACCOUNTS_TURNOVER_MISMATCH,
    "goodmans": fx.GOODMANS_ANNUAL_ACCOUNTS,
}

BANK_STATEMENTS = {
    "goodai_recent": fx.GOODAI_BANK_STATEMENTS_RECENT,
    "goodai_stale": fx.GOODAI_BANK_STATEMENTS_STALE,
    "goodmans": fx.GOODMANS_BANK_STATEMENTS,
}


@pytest.mark.parametrize("name", APPLICATIONS, ids=list(APPLICATIONS))
def test_application_fixture_matches_schema(name):
    ApplicationFormSchema.model_validate(APPLICATIONS[name])


@pytest.mark.parametrize("name", ANNUAL_ACCOUNTS, ids=list(ANNUAL_ACCOUNTS))
def test_annual_accounts_fixture_matches_schema(name):
    AnnualAccountsSchema.model_validate(ANNUAL_ACCOUNTS[name])


@pytest.mark.parametrize(
    "name,statement",
    [(name, s) for name, statements in BANK_STATEMENTS.items() for s in statements],
    ids=[f"{name}[{s['end_date']}]" for name, statements in BANK_STATEMENTS.items() for s in statements],
)
def test_bank_statement_fixture_matches_schema(name, statement):
    BankStatementSchema.model_validate(statement)


def test_each_bank_statement_group_has_three_statements():
    """The task calls for 3 bank statements per company -- GoodAI gets two
    variants (recent/stale) of 3, Goodman's gets one (all pre-insolvency)."""
    for name, statements in BANK_STATEMENTS.items():
        assert len(statements) == 3, name


# ---------------------------------------------------------------------------
# Scenario 1/2 -- turnover match vs. material mismatch
# ---------------------------------------------------------------------------

def test_goodai_happy_path_turnover_matches_application():
    assert (
        fx.GOODAI_ANNUAL_ACCOUNTS_HAPPY["turnover_current_year"]
        == fx.GOODAI_APPLICATION["annual_turnover"]
    )


def test_goodai_turnover_mismatch_is_material():
    accounts_turnover = fx.GOODAI_ANNUAL_ACCOUNTS_TURNOVER_MISMATCH["turnover_current_year"]
    application_turnover = fx.GOODAI_APPLICATION["annual_turnover"]
    relative_diff = abs(accounts_turnover - application_turnover) / application_turnover
    # "Materially different" per the task -- not just rounding/estimation
    # noise. 25% is comfortably past anything a reasonable estimate error
    # would produce; the fixture is ~62% different.
    assert relative_diff > 0.25, f"mismatch fixture only differs by {relative_diff:.0%}, not material"


# ---------------------------------------------------------------------------
# Scenario 3/4 -- bank statement recency vs. staleness
# ---------------------------------------------------------------------------

def _most_recent_end_date(statements: list[dict]) -> date:
    return max(date.fromisoformat(s["end_date"]) for s in statements)


def test_goodai_recent_statements_within_freshness_window():
    most_recent = _most_recent_end_date(fx.GOODAI_BANK_STATEMENTS_RECENT)
    assert fx.TODAY - most_recent <= fx.BANK_STATEMENT_FRESHNESS_WINDOW


def test_goodai_stale_statements_outside_freshness_window():
    most_recent = _most_recent_end_date(fx.GOODAI_BANK_STATEMENTS_STALE)
    assert fx.TODAY - most_recent > fx.BANK_STATEMENT_FRESHNESS_WINDOW


# ---------------------------------------------------------------------------
# Scenario 5 -- Goodman's documents predate its insolvency date
# ---------------------------------------------------------------------------

def test_goodmans_annual_accounts_predate_insolvency():
    accounting_year = date.fromisoformat(fx.GOODMANS_ANNUAL_ACCOUNTS["accounting_year"])
    assert accounting_year < fx.GOODMANS_INSOLVENCY_DATE


def test_goodmans_bank_statements_all_predate_insolvency():
    for statement in fx.GOODMANS_BANK_STATEMENTS:
        assert date.fromisoformat(statement["end_date"]) < fx.GOODMANS_INSOLVENCY_DATE


def test_goodmans_documents_are_also_stale_relative_to_today():
    """Not an independent scenario -- just confirms the natural consequence
    of (5): documents from before a 2017 insolvency are, today, far outside
    the freshness window (3)/(4) established for GoodAI."""
    most_recent = _most_recent_end_date(fx.GOODMANS_BANK_STATEMENTS)
    assert fx.TODAY - most_recent > fx.BANK_STATEMENT_FRESHNESS_WINDOW


# ---------------------------------------------------------------------------
# Integration: these fixtures actually load through load_application, not
# just through direct schema validation above -- catches any mismatch
# between the store.list_keys prefix convention and how these fixtures
# would actually be staged (input/annual_accounts*.json, input/bank_statement*.json).
# ---------------------------------------------------------------------------

def test_goodai_happy_path_documents_load_through_load_application():
    store = FakeStore(
        {
            "input/application.json": fx.GOODAI_APPLICATION,
            "input/annual_accounts_2025.json": fx.GOODAI_ANNUAL_ACCOUNTS_HAPPY,
            "input/bank_statement_2026-06.json": fx.GOODAI_BANK_STATEMENTS_RECENT[0],
            "input/bank_statement_2026-07.json": fx.GOODAI_BANK_STATEMENTS_RECENT[1],
            "input/bank_statement_2026-08.json": fx.GOODAI_BANK_STATEMENTS_RECENT[2],
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.load_application({}, runtime)

    assert result["application"] == fx.GOODAI_APPLICATION
    assert result["annual_accounts"] == [fx.GOODAI_ANNUAL_ACCOUNTS_HAPPY]
    assert len(result["bank_statements"]) == 3
    assert {s["end_date"] for s in result["bank_statements"]} == {
        s["end_date"] for s in fx.GOODAI_BANK_STATEMENTS_RECENT
    }


def test_goodmans_pre_insolvency_documents_load_through_load_application():
    store = FakeStore(
        {
            "input/application.json": fx.GOODMANS_APPLICATION,
            "input/annual_accounts_2016.json": fx.GOODMANS_ANNUAL_ACCOUNTS,
            "input/bank_statement_2017-09.json": fx.GOODMANS_BANK_STATEMENTS[0],
            "input/bank_statement_2017-10.json": fx.GOODMANS_BANK_STATEMENTS[1],
            "input/bank_statement_2017-11.json": fx.GOODMANS_BANK_STATEMENTS[2],
        }
    )
    runtime = FakeRuntime(g.AgentContext(store=store, policy_docs=FakePolicyDocs(), tools=[]))

    result = g.load_application({}, runtime)

    assert result["annual_accounts"] == [fx.GOODMANS_ANNUAL_ACCOUNTS]
    assert len(result["bank_statements"]) == 3
