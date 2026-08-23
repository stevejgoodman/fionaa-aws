# Offline evals for fionaa

Status: **registered locally, not yet deployed to AWS.** See "Deploying" below.

## What's here

- `agentcore/datasets/fionaa_eval_dataset.jsonl` — `AGENTCORE_EVALUATION_PREDEFINED_V1`
  dataset, registered in `agentcore.json`. 9 scenarios covering the three
  evidence-gathering nodes in `graph.py`:
  - Companies House verification (`check_companies_house`) — a real active
    company (Goodman's Consulting Ltd, 08139267, reused from
    `tests/test_live_companies_house.py`) in exact and fuzzy/noisy-input
    form, plus two fictitious-company cases that must resolve to
    `found=false`.
  - Policy check (`check_against_policy`) — three scenarios: a standard
    unsecured-business loan (asserting the response cites a specific policy
    clause rather than a bare accept/reject), an invoice-factoring case that
    exercises `compute_invoice_factoring_advance`'s deterministic advance calc, and a
    secured-loan case that guards against `policy_loader.py` loading the
    wrong loan type's policy file.
  - Web search (`search_web`) — one scenario asserting no fabricated adverse
    findings.

  Each `turns[].input` is the JSON-encoded `application` dict, matching what
  `graph.py` actually sends as message content to each node
  (`json.dumps(application)` / `f"Company: {company_name}"`).

  **Policy check no longer searches a knowledge base, but it can still call
  deterministic tools.** `check_against_policy` used to route through the
  `kb-target-loan-policies` MCP/knowledge-base tool; it now loads the
  matching `policies/<loan_type>/policy.md` directly by `loan_type` (see
  `policy_loader.py`) and passes the text straight into the prompt —
  `loan_type` is known from the application before the node runs, so there's
  nothing to search for. Separately, each `policy.md`/`general.md` can
  declare a `<!-- checks: ... --> ` comment naming which tools from the
  shared pool (`check_tools.CHECK_TOOLS_POOL`) apply to that loan type (e.g.
  `compute_invoice_factoring_advance`); `policy_loader.load_check_tool_names`
  reads that declaration and `check_against_policy` hands the model just that
  scoped subset. So the invoice-factoring scenario below does exercise a real
  tool call (`compute_invoice_factoring_advance`), and the unsecured/secured-loan
  scenarios have no `expected_trajectory` only because those loan types'
  policy.md files don't declare any checks — not because policy check can
  never call a tool.

- `agentcore/evaluators/companies_house_correctness.json` — custom
  LLM-as-a-Judge evaluator (`fionaa_companies_house_correctness`, `TRACE`
  level). Grades `check_companies_house`'s structured `found`/`confidence`/
  `summary` output against the dataset's `expected_response`, and penalizes
  false positives/negatives and ungrounded summaries specifically.

- `agentcore/evaluators/injection_resistance.json` — custom evaluator
  (`fionaa_injection_resistance`, `TRACE` level, no ground truth). Every node
  passes applicant-controlled fields (`company_name`, `applicant_name`,
  `registered_address`, ...) straight into a model turn — this checks the
  agent treated that content as data to verify, not instructions to follow.

Both are registered in `agentcore.json` (`evaluators: [...]`) alongside the
dataset (`datasets: [...]`).

## Trace/session semantics: one `graph.ainvoke()` = one trace, not one turn per node

Confirmed by downloading a real trace (`agentcore traces get`) and inspecting
its spans: AgentCore treats a single `main.py:invoke()` call as **one session
and one trace**. fionaa's three evidence-gathering nodes (`policy_check`,
`companies_house`, `web_search`) each show up as their own `invoke_agent`
sub-span sequence within that one trace (visible via the
`traceloop.association.properties.langgraph_path` attribute on
`opentelemetry.instrumentation.langchain`-scoped spans) — they are not
separate turns.

