import {
  AgentCoreApplication,
  AgentCoreMcp,
  AgentCorePaymentManager,
  AgentCorePaymentConnector,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
  type CustomJWTAuthorizerConfig,
  type HarnessDeploymentConfig,
} from '@aws/agentcore-cdk';
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as logs from 'aws-cdk-lib/aws-logs';
import { AwsCustomResource, AwsCustomResourcePolicy, PhysicalResourceId } from 'aws-cdk-lib/custom-resources';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * Harness deployment config: role-scoped fields (for IAM role + container build)
 * plus the full validated spec + its config directory so the L3 construct can
 * synthesize the AWS::BedrockAgentCore::Harness resource.
 */
export type HarnessConfig = HarnessDeploymentConfig;

export interface PaymentConnectorSpec {
  name: string;
  provider: 'CoinbaseCDP' | 'StripePrivy';
  credentialProviderArn: string;
}

export interface PaymentSpec {
  name: string;
  description?: string;
  authorizerType: 'AWS_IAM' | 'CUSTOM_JWT';
  authorizerConfiguration?: { customJWTAuthorizer: CustomJWTAuthorizerConfig };
  autoPayment?: boolean;
  paymentToolAllowlist?: string[];
  networkPreferences?: string[];
  connectors: PaymentConnectorSpec[];
}

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Harness role configurations.
   */
  harnesses?: HarnessConfig[];
  /**
   * Parsed connectorParameters for non-S3 KB data sources, keyed by
   * connectorConfigFile path. Forwarded to AgentCoreApplication.
   */
  connectorParametersByFile?: Record<string, Record<string, unknown>>;
  /**
   * Payment specifications with resolved credential provider ARNs.
   */
  paymentSpec?: PaymentSpec[];
}

function toCdkId(name: string): string {
  return name.replace(/_/g, '');
}

/**
 * Decide whether a deployed runtime should receive payment env vars + IAM grants.
 * Payments today only ships a runtime shim for Python HTTP runtimes; injecting
 * AGENTCORE_PAYMENT_* env vars into TypeScript / MCP / A2A / AGUI runtimes
 * would surface env vars they cannot consume and would dilute least-privilege
 * IAM grants for runtimes that never call ProcessPayment.
 */
