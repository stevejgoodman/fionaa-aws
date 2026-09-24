---
type: orientation
title: Quickstart
description: Top-level routing hub for FIONAA; start here to reach the runtime entrypoint, graph workflow, security and storage boundary, deployment operations, and the test suites that protect safe changes.
tags: [quickstart, orientation, runtime, graph, security, operations, testing]
verified:
  - by: openwiki/0.5.1
    at: 2026-09-24T15:24:05.146Z
sources:
  - id: openwiki-source-727d393caa888d84d0bc425e
    resource: repo://fionaa/agentcore/agentcore.json
  - id: openwiki-source-7a952fe224a2dc8405777465
    resource: repo://fionaa/agentcore/EVALS.md
  - id: openwiki-source-e6c0046084c70e3da7bfc0f2
    resource: repo://fionaa/app/main.py
  - id: openwiki-source-ee1287af284d9ff40046f6bc
    resource: repo://fionaa/app/README.md
  - id: openwiki-source-dd6d4e61f051b0cdaa563f25
    resource: repo://fionaa/app/src/fionaa/graph.py
  - id: openwiki-source-3a7b26ef394512a7b079de22
    resource: repo://fionaa/app/src/fionaa/main.py
  - id: openwiki-source-a24b0d2f77ec96563c6e37e0
    resource: repo://fionaa/README.md
generated: { by: "openwiki/0.5.1", at: "2026-09-24T15:24:05.146Z" }
---

# Quickstart

Use this page as the first stop when you are trying to change FIONAA safely. It does not explain every subsystem; it routes you to the page that owns the implementation detail.

## Start with the shortest useful path

1. **Runtime entrypoint** — see `/openwiki/architecture/runtime-entrypoint.md` to understand how a Bedrock AgentCore invocation assembles settings, identity, storage, checkpointing, gateway tools, model access, and graph execution.
2. **Graph topology** — see `/openwiki/architecture/graph-workflow.md` to understand node order, conditional routing, terminal paths, and where checkpointed state versus published artifacts are written.
3. **Domain and persistence concepts** — see `/openwiki/concepts/domain-model.md` and `/openwiki/concepts/persistence-and-artifacts.md` for the state schema, structured outputs, document loading, evidence files, and published decision artifacts.
4. **Security and storage** — see `/openwiki/concepts/security-and-storage.md` for verified identity, scoped credentials, and the S3 boundary that isolates customer data.
5. **Operations and evals** — see `/openwiki/operations/deployment-and-evals.md` for deployment, runtime setup, and the evaluation entrypoints used in this repo.
6. **Tests** — see `/openwiki/testing/unit-tests.md` and `/openwiki/testing/deepeval-and-path2-evals.md` for the fast regression suites and the runtime/evaluation checks that validate behavior end to end.

## Route by change type

If you know what you are changing, follow this routing map:

| Change area | Start here | Then follow to |
| --- | --- | --- |
| Launching the app or composing runtime dependencies | `/openwiki/architecture/runtime-entrypoint.md` | `/openwiki/architecture/graph-workflow.md` and `/openwiki/concepts/security-and-storage.md` |
| Graph nodes, branching, or state transitions | `/openwiki/architecture/graph-workflow.md` | `/openwiki/concepts/domain-model.md` and `/openwiki/concepts/persistence-and-artifacts.md` |
| Identity, IAM, or storage isolation | `/openwiki/concepts/security-and-storage.md` | `/openwiki/concepts/persistence-and-artifacts.md` |
| Published reports, evidence, or decision artifacts | `/openwiki/concepts/persistence-and-artifacts.md` | `/openwiki/workflows/human-review-publication.md` |
| Loan decision logic and branch outcomes | `/openwiki/workflows/loan-assessment-branching.md` | `/openwiki/concepts/domain-model.md` and `/openwiki/architecture/graph-workflow.md` |
| Human-review publication and validation annotations | `/openwiki/workflows/human-review-publication.md` | `/openwiki/concepts/persistence-and-artifacts.md` and `/openwiki/testing/deepeval-and-path2-evals.md` |
| Deployment, runtime configuration, or eval execution | `/openwiki/operations/deployment-and-evals.md` | `/openwiki/integrations/agentcore-runtime-and-gateway.md` |
| Fast regression checks or fixture behavior | `/openwiki/testing/unit-tests.md` | `/openwiki/testing/deepeval-and-path2-evals.md` |

## What each major entrypoint is for

The repository keeps the bootstrap, the installed package entrypoint, and the deployment configuration separate:

- `app/main.py` is the source-archive bootstrap used by AgentCore packaging.
- `app/src/fionaa/main.py` is the installed package runtime entrypoint.
- `agentcore/agentcore.json` is the deployment configuration that tells AgentCore what to run.

For ordinary changes, read the architecture and concept pages above instead of tracing every file in the source tree. The quickstart is only a navigator.

## Local sanity check

The repository README points to the standard local flow:

```bash
source /fionaa/app/fionaa/.venv/bin/activate
agentcore dev
agentcore invoke --dev "What can you do"
```

Use the operations page for deployment and evaluation workflows, and use the testing pages when you need to confirm that a change preserves behavior.

## Safe-change checklist

Before editing code, pick the owning page:

- **Runtime composition or credentials**: architecture/runtime-entrypoint first.
- **Workflow control flow**: architecture/graph-workflow first.
- **Data model or artifact format**: concepts/domain-model and concepts/persistence-and-artifacts first.
- **Identity or S3 isolation**: concepts/security-and-storage first.
- **Deploy or evaluate**: operations/deployment-and-evals first.
- **Validate behavior**: testing/unit-tests and testing/deepeval-and-path2-evals first.

If you still need more detail after this page, follow the linked page hierarchy rather than reading source files directly.
