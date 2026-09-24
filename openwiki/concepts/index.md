# Files

- [Domain model and runtime data shapes](domain-model.md) - Canonical application, document, result, and decision shapes in the FIONAA graph, including which fields are validated, checkpointed, and persisted as evidence artifacts.
- [Persistence and published artifacts](persistence-and-artifacts.md) - Explains how FIONAA separates durable S3 artifacts from AgentCore checkpoint state, and how callers should interpret node results, validation annotations, and review-ready reports.
- [Security and Storage Boundary](security-and-storage.md) - Runtime configuration supplies the bucket, KMS key, and role ARNs that shape customer identity, scoped STS credentials, and prefix-limited S3 access. The code fails closed when required settings are missing or malformed.
