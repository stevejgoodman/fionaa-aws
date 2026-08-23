import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface Path2BatchEvalStackProps extends StackProps {
  /** e.g. "stevejgoodman/fionaa-aws" -- used for the plain-name sub form. */
  readonly githubRepo: string;
  /** ID-qualified sub-claim components -- see GitHubOidcStack for how these were derived. */
  readonly githubOwnerLogin: string;
  readonly githubOwnerId: number;
  readonly githubRepoName: string;
  readonly githubRepoId: number;
  /** Branch this role's trust is scoped to (push + workflow_dispatch), e.g. "master". */
  readonly deployBranch: string;
  /** fionaa-6655-assets -- see fionaa/agentcore/cdk/lib/cdk-stack.ts. */
  readonly applicationsBucketName: string;
  /** FionaaTenantKey's ARN (same cdk-stack.ts construct). */
  readonly applicationsKmsKeyArn: string;
  /**
   * customer_id = sha256(email) for the disposable eval identity
   * (fionaa-eval-ci@example.com) -- see EVALS.md's Path 2 plan, work item 2.
   * This is the ONLY S3 prefix / KMS EncryptionContext this role may write.
   */
  readonly evalCustomerId: string;
  /** ClaimsAgent-UserPool ARN -- ONLY for AdminInitiateAuth against the one eval user. */
  readonly cognitoUserPoolArn: string;
  /** fionaa/eval-harness-cognito-credentials secret ARN (work item 2). */
  readonly evalCredentialsSecretArn: string;
  /** Evaluator ARNs this role may GetEvaluator on (fionaa_injection_resistance, fionaa_companies_house_correctness, ...). */
  readonly evaluatorArns: string[];
}

/**
 * GitHub Actions -> AWS OIDC federation for Path 2 (post-deploy
 * batch-evaluation gate against the real deployed Runtime -- see
 * ../../fionaa/agentcore/EVALS.md's "Path 2 plan" section, work item 3).
 *
 * Deliberately its own stack, same reasoning as GitHubOidcStack: kept out of
 * fionaa/agentcore/cdk/ (the AgentCore CLI's own vended stack) and out of
 * GitHubOidcStack itself -- Path 1's role is PR-time/read-mostly and scoped
 * to pull_request; this role is push-to-master/workflow_dispatch and needs
 * write access (staging eval data, starting batch evaluations), a
 * meaningfully different trust and blast radius that's clearer to reason
 * about as a separate role/stack than as an expansion of Path 1's.
 *
 * Deliberately does NOT include `bedrock-agentcore:InvokeAgentRuntime`.
 * fionaa's Runtime uses a CUSTOM_JWT authorizer (see agentcore.json), so
 * invocation happens over a plain HTTPS POST to /invocations with
 * `Authorization: Bearer <cognito id token>` -- not SigV4 -- per
 * agentcore_deploy_gotchas #8/#12. There is no IAM permission check on that
 * invocation path at all; only the JWT matters.
 *
 * Also deliberately does NOT grant anything needed for `agentcore deploy`
 * itself (CloudFormation/IAM/broader Bedrock AgentCore create/update
 * actions) -- that is a much larger permission surface and a separate
 * design decision, to be scoped explicitly when the CI workflow (work item
 * 8) is built, not folded into this narrowly-scoped batch-eval role.
 */
export class Path2BatchEvalStack extends Stack {
  readonly ciRole: iam.Role;

