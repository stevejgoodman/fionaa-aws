# Automated Reasoning evaluations

These standalone runners use synthetic facts and the application's installed
`PolicyConsistencyChecker`. Run explicitly from `fionaa/` after installing the
application with `uv sync --project app --group dev`:

```sh
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/test_live_guardrail.py
AWS_PROFILE=AIOps AWS_DEFAULT_REGION=us-east-1 app/.venv/bin/python evals/automated_reasoning/test_live_products.py
```

Execution makes billable AWS calls. Importing the runners does not execute them.
They read reviewed policy snapshots and version bindings from
[`agentcore/automated-reasoning/`](../../agentcore/automated-reasoning/README.md)
and write results beside these scripts, independent of the working directory.

- `live-results.json`: eight secured-loan boundary cases.
- `product-live-results.json`: 48 cases covering the other four products.
- `activation-results.json`: recorded deployed-runtime smoke test; the sample
  was referred following a `tooComplex` result, so it does not prove approval.

These results retain their original run dates; moving them does not constitute
a new live evaluation. Policy definitions, IAM configuration, bindings, source
snapshots, extraction review and provisioning remain with deployment assets.
