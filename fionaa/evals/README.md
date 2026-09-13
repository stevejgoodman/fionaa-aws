# FIONAA evaluations

Both evaluation paths share `datasets/fionaa_eval_dataset.jsonl`:

- `node/`: DeepEval node assessments, metrics and judge calibration.
- `automated_reasoning/`: standalone ARC boundary runners and recorded results.
- `runtime/`: stage disposable applications, invoke the deployed runtime,
  build ground truth and check batch evaluation results.

Infrastructure and deployment state remain in `../agentcore/`. Run CLI commands
from `fionaa/agentcore` so AgentCore finds the project and `.cli` output paths:

```sh
../app/.venv/bin/deepeval test run ../evals/node/test_policy_check.py
../app/.venv/bin/python ../evals/runtime/eval_path2_stage_and_invoke.py --help
```

Live evaluation execution requires the existing AWS/Gateway configuration and
incurs API calls. Node suites should run sequentially to respect shared quotas.
See [node evaluation guidance](node/README.md) and the
[evaluation history and runtime instructions](../agentcore/EVALS.md).

The dataset content, scenario IDs, metrics and thresholds were preserved during
this relocation. The AgentCore dataset configuration references the shared file
at `../evals/datasets/fionaa_eval_dataset.jsonl` relative to `agentcore/`.
