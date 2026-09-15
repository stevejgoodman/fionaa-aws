# Runtime policy consistency

All five loan types have versioned Automated Reasoning policies and standalone
guardrails in us-east-1, using the required US guardrail profile. These resources are
separate from the existing PII-Redaction guardrail. Runtime version 34 is deployed and READY; the DEFAULT endpoint routes to it
(activated 2026-09-12). All five bindings are configured in `agentcore.json`.

`runtime-bindings.json` contains all five concrete environment bindings;
`runtime-iam-policy.json` contains the scoped permissions installed on the
runtime role as inline policy `FionaaRuntimePolicyConsistency`. `REVIEW.md` documents corrections to AWS's generated rules.
[test_live_guardrail.py](../../evals/automated_reasoning/test_live_guardrail.py) exercises the real standalone checker on synthetic
boundary cases; [live-results.json](../../evals/automated_reasoning/live-results.json) records its latest findings. These smoke
tests do not replace full application evaluation or policy coverage review.

| Loan type | Guardrail ID | Version |
| --- | --- | --- |
| Secured business loans | bwcgqb1tsa07 | 1 |
| Unsecured business loans | 5h5yunnkf895 | 1 |
| Revolving credit facility | cdue92mr0ap4 | 1 |
| Invoice discounting | 7kbeqj127jvv | 1 |
| Invoice factoring | me18qejox6li | 1 |

The additional product definitions are in `<loan-type>-definition.json`,
with matching source snapshots in `<loan-type>.txt`. `product-resources.json`
records AWS resource creation. `create_product_guardrails.py` can resume a
partially completed creation run; it refuses changed definitions rather than
silently repointing a reviewed binding. Run [test_live_products.py](../../evals/automated_reasoning/test_live_products.py) explicitly
for the additional product boundary checks; findings are saved in
[product-live-results.json](../../evals/automated_reasoning/product-live-results.json).

Latest verification: 48/48 additional-product live boundary cases passed,
alongside 133 local unit tests. The original secured pilot previously passed
8/8 live cases. Full deployed-application evaluation remains separate from
these policy-boundary tests.

## Human review report

`validate_final_decision` checks the policy assessment and AI recommendation
at the end of the workflow. Each eligibility verdict, clause finding,
documentation gap, policy summary, recommendation outcome, reason and rationale
gets its own standalone `ApplyGuardrail` request. A free-text field may contain
multiple assertions; its raw findings are retained rather than assuming it is
one indivisible statement.

Every application ends with `outcome: pending_human_review`. Only a human
makes the loan decision. The AI's suggested approved/rejected/referred outcome,
reason and rationale are explicitly labelled `ai_recommendation` in
`decision/result.json`. Validation never replaces that recommendation.
The no-company branch also produces a pending review report, with a referral
recommendation and validation marked not checked; it skips later assessments.

`decision/validation.json` (also embedded in the report) contains:

- `claims`: the text, `source`, and `source_path` (a JSON pointer into the report),
  together with each checker's findings and request metadata.
- Per-claim `status`: `valid`, `invalid`, `inconclusive`, or `not_checked`.
  For unavailable checks, `check_status` preserves the underlying configuration,
  policy-version, service-error or unusable-response status.
- `counts`: totals for those four statuses, with no overall pass/fail gate.
- `evidence`: the shared input snapshot supplied to the checker. This records
  the available inputs, not which individual sources proved a given claim.

Valid means supported under the configured policy and supplied facts; it does
not authenticate the underlying documents. Invalid means a policy contradiction
was found. Inconclusive means the checker could not establish validity.
The checker's legacy `passed` flag remains for existing standalone clients;
the workflow ignores it and omits it from report annotations.

The scope remains policy consistency of the listed assessment fields. It does
not independently validate every statement in source documents or web results.
A policy version mismatch or unavailable service is never labelled invalid.


