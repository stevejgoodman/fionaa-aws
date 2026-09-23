# Automated Reasoning evaluations

These standalone runners use synthetic facts and the application's installed
`PolicyConsistencyChecker`. Run explicitly from `fionaa/` after installing the
application with `uv sync --project app --group dev`:

```sh
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/test_live_guardrail.py
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/test_live_products.py
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/diagnose_inconclusive.py
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/probe_premise_handling.py
```

Execution makes billable AWS calls. Importing the runners does not execute them.
They read reviewed policy snapshots and version bindings from
[`agentcore/automated-reasoning/`](../../agentcore/automated-reasoning/README.md)
and write results beside these scripts, independent of the working directory.

- `live-results.json`: eight secured-loan boundary cases.
- `product-live-results.json`: 48 cases covering the other four products.
- `inconclusive-diagnosis.json`: written by `diagnose_inconclusive.py`, which
  asks *why* a clear-cut application still comes back inconclusive. Each row
  names the policy variables the engine had to vary because no fact pinned
  them, and any fact that was sent but never became a premise. Unlike the two
  runners above it asserts no expected outcome -- it is a diagnostic, and it
  exits non-zero while any case is still undecided.
- `premise-handling-probe.json`: written by `probe_premise_handling.py`, which
  measures the two causes the diagnosis found -- how many facts it takes to
  trigger `tooComplex`, and which content qualifier actually turns a fact into
  a premise. Run it before changing how requests are built; its `premises`
  column is the thing to read.
- `activation-results.json`: recorded deployed-runtime smoke test; the sample
  was referred following a `tooComplex` result, so it does not prove approval.

Run 2026-09-23 (`inconclusive-diagnosis.json`) found that the inconclusive
results on complete applications are not caused by the missing derived facts
recorded in `ar_claims.KNOWN_UNCOVERED_VARIABLES`. With 16-18 facts the engine
returns `tooComplex` and does not solve at all; with one fact it solved but
placed that fact in `claims` rather than `premises`, leaving nothing to reason
from. Both are properties of how the request is assembled, so adding further
derived facts would make the first one worse. That gap remains real and still
has to be closed, but it is not what is blocking these claims.

These results retain their original run dates; moving them does not constitute
a new live evaluation. Policy definitions, IAM configuration, bindings, source
snapshots, extraction review and provisioning remain with deployment assets.
