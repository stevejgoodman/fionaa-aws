# Offline evals for fionaa

Status: **registered locally, not yet deployed to AWS.** See "Deploying" below.

## What's here

- `agentcore/datasets/fionaa_eval_dataset.jsonl` — `AGENTCORE_EVALUATION_PREDEFINED_V1`
  dataset, registered in `agentcore.json`. 7 scenarios covering the three
  evidence-gathering nodes in `graph.py`:
  - Companies House verification (`check_companies_house`) — a real active
    company (Goodman's Consulting Ltd, 08139267, reused from
    `tests/test_live_companies_house.py`) in exact and fuzzy/noisy-input
    form, plus two fictitious-company cases that must resolve to
    `found=false`.
  - Policy check (`check_against_policy`) — one standard unsecured-business
    loan scenario, asserting the response cites a specific policy clause
    rather than a bare accept/reject.
  - Web search (`search_web`) — one scenario asserting no fabricated adverse
    findings.

  Each `turns[].input` is the JSON-encoded `application` dict, matching what
  `graph.py` actually sends as message content to each node
  (`json.dumps(application)` / `f"Company: {company_name}"`).

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
2. **Node-level harness** (more direct, needs a small script): call
   `graph.check_companies_house`/`check_against_policy`/`search_web`
   directly with a `FakeRuntime`/real tools per scenario (same shape as
   `tests/test_graph.py`), collect the response, and score it with
   `agentcore run eval --evaluator-arn <arn> ...` in standalone mode, or via
   the `bedrock_agentcore.evaluation` SDK's `evaluate()` call directly.

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
