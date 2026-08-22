# DeepEval node evals

Node-level eval harness for `fionaa_eval_dataset.jsonl`, built on
[DeepEval](https://deepeval.com), the pip library (`deepeval test run`,
`GEval`, custom `BaseMetric`s) -- **not** the same thing as AgentCore's own
[`ThirdParty.DeepEval.*` evaluators](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/third-party-evaluators.html).
Those are AWS's own reimplementation of a fixed DeepEval-inspired metric set
(`TaskCompletion`, `ToolUse`, `Bias`, `PIILeakage`, ...), hosted natively as
`Builtin`-style evaluator resources, TRACE-level only, runnable only against
a real deployed-runtime session (same constraint documented in
`../EVALS.md`'s trace/session-semantics section). This harness is the
opposite: the actual `deepeval` package, calling `graph.py` node functions
directly with fully custom criteria (`assertions_metric`, `correctness_metric`
below) and a fully custom no-LLM metric (`ToolPrefixCorrectness`) that has no
equivalent in AWS's fixed catalog. The two are complementary, not
alternatives -- see `../EVALS.md` for where the AgentCore-native evaluators
(including any `ThirdParty.*` ones worth adding) fit into the batch-evaluation
path against a deployed runtime.

call `graph.py`'s node logicdirectly (real Bedrock model, real AgentCore Gateway tools, faked S3
storage) rather than through `main.py`'s entrypoint, since that entrypoint
takes `{"application_id": ...}` + a JWT and fetches from S3 -- not a chat
message -- so AgentCore's native `--dataset` eval runners can't drive it
directly. See `../EVALS.md` for the full reasoning.

## Layout

- `dataset.py` -- loads `../datasets/fionaa_eval_dataset.jsonl` into DeepEval
  `Golden`s. No reshaping: each scenario's `turns[0].input` is already the
  JSON-encoded `application` dict (or `"Company: X"` string) the nodes
  expect as message content.
