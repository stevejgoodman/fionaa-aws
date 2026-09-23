import json
import re
from pathlib import Path

import pytest

from fionaa.ar_claims import (
    APPROVAL_VARIABLE,
    CLAIM_VARIABLES,
    KNOWN_UNCOVERED_VARIABLES,
    DOCUMENTATION_VARIABLE,
    ELIGIBILITY_VARIABLE,
    LOAN_TYPE_LEAF_VARIABLES,
    approval_assertion,
    approval_premise_facts,
    documentation_assertion,
    eligibility_assertion,
    facts_for_claim,
    leaf_facts_for_loan_type,
    render_fact,
)
from fionaa.domain.applications import LoanType

AR_POLICY_DIR = Path(__file__).resolve().parents[2] / "agentcore" / "automated-reasoning"

# secured-business-loans binds to the reviewed, flat-shaped definition file,
# not secured-generated-definition.json (a raw build-workflow artifact in a
# different nested shape) -- see runtime-bindings.json.
DEFINITION_FILE_BY_LOAN_TYPE = {
    LoanType.unsecured_business_loans: "unsecured-business-loans-definition.json",
    LoanType.secured_business_loans: "secured-reviewed-definition.json",
    LoanType.revolving_credit_facility: "revolving-credit-facility-definition.json",
    LoanType.invoice_discounting: "invoice-discounting-definition.json",
    LoanType.invoice_factoring: "invoice-factoring-definition.json",
}


def test_render_fact():
    assert render_fact("isUKBased", True) == "isUKBased is true."
    assert render_fact("isUKBased", False) == "isUKBased is false."
    assert render_fact("loanAmount", 15000) == "loanAmount is 15000."
    assert render_fact("businessType", "BusinessType_LIMITED_COMPANY") == \
        "businessType is BusinessType_LIMITED_COMPANY."


@pytest.mark.parametrize("eligible,expected", [
    ("eligible", "isSubstantivelyEligible is true."),
    ("ineligible", "isSubstantivelyEligible is false."),
])
def test_eligibility_assertion(eligible, expected):
    assert eligibility_assertion({"eligible": eligible}) == expected


def test_eligibility_assertion_omitted_when_inconclusive_or_missing():
    assert eligibility_assertion({"eligible": "inconclusive"}) is None
    assert eligibility_assertion({}) is None


@pytest.mark.parametrize("gaps,expected", [
    ([], "hasRequiredDocuments is true."),
    (None, "hasRequiredDocuments is true."),
    (["Bank statements missing."], "hasRequiredDocuments is false."),
])
def test_documentation_assertion_never_omitted(gaps, expected):
    policy_check = {"documentation_gaps": gaps} if gaps is not None else {}
    assert documentation_assertion(policy_check) == expected


@pytest.mark.parametrize("outcome,expected", [
    ("approved", "approvalMeetsCoveredPolicy is true."),
    ("rejected", "approvalMeetsCoveredPolicy is false."),
])
def test_approval_assertion(outcome, expected):
    assert approval_assertion({"outcome": outcome}) == expected


def test_approval_assertion_omitted_for_referred_or_missing():
    assert approval_assertion({"outcome": "referred"}) is None
    assert approval_assertion({}) is None


def test_leaf_facts_for_loan_type_filters_out_other_loan_types_and_today():
    derived_facts = {
        "today": "2026-09-16",
        "loanAmount": 15000,
        "annualTurnover": 180000,
        "isUKBased": True,
    }

    unsecured = leaf_facts_for_loan_type(LoanType.unsecured_business_loans, derived_facts)
    invoice = leaf_facts_for_loan_type(LoanType.invoice_factoring, derived_facts)

    assert unsecured == {"loanAmount": 15000, "isUKBased": True}
    assert invoice == {"annualTurnover": 180000, "isUKBased": True}
    assert "today" not in unsecured and "today" not in invoice


@pytest.mark.parametrize("loan_type", list(LoanType))
def test_leaf_variables_are_actually_declared_by_the_real_ar_policy(loan_type):
    """Drift guard: agentcore/automated-reasoning/ isn't bundled into the
    deployed runtime (see ar_claims.py's module docstring), so
    LOAN_TYPE_LEAF_VARIABLES is hand-maintained -- this catches it silently
    going stale against the real, reviewed policy definitions."""
    definition = json.loads((AR_POLICY_DIR / DEFINITION_FILE_BY_LOAN_TYPE[loan_type]).read_text())
    declared = {variable["name"] for variable in definition["variables"]}

    assert LOAN_TYPE_LEAF_VARIABLES[loan_type] <= declared
    assert {ELIGIBILITY_VARIABLE, DOCUMENTATION_VARIABLE, APPROVAL_VARIABLE} <= declared


def _leaf_variables(definition: dict) -> set[str]:
    """Variables no rule defines -- the ones the AR engine can only know if
    they're sent as facts. Everything else (isEligibleBusinessType,
    loanAmountInRange, personalGuaranteeRequirementMet ...) is defined by a
    rule of the form `(= <name> <expression>)` and derived by the solver.

    `(=> hasCompaniesHouseMatch ...)` is an implication, not a definition,
    so hasCompaniesHouseMatch stays a leaf -- hence the anchored match on
    `(= ` rather than a substring search for the variable name."""
    declared = {variable["name"] for variable in definition["variables"]}
    defined = set()
    for rule in definition["rules"]:
        match = re.match(r"\(=\s+([A-Za-z_][A-Za-z0-9_]*)\b", rule["expression"].strip())
        if match and match.group(1) in declared:
            defined.add(match.group(1))
    return declared - defined


