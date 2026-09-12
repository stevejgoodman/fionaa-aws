# Files

- [Domain model and runtime data shapes](domain-model.md) - Canonical application, document, result, and decision shapes in the FIONAA graph, including which fields are validated, checkpointed, and persisted as evidence artifacts.
- [Persistence, artifacts, and checkpointing](persistence-and-artifacts.md) - Explains what FIONAA stores in S3, what is checkpointed in AgentCore Memory, and how each node's evidence artifacts are structured and retrieved.
- [Security, identity, and storage isolation](security-and-storage.md) - Verified request identity is turned into a scoped customer_id, short-lived STS credentials, and per-customer S3/KMS access boundaries. Customer data stays under application-specific prefixes, while shared policy documents live in a separate read-only bucket.