This matters because a `TRACE`-level evaluator's `{assistant_turn}`
placeholder resolves to the *last* node's response for that trace
(`web_search`) — never a middle node's, no matter which node you actually
want to grade. `{context}`, by contrast, does contain every prior node's
output. `fionaa_companies_house_correctness` originally graded
`{assistant_turn}` directly and silently scored web_search's narrative
output against companies_house's expected verdict — always "Incorrect",
regardless of whether companies_house was actually right. Fixed by rewriting
the instructions to have the judge locate and grade the companies_house
step specifically within `{context}`, not `{assistant_turn}`. Confirmed
against a real trace: score went from 1/"Incorrect" (grading web_search's
narrative) to 3/"Correct" (grading companies_house's actual tool-grounded
verdict) with the same session and the same ground truth.

If you add more node-specific TRACE-level evaluators later, apply the same
pattern — name the node explicitly in the instructions and tell the judge
where in `{context}` to look, since `{assistant_turn}` alone won't isolate
it in a multi-node single-trace graph like this one.

## A caveat worth knowing before you rely on `--dataset` scenario-invoke mode

`agentcore run eval --dataset fionaa_eval_dataset` / `run batch-evaluation
--dataset ...` invoke the deployed runtime directly with each scenario's
`turns[].input` as the message payload. fionaa's entrypoint
(`main.py:invoke`) doesn't take a chat message at all — it takes
`{"application_id": ...}` plus a verified JWT, and loads the application from
S3 (`load_application`) that must already exist at
`input/application.json` under that customer/application prefix. So the
scenario-invoke runner won't drive fionaa correctly out of the box.

Two ways to actually use this dataset given that:

1. **Trace/batch evaluation against real runs** (recommended, no code
   changes): stage each scenario's `application` JSON at
   `input/application.json` for a real `(customer_id, application_id)`,
   invoke the runtime normally, then run
   `agentcore run batch-evaluation --runtime fionaa --evaluator
   fionaa_companies_house_correctness fionaa_injection_resistance
   --ground-truth <file mapping session_id -> expected_response/assertions>`
   against the resulting sessions. The dataset's `expected_response`/
   `assertions` per scenario are exactly what belongs in that ground-truth
   file.
2. **Node-level harness** (more direct): `deepeval_evals/` does this -- calls
   `graph.check_companies_house`/`check_against_policy`/`search_web`/
   `check_financial_assessment` directly with a `FakeRuntime`/real tools per
   scenario (same shape as `tests/test_graph.py`), and scores the response
   with DeepEval's own `GEval`/custom metrics (built from this dataset's
   `assertions`/`expected_response`/`expected_trajectory`, plus
   `injection_resistance.json`'s rubric ported as a metric too) rather than
   the AgentCore Evaluate API's TRACE-level scoring, which only works
   against a real deployed-runtime session. See `deepeval_evals/README.md`.

Either way, the dataset and evaluator *content* (ground truth + rubrics) is
the reusable part — this note is about the plumbing to invoke fionaa with
it, which is a thinner, separate piece of work.

## Deploying

Nothing above exists in AWS yet — `agentcore add dataset`/`add evaluator`
only write local config. To create the real resources:

```bash
export AWS_PROFILE=AIOps
agentcore deploy --diff -y   # confirm what will change first
agentcore deploy -y
agentcore dataset publish-version --name fionaa_eval_dataset
```

**Heads up:** `agentcore deploy` diffed clean for the new dataset/evaluators,
but it *also* shows the Runtime resource's code artifact (S3 zip prefix)
changing, even with `app/fionaa` untouched (`git status` there is clean) —
this looks like non-deterministic packaging (e.g. zip timestamps) rather
than a real code change, but it means any `agentcore deploy` right now
repackages and redeploys the live runtime as a side effect. Worth deploying
at a moment you're comfortable with that, or worth root-causing the
non-determinism first if you want `deploy` to be a no-op when nothing
changed.

After deploying, `agentcore status --type dataset` and `agentcore status
--type evaluator` show the created resources.

## Path 2 plan: post-deploy batch-evaluation gate

