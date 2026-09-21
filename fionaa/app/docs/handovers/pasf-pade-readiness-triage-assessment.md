# PASF/PADE Automation Assessment: Unsecured-Business-Loan Readiness Triage

## Summary

This assesses the end-to-end unsecured-business-loan intake-to-decision pipeline: document
loading and validation, agentic evidence gathering (Companies House, policy, financial, web
search), advisory decision synthesis, deterministic claim validation, and — the step this
run specifically confirmed exists — a deterministic **triage router** that sends each
application to one of three destinations: `underwriter` (all essentials satisfied, adverse
recommendations included), `return_to_applicant` (a known, correctable evidence gap), or
`internal_hold` (missing/failed verification, held for internal attention rather than pushed
to the customer). A human always makes the actual credit decision; the AI only produces an
advisory recommendation and the routing verdict.

**PASF verdict: Zone II — Pilot First** (PASS 6.9/10, at the top edge of the band, one tenth
of a point below Zone I). The process is pulled down mainly by moderate-to-high stakeholder
impact (a real business's access to credit) and by structurability that's good but not
uniform — the routing/eligibility logic is fully deterministic and highly structured, while
the upstream evidence-gathering stages still involve LLM judgment over documents.

PADE was run across the 10 steps of the pipeline. **5 of 10 steps genuinely warrant an LLM
agent** (Companies House, policy, financial, web search evidence-gathering — all ReAct except
Companies House, which is Critic-Actor), **2 steps are AI Assistant/Copilot** (decision
synthesis, claim validation — both advisory, human-reviewed), **2 steps are already correctly
implemented as plain deterministic code** rather than an LLM agent (the readiness/triage
router itself, and work-queue dispatch), and **1 step is Human Only by design** (the final
underwriter/applicant/operations decision) — this last one is a deliberate policy choice
("every lending decision remains human"), not a capability gap.

## Part 1 — PASF: Process-Level Suitability

### Scorecard

| Dimension | Weight | Score (0–10) | Weighted | Notes/justification |
|---|---|---|---|---|
| D1 Structurability | 0.20 | 6.5 | 1.30 | The triage/eligibility rules and evidence-readiness checks are fully, formally specified (`triage.py`, `evidence_readiness.py`). But Companies House matching, financial consistency, and decision synthesis still require LLM judgment over prose/documents, so the process as a whole is moderately, not highly, structured. Above the D1<4 critical threshold, so no flag needed there. |
| D2 Reversibility | 0.15 | 9 | 1.35 | Nothing in this pipeline is a financial transaction or irreversible action. AI outputs are an advisory recommendation and an internal routing verdict; per the code, no response field ever claims a message was sent, an application submitted, or a loan approved/declined. Corrections and holds are just re-queued. |
| D3 Risk Profile | 0.20 | 7 | 1.40 | Misrouting causes friction (a good application returned for "corrections," or held internally) rather than a wrongly disbursed loan — the human underwriter is the actual credit-risk gate. Some care is warranted because a bad `return_to_applicant` message could misdirect a real customer, and Companies House groundedness can only downgrade a match (never fabricate one upward), which is a good asymmetric safety design. |
| D4 Data Quality | 0.15 | 7 | 1.05 | Intake is schema-validated (Pydantic) end-to-end; legacy submissions without a manifest fail safe into `internal_hold` rather than silently passing. Weakness: several fields are self-reported (`vat_registered`, turnover) and PDF extraction itself is explicitly out of scope for this increment (prepared JSON is trusted as-is). |
| D5 Rule Boundedness | 0.10 | 8 | 0.80 | The actual gating decision (underwriter / return / hold) is codified as an explicit, versioned rule set (`rules_version: unsecured-readiness-v1`) with no free-form judgment. The upstream evidence stages are policy-referenced but not purely rule-bound (agent judgment over documents). |
| D6 Frequency | 0.05 | 5 | 0.25 | Not given in the process description — treated as moderate application volume (this scores automation *value*, not feasibility, so it doesn't change the zone call; flag as unestimated if you have real volume figures). |
| D7 Exception Density | 0.10 | 6 | 0.60 | Exceptions are deliberately well-contained: ingestion errors, lookup failures, unreadable documents, and identity mismatches all route to `internal_hold` rather than being guessed at. Real-world loan evidence still generates a fair number of these (ambiguous VAT/borrowing declarations, name/address reconciliation), so this isn't "rare" — it's "handled safely when it happens." |
| D8 Stakeholder Impact | 0.05 | 3.5 | 0.175 | This determines a real business's access to financing and is directly customer-facing (return-to-applicant messaging, delay to underwriting). The human-decision guarantee mitigates but doesn't eliminate this — a wrong or slow routing verdict still lands on the applicant. |
| **PASS (total)** | | | **6.925 ≈ 6.9 / 10** | |

### Hard-stop check

- D3 < 2 **and** D2 < 3 → **No** (D3 = 7, D2 = 9)
- D8 < 3 **and** D3 < 4 → **No** (D8 = 3.5 is close to the threshold, but D3 = 7 is well clear of 4)
- Physical action with no digital interface → **No**

No hard stops triggered; the computed score stands.

### Zone verdict

**Zone II: Pilot First** — PASS 6.9 sits at the very top of the 5.5–6.9 band, one tenth of a
point below Zone I's 7.0 floor. Treat this as marginal, not comfortably Zone II: the framework
recommends a controlled pilot (3–6 months) with enhanced monitoring before full deployment,
but the process is a genuine near-miss for "Automate Now." The two dimensions holding it back
are **D1 Structurability** (mixed — the router is fully structured, the evidence stages
aren't) and **D8 Stakeholder Impact** (real customers, real financing decisions on the other
end). If D1 rises — e.g. by tightening the policy/financial-assessment prompts toward more
rule-bound extraction, the way the routing engine already is — this would likely cross into
Zone I on a future reassessment.

## Part 2 — PADE: Step-Level Design

### Step-by-step table

| # | Step | Hard-stop? | S1–S10 (brief) | Copilot / Agentic / Browser | Paradigm | Pattern | Governance note |
|---|---|---|---|---|---|---|---|
| 1 | Load & validate application + submission manifest | No | S1 9, S2 8, S3 2, S4 6, S5 2, S6 3, S7 7, S8 2, S9 5, S10 6 | 44.5 / **61.5** / 55 | Agentic AI | ReAct | Standard audit trail; already fails safe (bad JSON → stop, not silent drop) |
| 2 | Companies House identity verification (gates the rest of the graph) | No | S1 7, S2 8, S3 5, S4 4, S5 2, S6 4, S7 7, S8 4, S9 5, S10 7 | 51 / **53.5** / 47 | Agentic AI | Critic-Actor | The existing runtime groundedness check (downgrade-only) is functionally the "critic" half of this pattern already — keep it mandatory; this is the single hardest gate in the pipeline |
| 3 | Policy eligibility check | No | S1 7, S2 8, S3 5, S4 6, S5 3, S6 4, S7 7, S8 4, S9 5, S10 8 | 56.5 / **59** / 49 | Agentic AI | ReAct | High S10 (compliance) — this is exactly the step your Automated Reasoning claim-check exists to backstop downstream |
| 4 | Financial assessment (agentic + deterministic cross-check tool) | No | S1 7, S2 7, S3 5, S4 6, S5 2, S6 4, S7 6, S8 4, S9 5, S10 7 | 55 / **55.5** / 52 | Agentic AI | ReAct | Copilot/Agentic scores are essentially tied (55 vs 55.5) — the deterministic `cross_check_financial_figures` tool already does most of the real work, so this is a marginal Agentic call |
| 5 | Web-search evidence gathering (audit-only, never gates routing) | No | S1 6, S2 7, S3 3, S4 8, S5 1, S6 5, S7 5, S8 2, S9 5, S10 3 | 41.5 / **56.5** / 53 | Agentic AI | ReAct | Low stakes by design (audit-only signal); tool-call cap already bounds runaway search |
| 6 | Decision synthesis (advisory AI recommendation) | No | S1 6, S2 9, S3 6, S4 5, S5 1, S6 3, S7 8, S8 6, S9 5, S10 8 | **61** / 52.5 / 41 | AI Assistant | — | Feeds `ai_recommendation`, which every downstream artifact (dashboard, work queue) treats as advisory-only — keep it that way |
| 7 | Claim/groundedness validation (AR policy-consistency checker) | No | S1 8, S2 7, S3 4, S4 6, S5 2, S6 3, S7 7, S8 5, S9 5, S10 9 | **60.5** / 57 / — | AI Assistant | — | Genuinely hybrid: deterministic checker code wrapping an NL-to-logic translator — worth the same ongoing scrutiny already applied after the prior per-claim-decomposition fix |
| 8 | Deterministic readiness assessment + triage routing (the step this run confirmed) | No | S1 10, S2 9, S3 3, S4 7, S5 1, S6 2, S7 8, S8 3, S9 5, S10 9 | 57.5 / 65.5 (formula) | **Already deterministic code** | n/a | This is the strongest automation candidate in the whole pipeline, and it's correctly *not* an LLM agent at all — it's plain, versioned, testable code. The formula nominally favors "Agentic," but that would be a regression: don't route this through an LLM |
| 9 | Work-queue dispatch (route → queue, optimistic-concurrency delivery) | No | S1 9, S2 8, S3 2, S4 8, S5 1, S6 2, S7 8, S8 2, S9 5, S10 7 | 50 / 63 (formula) | **Already deterministic code** | n/a | Same note as step 8 — correctly implemented as plain dispatch logic, not an agent |
| 10 | Human review & final decision (underwriter approve/decline, applicant correction, or ops hold resolution) | **Yes** — policy-level "every lending decision remains human" is treated as equivalent to a certification requirement | N/A — hard stop | N/A | **Human Only** | n/a | Decision + rationale is already enforced as mandatory at the data layer (`work_queue.act` rejects a `complete` without both) |

Steps 8 and 9 are flagged as an intentional deviation from the raw PADE formula: the framework
scores every step against three *AI* paradigms, but has no category for "this should just be
code." Both are exactly that today, and that's a strength of the implementation, not a gap —
recommending an LLM agent for either would trade determinism for risk with no upside.

### Automation summary

- **5 of 10 steps → Agentic AI** (Companies House, policy, financial, web search, application loading) — 50%
- **2 of 10 steps → AI Assistant/Copilot** (decision synthesis, claim validation) — 20%
- **2 of 10 steps → already-correct deterministic code**, not an LLM paradigm (readiness/triage routing, work-queue dispatch) — 20%
- **1 of 10 steps → Human Only by policy** (final underwriting/applicant/operations decision) — 10%

Net: **90% of steps have some automated component**, but only half the pipeline needs an LLM
agent proper; a fifth is best-in-class deterministic automation already built that way, and
the credit decision itself stays with a human by design. No time-savings percentage is
estimated here — the process description didn't include current manual-processing time or
volume to baseline against; supply those if you want a defensible efficiency figure rather than
an invented one.

## Part 3 — Governance & Next Steps

- **HITL requirements:** Step 2 (Companies House) is the highest-leverage gate — its
  groundedness check should stay mandatory and downgrade-only. Steps 6–7 (synthesis, claim
  validation) are advisory only; nothing in this pipeline should be allowed to auto-message an
  applicant or auto-route to underwriting without the deterministic triage step 8 in between.
  Step 10 keeps the pre-existing mandatory decision+rationale requirement.
- **Audit/compliance requirements:** Steps 3, 7, and 8 carry the highest S10 (compliance)
  scores — policy eligibility, AR claim-checking, and the triage router itself are the places
  an examiner would look first. All three already write dedicated audit artifacts
  (`policy_check/result.json`, `decision/validation.json`, `decision/triage.json`).
- **Recommended rollout:** given the Zone II / marginal-Zone-I verdict, a 3–6 month controlled
  pilot with enhanced monitoring on live applications is the right next step — not a full
  unmonitored rollout. Track false-`return_to_applicant` and false-`internal_hold` rates
  specifically, since those are the two failure modes that land on real customers.
- **Caveats:** PASF predicts deployment success at roughly 74% accuracy and PADE predicts
  paradigm choice at roughly 83% accuracy in the source validation study, and vendor
  automation claims in that same study ran about 2x what was independently verified. Treat
  this assessment as decision support, not a guarantee — especially given the PASS score
  landed right at the Zone I/II boundary rather than comfortably inside either zone.
