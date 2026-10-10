.PHONY: help test eval-node eval-node-calibrate eval-path2-stage eval-path2-ground-truth eval-path2-gate agentcore-dev agentcore-invoke

VENV_PY := app/.venv/bin/python
VENV_PYTEST := app/.venv/bin/pytest
VENV_DEEPEVAL := app/.venv/bin/deepeval

help:
	@echo "test                   - run local unit tests (app/tests)"
	@echo "eval-node              - run DeepEval node evals (evals/node)"
	@echo "eval-node-calibrate    - run DeepEval judge calibration script"
	@echo "eval-path2-stage       - stage disposable applications + invoke deployed runtime"
	@echo "eval-path2-ground-truth- build ground-truth mapping from staged sessions"
	@echo "eval-path2-gate        - check latest batch-evaluation result against gate thresholds"
	@echo "agentcore-dev          - run the agent locally via 'agentcore dev'"
	@echo "agentcore-invoke       - invoke the local 'agentcore dev' server (PROMPT=\"...\")"

test:
	cd app && .venv/bin/pytest

eval-node:
	cd agentcore && ../$(VENV_DEEPEVAL) test run ../evals/node/

eval-node-calibrate:
	cd agentcore && ../$(VENV_PY) ../evals/node/calibrate_judges.py

eval-path2-stage:
	cd agentcore && ../$(VENV_PY) ../evals/runtime/eval_path2_stage_and_invoke.py

eval-path2-ground-truth:
	cd agentcore && ../$(VENV_PY) ../evals/runtime/eval_path2_build_ground_truth.py

eval-path2-gate:
	cd agentcore && ../$(VENV_PY) ../evals/runtime/eval_path2_check_gate.py

agentcore-dev:
	cd agentcore && agentcore dev

agentcore-invoke:
	cd agentcore && agentcore invoke --dev "$(PROMPT)"