Path 1 (`deepeval_evals/`) calls node functions directly, bypassing the
deployed runtime — fast and PR-time, but it never proves the actual deployed
artifact behaves correctly. Path 2 invokes the real Runtime and gates on
`batch-evaluation` scores against real sessions — the higher-fidelity check
that validates what's about to take production traffic. Not started yet;
this section is the plan, written before any of it exists.

### Constraints that shape the design (confirmed against current code)

- **`FionaaDataAccessRole`'s trust policy only trusts the Runtime's execution
  role** (`fionaa_iam_policies.md` §2) — a CI role cannot assume it to stage
  data the way the app itself would. Don't widen that trust policy for CI;
  it's a customer-isolation security boundary, not a place to bolt on
  unrelated access. Instead, per gotcha #10 in `agentcore_deploy_gotchas`:
  give the CI role its own narrow grant (`s3:PutObject` on a reserved
  disposable prefix + `kms:GenerateDataKey`/`kms:Encrypt` on the applications
  KMS key) and write `input/application.json` directly with the CI role's
  own credentials, setting the *same* `SSEKMSEncryptionContext` the app
  would (`storage.py`'s `put_json` shows the exact shape). The KMS key's
  default policy grants the account root full access, so this round-trips
  fine for the real app's later scoped read.
- **`customer_id` is `sha256(email claim)` from a verified JWT**
  (`security.py`), never caller-supplied — staging and invoking must agree
  on one fixed disposable identity. Need one throwaway Cognito user
  provisioned once (not per CI run) with a fixed, obviously-synthetic email
  (e.g. `eval-harness@fionaa-ci.invalid`), so `customer_id` is deterministic
  and known ahead of time for staging. `application_id` should be freshly
  randomly generated per scenario per run to avoid collisions across
  concurrent/historical CI runs.
- **Getting a real JWT means `cognito-idp:AdminInitiateAuth`
  (`ADMIN_USER_PASSWORD_AUTH`) against that throwaway user, grabbing
  `AuthenticationResult.IdToken`** (not `AccessToken` — gotcha #12) — the CI
  role needs that action scoped to the one user pool/user, nothing broader.
- **`agentcore invoke`/`run eval --dataset` can't drive fionaa's actual
  payload shape** (gotcha #8, and this doc's caveat above) — invocation has
  to POST directly to the Runtime's `/invocations` endpoint with
  `Authorization: Bearer <id_token>` and a
  `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header (≥33 chars,
  deterministic per scenario e.g. `eval-<scenario_id>-<run_id>` so sessions
  are traceable back to scenarios afterward).
- **`agentcore deploy`'s non-deterministic repackaging is root-caused and
  confirmed unfixable from this repo.** Decompiled the installed CLI
  (`@aws/agentcore` 0.24.1, the npm package behind the `agentcore` binary):
  its packaging step walks the source tree building zip entries as
  `[fileContents, {level: 6}]` — no `mtime` field — and its bundled
  `fflate`-derived zip writer falls back to `Date.now()` whenever an entry's
  `mtime` is unset. So every `agentcore deploy` bakes the current wall-clock
  time into every zip entry's header, changing the packaged artifact's bytes
  (and its S3 key/hash) on every run regardless of whether `app/fionaa`
  actually changed. Confirmed still present in the latest published version
  (0.27.1, fetched via `npm pack` for comparison) — not fixed upstream, not
  something an upgrade solves. The CLI does have a content-hash-based skip
  check (`computeProjectDeployHash`/`isDeploySkippable`), but it explicitly
  returns "not skippable" whenever the project defines any `runtimes` (which
  is fionaa's deployment type — it's scoped to the newer `harnesses` project
  type instead) and is wired only into the interactive `agentcore dev`
  auto-deploy decision, not the `deploy` command. **Verdict: genuine upstream
  bug, outside this repo's control, not worth further investigation.**
  Mitigate the same way `deepeval-ci.yml` already scopes its trigger: only
  run the deploy+gate pipeline on pushes that touch `app/fionaa/**`,
  `agentcore/agentcore.json`, `agentcore/evaluators/**`, or
  `agentcore/datasets/**` — don't deploy (and don't burn a batch-evaluation
  run) on unrelated pushes to `master`.

### Work items, roughly in order

1. ~~Investigate the `agentcore deploy` packaging non-determinism~~ — **done**,
   see above: confirmed root cause (zip entry `mtime` defaults to
   `Date.now()` in the CLI's bundled zip writer), confirmed unfixed in the
   latest upstream version, confirmed the CLI's own skip-check doesn't apply
   to `runtimes`-type projects. Mitigation is the path-filtered trigger
   above — no further action needed here before moving on.
2. ~~One-time manual setup: create the throwaway Cognito user~~ — **done**.
   Created `fionaa-eval-ci@example.com` in the existing pool
   (`us-east-1_QdHqgzqUA`, app client `14dnsnarjq6povfqeisdtvs07a`
   /`fionaa-test-client`, which already has `ALLOW_ADMIN_USER_PASSWORD_AUTH`)
   with a permanent password, `email_verified=true`. Derived
   `customer_id = sha256("fionaa-eval-ci@example.com")` =
   `17deb75df387eafcea144caa24f896e85216c2622721c6c33c6c1b8cd73eae18` — this
   is the fixed S3 prefix (`fionaa-applications/17deb75.../*`) Path 2's
   staging step writes under and the CI role's IAM grant (work item 3) needs
   to scope to. The password itself lives in Secrets Manager
   (`fionaa/eval-harness-cognito-credentials`, ARN:
   `arn:aws:secretsmanager:us-east-1:492646066653:secret:fionaa/eval-harness-cognito-credentials-8NACSV`,
   `{"username": ..., "password": ...}` shape) — not written here or
   anywhere in the repo. Verified end-to-end: `AdminInitiateAuth` with
   `ADMIN_USER_PASSWORD_AUTH` returns an `IdToken` whose `email`/`aud` claims
   match, confirming the derived `customer_id` above. The CI role (work item
   3) needs `cognito-idp:AdminInitiateAuth` scoped to this one user/pool
   *and* `secretsmanager:GetSecretValue` on the credentials secret above (the
   same pattern `github-oidc-stack.ts` already uses for the Gateway OAuth
   secret).
3. ~~New CDK stack in `ci-infra/`~~ — **done**. `ci-infra/lib/path2-batch-eval-stack.ts`
   (`Path2BatchEvalStack`, same pattern as `github-oidc-stack.ts`, own file,
   not bolted onto `fionaa/agentcore/cdk/`), deployed as stack
   `FionaaEvalsPath2Ci` → role `arn:aws:iam::492646066653:role/fionaa-evals-path2-ci`.
   - Trust: GitHub OIDC (imports the existing provider by its deterministic
     ARN rather than recreating it), scoped to `push`/`workflow_dispatch` on
     `master` via the ref-triggered `sub` claim — matching both the plain
     and ID-qualified forms the same way `github-oidc-stack.ts` does for
     `pull_request`, since the ID-qualified form was the one actually issued
     there. **Not yet empirically confirmed for this ref-triggered case** —
     verify the same way (temporary debug step decoding the real token) the
     first time work item 8's workflow runs, and fix the condition if the
     real claim differs.
   - `s3:PutObject` scoped to `fionaa-6655-assets/<eval-customer-id>/*` +
     `kms:GenerateDataKey` (not `Encrypt` — matches what S3 SSE-KMS actually
     calls, same action `FionaaDataAccessRole`'s own grant uses) on the
     applications KMS key, condition-scoped to the one disposable
     `customer_id`'s `EncryptionContext`.
   - `cognito-idp:AdminInitiateAuth` scoped to the user pool ARN (finest
     grain Cognito supports for this action — no per-user scoping) +
     `secretsmanager:GetSecretValue` on the credentials secret.
   - `bedrock-agentcore:StartBatchEvaluation`/`GetBatchEvaluation`/
     `ListBatchEvaluations` (no resource type — confirmed against AWS's own
     service-authorization reference, not guessed) + `StopBatchEvaluation`
     scoped to `batch-evaluate/*` + `bedrock-agentcore-control:GetEvaluator`
     (scoped to the known evaluator ARNs)/`ListEvaluators` to resolve
     `--evaluator <name>` (confirmed this is a *control*-plane action,
     distinct service prefix from the batch-evaluation actions above, even
     though both use `bedrock-agentcore:`-namespaced resource ARNs).
   - **Deliberately excludes `bedrock-agentcore:InvokeAgentRuntime`**
     (dropped from the original sketch above once confirmed unnecessary):
     fionaa's Runtime uses a `CUSTOM_JWT` authorizer, so invocation is a
     plain HTTPS POST with `Authorization: Bearer <id_token>` — no
     SigV4/IAM check applies to that call at all.
   - **Deliberately excludes anything `agentcore deploy` itself needs**
     (CloudFormation/IAM/broader Bedrock AgentCore create-update actions) —
     that's a much larger permission surface, left as an explicit decision
     for work item 8, not folded into this narrowly-scoped role.
   - Synth/diff verified clean before deploying (see CDK output) — the diff
     showed only the two new IAM resources, nothing to any existing stack.
4. **Staging + invoke script** (Python, lives under `agentcore/`): for each
   dataset scenario, `put_object` its `application` dict as
   `input/application.json` under the disposable prefix with a fresh
   `application_id`, `AdminInitiateAuth` for the ID token, POST to
   `/invocations` with that token + a deterministic session-id header,
   collect `{scenario_id: session_id}`.
5. **Ground-truth mapping file**: build the
   `session_id -> expected_response/assertions` JSON `batch-evaluation`
   wants, straight from the dataset's existing per-scenario fields — no new
   ground truth to author, same content Path 1 already reuses.
6. **Run `agentcore run batch-evaluation`** against the resulting sessions
   with `--evaluator fionaa_companies_house_correctness
   fionaa_injection_resistance` first (the two already deployed), verify
   scores manually end-to-end at least once directly against AWS before
   wiring anything into CI — per the standing rule of never trusting a
   fix/pipeline without a real run against real Bedrock/Gateway.
7. **Add AgentCore-native evaluators** worth including:
   `ThirdParty.DeepEval.*`/`AutoEval.*` — `ToolUse`, `TaskCompletion`, and
   especially `PIILeakage` (applications carry `applicant_name`/
   `registered_address`). These are AWS's own reimplementation, distinct
   from the pip `deepeval` package Path 1 uses — see
   `deepeval_evals/README.md`'s opening section for that distinction. Watch
   for the `{assistant_turn}` gotcha documented above (resolves to the last
   node's response, `web_search`, not necessarily the node being graded) on
   any new `TRACE`-level evaluator — apply the same `{context}`-scoped
   instruction pattern already used for
   `fionaa_companies_house_correctness`.
8. **CI workflow**: new `.github/workflows/` file (own file, different
   triggers/permissions from `deepeval-ci.yml` — push-to-master or
   post-merge, not `pull_request`), path-filtered per the mitigation above.
   Steps: assume the new role → `agentcore deploy -y` → stage + invoke →
   batch-evaluate → parse scores → **fail the job on bad scores**. Unlike
   Path 1 (deliberately advisory,`continue-on-error`), this gates, since it
   validates what's about to take production traffic — don't default it to
   advisory.
9. **Cleanup/retention**: disposable scenario data lands in the *real*
   applications bucket under the reserved prefix. Add an S3 lifecycle rule
   scoped to that prefix (or an explicit delete step post-eval) so CI runs
   don't accumulate test data in production storage indefinitely.

### Existing AWS resources to reuse

- Runtime: `fionaa_fionaa-xjO2ci9fd3`
- Memory: `fionaa_FionaaCheckpoint-3GY4rX79ck`
- Evaluators already deployed: `fionaa_injection_resistance`,
  `fionaa_companies_house_correctness`
- Dataset: `fionaa_fionaa_eval_dataset-MBsNVJBQmZ`

(see `.cli/deployed-state.json` for current ARNs/IDs)
