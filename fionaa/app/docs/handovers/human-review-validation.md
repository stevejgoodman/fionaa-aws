# Handover: claim validation for human review

## User intent — preserve this

The user corrected the purpose of `validate_final_decision`:

> "the outcome is ALWAYS determined by human review. Its to verify that each statement or claim is valid/inconclusive or invalid and state that against each piece of evidence."

When asked whether to keep a recommendation, the user chose:

> "Keep an explicitly labelled AI recommendation"

The workflow produces a review report. It must not approve or reject loans. Every
lending decision requires human review. Validation results annotate claims; they
must not overwrite the AI recommendation or act as a loan-decision gate.

The subsequently approved [readiness triage](readiness-triage.md) can return
incomplete unsecured submissions for applicant correction before underwriting.
It is separate from the lending recommendation and claim validation. Triage now
writes the final report after either assessment ending, adding the documentation
route without changing the lending outcome. PDF extraction remains future work.

The user prefers simple English and clear distinctions between local tests and live evaluations.

## Current state

Changes are implemented locally, with focused tests passing. Nothing was deployed, and no live AWS evaluation was run for these changes. The changes have not been committed by Codex.

Repository root: `/Users/stevegoodman/dev/fionaa-aws`.
All paths below are relative to that root.

### Report behaviour

- `decision/result.json` and the graph's `final_decision` now contain `outcome: pending_human_review`.
- The AI's `outcome`, `reason` and `rationale` are nested under `ai_recommendation`.
- Upstream assessments remain in the report.
- `decision/proposed.json` still stores the original synthesis output using its existing flat shape. It is an intermediate proposal, not a human decision.
- Existing node names, state keys and artifact paths were retained, including `validate_final_decision`, `reject_no_company` and `final_decision`.
- The runtime response in `main.py` reads the report's outcome, so it now returns `pending_human_review` without a separate code change.

Illustrative shape (not a live result):

```json
{
  "outcome": "pending_human_review",
  "ai_recommendation": {
    "outcome": "approved",
    "reason": "...",
    "rationale": "..."
  },
  "policy_check": {},
  "companies_house": {},
  "financial_assessment": {},
  "web_search": {},
  "validation": {
    "policy_sha256": "...",
    "counts": {"valid": 1, "inconclusive": 0, "invalid": 0, "not_checked": 0},
    "evidence": {},
    "claims": [
      {
        "source": "policy_check",
        "source_path": "/policy_check/clause_findings/0",
        "claim": {"summary": "The requested amount is within the policy range."},
        "status": "valid",
        "findings": []
      }
    ]
  }
}
```

### Claim validation

`fionaa/app/src/fionaa/workflow/validation.py`:

1. Collects the application, annual accounts, bank statements, Companies House findings and financial assessment as a shared evidence snapshot. Missing values remain missing/unknown.
2. Builds separate requests for:
   - Policy eligibility.
   - Each policy clause finding.
   - Each documentation gap.
   - Policy summary, if present (new coverage).
   - AI recommendation outcome, reason and rationale.
3. Calls the checker concurrently, once per listed field or list item.
4. Retains each result and its raw findings/metadata, with a `source_path` pointing into the final report.
5. Saves `decision/validation.json` and embeds the same validation record in `decision/result.json`.

There is no overall validation pass/fail gate. The report has counts of individual statuses instead.

| Per-claim report status | Meaning in this implementation |
| --- | --- |
| `valid` | Checker confirmed the claim under the policy and supplied facts. |
| `invalid` | Checker returned an invalid finding. |
| `inconclusive` | Checker returned usable findings but could not fully confirm validity, without an invalid finding. |
| `not_checked` | No usable validation result; underlying status is preserved in `check_status`. |

Examples of preserved `check_status`: `not_configured`, `policy_version_mismatch`, `validation_error`, `not_validated`. Operational failures must not be presented as invalid claims.

`fionaa/app/src/fionaa/policy_consistency.py` now exposes `status: invalid` for invalid findings, instead of collapsing them into `not_validated`. Its legacy `passed` flag remains for existing standalone clients/eval runners. The workflow ignores and removes that flag from report annotations. Configuration checks and the AWS request shape remain in place.

### Company not confirmed

The existing `reject_no_company` branch still stops before policy/financial/web/synthesis/validation stages. It now produces:

- `outcome: pending_human_review`.
- An explicitly labelled referral recommendation, with the company-not-confirmed reason.
- Embedded validation with `status: not_checked`, `check_status: company_not_confirmed`, and an empty claims list.

It does not perform claim validation or write a separate `decision/validation.json` on this branch. No automatic rejection remains on this path.

## Files changed

