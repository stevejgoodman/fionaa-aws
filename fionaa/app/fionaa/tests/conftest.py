import os
import sys
from pathlib import Path

# fionaa_scoped_agent.py reads these at import time. Tests never touch AWS,
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
