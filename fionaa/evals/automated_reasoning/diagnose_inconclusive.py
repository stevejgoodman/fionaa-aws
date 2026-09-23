"""Why does a clear-cut application still come back "inconclusive"?

Reproduces the blocker against the live guardrails and prints the cause
for each case, using ar_findings.diagnose(): the policy variables the AR
engine had to vary (because no fact pinned them), and the facts that were
sent but never became premises (lost in translation).

The cases are ordered as an experiment, not a test suite:

  shortfall_as_is        the reported failure -- a 3-month trading history
                         against a 6-month minimum, asserted ineligible.
                         By the rules alone this should be decidable
                         without hasPersonalGuarantee, so whatever this
                         prints is the real cause.
  shortfall_plus_pg      the same, with hasPersonalGuarantee supplied. If
                         this one decides and the one above doesn't, the
                         free variable really was the cause.
  shortfall_only_trading the single relevant fact and nothing else, to see
                         whether the other facts are adding translation
                         noise.
  docs_as_is /           the documentation claim, without and with the two
  docs_plus_declarations declarations the application form already has
                         (isVATRegistered/hasExistingBorrowing). Both
                         false discharge the conditional document rules,
                         so the second should decide where the first can't.

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
}

CASES = [
    ("shortfall_as_is", BASE_FACTS, "isSubstantivelyEligible is false."),
    ("shortfall_plus_pg", {**BASE_FACTS, "hasPersonalGuarantee": True},
     "isSubstantivelyEligible is false."),
    ("shortfall_only_trading", {"tradingHistoryMonths": 3},
     "isSubstantivelyEligible is false."),
    ("docs_as_is", BASE_FACTS, "hasRequiredDocuments is true."),
    ("docs_plus_declarations",
     {**BASE_FACTS, "isVATRegistered": False, "hasExistingBorrowing": False},
     "hasRequiredDocuments is true."),
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
    undecided = [row for row in results if row["status"] not in ("valid", "invalid")]
    return 1 if undecided else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
