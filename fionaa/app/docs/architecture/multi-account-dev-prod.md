# Scoping: multi-account dev/pre-prod/prod topology

Status: dev account created (`046451669835`); the `aws-targets.json`/`bin/cdk.ts` mechanics for it are done and synth-verified. Everything else below (Gateway cross-account access, CI promotion pipeline, pre-prod account) is still scoping only. Based on the AWS AgentOps blog's recommended model ([aws.amazon.com/blogs/machine-learning/agentops-...](https://aws.amazon.com/blogs/machine-learning/agentops-operationalize-agentic-ai-at-scale-with-amazon-bedrock-agentcore/)) and the current state of this repo's deployment config ([aws-targets.json](../../../agentcore/aws-targets.json), [bin/cdk.ts](../../../agentcore/cdk/bin/cdk.ts)).

## Done

- `aws-targets.json`: added a `dev` target (`046451669835`, `us-east-1`) alongside the existing `default` (prod, `492646066653`) — see file for why `default` wasn't renamed to `prod`.
- `bin/cdk.ts`: fixed the `SecurityFoundationsStack` dedupe-by-account gap the code already flagged in a comment — it previously only deployed account-level security foundations (CloudTrail, Inspector, cost alarms, Macie) to `targets[0]`'s account. Now it dedupes by `${account}/${region}` and deploys one copy per distinct account, so dev gets its own `AgentCore-fionaa-security-foundations-dev` stack. The first-seen account/region keeps the original unsuffixed stack name (`AgentCore-fionaa-security-foundations`) to avoid renaming prod's already-deployed stack.
- Verified via synth-diff (stashing the changes, building a baseline, then comparing): both of prod's existing templates (`AgentCore-fionaa-default`, `AgentCore-fionaa-security-foundations`) are byte-identical before/after — this change is purely additive for prod. A fourth stack, `AgentCore-fionaa-dev`, now also synthesizes.

## Done: local-dev IAM trust, per account

Fixed. [cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts)'s `LocalDevOnly` trust statement on `FionaaDataAccessRole` now takes a `localDevRoleArn` stack prop instead of a hardcoded ARN, and is only added when one is supplied for the account. [bin/cdk.ts](../../../agentcore/cdk/bin/cdk.ts) reads the new [agentcore/local-dev-roles.json](../../../agentcore/local-dev-roles.json) (account ID → SSO role ARN, optional/opt-in per account) and passes the entry matching each target's account.

Confirmed via `aws sts get-caller-identity --profile dev` against the AWS CLI profile you created for the dev account: dev's role is `arn:aws:iam::046451669835:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_AdministratorAccess_dd6ea54c80028836` — a **different hash** from prod's `...AdministratorAccess_28e99e3e3cd43fb4` despite the identical permission-set name, confirming the earlier caution that these can't be assumed to match across accounts. Verified via synth: prod's `AgentCore-fionaa-default` template keeps the exact same `LocalDevOnly` grant it had before (same ARN, same conditions — just now conditionally added rather than unconditional), and the new `AgentCore-fionaa-dev` template carries dev's own correct ARN.

## What the blog recommends

- **Separate AWS accounts per environment**: a shared-services account (ECR, pipeline artifacts, Secrets Manager, central monitoring), and dedicated dev / pre-prod / prod application accounts.
- **Per-account AgentCore resources**: Runtime and Memory are account-scoped and deployed independently to each environment — each account owns its own Memory (data residency, independent scaling, cost allocation).
- **Gateway starts shared**, hosted in the shared-services account, with IAM policies restricting direct access so app accounts route through it rather than calling tools directly.
- **Promotion pipeline**: merge to main → deploy to pre-prod → run integration/performance/security/agentic-AI evaluation gates → automatic promotion to prod on pass.
- **Runtime version aliases** (`DEV`/`PREPROD`/`PROD`) on AgentCore Runtime's immutable versions, for independent promotion and instant rollback within a single Runtime rather than fully separate Runtimes per stage in some designs.
- Everything provisioned via IaC to keep tool names and config identical across accounts and prevent drift.

## Current state here

