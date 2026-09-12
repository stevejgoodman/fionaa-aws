---
type: testing guide
title: Unit tests and boundary fakes
description: Focused unit tests that pin module boundaries for storage, security, graph routing, persistence, and agent-scoped helpers.
tags: [testing, unit-tests, fakes, storage, security, graph]
verified:
  - by: openwiki/0.5.1
    at: 2026-09-12T16:18:14.763Z
sources:
  - id: openwiki-source-6a3998da098be404b24d8add
    resource: repo://fionaa/app/fionaa/tests/fakes.py
  - id: openwiki-source-9968ff105989269e2c54af38
    resource: repo://fionaa/app/fionaa/tests/test_graph.py
  - id: openwiki-source-3fe1d5567f988e8081c05eeb
    resource: repo://fionaa/app/fionaa/tests/test_security.py
  - id: openwiki-source-b21479a03a34050d0893646c
    resource: repo://fionaa/app/fionaa/tests/test_storage.py
generated: { by: "openwiki/0.5.1", at: "2026-09-12T16:18:14.763Z" }
---

# Unit tests and boundary fakes

This page documents the unit-test layer that protects the `fionaa` application from regressions at its module boundaries. The tests are intentionally narrow: they replace AWS, Bedrock, LangGraph runtime objects, and MCP tools with fakes so each module can prove its own contract without depending on live services.

The test suite is organized around three concerns:

- **storage boundaries**: key scoping, bucket selection, encryption context, and artifact persistence
- **security boundaries**: identity derivation from verified request context and tag-safety invariants
- **graph boundaries**: document loading, node routing, tool scoping, grounding, and checkpoint-safe state

## Boundary fakes

`fionaa/app/fionaa/tests/fakes.py` defines the shared stand-ins that make the unit suite possible:

- `FakeStore` mimics the `ApplicationStore` / `PolicyDocStore` surface with an in-memory dictionary, while recording `put_json` calls and returning `fake://...` URIs.
- `FakePolicyDocs` returns policy document bytes by key.
- `FakeRuntime` only carries `.context`, matching what graph nodes read from `langgraph.runtime.Runtime`.
- `FakeTool` only exposes `.name`, which is enough for `graph.tools_for(...)` prefix filtering.
- `make_fake_create_agent(...)` replaces `langchain.agents.create_agent` and records the tools, prompt, and message content passed into each node.

The fake agent also models two important behaviors that the graph tests depend on:

1. It can synthesize structured outputs for different `response_format` schemas without hardcoding field names for one schema.
2. It can emit a harmless `ToolMessage` for `CompaniesHouse___*` tools so groundedness checks can be exercised without a real model or tool execution.

That design lets the graph tests verify real control flow, while keeping the tests deterministic and fast.

## Storage tests

`fionaa/app/fionaa/tests/test_storage.py` proves that the storage layer is opinionated about scope and persistence, not just serialization.

The key contract is that `ApplicationStore` is always identity-scoped. Reads and writes are rooted under the customer/application prefix, and `list_keys(...)` both applies that prefix and strips it back off before returning relative keys. That means the store cannot accidentally leak data across customers or applications, even if different buckets contain similarly named objects.

The storage tests also pin the encryption behavior of writes: `put_json(...)` must send an SSE-KMS encryption context whose `customer_id` matches the authenticated identity. The suite checks the exact key shape, returned S3 URI, and context payload so a refactor cannot silently weaken the security envelope.

`PolicyDocStore` is covered separately to show that policy text is read from the shared policy-documents bucket rather than from application-scoped storage.

## Security tests

`fionaa/app/fionaa/tests/test_security.py` locks down two responsibilities that would be easy to weaken during refactoring:

- customer IDs are normalized before hashing, so case and surrounding whitespace do not change the derived identity
- `CustomerIdentity` rejects unsafe tag values, preventing untrusted strings from being used as tag metadata

The request-context tests also prove that identity comes from the verified JWT in the `Authorization` header, not from caller-supplied arguments. The code accepts either `email` or `custom:email`, and fails closed when the header or email claim is missing.

## Graph tests

`fionaa/app/fionaa/tests/test_graph.py` is the main boundary test file for workflow behavior. It documents how the graph loads inputs, routes branches, scopes tools, and persists evidence.

### Context and checkpointing

