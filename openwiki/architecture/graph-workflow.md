---
type: architecture concept
title: Graph workflow and stage sequencing
description: LangGraph state, runtime context, routing, persistence artifacts, and validation behavior for the FIONAA loan-assessment workflow.
tags: [langgraph, workflow, state-machine, runtime-context, persistence, branching]
sources:
  - id: openwiki-source-dd6d4e61f051b0cdaa563f25
    resource: repo://fionaa/app/src/fionaa/graph.py
  - id: openwiki-source-7b9d97740400f92bd8681a90
    resource: repo://fionaa/app/src/fionaa/workflow/companies_house.py
  - id: openwiki-source-9ad7e6a82e6fffc4549c7cfd
    resource: repo://fionaa/app/src/fionaa/workflow/decision.py
  - id: openwiki-source-174a46d6d458798714873613
    resource: repo://fionaa/app/src/fionaa/workflow/financial.py
  - id: openwiki-source-096587136b7e36f1db7f0a20
    resource: repo://fionaa/app/src/fionaa/workflow/loading.py
  - id: openwiki-source-8a4054a1668bf6243016606e
    resource: repo://fionaa/app/src/fionaa/workflow/state.py
  - id: openwiki-source-63e845a549db7ee7800e90c7
    resource: repo://fionaa/app/src/fionaa/workflow/web_search.py
  - id: openwiki-source-252cdfea0eb98a9790142319
    resource: repo://fionaa/app/tests/test_graph.py
generated: { by: "openwiki/0.5.1", at: "2026-09-15T10:28:44.889Z" }
---

# Graph workflow and stage sequencing

FIONAA’s request handling is organized as a LangGraph state machine with two distinct data planes:

- **Checkpointed state**: the serializable application and result data that flows from node to node.
- **Runtime context**: per-invocation dependencies such as the application store, policy-document store, model handles, and live tool objects.

That split matters because the graph is checkpointed for recovery and replay, but the runtime context is not part of the checkpoint. Workflow nodes read and write structured state; invocation-scoped collaborators stay in `AgentContext`.

## State and runtime context

`ApplicationState` is the checkpointed `TypedDict` used by the graph. It carries the loaded application, staged document lists, intermediate node outputs, and the final decision fields. The state uses last-write-wins semantics for downstream results, so each node can replace its own slice of workflow output without merging with older values.

`AgentContext` is the run-scoped companion schema registered on the graph. It holds the `ApplicationStore`, `PolicyDocStore`, live tool handles, and optional model/policy-check collaborators. These objects are intentionally kept out of checkpointed state because they are invocation-scoped and not suitable for serialization.

The operational rule is simple:

- **State is durable workflow data.** It is checkpointed and is what later nodes re-read.
- **Runtime context is ephemeral execution scaffolding.** It is available to nodes during one run, but it is never serialized into the checkpoint.

This separation lets the graph resume from state while still using non-serializable resources during a run.

## Topology

The graph is mostly linear. The only dynamic branch is the Companies House node, which returns the next hop at runtime.

```mermaid
flowchart TD
    START --> load_application
    load_application --> companies_house
    companies_house -->|found true| policy_check
    companies_house -->|found false| reject_no_company
    policy_check --> financial_assessment
    financial_assessment --> web_search
    web_search --> synthesize_decision
    synthesize_decision --> validate_final_decision
    validate_final_decision --> END
    reject_no_company --> END
```

This shows the canonical execution path, the no-company branch, and the final validation step that closes the success path.

## Node responsibilities in execution order

### 1. `load_application`

This node reads `input/application.json` from the runtime store and fails immediately if it is missing. It then discovers additional staged documents by prefix under the same `input/` area and validates each JSON object against the expected schema before placing it in state.

Two invariants matter here:

- Documents are loaded by prefix, not by manifest, so multiple annual accounts or bank statements can be staged.
- Invalid document JSON is not ignored; the run fails so later assessments do not silently consume bad evidence.

The node returns the application plus validated document lists, which become the starting point for downstream analysis.

### 2. `companies_house`

This node performs the registered-company verification and is the workflow’s first routing point. It runs an agent constrained to `CompaniesHouseResult`, with Companies House tools and the `geo-target___CheckSameArea` helper available because the prompt expects it for address reconciliation.

After the model responds, the node performs two important controls:

1. It extracts tool calls as evidence and redacts street-level address detail before persistence.
2. It applies a runtime groundedness check to the `found` claim.

That groundedness step is deliberately asymmetric: it can downgrade `found=True` to `found=False` if the tool evidence does not support the claim, but it never upgrades `found=False` to `found=True`. That makes the branch decision safer, because a fabricated company match is what would otherwise let an unverified application proceed.

The node returns a `Command` that updates state and routes to one of two paths:

- `policy_check` when `found` remains true.
- `reject_no_company` when `found` is false.

It also persists `companies_house/result.json`, including the groundedness metadata and redacted tool calls.

### 3. `policy_check`

