import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface GitHubOidcStackProps extends StackProps {
  /** e.g. "stevejgoodman/fionaa-aws" -- used for the plain-name sub form. */
  readonly githubRepo: string;
  /**
   * ID-qualified sub-claim components (see the trust condition below for
   * why both forms are matched). Fetch via:
   *   gh api repos/OWNER/REPO --jq '{owner_login: .owner.login, owner_id: .owner.id, repo_name: .name, repo_id: .id}'
   */
  readonly githubOwnerLogin: string;
  readonly githubOwnerId: number;
  readonly githubRepoName: string;
  readonly githubRepoId: number;
  /** ARN of the AmazonBedrockModel judge / graph.py model's cross-region inference profile. */
  readonly bedrockInferenceProfileArn: string;
  /** Underlying foundation-model ARN(s) the inference profile can route to (region-wildcarded). */
  readonly bedrockFoundationModelArns: string[];
  /** Gateway OAuth client secret this CI role needs to read (AGENTCORE_GATEWAY_CLIENT_SECRET_ARN). */
  readonly gatewayClientSecretArn: string;
}

/**
 * GitHub Actions -> AWS OIDC federation for deepeval_evals/ CI (Path 1 in
 * ../fionaa/agentcore/deepeval_evals/README.md's CI plan).
 *
 * Deliberately kept as its own small stack, separate from
 * fionaa/agentcore/cdk/ -- that app is the AgentCore CLI's own
 * vended/regenerated stack (driven by agentcore.json via `agentcore
 * deploy`), not a place to hand-add unrelated CI infrastructure.
 *
 * Scope is intentionally narrow: this role can invoke Bedrock (to run the
 * eval harness's model calls and its GEval judge, both pinned to the same
 * model -- see model/load.py and metrics.py's _JUDGE_MODEL) and read the
 * one Gateway OAuth client secret gateway.py resolves at runtime. It has
 * no S3, no write access, and no access to the deployed AgentCore Runtime
 * itself -- deepeval_evals/ calls graph.py's node functions directly with
 * FakeStore/FakePolicyDocs (see test_policy_check.py), it never touches
 * the real applications bucket.
 */
export class GitHubOidcStack extends Stack {
  readonly ciRole: iam.Role;

  constructor(scope: Construct, id: string, props: GitHubOidcStackProps) {
    super(scope, id, props);

    // A GitHub OIDC provider is a single account-wide resource (AWS caps
    // one per issuer URL) -- reuse it if a prior stack/session already
    // created one, otherwise create it here.
    const provider = new iam.OpenIdConnectProvider(this, 'GitHubActionsOidcProvider', {
      url: 'https://token.actions.githubusercontent.com',
      clientIds: ['sts.amazonaws.com'],
    });

    // Trust scoped to pull_request runs on this exact repo only. GitHub's
    // docs describe the `sub` claim for a PR-triggered run as literally
    // "repo:OWNER/REPO:pull_request" -- but a real run against this repo
    // (2026-08-22) actually issued
    // "repo:stevejgoodman@7223202/fionaa-aws@1307842866:pull_request", an
    // ID-qualified format (login@ownerId/repo@repoId), not the plain-name
    // one the docs show. Matching both forms via StringLike's OR-of-values
    // rather than guessing which one is current -- confirmed empirically
    // via a temporary debug step (actions/github-script decoding
    // core.getIDToken()'s JWT payload) after the plain-name-only version of
    // this condition produced "Not authorized to perform
    // sts:AssumeRoleWithWebIdentity" on every run.
    const principal = new iam.WebIdentityPrincipal(provider.openIdConnectProviderArn, {
      StringEquals: {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
      },
      StringLike: {
        'token.actions.githubusercontent.com:sub': [
          `repo:${props.githubRepo}:pull_request`,
          `repo:${props.githubOwnerLogin}@${props.githubOwnerId}/${props.githubRepoName}@${props.githubRepoId}:pull_request`,
        ],
      },
    });

    this.ciRole = new iam.Role(this, 'DeepEvalCiRole', {
      roleName: 'fionaa-deepeval-ci',
      assumedBy: principal,
      description:
        'Assumed by GitHub Actions (OIDC) to run deepeval_evals/ against real Bedrock/Gateway on pull requests.',
      maxSessionDuration: Duration.hours(1),
    });

    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeBedrockModel',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Converse', 'bedrock:ConverseStream'],
        resources: [props.bedrockInferenceProfileArn, ...props.bedrockFoundationModelArns],
      }),
    );

    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadGatewayOAuthClientSecret',
        actions: ['secretsmanager:GetSecretValue'],
        resources: [props.gatewayClientSecretArn],
      }),
    );

    new CfnOutput(this, 'CiRoleArn', {
      value: this.ciRole.roleArn,
      description: 'Set as the AWS_CI_ROLE_ARN repo variable for the deepeval-ci GitHub Actions workflow.',
    });
  }
}
