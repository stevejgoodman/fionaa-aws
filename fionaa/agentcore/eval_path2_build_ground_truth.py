"""Path 2 ground-truth mapping: builds the JSON file `agentcore run
batch-evaluation --ground-truth <path>` wants, straight from the dataset's
existing per-scenario fields plus the session IDs
eval_path2_stage_and_invoke.py produced.

See ../agentcore/EVALS.md's "Path 2 plan" section (work item 5).

File shape confirmed against the CLI's own parser (decompiled
`@aws/agentcore`), not guessed: either a bare JSON array of session-metadata
entries, or an object with a `sessionMetadata` key holding that array. Each
entry:

    {
      "sessionId": "...",
      "testScenarioId": "...",
      "groundTruth": {
        "inline": {
          "assertions": [{"text": "..."}, ...],
          "expectedTrajectory": {"toolNames": ["...", ...]},
          "turns": [{"input": {"prompt": "..."}, "expectedResponse": {"text": "..."}}]
        }
      }
    }

Matches the AWS SDK's StartBatchEvaluation request shape exactly (same
`evaluationMetadata.sessionMetadata` structure), since the CLI passes this
straight through.

Usage:
    cd fionaa/agentcore
    python3 eval_path2_build_ground_truth.py \\
        --session-map .cli/path2-session-map.json \\
        --output .cli/path2-ground-truth.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "fionaa_eval_dataset.jsonl"


def load_dataset_by_scenario_id() -> dict[str, dict[str, Any]]:
    by_id = {}
    with DATASET_PATH.open() as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            by_id[record["scenario_id"]] = record
    return by_id


def build_session_metadata_entry(scenario_id: str, session_id: str, record: dict[str, Any]) -> dict[str, Any]:
    turn = record["turns"][0]
    inline: dict[str, Any] = {}

    assertions = record.get("assertions") or []
    if assertions:
        inline["assertions"] = [{"text": a} for a in assertions]

    expected_trajectory = record.get("expected_trajectory") or []
    if expected_trajectory:
        inline["expectedTrajectory"] = {"toolNames": expected_trajectory}

    expected_response = turn.get("expected_response")
    if expected_response:
        inline["turns"] = [{"input": {"prompt": turn["input"]}, "expectedResponse": {"text": expected_response}}]

    return {
        "sessionId": session_id,
        "testScenarioId": scenario_id,
        "groundTruth": {"inline": inline},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--session-map",
        required=True,
        help="path to the scenario_id -> {session_id, ...} JSON eval_path2_stage_and_invoke.py wrote",
    )
    parser.add_argument("--output", required=True, help="path to write the ground-truth JSON file")
    args = parser.parse_args()

    session_map = json.loads(Path(args.session_map).read_text())
    dataset_by_id = load_dataset_by_scenario_id()

    entries = []
    skipped = []
    for scenario_id, run_result in session_map.items():
        if run_result.get("status_code") != 200:
            skipped.append((scenario_id, f"status_code={run_result.get('status_code')}"))
            continue
        record = dataset_by_id.get(scenario_id)
        if record is None:
            skipped.append((scenario_id, "no matching dataset entry"))
            continue
        entries.append(build_session_metadata_entry(scenario_id, run_result["session_id"], record))

    if skipped:
        print(f"Skipped {len(skipped)} scenario(s):")
        for scenario_id, reason in skipped:
            print(f"  {scenario_id}: {reason}")

    if not entries:
        raise SystemExit("no ground-truth entries built -- nothing to write")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=2))
    print(f"\nWrote {len(entries)} ground-truth entries to {out_path}")


if __name__ == "__main__":
    main()
