"""Converts fionaa_eval_dataset.jsonl into DeepEval Goldens.

Each record's turns[0].input is already the JSON-encoded `application` dict
(or "Company: X" string for web-search scenarios) that graph.py's nodes
expect as message content -- see graph.py's HumanMessage(content=...) calls
at the companies_house/policy_check/web_search nodes. No reshaping is needed
going in, unlike AgentCore's native --dataset eval runners, which assume a
chat-turn payload fionaa's actual entrypoint (main.py:invoke) doesn't accept
(see ../EVALS.md).

This is meant to replace run_node_evals.py's dataset loading + custom
LLM-as-judge scoring with DeepEval's own metrics (see metrics.py) -- the
dataset itself doesn't change.
"""

from __future__ import annotations

import json
from pathlib import Path

from deepeval.dataset import Golden

DATASET_PATH = Path(__file__).resolve().parent.parent / "datasets" / "fionaa_eval_dataset.jsonl"


def load_goldens(prefix: str | None = None) -> list[Golden]:
    """Loads all scenarios as Goldens, optionally filtered to one node kind
    by its scenario_id prefix ("companies-house-", "policy-check-",
    "web-search-") -- same routing convention run_node_evals.py's
    `_node_kind` used.

    `assertions` / `expected_trajectory` from the dataset ride along in
    `additional_metadata` rather than being folded into the Golden's own
    fields, since DeepEval's built-in fields (expected_output, tools_called,
    etc.) get set on the LLMTestCase once a node has actually run -- a
    Golden only holds what's known ahead of time.
    """
    goldens = []
    with DATASET_PATH.open() as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            if prefix and not record["scenario_id"].startswith(prefix):
                continue
            turn = record["turns"][0]
            goldens.append(
                Golden(
                    input=turn["input"],
                    expected_output=turn.get("expected_response"),
                    additional_metadata={
                        "scenario_id": record["scenario_id"],
                        "assertions": record.get("assertions") or [],
                        "expected_trajectory": record.get("expected_trajectory") or [],
                    },
                )
            )
    return goldens
