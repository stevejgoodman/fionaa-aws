"""Prevent policy edits from silently leaving the deployed binding stale."""
import json
from pathlib import Path

import pytest

from policy_consistency import policy_digest
from policy_loader import load_policy_text
from schemas import LoanType

ROOT = Path(__file__).resolve().parents[3] / "agentcore" / "automated-reasoning"


@pytest.mark.parametrize("loan_type", list(LoanType))
def test_every_product_has_current_versioned_binding(loan_type):
    bindings = json.loads((ROOT / "runtime-bindings.json").read_text())
    binding = bindings[loan_type.value]
    assert binding["guardrail_version"].isdigit()
    assert int(binding["guardrail_version"]) > 0
    assert binding["policy_sha256"] == policy_digest(load_policy_text(loan_type))
    iam = json.loads((ROOT / "runtime-iam-policy.json").read_text())
    resources = [resource for statement in iam["Statement"]
                 if "bedrock:ApplyGuardrail" in statement["Action"]
                 for resource in statement["Resource"]]
    assert any(arn.endswith("/" + binding["guardrail_id"]) for arn in resources)


def test_iam_allows_required_us_guardrail_profile_destinations():
    iam = json.loads((ROOT / "runtime-iam-policy.json").read_text())
    resources = {resource for statement in iam["Statement"]
                 if statement["Effect"] == "Allow"
                 and "bedrock:ApplyGuardrail" in statement["Action"]
                 for resource in statement["Resource"]}
    for region in ("us-east-1", "us-east-2", "us-west-2"):
        assert (f"arn:aws:bedrock:{region}:492646066653:"
                "guardrail-profile/us.guardrail.v1:0") in resources
