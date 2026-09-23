"""Do all three fully-covered products now decide every claim?

unsecured-business-loans, secured-business-loans and
revolving-credit-facility have no entries left in
ar_claims.KNOWN_UNCOVERED_VARIABLES: every leaf variable their policies
declare is derived by ar_facts.py. This checks that end to end, through
the production path -- build_derived_facts, then facts_for_claim, then
the production checker -- with each product's conditional requirements
deliberately switched ON, which is the case that previously left a
variable unbound:

  * VAT registered, so a VAT return is genuinely required
  * existing borrowing declared, so borrowing details are required
  * a loan above GBP25,000, so a personal guarantee is required
  * a revolving facility declared secured, so security details are required

Each product is run twice: complete (every claim must be valid) and
deficient, with one required document withheld (the documentation claim
must be invalid, not inconclusive -- the engine must be able to say no).

Requires the AIOps profile; charges one ApplyGuardrail request per claim.
"""
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent
POLICY_DIR = RESULTS_DIR.parents[1] / "agentcore" / "automated-reasoning"
from fionaa.ar_claims import (
    DOCUMENTATION_VARIABLE,
    ELIGIBILITY_VARIABLE,
    approval_premise_facts,
    facts_for_claim,
    render_fact,
)
from fionaa.ar_facts import build_derived_facts
from fionaa.domain.applications import LoanType
from fionaa.policy_consistency import PolicyConsistencyChecker

TODAY = date(2026, 9, 23)
POLICY_FILE = {
    LoanType.unsecured_business_loans: "unsecured-business-loans.txt",
    LoanType.secured_business_loans: "secured-business-loans.txt",
    LoanType.revolving_credit_facility: "revolving-credit-facility.txt",
}


def application(loan_type: LoanType) -> dict:
    """A complete, eligible applicant for this product, with every
    conditional requirement switched on."""
    base = {
        "loan_type": loan_type.value,
        "applicant_name": "Steven Goodman", "year_of_birth": "1972",
        "company_name": "Example Consulting Limited",
        "director_first_name": "Steven", "director_surname": "Goodman",
        "director_residential_address": "3 Manor Road, Ruislip",
        "trading_start_date": "2012-07-11", "annual_turnover": 180000,
        "loan_term": 24, "vat_registered": True, "has_existing_borrowing": True,
    }
    if loan_type is LoanType.unsecured_business_loans:
        # Above GBP25,000, so the personal guarantee is required.
        return {**base, "loan_amount": 40000, "personal_guarantee_agreed": True}
    if loan_type is LoanType.secured_business_loans:
        return {**base, "loan_amount": 250000, "collateral_asset_type": "property"}
    return {**base, "loan_amount": 50000, "facility_is_secured": True}


DOCUMENTS = {
    "annual_accounts": [{"accounting_year": "2026-03-31",
                         "registered_address": "3 Manor Road, Ruislip"}],
    "bank_statements": [{"end_date": "2026-09-01"}, {"end_date": "2026-08-01"},
                        {"end_date": "2026-07-01"}],
    "director_id": [{"document_kind": "passport", "holder_name": "Steven Goodman",
                     "expiry_date": "2030-01-01"}],
    "proof_of_address": [{"document_kind": "utility bill", "holder_name": "Steven Goodman",
                          "address": "3 Manor Road, Ruislip", "issue_date": "2026-08-01"}],
    "vat_returns": [{"business_name": "Example Consulting Limited"}],
    "existing_borrowing": [{"lender_name": "Example Bank"}],
    "security_assets": [{"asset_type": "property"}],
}
# Withheld in the deficient run: the document each product's conditional
# rules newly depend on.
WITHHELD = {
    LoanType.unsecured_business_loans: "vat_returns",
    LoanType.secured_business_loans: "security_assets",
    LoanType.revolving_credit_facility: "security_assets",
}


def facts_for(loan_type: LoanType, withhold: str | None) -> dict:
    documents = {name: ([] if name == withhold else docs) for name, docs in DOCUMENTS.items()}
    return build_derived_facts(
        application(loan_type), documents["annual_accounts"], documents["bank_statements"],
        {"found": True, "summary": "EXAMPLE CONSULTING LTD"}, TODAY,
        director_id=documents["director_id"], proof_of_address=documents["proof_of_address"],
        vat_returns=documents["vat_returns"], existing_borrowing=documents["existing_borrowing"],
        security_assets=documents["security_assets"])


async def main():
    bindings = json.loads((POLICY_DIR / "runtime-bindings.json").read_text())
    checker = PolicyConsistencyChecker(bindings)
    results = []
    failures = []
    for loan_type in POLICY_FILE:
        policy = (POLICY_DIR / POLICY_FILE[loan_type]).read_text()
        for run, withhold in (("complete", None), ("deficient", WITHHELD[loan_type])):
            derived = facts_for(loan_type, withhold)
            eligible = "eligible"
            documents_complete = withhold is None
            checks = [
                (ELIGIBILITY_VARIABLE, facts_for_claim(loan_type, ELIGIBILITY_VARIABLE, derived),
                 render_fact(ELIGIBILITY_VARIABLE, True), "valid"),
                (DOCUMENTATION_VARIABLE, facts_for_claim(loan_type, DOCUMENTATION_VARIABLE, derived),
                 render_fact(DOCUMENTATION_VARIABLE, True), "valid" if documents_complete else "invalid"),
                ("approvalMeetsCoveredPolicy",
                 approval_premise_facts({"eligible": eligible,
                                         "documentation_gaps": [] if documents_complete else ["Missing."]}),
                 render_fact("approvalMeetsCoveredPolicy", documents_complete), "valid"),
            ]
            for variable, sent, assertion, expected in checks:
                result = await checker.check(loan_type.value, policy, sent, assertion)
                ok = result["status"] == expected
                if not ok:
                    failures.append(f"{loan_type.value}/{run}/{variable}")
                results.append({"product": loan_type.value, "run": run, "variable": variable,
                                "facts_sent": sorted(sent), "assertion": assertion,
                                "expected": expected, "matched": ok, **result})
                (RESULTS_DIR / "product-coverage-results.json").write_text(
                    json.dumps(results, indent=2, default=str) + "\n")
                print(f"{loan_type.value:26} {run:10} {variable:28} "
                      f"premises={len(sent):2} {result['status']:14} "
                      f"{'PASS' if ok else 'FAIL expected ' + expected}", flush=True)
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
