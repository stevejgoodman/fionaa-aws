---
type: concept
title: Security, identity, and storage isolation
description: Verified request identity is turned into a scoped customer_id, short-lived STS credentials, and per-customer S3/KMS access boundaries. Customer data stays under application-specific prefixes, while shared policy documents live in a separate read-only bucket.
tags: [security, identity, storage, s3, kms, sts, isolation]
verified:
  - by: openwiki/0.5.1
    at: 2026-09-12T16:18:14.763Z
sources:
  - id: openwiki-source-00148c711013e0dd91ed41fe
    resource: repo://fionaa/app/fionaa/security.py
  - id: openwiki-source-cf842277611ee35d9ba6ad50
    resource: repo://fionaa/app/fionaa/storage.py
  - id: openwiki-source-3fe1d5567f988e8081c05eeb
    resource: repo://fionaa/app/fionaa/tests/test_security.py
  - id: openwiki-source-b21479a03a34050d0893646c
    resource: repo://fionaa/app/fionaa/tests/test_storage.py
generated: { by: "openwiki/0.5.1", at: "2026-09-12T16:18:14.763Z" }
---

# Security, identity, and storage isolation

This page describes the trust boundary that keeps customer data isolated in FIONAA. The design starts with a verified inbound identity, derives a deterministic `customer_id`, assumes short-lived AWS credentials with that tag, and then constrains all application data access to the matching S3 prefix. The storage layer is intentionally narrow: it builds keys from the verified identity, applies KMS encryption context that matches the same identity, and uses a separate bucket for shared policy documents.

## Verified identity is the source of truth

`identity_from_request_context` reads the inbound `Authorization` bearer token from the AgentCore request context and decodes the JWT without re-verifying the signature in-process. The code relies on the runtime having already validated the token before the request reaches the handler, and it rejects malformed inputs instead of guessing:

- missing `Authorization` header raises `ValueError`
- missing email claim raises `ValueError`
- identity is derived from `email` or `custom:email`

The important invariant is that the caller does not supply `customer_id`. The agent derives it from verified claims, so identity binding comes from the request context rather than from invoke payload data.

`customer_id` is computed as `sha256(lowercased, trimmed email)`. That normalization makes the same user map to the same storage prefix even if the email casing or surrounding whitespace varies, and it keeps the raw email out of the session tag and S3 key material.

`CustomerIdentity` also validates both `customer_id` and `application_id` against a safe STS tag character set before they are used downstream. This is deliberate input hardening: malformed identity fields fail fast instead of being propagated into role session tags or object keys.

## STS scoping is the coarse security boundary

The runtime obtains short-lived credentials by calling STS `AssumeRole` against `FionaaDataAccessRole`. The credentials are created with:

- `RoleSessionName` that embeds the customer and application identifiers for traceability
- session tags for `customer_id` and `application_id`
- `TransitiveTagKeys=["customer_id"]` so the customer tag survives onward role chaining
- `DurationSeconds=3600`

This is the coarse boundary for customer-data access. The execution role does not need direct S3 read/write permissions on application data; it only needs permission to assume the data-access role. If the session tags or identity inputs are wrong, the downstream role assumption and object access should fail rather than silently widening access.

The credentials are wrapped in `DeferredRefreshableCredentials`, so long-running graphs can transparently re-assume the role when the session expires.

## S3 access is prefix-scoped to the verified identity

`ApplicationStore` is the only module that reads or writes customer application objects. It builds every key from the stored `CustomerIdentity` and never from caller input. Its prefix shape is:

`<customer_id>/<application_id>`

All JSON documents, generated results, and application PDFs are addressed beneath that prefix. The store returns relative keys for listing, so callers stay inside the same identity boundary instead of manipulating raw bucket paths.

Read behavior is strict about errors:

- missing objects map to `None`
- `AccessDenied` is not swallowed; it is logged as a security-relevant event and re-raised
- other S3 errors are re-raised

That treatment matters because `AccessDenied` can indicate either a misconfigured tag/session or a real isolation violation. In both cases, the system should surface the failure rather than hide it.

Write behavior also enforces the same boundary. `put_json` writes into the application bucket with:

- `ServerSideEncryption="aws:kms"`
- `SSEKMSKeyId` from `FIONAA_KMS_KEY_ARN`
- `SSEKMSEncryptionContext` containing `{"customer_id": <derived customer id>}` encoded as base64 JSON

The encryption context must match the KMS grant condition tied to the data-access role. That means the object is not just stored under the right prefix; decryption also depends on the same customer identity. The implementation treats this as part of the security model, not as a convenience setting.

## Shared policy documents are separate from customer data

`PolicyDocStore` is intentionally distinct from `ApplicationStore`. It reads from `FIONAA_POLICY_DOCS_BUCKET`, which contains shared, read-only policy documents rather than per-customer records. The application data bucket remains prefix-isolated per customer/application, while the policy bucket has no customer prefix condition because the contents are meant to be shared.

This separation is important operationally and conceptually:

- customer application artifacts are private and scoped by identity
- policy documents are shared reference material
- the two buckets have different access expectations and different enforcement models

## Failure handling and invariants

The page’s main security invariants are:

1. identity is derived from verified JWT claims, not from user-supplied request data
2. `customer_id` is normalized, hashed, and safe for STS tag use
3. STS credentials carry the customer tag and are short-lived
4. application objects stay under `<customer_id>/<application_id>/...`
5. KMS encryption context must include the same `customer_id`
6. `AccessDenied` is surfaced, not hidden
7. shared policy docs are read from a separate bucket

When any of those assumptions fail, the code should fail closed. Silent fallback would break the isolation model.

## Focused tests

The tests exercise the key security invariants rather than AWS integration itself:

- hashing normalizes email case and whitespace
- unsafe identity/tag values are rejected
- identity is derived from the verified JWT claims
- missing authorization and missing email claim raise `ValueError`
- application S3 keys are scoped to the identity prefix
- `put_json` writes the expected KMS encryption context
- `list_keys` stays within the application prefix
- `PolicyDocStore` reads from the shared policy bucket

Together, these tests confirm that the implementation preserves the trust boundary from request identity through STS scoping and into S3/KMS enforcement.
