"""Path 2 staging + invoke: pushes eval scenarios through the REAL deployed
fionaa Runtime (not graph.py node functions directly -- that's Path 1's
deepeval_evals/) and records the resulting session IDs for batch-evaluation.

See ../agentcore/EVALS.md's "Path 2 plan" section (work item 4) for the full
design and the constraints this implements:

- fionaa's actual entrypoint takes {"application_id": ...} + a verified JWT,
  not a chat message, so nothing dataset-driven can invoke it directly
  (EVALS.md's "A caveat worth knowing" section). Each scenario's
  `application` dict is staged at input/application.json under a disposable
  (customer_id, application_id) prefix first, then the Runtime is invoked
  normally and loads it itself via graph.py's load_application node.
- customer_id is sha256(email) from a verified JWT (security.py), never
  caller-supplied -- staging and invoking both use the one fixed disposable
  identity from work item 2 (fionaa-eval-ci@example.com).
- FionaaDataAccessRole's trust policy only trusts the Runtime's execution
  role (fionaa_iam_policies.md Section 2) -- this script's IAM role
  (fionaa-evals-path2-ci, work item 3) can't assume it, so it writes
  directly with its own narrower S3/KMS grant, setting the same
  SSEKMSEncryptionContext the app would (storage.py's put_json).
- Invocation is a plain HTTPS POST with Authorization: Bearer <id_token> --
  fionaa's Runtime uses a CUSTOM_JWT authorizer, so there's no SigV4/IAM
  check on this call at all (agentcore_deploy_gotchas #8/#12). The ID token
  comes from cognito-idp:AdminInitiateAuth (ADMIN_USER_PASSWORD_AUTH)
  against the disposable Cognito user provisioned in work item 2 -- the ID
  token specifically, not the access token, since only it carries the
  `email` claim security.py needs.

Only scenarios whose `application` is a genuinely complete, full-graph-ready
application belong here (see EVALS.md's note on why the "fullapp-" prefixed
dataset entries exist separately from Path 1's per-node fixtures -- most of
the original dataset's scenarios were authored for isolated node calls and
don't compose into a valid whole-graph run, e.g. companies-house-* scenarios
have no loan_type and policy-check-* scenarios use fictitious company data
that companies_house will never find).

Usage:
    cd fionaa/agentcore
    AWS_PROFILE=AIOps ../app/fionaa/.venv/bin/python3 eval_path2_stage_and_invoke.py \\
        --output .cli/path2-session-map.json
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import boto3
import httpx

REGION = "us-east-1"
ACCOUNT = "492646066653"

APPLICATIONS_BUCKET = "fionaa-6655-assets"
KMS_KEY_ARN = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/ad0bef90-102a-4cd0-8dcf-9d6744c52743"

# sha256("fionaa-eval-ci@example.com") -- see EVALS.md Path 2 plan, work item 2.
EVAL_CUSTOMER_ID = "17deb75df387eafcea144caa24f896e85216c2622721c6c33c6c1b8cd73eae18"
EVAL_CREDENTIALS_SECRET_ID = "fionaa/eval-harness-cognito-credentials"

COGNITO_USER_POOL_ID = "us-east-1_QdHqgzqUA"
COGNITO_CLIENT_ID = "14dnsnarjq6povfqeisdtvs07a"

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "fionaa_eval_dataset.jsonl"
DEPLOYED_STATE_PATH = Path(__file__).resolve().parent / ".cli" / "deployed-state.json"

# Session header must be >=33 chars (agentcore_deploy_gotchas #12); a
# scenario_id this short would need padding, but every current fullapp-*
# scenario_id already exceeds this comfortably -- padded anyway to be robust
# to future short scenario_ids rather than relying on that holding forever.
_MIN_SESSION_ID_LEN = 33


def load_fullapp_scenarios(prefix: str = "fullapp-") -> list[dict[str, Any]]:
    scenarios = []
    with DATASET_PATH.open() as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["scenario_id"].startswith(prefix):
                scenarios.append(record)
    return scenarios


def runtime_arn() -> str:
    state = json.loads(DEPLOYED_STATE_PATH.read_text())
    return state["targets"]["default"]["resources"]["runtimes"]["fionaa"]["runtimeArn"]


def get_eval_id_token(session: boto3.Session) -> str:
    """AdminInitiateAuth against the disposable eval user -- ID token (has
    the `email` claim security.py needs), not the access token."""
    secrets = session.client("secretsmanager", region_name=REGION)
    creds = json.loads(secrets.get_secret_value(SecretId=EVAL_CREDENTIALS_SECRET_ID)["SecretString"])

    cognito = session.client("cognito-idp", region_name=REGION)
    resp = cognito.admin_initiate_auth(
        UserPoolId=COGNITO_USER_POOL_ID,
        ClientId=COGNITO_CLIENT_ID,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    if resp.get("ChallengeName"):
        raise RuntimeError(f"unexpected auth challenge: {resp['ChallengeName']} -- eval user's password may not be permanent")
    return resp["AuthenticationResult"]["IdToken"]


def stage_application(session: boto3.Session, application: dict[str, Any]) -> str:
    """Writes input/application.json under a fresh application_id in the
    disposable customer_id prefix. Returns the application_id.

    Mirrors storage.py's put_json exactly (same SSEKMSEncryptionContext
    shape) since the app will read this back via its own scoped
    FionaaDataAccessRole session, which requires the encryption context to
    match on decrypt (agentcore_deploy_gotchas #7).
    """
    application_id = str(uuid.uuid4())
    s3 = session.client("s3", region_name=REGION)
    key = f"{EVAL_CUSTOMER_ID}/{application_id}/input/application.json"
    s3.put_object(
        Bucket=APPLICATIONS_BUCKET,
        Key=key,
        Body=json.dumps(application, indent=2).encode(),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=KMS_KEY_ARN,
        SSEKMSEncryptionContext=base64.b64encode(json.dumps({"customer_id": EVAL_CUSTOMER_ID}).encode()).decode(),
    )
    return application_id


def invoke_runtime(id_token: str, application_id: str, session_id: str, timeout_s: float = 300.0) -> dict[str, Any]:
    """POSTs directly to the Runtime's /invocations endpoint -- fionaa's
    payload shape ({"application_id": ...}) isn't something `agentcore
    invoke`/`run eval --dataset` can send (agentcore_deploy_gotchas #8),
    and CUSTOM_JWT auth means Bearer, not SigV4."""
    encoded_arn = urllib.parse.quote(runtime_arn(), safe="")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations"
    resp = httpx.post(
        url,
        params={"qualifier": "DEFAULT"},
        headers={
            "Authorization": f"Bearer {id_token}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
            "Content-Type": "application/json",
        },
        json={"application_id": application_id},
        timeout=timeout_s,
    )
    return {"status_code": resp.status_code, "body": _safe_json(resp)}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def make_session_id(scenario_id: str, run_id: str) -> str:
    session_id = f"eval-{scenario_id}-{run_id}"
    if len(session_id) < _MIN_SESSION_ID_LEN:
        session_id = session_id.ljust(_MIN_SESSION_ID_LEN, "0")
    return session_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", default="fullapp-", help="dataset scenario_id prefix to stage (default: fullapp-)")
    parser.add_argument("--output", required=True, help="path to write the scenario_id -> session_id/application_id JSON mapping")
    args = parser.parse_args()

    scenarios = load_fullapp_scenarios(prefix=args.prefix)
    if not scenarios:
        raise SystemExit(f"no scenarios found with prefix {args.prefix!r} in {DATASET_PATH}")

    session = boto3.Session()
    print(f"Authenticating as the disposable eval identity ({EVAL_CUSTOMER_ID[:12]}...)")
    id_token = get_eval_id_token(session)

    run_id = uuid.uuid4().hex[:8]
    results: dict[str, Any] = {}

    # Sequential, not parallel -- concurrent invocations share the same
    # account-level Bedrock quota (see ../../.github/workflows/deepeval-ci.yml's
    # comment on the same throttling risk for Path 1).
    for scenario in scenarios:
        scenario_id = scenario["scenario_id"]
        application = json.loads(scenario["turns"][0]["input"])
        session_id = make_session_id(scenario_id, run_id)

        print(f"[{scenario_id}] staging application data...")
        application_id = stage_application(session, application)

        print(f"[{scenario_id}] invoking runtime (session_id={session_id})...")
        t0 = time.monotonic()
        outcome = invoke_runtime(id_token, application_id, session_id)
        elapsed = time.monotonic() - t0
        print(f"[{scenario_id}] done in {elapsed:.1f}s -- status {outcome['status_code']}")

        results[scenario_id] = {
            "application_id": application_id,
            "session_id": session_id,
            "status_code": outcome["status_code"],
            "response": outcome["body"],
            "elapsed_s": round(elapsed, 1),
        }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} scenario results to {out_path}")

    failures = {sid: r for sid, r in results.items() if r["status_code"] != 200}
    if failures:
        print(f"\n{len(failures)} scenario(s) did not return 200:")
        for sid, r in failures.items():
            print(f"  {sid}: status={r['status_code']} response={r['response']!r}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