- One AWS account (`492646066653`) does everything: the Fionaa Runtime, Memory, guardrails, and (per [agentcore-cdk-1.0-upgrade-scope.md](agentcore-cdk-1.0-upgrade-scope.md)) it's also where the destructive Runtime/Memory replacement from the rc.2 CDK upgrade would land if run today.
- `aws-targets.json` already has the right shape for this — it's a list of `{name, account, region}`, and `bin/cdk.ts` already loops over every entry, deploying a full `AgentCoreStack` + `SecurityFoundationsStack` per target. **Multi-account is already mechanically supported here** — today there's just one entry.
- No deploy pipeline exists in CI at all (checked `.github/workflows/`) — only eval workflows (`deepeval-ci.yml`, `evals-path2-batch-eval.yml`), both authenticating via GitHub OIDC into the same single account. Deploys today are manual, via `agentcore deploy` run locally (the `LocalDevOnly` SSO trust statement in [cdk-stack.ts:372-387](../../../agentcore/cdk/lib/cdk-stack.ts#L372-L387) confirms this).
- Fionaa's Gateway (`claimsagent-claimsgateway`) is already "shared" in spirit — it's an out-of-repo stack this app imports by ARN rather than owning — but it currently lives in the *same* account as the Fionaa Runtime. The blog's model needs it in a distinct shared-services account with cross-account access, which is a real move, not just a relabeling.
- `AwsDeploymentTargetSchema` (checked in the rc.2 package) is `{name, description?, account, region}` — it has no field for a cross-account deploy-role ARN. The schema doesn't model *how* you get credentials into a target account; that's left entirely to whatever runs `cdk deploy`/`agentcore deploy` (a local AWS profile, or a CI OIDC role) already being scoped to that account. This piece has to be built, not configured.

## What I can't do from here

The AWS access available in this session is a different, unrelated AWS account (`142409398286`, via root credentials) — not Fionaa's real account. I have no visibility into whether `492646066653` is already part of an AWS Organization, and **creating new AWS accounts is not something I can or should do autonomously**: it requires org-admin access, a unique root email per new account, and has real billing/governance consequences that only you can authorize and execute (via AWS Organizations' `CreateAccount`, or your org admin's console). That's the hard blocker before anything below can become real infrastructure.

## What's needed, roughly in order

1. **You/your org admin**: decide account count and names. The blog's full model is 4 accounts (shared-services, dev, pre-prod, prod); given `492646066653` already exists and does everything today, the pragmatic mapping is probably: keep it as **prod**, create **dev** and **shared-services** first (pre-prod can follow once dev/prod promotion is working). Create them under an AWS Organization if one doesn't already exist — needed for consolidated billing and for the cross-account IAM trust relationships below to be manageable.
2. **Me, once account IDs exist**:
   - Add entries to `aws-targets.json` per new account (mechanical — the loop in `bin/cdk.ts` already handles it).
   - Design and add a cross-account deploy-role stack: an IAM role in each app account, trusted by GitHub's OIDC provider (mirroring the existing `deepeval-ci.yml`/`evals-path2-batch-eval.yml` pattern) or by a CI/CD account, scoped to `cdk deploy`'s needs.
   - Design the Gateway's cross-account access: move/re-point `claimsagent-claimsgateway` into shared-services (coordinate with whoever owns that stack — it's out-of-repo), add a resource policy or cross-account role allowing each app account's Runtime role to invoke it. This is the part of the blog's model this repo is currently furthest from.
   - Build the actual GitHub Actions promotion pipeline: dev deploy on merge to main, a pre-prod gate running the existing eval suite ([EVALS.md](../../../agentcore/EVALS.md) territory — the two eval workflows already exist and could feed this gate), manual or automatic promotion to prod.
   - Investigate AgentCore Runtime version aliases (mentioned in the blog) against the CDK constructs available — not yet checked whether `@aws/agentcore-cdk` (alpha.53 or rc.2) exposes this, or whether it's console/CLI-only today.
3. **Sequencing with the in-flight CDK upgrade work**: this is good news for that effort specifically — [agentcore-cdk-1.0-upgrade-scope.md](agentcore-cdk-1.0-upgrade-scope.md) found that upgrading to `1.0.0-rc.2` naively deletes and recreates the live Runtime and Memory. A dev account is exactly where that upgrade should land first — once dev exists, the rc.2 upgrade (including the required `overrideLogicalId()` pinning work) can be tested against real dev infrastructure, not just synthesized templates, before it ever touches prod (today's `492646066653`).

## Done: dev account is live

- Dev account created (`046451669835`), local AWS CLI profile `dev` configured with access to it.
- `cdk bootstrap --profile dev aws://046451669835/us-east-1` run successfully — the dev account now has the CDK toolkit stack, so `cdk deploy`/`agentcore deploy` can target it.

**Dev is now ready for real deploys**, including using it as the safe place to run the `@aws/agentcore-cdk` 1.0-rc.2 upgrade from [agentcore-cdk-1.0-upgrade-scope.md](agentcore-cdk-1.0-upgrade-scope.md) — that upgrade destroys and recreates the Runtime and Memory, which is exactly the kind of change this account split exists for. Bootstrapping prod was not touched or needed again (already bootstrapped from its original setup).

## Done: Gateway access for dev

You control the `claimsagent-claimsgateway` stack (confirmed — it's in the same prod account, `492646066653`), and it authenticates via Cognito (`CUSTOM_JWT`), not IAM — so cross-account access is really "does dev have a trusted Cognito credential," not an IAM trust policy on the Gateway itself. Chose **separate credentials per account** over sharing prod's: a dev-side leak can't be used to call the Gateway as prod.

Done so far, in [cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts):
- Found and fixed two bugs in the existing Gateway wiring: the OAuth token-endpoint URL and the client-secret ARN were both templated off `this.account`, which only happened to work because prod was the only account that existed. Both are now fixed literals pointing at prod (`492646066653`), since that's permanently where the Cognito user pool and secret live regardless of which account is deploying.
- Added a `gatewayClient` stack prop (`{clientId, secretArn}`), sourced per-account from new [agentcore/gateway-credentials.json](../../../agentcore/gateway-credentials.json). Prod's existing client ID/secret are in there now; dev has no entry yet.
- A target account with no entry gets `Annotations.of(this).addError(...)` (not a thrown exception) — this blocks *that stack's* `cdk deploy`/`cdk synth` with a clear message, without crashing the whole multi-stack `App` and taking prod down with it. Verified: prod's template is untouched and dev's carries the expected `aws:cdk:error` annotation.

Done, once `AIOps` was refreshed:
- New Cognito app client `Fionaa-Dev-M2M` (`5qpic8seevqp2g4d550is74rk6`) created in `ClaimsAgent-UserPool` (`us-east-1_QdHqgzqUA`), same `client_credentials`/`agentcore/invoke` config as prod's existing client.
- Its secret stored in a **new secret in the dev account** (`fionaa/agentcore-gateway-client-secret-dev`, `046451669835`) — not prod's Secrets Manager, dev owns its own copy.
- `gateway-credentials.json` now has both accounts' entries; re-synth confirms dev's `Annotations` error is gone and its Runtime carries the correct client ID + its own secret ARN, while still pointing at prod's shared Gateway URL/token endpoint.
- Sanity-checked the credential itself works: minted a real token from the new client via `curl` against the Cognito token endpoint — got back a valid `Bearer` token. Confirms the Cognito side is correctly configured.

**Done.** You ran the `update-gateway` call adding the dev client to `allowedClients` (verified via `get-gateway`: both `qcqj6bgve5u8c2bg1qkiobsps` and `5qpic8seevqp2g4d550is74rk6` are listed; prod's untouched). Verified end-to-end for real: minted a token with dev's credentials and called the Gateway's `tools/list` directly — got back the actual tool catalog (Companies House, etc.) instead of a 401/403. Dev's Fionaa Runtime can now reach every Gateway tool: Companies House, web search, location matching.

**Gateway cross-account access is fully working now.** Dev has its own Cognito client, its own Secrets Manager secret, and confirmed access to the shared Gateway — without touching or depending on prod's credential.

## Still open

- CI/CD promotion pipeline (dev → pre-prod → prod with evaluation gates) — not started; today's eval workflows (`deepeval-ci.yml`, `evals-path2-batch-eval.yml`) still only authenticate into prod's account.
- Pre-prod account — not created; the full blog model has one, current setup is dev/prod only.
- Runtime version aliases (DEV/PREPROD/PROD) — not yet investigated against the CDK constructs available.
