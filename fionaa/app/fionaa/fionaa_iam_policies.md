# FIONAA IAM policies — per-customer isolation

Several pieces have to line up or the isolation silently fails open (or fails
shut). Sections 1–4 cover S3/DynamoDB, where isolation is IAM-enforced.
Section 5 covers checkpointing, where — deliberately — it isn't.

---

## 1. AgentCore Runtime execution role — permissions

Deliberately has **no** S3 access to the applications bucket. Its only route to
customer data is assuming the data-access role.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeScopedDataRole",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::111122223333:role/FionaaDataAccessRole"
    },
    {
      "Sid": "TagTheSession",
      "Effect": "Allow",
      "Action": "sts:TagSession",
      "Resource": "arn:aws:iam::111122223333:role/FionaaDataAccessRole"
    }
  ]
}
```

`sts:TagSession` is a separate action from `sts:AssumeRole`. Omitting it is the
single most common cause of `AccessDenied` when first wiring this up.

---

## 2. FionaaDataAccessRole — trust policy

This is where the enforcement really lives. Without the `ForAllValues` +
`aws:TagKeys` condition, the runtime could assume the role with **no** tag, and
`${aws:PrincipalTag/customer_id}` would resolve to an empty string — which in
some policy forms widens rather than narrows access.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::111122223333:role/FionaaAgentCoreExecutionRole"
      },
      "Action": ["sts:AssumeRole", "sts:TagSession"],
      "Condition": {
        "StringLike": { "aws:RequestTag/customer_id": "?*" },
        "ForAllValues:StringEquals": {
          "aws:TagKeys": ["customer_id", "application_id"]
        }
      }
    }
  ]
}
```

- `StringLike: "?*"` forces a non-empty `customer_id` tag on every assume.
- `ForAllValues:StringEquals` on `aws:TagKeys` means only those two tag keys may
  be set — the caller cannot smuggle in extra tags.

---

