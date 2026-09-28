#!/usr/bin/env node
import { AgentCoreStack, type HarnessConfig } from '../lib/cdk-stack';
import { SecurityFoundationsStack } from '../lib/security-foundations-stack';
import {
  readAgentCoreProject,
  AgentCoreProjectSpecSchema,
  HarnessSpecSchema,
  type AwsDeploymentTarget,
} from '@aws/agentcore-cdk';
import { App, type Environment } from 'aws-cdk-lib';
import * as path from 'path';
import * as fs from 'fs';

function toEnvironment(target: AwsDeploymentTarget): Environment {
  return {
    account: target.account,
    region: target.region,
  };
}

function sanitize(name: string): string {
  return name.replace(/_/g, '-');
}

function toStackName(projectName: string, targetName: string): string {
  return `AgentCore-${sanitize(projectName)}-${sanitize(targetName)}`;
}

async function main() {
  // readAgentCoreProject walks up from cwd (the CLI sets it to agentcore/cdk/) to find
  // agentcore/agentcore.json, and reads aws-targets.json + .cli/deployed-state.json
  // alongside it. Replaces the old ConfigIO class (removed in @aws/agentcore-cdk 1.0).
  const project = readAgentCoreProject();
  const { configRoot, projectRoot, targets, deployedState } = project;

  // agentCoreJson is intentionally untyped by readAgentCoreProject (it doesn't assume a
  // schema version); validate it ourselves against the v2 spec schema.
  const spec = AgentCoreProjectSpecSchema.parse(project.agentCoreJson);

  // The vended CDK project compiles against the published @aws/agentcore-cdk
  // schema type, which may lag the CLI's own AgentCoreProjectSpec (e.g. payments,
  // harnesses, gateway fields). Cast once so those fields are reachable.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const specAny = spec as any;

  // Gateways: AgentCoreApplication now creates these itself from spec.agentCoreGateways
  // (@aws/agentcore-cdk 1.0 removed the separate AgentCoreMcp construct/mcpSpec this app
  // used to build by hand) — nothing to extract here anymore.

  if (targets.length === 0) {
    throw new Error('No deployment targets configured. Please define targets in agentcore/aws-targets.json');
  }

  // Per-account IAM Identity Center (SSO) role ARN for local-dev access to
  // FionaaDataAccessRole (see cdk-stack.ts's localDevRoleArn). Optional file --
  // an account with no entry here just gets no local-dev grant.
  let localDevRolesByAccount: Record<string, string> = {};
  try {
    localDevRolesByAccount = JSON.parse(fs.readFileSync(path.join(configRoot, 'local-dev-roles.json'), 'utf8'));
  } catch {
    // File may not exist -- local-dev access is opt-in per account.
  }

  // Per-account Cognito app client for AgentCore Gateway access (see cdk-stack.ts's
  // gatewayClient). Each account needs its own entry -- cdk-stack.ts throws at synth
  // time for a target account with none, rather than deploying a Runtime that can
  // never call the Gateway.
  let gatewayClientsByAccount: Record<string, { clientId: string; secretArn: string }> = {};
  try {
    gatewayClientsByAccount = JSON.parse(fs.readFileSync(path.join(configRoot, 'gateway-credentials.json'), 'utf8'));
  } catch {
    // File may not exist yet during early bootstrapping.
  }

  // Existing S3 bucket to reuse per account (see cdk-stack.ts's existingApplicationsBucketName).
  // An account with no entry gets its own fresh bucket instead of sharing prod's.
  let applicationsBucketOverridesByAccount: Record<string, string> = {};
  try {
    applicationsBucketOverridesByAccount = JSON.parse(
      fs.readFileSync(path.join(configRoot, 'applications-bucket-overrides.json'), 'utf8')
    );
  } catch {
    // File may not exist -- every account then gets its own bucket.
  }

  // Read non-S3 KB connector-config files and pass their parsed contents to the
  // L3 verbatim. The L3 does not read files; it expects the parsed
  // connectorParameters keyed by the data source's connectorConfigFile path.
  const connectorParametersByFile: Record<string, Record<string, unknown>> = {};
  for (const kb of specAny.knowledgeBases ?? []) {
    for (const ds of kb.dataSources ?? []) {
      if (ds.type !== 'S3' && ds.connectorConfigFile) {
        const abs = path.resolve(projectRoot, ds.connectorConfigFile);
        try {
          connectorParametersByFile[ds.connectorConfigFile] = JSON.parse(fs.readFileSync(abs, 'utf-8'));
        } catch (err) {
          throw new Error(
            `Could not read connector config '${ds.connectorConfigFile}' for knowledge base '${kb.name}' at ${abs}: ${err instanceof Error ? err.message : err}`
          );
        }
      }
    }
  }

  // Harness is preview-gated. The CLI bundle bakes the preview flag at build time and
  // forwards it to this child process via AGENTCORE_PREVIEW (see toolkit-lib/wrapper.ts).
  // This app is built separately and cannot see that build-time define, so it gates on the
  // env var. Absent/anything-but-'1' defaults to off so a stale harnesses[] entry in a
  // non-preview build never synthesizes an AWS::BedrockAgentCore::Harness resource.
  const previewEnabled = process.env.AGENTCORE_PREVIEW === '1';

  const harnessConfigs: HarnessConfig[] = [];
  for (const entry of previewEnabled ? (specAny.harnesses ?? []) : []) {
    const harnessDir = path.resolve(projectRoot, entry.path);
    const harnessPath = path.resolve(harnessDir, 'harness.json');
    try {
      const harnessSpec = HarnessSpecSchema.parse(JSON.parse(fs.readFileSync(harnessPath, 'utf-8')));
      harnessConfigs.push({
        name: entry.name,
        executionRoleArn: harnessSpec.executionRoleArn,
        // @aws/agentcore-cdk 1.0 replaced memoryName (an `existing`-mode memory ref, used to
        // scope IAM against a named sibling memory) with managedMemory: AgentCoreApplication
        // now resolves an `existing` ref's IAM itself from harnessSpec below; this role only
        // needs to know whether the harness's OWN runtime role talks to harness-managed memory.
        managedMemory: harnessSpec.memory?.mode === 'managed',
        containerUri: harnessSpec.containerUri,
        hasDockerfile: !!harnessSpec.dockerfile,
        dockerfile: harnessSpec.dockerfile,
        codeLocation: harnessSpec.dockerfile ? harnessDir : undefined,
        tools: harnessSpec.tools,
        skills: harnessSpec.skills,
        apiKeyArn: harnessSpec.model?.apiKeyArn,
        efsAccessPoints: harnessSpec.efsAccessPoints,
        s3AccessPoints: harnessSpec.s3AccessPoints,
        apiFormat: harnessSpec.model?.apiFormat,
        // Full spec + dir drive the AWS::BedrockAgentCore::Harness CFN resource.
        spec: harnessSpec,
        harnessDir,
      });
    } catch (err) {
      throw new Error(
        `Could not read harness.json for "${entry.name}" at ${harnessPath}: ${err instanceof Error ? err.message : err}`
      );
    }
  }

  const app = new App();

  // Account/region-level security foundations (CloudTrail, Inspector, cost
  // alarms/budgets, Macie) are singletons, not per-AgentCore-target resources --
  // deploy once per distinct (account, region), not once per entry in
  // aws-targets.json. Now that dev (046451669835) and prod (492646066653) are
  // separate accounts, dedupe by `${account}/${region}` so each account gets
  // its own copy instead of only the first target's account getting one.
  const seenAccountRegions = new Set<string>();
  for (const target of targets) {
    const key = `${target.account}/${target.region}`;
    if (seenAccountRegions.has(key)) continue;
    const isFirstAccountRegion = seenAccountRegions.size === 0;
    seenAccountRegions.add(key);
    // The first (account, region) keeps the original unsuffixed stack name --
    // that's prod's already-deployed stack (targets[0] is "default"/492646066653
    // in aws-targets.json), and renaming it would orphan it exactly like the
    // AgentCoreStack risk documented in multi-account-dev-prod.md. Every
    // additional account (dev, and any future ones) gets its own suffixed stack.
    const stackName = isFirstAccountRegion
      ? `AgentCore-${sanitize(spec.name)}-security-foundations`
      : `AgentCore-${sanitize(spec.name)}-security-foundations-${sanitize(target.name)}`;
    new SecurityFoundationsStack(app, stackName, {
      env: toEnvironment(target),
      description: `Account-level security foundations (CloudTrail, Inspector, Bedrock cost/consumption alarms, Macie) for ${spec.name}`,
      notificationEmail: process.env.FIONAA_SECURITY_ALERTS_EMAIL,
    });
  }

  for (const target of targets) {
    const env = toEnvironment(target);
    const stackName = toStackName(spec.name, target.name);

    // Extract credentials from deployed state for this target. readAgentCoreProject
    // validates deployed-state.json against DeployedStateSchema, so this is typed
    // end-to-end now instead of the manual JSON.parse + cast chain ConfigIO needed.
    const credentials = deployedState?.targets[target.name]?.resources?.credentials;

    // Payment credential provider ARNs live in the same credentials map as identity credentials
    const paymentCredentials = credentials;

    const paymentSpec = specAny.payments?.length
      ? specAny.payments.map(
          (p: {
            name: string;
            description?: string;
            authorizerType: 'AWS_IAM' | 'CUSTOM_JWT';
            authorizerConfiguration?: unknown;
            autoPayment?: boolean;
            paymentToolAllowlist?: string[];
            networkPreferences?: string[];
            connectors: { name: string; provider?: string; credentialName: string }[];
          }) => ({
            name: p.name,
            description: p.description,
            authorizerType: p.authorizerType,
            authorizerConfiguration: p.authorizerConfiguration,
            autoPayment: p.autoPayment,
            paymentToolAllowlist: p.paymentToolAllowlist,
            networkPreferences: p.networkPreferences,
            connectors: p.connectors.map(c => {
              const credentialProviderArn = paymentCredentials?.[c.credentialName]?.credentialProviderArn;
              if (!credentialProviderArn) {
                // Fail fast with an actionable message rather than passing an empty
                // ARN that fails opaquely server-side at CreatePaymentConnector.
                throw new Error(
                  `Payment connector "${c.name}" on manager "${p.name}" references credential ` +
                    `"${c.credentialName}", but no deployed credential provider was found for it. ` +
                    `Run \`agentcore deploy\` so the credential provider is created first.`
                );
              }
              return { name: c.name, provider: c.provider, credentialProviderArn };
            }),
          })
        )
      : undefined;

    new AgentCoreStack(app, stackName, {
      spec,
      projectRoot,
      credentials,
      connectorParametersByFile,
      harnesses: harnessConfigs.length > 0 ? harnessConfigs : undefined,
      paymentSpec,
      localDevRoleArn: localDevRolesByAccount[target.account],
      gatewayClient: gatewayClientsByAccount[target.account],
      existingApplicationsBucketName: applicationsBucketOverridesByAccount[target.account],
      env,
      description: `AgentCore stack for ${spec.name} deployed to ${target.name} (${target.region})`,
      tags: {
        'agentcore:project-name': spec.name,
        'agentcore:target-name': target.name,
      },
    });
  }

  app.synth();
}

main().catch((error: unknown) => {
  console.error('AgentCore CDK synthesis failed:', error instanceof Error ? error.message : error);
  process.exit(1);
});
