# Fionaa AWS architecture

Open [fionaa-aws-architecture.drawio](fionaa-aws-architecture.drawio) in diagrams.net or the VS Code Draw.io extension. All boxes, connections and labels are editable; AWS service icons are embedded SVGs from the [official AWS Architecture Icons package](https://aws.amazon.com/architecture/icons/) (31 July 2026 release), so the file does not depend on a particular draw.io stencil-library version. AgentCore Runtime, Memory, Gateway and evaluations share the official AgentCore service icon; Guardrails uses the Bedrock service icon. Icons are attached to their service boxes and move with them.

The diagram contains three pages:

1. **AWS service architecture** — runtime, authentication, customer data, models, checkpoints and MCP integrations.
2. **Assessment workflow** — company verification, assessment, claim validation and the human-review report.
3. **Delivery, evaluation and operations** — GitHub OIDC, deployment, batch evaluation and account-level controls.

This is a source-derived snapshot dated 16 September 2026, including the current working-tree changes to the CDK stack and the new security-foundations stack. It is not a live AWS inventory. The configured deployment region is us-east-1; the model uses a US cross-region inference profile. Amber dashed boxes indicate existing or separately managed dependencies. Arrows describe logical calls/data dependencies, not network routing or every IAM permission.

## Source map

| Diagram area | Authoritative source |
| --- | --- |
| Runtime, Memory, evaluators and dataset | [agentcore.json](../../../agentcore/agentcore.json) |
| Deployment target | [aws-targets.json](../../../agentcore/aws-targets.json) |
| S3 import, scoped IAM role, KMS, content guardrail, gateway config and geo Lambda | [cdk-stack.ts](../../../agentcore/cdk/lib/cdk-stack.ts) |
| Security controls and notifications | [security-foundations-stack.ts](../../../agentcore/cdk/lib/security-foundations-stack.ts), [stack entrypoint](../../../agentcore/cdk/bin/cdk.ts) |
| Invocation setup and response | [main.py](../../src/fionaa/main.py) |
| Customer identity and role assumption | [security.py](../../src/fionaa/security.py) |
| S3 artifacts and encryption | [storage.py](../../src/fionaa/storage.py) |
| Checkpoint isolation | [checkpointing.py](../../src/fionaa/integrations/checkpointing.py) |
| Model and content guardrail | [model/load.py](../../src/fionaa/model/load.py) |
| OAuth and MCP tool loading | [gateway.py](../../src/fionaa/gateway.py) |
| Workflow topology and human review | [graph.py](../../src/fionaa/graph.py), [decision.py](../../src/fionaa/workflow/decision.py), [validation.py](../../src/fionaa/workflow/validation.py) |
| Automated Reasoning checks and provisioning | [policy_consistency.py](../../src/fionaa/policy_consistency.py), [AR README](../../../agentcore/automated-reasoning/README.md) |
| Geo target registration outside CDK | [geo Lambda README](../../../agentcore/lambda/geo_area_match/README.md) |
| CI roles | [ci-infra](../../../../ci-infra/lib/) |
| Actual CI commands and triggers | [GitHub workflows](../../../../.github/workflows/) |

## Architectural details

- The runtime uses PUBLIC networking and CUSTOM_JWT authentication. No VPC, API Gateway or deployed dashboard hosting is defined for this application in the inspected source.
- The customer ID is derived from the verified email. STS session tags scope S3 access to the customer's prefix; the shared `loan_policy_documents/` prefix is read-only. Both bucket environment variables point to the same existing bucket. Assessment policy text is bundled in the application.
- Memory uses credentials from the assumed role, but actor/thread isolation is enforced by application code, not an IAM actor-level condition. Memory has a 30-day event expiry and no long-term extraction strategies.
- Runtime model calls attach the content guardrail. Automated Reasoning is a separate `ApplyGuardrail` request with per-loan-type bindings; it is not a model-to-guardrail service hop.
- Cognito authenticates runtime callers and separately issues client-credentials access tokens for Gateway access. Gateway OAuth secrets are read from Secrets Manager at invocation time.
- The shared ClaimsAgent gateway and its Companies House/web-search integrations are not provisioned by this repository. Their internal backing architecture is intentionally abstracted. The geo Lambda is defined here; its README records manual gateway registration completed on 14 August 2026. That historical status has not been rechecked against AWS.
- Final reports contain `pending_human_review` and an advisory AI recommendation, including when company verification fails. The dashboard files are mockups; a deployed human approval service is not represented.
- There are two configured custom evaluators, a managed evaluation dataset, and no online evaluation configuration. CI batch evaluation is distinct from the customer request path.

Validation: parsed the XML and checked unique cell IDs, connection references and page bounds on all three pages. A diagrams.net visual render was not performed.
