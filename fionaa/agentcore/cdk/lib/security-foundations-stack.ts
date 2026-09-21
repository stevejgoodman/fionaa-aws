import { CfnOutput, Duration, Stack, type StackProps } from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as ce from 'aws-cdk-lib/aws-ce';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as macie from 'aws-cdk-lib/aws-macie';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { AwsCustomResource, AwsCustomResourcePolicy, PhysicalResourceId } from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';

export interface SecurityFoundationsStackProps extends StackProps {
  /**
   * Email address to notify for Bedrock consumption alarms and cost anomalies.
   * If omitted, the SNS topic is still created (subscribe to it manually).
   */
  notificationEmail?: string;
  /**
   * Monthly budget limit (USD) for Bedrock + SageMaker spend.
   * @default 100
   */
  monthlyBudgetLimitUsd?: number;
}

/**
 * Account/region-level security controls that don't belong to any single
 * agent's stack: CloudTrail, Lambda vulnerability scanning, Bedrock
 * consumption alarms, cost anomaly detection/budgets, and Macie.
 *
 * These are account-wide singletons -- instantiate this stack exactly once
 * per (account, region), not once per AgentCore deployment target. See
 * bin/cdk.ts.
 */
export class SecurityFoundationsStack extends Stack {
  constructor(scope: Construct, id: string, props: SecurityFoundationsStackProps = {}) {
    super(scope, id, props);

    const { notificationEmail, monthlyBudgetLimitUsd = 100 } = props;

    // ── B1: CloudTrail ──────────────────────────────────────────────────
    // A multi-region trail with all management events (the defaults) covers
    // Bedrock control-plane API calls -- Bedrock has no S3-style data events
    // to add an advanced event selector for.
    // Closes: BR-06, AG-08.
    new cloudtrail.Trail(this, 'AccountTrail', {
      trailName: 'account-management-events',
      sendToCloudWatchLogs: true,
      cloudWatchLogsRetention: undefined, // use construct default
    });

    // ── B2: Amazon Inspector Lambda scanning ────────────────────────────
    // No CFN L1 resource exists for account-level Inspector2 enablement in
    // this CDK version, so call the Enable API directly via a custom
    // resource. Closes: BR-33, OW-03.
    const inspectorLambdaScanningEnabler = new AwsCustomResource(this, 'InspectorLambdaScanningEnabler', {
      onCreate: {
        service: 'inspector2',
        action: 'Enable',
        parameters: {
          accountIds: [this.account],
          resourceTypes: ['LAMBDA', 'LAMBDA_CODE'],
        },
        physicalResourceId: PhysicalResourceId.of('inspector2-lambda-scanning'),
      },
      onUpdate: {
        service: 'inspector2',
        action: 'Enable',
        parameters: {
          accountIds: [this.account],
          resourceTypes: ['LAMBDA', 'LAMBDA_CODE'],
        },
        physicalResourceId: PhysicalResourceId.of('inspector2-lambda-scanning'),
      },
      // Deliberately no onDelete -- leaving Inspector scanning enabled on
      // stack teardown is the safe default; disabling it destructively via
      // an account-wide API call from a stack deletion is not.
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: AwsCustomResourcePolicy.ANY_RESOURCE }),
    });
    // Enabling Inspector2 for the first time in an account makes the service
    // create its own service-linked role -- fromSdkCalls only grants the
    // inspector2:Enable action itself, not this implicit side effect, so the
    // call fails with "not authorized to perform iam:CreateServiceLinkedRole"
    // without this explicit grant.
    inspectorLambdaScanningEnabler.grantPrincipal.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ['iam:CreateServiceLinkedRole'],
        resources: ['arn:aws:iam::*:role/aws-service-role/inspector2.amazonaws.com/AWSServiceRoleForAmazonInspector2'],
        conditions: { StringEquals: { 'iam:AWSServiceName': 'inspector2.amazonaws.com' } },
      })
    );

    // ── Shared SNS topic for security/cost notifications ────────────────
    const alertsTopic = new sns.Topic(this, 'SecurityAlertsTopic', {
      topicName: 'fionaa-security-alerts',
      displayName: 'Fionaa AWS security & cost alerts',
    });
    if (notificationEmail) {
      alertsTopic.addSubscription(new subscriptions.EmailSubscription(notificationEmail));
    }

    // ── B3: CloudWatch alarms on Bedrock consumption ────────────────────
    // Closes: BR-32, AG-14, FS-05, FS-11, OW-10.
    const invocationThrottlesAlarm = new cloudwatch.Alarm(this, 'BedrockInvocationThrottlesAlarm', {
      alarmDescription:
        'Any Bedrock InvocationThrottles in the last 5 minutes -- possible runaway agent loop or capacity issue',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/Bedrock',
        metricName: 'InvocationThrottles',
        statistic: 'Sum',
        period: Duration.minutes(5),
      }),
      threshold: 0,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    invocationThrottlesAlarm.addAlarmAction(new cloudwatchActions.SnsAction(alertsTopic));

    // Token-volume alarms are informational trip-wires, not hard limits --
    // thresholds should be tuned to this account's actual expected peak load.
    for (const [metricName, threshold] of [
      ['InputTokenCount', 2_000_000],
      ['OutputTokenCount', 1_000_000],
    ] as const) {
      const alarm = new cloudwatch.Alarm(this, `Bedrock${metricName}Alarm`, {
        alarmDescription: `Bedrock ${metricName} exceeded ${threshold} in a 1-hour window -- review for abuse/looping agents`,
        metric: new cloudwatch.Metric({
          namespace: 'AWS/Bedrock',
          metricName,
          statistic: 'Sum',
          period: Duration.hours(1),
        }),
        threshold,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
      alarm.addAlarmAction(new cloudwatchActions.SnsAction(alertsTopic));
    }

    // ── B4: Cost Anomaly Detection + Budget ─────────────────────────────
    // Closes: FS-04, FS-06, OW-10.
    const anomalyMonitor = new ce.CfnAnomalyMonitor(this, 'BedrockSageMakerAnomalyMonitor', {
      monitorName: 'fionaa-bedrock-sagemaker-anomaly-monitor',
      monitorType: 'DIMENSIONAL',
      monitorDimension: 'SERVICE',
    });
    new ce.CfnAnomalySubscription(this, 'BedrockSageMakerAnomalySubscription', {
      subscriptionName: 'fionaa-bedrock-sagemaker-anomaly-subscription',
      frequency: 'IMMEDIATE',
      monitorArnList: [anomalyMonitor.attrMonitorArn],
      subscribers: [{ type: 'SNS', address: alertsTopic.topicArn }],
      thresholdExpression: JSON.stringify({
        Dimensions: { Key: 'ANOMALY_TOTAL_IMPACT_ABSOLUTE', Values: ['100'], MatchOptions: ['GREATER_THAN_OR_EQUAL'] },
      }),
    });

    new budgets.CfnBudget(this, 'BedrockSageMakerBudget', {
      budget: {
        budgetName: 'fionaa-bedrock-sagemaker-budget',
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: monthlyBudgetLimitUsd, unit: 'USD' },
        costFilters: { Service: ['Amazon Bedrock', 'Amazon SageMaker'] },
      },
      notificationsWithSubscribers: [80, 100].map(thresholdPct => ({
        notification: {
          notificationType: 'ACTUAL',
          comparisonOperator: 'GREATER_THAN',
          threshold: thresholdPct,
          thresholdType: 'PERCENTAGE',
        },
        subscribers: [{ subscriptionType: 'SNS', address: alertsTopic.topicArn }],
      })),
    });

    // ── B5: Amazon Macie ─────────────────────────────────────────────────
    // Closes: FS-44, OW-02.
    new macie.CfnSession(this, 'MacieSession', {
      status: 'ENABLED',
      // Macie's API only supports FIFTEEN_MINUTES / ONE_HOUR / SIX_HOURS --
      // there is no 24h option, so SIX_HOURS is the closest available cadence.
      findingPublishingFrequency: 'SIX_HOURS',
    });

    // ── B6: Small fixes on the pre-existing agentcore-cli staging bucket ──
    // Closes: FS-21 (versioning), FS-46 (data-classification tag). This
    // bucket is created by the agentcore CLI toolkit outside this repo's
    // CDK, so mutate its settings via custom resource rather than importing
    // and redeclaring it.
    const stagingBucketName = `bedrock-agentcore-codebuild-sources-${this.account}-${this.region}`;
    const stagingBucketFix = new AwsCustomResource(this, 'StagingBucketVersioningAndTags', {
      onCreate: {
        service: 's3',
        action: 'putBucketVersioning',
        parameters: { Bucket: stagingBucketName, VersioningConfiguration: { Status: 'Enabled' } },
        physicalResourceId: PhysicalResourceId.of('staging-bucket-versioning'),
      },
      onUpdate: {
        service: 's3',
        action: 'putBucketVersioning',
        parameters: { Bucket: stagingBucketName, VersioningConfiguration: { Status: 'Enabled' } },
        physicalResourceId: PhysicalResourceId.of('staging-bucket-versioning'),
      },
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: AwsCustomResourcePolicy.ANY_RESOURCE }),
    });
    const stagingBucketTags = new AwsCustomResource(this, 'StagingBucketTags', {
      onCreate: {
        service: 's3',
        action: 'putBucketTagging',
        parameters: {
          Bucket: stagingBucketName,
          Tagging: { TagSet: [{ Key: 'data-classification', Value: 'Internal' }] },
        },
        physicalResourceId: PhysicalResourceId.of('staging-bucket-tags'),
      },
      onUpdate: {
        service: 's3',
        action: 'putBucketTagging',
        parameters: {
          Bucket: stagingBucketName,
          Tagging: { TagSet: [{ Key: 'data-classification', Value: 'Internal' }] },
        },
        physicalResourceId: PhysicalResourceId.of('staging-bucket-tags'),
      },
      policy: AwsCustomResourcePolicy.fromSdkCalls({ resources: AwsCustomResourcePolicy.ANY_RESOURCE }),
    });
    stagingBucketTags.node.addDependency(stagingBucketFix);

    new CfnOutput(this, 'SecurityAlertsTopicArn', { value: alertsTopic.topicArn });
  }
}
