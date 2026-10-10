# Scoping: upgrade @aws/agentcore-cdk to 1.0.0-rc.2

Status: **migration applied for real** on this branch (`feature/agentcore-identity-migration`) — `package.json`, `bin/cdk.ts`, `lib/cdk-stack.ts`, `test/cdk.test.ts`, and `agentcore.json` all updated to rc.2/schema v2, compiling and synthesizing cleanly for both the `default` (prod) and `dev` targets. Originally scoped via a static diff of `0.1.0-alpha.53` against `1.0.0-rc.2` (`npm pack`'d and inspected locally), then proven out in a disposable git worktree (`spike/agentcore-cdk-rc2-synth`, since discarded) before being reapplied here on top of the multi-account (`local-dev-roles.json`, `aws-targets.json` dev entry) changes. Not yet deployed to any real AWS account — see "Next: actually deploying to dev" below. This supersedes the "custom resource" approach in [agentcore-identity-migration.md](agentcore-identity-migration.md); rc.2 makes Identity credential providers declarative, but getting there requires migrating past several breaking changes first.

## What changed, for real, this time

Same edits validated in the spike (see "Confirmed breaking changes" and "Confirmed by actually installing rc.2" below for the full rationale), reapplied directly to the live files:
- `package.json`: `@aws/agentcore-cdk` → `1.0.0-rc.2`, `aws-cdk-lib` → `~2.271.0`.
- `bin/cdk.ts`: `ConfigIO` → `readAgentCoreProject` + `AgentCoreProjectSpecSchema.parse`, dropped the manual `mcpSpec` extraction (Gateway is now spec-driven inside `AgentCoreApplication`), `memoryName` → `managedMemory` in the harness-config loop, simplified the deployed-state credentials read now that it's typed.
- `lib/cdk-stack.ts`: dropped `AgentCoreMcp`/`AgentCoreMcpSpec` and the manual `new AgentCoreMcp(...)` block, added the now-required `projectRoot` prop, `application.environments` → `application.runtimeEnvironments`, `env.agent` → `env.spec`.
- `test/cdk.test.ts`: `version: 1` → `2`, dropped the now-rejected `mcpRuntimeTools`/`unassignedTargets`/`datasets` fields, added `projectRoot`.
- `agentcore.json`: `version: 1` → `2`, dropped `$schema`, `abTests` (already empty), and `datasets` (see the unresolved gap below).

Verified: `npx tsc --noEmit` clean, `npx jest` passes, and a real synth (`node dist/bin/cdk.js`, i.e. `cdk synth`) produces all four expected stacks (`AgentCore-fionaa-default`, `AgentCore-fionaa-security-foundations`, `AgentCore-fionaa-dev`, `AgentCore-fionaa-security-foundations-dev`) with no errors.

## New finding: Gateway config is baked with the wrong account for dev

Fionaa's Gateway integration env vars (`AGENTCORE_GATEWAY_URL`, `AGENTCORE_GATEWAY_TOKEN_ENDPOINT`, the Secrets Manager client-secret ARN — all at [cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts) around the Gateway wiring block) are templated off `this.account`/`this.region`. Synthesizing for the `dev` target produces `https://claims-agent-046451669835.auth.us-east-1.amazoncognito.com/oauth2/token` — a Cognito domain that only exists in **prod** (`492646066653`); the actual Gateway (`claimsagent-claimsgateway`) and its Cognito app client live there, not in dev. This isn't a synth/deploy blocker (CDK doesn't validate that referenced ARNs exist), but it means: **Runtime, Memory, Guardrail, and Evaluators will deploy and work fine in dev, but any Gateway-dependent tool call (Companies House, web search, location matching) will fail at agent runtime** until the Gateway cross-account access work from [multi-account-dev-prod.md](multi-account-dev-prod.md) happens. Worth knowing before treating a dev deploy as a full functional test of the upgrade.

## Deployed to dev — real, end-to-end validation

`cdk deploy AgentCore-fionaa-dev --profile dev` was run for real (scoped to dev only, per the `--stack-name` argument — prod's stacks are never touched by this). First attempt failed and cleanly rolled back (`Role does not have access for model` on both Evaluators) — a brand-new-account first-use hiccup (no Bedrock/AgentCore service-linked role existed yet in dev at all, confirmed via `aws iam list-roles`), not a code bug. Retrying immediately succeeded: 29/29 resources, full `AgentCore-fionaa-dev` stack live with a real Runtime, Memory, both Evaluators, the geo-area-match Lambda, and the Guardrail.

**Full graph invocation test, not just infrastructure existence**: staged a real test application in S3, minted a Cognito ID token via the existing eval-harness test user (`fionaa-eval-ci@example.com`, shared Cognito pool — same inbound-auth mechanism prod uses), and POSTed directly to the Runtime's `/invocations` endpoint (same pattern as [eval_path2_stage_and_invoke.py](../../../evals/runtime/eval_path2_stage_and_invoke.py)). Got back a real `200` with `outcome: pending_human_review` and URIs for every node's output (policy_check, companies_house, financial_assessment, web_search, decision) — the whole LangGraph ran end-to-end on rc.2, including a live Gateway call to Companies House through the dev credentials wired up earlier.

**Two more real bugs found and fixed along the way, both pre-existing (not introduced by the rc.2 migration):**

1. **S3 bucket was prod-account-only, same pattern as the Gateway bug.** `cdk-stack.ts` hardcoded the same physical bucket name (`fionaa-6655-assets`) for every target account, and that bucket has no bucket policy at all (`NoSuchBucketPolicy`) — meaning dev's Runtime could not read or write application data, and couldn't read the shared `loan_policy_documents/*` needed for `policy_check`. Fixed: prod keeps importing its existing bucket via a new `existingApplicationsBucketName` prop (sourced from [agentcore/applications-bucket-overrides.json](../../../agentcore/applications-bucket-overrides.json)); any account with no override — dev included — gets its own fresh, KMS-encrypted, non-public bucket instead. Confirmed synth-diff clean for prod (no new bucket, unchanged). Copied all 28 `loan_policy_documents/*` files into dev's new bucket via `aws s3 sync` (download from prod with `AIOps`, upload to dev with `dev`).
2. **Guardrail IAM policy was missing the cross-region guardrail-profile ARN — and needed two fixes, not one.** `ApplyFionaaGuardrail`'s policy only granted `bedrock:ApplyGuardrail` on the guardrail's own ARN, not the `guardrail-profile/us.guardrail.v1:0` ARN that STANDARD-tier cross-region invocation actually routes through. First fix granted the profile ARN for `this.region` only — that resolved the error on the next invocation, but a *later* invocation hit the identical `AccessDeniedException` again, this time on `guardrail-profile/us.guardrail.v1:0` in **`us-east-2`**, a different region than the stack's own `us-east-1`. The "us." cross-region profile can dispatch a single `ApplyGuardrail` call to any US region it covers, not just the deploying stack's region, so the grant needed a wildcard region (`arn:aws:bedrock:*:${this.account}:guardrail-profile/us.guardrail.v1:0`), not a literal one. Confirmed fixed on the third real invocation: fully grounded, `confidence: "high"`, real Companies House data, zero access errors anywhere in the trace. **Prod runs the identical runtime-role code and has almost certainly been silently failing this same contextual-grounding check on every invocation** (caught by the app's own fallback path rather than crashing, so it never surfaced as a hard error) — worth deploying this fix to prod on its own, independent of the rc.2 upgrade.

Also hit (and resolved) a **fresh-dev-account Bedrock Marketplace gap**: Claude Sonnet 4.5 (the main graph's model, `us.anthropic.claude-sonnet-4-5-20250929-v1:0`) had never been subscribed to in this account. Resolved via CLI, not the console: `aws bedrock list-foundation-model-agreement-offers` → `aws bedrock create-foundation-model-agreement` with the returned offer token. Took a few minutes to propagate from `PENDING` to actually working.

**Final state**: a real end-to-end invocation against dev now runs completely clean — full LangGraph execution (policy_check, companies_house with live, grounded Companies House data at `confidence: "high"`, financial_assessment, web_search, triage), `outcome: pending_human_review`, zero access-denied errors anywhere in any node's output.

## Confirmed by actually installing rc.2

`npm install` with `@aws/agentcore-cdk@1.0.0-rc.2` failed until `aws-cdk-lib` was also bumped from `~2.261.0` to `~2.271.0` — rc.2 peer-depends on `aws-cdk-lib@^2.269.0`. That's a fourth coupled dependency, not previously identified from static inspection alone. Once bumped, `npm install` succeeded cleanly (293 packages, 0 vulnerabilities).

`npx tsc --noEmit` against the unmodified repo code then gave **exact, compiler-verified breakage** — stronger evidence than the `.d.ts` diffing below:

```
bin/cdk.ts(4,10): error TS2305: Module '"@aws/agentcore-cdk"' has no exported member 'ConfigIO'.
bin/cdk.ts(101,9): error TS2353: Object literal may only specify known properties, and 'memoryName' does not exist in type 'HarnessDeploymentConfig'.
lib/cdk-stack.ts(3,3): error TS2305: Module '"@aws/agentcore-cdk"' has no exported member 'AgentCoreMcp'.
lib/cdk-stack.ts(7,8): error TS2305: Module '"@aws/agentcore-cdk"' has no exported member 'AgentCoreMcpSpec'.
lib/cdk-stack.ts(141,40): error TS2339: Property 'environments' does not exist on type 'AgentCoreApplication'.
lib/cdk-stack.ts(623,44): error TS2339: Property 'environments' does not exist on type 'AgentCoreApplication'.
lib/cdk-stack.ts(693,48): error TS2339: Property 'environments' does not exist on type 'AgentCoreApplication'.
test/cdk.test.ts(10,7): error TS2322: Type '1' is not assignable to type '2'.
```

That last one is the sharpest finding of the spike: `test/cdk.test.ts` hardcodes `version: 1` in an inline spec literal, and rc.2's types reject it outright. This is no longer an inference from reading `AgentCoreProjectSpecSchema`'s `z.ZodLiteral<2>` in isolation — it's a live compile failure against this repo's own code, which removes the "maybe the CLI is lenient about the literal" doubt entirely. The v1→v2 config migration is mandatory, not advisory.

Two more findings beyond the earlier static diff:
- **`ConfigIO` (the class `bin/cdk.ts` uses to load `agentcore.json` — `new ConfigIO({baseDir}).readProjectSpec()` / `.readAWSDeploymentTargets()`) is removed entirely**, not renamed. rc.2's replacement is the free function `readAgentCoreProject` (exported from the package root). The config-loading section of `bin/cdk.ts` (lines ~26-45, including the manual `mcpSpec` extraction from `specAny.agentCoreGateways` for the now-deleted `AgentCoreMcp` construct) needs a rewrite, not a search-and-replace.
- **`HarnessDeploymentConfig` dropped its `memoryName` field.** `bin/cdk.ts`'s harness-config loop currently pre-extracts `memoryName: harnessSpec.memory?.mode === 'existing' ? harnessSpec.memory.name : undefined` for the CDK construct to consume. rc.2 wants something else here (consistent with `AgentCoreApplication`'s new internal `resolveHarnessMemory` doing this resolution itself) — exact replacement field not yet confirmed, needs checking against rc.2's `AgentCoreHarnessRole`/`HarnessRoleConfig` types before writing the fix.

## Bottom line

This is not a `package.json` version bump. Three things move together and all three are required before Identity's declarative `credentials[]` can be used:

1. **The project config schema itself is versioned and enforced.** [agentcore.json](../../../agentcore/agentcore.json) declares `"$schema": ".../v1/agentcore.json"`, `"version": 1`. rc.2's schema (`AgentCoreProjectSpecSchema`) hard-codes `version: z.ZodLiteral<2>` — the file has to become a v2 document, not just be read by a newer library. I have not found a migration command in this package for v1→v2; that needs to be confirmed with AWS's current AgentCore CLI docs before starting, not assumed.
2. **`cdk-stack.ts` uses constructs/props that were renamed or removed in rc.2.**
3. **The `agentcore` CLI (used for `agentcore dev`/`deploy`/`invoke`, referenced in [cdk-stack.ts:368](../../../agentcore/cdk/lib/cdk-stack.ts#L368) local-dev role) has its own version compatibility with the schema/CDK library version — needs checking, not assumed compatible.**

## Confirmed breaking changes in cdk-stack.ts

| Current (alpha.53) | rc.2 | Where it's used now |
|---|---|---|
| `AgentCoreMcp` construct, instantiated manually with `mcpSpec` | Removed. Gateways are now created automatically inside `AgentCoreApplication` from the project spec's gateway entries, exposed via `application.gateways: Map<string, AgentCoreGateway>` | [cdk-stack.ts:126-135](../../../agentcore/cdk/lib/cdk-stack.ts#L126-L135) — this whole block is deleted |
| `application.environments: Map<string, AgentEnvironment>` | Renamed to `application.runtimeEnvironments: Map<string, RuntimeEnvironment>`, plus a new direct `application.runtimes: Map<string, AgentCoreRuntime>` | [cdk-stack.ts:141](../../../agentcore/cdk/lib/cdk-stack.ts#L141) `this.application.environments.get('fionaa')`, and the payments loop at [cdk-stack.ts:644,714](../../../agentcore/cdk/lib/cdk-stack.ts#L644) `this.application.environments.values()` |
| `AgentEnvironment.agent` (has `.entrypoint`, `.protocol`) | Renamed to `RuntimeEnvironment.spec` (type `AgentEnvSpec`) | `isPaymentEligibleAgent(agent: {entrypoint, protocol})` at [cdk-stack.ts:88-95](../../../agentcore/cdk/lib/cdk-stack.ts#L88-L95), called as `isPaymentEligibleAgent(env.agent)` at [cdk-stack.ts:645,715](../../../agentcore/cdk/lib/cdk-stack.ts#L645) — becomes `env.spec` |
| `AgentCoreApplicationProps` — no `projectRoot` | `projectRoot: string` now **required** | The `new AgentCoreApplication(this, 'Application', appProps)` call at [cdk-stack.ts:124](../../../agentcore/cdk/lib/cdk-stack.ts#L124) needs a new required field threaded from `bin/cdk.ts` |
| `AgentEcrRepository` | Renamed `ContainerRepository` | Not directly imported in this repo's `cdk-stack.ts`/`bin/cdk.ts` today — low risk, but check `.llm-context/agentcore.ts` and any generated CLI scaffolding that might reference the old name |
| `credentials?: Record<string, {credentialProviderArn, clientSecretArn?}>` | Renamed type-alias `DeployedCredentials`, **same shape** (`CredentialDeployedStateSchema` = `{credentialProviderArn: string, clientSecretArn?: string}`) | [cdk-stack.ts:61,121-123](../../../agentcore/cdk/lib/cdk-stack.ts#L61) — low risk, likely just a type import rename |

Not yet checked (do before implementing): `AgentCoreRuntime`'s own method surface (`addToPolicy`, `addEnvironmentVariable`, `.role`, `.roleArn`) — spot-checked as still present in rc.2's `AgentCoreRuntime.d.ts` export list but not diffed line-by-line against actual call sites.

## Why the Identity payoff still holds after the upgrade

Fionaa's real Gateway (`claimsagent-claimsgateway`) is owned by a different, out-of-repo stack and isn't declared in `agentcore.json`'s `agentCoreGateways` (currently `[]` — it's wired entirely by hand as raw env vars at [cdk-stack.ts:553-563](../../../agentcore/cdk/lib/cdk-stack.ts#L553-L563)). rc.2's `AgentCoreGateway.fromGatewayArn(scope, id, gatewayArn, outboundAuth)` is built for exactly this: import an externally-owned gateway by ARN and supply `outboundAuth: { oauth: { providerArn, scopes, grantType: 'CLIENT_CREDENTIALS' } }`. Combined with a top-level `credentials[]` entry of `authorizerType: "OAuthCredentialProvider"` in the (migrated) project spec, this replaces [gateway.py](../../src/fionaa/gateway.py)'s manual Secrets Manager + Cognito flow without a custom resource — confirming the assessment from the previous doc, now that the construct shapes are confirmed rather than inferred from the schema alone.

## Mechanical migration: done and synth-verified in the spike worktree

All fixes from the "Confirmed breaking changes" table above were applied in `spike/agentcore-cdk-rc2-synth` (`bin/cdk.ts`, `lib/cdk-stack.ts`, `test/cdk.test.ts`, plus migrating `agentcore.json` to `version: 2`). Result:

- `npx tsc --noEmit` — clean.
- `npx jest` — the existing unit test passes.
- `node dist/bin/cdk.js` (equivalent to `cdk synth`, run directly since the `cdk` CLI wrapper produces no stdout for a multi-stack app) — **synthesizes successfully** against Fionaa's real `agentcore.json`/`aws-targets.json`, producing both `AgentCore-fionaa-default` and `AgentCore-fionaa-security-foundations` templates.

`agentcore.json` v1→v2 turned out to be far smaller than feared: only `$schema`, `abTests` (already empty for Fionaa), and `datasets` are rejected as unrecognized top-level keys by `AgentCoreProjectSpecSchema` in rc.2 — everything else (`runtimes`, `memories`, `credentials`, `evaluators`, `agentCoreGateways`, etc.) validated as-is after bumping `version: 2`. **`datasets` dropping is not cosmetic** — see below.

## Critical finding: this upgrade replaces the live Runtime and Memory, not updates them

I diffed the synthesized `AgentCore-fionaa-default` template (rc.2, mechanically migrated) against a baseline synth of the **unmodified, currently-deployed code** (alpha.53, same `agentcore.json`/`aws-targets.json`, same account/region). Every logical ID for the stack's two most sensitive resources changed:

| Baseline (deployed today) | rc.2 | 
|---|---|
| `ApplicationAgentFionaaRuntimeA1BB9E30` (`AWS::BedrockAgentCore::Runtime`) | `ApplicationRuntimeFionaaRuntime3F6582F6` |
| `ApplicationAgentFionaaRuntimeExecutionRole45C2EC50` + its policy | `ApplicationRuntimeFionaaRuntimeExecutionRoleD1FE981A` + a new policy |
| `ApplicationMemoryFionaaCheckpoint95B0AA9C` (`AWS::BedrockAgentCore::Memory`) | `ApplicationMemoryFionaaCheckpointMemoryE1C6271D` |
| `ApplicationMemoryFionaaCheckpointExecutionRole04BC70DD` | `ApplicationMemoryFionaaCheckpointServiceRole31C5980A` |

This is the `AgentEnvironment`→`RuntimeEnvironment` rename (and an internal `Memory`→`...Memory` construct-id restructure) leaking into CloudFormation's logical IDs, which are derived from the construct tree path. **CloudFormation has no way to know these are the "same" resource under a new name — it will delete the old Runtime/Memory/roles and create new ones.** For Fionaa that means: a new Runtime ARN (breaking anything that references the current one — Cognito authorizer binding, monitoring, any hardcoded endpoint), and **loss of the checkpoint Memory's stored session history** (30-day event data, per [cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts) — `FionaaCheckpoint`'s `eventExpiryDuration: 30`). This is not survivable as a routine deploy; it needs `overrideLogicalId()` pinning on every renamed construct (Runtime, its role, Memory, its role) *before* this is safe to run against the live stack, then a real `cdk diff` against the deployed stack (not just a fresh synth) to confirm zero-resource-change.

The two evaluators also got new logical ID suffixes (`...6CE16DA4`→`...2B2AEC2B` etc.) — same category of risk, lower stakes (evaluators are stateless config, replacement is annoying but not data-destructive).

**Confirmed, not just inferred**: dropping `datasets` (required by the v2 schema, which has no such field) also drops the actual `AWS::BedrockAgentCore::Dataset` resource (`ApplicationDatasetFionaaEvalDatasetCDA5F93E`) from the synthesized template. Fionaa's `fionaa_eval_dataset` registration would be deleted outright, not just re-expressed differently — wherever datasets live in the v2 model (not yet found in this package), it needs to be re-added before this upgrade ships, not treated as a schema-cleanup no-op.

Minor, lower-confidence finding: `AWS::CDK::Metadata` and two default Lambda `AWS::Logs::LogGroup` resources present in the baseline are absent from the rc.2 synth. Not yet root-caused — plausibly a behavior change from the required `aws-cdk-lib` 2.261→2.271 bump rather than `@aws/agentcore-cdk` itself, since neither Lambda function's own properties changed. Worth understanding, unlikely to be dangerous (CloudWatch will auto-create a log group on first invoke either way).

## Recommended sequencing

1. ~~Confirm the v1→v2 `agentcore.json` migration path~~ — done above; it was a 3-field removal, not a rewrite. `datasets`' new home is the one still-unresolved unknown, and it must be resolved before proceeding (see above).
2. **Add `overrideLogicalId()` calls pinning the Runtime, its execution role, the Memory, and its execution role to their current logical IDs**, then re-synth and re-diff against baseline until the resource-identity table above shows zero changes. This is now a required step, not a precaution — the un-pinned upgrade does not go in without deleting the live agent and its memory.
3. Once diff-clean: land the mechanical renames as one commit with no live-infrastructure impact.
4. Only then add the Identity credential provider + switch `gateway.py` — as its own commit.

## Open questions

1. Where does `datasets` live in the v2 schema/model? Not found in this package's exports — check current AWS docs, since Fionaa's eval dataset registration depends on it.
2. Is `1.0.0-rc.2` recommended for production use, or should this wait for GA given the API can still move pre-1.0.0?
3. Confirm the `agentcore` CLI version installed for local dev ([cdk-stack.ts:368-372](../../../agentcore/cdk/lib/cdk-stack.ts#L368-L372), `agentcore dev`) is compatible with a v2 schema project before switching, or local dev breaks even if CDK deploy works.
4. Root-cause the missing `AWS::CDK::Metadata` / default Lambda log-group resources (likely the `aws-cdk-lib` version bump, not `@aws/agentcore-cdk`) before assuming they're harmless.
