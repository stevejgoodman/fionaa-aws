"""Explicit live smoke runner, outside the unit-test directory.

Run from the project root with AWS_PROFILE=AIOps and AWS_DEFAULT_REGION=us-east-1:
  app/.venv/bin/python evals/automated_reasoning/test_live_guardrail.py
Uses synthetic facts only. Charges ApplyGuardrail requests. Does not deploy.
"""
import asyncio
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent
POLICY_DIR = RESULTS_DIR.parents[1] / "agentcore" / "automated-reasoning"
from fionaa.policy_consistency import PolicyConsistencyChecker, policy_digest


async def main():
    text = (POLICY_DIR / "secured-business-loans.txt").read_text()
    checker = PolicyConsistencyChecker({"secured-business-loans": {
        "guardrail_id": "bwcgqb1tsa07", "guardrail_version": "1",
        "policy_sha256": policy_digest(text),
    }})
    cases = [
        ("amount_in_range", {"loanAmount": 40000},
         "loanAmountInRange is true.", True),
        ("amount_below_minimum", {"loanAmount": 24000},
         "loanAmountInRange is true.", False),
        ("trading_12_months", {"tradingHistoryMonths": 12},
         "meetsMinimumTradingHistory is true.", True),
        ("trading_11_months", {"tradingHistoryMonths": 11},
         "meetsMinimumTradingHistory is true.", False),
        ("statements_89_days", {"bankStatementsMonthsCount": 3, "mostRecentStatementAgeDays": 89},
         "hasRecentBankStatements is true.", True),
        ("statements_90_days", {"bankStatementsMonthsCount": 3, "mostRecentStatementAgeDays": 90},
         "hasRecentBankStatements is true.", False),
        ("invalid_term_approval", {"loanAmount": 40000, "loanTermMonths": 84},
         "approvalMeetsCoveredPolicy is true.", False),
        ("missing_collateral_approval", {"hasProofOfCollateralOwnershipValuation": False},
         "approvalMeetsCoveredPolicy is true.", False),
    ]
    results = []
    for name, facts, assertion, expected in cases:
        result = await checker.check("secured-business-loans", text, facts, assertion)
        results.append({"case": name, "expected_pass": expected, **result})
        print(name, "expected", expected, "actual", result["passed"], result["status"], flush=True)
        (RESULTS_DIR / "live-results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(r["passed"] == r["expected_pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
