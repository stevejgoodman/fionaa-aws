"""FIONAA — the only module that touches S3.

Every key is built from the verified `CustomerIdentity`, never from caller
input. IAM (the FionaaDataAccessRole session-tag condition — see
fionaa_scoped_agent.py) is the real boundary; this module just makes it hard
to write a call that violates it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from security import CustomerIdentity

log = logging.getLogger("fionaa")

APPLICATIONS_BUCKET = os.environ["FIONAA_APPLICATIONS_BUCKET"]
POLICY_DOCS_BUCKET = os.environ["FIONAA_POLICY_DOCS_BUCKET"]


class ApplicationStore:
    """All keys are built from the identity, never from caller input. IAM is the
    real boundary; this class just makes it hard to write a violating call."""

    def __init__(self, identity: CustomerIdentity, session: boto3.Session) -> None:
        self._identity = identity
        self._s3 = session.client("s3")

    @property
    def _prefix(self) -> str:
        return f"{self._identity.customer_id}/{self._identity.application_id}"

    def get_json(self, relative_key: str) -> Optional[dict[str, Any]]:
        key = f"{self._prefix}/{relative_key}"
        try:
            obj = self._s3.get_object(Bucket=APPLICATIONS_BUCKET, Key=key)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("NoSuchKey", "404"):
                return None
            if code == "AccessDenied":
                # Either a genuine isolation violation or a misconfigured tag.
                # Never swallow this — it is an auditable security event.
                log.error("AccessDenied reading %s for customer_id=%s",
                          key, self._identity.customer_id)
            raise
        return json.loads(obj["Body"].read())

    def put_json(self, relative_key: str, payload: dict[str, Any]) -> str:
        key = f"{self._prefix}/{relative_key}"
        self._s3.put_object(
            Bucket=APPLICATIONS_BUCKET,
            Key=key,
            Body=json.dumps(payload, indent=2).encode(),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=os.environ["FIONAA_KMS_KEY_ARN"],
            # Must match the kms:EncryptionContext:customer_id condition on
            # FionaaDataAccessRole's KMS grant (fionaa_iam_policies.md Section 3) —
            # without this, GetObject's server-side decrypt uses S3's default
            # context instead and every read is AccessDenied. The API wants this
            # base64-encoded JSON, not a raw dict.
            SSEKMSEncryptionContext=base64.b64encode(
                json.dumps({"customer_id": self._identity.customer_id}).encode()
            ).decode(),
        )
        return f"s3://{APPLICATIONS_BUCKET}/{key}"

    def get_document_bytes(self, filename: str) -> bytes:
        """Large blobs (application PDFs) live under the same enforced prefix."""
        key = f"{self._prefix}/input/documents/{filename}"
        return self._s3.get_object(Bucket=APPLICATIONS_BUCKET, Key=key)["Body"].read()


class PolicyDocStore:
    """Bank policy documents are shared and read-only — separate bucket, and the
    data-access role only holds s3:GetObject on it with no prefix condition."""

    def __init__(self, session: boto3.Session) -> None:
        self._s3 = session.client("s3")

    def load(self, doc_key: str) -> bytes:
        return self._s3.get_object(Bucket=POLICY_DOCS_BUCKET, Key=doc_key)["Body"].read()