function isPaymentEligibleAgent(agent: { entrypoint?: string; protocol?: string }): boolean {
  if (agent.protocol && agent.protocol !== 'HTTP') {
    return false;
  }
  const entrypoint = typeof agent.entrypoint === 'string' ? agent.entrypoint : '';
  const entrypointFile = entrypoint.split(':')[0] ?? '';
  return entrypointFile.endsWith('.py');
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * This is a thin wrapper that instantiates L3 constructs.
 * All resource logic and outputs are contained within the L3 constructs.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials, harnesses, connectorParametersByFile, paymentSpec } = props;

    // Create AgentCoreApplication with all agents and harness roles
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const appProps: Record<string, unknown> = { spec };
    if (harnesses?.length) {
      appProps.harnesses = harnesses;
    }
    if (connectorParametersByFile && Object.keys(connectorParametersByFile).length > 0) {
      appProps.connectorParametersByFile = connectorParametersByFile;
    }
    if (credentials) {
      appProps.credentials = credentials;
    }
    this.application = new AgentCoreApplication(this, 'Application', appProps as any);

    // Create AgentCoreMcp if there are gateways configured
    if (mcpSpec?.agentCoreGateways && mcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }

    // Fionaa per-customer data isolation: FionaaDataAccessRole, tenant KMS key,
    // and the checkpoint memory's IAM/env wiring. See
    // app/fionaa/fionaa_iam_policies.md for the design this implements, and
    // app/fionaa/fionaa_scoped_agent.py for the code that consumes these env vars.
    const fionaaEnv = this.application.environments.get('fionaa');
    const checkpointMemory = this.application.memories.get('FionaaCheckpoint');
    if (fionaaEnv && checkpointMemory) {
      // Reuses the existing bucket already holding real policy-doc content
      // (see stevetesting.ipynb's S3 smoke test) rather than standing up a
      // fresh, empty one.
      const applicationsBucket = s3.Bucket.fromBucketName(this, 'FionaaApplicationsBucket', 'fionaa-6655-assets');

      const tenantKey = new kms.Key(this, 'FionaaTenantKey', {
        description: 'Encrypts customer application data in the Fionaa applications bucket',
        alias: 'fionaa-tenant-key',
        enableKeyRotation: true,
      });

      // Let the AgentCore Memory service encrypt/decrypt checkpoint data at
      // rest with this account's own key rather than a service-owned one
      // (AWS security scan findings AC-07, AG-19).
      tenantKey.grant(
        new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
          conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
        }),
        'kms:Decrypt',
        'kms:GenerateDataKey',
        'kms:DescribeKey'
      );

      // AgentCoreMemory's L1 CfnMemory resource is always synthesized with
      // logical id 'Resource' directly under the memory construct, so it
      // resolves as the construct's default child -- see
      // @aws/agentcore-cdk's AgentCoreMemory.js. The v2 schema/CDK props
      // don't yet expose encryptionKeyArn end-to-end (agentcore.json can
      // only hold a static string, and this key's ARN isn't known until
      // synth), so wire it in directly on the L1 resource instead.
      (checkpointMemory.node.defaultChild as bedrockagentcore.CfnMemory).encryptionKeyArn = tenantKey.keyArn;

      // Bedrock model invocation logging -- CloudFormation has no native
      // resource for PutModelInvocationLoggingConfiguration (it's a
      // per-region account setting, not a discrete resource), so wire it via
      // a custom resource (AWS security scan findings BR-04, AG-07, OW-01/07,
      // FS-43).
      const modelInvocationLogGroupName = '/aws/bedrock/fionaa-model-invocations';
      const modelInvocationLogGroup = new logs.LogGroup(this, 'FionaaModelInvocationLogs', {
        logGroupName: modelInvocationLogGroupName,
        retention: logs.RetentionDays.ONE_YEAR,
        encryptionKey: tenantKey,
        // CDK's default (RETAIN) leaves this behind on any failed
        // deploy/rollback, which then blocks the next attempt with
        // "already exists" -- DESTROY so that self-heals. Trade-off: an
        // intentional stack deletion or resource replacement also deletes
        // these logs rather than keeping them.
        removalPolicy: RemovalPolicy.DESTROY,
      });
      // Built from the (literal, static) log group name rather than
      // modelInvocationLogGroup.logGroupArn -- that Fn::GetAtt would make the
      // key's own policy depend on the log group, while the log group's
      // `encryptionKey: tenantKey` above already makes it depend on the key.
      // Together that's a genuine circular dependency (CloudFormation:
      // "Circular dependency between resources"), not a false positive.
      const modelInvocationLogGroupArn = `arn:aws:logs:${this.region}:${this.account}:log-group:${modelInvocationLogGroupName}`;
      tenantKey.grant(
        new iam.ServicePrincipal('logs.amazonaws.com', {
          conditions: { ArnLike: { 'kms:EncryptionContext:aws:logs:arn': modelInvocationLogGroupArn } },
        }),
        'kms:Encrypt*',
        'kms:Decrypt*',
        'kms:ReEncrypt*',
        'kms:GenerateDataKey*',
        'kms:Describe*'
      );

      // Role Bedrock assumes to deliver invocation logs into the log group above.
      const modelInvocationLoggingRole = new iam.Role(this, 'FionaaModelInvocationLoggingRole', {
        assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
          conditions: {
            StringEquals: { 'aws:SourceAccount': this.account },
            ArnLike: { 'aws:SourceArn': `arn:aws:bedrock:${this.region}:${this.account}:*` },
          },
        }),
      });
      modelInvocationLoggingRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
          resources: [modelInvocationLogGroup.logGroupArn, `${modelInvocationLogGroup.logGroupArn}:*`],
        })
      );

      const modelInvocationLoggingParams = {
        loggingConfig: {
          cloudWatchConfig: {
            logGroupName: modelInvocationLogGroup.logGroupName,
            roleArn: modelInvocationLoggingRole.roleArn,
          },
          textDataDeliveryEnabled: true,
        },
      };
      const modelInvocationLoggingConfig = new AwsCustomResource(this, 'ModelInvocationLoggingConfig', {
        onCreate: {
          service: 'bedrock',
          action: 'PutModelInvocationLoggingConfiguration',
          parameters: modelInvocationLoggingParams,
          physicalResourceId: PhysicalResourceId.of('fionaa-model-invocation-logging'),
        },
        onUpdate: {
          service: 'bedrock',
          action: 'PutModelInvocationLoggingConfiguration',
          parameters: modelInvocationLoggingParams,
          physicalResourceId: PhysicalResourceId.of('fionaa-model-invocation-logging'),
        },
        onDelete: {
          service: 'bedrock',
          action: 'DeleteModelInvocationLoggingConfiguration',
        },
        policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: AwsCustomResourcePolicy.ANY_RESOURCE }),
      });
      modelInvocationLoggingConfig.node.addDependency(modelInvocationLogGroup, modelInvocationLoggingRole);
      // PutModelInvocationLoggingConfiguration hands Bedrock a role ARN to
      // assume -- fromSdkCalls only grants the API action itself, not the
      // separate iam:PassRole permission the caller needs whenever a call
      // passes a role ARN for another service to assume.
      const passRoleGrant = modelInvocationLoggingConfig.grantPrincipal.addToPrincipalPolicy(
        new iam.PolicyStatement({
          actions: ['iam:PassRole'],
          resources: [modelInvocationLoggingRole.roleArn],
        })
      );
      // addToPrincipalPolicy creates a new IAM::Policy resource, but nothing
      // otherwise ties it to this custom resource's invocation order --
      // without this, CloudFormation can (and did) invoke the Lambda before
      // that policy attaches, failing with AccessDenied on iam:PassRole.
      if (passRoleGrant.policyDependable) {
        modelInvocationLoggingConfig.node.addDependency(passRoleGrant.policyDependable);
      }

      // Model-invocation guardrail -- see app/fionaa/model/load.py, which
      // reads FIONAA_GUARDRAIL_ID/FIONAA_GUARDRAIL_VERSION (set below) and
      // attaches them via ChatBedrockConverse's guardrail_config. Deliberately
      // narrow scope: content filters for prompt-attack + baseline harm
      // categories, plus a PII filter limited to identifiers with zero
      // legitimate use in a UK business-loan application (credit card/SSN/NI
      // number/password/AWS keys). Never NAME/ADDRESS/PHONE/EMAIL -- those
      // are expected business content and the actual KYC signal this agent
      // exists to check (see app/fionaa/redaction.py's module docstring for
      // the same reasoning applied to S3 evidence/dashboard redaction).
      // IAM enforcement (denying model calls that omit this guardrail) is a
      // deliberate follow-up, not done here -- see EVALS.md.
      const guardrail = new bedrock.CfnGuardrail(this, 'FionaaGuardrail', {
        name: 'fionaa-guardrail',
        description: 'Prompt-attack/content filtering + non-business-PII masking for the fionaa loan agent',
        blockedInputMessaging: 'This request could not be processed.',
        blockedOutputsMessaging: 'This response could not be processed.',
        // STANDARD content-filter tier requires cross-Region inference.
        crossRegionConfig: {
          guardrailProfileArn: `arn:aws:bedrock:${this.region}:${this.account}:guardrail-profile/us.guardrail.v1:0`,
        },
        contentPolicyConfig: {
          // STANDARD tier adds prompt-leakage detection on top of the
          // PROMPT_ATTACK filter below (see AWS security scan finding BR-16).
          contentFiltersTierConfig: { tierName: 'STANDARD' },
          filtersConfig: [
            // PROMPT_ATTACK is input-only -- outputStrength must be NONE.
            { type: 'PROMPT_ATTACK', inputStrength: 'HIGH', outputStrength: 'NONE' },
            { type: 'HATE', inputStrength: 'MEDIUM', outputStrength: 'MEDIUM' },
            { type: 'INSULTS', inputStrength: 'MEDIUM', outputStrength: 'MEDIUM' },
            { type: 'SEXUAL', inputStrength: 'MEDIUM', outputStrength: 'MEDIUM' },
            { type: 'VIOLENCE', inputStrength: 'MEDIUM', outputStrength: 'MEDIUM' },
            { type: 'MISCONDUCT', inputStrength: 'MEDIUM', outputStrength: 'MEDIUM' },
          ],
        },
        // Guards against hallucinated/ungrounded responses and off-topic
        // answers in RAG-backed nodes (BR-27, OW-04/09). Only takes effect on
        // calls that pass grounding-source content through ApplyGuardrail --
        // harmless no-op otherwise.
        contextualGroundingPolicyConfig: {
          filtersConfig: [
            { type: 'GROUNDING', threshold: 0.7 },
            { type: 'RELEVANCE', threshold: 0.7 },
          ],
        },
        sensitiveInformationPolicyConfig: {
          piiEntitiesConfig: [
            'CREDIT_DEBIT_CARD_NUMBER',
            'CREDIT_DEBIT_CARD_CVV',
            'CREDIT_DEBIT_CARD_EXPIRY',
            'US_SOCIAL_SECURITY_NUMBER',
            'UK_NATIONAL_INSURANCE_NUMBER',
            'PASSWORD',
            'PIN',
            'AWS_ACCESS_KEY',
            'AWS_SECRET_KEY',
          ].map(type => ({ type, action: 'ANONYMIZE', inputEnabled: true, outputEnabled: true })),
        },
      });

      // DRAFT is mutable and must never be used in production -- pin a
      // numbered version (see FIONAA_GUARDRAIL_VERSION below).
      const guardrailVersion = new bedrock.CfnGuardrailVersion(this, 'FionaaGuardrailVersion', {
        guardrailIdentifier: guardrail.attrGuardrailId,
      });

      // Assumed per-invocation by the runtime with a customer_id session tag
      // (see fionaa_scoped_agent.scoped_boto_session). The StringLike + ForAllValues
      // conditions force a non-empty customer_id and forbid smuggling extra tags.
      const dataAccessRole = new iam.Role(this, 'FionaaDataAccessRole', {
        roleName: 'FionaaDataAccessRole',
        description: 'Scopes S3/KMS/Memory access to the customer_id tagged on the assuming session',
        assumedBy: new iam.ArnPrincipal(fionaaEnv.runtime.roleArn).withConditions({
          StringLike: { 'aws:RequestTag/customer_id': '?*' },
          'ForAllValues:StringEquals': { 'aws:TagKeys': ['customer_id', 'application_id'] },
        }),
      });

      // `assumedBy` above only puts sts:AssumeRole in the trust document.
      // sts:TagSession must ALSO be an explicitly allowed action on the trust
      // policy itself (not just the caller's identity policy) or a session-tagged
      // AssumeRole call is denied — see fionaa_iam_policies.md Section 2.
      dataAccessRole.assumeRolePolicy?.addStatements(
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          principals: [new iam.ArnPrincipal(fionaaEnv.runtime.roleArn)],
          actions: ['sts:TagSession'],
          conditions: {
            StringLike: { 'aws:RequestTag/customer_id': '?*' },
            'ForAllValues:StringEquals': { 'aws:TagKeys': ['customer_id', 'application_id'] },
          },
        })
      );

      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'ObjectsUnderOwnPrefixOnly',
          actions: ['s3:GetObject', 's3:PutObject'],
          resources: [`${applicationsBucket.bucketArn}/\${aws:PrincipalTag/customer_id}/*`],
        })
      );
      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'ListOwnPrefixOnly',
          actions: ['s3:ListBucket'],
          resources: [applicationsBucket.bucketArn],
          conditions: { StringLike: { 's3:prefix': '${aws:PrincipalTag/customer_id}/*' } },
        })
      );
      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'SharedPolicyDocsReadOnly',
          actions: ['s3:GetObject'],
          resources: [`${applicationsBucket.bucketArn}/loan_policy_documents/*`],
        })
      );
      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'EncryptDecryptWithTenantKey',
          actions: ['kms:Decrypt', 'kms:GenerateDataKey'],
          resources: [tenantKey.keyArn],
          conditions: {
            StringEquals: { 'kms:EncryptionContext:customer_id': '${aws:PrincipalTag/customer_id}' },
          },
        })
      );
      // Same actions AgentCoreMemory.grant() would add for a runtime — but this
      // role, not the ambient execution role, is what build_checkpointer()
      // actually uses (see its docstring on why: app-enforced, not IAM-scoped,
      // isolation for checkpoint data).
      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'CheckpointMemoryAccess',
          actions: [
            'bedrock-agentcore:GetEvent',
            'bedrock-agentcore:GetMemory',
            'bedrock-agentcore:GetMemoryRecord',
            'bedrock-agentcore:ListActors',
            'bedrock-agentcore:ListEvents',
            'bedrock-agentcore:ListSessions',
            'bedrock-agentcore:CreateEvent',
            'bedrock-agentcore:DeleteEvent',
            'bedrock-agentcore:DeleteMemoryRecord',
          ],
          resources: [checkpointMemory.memoryArn],
        })
      );
      // AgentCore Memory's CreateEvent (and other data-plane calls) encrypt/
      // decrypt against the memory's customer-managed key via a Forward
      // Access Session (FAS) -- the KMS call runs under the *caller's own*
      // IAM identity forwarded through the service, not the service's own
      // identity. That means the bare bedrock-agentcore.amazonaws.com
      // service-principal grant on tenantKey below (added for the S3/logs
      // side of this key) is the wrong mechanism for this call and doesn't
      // authorize it -- confirmed live: CreateEvent failed with
      // "AccessDeniedException: Unable to perform KMS operations" even
      // though that service-principal statement was present and correct
      // for its own purpose. Per AWS's own guidance ("Encrypt your Amazon
      // Bedrock AgentCore Memory"), the fix is to grant the actual calling
      // principal (dataAccessRole, which is what build_checkpointer() uses)
      // these KMS actions directly, scoped with kms:ViaService so the grant
      // can't be used outside an AgentCore Memory call.
      dataAccessRole.addToPolicy(
        new iam.PolicyStatement({
          sid: 'CheckpointMemoryKmsViaService',
          actions: [
            'kms:CreateGrant',
            'kms:Decrypt',
            'kms:DescribeKey',
            'kms:GenerateDataKey',
            'kms:GenerateDataKeyWithoutPlaintext',
            'kms:ReEncrypt*',
          ],
          resources: [tenantKey.keyArn],
          conditions: {
            StringEquals: { 'kms:ViaService': `bedrock-agentcore.${this.region}.amazonaws.com` },
          },
        })
      );

      // Runtime execution role's only route to customer data: assume + tag-session.
      fionaaEnv.runtime.addToPolicy(
        new iam.PolicyStatement({
          sid: 'AssumeScopedDataRole',
          actions: ['sts:AssumeRole', 'sts:TagSession'],
          resources: [dataAccessRole.roleArn],
        })
      );

      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_APPLICATIONS_BUCKET', applicationsBucket.bucketName);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_POLICY_DOCS_BUCKET', applicationsBucket.bucketName);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_KMS_KEY_ARN', tenantKey.keyArn);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_DATA_ACCESS_ROLE_ARN', dataAccessRole.roleArn);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_CHECKPOINT_MEMORY_ID', checkpointMemory.memoryId);

      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_GUARDRAIL_ID', guardrail.attrGuardrailId);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_GUARDRAIL_VERSION', guardrailVersion.attrVersion);

      // Automated Reasoning guardrail bindings (per-loan-type guardrail
      // id/version/policy-digest, see policy_consistency.py) used to live
      // as the raw JSON in agentcore.json's envVars -- moved to SSM
      // Parameter Store because AgentCore Runtime now rejects any update
      // once total environment-variable payload exceeds 1024 bytes, and
      // this JSON alone was ~700+ of the runtime's 1762 bytes. Not secret
      // data (guardrail ids + SHA256 digests of policy documents, no
      // credentials), so Parameter Store rather than Secrets Manager.
      const arGuardrailBindings = {
        'secured-business-loans': {
          guardrail_id: 'bwcgqb1tsa07',
          guardrail_version: '1',
          policy_sha256: '1f2eed9854fd54bae05b1bdeadb9b1f4bc967cf8e51f183a811cc152d6e4eea6',
        },
        'unsecured-business-loans': {
          guardrail_id: '5h5yunnkf895',
          guardrail_version: '1',
          policy_sha256: '59192d6b3c4bb7a00ce0a4b26deb12633fa8970c4bc761ff2d1023962840c62c',
        },
        'revolving-credit-facility': {
          guardrail_id: 'cdue92mr0ap4',
          guardrail_version: '1',
          policy_sha256: '8ffafa54cbf8f826f4742477eee12561f34e6c30bbe7386e0315fe938910337e',
        },
        'invoice-discounting': {
          guardrail_id: '7kbeqj127jvv',
          guardrail_version: '1',
          policy_sha256: 'b1a79c82f543519da507b71aef7981671acea6dac2c57c0acfdbd40bdea26d63',
        },
        'invoice-factoring': {
          guardrail_id: 'me18qejox6li',
          guardrail_version: '1',
          policy_sha256: 'bbe4bc64b1596806088fdafb12c07c974db53cf43f89be344d738b29829cdeee',
        },
      };
      const arGuardrailBindingsParam = new ssm.StringParameter(this, 'FionaaArGuardrailBindings', {
        parameterName: '/fionaa/ar-guardrails',
        stringValue: JSON.stringify(arGuardrailBindings),
      });
      arGuardrailBindingsParam.grantRead(fionaaEnv.runtime.role);
      fionaaEnv.runtime.addEnvironmentVariable('FIONAA_AR_GUARDRAILS_PARAM', arGuardrailBindingsParam.parameterName);
      fionaaEnv.runtime.addToPolicy(
        new iam.PolicyStatement({
          sid: 'ApplyFionaaGuardrail',
          actions: ['bedrock:ApplyGuardrail'],
          resources: [guardrail.attrGuardrailArn],
        })
      );

      // AgentCore Gateway — reused from the existing ClaimsAgent stack rather
      // than standing up a new one. The client secret is a real credential:
      // it's resolved from Secrets Manager at call time (see
      // fionaa_scoped_agent._gateway_client_secret), never a plain env var.
      const gatewaySecret = secretsmanager.Secret.fromSecretCompleteArn(
        this,
        'FionaaGatewayClientSecret',
        'arn:aws:secretsmanager:us-east-1:492646066653:secret:fionaa/agentcore-gateway-client-secret-ZuXvPz'
      );
      gatewaySecret.grantRead(fionaaEnv.runtime.role);

      fionaaEnv.runtime.addEnvironmentVariable(
        'AGENTCORE_GATEWAY_URL',
        'https://claimsagent-claimsgateway-glrrsnaalt.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp'
      );
      fionaaEnv.runtime.addEnvironmentVariable(
        'AGENTCORE_GATEWAY_TOKEN_ENDPOINT',
        'https://claims-agent-492646066653.auth.us-east-1.amazoncognito.com/oauth2/token'
      );
      fionaaEnv.runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_OAUTH_SCOPES', 'agentcore/invoke');
      fionaaEnv.runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_CLIENT_ID', 'qcqj6bgve5u8c2bg1qkiobsps');
      fionaaEnv.runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_CLIENT_SECRET_ARN', gatewaySecret.secretArn);

      // AgentCore Gateway Lambda target: "is this the same area?" tool, backing
      // the check_companies_house node's address-equivalence check (Ruislip vs
      // Greater London, etc — see
      // app/fionaa/tests/test_live_companies_house.py, case
      // id="goodai-consulting-london-vs-ruislip"). This Lambda is ours to
      // deploy via CDK, but claimsagent-claimsgateway itself is owned by a
      // different, out-of-repo stack — attaching this function as an MCP
      // Gateway Target, and granting that Gateway's execution role
      // lambda:InvokeFunction on it, is a manual step. See
      // lambda/geo_area_match/README.md for the exact commands.
      const geoAreaMatchFn = new lambda.Function(this, 'GeoAreaMatchFunction', {
        functionName: 'fionaa-geo-area-match',
        description:
          'AgentCore Gateway target: resolves whether two place names are the same administrative area (e.g. Ruislip vs Greater London)',
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'handler.lambda_handler',
        // __dirname differs between `ts-node` (lib/) and the compiled `dist/lib/`
        // this app actually runs from (see cdk.json's `app` entrypoint), so anchor
        // on process.cwd() instead — bin/cdk.ts's own config-root resolution
        // already relies on the CLI setting cwd to agentcore/cdk/.
        code: lambda.Code.fromAsset(path.resolve(process.cwd(), '../lambda/geo_area_match')),
        timeout: Duration.seconds(10),
        memorySize: 128,
        // FS-09 (cap concurrent executions so a runaway agent loop can't
        // exhaust account-wide Lambda concurrency) is currently NOT
        // enforced here: this account's total Lambda concurrency limit is
        // only 10 (AWS's default floor), and AWS requires >=10 to stay
        // unreserved at all times, so any reservedConcurrentExecutions
        // value here is rejected by the service. Request a Service Quotas
        // increase for Lambda concurrent executions, then reinstate
        // reservedConcurrentExecutions (was 5).
      });

      geoAreaMatchFn.addToRolePolicy(
        new iam.PolicyStatement({
          sid: 'LocationPlacesSearch',
          actions: ['geo-places:SearchText'],
          // geo-places (the newer Amazon Location Service Places API) has no
          // resource-level ARNs to scope this to.
          resources: ['*'],
        })
      );

      new CfnOutput(this, 'GeoAreaMatchFunctionArn', {
        description:
          'Lambda ARN to register as an MCP Gateway Target on claimsagent-claimsgateway (manual step, see lambda/geo_area_match/README.md)',
        value: geoAreaMatchFn.functionArn,
      });

      // NOTE: platformVersion V2 (elastic, faster cold starts -- see
      // https://aws.amazon.com/blogs/machine-learning/the-new-agentcore-runtime-elastic-optimized-and-consistently-fast-starts/)
      // was attempted here via a direct UpdateAgentRuntime custom resource
      // (CloudFormation doesn't expose this field yet), but V2 caps total
      // environment-variable payload at 1024 bytes and this runtime's env
      // vars total ~1762 bytes (mostly FIONAA_AR_GUARDRAILS). The update was
      // reverted rather than shrinking env vars blindly -- revisit once
      // FIONAA_AR_GUARDRAILS (or similar) moves out of environment
      // variables (e.g. into Secrets Manager, fetched at startup).
    }

    // Create payment infrastructure via CFN constructs
    if (paymentSpec && paymentSpec.length > 0) {
      for (const payment of paymentSpec) {
        const mgrId = toCdkId(payment.name);
        const manager = new AgentCorePaymentManager(this, `Payment${mgrId}`, {
          projectName: spec.name,
          name: payment.name,
          authorizerType: payment.authorizerType,
          description: payment.description,
          authorizerConfiguration: payment.authorizerConfiguration,
          tags: spec.tags,
        });

        const prefix = `AGENTCORE_PAYMENT_${payment.name.toUpperCase().replace(/-/g, '_')}`;

        // Wire env vars from construct output tokens into eligible agent environments only.
        // See isPaymentEligibleAgent — non-Python or non-HTTP runtimes have no shim that
        // can consume these env vars, and giving them sts:AssumeRole on the
        // ProcessPaymentRole would broaden the privilege surface unnecessarily.
        for (const env of this.application.environments.values()) {
          if (!isPaymentEligibleAgent(env.agent)) {
            continue;
          }
          env.runtime.addEnvironmentVariable(`${prefix}_MANAGER_ARN`, manager.paymentManagerArn);
          env.runtime.addEnvironmentVariable(`${prefix}_PROCESS_PAYMENT_ROLE_ARN`, manager.processPaymentRoleArn);

          // Grant runtime execution role permission to assume the ProcessPaymentRole.
          // The ProcessPaymentRole's trust policy allows AccountRootPrincipal, but the
          // caller still needs sts:AssumeRole on its own role to perform the assumption.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: ['sts:AssumeRole'],
              resources: [manager.processPaymentRoleArn],
            })
          );

          // Grant payment data-plane actions directly to the runtime role.
          //
          // NOTE: This deviates from the canonical role model in the AgentCore Payments
          // beta guide, which assigns Get/List/Create instrument+session actions to a
          // separate ManagementRole and limits the agent's role to ProcessPayment only.
          // The current SDK plugin (AgentCorePaymentsPlugin.generate_payment_header)
          // calls GetPaymentInstrument internally during the 402 auto-pay path, so the
          // runtime role needs read access. CreatePaymentSession is included so
          // `agentcore invoke --auto-session` works without a separate ManagementRole
          // call. Tighten this if the SDK is updated to accept pre-fetched instrument
          // details and split create-session into a backend-only flow.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: [
                'bedrock-agentcore:GetPaymentInstrument',
                'bedrock-agentcore:ListPaymentInstruments',
                'bedrock-agentcore:GetPaymentInstrumentBalance',
                'bedrock-agentcore:GetPaymentSession',
                'bedrock-agentcore:ListPaymentSessions',
                'bedrock-agentcore:CreatePaymentSession',
                'bedrock-agentcore:ProcessPayment',
              ],
              resources: [manager.paymentManagerArn, `${manager.paymentManagerArn}/*`],
            })
          );

          if (payment.autoPayment !== undefined) {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTO_PAYMENT`, String(payment.autoPayment));
          }
          if (payment.paymentToolAllowlist) {
            env.runtime.addEnvironmentVariable(`${prefix}_TOOL_ALLOWLIST`, payment.paymentToolAllowlist.join(','));
          }
          if (payment.networkPreferences) {
            env.runtime.addEnvironmentVariable(`${prefix}_NETWORK_PREFERENCES`, payment.networkPreferences.join(','));
          }
          if (payment.authorizerType === 'CUSTOM_JWT') {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTH_MODE`, 'bearer');
          }
        }

        // Create connectors for this manager
        for (const connector of payment.connectors) {
          const connId = toCdkId(connector.name);
          const conn = new AgentCorePaymentConnector(this, `Payment${mgrId}${connId}`, {
            projectName: spec.name,
            paymentManager: manager,
            connectorName: connector.name,
            connectorType: connector.provider,
            credentialProviderArn: connector.credentialProviderArn,
          });

          // Wire first connector's ID as env var (eligible agents only)
          if (connector === payment.connectors[0]) {
            for (const env of this.application.environments.values()) {
              if (!isPaymentEligibleAgent(env.agent)) continue;
              env.runtime.addEnvironmentVariable(`${prefix}_CONNECTOR_ID`, conn.paymentConnectorId);
            }
          }

          new CfnOutput(this, `Payment${mgrId}${connId}ConnectorId`, {
            value: conn.paymentConnectorId,
          });
        }

        // CFN Outputs for post-deploy state parsing
        new CfnOutput(this, `Payment${mgrId}ManagerArn`, {
          value: manager.paymentManagerArn,
        });
        new CfnOutput(this, `Payment${mgrId}ManagerId`, {
          value: manager.paymentManagerId,
        });
        new CfnOutput(this, `Payment${mgrId}ProcessPaymentRoleArn`, {
          value: manager.processPaymentRoleArn,
        });
        new CfnOutput(this, `Payment${mgrId}ResourceRetrievalRoleArn`, {
          value: manager.resourceRetrievalRoleArn,
        });
      }
    }

    // Stack-level output
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }
}
