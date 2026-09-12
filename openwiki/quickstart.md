---
type: quickstart
title: Quickstart
description: Start the app locally, then follow the architecture, security, deployment, and test pages for the graph, storage, runtime, and evaluation flow.
tags: [quickstart, agentcore, langgraph, security, deployment, testing]
verified:
  - by: openwiki/0.5.1
    at: 2026-09-12T16:18:14.763Z
sources:
  - id: openwiki-source-9c608fd4e7b4544481481d22
    resource: repo://fionaa/app/fionaa/graph.py
  - id: openwiki-source-0d072c3d264c95c182cb94cd
    resource: repo://fionaa/app/fionaa/main.py
  - id: openwiki-source-28042c3c40df032b806e018e
    resource: repo://fionaa/app/fionaa/README.md
  - id: openwiki-source-00148c711013e0dd91ed41fe
    resource: repo://fionaa/app/fionaa/security.py
  - id: openwiki-source-cf842277611ee35d9ba6ad50
    resource: repo://fionaa/app/fionaa/storage.py
  - id: openwiki-source-9968ff105989269e2c54af38
    resource: repo://fionaa/app/fionaa/tests/test_graph.py
  - id: openwiki-source-a24b0d2f77ec96563c6e37e0
    resource: repo://fionaa/README.md
generated: { by: "openwiki/0.5.1", at: "2026-09-12T16:18:14.763Z" }
---

# Quickstart

## Start locally

1. Activate the app environment:
   ```bash
   source /fionaa/app/fionaa/.venv/bin/activate
   ```
2. Run the AgentCore dev server:
   ```bash
   agentcore dev
   ```
3. In another terminal, invoke it:
   ```bash
   agentcore invoke --dev "What can you do"
   ```

## What to read next

- [Runtime entrypoint and invocation flow](/openwiki/architecture/runtime-entrypoint.md) for how `main.py` derives identity, loads scoped resources, and starts the LangGraph run.
- [Graph workflow and node responsibilities](/openwiki/architecture/graph-workflow.md) for the state machine, branching, checkpointing, and evidence writes.
- [Security, identity, and storage isolation](/openwiki/concepts/security-and-storage.md) for the IAM boundary, scoped S3 access, and customer isolation.
- [Deployment, batch evaluation, and eval harnesses](/openwiki/operations/deployment-and-evals.md) when you are ready to deploy or run evals.
- [Unit tests and boundary fakes](/openwiki/testing/unit-tests.md) for the focused tests that lock in graph, storage, and checkpoint behavior.

## Fast routing map

- Use `main.py` as the runtime entrypoint.
- Use `security.py` and `storage.py` to understand identity scoping and S3 access.
- Use `graph.py` to trace node flow and artifact persistence.
- Use `tests/test_graph.py` to see the invariants exercised in practice.
