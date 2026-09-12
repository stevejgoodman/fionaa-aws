"""Standalone Bedrock Automated Reasoning validation; never silently bypassed.

Bindings are reviewed deployment configuration, not applicant input. Each
loan type binds a numbered guardrail version to the bundled policy digest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def policy_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PolicyConsistencyChecker:
    def __init__(self, bindings: dict, client: Any = None):
        self.bindings = bindings
        self.client = client

    @classmethod
    def from_environment(cls):
        try:
            bindings = json.loads(os.environ.get("FIONAA_AR_GUARDRAILS", "{}"))
        except json.JSONDecodeError:
            bindings = {}
        return cls(bindings if isinstance(bindings, dict) else {})

    async def check(self, loan_type: str, policy_text: str, facts: dict, claim: dict) -> dict:
        digest = policy_digest(policy_text)
        binding = self.bindings.get(loan_type, {})
        result = {"passed": False, "policy_sha256": digest, "findings": []}
        if not isinstance(binding, dict):
            return {**result, "status": "not_configured"}
        identifier = binding.get("guardrail_id")
        version = binding.get("guardrail_version", "")
        if not identifier or not re.fullmatch(r"[1-9][0-9]{0,7}", str(version)):
            return {**result, "status": "not_configured"}
        result.update(guardrail_id=identifier, guardrail_version=str(version))
        if binding.get("policy_sha256") != digest:
            return {**result, "status": "policy_version_mismatch"}

        def apply():
            if self.client is None:
                self.client = boto3.client(
                    "bedrock-runtime",
                    config=Config(connect_timeout=5, read_timeout=60,
                                  retries={"mode": "standard", "total_max_attempts": 2}),
                )
            return self.client.apply_guardrail(
                guardrailIdentifier=identifier,
                guardrailVersion=str(version),
                source="OUTPUT",
                outputScope="FULL",
                content=[
                    {"text": {"text": "Application evidence; absent values are unknown:\n"
                              + json.dumps(facts, allow_nan=False, default=str),
                              "qualifiers": ["query"]}},
                    {"text": {"text": "Assessment to validate:\n"
                              + json.dumps(claim, allow_nan=False, default=str),
                              "qualifiers": ["guard_content"]}},
                ],
            )

        try:
            response = await asyncio.to_thread(apply)
        except (BotoCoreError, ClientError, ValueError) as exc:
            # Never log exception messages: service errors may echo input.
            return {**result, "status": "validation_error", "error_type": type(exc).__name__}
        findings = [
            finding
            for assessment in response.get("assessments", [])
            for finding in assessment.get("automatedReasoningPolicy", {}).get("findings", [])
        ]
        units = response.get("usage", {}).get("automatedReasoningPolicyUnits", 0)
        passed = (
            response.get("action") == "NONE"
            and units > 0
            and bool(findings)
            and all(set(finding) == {"valid"} for finding in findings)
        )
        return {**result, "passed": passed,
                "status": "valid" if passed else "not_validated",
                "findings": findings, "usage": response.get("usage", {}),
                "request_id": response.get("ResponseMetadata", {}).get("RequestId")}
