"""FIONAA — identity resolution and IAM credential scoping.

Fine layer of the security model described in fionaa_scoped_agent.py: every
read/write against customer data goes through short-lived credentials
obtained by assuming FionaaDataAccessRole with a session tag
customer_id=<sha256 of the verified email claim>. That role's policy scopes
S3 to arn:aws:s3:::fionaa-applications/${aws:PrincipalTag/customer_id}/* so a
bug in node code cannot reach another customer's prefix — the call fails with
AccessDenied at the IAM layer.

Identity scheme: customer_id is a hash of the customer's email address (never
the raw email, so the S3 key/session tag carries no PII); application_id is a
randomly generated opaque ID minted when the application is created upstream
of this agent. Neither needs a separate ID-mapping table.

See fionaa_iam_policies.md for the matching trust/permission policies.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import boto3
import jwt
from botocore.credentials import DeferredRefreshableCredentials
from botocore.session import get_session as get_botocore_session

log = logging.getLogger("fionaa")

DATA_ACCESS_ROLE_ARN = os.environ["FIONAA_DATA_ACCESS_ROLE_ARN"]

# STS session tag values allow [\w+=,.@-]. Reject anything else *before* it
# reaches the tag, so a malformed identity can never widen the S3 prefix.
_SAFE_TAG_VALUE = re.compile(r"^[\w+=,.@-]{1,256}$")


@dataclass(frozen=True)
class CustomerIdentity:
    """Identity resolved from the *validated inbound JWT*, never from the
    invoke payload. AgentCore Identity puts the verified claims on the request
    context; trusting a caller-supplied customer_id would defeat the whole
    design.

    customer_id is a sha256 hash of the customer's email (see
    `_hash_customer_id`), so the value that ends up in the S3 key and the STS
    session tag is never the email itself. application_id is a randomly
    generated opaque ID minted upstream when the application is created —
    already free of PII, so it's used as-is.
    """

    customer_id: str
    application_id: str

    def __post_init__(self) -> None:
        if not _SAFE_TAG_VALUE.match(self.customer_id):
            raise ValueError(f"unsafe customer_id for session tag: {self.customer_id!r}")
        if not _SAFE_TAG_VALUE.match(self.application_id):
            raise ValueError(f"unsafe application_id: {self.application_id!r}")


def _hash_customer_id(email: str) -> str:
    """customer_id = sha256(lowercased, trimmed email). Lowercasing/trimming
    first means the same person logging in with differently-cased or
    whitespace-padded email still lands on the same S3 prefix."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def identity_from_request_context(context: Any, application_id: str) -> CustomerIdentity:
    """Extract the customer identifier from AgentCore's verified JWT claims.

    The Runtime's customJWTAuthorizer validates the inbound token before the
    request reaches this code, but it does not parse claims out for you — it
    just forwards the raw Authorization header on `context.request_headers`.
    Signature verification is skipped here deliberately: re-verifying would
    need the IdP's signing key duplicated into this process, and the Runtime
    has already rejected anything with a bad signature, wrong issuer, or wrong
    audience before this handler ever runs.

    customer_id is derived here, not trusted from a claim — hashing happens on
    our side so the algorithm is ours to audit rather than depending on the IdP
    having pre-hashed anything into a custom claim.
    """
    headers = context.request_headers or {}
    auth_header = headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ")
    if not token:
        raise ValueError("no Authorization header on request context")
    claims = jwt.decode(token, options={"verify_signature": False})
    email = claims.get("email") or claims.get("custom:email")
    if not email:
        raise ValueError("verified JWT has no email claim; cannot derive customer_id")
    return CustomerIdentity(customer_id=_hash_customer_id(email), application_id=application_id)


def scoped_boto_session(identity: CustomerIdentity) -> boto3.Session:
    """A boto3 Session whose credentials are tagged with this customer_id.

    Uses DeferredRefreshableCredentials so long-running graphs transparently
    re-assume the role before the 1h session expires — important because
    later nodes in the chain may run well after the first node acquired
    credentials.
    """
    sts = boto3.client("sts")

    def _refresh() -> dict[str, str]:
        resp = sts.assume_role(
            RoleArn=DATA_ACCESS_ROLE_ARN,
            # Session name lands in CloudTrail — make it traceable per application.
            RoleSessionName=f"fionaa-{identity.customer_id}-{identity.application_id}"[:64],
            Tags=[
                {"Key": "customer_id", "Value": identity.customer_id},
                {"Key": "application_id", "Value": identity.application_id},
            ],
            # Transitive so the tag survives any onward role chaining.
            TransitiveTagKeys=["customer_id"],
            DurationSeconds=3600,
        )
        creds = resp["Credentials"]
        log.info(
            "assumed data-access role customer_id=%s application_id=%s expires=%s",
            identity.customer_id, identity.application_id, creds["Expiration"],
        )
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }

    botocore_session = get_botocore_session()
    botocore_session._credentials = DeferredRefreshableCredentials(
        refresh_using=_refresh, method="sts-assume-role"
    )
    return boto3.Session(botocore_session=botocore_session)
