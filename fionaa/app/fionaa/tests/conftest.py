import os
import sys
from pathlib import Path

import pytest

# security.py/storage.py read these at import time. Tests never touch AWS,
# so the values just need to be present, not real.
os.environ.setdefault("FIONAA_APPLICATIONS_BUCKET", "test-applications-bucket")
os.environ.setdefault("FIONAA_POLICY_DOCS_BUCKET", "test-policy-docs-bucket")
os.environ.setdefault("FIONAA_KMS_KEY_ARN", "alias/aws/s3")
os.environ.setdefault("FIONAA_DATA_ACCESS_ROLE_ARN", "arn:aws:iam::000000000000:role/test-role")
os.environ.setdefault("FIONAA_CHECKPOINT_MEMORY_ID", "test-memory-id")
os.environ.setdefault("AGENTCORE_GATEWAY_URL", "https://gateway.example.invalid/mcp")
os.environ.setdefault("AGENTCORE_GATEWAY_TOKEN_ENDPOINT", "https://auth.example.invalid/oauth2/token")
os.environ.setdefault("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke")
os.environ.setdefault("AGENTCORE_GATEWAY_CLIENT_ID", "test-client-id")
os.environ.setdefault("AGENTCORE_GATEWAY_CLIENT_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:test-secret")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# "live" tests — tests/test_live_*.py exercise real nodes against the real
# AgentCore Gateway (real Companies House data, real web search, real Bedrock
# model). They cost time/money and need real credentials, so they're opt-in:
# skipped by default, only collected for real when `--run-live` is passed.
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked 'live' (tests/test_live_*.py) — these hit the "
        "real Companies House API, real web search, and a real Bedrock model "
        "via the AgentCore Gateway. Needs real gateway env config "
        "(fionaa/agentcore/.env.local) and AWS credentials able to read the "
        "gateway's client-secret from Secrets Manager. Skipped by default.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: hits the real Companies House API / web search / Bedrock via "
        "the AgentCore Gateway — skipped unless --run-live is passed",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live (hits real APIs; see tests/test_live_*.py)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
