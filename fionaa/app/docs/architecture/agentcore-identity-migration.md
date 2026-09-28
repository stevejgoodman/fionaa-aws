# Migrating Gateway auth to AgentCore Identity

Status: proposed, not implemented. Written to scope the work before touching `cdk-stack.ts` or `gateway.py`.

## What this covers, and what it doesn't

Fionaa has three separate auth mechanisms today. Only one of them is a real gap relative to AgentCore Identity:

| Layer | Current implementation | AgentCore Identity equivalent | In scope? |
|---|---|---|---|
| Inbound (caller → Runtime) | `CustomJWTAuthorizerConfig` validating Cognito tokens ([cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts), `AgentCoreRuntimeProps`) | This *is* Identity's inbound-auth mechanism already | No change needed |
| Outbound (agent → Gateway) | Hand-rolled Cognito client-credentials: client secret in Secrets Manager, token minted with `urllib` ([gateway.py](../../src/fionaa/gateway.py)) | Identity OAuth2 credential provider + workload token vault (`GetWorkloadAccessToken`) | **Yes — this doc** |
| Resource access (agent → S3/KMS/Memory) | STS `AssumeRole` into `FionaaDataAccessRole` with `customer_id`/`application_id` session tags ([cdk-stack.ts:343-473](../../../agentcore/cdk/lib/cdk-stack.ts#L343-L473)) | Not an Identity concern — this is per-tenant IAM isolation, Identity doesn't do resource-level scoping | No change — stays as-is |

So "migrate to AgentCore Identity" in practice means: replace the manual Cognito client-credentials dance in `gateway.py` with an Identity-managed OAuth2 credential provider.

## Why this isn't a drop-in CDK change

`@aws/agentcore-cdk` is pinned at `0.1.0-alpha.53` ([package.json](../../../agentcore/cdk/package.json)). Its exports ([dist/index.d.ts](../../../agentcore/cdk/node_modules/@aws/agentcore-cdk/dist/index.d.ts)) cover Runtime, Memory, Gateway/MCP, Payments, Policy, Harness, Knowledge Base — there is no `AgentCoreIdentity`, `CredentialProvider`, or `OAuth2CredentialProvider` L2/L3 construct in this version. The stack would need to provision the credential provider via `AwsCustomResource` calling the control-plane API directly, following the same pattern already used for `PutModelInvocationLoggingConfiguration` at [cdk-stack.ts:236-254](../../../agentcore/cdk/lib/cdk-stack.ts#L236-L254).

## Proposed changes

### 1. CDK: provision an OAuth2 credential provider (custom resource)

Add near the existing `gatewaySecret` block ([cdk-stack.ts:542-563](../../../agentcore/cdk/lib/cdk-stack.ts#L542-L563)):

- An `AwsCustomResource` calling `bedrock-agentcore-control:CreateOauth2CredentialProvider`, `:UpdateOauth2CredentialProvider` (onUpdate), `:DeleteOauth2CredentialProvider` (onDelete), configured with:
  - vendor: generic OAuth2 (Cognito as the IdP)
  - token endpoint: the existing `claims-agent-<account>.auth.us-east-1.amazoncognito.com/oauth2/token`
  - client ID: `qcqj6bgve5u8c2bg1qkiobsps` (existing)
  - client secret: passed once at creation — the service re-encrypts and stores it internally; **the existing `fionaa/agentcore-gateway-client-secret` Secrets Manager entry can be deleted from the stack afterward** (Identity takes over as the secret's system of record)
  - scopes: `agentcore/invoke`
- IAM: grant the Runtime execution role `bedrock-agentcore:GetWorkloadAccessToken` / `GetResourceOauth2Token` (exact action names TBC — verify against current `create-oauth2-credential-provider` docs before implementing, this alpha SDK/API surface moves) scoped to the new provider's ARN.
- Env vars: replace `AGENTCORE_GATEWAY_CLIENT_ID` / `AGENTCORE_GATEWAY_CLIENT_SECRET_ARN` / `AGENTCORE_GATEWAY_TOKEN_ENDPOINT` / `AGENTCORE_GATEWAY_OAUTH_SCOPES` with a single `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER_ARN` (or name).
- Remove: the `gatewaySecret.grantRead(...)` grant and the four `addEnvironmentVariable` calls for the manual OAuth config.

### 2. App: replace `gateway.py`'s manual token minting

Replace `_gateway_client_secret()` / `_gateway_token()` with a call to the AgentCore Identity SDK's workload-token helper (`bedrock_agentcore.identity` or equivalent — confirm the current Python SDK surface, this has changed across AgentCore SDK releases) to fetch a bearer token for the configured credential provider. `load_gateway_tools()`'s shape (mint token → build `MultiServerMCPClient` with `Authorization: Bearer`) stays the same; only the token-minting call changes.

### 3. Tests

`tests/live_helpers.py` and anything stubbing `AGENTCORE_GATEWAY_CLIENT_SECRET_ARN` / the Cognito token endpoint needs updating to stub the Identity token call instead.

## Open questions before implementation

1. **Exact IAM action names and SDK call surface for AgentCore Identity are alpha/evolving** — this doc's action names must be re-verified against current AWS docs at implementation time, not trusted from this write-up.
2. **Blast radius**: the Gateway (`claimsagent-claimsgateway`) is an out-of-repo stack owned elsewhere. Confirm whether its owners are already using Identity for their own OAuth2 flow, or whether Fionaa switching unilaterally creates a second, inconsistent auth path into the same Gateway.
3. **Value vs. cost**: the concrete win is AWS-managed secret rotation/audit for one client secret. Given the custom-resource complexity and the alpha SDK surface, confirm this is worth doing now versus once `@aws/agentcore-cdk` ships a native Identity construct.
