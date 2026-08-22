#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { GitHubOidcStack } from '../lib/github-oidc-stack';

const app = new App();

const account = '492646066653';
const region = 'us-east-1'; // matches metrics.py's _JUDGE_MODEL region and model/load.py's inference profile

new GitHubOidcStack(app, 'FionaaGitHubOidcCi', {
  env: { account, region },
  githubRepo: 'stevejgoodman/fionaa-aws',
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
