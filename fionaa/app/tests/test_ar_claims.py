import json
from pathlib import Path

import pytest

from fionaa.ar_claims import (
    APPROVAL_VARIABLE,
    DOCUMENTATION_VARIABLE,
    ELIGIBILITY_VARIABLE,
    LOAN_TYPE_LEAF_VARIABLES,
    approval_assertion,
    documentation_assertion,
    eligibility_assertion,
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