Deployed smoke test (2026-09-12): runtime 34 returned HTTP 200 for a disposable
unsecured-loan application. The standalone check consumed 3 Automated Reasoning
units and returned `tooComplex`; the graph referred the application before final
validation. This verifies runtime permissions and fail-closed enforcement, not a
successful approval path. Evidence: [activation-results.json](../../evals/automated_reasoning/activation-results.json). Simplifying the
validation evidence and evaluating translation coverage remain follow-up work.
The initial smoke test exposed missing cross-region profile permissions; these
were corrected and six binding/IAM regression tests passed.

## Deployment configuration

The runtime environment variable `FIONAA_AR_GUARDRAILS` is a JSON object keyed
by loan type. Each entry must contain:

```json
{
  "secured-business-loans": {
    "guardrail_id": "REPLACE_WITH_REVIEWED_GUARDRAIL_ID",
    "guardrail_version": "1",
    "policy_sha256": "SHA256_OF_LOAD_POLICY_TEXT_OUTPUT"
  }
}
```

Use a numbered, immutable guardrail version with the reviewed Automated
Reasoning policy attached. `DRAFT` is rejected. The digest binds the reviewed
deployment configuration to the exact text loaded by `load_policy_text`;
changing either general or product policy requires review and rebinding.
The digest is a deployment assertion, not an AWS attestation of policy fidelity.

Grant the Runtime execution role `bedrock:ApplyGuardrail` on the selected
guardrail ARN and `bedrock:InvokeAutomatedReasoningPolicy` on the attached
versioned Automated Reasoning policy ARN. Keep this on the runtime role, separate from the
customer-scoped S3 data-access role. Do not use `bedrock:*`.
Inference-only IAM guardrail conditions cannot enforce a separate standalone
validation call; enforcement here is the mandatory graph transition.

The inline IAM policy is managed separately from CloudFormation. If the runtime
role is replaced, reapply it to the new role before serving applications:

```sh
aws iam put-role-policy \
  --role-name AgentCore-fionaa-default-ApplicationAgentFionaaRunt-CRsQ1yMJM10I \
  --policy-name FionaaRuntimePolicyConsistency \
  --policy-document file://agentcore/automated-reasoning/runtime-iam-policy.json \
  --profile AIOps
```

Run from `fionaa/`. The policy also grants `ApplyGuardrail` on the specific
US guardrail profile in us-east-1, us-east-2 and us-west-2, as required by
[AWS cross-region permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrail-profiles-permissions.html).
The AgentCore CLI synchronized its managed CDK dependency versions during deployment;
the updated package manifest and lockfile are retained for reproducibility.

## Activation checklist

1. Build the formal policy from general.md plus the applicable policy.md.
   `secured-business-loans.txt` and `secured-build-input.json` capture the
   initial secured-loan pilot source and API input.
2. Review extracted rules, variable mappings, and source coverage. Eligibility
   and documentation completeness must remain separate. Unknown facts must
   not become satisfied requirements. The checker must cover the actual
   `eligible` and final `outcome` claims, not only incidental numeric claims.
3. Test valid and invalid amounts, terms, trading history, missing collateral,
   statement age at 89/90/91 days, and incorrect approval claims against real
   ApplyGuardrail. Check natural-language translation as well as formal rules.
4. Publish policy and guardrail versions, configure IAM and the runtime binding,
   then run a deployed-runtime evaluation before accepting automated decisions.

Without bindings, every application reaching policy validation is referred.
Do not deploy expecting existing automatic approvals to continue unchanged.
The local mocked tests verify runtime enforcement, not formal policy fidelity.

## Scope

Automated Reasoning checks logical consistency with supplied facts and covered
rules. It does not establish the truth of application data or replace numeric
calculations, document validation, prompt-injection protection, or human review.
Findings may include translated input: they are sensitive evidence and belong
in the existing customer-scoped store, not application logs.

References:
- https://docs.aws.amazon.com/bedrock/latest/userguide/integrate-automated-reasoning-checks.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/create-automated-reasoning-policy.html