@pytest.mark.parametrize("loan_type", list(LoanType))
def test_uncovered_leaf_variables_match_the_written_down_gap(loan_type):
    """The AR fact-coverage gap, asserted exactly rather than described.

    A leaf variable ar_facts.py doesn't compute reaches the engine unbound,
    and any claim depending on it can then only come back "satisfiable" ->
    "inconclusive". KNOWN_UNCOVERED_VARIABLES is that gap written down; this
    fails both ways -- when a policy revision declares a new leaf nobody
    wired up, and when a fact is added without striking it off the list."""
    definition = json.loads((AR_POLICY_DIR / DEFINITION_FILE_BY_LOAN_TYPE[loan_type]).read_text())

    uncovered = _leaf_variables(definition) - LOAN_TYPE_LEAF_VARIABLES[loan_type]

    assert uncovered == set(KNOWN_UNCOVERED_VARIABLES[loan_type])


# Measured against the live guardrails on 2026-09-23: 13 premises decide,
# 17 come back "tooComplex" (evals/automated_reasoning/
# premise-handling-probe.json). Held well below 13 here -- a claim that
# creeps up to the measured ceiling is already failing intermittently by
# the time anyone notices.
PREMISE_BUDGET = 13


def _rule_dependencies(definition: dict) -> dict[str, set[str]]:
    """{variable: variables its defining rule mentions}, for the rules of
    the form `(= <name> <expression>)` that define one variable from
    others. An implication like `(=> hasCompaniesHouseMatch ...)` defines
    nothing, so it contributes no edge."""
    declared = {variable["name"] for variable in definition["variables"]}
    dependencies: dict[str, set[str]] = {}
    for rule in definition["rules"]:
        expression = rule["expression"].strip()
        match = re.match(r"\(=\s+([A-Za-z_][A-Za-z0-9_]*)\b", expression)
        if match and match.group(1) in declared:
            mentioned = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression[match.end():]))
            dependencies.setdefault(match.group(1), set()).update(mentioned & declared)
    return dependencies


def _reachable(variable: str, dependencies: dict[str, set[str]], seen=frozenset()) -> set[str]:
    reached = {variable}
    for dependency in dependencies.get(variable, ()):
        if dependency not in seen:
            reached |= _reachable(dependency, dependencies, seen | {variable})
    return reached


@pytest.mark.parametrize("loan_type", list(LoanType))
@pytest.mark.parametrize("claim_variable", [ELIGIBILITY_VARIABLE, DOCUMENTATION_VARIABLE])
def test_claim_variables_match_the_real_policy_dependency_graph(loan_type, claim_variable):
    """Drift guard for the per-claim fact scoping. Too narrow and a claim
    is starved of a fact its own rules need; too wide and the request
    grows back towards the size the engine refuses to solve. Recomputed
    here from the reviewed definitions, which aren't bundled into the
    deployed runtime -- same reason LOAN_TYPE_LEAF_VARIABLES is
    hand-maintained."""
    definition = json.loads((AR_POLICY_DIR / DEFINITION_FILE_BY_LOAN_TYPE[loan_type]).read_text())

    expected = _reachable(claim_variable, _rule_dependencies(definition)) - {claim_variable}

    assert set(CLAIM_VARIABLES[loan_type][claim_variable]) == expected


@pytest.mark.parametrize("loan_type", list(LoanType))
def test_no_claim_can_be_sent_more_facts_than_the_engine_will_solve(loan_type):
    """Every fact ar_facts.py can currently produce, offered to each claim:
    what reaches the engine must stay inside the measured budget. This is
    the regression guard on the actual blocker -- adding derived facts
    (the KNOWN_UNCOVERED_VARIABLES work) is safe only while this holds."""
    every_fact = {name: True for name in LOAN_TYPE_LEAF_VARIABLES[loan_type]} | {"today": "2026-09-23"}

    for claim_variable in (ELIGIBILITY_VARIABLE, DOCUMENTATION_VARIABLE):
        sent = facts_for_claim(loan_type, claim_variable, every_fact)
        assert "today" not in sent
        assert len(sent) <= PREMISE_BUDGET, f"{loan_type.value}/{claim_variable}: {sorted(sent)}"

    # The approval claim never grows with the facts: it is always composed
    # from the other two claims' subjects.
    assert len(approval_premise_facts({"eligible": "eligible", "documentation_gaps": []})) == 2


def test_facts_for_claim_of_an_unmapped_claim_sends_nothing():
    """Fail visibly rather than silently sending a too-large request."""
    assert facts_for_claim(LoanType.unsecured_business_loans, APPROVAL_VARIABLE,
                           {"loanAmount": 15000}) == {}


@pytest.mark.parametrize("eligible,expected", [
    ("eligible", {"hasRequiredDocuments": True, "isSubstantivelyEligible": True}),
    ("ineligible", {"hasRequiredDocuments": True, "isSubstantivelyEligible": False}),
    ("inconclusive", {"hasRequiredDocuments": True}),
    (None, {"hasRequiredDocuments": True}),
])
def test_approval_premise_facts(eligible, expected):
    assert approval_premise_facts({"eligible": eligible, "documentation_gaps": []}) == expected


def test_approval_premise_facts_follow_the_documentation_gaps():
    facts = approval_premise_facts({"eligible": "eligible", "documentation_gaps": ["Missing."]})

    assert facts["hasRequiredDocuments"] is False
