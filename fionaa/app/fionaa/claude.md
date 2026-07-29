# AWS Guidance Document

**Core Principles:**
The guidance recommends prioritizing the AWS MCP Server for AWS interactions due to its sandboxed execution and audit capabilities. Before undertaking tasks, relevant AWS skills should be checked and loaded via `retrieve_skill`.

**Documentation and Verification:**
"When uncertain about specific AWS details (API parameters, permissions, limits, error codes), verify against documentation rather than guessing."

**Infrastructure Approach:**
For infrastructure creation, the document advises using infrastructure-as-code tools like AWS CDK or CloudFormation rather than direct CLI commands. When managing infrastructure, adherence to AWS Well-Architected Framework principles is emphasized.

**Naming Conventions:**
Resource names and descriptions should use hyphens instead of em dashes.

**Secret Management:**
A critical requirement states that the `aws-secrets-manager` skill must be loaded first for any work involving secrets, credentials, API keys, tokens, or passwords. Direct calls to Secrets Manager APIs are prohibited. Instead, utilize `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with `asm-exec` to enable runtime secret resolution without exposing sensitive data in context.
