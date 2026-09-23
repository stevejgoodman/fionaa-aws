"""Why does a clear-cut application still come back "inconclusive"?

Reproduces the blocker against the live guardrails and prints the cause
for each case, using ar_findings.diagnose(): the policy variables the AR
engine had to vary (because no fact pinned them), and the facts that were
sent but never became premises (lost in translation).

The cases are ordered as an experiment, not a test suite:

Every case uses the same application: complete and unambiguous apart from
a 3-month trading history against a 6-month minimum, so each claim has a
definite right answer.

  *_scoped / composed  what production sends now -- each claim gets only
                       the facts its own rules reach, as query-qualified
                       premises, and the approval claim is composed from
                       the other two claims' subjects. These must decide.
  unscoped_*           what production sent before: every fact for the
                       loan type, to every claim. Kept as the contrast;
                       expected to stay undecided.

Requires AWS credentials for the account that owns the guardrails named in
runtime-bindings.json, and charges one standalone ApplyGuardrail request
per case. Deploys nothing and writes nothing outside this directory.
"""
import asyncio
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent
POLICY_DIR = RESULTS_DIR.parents[1] / "agentcore" / "automated-reasoning"
from fionaa.ar_claims import (
    DOCUMENTATION_VARIABLE,
    ELIGIBILITY_VARIABLE,
    approval_premise_facts,
    facts_for_claim,
)
from fionaa.domain.applications import LoanType
from fionaa.policy_consistency import PolicyConsistencyChecker

PRODUCT = "unsecured-business-loans"

# A complete, unambiguous application apart from the trading history: every
# fact ar_facts.build_derived_facts() computes today for this product, with
# tradingHistoryMonths deliberately below the 6-month minimum.
BASE_FACTS = {
    "applicantAge": 45,
    "bankStatementsMonthsCount": 3,
    "businessType": "BusinessType_LIMITED_COMPANY",
    "directorIDType": "DirectorIDType_PASSPORT",
    "hasAccountsOrManagementInformation": True,
    "hasCompaniesHouseMatch": True,
    "hasProofOfDirectorAddress": True,
    "hasRecentBankStatements": True,
    "hasUKAddress": True,
    "hasValidDirectorID": True,
    "isRegisteredInUK": True,
    "isUKBased": True,
    "loanAmount": 40000,
    "loanTermMonths": 24,
    "mostRecentStatementAgeDays": 10,
    "tradingHistoryMonths": 3,
    # Declared on the application form; both false, so neither conditional
    # document rule needs the document variable behind it.
    "isVATRegistered": False,
    "hasExistingBorrowing": False,
}

LOAN_TYPE = LoanType.unsecured_business_loans


def _scoped(claim_variable):
    return facts_for_claim(LOAN_TYPE, claim_variable, BASE_FACTS)


# The first two cases are what production sends now: facts scoped to the
# claim's own rules, as a query-qualified premise block. The `unscoped_`
# cases are what it sent before, kept as the contrast -- they are expected
# to stay undecided.
CASES = [
    ("eligibility_scoped", _scoped(ELIGIBILITY_VARIABLE), "isSubstantivelyEligible is false."),
    ("documentation_scoped", _scoped(DOCUMENTATION_VARIABLE), "hasRequiredDocuments is true."),
    # Composed from the other two claims' subjects, never from their facts.
    ("approval_composed",
     approval_premise_facts({"eligible": "ineligible", "documentation_gaps": []}),
     "approvalMeetsCoveredPolicy is false."),
    ("unscoped_eligibility", BASE_FACTS, "isSubstantivelyEligible is false."),
    ("unscoped_documentation", BASE_FACTS, "hasRequiredDocuments is true."),
]


async def main():
    bindings = json.loads((POLICY_DIR / "runtime-bindings.json").read_text())
    checker = PolicyConsistencyChecker(bindings)
    policy = (POLICY_DIR / f"{PRODUCT}.txt").read_text()
    results = []
    for name, facts, claim in CASES:
        result = await checker.check(PRODUCT, policy, facts, claim)
        diagnostics = result.get("diagnostics", {})
        results.append({"case": name, "facts": facts, "claim": claim, **result})
        (RESULTS_DIR / "inconclusive-diagnosis.json").write_text(
            json.dumps(results, indent=2, default=str) + "\n")
        print(f"{name:24} status={result['status']:18} "
              f"kinds={diagnostics.get('finding_kinds')} "
              f"unbound={diagnostics.get('unbound_variables')} "
              f"not_translated={diagnostics.get('facts_not_translated')}", flush=True)
    # Only the production-path cases gate the exit code. The unscoped_*
    # rows reproduce the old behaviour on purpose and are expected to stay
    # undecided; they are the contrast, not a target.
    undecided = [row["case"] for row in results
                 if not row["case"].startswith("unscoped_")
                 and row["status"] not in ("valid", "invalid")]
    if undecided:
        print("undecided:", ", ".join(undecided))
    return 1 if undecided else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
