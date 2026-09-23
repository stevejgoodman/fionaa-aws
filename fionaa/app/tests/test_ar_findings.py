"""Shapes here mirror real ApplyGuardrail responses captured in
evals/automated_reasoning/*-results.json (note the newline-wrapped `logic`
strings -- AR pretty-prints them, so parsing has to collapse whitespace)."""
from fionaa.ar_findings import (
    diagnose,
    finding_kinds,
    scenario_assignments,
    translated_premise_variables,
    unbound_variables,
)


def _statements(*logic):
    return {"statements": [{"logic": item} for item in logic]}


def _satisfiable(true_logic, false_logic, premises=(), untranslated=()):
    return {"satisfiable": {
        "translation": {
            "premises": [{"logic": item} for item in premises],
            "untranslatedPremises": [{"text": item} for item in untranslated],
        },
        "claimsTrueScenario": _statements(*true_logic),
        "claimsFalseScenario": _statements(*false_logic),
    }}


def test_scenario_assignments_parses_every_statement_form():
    scenario = _statements(
        "(= isVATRegistered\n   false)",
        "(= loanAmount\n   40000.0)",
        "(= businessType\n   BusinessType_LIMITED_COMPANY)",
        "(not hasVATReturns)",
        "hasValidDirectorID",
    )

    assert scenario_assignments(scenario) == {
        "isVATRegistered": "false",
        "loanAmount": "40000.0",
        "businessType": "BusinessType_LIMITED_COMPANY",
        "hasVATReturns": "false",
        "hasValidDirectorID": "true",
    }


def test_scenario_assignments_of_nothing():
    assert scenario_assignments(None) == {}
    assert scenario_assignments({}) == {}


def test_unbound_variables_are_the_ones_the_engine_had_to_vary():
    """tradingHistoryMonths is identical in both scenarios -- it was pinned
    by a fact. hasPersonalGuarantee differs, so it's what left the claim
    undecided."""
    findings = [_satisfiable(
        true_logic=("(= tradingHistoryMonths\n   9)", "hasPersonalGuarantee"),
        false_logic=("(= tradingHistoryMonths\n   9)", "(not hasPersonalGuarantee)"),
    )]

    assert unbound_variables(findings) == ["hasPersonalGuarantee"]


def test_a_variable_present_in_only_one_scenario_counts_as_unbound():
    findings = [_satisfiable(
        true_logic=("hasVATReturns",),
        false_logic=(),
    )]

    assert unbound_variables(findings) == ["hasVATReturns"]


def test_decided_findings_contribute_no_unbound_variables():
    """A valid/invalid finding carries a full scenario too, but it was
    decided -- whatever it left free didn't matter, so diffing it would
    name irrelevant variables."""
    valid = {"valid": {
        "translation": {"premises": [{"logic": "(= tradingHistoryMonths\n   9)"}]},
        "claimsTrueScenario": _statements("(= hasPersonalGuarantee\n   true)"),
    }}

    assert unbound_variables([valid]) == []


def test_translated_premise_variables_and_facts_not_translated():
    findings = [_satisfiable(
        true_logic=("hasVATReturns",),
        false_logic=("(not hasVATReturns)",),
        premises=("(= tradingHistoryMonths\n   9)", "(= loanAmount\n   40000.0)"),
        untranslated=("today is 2026-09-23.",),
    )]

    assert translated_premise_variables(findings) == {"tradingHistoryMonths", "loanAmount"}

    result = diagnose({"tradingHistoryMonths": 9, "loanAmount": 40000, "isUKBased": True}, findings)

    assert result == {
        "finding_kinds": ["satisfiable"],
        "unbound_variables": ["hasVATReturns"],
        # Sent but never became a premise: a wording/translation problem,
        # not a missing derivation.
        "facts_not_translated": ["isUKBased"],
        "untranslated_premises": ["today is 2026-09-23."],
    }


def test_untranslatable_findings_report_the_kind_and_claim_nothing_else():
    """tooComplex/noTranslations translate nothing, so every fact would
    look untranslated -- the kind is the whole diagnosis."""
    result = diagnose({"loanAmount": 40000}, [{"tooComplex": {}}, {"noTranslations": {}}])

    assert result["finding_kinds"] == ["noTranslations", "tooComplex"]
    assert result["facts_not_translated"] == []
    assert result["unbound_variables"] == []


def test_finding_kinds_deduplicates_across_findings():
    assert finding_kinds([{"valid": {}}, {"valid": {}}, {"invalid": {}}]) == ["invalid", "valid"]