This node evaluates the application against the loan policy selected from the application’s loan type. It does not search for the policy document; policy selection is already resolved by loan type.

It also scopes deterministic calculation tools from `CHECK_TOOLS_POOL` according to the policy metadata, so the model only sees the tools that policy explicitly authorizes. The node submits the application, policy text, bank-statement end dates, and today’s date to the agent, then stores a structured `PolicyCheckResult`.

The node writes `policy_check/result.json` as evidence, including harvested tool calls. Those tool calls are recorded separately from state so downstream nodes still receive the clean structured result shape.

### 4. `financial_assessment`

This node is only reachable after a positive Companies House branch. It uses the application, Companies House findings, policy check result, annual accounts, and bank statements to produce a structured financial assessment.

Before the agent runs, the workflow computes a deterministic cross-check of application figures against the most recent annual accounts. The agent is instructed to copy that cross-check through verbatim rather than recomputing the delta itself. That keeps the numeric comparison authoritative and avoids having the model re-derive materiality.

The node also passes the financial documents read from state, which means it can reason over multiple annual accounts or multiple bank statements if they were staged. The result is persisted as `financial_assessment/result.json` together with tool-call evidence.

### 5. `web_search`

This node runs after financial assessment on the success path. It uses the company name plus the Companies House findings to search for online corroboration such as a company site or a related profile.

Unlike the Companies House node, the web-search evidence is audit-only. The node records whether it actually used a tool, but that groundedness signal does not affect routing. The node returns the raw search result string in state because the final synthesis node expects that shape.

It persists `web_search/result.json` with the search result, the redacted tool calls, and a boolean indicator showing whether the search was tool-backed.

### 6. `synthesize_decision`

This is the final synthesis node on the success path. It does not re-fetch data or re-run checks; it reads the upstream results already stored in state and asks the model for a final structured decision.

The node exists because the success path otherwise would have ended after collecting evidence, without any consolidated outcome. It writes `decision/proposed.json` containing the proposed verdict plus the upstream policy, Companies House, financial assessment, and web-search findings that informed it.

### 7. `validate_final_decision`

The final validator sits after synthesis. It is the last checkpoint before `END` on the success path and gives the graph a place to enforce the final decision shape before completion.

In control-flow terms, this means the graph does not publish the final decision directly from the synthesis node. The synthesis node produces a proposed decision in state; the validator is the handoff that allows the workflow to finish with a checked outcome.

### 8. `reject_no_company`

This is the terminal branch for applications that do not produce a confirmed Companies House match. It writes a single consolidated `decision/result.json` artifact and returns a rejection decision with reason `companies_house_no_match`.

The artifact includes the earlier policy and Companies House results, so a reviewer can understand why the run ended early without reconstructing the path from separate node outputs.

## Persistence model

Each major node writes a node-specific evidence artifact under a fixed path in the application store:

- `policy_check/result.json`
- `companies_house/result.json`
- `financial_assessment/result.json`
- `web_search/result.json`
- `decision/proposed.json`
- `decision/result.json`
- `decision/validation.json`

These artifacts are separate from checkpointed state. The state is what LangGraph can resume from; the artifacts are the reviewable evidence trail. That separation is also why tool calls are stored in the artifacts rather than in the structured state objects that downstream prompts read.

## Branching and failure behavior

The graph has three meaningful early-failure or early-completion behaviors:

- Missing `input/application.json` fails at `load_application`.
- Invalid staged document JSON fails at `load_application` during schema validation.
- A negative Companies House verification branches directly to `reject_no_company`, which ends the run after writing the rejection artifact.

The Companies House branch is the only conditional route in the graph. Everything else is linear, which keeps the workflow predictable: once the company is confirmed, the graph always proceeds through policy check, financial assessment, web search, synthesis, and final validation before ending.

## Extension points

The graph is designed so new document types can be added to `DOCUMENT_SPECS` without changing `load_application` control flow. A spec can gate on application content via `applies_to`, which allows future loan types or document requirements without adding branching logic to the loader.

The same design applies to tools:

- Policy checks pull only the deterministic tools named by policy metadata.
- Companies House and web search receive only the tool prefixes they are meant to use.

That keeps the graph modular: each node gets the smallest execution surface needed for its own responsibility.

## Tests that matter

The unit tests in `tests/test_graph.py` capture the behavioral contracts that are easiest to break accidentally:

- `checkpoint_config` derives thread and actor scoping from the verified identity.
- `load_application` reads the application, loads documents by prefix, skips inapplicable document specs, and fails on invalid JSON.
- `check_against_policy` scopes tools by loan type, passes bank-statement end dates, and persists tool-call evidence.
- `check_companies_house` preserves the branch contract, persists redacted evidence, and honors groundedness overrides.
- `reject_no_company` consolidates the early termination decision.
- `synthesize_decision` produces the proposed decision that validation then annotates for review.

Those tests matter because they encode the graph’s invariants: routing is based on structured results, evidence is persisted separately from state, and the success path always ends in a validated decision rather than an open-ended evidence collection phase.