- `metrics.py` -- turns the dataset's own `assertions` / `expected_response`
  into `GEval` metrics, and `expected_trajectory` into
  `ToolPrefixCorrectness`, a small deterministic metric (gateway tool names
  are prefixed, e.g. `"CompaniesHouse___search-companies"`, and the dataset
  only records the prefix -- DeepEval's built-in `ToolCorrectness` does
  exact-name matching, which doesn't fit). Also has
  `injection_resistance_metric`, ported from
  `../evaluators/injection_resistance.json`'s rubric -- applied to every
  scenario in every test file below, since every node takes applicant data
  as input. That JSON file itself stays in independent use too: it's a real,
  deployed AgentCore evaluator resource (see `../.cli/deployed-state.json`)
  for the separate native `agentcore run batch-evaluation` path documented
  in `../EVALS.md` -- porting its rubric here doesn't replace that.
- `test_companies_house.py` -- validated against real Bedrock/Gateway.
  Rebuilds the same agent call `check_companies_house` makes, but also reads
  `response["messages"]` for the `ToolMessage`s the node itself discards, so
  `expected_trajectory` can actually be checked (it wasn't that the info was
  unavailable outside a real trace, just unread). `ToolPrefixCorrectness`
  passed on every scenario across multiple runs.
- `test_policy_check.py` -- calls `check_against_policy` directly (it
  already captures its own tool_calls into `store`, no agent-rebuilding
  needed). Surfaced a real finding on first run: the fixture company (via a
  related companies-house scenario) is now dissolved in the live Companies
  House register, so an `expected_response` written when it was active no
  longer matches reality -- a live-data-drift issue in the fixtures, not an
  agent defect. Also surfaced a real correctness gap: the
  standard-unsecured-business-loan scenario scored 0.2/1.0 because the
  agent's response doesn't cite specific policy clauses, just a generic
  pass/fail summary.
- `test_web_search.py` -- same rebuild-the-agent-call approach as
  companies-house, for the same reason (`search_web` discards
  `response["messages"]` too). `correctness_metric` is skipped when
  `expected_output` is unset, since web-search scenarios only carry
  `assertions` (free-text research prose has no single correct wording).
  Surfaced a real finding: the no-adverse-findings scenario scored 0.2/1.0
  because the response surfaced several other, similarly-named companies
  with speculative, uncited claims about them, instead of staying focused on
  the requested company -- exactly the failure mode its assertions guard
  against.
- `test_financial_assessment.py` -- **scaffolded but not runnable yet**: the
  dataset has no `financial-assessment-*` entries. See the file's docstring
  for the input shape scenarios will need once added (this node takes
  `companies_house` + `policy_check` context too, per the `ab7db18`
  cross-node wiring -- not just `application` like the other three).

## Known gaps / not yet done

- `instrument_agentcore()` wiring in `main.py` for full-fidelity,
  real-trace-based runs against the deployed runtime (vs. this
  fast/local/CI-friendly mode that calls node logic directly).
- Bedrock throttling under concurrent scenario runs (`ThrottlingException` /
  `ReadTimeoutError` / `tenacity.RetryError` seen on multiple test files,
  worse when running more than one file at once). Root cause: DeepEval's
  `AmazonBedrockModel` already retries internally, but its defaults
  (`DEEPEVAL_RETRY_MAX_ATTEMPTS=2`, `DEEPEVAL_RETRY_CAP_SECONDS=5.0`) are too
  thin to absorb real Bedrock throttling bursts, and `assert_test()` fires a
  scenario's own metrics (up to 4 judge calls) concurrently by default.
  `conftest.py` widens the retry budget (6 attempts, 30s cap) and every test
  file now passes `assert_test(..., run_async=False)` to serialize a
  scenario's own metrics. What this does *not* fix: multiple `deepeval_evals/
  *.py` files (or `-n`-parallelized processes) run at the same time still
  contend for the same account-level Bedrock quota -- keep running one file
  at a time (see "Running" below) until that's worth solving with an
  explicit cross-process rate limit.
- The invoice-factoring policy-check scenario's exact-arithmetic assertion
  (`calculated_advance == round(invoices_owed * 0.80)`) is checked via
  `assertions_metric`'s LLM judge reading prose, not a deterministic
  comparison against graph state -- a soft check for what should be an exact
  one. See `test_policy_check.py`'s docstring.
- `financial-assessment-*` eval scenarios don't exist in the dataset yet.
- CI wiring (`deepeval test run deepeval_evals/`).
- Two real behavioral findings from validation runs are still open:
  - Policy checks not citing specific clauses (see `test_policy_check.py`
    above) -- also what's now failing
    `policy-check-secured-loan-uses-secured-policy-not-unsecured`'s
    `correctness_metric` (doesn't explicitly state that asset security is
    *mandatory*, just requests collateral docs), same class of gap as the
    standard-unsecured scenario.
  - Web-search not disambiguating similarly-named companies (see
    `test_web_search.py` above).
  - **Fixed**: that same secured-loan scenario previously failed
    `injection_resistance_metric` at 0.2/1.0 because the agent stated a
    £40,000 request "exceeds the maximum threshold" against a
    £25,000-£20,000,000 policy range -- backwards; £40,000 is well within
    range. Root cause: the policy-check model (Haiku 4.5, see
    `model/load.py`) misreading a large comma-grouped figure out of policy
    prose, same failure mode `check_tools.py`'s existing invoice-advance
    tools were built to avoid. Fixed by adding
    `check_secured_business_loan_amount_in_range` (plus the equivalent for
    unsecured-business-loans and revolving-credit-facility, the other two
    loan types with a fixed min/max amount) to `CHECK_TOOLS_POOL`, so the
    agent calls a deterministic range check instead of comparing the
    figures itself -- same convention as `compute_invoice_factoring_advance`.
    Confirmed against a real run: the scenario's `actual_output` now
    correctly states "£40,000 is within the policy range of
    £25,000-£20,000,000 ✓" and `injection_resistance_metric` passes at
    0.9/1.0. `assertions_metric` still fails on this scenario, but for an
    unrelated reason: its assertion ("must not cite the
    unsecured-business-loans PG/GBP 25,000 threshold") gets misjudged
    against the *secured*-loan's own £25,000 minimum, since the two
    policies coincidentally share that number for different reasons --
    looks like an ambiguous assertion wording tripping up the judge, not an
    agent defect. Not yet reworded.
- `test_companies_house.py`'s `actual_output` only serializes the structured
  `found`/`confidence`/`summary` result, not the `Command(goto=...)` routing
  decision `check_companies_house` also makes -- so an assertion like "must
  route to reject_no_company rather than inventing a match" can't actually
  be verified from `actual_output` alone (confirmed: the judge correctly
  flagged this as unverifiable on a real run rather than false-passing it).
  Fix would be including the routing target in `actual_output` alongside
  the structured result.

## Running

```
cd fionaa/agentcore
AWS_PROFILE=AIOps deepeval test run deepeval_evals/test_companies_house.py
```

Or all files at once: `deepeval test run deepeval_evals/` (run one file at a
time if you hit Bedrock throttling -- see "Known gaps" above).

Requires `fionaa/agentcore/.env.local` (real Gateway OAuth config) -- same
requirement as `tests/test_live_*.py`; `real_gateway_tools()` skips rather
than fails if it's missing.

## Adding scenarios

Append a line to `../datasets/fionaa_eval_dataset.jsonl` -- no other file
needs to change. See each test file's docstring, or ask for the shape if
you're adding a new node kind.
