#!/usr/bin/env python3
"""Pulls one real FIONAA run out of AgentCore checkpoint memory + S3 evidence
artifacts and writes it as run-data.json for job-dashboard.html to replay.

This is the "poller" the dashboard design calls for, run in one-shot mode
against a completed historical run (see fionaa_architecture design notes /
conversation). Re-run with --watch to poll on an interval and re-write the
JSON (and, with --publish, re-run the Artifact side) whenever new checkpoint
steps appear -- the same code path this script would use against a run
that's still in flight.

Usage:
    .venv/bin/python fetch_run.py \
        --profile AIOps --region us-east-1 \
        --customer-id 17deb75df387eafcea144caa24f896e85216c2622721c6c33c6c1b8cd73eae18 \
        --application-id 01388386fce828d9048d8af69701df07167447e76b4e2a91ef675881b44b0f59 \
        --out run-data.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

FIONAA_APP_DIR = Path(__file__).resolve().parents[2] / "fionaa"

# Required at import time by graph.py/storage.py/security.py -- values only
# need to be *present*; the ones that matter (bucket, memory id) are set from
# real CLI args below. See those modules' module-level os.environ[...] reads.
os.environ.setdefault("FIONAA_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::000000000000:role/unused-for-reads")
os.environ.setdefault("FIONAA_POLICY_DOCS_BUCKET", "unused-for-this-script")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(FIONAA_APP_DIR))


STATE_KEYS_BY_NODE = {
    "load_application": ["application", "annual_accounts", "bank_statements"],
    "policy_check": ["policy_check"],
    "companies_house": ["companies_house", "companies_house_found"],
    "financial_assessment": ["financial_assessment"],
    "web_search": ["web_search"],
    "reject_no_company": ["final_decision"],
    "synthesize_decision": ["final_decision"],
}

ARTIFACT_BY_NODE = {
    "policy_check": "policy_check/result.json",
    "companies_house": "companies_house/result.json",
    "financial_assessment": "financial_assessment/result.json",
    "web_search": "web_search/result.json",
    "reject_no_company": "decision/result.json",
    "synthesize_decision": "decision/result.json",
}


def build_steps(history, store) -> list[dict]:
    """Turns a chronological list of LangGraph StateSnapshots into one entry
    per node that actually ran.

    AgentCoreMemorySaver's checkpoint metadata doesn't populate `writes`
    (unlike e.g. MemorySaver), so the node that completed between snapshot i
    and i+1 is read off `.next` instead: `.next` names the node that was
    *about to run* at snapshot i, and it has run by the time snapshot i+1
    exists -- `.created_at` on i+1 is therefore that node's real finish
    time, and the gap since snapshot i's `.created_at` is its real duration.

    Node output text/tool_calls are backfilled from the node's own S3
    evidence artifact where one exists (policy_check/result.json etc) --
    richer than the bare state value, which lacks tool_calls.
    """
    from datetime import datetime

    def parse(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None

    steps = []
    for i in range(len(history) - 1):
        node_id = history[i].next[0] if history[i].next else None
        if not node_id or node_id == "__start__":
            continue

        before, after = history[i].values, history[i + 1].values
        keys = STATE_KEYS_BY_NODE.get(node_id, [])
        state_value = {k: after.get(k) for k in keys if after.get(k) != before.get(k)}

        t0, t1 = parse(history[i].created_at), parse(history[i + 1].created_at)
        duration_ms = int((t1 - t0).total_seconds() * 1000) if t0 and t1 else None

        evidence = None
        rel_key = ARTIFACT_BY_NODE.get(node_id)
        if rel_key:
            try:
                evidence = store.get_json(rel_key)
            except Exception as exc:  # pragma: no cover -- best-effort enrichment
                evidence = {"_fetch_error": str(exc)}

        steps.append(
            {
                "node": node_id,
                "timestamp": history[i + 1].created_at,
                "duration_ms": duration_ms,
                "state_value": state_value,
                "evidence": evidence,
            }
        )
    return steps


async def _fetch(args) -> dict:
    import boto3

    os.environ["FIONAA_APPLICATIONS_BUCKET"] = args.bucket
    os.environ["FIONAA_CHECKPOINT_MEMORY_ID"] = args.memory_id

    import graph as g  # noqa: E402  (env vars must be set first)
    import security as sec  # noqa: E402
    import storage as st  # noqa: E402

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = sec.CustomerIdentity(customer_id=args.customer_id, application_id=args.application_id)
    checkpointer = g.build_checkpointer(session)
    compiled = g.build_graph(checkpointer=checkpointer)
    config = g.checkpoint_config(identity)

    # get_state_history yields newest-first; reverse for chronological replay.
    history = [snap async for snap in compiled.aget_state_history(config)]
    history.reverse()

    if not history:
        return {"found": False}

    store = st.ApplicationStore(identity, session)
    application = store.get_json("input/application.json")
    steps = build_steps(history, store)
    final_values = history[-1].values

    return {
        "found": True,
        "fetched_at": None,  # filled by caller (Date.now() unavailable here on purpose -- caller stamps it)
        "customer_id": args.customer_id,
        "application_id": args.application_id,
        "application": application,
        "steps": steps,
        "final_decision": final_values.get("final_decision"),
        "companies_house_found": final_values.get("companies_house_found"),
        "step_count": len(history),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="AIOps")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--memory-id", default="fionaa_FionaaCheckpoint-3GY4rX79ck")
    parser.add_argument("--bucket", default="fionaa-6655-assets")
    parser.add_argument("--customer-id", required=True, help="actor_id / hashed customer_id")
    parser.add_argument("--application-id", required=True, help="thread_id / session_id")
    parser.add_argument("--out", default="run-data.json")
    parser.add_argument("--watch", type=int, default=0, help="poll every N seconds instead of running once")
    args = parser.parse_args()

    def run_once():
        data = asyncio.run(_fetch(args))
        data["fetched_at_epoch"] = time.time()
        out_path = Path(args.out)
        out_path.write_text(json.dumps(data, indent=2, default=str))
        print(f"[{time.strftime('%H:%M:%S')}] wrote {out_path} -- {data.get('step_count', 0)} steps, "
              f"found={data.get('found')}")
        return data

    if not args.watch:
        run_once()
        return

    last_count = None
    while True:
        data = run_once()
        count = data.get("step_count")
        if count == last_count:
            print("  (no new steps)")
        last_count = count
        if data.get("final_decision") is not None:
            print("  run complete (final_decision present) -- stopping watch")
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
