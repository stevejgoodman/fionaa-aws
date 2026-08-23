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
2. **One-time manual setup (not CDK, not repeatable infra):** create the
   throwaway Cognito user for the disposable eval identity in the existing
   user pool. Document the email/customer_id pair (customer_id is derived,
   not chosen) somewhere durable — this doc or a memory, not just left in
   someone's shell history.
3. **New CDK stack in `ci-infra/`** (own file/stack, same pattern as
   `github-oidc-stack.ts`, not bolted onto `fionaa/agentcore/cdk/`):
   - Trust: GitHub OIDC, scoped to `push`/`workflow_dispatch` on `master`
     (broader than Path 1's `pull_request`-only trust — reuse the existing
     OIDC provider, don't recreate it).
   - `bedrock-agentcore:InvokeAgentRuntime` scoped to the fionaa runtime ARN.
   - `s3:PutObject` scoped to `fionaa-applications/<eval-harness-customer-id>/*`
     + `kms:GenerateDataKey`/`kms:Encrypt` on the applications KMS key.
   - `cognito-idp:AdminInitiateAuth` scoped to the one throwaway user/pool.
   - `bedrock-agentcore:*BatchEvaluation*` (and whatever read/list actions
     `agentcore run batch-evaluation` needs — check its actual API calls
     rather than guessing the action list).
   - Deliberately no broader S3/data access than that — same "narrow scope"
     principle as `github-oidc-stack.ts`.
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