  constructor(scope: Construct, id: string, props: Path2BatchEvalStackProps) {
    super(scope, id, props);

    // Same OIDC provider GitHubOidcStack creates (AWS caps one per issuer
    // URL per account) -- import by its deterministic ARN rather than
    // creating a second one, which CloudFormation would reject.
    const providerArn = `arn:aws:iam::${this.account}:oidc-provider/token.actions.githubusercontent.com`;
    const provider = iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(this, 'ImportedGitHubOidcProvider', providerArn);

    // Trust scoped to push + workflow_dispatch runs against `deployBranch`
    // only, on this exact repo. GitHub's ref-triggered sub claim is
    // documented as "repo:OWNER/REPO:ref:refs/heads/BRANCH" -- but
    // GitHubOidcStack's pull_request trust condition found the *actual*
    // issued claim to be the ID-qualified form
    // ("repo:login@ownerId/repo@repoId:...") instead, confirmed only by a
    // real run with a temporary debug step. Matching both forms here on the
    // same suspicion; this has NOT yet been empirically confirmed for the
    // ref-triggered case specifically -- verify the same way when work item
    // 8's workflow first runs, and correct this condition if the real claim
    // differs (see GitHubOidcStack's comment for the debugging approach).
    const refSubject = `refs/heads/${props.deployBranch}`;
    const principal = new iam.WebIdentityPrincipal(provider.openIdConnectProviderArn, {
      StringEquals: {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
      },
      StringLike: {
        'token.actions.githubusercontent.com:sub': [
          `repo:${props.githubRepo}:ref:${refSubject}`,
          `repo:${props.githubOwnerLogin}@${props.githubOwnerId}/${props.githubRepoName}@${props.githubRepoId}:ref:${refSubject}`,
        ],
      },
    });

    this.ciRole = new iam.Role(this, 'Path2BatchEvalCiRole', {
      roleName: 'fionaa-evals-path2-ci',
      assumedBy: principal,
      description:
        'Assumed by GitHub Actions (OIDC) to stage disposable eval data, invoke the real fionaa Runtime, and run batch-evaluation against the resulting sessions -- see EVALS.md Path 2 plan.',
    });

    // Stage input/application.json under the one disposable eval prefix.
    // FionaaDataAccessRole's trust policy only trusts the Runtime's
    // execution role (fionaa_iam_policies.md Section 2) -- this role can't
    // assume it. Writing directly with this narrow grant + the same
    // SSEKMSEncryptionContext the app would set is the documented
    // workaround (agentcore_deploy_gotchas #10).
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'StageDisposableEvalApplicationData',
        actions: ['s3:PutObject'],
        resources: [`arn:aws:s3:::${props.applicationsBucketName}/${props.evalCustomerId}/*`],
      }),
    );

    // S3 SSE-KMS on write calls kms:GenerateDataKey (not kms:Encrypt) --
    // matches the action FionaaDataAccessRole's own EncryptDecryptWithTenantKey
    // statement grants (cdk-stack.ts). EncryptionContext condition is fixed
    // to the one disposable customer_id, since this role writes with its own
    // credentials rather than a session-tagged principal.
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'EncryptEvalDataWithTenantKey',
        actions: ['kms:GenerateDataKey'],
        resources: [props.applicationsKmsKeyArn],
        conditions: {
          StringEquals: { 'kms:EncryptionContext:customer_id': props.evalCustomerId },
        },
      }),
    );

    // Real JWT for invocation means AdminInitiateAuth against the one
    // disposable Cognito user (agentcore_deploy_gotchas #12 -- ID token, not
    // access token). Cognito's AdminInitiateAuth doesn't support scoping
    // below the user-pool ARN, so the user pool itself is the finest grain
    // available; the one throwaway user in it is the actual scope limiter.
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AuthenticateAsDisposableEvalUser',
        actions: ['cognito-idp:AdminInitiateAuth'],
        resources: [props.cognitoUserPoolArn],
      }),
    );

    // That user's password (work item 2) -- never written to the repo.
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadEvalUserCredentials',
        actions: ['secretsmanager:GetSecretValue'],
        resources: [props.evalCredentialsSecretArn],
      }),
    );

    // Run batch-evaluation against the sessions just created. These are
    // bedrock-agentcore (data-plane) actions, confirmed against AWS's own
    // service-authorization reference -- StartBatchEvaluation has no
    // resource type in that reference (the batch evaluation's ID doesn't
    // exist until Start creates it, so it can't be resource-scoped ahead of
    // time); Get/List are likewise resource-type-free. Stop *does* document
    // a batch-evaluate resource type, scoped to this account/region.
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RunBatchEvaluation',
        actions: ['bedrock-agentcore:StartBatchEvaluation', 'bedrock-agentcore:GetBatchEvaluation', 'bedrock-agentcore:ListBatchEvaluations'],
        resources: ['*'],
      }),
    );
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'StopBatchEvaluation',
        actions: ['bedrock-agentcore:StopBatchEvaluation'],
        resources: [`arn:aws:bedrock-agentcore:${this.region}:${this.account}:batch-evaluate/*`],
      }),
    );

    // Resolve `--evaluator <name>` to an evaluator resource. This is a
    // control-plane action (bedrock-agentcore-control, per the "Evaluations"
    // row of AWS's AgentCore service table) -- distinct from the
    // batch-evaluation actions above, even though ARNs for both stay under
    // the bedrock-agentcore: (not -control) resource namespace.
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ResolveEvaluators',
        actions: ['bedrock-agentcore-control:GetEvaluator'],
        resources: props.evaluatorArns,
      }),
    );
    this.ciRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ListEvaluators',
        actions: ['bedrock-agentcore-control:ListEvaluators'],
        resources: ['*'],
      }),
    );

    new CfnOutput(this, 'Path2CiRoleArn', {
      value: this.ciRole.roleArn,
      description: 'Set as the AWS_EVALS_PATH2_CI_ROLE_ARN repo variable for the Path 2 GitHub Actions workflow (work item 8).',
    });
  }
}