| File | Change |
| --- | --- |
| `fionaa/app/src/fionaa/workflow/validation.py` | Per-field annotations, source pointers, evidence snapshot, counts; removed decision gate. |
| `fionaa/app/src/fionaa/workflow/decision.py` | Shared `human_review_report` helper; no-company path leaves decision pending. |
| `fionaa/app/src/fionaa/policy_consistency.py` | Distinct invalid status; legacy checker flag retained. |
| `fionaa/app/src/fionaa/prompts.py` | Synthesis explicitly produces advice for a human. |
| `fionaa/app/src/fionaa/domain/assessments.py` | Recommendation schema descriptions updated; existing class/enum names retained. |
| `fionaa/app/src/fionaa/graph.py` | Comments updated; topology unchanged. |
| `fionaa/app/docs/dashboard-mockup/job-dashboard.html` | Displays pending review, labelled recommendation and claim statuses. |
| `fionaa/app/tests/test_policy_consistency.py` | Annotation, status and human-outcome regression tests. |
| `fionaa/app/tests/test_graph.py` | Updated report expectations and checker mocking. |
| `fionaa/agentcore/automated-reasoning/README.md` | Documents current behaviour and limitations. |

## Verification actually performed

From repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 fionaa/app/.venv/bin/pytest \
  -p pytest_asyncio.plugin -q \
  fionaa/app/tests/test_policy_consistency.py \
  fionaa/app/tests/test_graph.py \
  fionaa/app/tests/test_schemas.py
```

Result: **95 passed, 2 deprecation warnings**.

Coverage includes:

- All three recommendation outcomes remain advisory across valid, invalid, inconclusive and unavailable-check results.
- Mixed statuses remain attached to their own claims.
- Source pointers resolve into the report; evidence and raw findings are retained.
- Input state is not mutated.
- No-company and unconfigured-checker paths produce pending review reports.
- Graph execution, checkpoint persistence and schemas.

These are local tests using mocked external services. They do not establish real Automated Reasoning translation quality or end-to-end deployed behaviour.

The first attempt with automatic pytest plugin loading failed because `pytest_rerunfailures` tried to bind a local socket forbidden by the sandbox. Disabling plugin autoload and explicitly loading pytest-asyncio resolved that environment issue. Intermediate graph tests also exposed a mock targeting the removed `_check_claim` helper; the mock now targets `PolicyConsistencyChecker.check`, and the final run passed.

Additional checks:

- Extracted inline dashboard JavaScript and ran `node --check`: passed.
- `git diff --check -- fionaa`: passed.
- No browser visual/interaction test was performed.

## Live evals: not run

Neither of these runners was executed, and neither result JSON was updated by this work:

- `fionaa/evals/automated_reasoning/test_live_guardrail.py` → `live-results.json`.
- `fionaa/evals/automated_reasoning/test_live_products.py` → `product-live-results.json`.

Both runners use synthetic boundary cases and real AWS ApplyGuardrail calls. They exercise the standalone checker, not the new report-building workflow.

Important before interpreting or running them:

- The secured runner uses a hard-coded guardrail ID/version and policy text under `fionaa/agentcore/automated-reasoning/`.
- The product runner loads `runtime-bindings.json` from that directory.
- Both overwrite their result files as they run and incur AWS request charges.
- The secured runner currently judges success through `passed`; an inconclusive result can therefore count as success for a positive case. Strict validity needs explicit status/raw-finding assertions.
- The product runner checks raw valid/invalid findings as well as `passed`.
- Passing these boundary cases would not demonstrate that real workflow evidence, recommendation wording or compound explanations translate successfully.

## Remaining boundaries and useful follow-up

These are review/evaluation gaps, not proof that the implementation is faulty:

1. **Field-level versus statement-level checks:** each reason, rationale or summary is one request even when it contains several statements. The code does not extract individual sentences or atomic factual claims. This may fall short of a literal interpretation of “each statement”. Raw checker findings are retained.
2. **Source pointer versus proof citation:** `source_path` identifies where the assessed claim appears in the report. It is not a citation to the precise document/page/value supporting that claim. `evidence` stores the shared inputs, not a per-claim attribution of which sources the checker used.
3. **Coverage:** Companies House, financial-assessment and web-search narratives are not each independently checked by this node. Web-search findings are not directly included in `_facts`. Source documents are inputs, not independently authenticated outputs.
4. **Live quality:** new “recommended outcome” wording, the added policy-summary request, and realistic evidence bundles have not been evaluated against AWS. Boundary-case successes must not be presented as evidence for those behaviours.
5. **Consumers:** report shape changed: top-level outcome is pending, reason/rationale moved under `ai_recommendation`, and aggregate validation passed/status became counts. Known dashboard code was adjusted; external consumers may need migration.
6. **Human decision capture:** this change marks reports as pending review. It does not implement a screen, API or storage workflow for a human to record their eventual decision.
7. **No-company coverage:** this path deliberately skips claim checks; discuss separately if every branch needs annotations.

A useful next task is to review these boundaries with the user, then evaluate the exact workflow claim/evidence shape with synthetic representative applications. Keep local regression results, standalone live boundary results and end-to-end workflow evaluation results clearly separated.

## Workspace care

There were pre-existing modified OpenWiki files and unrelated untracked files before this work. Preserve them. Do not regenerate, hand-edit or revert OpenWiki as part of this change. Follow repository AGENTS.md instructions: code/tests are authoritative, use focused validation, and preserve complete failure output.
