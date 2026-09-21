---
type: orientation
title: Quickstart
description: Top-level routing hub for the repo; start here to reach the runtime entrypoints, graph wiring, security boundaries, deployment and eval operations, and unit tests that own implementation detail.
tags: [quickstart, orientation, runtime, graph, security, operations, testing]
verified:
  - by: openwiki/0.5.1
    at: 2026-09-19T09:15:01.080Z
sources:
  - id: openwiki-source-727d393caa888d84d0bc425e
    resource: repo://fionaa/agentcore/agentcore.json
  - id: openwiki-source-7a952fe224a2dc8405777465
    resource: repo://fionaa/agentcore/EVALS.md
  - id: openwiki-source-e6c0046084c70e3da7bfc0f2
    resource: repo://fionaa/app/main.py
  - id: openwiki-source-ee1287af284d9ff40046f6bc
    resource: repo://fionaa/app/README.md
  - id: openwiki-source-3a7b26ef394512a7b079de22
    resource: repo://fionaa/app/src/fionaa/main.py
  - id: openwiki-source-a24b0d2f77ec96563c6e37e0
    resource: repo://fionaa/README.md
generated: { by: "openwiki/0.5.1", at: "2026-09-19T09:15:01.080Z" }
---

# Quickstart

This page is the repository’s orientation layer. It does not explain every subsystem in detail; instead, it helps you get from the top-level entrypoints to the page that owns the implementation you want to change.

## Recommended reading order

Follow this path when you are new to the repo or about to make a change:

1. **Runtime entrypoints** — understand how the app is launched.
2. **Graph wiring and state** — understand how requests move through the workflow.
3. **Domain and persistence concepts** — understand the data and artifact shapes the workflow carries.
4. **Security and storage** — understand identity, IAM scope, and cross-customer isolation.
5. **AgentCore integration** — understand how the runtime connects to tools, models, and gateway services.
6. **Operations** — understand deployment and evaluation entrypoints.
7. **Unit tests** — understand the fast checks that protect the boundaries above.

## Route map

| Area | Start here | Why it matters |
| --- | --- | --- |
| Runtime bootstrap | `/openwiki/architecture/runtime-entrypoint.md` | Explains the package entrypoint, invocation assembly, and how one AgentCore request becomes scoped dependencies and a graph run. |
| Graph workflow | `/openwiki/architecture/graph-workflow.md` | Explains node order, branching, checkpoints, and terminal paths. |
| Domain and state | `/openwiki/concepts/domain-model.md` | Defines the shared models and checkpointed state that cross workflow boundaries. |
| Persistence and artifacts | `/openwiki/concepts/persistence-and-artifacts.md` | Explains where application data, evidence, and outputs are stored and discovered. |
| Security and storage | `/openwiki/concepts/security-and-storage.md` | Explains verified identity, scoped credentials, and storage isolation. |
| AgentCore integrations | `/openwiki/integrations/agentcore-runtime-and-gateway.md` | Explains the runtime, gateway, and model-loading boundary. |
| Deployment and evals | `/openwiki/operations/deployment-and-evals.md` | Explains configuration, deployment, and evaluation workflows. |
| Unit tests | `/openwiki/testing/unit-tests.md` | Explains the focused tests that guard the runtime and workflow invariants. |

## What to use for what

Use the quickstart to decide where to look next:

- If you are editing `app/main.py` or `app/src/fionaa/main.py`, go to the runtime entrypoint page first.
- If you are changing `app/src/fionaa/graph.py` or any node transition, go to the graph workflow page first.
- If you are touching `app/src/fionaa/security.py`, `app/src/fionaa/storage.py`, or customer scoping, go to the security and storage page first.
- If you are changing `agentcore/agentcore.json`, deployment settings, or eval registration, go to the operations page first.
- If you are validating behavior, go to the unit-test page before widening the change.

## Entry points to keep distinct

The repo uses three different layers that should not be conflated:

- `app/main.py` is the source-archive bootstrap used by AgentCore packaging.
- `app/src/fionaa/main.py` is the installed package’s runtime entrypoint.
- `agentcore/agentcore.json` is the deployment/runtime configuration that tells AgentCore which entrypoint to run and how to register related resources.

Keep that separation in mind when you read the rest of the wiki: the quickstart points you to the owning page, but it does not duplicate the implementation details that belong there.

## Minimal local flow

For a quick local sanity check, the repository README points to the standard AgentCore development commands:

```bash
source /fionaa/app/fionaa/.venv/bin/activate
agentcore dev
agentcore invoke --dev "What can you do"
```

Those commands start the local runtime and then invoke it. For deployment and evaluation workflows, use the dedicated operations page instead of extending this page inline.

## Change triage

When you are deciding where a change belongs, use this rule of thumb:

- **Runtime assembly**: follow the runtime entrypoint page.
- **Node sequencing or branching**: follow the graph workflow page.
- **Identity, storage, or customer isolation**: follow the security and storage page.
- **Model, gateway, or tool loading**: follow the integrations page.
- **Deployment, datasets, evaluators, or runtime evals**: follow the operations page.
- **Fast regression checks and fixtures**: follow the unit-test page.

This page stays intentionally short so it can remain the first stop in the documentation tree.
