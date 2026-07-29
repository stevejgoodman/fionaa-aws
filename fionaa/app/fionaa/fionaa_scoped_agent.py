"""
FIONAA — LangGraph agent with IAM-enforced per-customer S3 isolation.

Security model
--------------
Coarse layer : the AgentCore Runtime execution role can do exactly one thing
               against customer data — sts:AssumeRole onto FionaaDataAccessRole.
               It has NO direct s3:GetObject/PutObject on the data bucket.

Fine layer   : every read/write goes through short-lived credentials obtained by
               assuming FionaaDataAccessRole with a session tag
               customer_id=<sha256 of the verified email claim>. That role's
               policy scopes S3 to
               arn:aws:s3:::fionaa-applications/${aws:PrincipalTag/customer_id}/*
               so a bug in node code cannot reach another customer's prefix —
               the call fails with AccessDenied at the IAM layer.

Identity scheme: customer_id is a hash of the customer's email address (never
the raw email, so the S3 key/session tag carries no PII); application_id is a
randomly generated opaque ID minted when the application is created upstream
of this agent. Neither needs a separate ID-mapping table.

See fionaa_iam_policies.md for the matching trust/permission policies.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Annotated, Any, Optional, TypedDict

import boto3
import jwt
from botocore.credentials import DeferredRefreshableCredentials
from botocore.exceptions import ClientError
from botocore.session import get_session as get_botocore_session
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph_checkpoint_aws import AgentCoreMemorySaver
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.messages import HumanMessage, AIMessage, ToolCall
from langchain_mcp_adapters.client import MultiServerMCPClient
from model.load import load_model

log = logging.getLogger("fionaa")

DATA_ACCESS_ROLE_ARN = os.environ["FIONAA_DATA_ACCESS_ROLE_ARN"]
APPLICATIONS_BUCKET = os.environ["FIONAA_APPLICATIONS_BUCKET"]
POLICY_DOCS_BUCKET = os.environ["FIONAA_POLICY_DOCS_BUCKET"]
CHECKPOINT_MEMORY_ID = os.environ["FIONAA_CHECKPOINT_MEMORY_ID"]

# AgentCore Gateway — MCP target exposing Companies House, web search, the
# loan-policy knowledge base, etc. (see GatewayClaimsGatewayUrlOutput in the
# stack outputs). Cognito client-credentials flow, same values
# stevetesting.ipynb uses to mint a bearer token by hand.
GATEWAY_URL = os.environ["AGENTCORE_GATEWAY_URL"]
GATEWAY_TOKEN_ENDPOINT = os.environ["AGENTCORE_GATEWAY_TOKEN_ENDPOINT"]
GATEWAY_OAUTH_SCOPES = os.environ["AGENTCORE_GATEWAY_OAUTH_SCOPES"]
GATEWAY_CLIENT_ID = os.environ["AGENTCORE_GATEWAY_CLIENT_ID"]
# The client secret itself is never an env var — only its Secrets Manager ARN
# is. Fetched at call time in _gateway_token() via the runtime's own
# execution-role credentials (this isn't customer data, so it doesn't go
# through scoped_boto_session).
GATEWAY_CLIENT_SECRET_ARN = os.environ["AGENTCORE_GATEWAY_CLIENT_SECRET_ARN"]

# STS session tag values allow [\w+=,.@-]. Reject anything else *before* it
# reaches the tag, so a malformed identity can never widen the S3 prefix.
_SAFE_TAG_VALUE = re.compile(r"^[\w+=,.@-]{1,256}$")

model = load_model()


def _gateway_client_secret() -> str:
    """Resolves the Gateway OAuth client secret from Secrets Manager.

    Never put this in a plain env var — AgentCore Runtime env vars are
    visible via get-agent-runtime, and this is a real credential, not
    config (see CLAUDE.md's secrets rule).
    """
    client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=GATEWAY_CLIENT_SECRET_ARN)["SecretString"]


def _gateway_token() -> str:
    """Client-credentials OAuth token for the AgentCore Gateway.

    Minted fresh per graph build (see `load_gateway_tools`, called from
    main.py once per invocation) rather than cached at module load — the
    Cognito access token is short-lived, same reasoning as why the
    checkpointer isn't a module-level singleton (see `build_checkpointer`).
    """
    creds = base64.b64encode(f"{GATEWAY_CLIENT_ID}:{_gateway_client_secret()}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": GATEWAY_OAUTH_SCOPES.replace(",", " "),
    }).encode()
    req = urllib.request.Request(
        GATEWAY_TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310 - fixed HTTPS endpoint from our own env config
        return json.loads(resp.read())["access_token"]


async def load_gateway_tools() -> list[Any]:
    """MCP tools exposed by the AgentCore Gateway, ready to hand to
    `create_agent(tools=...)`. Call once per invocation (see main.py) and
    thread the result through `ApplicationState["tools"]` — same pattern as
    `store`/`policy_docs`, so nodes stay testable with a plain list of fakes
    instead of reaching for a module global.
    """
    token = await asyncio.to_thread(_gateway_token)
    client = MultiServerMCPClient({
        "fionaa_gateway": {
            "url": GATEWAY_URL,
            "transport": "streamable_http",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    })
    return await client.get_tools()

# ---------------------------------------------------------------------------
# 1. Identity -> scoped credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CustomerIdentity:
    """Identity resolved from the *validated inbound JWT*, never from the
    invoke payload. AgentCore Identity puts the verified claims on the request
    context; trusting a caller-supplied customer_id would defeat the whole
    design.

    customer_id is a sha256 hash of the customer's email (see
    `_hash_customer_id`), so the value that ends up in the S3 key and the STS
    session tag is never the email itself. application_id is a randomly
    generated opaque ID minted upstream when the application is created —
    already free of PII, so it's used as-is.
    """

    customer_id: str
    application_id: str

    def __post_init__(self) -> None:
        if not _SAFE_TAG_VALUE.match(self.customer_id):
            raise ValueError(f"unsafe customer_id for session tag: {self.customer_id!r}")
        if not _SAFE_TAG_VALUE.match(self.application_id):
            raise ValueError(f"unsafe application_id: {self.application_id!r}")


def _hash_customer_id(email: str) -> str:
    """customer_id = sha256(lowercased, trimmed email). Lowercasing/trimming
    first means the same person logging in with differently-cased or
    whitespace-padded email still lands on the same S3 prefix."""
    normalized = email.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def identity_from_request_context(context: Any, application_id: str) -> CustomerIdentity:
    """Extract the customer identifier from AgentCore's verified JWT claims.

    The Runtime's customJWTAuthorizer validates the inbound token before the
    request reaches this code, but it does not parse claims out for you — it
    just forwards the raw Authorization header on `context.request_headers`.
    Signature verification is skipped here deliberately: re-verifying would
    need the IdP's signing key duplicated into this process, and the Runtime
    has already rejected anything with a bad signature, wrong issuer, or wrong
    audience before this handler ever runs.

    customer_id is derived here, not trusted from a claim — hashing happens on
    our side so the algorithm is ours to audit rather than depending on the IdP
    having pre-hashed anything into a custom claim.
    """
    headers = context.request_headers or {}
    auth_header = headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ")
    if not token:
        raise ValueError("no Authorization header on request context")
    claims = jwt.decode(token, options={"verify_signature": False})
    email = claims.get("email") or claims.get("custom:email")
    if not email:
        raise ValueError("verified JWT has no email claim; cannot derive customer_id")
    return CustomerIdentity(customer_id=_hash_customer_id(email), application_id=application_id)


def scoped_boto_session(identity: CustomerIdentity) -> boto3.Session:
    """A boto3 Session whose credentials are tagged with this customer_id.

    Uses DeferredRefreshableCredentials so long-running graphs transparently
    re-assume the role before the 1h session expires — important because the
    reassess node may run well after the first node acquired credentials.
    """
    sts = boto3.client("sts")

    def _refresh() -> dict[str, str]:
        resp = sts.assume_role(
            RoleArn=DATA_ACCESS_ROLE_ARN,
            # Session name lands in CloudTrail — make it traceable per application.
            RoleSessionName=f"fionaa-{identity.customer_id}-{identity.application_id}"[:64],
            Tags=[
                {"Key": "customer_id", "Value": identity.customer_id},
                {"Key": "application_id", "Value": identity.application_id},
            ],
            # Transitive so the tag survives any onward role chaining.
            TransitiveTagKeys=["customer_id"],
            DurationSeconds=3600,
        )
        creds = resp["Credentials"]
        log.info(
            "assumed data-access role customer_id=%s application_id=%s expires=%s",
            identity.customer_id, identity.application_id, creds["Expiration"],
        )
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }

    botocore_session = get_botocore_session()
    botocore_session._credentials = DeferredRefreshableCredentials(
        refresh_using=_refresh, method="sts-assume-role"
    )
    return boto3.Session(botocore_session=botocore_session)


def build_checkpointer(session: boto3.Session) -> BaseCheckpointSaver:
    """Built fresh per invocation from the customer-scoped session — never at
    module load from the Runtime's broad execution-role credentials.

    AgentCoreMemorySaver is a managed AWS service (Bedrock AgentCore Memory),
    not a table we own — much less to stand up than the DynamoDB + optional
    S3-offload approach. Trade-off, and it's a real one: unlike the S3/DynamoDB
    isolation in Section 3.4, there's no IAM condition here scoping *which*
    actor_id a session may write/read — AgentCoreMemorySaver just calls
    CreateEvent/ListEvents with whatever actor_id it's given. Isolation for
    checkpoint data rests on this code always deriving actor_id from the
    verified identity (never the payload), the same discipline as
    customer_id elsewhere, but application-enforced rather than IAM-enforced.
    Acceptable here because checkpoints are operational-recovery state, not
    the compliance record — that's `assessment/audit_trail.json` in S3.

    AgentCoreMemorySaver builds its own boto3 client internally (it takes
    **boto3_kwargs, not a Session), so the customer-scoped credentials have to
    be unpacked from `session` and passed through explicitly — otherwise it
    would silently fall back to the ambient execution role.
    """
    creds = session.get_credentials().get_frozen_credentials()
    return AgentCoreMemorySaver(
        memory_id=CHECKPOINT_MEMORY_ID,
        aws_access_key_id=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        aws_session_token=creds.token,
        region_name=session.region_name,
    )


def checkpoint_config(identity: CustomerIdentity) -> dict[str, Any]:
    """actor_id/thread_id for AgentCoreMemorySaver, derived from the verified
    identity — never accepted from the caller (same rule as customer_id
    itself). actor_id=customer_id is what actually scopes checkpoint data to
    this customer; thread_id=application_id doesn't need to embed customer_id
    itself anymore, since actor_id already carries that."""
    return {"configurable": {"thread_id": identity.application_id, "actor_id": identity.customer_id}}


# ---------------------------------------------------------------------------
# 2. Storage layer — the only place that touches S3
# ---------------------------------------------------------------------------

class ApplicationStore:
    """All keys are built from the identity, never from caller input. IAM is the
    real boundary; this class just makes it hard to write a violating call."""

    def __init__(self, identity: CustomerIdentity, session: boto3.Session) -> None:
        self._identity = identity
        self._s3 = session.client("s3")

    @property
    def _prefix(self) -> str:
        return f"{self._identity.customer_id}/{self._identity.application_id}"

    def get_json(self, relative_key: str) -> Optional[dict[str, Any]]:
        key = f"{self._prefix}/{relative_key}"
        try:
            obj = self._s3.get_object(Bucket=APPLICATIONS_BUCKET, Key=key)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("NoSuchKey", "404"):
                return None
            if code == "AccessDenied":
                # Either a genuine isolation violation or a misconfigured tag.
                # Never swallow this — it is an auditable security event.
                log.error("AccessDenied reading %s for customer_id=%s",
                          key, self._identity.customer_id)
            raise
        return json.loads(obj["Body"].read())

    def put_json(self, relative_key: str, payload: dict[str, Any]) -> str:
        key = f"{self._prefix}/{relative_key}"
        self._s3.put_object(
            Bucket=APPLICATIONS_BUCKET,
            Key=key,
            Body=json.dumps(payload, indent=2).encode(),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=os.environ["FIONAA_KMS_KEY_ARN"],
            # Must match the kms:EncryptionContext:customer_id condition on
            # FionaaDataAccessRole's KMS grant (fionaa_iam_policies.md Section 3) —
            # without this, GetObject's server-side decrypt uses S3's default
            # context instead and every read is AccessDenied. The API wants this
            # base64-encoded JSON, not a raw dict.
            SSEKMSEncryptionContext=base64.b64encode(
                json.dumps({"customer_id": self._identity.customer_id}).encode()
            ).decode(),
        )
        return f"s3://{APPLICATIONS_BUCKET}/{key}"

    def get_document_bytes(self, filename: str) -> bytes:
        """Large blobs (application PDFs) live under the same enforced prefix."""
        key = f"{self._prefix}/input/documents/{filename}"
        return self._s3.get_object(Bucket=APPLICATIONS_BUCKET, Key=key)["Body"].read()


class PolicyDocStore:
    """Bank policy documents are shared and read-only — separate bucket, and the
    data-access role only holds s3:GetObject on it with no prefix condition."""

    def __init__(self, session: boto3.Session) -> None:
        self._s3 = session.client("s3")

    def load(self, doc_key: str) -> bytes:
        return self._s3.get_object(Bucket=POLICY_DOCS_BUCKET, Key=doc_key)["Body"].read()


# ---------------------------------------------------------------------------
# 3. Graph state
# ---------------------------------------------------------------------------

def _last_write_wins(_old: Any, new: Any) -> Any:
    return new


class ApplicationState(TypedDict, total=False):
    identity: CustomerIdentity
    store: ApplicationStore
    policy_docs: PolicyDocStore
    tools: list[Any]

    application: dict[str, Any]
    policy_check: Annotated[dict[str, Any], _last_write_wins]
    companies_house: Annotated[dict[str, Any], _last_write_wins]
    web_search: Annotated[dict[str, Any], _last_write_wins]
    assessment: Annotated[dict[str, Any], _last_write_wins]


# ---------------------------------------------------------------------------
# 4. Nodes — each writes its own artifact under a fixed subpath
# ---------------------------------------------------------------------------

def load_application(state: ApplicationState) -> dict[str, Any]:
    store = state["store"]
    application = store.get_json("input/application.json")
    if application is None:
        raise FileNotFoundError("application.json not found for this application_id")
    return {"application": application}


async def check_against_policy(state: ApplicationState) -> dict[str, Any]:
    application = state["application"]

    PROMPT = """You are a loan assessor.
        Your job is to compare the loan application details to the requirements in the loan policy.
        First you must find the correct loan policy document in the knowledge base.
        You have access to the following tools: kb-target-loan-policies """

    agent = create_agent(
        model=model,
        tools=state["tools"],
        system_prompt=PROMPT,
    )

    # MCP-backed tools only implement async invocation (no sync `func`, only
    # a `coroutine`) — agent.invoke() raises NotImplementedError as soon as
    # the model calls one, so this has to be ainvoke().
    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=json.dumps(application))]}
    )
    loan_result = response["messages"][-1].content
    state["store"].put_json("policy_check/result.json", loan_result)
    return {"policy_check": loan_result}


async def check_companies_house(state: ApplicationState) -> dict[str, Any]:
    application = state["application"]

    PROMPT = """You are a company verification researcher.
        Your job is to confirm the applicant's company is a genuine, active UK
        company registered with Companies House, and that the named applicant
        appears as an officer or person with significant control.
        The company number may be missing or wrong and the company name may be
        misspelled or a trading name rather than the registered name — search
        by name first if the company number doesn't resolve.
        You have access to the CompaniesHouse___* tools."""

    agent = create_agent(
        model=model,
        tools=state["tools"],
        system_prompt=PROMPT,
    )

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=json.dumps(application))]}
    )
    companies_house_result = response["messages"][-1].content

    state["store"].put_json("companies_house/result.json", companies_house_result)
    return {"companies_house": companies_house_result}


async def search_web(state: ApplicationState) -> dict[str, Any]:
    company_name = state["application"]["company_name"]
    PROMPT = """
        You are an internet researcher.
        Your job is to search the internet for the person below or their company.
        Look for any websites or pages on linked-in. Note that there may be alternative spellings of the person or applicant
        such as shortened names or nick-names, or alternative names for the company, such as trading-as or minor grammatical differences.
        You have access to the tool websearch-target___WebSearch
        """

    agent = create_agent(
        model=model,
        tools=state["tools"],
        system_prompt=PROMPT,
    )

    response = await agent.ainvoke(
        {"messages": [HumanMessage(content=f"Company: {company_name}")]}
    )

    web_search_result = response["messages"][-1].content

    state["store"].put_json("web_search/result.json", web_search_result)
    return {"web_search": web_search_result}

# policy check is agentic rag (but essentially a single tool)
# companies house is agentic search (i suppose could involve multiple hops)
# websearch is agentic search looking for website, person linkedin etc.
# assessment is check it all, then loop back to a previous tool (1x)
# if data collected is unsatisfactory
def reassess(state: ApplicationState) -> dict[str, Any]:
    assessment = _invoke_assessment_model(
        application=state["application"],
        policy_check=state["policy_check"],
        companies_house=state["companies_house"],
        web_search=state["web_search"],
    )
    store = state["store"]
    store.put_json("assessment/final_result.json", assessment)
    store.put_json("assessment/audit_trail.json", {
        "customer_id": state["identity"].customer_id,
        "application_id": state["identity"].application_id,
        "inputs": {
            "policy_check": "policy_check/result.json",
            "companies_house": "companies_house/result.json",
            "web_search": "web_search/result.json",
        },
        "decision": assessment.get("decision"),
        "model_id": assessment.get("model_id"),
    })
    return {"assessment": assessment}


# ---------------------------------------------------------------------------
# 5. Graph wiring
# ---------------------------------------------------------------------------

def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None):
    """Compiled per invocation, not once at module load. The checkpointer (if
    any) has to be built from that invocation's customer-scoped session — see
    `build_checkpointer` — so it can't be baked into a module-level singleton
    the way an uncheckpointed graph could be.
    """
    g = StateGraph(ApplicationState)
    g.add_node("load_application", load_application)
    g.add_node("policy_check", check_against_policy)
    g.add_node("companies_house", check_companies_house)
    g.add_node("web_search", search_web)
    g.add_node("reassess", reassess)

    g.add_edge(START, "load_application")
    # Evidence-gathering nodes run sequentially, each depending on the
    # previous one's output being in state.
    # TODO would be conditional edge - check whether meets basic policy check,
    # check if exists in companies house before proceeeding
    g.add_edge("load_application", "policy_check")
    g.add_edge("policy_check", "companies_house")
    g.add_edge("companies_house", "web_search")
    # reassess only has one predecessor: the last node in the chain. Wiring
    # it from all three (as a leftover from an earlier parallel-fan-out
    # design) made it fire as soon as policy_check completed — before
    # companies_house/web_search had even run — and then cancel them
    # mid-flight once reassess's own (still-unimplemented) call raised.
    g.add_edge("web_search", "reassess")
    g.add_edge("reassess", END)
    return g.compile(checkpointer=checkpointer)


# The AgentCore Runtime entrypoint lives in main.py (that's what agentcore.json
# points at). This module is the library it wires up: identity, scoped storage,
# the checkpointer, and the graph builder.


# ---------------------------------------------------------------------------
# 6. Stubs — replace with your Bedrock / Gateway MCP client calls
# ---------------------------------------------------------------------------

def _invoke_assessment_model(**kwargs: Any) -> dict:
    raise NotImplementedError
