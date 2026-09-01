# Fionaa job-run dashboard

A visualization of one FIONAA loan application moving through the LangGraph
pipeline on AgentCore, stage by stage, ending in the synthesized decision.
Published as a Claude Artifact for review; the exact same file also opens
standalone in any browser (fully self-contained, no server required).

## Files

- **job-dashboard.html** — the page template. Contains a
  `<script id="run-data" type="application/json">__RUN_DATA_JSON__</script>`
  placeholder instead of real data, so this is what's safe to commit.
- **fetch_run.py** — pulls one real run out of AgentCore checkpoint memory
  (`AgentCoreMemorySaver` via the real `graph.py`/`compiled_graph.aget_state_history`)
  plus its S3 evidence artifacts (`ApplicationStore`), and writes it to
  `run-data.json`. Gitignored — this can contain real applicant financial/legal detail.
- **build.py** — injects `run-data.json` into the template, producing
  `job-dashboard.rendered.html`. Also gitignored, for the same reason.

## Regenerating

```
cd fionaa/app/docs/dashboard-mockup
../../fionaa/.venv/bin/python fetch_run.py \
  --profile AIOps --region us-east-1 \
  --customer-id <actor_id / hashed customer_id> \
  --application-id <thread_id / S3 application folder UUID>
../../fionaa/.venv/bin/python build.py
```

Then publish `job-dashboard.rendered.html` as the Artifact.

## Why the page can't poll AWS directly

The Artifact page's CSP blocks calls to arbitrary hosts (Google Fonts is the
one exception), and there's no AgentCore MCP connector wired up, so the
polling has to happen outside the browser: re-run `fetch_run.py` (it has a
`--watch N` mode for polling every N seconds until `final_decision` appears)
and re-publish `job-dashboard.rendered.html` when it changes. Every open
viewer of a republished Artifact auto-reloads to the new version.

## Design notes

- **customer-scoped access**: `fetch_run.py` builds `CustomerIdentity` and
  reads through the real `graph.build_checkpointer`/`ApplicationStore`, the
  same code path the app itself uses — no separate read model.
- **sequencing**: `AgentCoreMemorySaver`'s checkpoint metadata doesn't
  populate `writes` the way e.g. `MemorySaver` does. The node that completed
  between snapshot *i* and *i+1* is read off `.next` instead — see
  `build_steps()` in fetch_run.py for the reasoning.
- **content**: checkpoint state gives the sequencing/timing; each node's own
  S3 evidence artifact (`policy_check/result.json` etc) gives the richer
  detail (tool calls) the bare state value lacks.
