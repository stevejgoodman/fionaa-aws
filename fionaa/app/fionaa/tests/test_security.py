"""Unit tests for security.py: identity resolution and hashing.

None of these touch AWS — scoped_boto_session (STS assume-role) isn't
exercised here since it has no branching logic to unit-test without a real
STS call; the discipline it enforces (identity always derived from the
verified JWT) is covered by identity_from_request_context below.
"""

import jwt
import pytest

import security as sec

from fakes import FakeRequestContext


# ---------------------------------------------------------------------------
# CustomerIdentity / hashing
# ---------------------------------------------------------------------------

def test_hash_customer_id_normalizes_case_and_whitespace():
    assert sec._hash_customer_id(" Person@Example.com ") == sec._hash_customer_id("person@example.com")


def test_customer_identity_rejects_unsafe_tag_values():
    with pytest.raises(ValueError):
        sec.CustomerIdentity(customer_id="not safe; drop table", application_id="app-123")


# ---------------------------------------------------------------------------
# identity_from_request_context
# ---------------------------------------------------------------------------

def _bearer_token(claims):
    # Signature is never verified by identity_from_request_context, so any
    # signing key/algorithm here is fine.
    return jwt.encode(claims, "unused-signing-key", algorithm="HS256")


def test_identity_from_request_context_derives_customer_id_from_email():
    token = _bearer_token({"email": "Person@Example.com"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})

    identity = sec.identity_from_request_context(context=context, application_id="app-456")

    assert identity.customer_id == sec._hash_customer_id("person@example.com")
    assert identity.application_id == "app-456"


def test_identity_from_request_context_falls_back_to_custom_email_claim():
    token = _bearer_token({"custom:email": "person@example.com"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})

    identity = sec.identity_from_request_context(context=context, application_id="app-456")

    assert identity.customer_id == sec._hash_customer_id("person@example.com")


def test_identity_from_request_context_requires_authorization_header():
    context = FakeRequestContext({})
    with pytest.raises(ValueError, match="no Authorization header"):
        sec.identity_from_request_context(context=context, application_id="app-456")


def test_identity_from_request_context_requires_email_claim():
    token = _bearer_token({"sub": "no-email-here"})
    context = FakeRequestContext({"Authorization": f"Bearer {token}"})
    with pytest.raises(ValueError, match="no email claim"):
        sec.identity_from_request_context(context=context, application_id="app-456")
