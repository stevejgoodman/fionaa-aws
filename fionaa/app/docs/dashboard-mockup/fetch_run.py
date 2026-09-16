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

FIONAA_APP_DIR = Path(__file__).resolve().parents[2] / "src" / "fionaa"

# Required at import time by graph.py/storage.py/security.py -- values only
# need to be *present*; the ones that matter (bucket, memory id) are set from
# real CLI args below. See those modules' module-level os.environ[...] reads.
os.environ.setdefault("FIONAA_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::000000000000:role/unused-for-reads")
os.environ.setdefault("FIONAA_POLICY_DOCS_BUCKET", "unused-for-this-script")
os.environ.setdefault("FIONAA_KMS_KEY_ARN", "alias/aws/s3")  # only put_json uses this; get_json doesn't
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(FIONAA_APP_DIR))

from redaction import (  # noqa: E402  (needs sys.path insert above first)
    redact_annual_accounts,
    redact_application,
    redact_bank_statements,
    redact_prose_values,
    redact_tool_calls,
    sensitive_prose_values,
)


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

def build_steps(history, store, sensitive_values: list[str]) -> list[dict]:
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
    richer than the bare state value, which lacks tool_calls. That evidence
    is LLM-authored prose (clause_findings, discrepancies, etc) which can
    quote raw address/birth-year detail verbatim -- see
    redaction.redact_prose_values -- so sensitive_values (computed once by
    the caller from the run's own application/annual_accounts/
    bank_statements) is scrubbed out of it here, on top of the
    redact_application/etc. structured-field redaction below.
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
        if node_id == "load_application":
            if "application" in state_value:
                state_value["application"] = redact_application(state_value["application"])
            if "annual_accounts" in state_value:
                state_value["annual_accounts"] = redact_annual_accounts(state_value["annual_accounts"])
            if "bank_statements" in state_value:
                state_value["bank_statements"] = redact_bank_statements(state_value["bank_statements"])
        # Every subsequent node's state_value can carry LLM-authored prose
        # that quotes address/birth-year detail verbatim (policy_check's
        # clause_findings, companies_house's summary, financial_assessment's
        # discrepancies, web_search's narrative) -- redact_application/etc.
        # above only fix the structured load_application fields themselves.
        state_value = redact_prose_values(state_value, sensitive_values)

        t0, t1 = parse(history[i].created_at), parse(history[i + 1].created_at)
        duration_ms = int((t1 - t0).total_seconds() * 1000) if t0 and t1 else None

        evidence = None
        rel_key = ARTIFACT_BY_NODE.get(node_id)
        if rel_key:
            try:
                evidence = store.get_json(rel_key)
                # Second line of defense on top of graph.py's own
                # redact_tool_calls call at write time -- catches evidence
                # written before that fix shipped, and is a no-op otherwise
                # (redacting already-redacted content changes nothing).
                if evidence and "tool_calls" in evidence:
                    evidence["tool_calls"] = redact_tool_calls(evidence["tool_calls"])
                if evidence:
                    evidence = redact_prose_values(evidence, sensitive_values)
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
    final_values = history[-1].values
    # Computed once from the run's own final state (application/
    # annual_accounts/bank_statements persist unchanged in state once
    # load_application sets them), then scrubbed out of every LLM-authored
    # prose field below -- see redaction.sensitive_prose_values.
    sensitive_values = sensitive_prose_values(
        final_values.get("application"), final_values.get("annual_accounts"),
        final_values.get("bank_statements"),
    )
    # redact_application leaves year_of_birth alone by design (see its
    # docstring -- it's not sensitive as a standalone structured field), but
    # the user-facing header/detail panel here is exactly the kind of
    # display this run asked to have birth-year redacted from, so also
    # apply the prose scrub (which does catch it, via sensitive_values).
    application = redact_prose_values(redact_application(store.get_json("input/application.json")), sensitive_values)
    steps = build_steps(history, store, sensitive_values)
    final_decision = redact_prose_values(final_values.get("final_decision"), sensitive_values)
    # validate_final_decision's own evidence blob (decision/result.json's
    # validation.evidence) is the *raw* application/annual_accounts/
    # bank_statements -- see workflow/validation.py's _facts() -- not the
    # structurally-redacted copy build_steps already applied to
    # load_application's state_value above. redact_prose_values' substring
    # scrub catches address text inside it, but not e.g. a raw bank account
    # number, so re-apply the structured redactors here too.
    evidence = (final_decision or {}).get("validation", {}).get("evidence")
    if evidence:
        evidence["application_self_reported"] = redact_application(evidence.get("application_self_reported"))
        evidence["annual_accounts"] = redact_annual_accounts(evidence.get("annual_accounts"))
        evidence["bank_statements"] = redact_bank_statements(evidence.get("bank_statements"))

    return {
        "found": True,
        "fetched_at": None,  # filled by caller (Date.now() unavailable here on purpose -- caller stamps it)
        "customer_id": args.customer_id,
        "application_id": args.application_id,
        "application": application,
        "steps": steps,
        "final_decision": final_decision,
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
