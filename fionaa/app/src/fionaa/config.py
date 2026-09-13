"""Runtime configuration, read explicitly rather than during imports."""
from dataclasses import dataclass
import os


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ValueError(f"Missing required configuration: {name}")
    return value


@dataclass(frozen=True)
class RuntimeSettings:
    applications_bucket: str
    policy_docs_bucket: str
    kms_key_arn: str
    data_access_role_arn: str
    checkpoint_memory_id: str

    @classmethod
    def from_environment(cls):
        return cls(*(required_env(name) for name in (
            "FIONAA_APPLICATIONS_BUCKET", "FIONAA_POLICY_DOCS_BUCKET",
            "FIONAA_KMS_KEY_ARN", "FIONAA_DATA_ACCESS_ROLE_ARN",
            "FIONAA_CHECKPOINT_MEMORY_ID",
        )))
