"""Unit tests for storage.py: ApplicationStore/PolicyDocStore key scoping.

boto3.Session.client("s3") is monkeypatched to a fake client that just
records calls — these tests are about which key/bucket each method builds,
not about talking to real S3.
"""

import base64
import io
import json

import boto3
import pytest
from botocore.exceptions import ClientError

import storage as st


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes] | None = None) -> None:
        self.objects = objects or {}
        self.put_calls: list[dict] = []

    def get_object(self, Bucket, Key):
        body = self.objects.get((Bucket, Key))
        if body is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(body)}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        client = self

        class FakePaginator:
            def paginate(self, Bucket, Prefix):
                # Single page is enough for these tests -- list_keys only
                # cares about aggregating .get("Contents", []) across pages.
                matching = [
                    {"Key": key} for (bucket, key) in client.objects if bucket == Bucket and key.startswith(Prefix)
                ]
                yield {"Contents": matching}

        return FakePaginator()


@pytest.fixture
def identity():
    import security as sec
    return sec.CustomerIdentity(customer_id="a" * 64, application_id="app-123")


def _session_with(monkeypatch, fake_client):
    session = boto3.Session.__new__(boto3.Session)  # avoid a real botocore session
    monkeypatch.setattr(type(session), "client", lambda self, service: fake_client)
    return session


def test_get_json_scopes_key_to_identity_prefix(monkeypatch, identity):
    body = json.dumps({"ok": True}).encode()
    fake_client = FakeS3Client({(st.APPLICATIONS_BUCKET, f"{identity.customer_id}/{identity.application_id}/input/application.json"): body})
    store = st.ApplicationStore(identity, _session_with(monkeypatch, fake_client))

    assert store.get_json("input/application.json") == {"ok": True}


def test_get_json_returns_none_when_missing(monkeypatch, identity):
    store = st.ApplicationStore(identity, _session_with(monkeypatch, FakeS3Client()))
    assert store.get_json("input/application.json") is None


def test_put_json_writes_kms_encryption_context_matching_customer_id(monkeypatch, identity):
    fake_client = FakeS3Client()
    store = st.ApplicationStore(identity, _session_with(monkeypatch, fake_client))

    uri = store.put_json("policy_check/result.json", {"ok": True})

    call = fake_client.put_calls[0]
    assert call["Key"] == f"{identity.customer_id}/{identity.application_id}/policy_check/result.json"
    context = json.loads(base64.b64decode(call["SSEKMSEncryptionContext"]))
    assert context == {"customer_id": identity.customer_id}
    assert uri == f"s3://{st.APPLICATIONS_BUCKET}/{identity.customer_id}/{identity.application_id}/policy_check/result.json"


def test_list_keys_scopes_prefix_to_identity_and_strips_it(monkeypatch, identity):
    prefix = f"{identity.customer_id}/{identity.application_id}"
    fake_client = FakeS3Client(
        {
            (st.APPLICATIONS_BUCKET, f"{prefix}/input/annual_accounts_2023.json"): b"{}",
            (st.APPLICATIONS_BUCKET, f"{prefix}/input/annual_accounts_2024.json"): b"{}",
            # Same filename prefix, different identity -- must not leak across
            # customers just because "input/annual_accounts" also matches.
            ("other-bucket", f"{prefix}/input/annual_accounts_2025.json"): b"{}",
            (st.APPLICATIONS_BUCKET, f"{prefix}/input/bank_statement_jan.json"): b"{}",
            (st.APPLICATIONS_BUCKET, f"{prefix}/input/application.json"): b"{}",
        }
    )
    store = st.ApplicationStore(identity, _session_with(monkeypatch, fake_client))

    keys = store.list_keys("input/annual_accounts")

    assert sorted(keys) == ["input/annual_accounts_2023.json", "input/annual_accounts_2024.json"]


def test_list_keys_returns_empty_when_nothing_matches(monkeypatch, identity):
    store = st.ApplicationStore(identity, _session_with(monkeypatch, FakeS3Client()))
    assert store.list_keys("input/annual_accounts") == []


def test_policy_doc_store_reads_from_shared_bucket(monkeypatch):
    fake_client = FakeS3Client({(st.POLICY_DOCS_BUCKET, "lending/v7.md"): b"policy text"})
    store = st.PolicyDocStore(_session_with(monkeypatch, fake_client))

    assert store.load("lending/v7.md") == b"policy text"
