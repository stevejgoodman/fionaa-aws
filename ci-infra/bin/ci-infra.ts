#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { GitHubOidcStack } from '../lib/github-oidc-stack';
import { Path2BatchEvalStack } from '../lib/path2-batch-eval-stack';

const app = new App();

const account = '492646066653';
const region = 'us-east-1'; // matches metrics.py's _JUDGE_MODEL region and model/load.py's inference profile

new GitHubOidcStack(app, 'FionaaGitHubOidcCi', {
  env: { account, region },
  githubRepo: 'stevejgoodman/fionaa-aws',
  // From: gh api repos/stevejgoodman/fionaa-aws --jq '{owner_login: .owner.login, owner_id: .owner.id, repo_name: .name, repo_id: .id}'
  githubOwnerLogin: 'stevejgoodman',
  githubOwnerId: 7223202,
  githubRepoName: 'fionaa-aws',
  githubRepoId: 1307842866,
  // Same cross-region inference profile model/load.py's MODEL_ID and
  // metrics.py's _JUDGE_MODEL both resolve to -- see fionaa/app/fionaa/model/load.py.
  bedrockInferenceProfileArn: `arn:aws:bedrock:${region}:${account}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0`,
  // Foundation-model ARNs are AWS-owned (no account ID); a "us." cross-region
  // profile can route to any of these constituent regions.
  bedrockFoundationModelArns: [
    'arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0',
    'arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0',
    'arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0',
  ],
  gatewayClientSecretArn:
    'arn:aws:secretsmanager:us-east-1:492646066653:secret:fionaa/agentcore-gateway-client-secret-ZuXvPz',
});

new Path2BatchEvalStack(app, 'FionaaEvalsPath2Ci', {
  env: { account, region },
  githubRepo: 'stevejgoodman/fionaa-aws',
  githubOwnerLogin: 'stevejgoodman',
  githubOwnerId: 7223202,
  githubRepoName: 'fionaa-aws',
  githubRepoId: 1307842866,
  deployBranch: 'master',
  // fionaa/agentcore/cdk/lib/cdk-stack.ts: FionaaApplicationsBucket / FionaaTenantKey.
  applicationsBucketName: 'fionaa-6655-assets',
  applicationsKmsKeyArn: `arn:aws:kms:${region}:${account}:key/ad0bef90-102a-4cd0-8dcf-9d6744c52743`,
  // sha256("fionaa-eval-ci@example.com") -- see EVALS.md Path 2 plan, work item 2.
  evalCustomerId: '17deb75df387eafcea144caa24f896e85216c2622721c6c33c6c1b8cd73eae18',
  // ClaimsAgent-UserPool (agentcore.json's customJwtAuthorizer.discoveryUrl).
  cognitoUserPoolArn: `arn:aws:cognito-idp:${region}:${account}:userpool/us-east-1_QdHqgzqUA`,
  evalCredentialsSecretArn:
    'arn:aws:secretsmanager:us-east-1:492646066653:secret:fionaa/eval-harness-cognito-credentials-8NACSV',
  // fionaa/agentcore/.cli/deployed-state.json's evaluators -- update if more are added (work item 7).
  evaluatorArns: [
    'arn:aws:bedrock-agentcore:us-east-1:492646066653:evaluator/fionaa_fionaa_injection_resistance-d95t9A3x47',
    'arn:aws:bedrock-agentcore:us-east-1:492646066653:evaluator/fionaa_fionaa_companies_house_correctness-GnF38v4Rr7',
  ],
});
