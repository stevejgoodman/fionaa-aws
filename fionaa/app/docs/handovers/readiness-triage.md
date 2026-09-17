# Readiness triage: approved rules and implementation increments

The user approved the unsecured-business-loan checklist on 17 September 2026.
Every lending decision remains human. Readiness routing is independent of the
AI recommendation, substantive eligibility and claim-validation results.

Essential: application details (including amount and term), director ID,
director proof of address, business bank statements, accounts or management
information, and existing borrowing details where applicable.

VAT returns remain required when registered but may follow during underwriting.
The personal guarantee for requests over £25,000 may also follow. Unknown VAT or
borrowing applicability requires applicant clarification. A missing requested
amount must be clarified. Explicit negative declarations remove conditional
requirements. No percentage-of-files threshold is used.

Bank evidence must cover at least three months and the most recent statement
must be less than 90 days old at submission. Filed accounts must have an
accounting date within the preceding 12 months. Management information is an
alternative to filed accounts. The policy does not specify management-information
coverage, proof-of-address document types or VAT-return periods; no new limits
have been introduced.

## Increment 2a: structured findings and routing engine

`src/fionaa/triage.py` provides a typed input, itemised findings with evidence
references, corrections and policy references, and a deterministic router.
It accepts checked evidence findings from trusted assessment code; applicants
must not be allowed to submit their own satisfied statuses. A persisted
submission date is required. Evidence producers must use that date when checking
coverage and freshness. The router itself does not inspect or extract documents.

- All essentials satisfied: underwriter, including adverse recommendations.
- Known essential gap: return to applicant with corrections.
- Missing assessment or failed verification: internal hold, with no correction
  attributed to the applicant for that failed check. This takes precedence over
  known gaps, so a partial correction report is not sent automatically.
- Unsupported loan types fail input validation rather than using these rules.

The engine was initially delivered standalone and approved before integration.

## Increment 2b: prepared-evidence ingestion and workflow integration

Implemented locally. The user explicitly deferred the PDF extraction pipeline;
this increment consumes prepared JSON and never opens or extracts PDFs.

`domain/ingestion.py` defines the trusted intake contract. For unsecured loans,
`workflow/loading.py` reads `ingestion/submission.json` and validates the extracted
JSON referenced in its document inventory. Other loan types retain legacy loading.

Example manifest (one document shown; include **every** uploaded document):

```json
{
  "schema_version": 1,
  "submission_id": "submission-1",
  "submission_date": "2026-09-01",
  "inventory_complete": true,
  "documents": [
    {
      "document_type": "director_id",
      "source_key": "input/documents/director-id.pdf",
      "extraction_key": "ingestion/documents/director-id.json",
      "processing_status": "processed"
    }
  ]
}
```

The referenced extracted JSON for that ID is:

```json
{"document_kind": "passport", "holder_name": "Jane Smith"}
```

The manifest and prepared extractions are trusted intake outputs, not fields
accepted from an applicant. They establish processing status, not a readiness
verdict: the application computes the verdict. The future producer must complete
the upload inventory before publishing a manifest, maintain the same submission
date on retries, and use a new submission identity for a resubmission. Original
source keys are retained as evidence references; this consumer does not verify
raw-file content or authenticity. Paths are application-relative; traversal and
duplicate document records are rejected. No new upload API or producer is included.

Supported document types and extracted fields:

| Type | Prepared JSON |
| --- | --- |
| `bank_statement` | Existing `BankStatementSchema`, including start/end dates and account details |
| `annual_accounts` | Existing `AnnualAccountsSchema` |
| `director_id` | `document_kind` (`passport` / `driving_licence`), `holder_name` |
| `proof_of_address` | `holder_name`, `address` |
| `management_information` | `company_name`, `period_start`, `period_end`, `turnover`, `profit` |
| `vat_returns` | `company_name`, `period_start`, `period_end`, `vat_due` |
| `existing_borrowing` | `company_name`, nonempty `borrowing` list of `lender`, `outstanding_balance`, `monthly_repayment` |
| `personal_guarantee` | `guarantor_name`, `executed` |

`input/application.json` accepts explicit boolean `vat_registered` and
`has_existing_borrowing` declarations. Missing/null means unknown, not false.
Submission date comes from the manifest, not an applicant declaration or the
wall clock. New manifests with future or invalid dates are held internally.