The graph now keeps `store`, `policy_docs`, and `tools` in runtime context rather than in checkpointed state. The checkpoint regression test exists because those live objects are not msgpack-serializable; if they re-enter the state, checkpointing fails. The test therefore verifies that a real `MemorySaver` checkpoint can be written and restored while the state itself stays limited to plain values.

`checkpoint_config(identity)` is also pinned: the thread ID comes from `application_id`, and the actor ID comes from the hashed `customer_id`. That keeps per-application graph runs isolated while preserving actor identity in the checkpoint config.

### Application loading and document scoping

`load_application(...)` reads the staged application JSON and expands document groups by key prefix, not by a separate manifest. The tests show three important behaviors:

- document collections such as annual accounts and bank statements are discovered by naming convention under `input/`
- documents are sorted by key before being returned, so downstream processing is deterministic
- malformed documents fail loudly instead of being dropped, because bad input would otherwise distort later financial checks

A conditional `DocumentSpec` is also exercised to prove that future document types can gate on application data through `applies_to(...)`. When a spec does not apply, `load_application(...)` must skip it entirely rather than returning an empty placeholder.

### Policy check routing

`check_against_policy(...)` is pinned to the contract that policy text and calculation tools are assembled from policy metadata, not hardcoded inside the node.

The tests verify that:

- the node loads the correct policy text for the loan type
- the agent receives only the check tools declared for that policy branch
- tool order follows the pool definition, not the order in which tool names are parsed
- bank-statement end dates are passed into the prompt because recency checks belong to the policy stage
- tool-call evidence is harvested from the agent response and persisted to `policy_check/result.json`

That separation matters: the tools themselves stay pure functions, while the node converts their observed calls into evidence artifacts.

### Companies House branch

`check_companies_house(...)` proves the graph’s branch-routing and grounding rules.

The node scopes tools by prefix, so the Companies House branch can also receive a geo-targeting helper when the prompt needs it. If the structured response says `found=True`, the runtime grounding layer must verify that the response is supported by at least one matching `CompaniesHouse___*` tool call. The tests intentionally cover both the happy path and the failure modes:

- a valid company match persists a grounded evidence artifact and routes to `financial_assessment`
- tool-call evidence is stored only in the S3 artifact, while the in-memory `companies_house` state remains the plain structured result consumed by later nodes
- redaction is applied to persisted tool-call payloads so street-level addresses do not leak into evidence
- unsupported `found=True` results are overridden to `found=False` and routed to `reject_no_company`
- a tool call for the wrong company number is also treated as ungrounded
- `found=False` routes directly to the rejection branch

This is the clearest example of the page’s core theme: the tests define not just outputs, but the conditions under which a node is trusted.

### Financial assessment routing

`check_financial_assessment(...)` is tested as the node that combines application data, Companies House findings, policy results, annual accounts, and bank statements into a single assessment.

The suite confirms that:

- only the deterministic repayment tool is scoped into the node
- the prompt includes annual accounts and bank statements when they are present
- cross-check output is included in the message, including material mismatch flags
- tool-call evidence is extracted from the agent response and written to `financial_assessment/result.json`

This keeps affordability and consistency checks in the financial stage, not the policy stage.

### Web search branch

`search_web(...)` is pinned to a narrower contract: it builds a search prompt from the company name and Companies House findings, and it scopes only the web-search tool prefix.

The tests also show that the node’s persisted artifact is a different shape from its in-memory state:

- state keeps the bare search result string for downstream decision synthesis
- the S3 artifact stores the result together with tool-call evidence and grounding metadata

That split protects workflow consumers from evidence bookkeeping while still preserving auditability.

### Decision synthesis and rejection branch

`synthesize_decision(...)` is the final aggregator. The tests show that it consumes the prior stage outputs as context, persists the final decision artifact, and can emit either approval or rejection based on schema-valid agent output.

The explicit rejection branch, `reject_no_company(...)`, persists a final rejection reason when Companies House cannot confirm the company. That branch ensures the graph can terminate early when company identity fails before later checks run.

### Full graph wiring

The end-to-end graph test proves the branch composition:

- the workflow runs through the expected nodes in order
- context objects are available to nodes without being checkpointed
- the graph writes all expected evidence artifacts
- structured-response helpers can satisfy multiple schemas in one run

Together, those tests make the workflow safe to evolve: they define what must stay stable at the integration seams while leaving implementation details free to change.
