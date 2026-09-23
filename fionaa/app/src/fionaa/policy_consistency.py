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

from fionaa.ar_claims import render_fact
from fionaa.ar_findings import diagnose


def policy_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class PolicyConsistencyChecker:
    def __init__(self, bindings: dict, client: Any = None):
        self.bindings = bindings
        self.client = client

    @classmethod
    def from_environment(cls):
        # FIONAA_AR_GUARDRAILS_PARAM (an SSM Parameter Store name) is the
        # deployed path -- AgentCore Runtime rejects updates once total
        # environment-variable payload exceeds 1024 bytes, so this JSON
        # lives in Parameter Store instead. FIONAA_AR_GUARDRAILS (the raw
        # JSON inline) is kept as a fallback for local/test use.
        param_name = os.environ.get("FIONAA_AR_GUARDRAILS_PARAM")
        if param_name:
            raw = boto3.client("ssm").get_parameter(Name=param_name)["Parameter"]["Value"]
        else:
            raw = os.environ.get("FIONAA_AR_GUARDRAILS", "{}")
        try:
            bindings = json.loads(raw)
        except json.JSONDecodeError:
            bindings = {}
        return cls(bindings if isinstance(bindings, dict) else {})

    async def check(self, loan_type: str, policy_text: str, facts: dict, assertion: str) -> dict:
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
            # Facts are the premises, so they go in query blocks; only the
            # assertion under test is guard_content. An earlier comment here
            # claimed query blocks weren't reliably used as premises and sent
            # the facts as guard_content too -- measured against the live
            # guardrails on 2026-09-23, that produced *zero* premises at every
            # size from 1 to 13 facts (see
            # evals/automated_reasoning/premise-handling-probe.json). The
            # translator put the facts in the claims instead, so each check
            # asked whether the facts and the assertion could hold together --
            # satisfiable, hence "inconclusive" -- rather than whether the
            # assertion followed from the facts. The same facts sent as query
            # decide the same claims valid/invalid.
            content = [
                {"text": {"text": render_fact(name, value), "qualifiers": ["query"]}}
                for name, value in sorted(facts.items())
            ]
            content.append({"text": {"text": assertion, "qualifiers": ["guard_content"]}})
            return self.client.apply_guardrail(
                guardrailIdentifier=identifier,
                guardrailVersion=str(version),
                source="OUTPUT",
                outputScope="FULL",
                content=content,
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
        guardrail_ok = response.get("action") == "NONE" and units > 0 and bool(findings)
        # Preserve contradictions as invalid, separately from unavailable checks.
        # passed remains for existing checker clients; the workflow does not gate on it.
        has_invalid = any(set(finding) == {"invalid"} for finding in findings)
        passed = guardrail_ok and not has_invalid
        all_valid = guardrail_ok and all(set(finding) == {"valid"} for finding in findings)
        status = ("invalid" if has_invalid else "valid" if all_valid
                  else "inconclusive" if passed else "not_validated")
        return {**result, "passed": passed,
                "status": status,
                "findings": findings,
                # Why an undecided check was undecided -- policy variable
                # names only, never applicant values. See ar_findings.py.
                "diagnostics": diagnose(facts, findings),
                "usage": response.get("usage", {}),
                "request_id": response.get("ResponseMetadata", {}).get("RequestId")}