Processing states are `processed`, `pending`, `failed`, and `unreadable`.
Only a complete inventory can establish that a required document was not uploaded.
An unreadable source must be confirmed by the trusted producer; a JSON/schema
error or missing extraction is recorded as a processing failure. Storage access
errors propagate rather than being converted to applicant gaps. Legacy unsecured
submissions without a manifest get `internal_hold`; their historical submission
date is never invented.

`evidence_readiness.py` checks the inventory deterministically. It compares names
and addresses using whitespace/case normalization; unresolved identity differences
go to internal reconciliation, not automated applicant correction. Document
presence is not a claim of authenticity. Bank coverage counts the union of covered
days per account, excluding duplicate and overlapping periods. It uses calendar
months measured backwards from that account's latest inclusive statement end;
it does not combine accounts or impose a new consecutive-months condition.
Exactly 90 days is stale. Future/reversed/uninterpretable dates need internal
verification. Accounts use a calendar 12-month boundary; management information
is a supported alternative, including when it reports a loss.

`workflow/triage.py` runs after both the annotated-review path and the no-company
path. An incomplete application or failed evidence processing can skip assessment
agents and reach triage with an explicitly unassessed review report. Company tool
errors, missing grounding, and lookup timeouts/connection failures produce an
internal hold. A completed no-match lookup does not itself create a document gap.

The policy stage uses the checked documentation findings instead of inferring
completeness from filenames or business-address matches. The financial stage
receives management information separately from filed accounts. Policy and claim
validation use the manifest date; claim validation also uses checked bank coverage
and ID/address findings. Legacy, unmanifested assessment retains the previous
wall-clock fallback but cannot pass unsecured triage.

Triage is the sole writer of `decision/result.json`, preventing an intermediate
report without routing from appearing as final. It also writes
`decision/triage.json`. The result retains `outcome: pending_human_review` and the
existing `ai_recommendation`; `triage.route` is separate. Other products keep their
existing report shape. Routing is recorded only: no underwriting queue, applicant
message or resubmission action is executed in this increment.

Validation: 177 focused tests passed using:

```sh
.venv/bin/python -m pytest -p no:rerunfailures tests/test_triage.py tests/test_workflow_triage.py tests/test_graph.py tests/test_ar_facts.py tests/test_ar_claims.py tests/test_policy_consistency.py tests/test_schemas.py -q
```

The rerun-failures plugin is disabled because its local socket is blocked in the
sandbox. No live services or deployment were used.

## Step 3: API and dashboard exposure

Implemented locally after approval. `run_response.py` builds the authenticated
runtime response from the final report. It preserves `outcome` and `decision_uri`
and adds `workflow_status`, the advisory `ai_recommendation`, `triage`, `triage_uri`,
and `applicant_corrections`.

| Stored route | API workflow status | Dashboard label |
| --- | --- | --- |
| `underwriter` | `ready_for_underwriting` | Ready for underwriting |
| `return_to_applicant` | `applicant_corrections_required` | Applicant corrections required |
| `internal_hold` | `internal_attention_required` | Internal attention required |
| No triage (legacy/other product) | `pending_human_review` | Pending human review |

`applicant_corrections` contains only blocking, actionable gaps when the route is
`return_to_applicant`. Each item contains `requirement`, `correction` and
`evidence_refs`. For an internal hold or underwriting route it is empty. The full
triage findings remain available to internal consumers, including supporting gaps
that underwriting can resolve. Unknown future routes default to internal attention.
No response fields claim that an application was sent, a message delivered, or a
loan approved/rejected. Logging records application identity and workflow status,
not the new correction text or complete findings.

The standalone dashboard shows the route ahead of the advisory recommendation,
with separate blocking and supporting gaps and optional evidence references.
It includes only executed stages and handles legacy and in-progress reports.
The poller waits until no graph nodes remain before presenting the final report,
and recognizes the new triage/validation/deferred-assessment stages. Nested
validation evidence remains redacted. HTML-like input is escaped during embedding
and display.

Local checks cover runtime response wiring, all routes, legacy behaviour, stale
assessment URIs, polling before/after triage, redaction and safe JSON embedding.
Five isolated Chrome scenarios passed, including desktop and mobile screenshot
inspection. The in-app browser execution tool was unavailable; installed Chrome
was used with a temporary profile, synthetic data and HTTP requests blocked.
Nothing was deployed or published.

## Next approval checkpoint

Queues, notifications and linked resubmissions require the next approval. PDF
extraction remains future work, using the contract above.
