"""Explicit live smoke runner, outside the unit-test directory.

Run from the project root with AWS_PROFILE=AIOps and AWS_DEFAULT_REGION=us-east-1:
  app/fionaa/.venv/bin/python agentcore/automated-reasoning/test_live_guardrail.py
Uses synthetic facts only. Charges ApplyGuardrail requests. Does not deploy.
"""
import asyncio
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app" / "fionaa"
sys.path.insert(0, str(APP))
from policy_consistency import PolicyConsistencyChecker, policy_digest


async def main():
    root = Path(__file__).resolve().parent
    text = (root / "secured-business-loans.txt").read_text()
    checker = PolicyConsistencyChecker({"secured-business-loans": {
        "guardrail_id": "bwcgqb1tsa07", "guardrail_version": "1",
        "policy_sha256": policy_digest(text),
    }})
    cases = [
        ("amount_in_range", {"loan_amount": 40000},
         {"summary": "The requested GBP 40000 is within the secured loan amount range."}, True),
        ("amount_below_minimum", {"loan_amount": 24000},
         {"summary": "The requested GBP 24000 is within the secured loan amount range."}, False),
        ("trading_12_months", {"tradingHistoryMonths": 12},
         {"summary": "The business meets the minimum secured-loan trading history requirement."}, True),
        ("trading_11_months", {"tradingHistoryMonths": 11},
         {"summary": "The business meets the minimum secured-loan trading history requirement."}, False),
        ("statements_89_days", {"bankStatementsMonthsCount": 3, "mostRecentStatementAgeDays": 89},
         {"summary": "The supplied statements satisfy the policy's bank statement recency and coverage requirements."}, True),
        ("statements_90_days", {"bankStatementsMonthsCount": 3, "mostRecentStatementAgeDays": 90},
         {"summary": "The supplied statements satisfy the policy's bank statement recency and coverage requirements."}, False),
        ("invalid_term_approval", {"loan_amount": 40000, "loan_term": 84},
         {"outcome": "approved", "reason": "This approval meets all covered secured lending policy requirements."}, False),
        ("missing_collateral_approval", {"hasProofOfCollateralOwnershipValuation": False},
         {"outcome": "approved", "reason": "This approval meets all covered secured lending policy requirements."}, False),
    ]
    results = []
    for name, facts, claim, expected in cases:
        result = await checker.check("secured-business-loans", text, facts, claim)
        results.append({"case": name, "expected_pass": expected, **result})
        print(name, "expected", expected, "actual", result["passed"], result["status"], flush=True)
        (root / "live-results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(r["passed"] == r["expected_pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
