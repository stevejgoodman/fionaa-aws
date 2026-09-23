"""How must facts be sent so AR treats them as premises and still solves?

diagnose_inconclusive.py (run 2026-09-23) found the real blocker, and it
is not the missing derived facts:

  * With 16-18 facts every claim came back `tooComplex` -- the engine
    declined to solve at all, so no amount of *additional* facts could
    have helped; each one makes the request bigger.
  * With a single fact the engine solved, but put that fact in `claims`,
    not `premises` ("premises: []"). Asked whether the fact and the
    assertion can hold together, it correctly answers "satisfiable".
    Nothing can ever be *entailed* when premises are empty.

Both point at how the request is built (policy_consistency.check), not at
ar_facts.py. This probe measures two things independently:

  scaling/*   How many guard_content facts it takes to trigger
              `tooComplex`, holding the claim fixed. Finds the ceiling.
  qualifier/* Whether the facts become real premises under four ways of
              sending them, at a size well under that ceiling:
                guard_content_blocks  one guard_content block per fact
                                      (what production does today)
                query_blocks          one query block per fact
                query_single          all facts in one query block
                guard_single          all facts in one guard_content block
              `query` is the API's premise channel; the comment in
              policy_consistency.py says it was unreliable, which this
              either confirms or overturns with a recorded run.

Read the `premises` column first: a strategy that leaves it empty cannot
decide any claim, however few facts it sends.

Requires the AIOps profile (see README) and charges one ApplyGuardrail
request per row. Writes premise-handling-probe.json beside this script.
"""
import asyncio
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent
POLICY_DIR = RESULTS_DIR.parents[1] / "agentcore" / "automated-reasoning"
from fionaa.ar_claims import render_fact
from fionaa.ar_findings import diagnose, finding_kinds

PRODUCT = "unsecured-business-loans"
CLAIM = "isSubstantivelyEligible is false."

# Ordered most- to least-relevant to CLAIM: isSubstantivelyEligible's rule
# reads isUKBased, isEligibleBusinessType, isApplicantAgeEligible,
# loanAmountInRange, meetsMinimumTradingHistory, loanTermInRange and
# personalGuaranteeRequirementMet. The first seven entries are therefore
# the claim-relevant set; everything after supports hasRequiredDocuments
# instead and is noise for this claim. Scaling takes prefixes of this
# list, so a `tooComplex` at N means N facts were too many even when they
# were the N most relevant ones.
FACTS_BY_RELEVANCE = [
    ("tradingHistoryMonths", 3),
    ("loanAmount", 40000),
    ("loanTermMonths", 24),
    ("applicantAge", 45),
    ("businessType", "BusinessType_LIMITED_COMPANY"),
    ("isUKBased", True),
    ("hasPersonalGuarantee", True),
    ("isRegisteredInUK", True),
    ("hasUKAddress", True),
    ("hasCompaniesHouseMatch", True),
    ("hasValidDirectorID", True),
    ("directorIDType", "DirectorIDType_PASSPORT"),
    ("hasRecentBankStatements", True),
    ("bankStatementsMonthsCount", 3),
    ("mostRecentStatementAgeDays", 10),
    ("hasProofOfDirectorAddress", True),
    ("hasAccountsOrManagementInformation", True),
]
SCALING_SIZES = (1, 2, 4, 7, 10, 13, 17)
CLAIM_SCOPED = dict(FACTS_BY_RELEVANCE[:7])


def _block(text, qualifier):
    return {"text": {"text": text, "qualifiers": [qualifier]}}


def content_for(facts: dict, strategy: str) -> list[dict]:
    sentences = [render_fact(name, value) for name, value in sorted(facts.items())]
    if strategy == "guard_content_blocks":
        blocks = [_block(sentence, "guard_content") for sentence in sentences]
    elif strategy == "query_blocks":
        blocks = [_block(sentence, "query") for sentence in sentences]
    elif strategy == "query_single":
        blocks = [_block(" ".join(sentences), "query")]
    elif strategy == "guard_single":
        blocks = [_block(" ".join(sentences), "guard_content")]
    else:
        raise ValueError(strategy)
    return blocks + [_block(CLAIM, "guard_content")]


def run(client, identifier, version, facts, strategy):
    response = client.apply_guardrail(
        guardrailIdentifier=identifier, guardrailVersion=str(version),
        source="OUTPUT", outputScope="FULL", content=content_for(facts, strategy))
    findings = [
        finding
        for assessment in response.get("assessments", [])
        for finding in assessment.get("automatedReasoningPolicy", {}).get("findings", [])
    ]
    premises = [
        " ".join(premise.get("logic", "").split())
        for finding in findings
        for body in [next(iter(finding.values()), {})]
        if isinstance(body, dict)
        for premise in body.get("translation", {}).get("premises", [])
    ]
    return {"kinds": finding_kinds(findings), "premises": premises,
            "diagnostics": diagnose(facts, findings), "findings": findings}


async def main():
    import boto3
    bindings = json.loads((POLICY_DIR / "runtime-bindings.json").read_text())
    binding = bindings[PRODUCT]
    client = boto3.client("bedrock-runtime")
    identifier, version = binding["guardrail_id"], binding["guardrail_version"]

    cases = [(f"scaling/{size:02d}_facts", dict(FACTS_BY_RELEVANCE[:size]), "guard_content_blocks")
             for size in SCALING_SIZES]
    cases += [(f"qualifier/{strategy}", CLAIM_SCOPED, strategy)
              for strategy in ("guard_content_blocks", "query_blocks", "query_single", "guard_single")]

    results = []
    print(f"{'case':34} {'kinds':22} premises")
    for name, facts, strategy in cases:
        try:
            outcome = await asyncio.to_thread(run, client, identifier, version, facts, strategy)
        except Exception as exc:  # a probe: report and keep going
            outcome = {"kinds": [f"error:{type(exc).__name__}"], "premises": [], "diagnostics": {}}
        results.append({"case": name, "strategy": strategy, "fact_count": len(facts),
                        "facts": facts, "claim": CLAIM, **outcome})
        (RESULTS_DIR / "premise-handling-probe.json").write_text(
            json.dumps(results, indent=2, default=str) + "\n")
        print(f"{name:34} {','.join(outcome['kinds']):22} {outcome['premises']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
