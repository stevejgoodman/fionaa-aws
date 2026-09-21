# Files

- [Domain model and runtime data shapes](domain-model.md) - Canonical application, document, result, and decision shapes in the FIONAA graph, including which fields are validated, checkpointed, and persisted as evidence artifacts.
- [Persistence and published artifacts](persistence-and-artifacts.md) - Explains how FIONAA separates durable S3 artifacts from AgentCore checkpoint state, and how callers should interpret node results, validation annotations, and review-ready reports.
- [Identity, IAM scoping, and storage safety](security-and-storage.md) - Verified JWT claims are turned into a scoped customer identity, then into short-lived STS credentials and prefix-limited S3/KMS access. Missing data fails closed as an expected absence, while AccessDenied is treated as a security-relevant event.
