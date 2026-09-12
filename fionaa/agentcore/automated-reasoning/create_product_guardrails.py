"""Create the four additional standalone guardrails from reviewed JSON definitions.

Run explicitly with AWS_PROFILE and AWS_DEFAULT_REGION. Creates resources,
but does not change the application runtime or its IAM role. The journal makes
restarts reuse resources created by a previous attempt.
"""
import hashlib
import json
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent
PRODUCTS = ("unsecured-business-loans", "revolving-credit-facility",
            "invoice-discounting", "invoice-factoring")


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    client = boto3.client("bedrock", config=Config(
        connect_timeout=5, read_timeout=60,
        retries={"mode": "standard", "total_max_attempts": 3}))
    journal_path = ROOT / "product-resources.json"
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
    bindings_path = ROOT / "runtime-bindings.json"
    bindings = json.loads(bindings_path.read_text())
    iam_path = ROOT / "runtime-iam-policy.json"
    iam = json.loads(iam_path.read_text())
    for product in PRODUCTS:
        definition_text = (ROOT / f"{product}-definition.json").read_text()
        definition_hash = hashlib.sha256(definition_text.encode()).hexdigest()
        entry = journal.setdefault(product, {"local_definition_sha256": definition_hash})
        if entry["local_definition_sha256"] != definition_hash:
            raise ValueError(f"{product}: definition changed; explicitly review/version the existing policy")
        name = "Fionaa" + "".join(part.title() for part in product.split("-"))
        if "policy" not in entry:
            entry["policy"] = client.create_automated_reasoning_policy(
                name=name, description=f"Runtime policy consistency for {product}",
                policyDefinition=json.loads(definition_text))
            save(journal_path, journal)
        if "policy_version" not in entry:
            entry["policy_version"] = client.create_automated_reasoning_policy_version(
                policyArn=entry["policy"]["policyArn"],
                lastUpdatedDefinitionHash=entry["policy"]["definitionHash"])
            save(journal_path, journal)
        policy_arn = entry["policy"]["policyArn"] + ":" + entry["policy_version"]["version"]
        if "guardrail" not in entry:
            entry["guardrail"] = client.create_guardrail(
                name=name + "Consistency", description=f"Standalone AR validation for {product}",
                automatedReasoningPolicyConfig={"policies": [policy_arn], "confidenceThreshold": 0.8},
                crossRegionConfig={"guardrailProfileIdentifier": "us.guardrail.v1:0"},
                blockedInputMessaging="Policy validation requires review.",
                blockedOutputsMessaging="Policy validation requires review.")
            save(journal_path, journal)
        if "guardrail_version" not in entry:
            entry["guardrail_version"] = client.create_guardrail_version(
                guardrailIdentifier=entry["guardrail"]["guardrailId"],
                description="Reviewed lending rules; standalone validation")
            save(journal_path, journal)
        bindings[product] = {
            "guardrail_id": entry["guardrail"]["guardrailId"],
            "guardrail_version": entry["guardrail_version"]["version"],
            "policy_sha256": hashlib.sha256((ROOT / f"{product}.txt").read_bytes()).hexdigest(),
        }
        save(bindings_path, bindings)
        for statement in iam["Statement"]:
            arn = entry["guardrail"]["guardrailArn"] if "bedrock:ApplyGuardrail" in statement["Action"] else policy_arn
            if arn not in statement["Resource"]:
                statement["Resource"].append(arn)
        save(iam_path, iam)
        print(product, bindings[product]["guardrail_id"], "ready for validation", flush=True)


if __name__ == "__main__":
    main()