## 3. FionaaDataAccessRole — permissions policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ObjectsUnderOwnPrefixOnly",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::fionaa-applications/${aws:PrincipalTag/customer_id}/*"
    },
    {
      "Sid": "ListOwnPrefixOnly",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::fionaa-applications",
      "Condition": {
        "StringLike": {
          "s3:prefix": "${aws:PrincipalTag/customer_id}/*"
        }
      }
    },
    {
      "Sid": "SharedPolicyDocsReadOnly",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::fionaa-policy-docs/*"
    },
    {
      "Sid": "EncryptDecryptWithTenantKey",
      "Effect": "Allow",
      "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
      "Resource": "arn:aws:kms:eu-west-2:111122223333:key/<key-id>",
      "Condition": {
        "StringEquals": {
          "kms:EncryptionContext:customer_id": "${aws:PrincipalTag/customer_id}"
        }
      }
    }
  ]
}
```

`ListBucket` needs its own statement — it's a **bucket-level** action, so the
prefix has to be constrained via the `s3:prefix` condition key, not the resource
ARN. A policy that only constrains `GetObject`/`PutObject` still lets a session
enumerate every customer's keys.

The KMS encryption-context condition is belt-and-braces: even if an S3 statement
were later loosened by mistake, decryption still fails for another customer's
objects. Note it requires passing matching `EncryptionContext` on write.

---

## 4. Bucket policy — deny anything untagged

Optional but worth having for a regulated store. Denies access to the data-access
role unless the principal tag matches the key prefix, regardless of what the
identity policy says.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyCrossCustomerAccess",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": "arn:aws:s3:::fionaa-applications/*",
      "Condition": {
        "ArnEquals": {
          "aws:PrincipalArn": "arn:aws:iam::111122223333:role/FionaaDataAccessRole"
        },
        "StringNotLike": {
          "s3:prefix": "${aws:PrincipalTag/customer_id}/*"
        }
      }
    }
  ]
}
```

---

## 5. Checkpointing — `AgentCoreMemorySaver` (Bedrock AgentCore Memory)

Superseded a bespoke DynamoDB table + `LeadingKeys`/S3-offload IAM design (see
git history if you need it) — that approach worked but required verifying
`DynamoDBSaver`'s internal `PK` schema and an unconfirmed `LeadingKeys` +
`StringLike` IAM combination just to get isolation. `AgentCoreMemorySaver`
(from `langgraph-checkpoint-aws`) uses a managed AWS service instead: no table
to provision, no key-schema archaeology.

**The trade-off, made deliberately:** unlike Sections 3–4, there is **no IAM
condition here scoping which `actor_id` a session may read/write.**
`AgentCoreMemorySaver` calls `bedrock-agentcore:CreateEvent` /
`bedrock-agentcore:ListEvents` with whatever `actor_id`/`session_id` it's
given — same shared Memory resource for every customer, no per-tenant IAM
boundary the way `${aws:PrincipalTag/customer_id}` gives the S3/DynamoDB
resources. Isolation for checkpoint data rests entirely on the application
always deriving `actor_id` from the verified identity (`checkpoint_config` in
`fionaa_scoped_agent.py`), never from the payload — the same discipline as
`customer_id` elsewhere, but **enforced in code, not by IAM**. If that node
code has a bug, it can call `CreateEvent`/`ListEvents` with any `actor_id` it
likes and the permissions policy below won't stop it.

This is acceptable specifically because checkpoints are operational-recovery
state, not the compliance record — `assessment/audit_trail.json` (S3, Section
3.3) remains the durable, IAM-isolated audit trail. It would **not** be an
acceptable trade-off for the applications bucket or the checkpoint content
itself if it were the only record of a decision.

```json
{
  "Sid": "CheckpointMemoryEvents",
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:GetEvent",
    "bedrock-agentcore:DeleteEvent"
  ],
  "Resource": "arn:aws:bedrock-agentcore:<region>:<account>:memory/<memory-id>"
}
```

No resource-per-customer scoping is possible here (unlike the S3/DynamoDB
statements above) — this grants access to the whole Memory resource, gated
only by `actor_id` being correct in code. Confirm against current AWS docs
whether a condition key scoping by `actor_id` exists before assuming this list
of actions is final; none was found as of this writing.

`AgentCoreMemorySaver` doesn't accept a `boto3.Session` the way `DynamoDBSaver`
did — it builds its own client from `**boto3_kwargs`. `build_checkpointer`
unpacks the scoped session's frozen credentials and passes them through
explicitly; skipping that would silently fall back to the ambient execution
role's (unscoped) credentials for every Memory call.

---

## Verifying it actually holds

Two tests worth having in CI, because a broken isolation control that *looks*
configured is worse than none:

1. **Positive** — assume with `customer_id=cust_A`, read
   `cust_A/app_1/input/application.json` → 200.
2. **Negative** — assume with `customer_id=cust_A`, attempt
   `cust_B/app_9/input/application.json` → must raise `AccessDenied`.
   Assert on the exception, not on the absence of data; a `NoSuchKey` result
   means the boundary is *not* being enforced.

Also worth asserting that `ListBucket` with `Prefix="cust_B/"` denies, since
that's the statement most often left unconstrained.

There is no equivalent negative IAM test for the checkpoint memory (Section
5) — the permissions policy doesn't scope by `actor_id`, so a session with
`customer_id=cust_A` can call `ListEvents` with `actor_id=cust_B` and
succeed at the IAM layer. What should actually be tested is that
`checkpoint_config`/`build_checkpointer` are never called with a caller-
supplied identity — i.e. a code-review/unit-test guarantee that `actor_id`
only ever originates from `identity_from_request_context`, not an IAM test.

---

## Operational notes

- **Session duration vs. graph duration.** Assumed-role credentials default to
  1h and cap at the role's `MaxSessionDuration`. Long-running assessments must
  use refreshable credentials (as in `scoped_boto_session`) or a late node will
  fail with `ExpiredToken`.
- **CloudTrail.** `RoleSessionName` and the session tags both appear in
  CloudTrail's `userIdentity`, giving auditors a direct
  customer_id → S3 operation trail. Keep the session name meaningful.
- **Tag value charset.** STS session tags allow `[\w+=,.@-]` only, max 256
  chars. `customer_id` is a sha256 hex digest of the customer's email and
  `application_id` is a randomly generated opaque ID — both fit the charset
  and carry no PII, but the code still validates before tagging (rejects
  anything unsafe up front) since that's what actually enforces it.
- **Don't trust the payload.** `customer_id` must come from verified JWT claims.
  If it comes from the invoke payload, a caller can simply ask for another
  customer's tag and IAM will faithfully grant it.
- **Checkpointer construction and `actor_id`/`thread_id` follow the same rule.**
  Build `AgentCoreMemorySaver` per invocation from the scoped session
  (`build_checkpointer`), never once at module load — since there's no IAM
  condition backing this one up (Section 5), a module-level checkpointer built
  from ambient credentials would have no isolation at all, not even the
  degraded code-enforced kind. `actor_id`/`thread_id` (`checkpoint_config`) are
  likewise derived from identity, never accepted from the caller — this is the
  *only* thing enforcing checkpoint isolation, which matters more once HITL is
  live, since a paused thread sits in Memory waiting for input.
