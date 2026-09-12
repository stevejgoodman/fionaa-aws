"""Explicit live boundary tests for the four additional guardrail policies.

Uses synthetic facts and the production checker. Requires AWS credentials;
charges standalone ApplyGuardrail requests. Does not deploy the runtime.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "app" / "fionaa"))
from policy_consistency import PolicyConsistencyChecker


def cases(product):
    rows = []

    def add(name, facts, text, valid):
        rows.append((name, facts, {"summary": text}, "valid" if valid else "invalid"))

    for age in (89, 90):
        add(f"statement_age_{age}", {"bankStatementsMonthsCount": 3, "mostRecentStatementAgeDays": age},
            "The bank statements meet the policy's coverage and recency requirements.", age == 89)
    if product == "unsecured-business-loans":
        for months in (5, 6):
            add(f"trading_{months}", {"tradingHistoryMonths": months},
                "The business meets the minimum trading history requirement.", months == 6)
        for amount in (25000, 25001):
            add(f"guarantee_{amount}", {"loanAmount": amount, "hasPersonalGuarantee": False},
                "The personal guarantee requirement is satisfied for this loan.", amount == 25000)
        for term in (60, 61):
            add(f"term_{term}", {"loanTermMonths": term},
                "The requested loan term is within the allowed policy range.", term == 60)
        for amount in (1000, 999, 500000, 500001):
            add(f"amount_{amount}", {"loanAmount": amount},
                "The requested loan amount is within the policy range.", 1000 <= amount <= 500000)
    elif product == "revolving-credit-facility":
        for months in (11, 12):
            add(f"trading_{months}", {"tradingHistoryMonths": months},
                "The business meets the minimum trading history requirement.", months == 12)
        for amount in (10000, 9999, 1000000, 1000001):
            add(f"amount_{amount}", {"loanAmount": amount},
                "The requested facility amount is within the policy range.", 10000 <= amount <= 1000000)
        for secured in (False, True):
            add(f"conditional_security_{secured}", {
                "hasRecentBankStatements": True, "hasValidDirectorID": True,
                "hasProofOfDirectorAddress": True, "hasAccountsOrManagementInformation": True,
                "isVATRegistered": False, "hasBorrowingDetails": True,
                "isSecuredFacility": secured, "hasSecurityAssetDetails": False,
            }, "All required documents for this revolving facility have been supplied.", not secured)
    else:
        minimum = 100000 if product == "invoice-discounting" else 50000
        for turnover in (minimum - 1, minimum):
            add(f"turnover_{turnover}", {"annualTurnover": turnover},
                "The annual turnover meets the minimum turnover requirement.", turnover == minimum)
        for rate in (69, 70, 90, 91):
            add(f"advance_{rate}", {"advanceRatePercent": rate},
                "The proposed advance rate is within the policy's allowed advance band.", 70 <= rate <= 90)
        for term in (5, 6):
            add(f"contract_{term}", {"contractTermMonths": term},
                "The contract duration meets the minimum contract period requirement.", term == 6)
        add("missing_aged_debt_report", {"hasAgedDebtReports": False},
            "All required documentation has been supplied.", False)
    # A real approval claim must not be validated with missing required documents.
    rows.append(("approval_missing_documents", {"hasRequiredDocuments": False},
                 {"outcome": "approved", "reason": "This approval meets all covered policy requirements."}, "invalid"))
    return rows


async def main():
    bindings = json.loads((ROOT / "runtime-bindings.json").read_text())
    checker = PolicyConsistencyChecker(bindings)
    results = []
    for product in ("unsecured-business-loans", "revolving-credit-facility", "invoice-discounting", "invoice-factoring"):
        source = (ROOT / f"{product}.txt").read_text()
        for name, facts, claim, expected in cases(product):
            result = await checker.check(product, source, facts, claim)
            kinds = [next(iter(f)) for f in result["findings"]]
            matched = (result["passed"] and kinds and all(k == "valid" for k in kinds)) if expected == "valid" else (not result["passed"] and "invalid" in kinds)
            row = {"product": product, "case": name, "facts": facts, "claim": claim,
                   "expected": expected, "matched": bool(matched), **result}
            results.append(row)
            (ROOT / "product-live-results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(product, name, expected, kinds, "PASS" if matched else "FAIL", flush=True)
    return 0 if all(r["matched"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
