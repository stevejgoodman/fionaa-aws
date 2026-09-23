"""Why an Automated Reasoning check came back inconclusive.

PolicyConsistencyChecker.check() reports "inconclusive" whenever the AR
engine could not prove the assertion either way. That status on its own
says nothing about *why*, and the two causes need opposite responses:

  * A variable the policy declares was never sent as a fact, so the engine
    is free to choose its value. Varying it flips the assertion, so the
    engine answers "satisfiable" -- true in some scenarios, false in
    others. The fix is to derive that fact (see ar_facts.py); until then
    the claim can never be decided, no matter how clear-cut the
    application is.
  * A fact *was* sent but AR's NL-to-logic translator dropped it, so it
    never became a premise. The fix is to the wording, not to the facts.

This module tells the two apart from the response AR already returns, and
names the variables involved. Nothing here changes a status or a decision
-- it only annotates, so the gap is visible in decision/validation.json
and measurable by the evals rather than being inferred by hand.

A "satisfiable" finding carries a claimsTrueScenario and a
claimsFalseScenario, each a complete set of variable assignments. Any
variable assigned differently between the two is one the engine had to
vary to reach both answers: that is exactly the set of free variables
this claim needed and did not get.
"""
from __future__ import annotations

import re

# Scenario/premise statements come back as s-expressions, e.g.
# "(= isVATRegistered\n   false)", "(not hasVATReturns)", or a bare
# "hasValidDirectorID" for a true boolean.
_ASSIGNMENT = re.compile(r"^\(=\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)\)$")
_NEGATION = re.compile(r"^\(not\s+([A-Za-z_][A-Za-z0-9_]*)\)$")
_BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Every finding kind the guardrail can return that carries a "translation"
# block (see the GuardrailAutomatedReasoning*Finding shapes). Kinds without
# one -- tooComplex, noTranslations -- are reported by name alone, which is
# itself the diagnosis.
_TRANSLATED_KINDS = ("valid", "invalid", "satisfiable", "impossible", "translationAmbiguous")


def _statement_variable(logic: str) -> tuple[str, str] | None:
    """('isVATRegistered', 'false') for one scenario/premise statement."""
    collapsed = " ".join(str(logic).split())
    match = _ASSIGNMENT.match(collapsed)
    if match:
        return match.group(1), match.group(2).strip()
    match = _NEGATION.match(collapsed)
    if match:
        return match.group(1), "false"
    if _BARE.match(collapsed):
        return collapsed, "true"
    return None


def scenario_assignments(scenario: dict | None) -> dict[str, str]:
    """{variable: assigned value} for one claimsTrue/FalseScenario."""
    assignments = {}
    for statement in (scenario or {}).get("statements", []):
        parsed = _statement_variable(statement.get("logic", ""))
        if parsed is not None:
            assignments[parsed[0]] = parsed[1]
    return assignments


def unbound_variables(findings: list[dict]) -> list[str]:
    """Variables the engine had to vary to satisfy the claim both ways.

    Only "satisfiable" findings are examined: a valid/invalid finding was
    decided, so whatever it left free did not matter to the outcome. Its
    scenarios still assign every variable, which is why they are ignored
    here rather than diffed -- diffing them would name variables that are
    genuinely irrelevant to the claim."""
    names: set[str] = set()
    for finding in findings:
        body = finding.get("satisfiable")
        if not body:
            continue
        true_case = scenario_assignments(body.get("claimsTrueScenario"))
        false_case = scenario_assignments(body.get("claimsFalseScenario"))
        names |= {
            name for name in set(true_case) | set(false_case)
            if true_case.get(name) != false_case.get(name)
        }
    return sorted(names)


def _translations(findings: list[dict]) -> list[dict]:
    return [
        finding[kind]["translation"]
        for finding in findings
        for kind in _TRANSLATED_KINDS
        if isinstance(finding.get(kind), dict) and isinstance(finding[kind].get("translation"), dict)
    ]


def translated_premise_variables(findings: list[dict]) -> set[str]:
    """Variable names AR actually turned into premises."""
    names = set()
    for translation in _translations(findings):
        for premise in translation.get("premises", []):
            parsed = _statement_variable(premise.get("logic", ""))
            if parsed is not None:
                names.add(parsed[0])
    return names


def finding_kinds(findings: list[dict]) -> list[str]:
    return sorted({kind for finding in findings for kind in finding})


def diagnose(facts: dict, findings: list[dict]) -> dict:
    """Annotation for one check result -- see the module docstring.

    `facts_not_translated` is the other half of the diagnosis: a fact that
    was sent but never became a premise was lost in translation, not
    missing from ar_facts.py. Both lists are variable names from the
    policy's own vocabulary, never applicant values."""
    translations = _translations(findings)
    translated = translated_premise_variables(findings)
    untranslated = [
        premise.get("text", "")
        for translation in translations
        for premise in translation.get("untranslatedPremises", [])
    ]
    return {
        "finding_kinds": finding_kinds(findings),
        "unbound_variables": unbound_variables(findings),
        # Only meaningful when AR produced a translation at all: with a
        # tooComplex/noTranslations finding nothing was translated, and
        # listing every fact as "not translated" would read as a fact-wiring
        # problem when the finding kind is already the whole diagnosis.
        "facts_not_translated": sorted(set(facts) - translated) if translations else [],
        "untranslated_premises": untranslated,
    }
