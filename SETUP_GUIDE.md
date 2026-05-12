# Step-by-Step Setup Guide

This guide explains how to set up and run the AgentCore Multi-Agent Deployer even if you are not deeply technical.

## 1. What You Need Before Starting

Create or confirm access to these accounts:

- AWS account where infrastructure will be deployed.
- GitHub account or GitHub organization where new repositories can be created.
- Amazon Bedrock AgentCore access in AWS.

Install these tools on your laptop:

- Git
- Python 3.11 or newer
- Node.js 20 or newer
- Terraform 1.6 or newer
- AWS CLI

Optional but recommended:

- Checkov
- OPA
- Docker Desktop, if you plan to package agents as containers

## 2. Download The Code

Open PowerShell and run:

```powershell
git clone https://github.com/PrateekGowda/agentic_ai.git
cd agentic_ai
```

## 3. Create Local Settings

Copy the example settings file:

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill these values:

```text
AWS_REGION=us-east-1
GITHUB_OWNER=your-github-user-or-organization
GITHUB_TOKEN=your-github-token
```

For local testing, the AgentCore runtime ARN values can stay blank. When you deploy AgentCore runtimes later, fill these:

```text
AGENTCORE_REQUIREMENT_RUNTIME_ARN=
AGENTCORE_PROVISIONER_RUNTIME_ARN=
AGENTCORE_DEPLOYER_RUNTIME_ARN=
AGENTCORE_COMPLIANCE_RUNTIME_ARN=
```

## 4. Configure AWS Access

Run:

```powershell
aws configure
```

Enter:

- AWS access key
- AWS secret key
- Default region, such as `us-east-1`
- Output format, such as `json`

For a company setup, use AWS SSO or an approved enterprise credential method instead of personal keys.

## 5. Install Backend Dependencies

Run:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e "services/orchestrator[dev]"
```

## 6. Install UI Dependencies

Run:

```powershell
npm install
```

## 7. Start The Backend

Run this in PowerShell:

```powershell
uvicorn orchestrator.main:app --reload --app-dir services/orchestrator/src
```

Keep this window open. The backend will run at:

```text
http://localhost:8000
```

## 8. Start The Web UI

Open a second PowerShell window in the same repository folder and run:

```powershell
npm run dev --workspace apps/web
```

Open this address in your browser:

```text
http://localhost:3000
```

## 9. Try The Basic Workflow

On the web page:

1. Click `Start Session`.
2. Fill in application name, description, owner, cost center, region, and environment.
3. Click `Send to Requirement Agent`.
4. Click `Create GitHub Repo`.
5. Click `Run Compliance`.
6. If compliance is successful, click `Approve Apply`.
7. Click `Deploy`.

The right side of the UI shows:

- GitHub repository link.
- Deployment status.
- Compliance findings.
- Deployment timeline.
- Documentation links after deployment.

## 10. Deploy The Platform Infrastructure

The platform infrastructure code is in:

```text
iac/platform
```

It creates foundation resources such as:

- S3 bucket for Terraform state.
- DynamoDB table for sessions/state records.
- KMS key for encryption.
- CloudWatch log group.
- CodeBuild project for Terraform execution.

Run:

```powershell
cd iac/platform
terraform init
terraform plan -var "owner=your-email@example.com" -var "github_owner=your-github-org" -var "agentcore_runtime_image_uri=replace-after-agent-image-build"
terraform apply -var "owner=your-email@example.com" -var "github_owner=your-github-org" -var "agentcore_runtime_image_uri=replace-after-agent-image-build"
```

Only type `yes` when you are sure the AWS account and region are correct.

## 11. Add Company Standards

The sample standards file is:

```text
samples/company-standards.md
```

Update it with your company rules, for example:

- Required tags.
- Encryption requirements.
- Approved regions.
- IAM restrictions.
- Logging requirements.
- Production approval rules.

Later, this can be connected to Confluence so the platform reads standards from your company documentation.

## 12. How Generated Infrastructure Is Secured

The Terraform template includes:

- Encrypted S3 backend state.
- DynamoDB locking pattern.
- KMS key rotation.
- S3 public access block.
- S3 bucket encryption.
- CloudWatch log group.
- Required tags.
- Policy checks before deployment.

## 13. How To Test

Run backend tests:

```powershell
python -m pytest services/orchestrator/tests
```

Run TypeScript checks:

```powershell
npm run typecheck
```

Run UI build:

```powershell
npm run build
```

## 14. Common Problems

If the UI cannot connect to the backend:

- Confirm backend is running on `http://localhost:8000`.
- Confirm `.env` has `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.

If GitHub repository creation fails:

- Confirm `GITHUB_TOKEN` is set.
- Confirm the token has permission to create repositories.
- Confirm `GITHUB_OWNER` is correct.

If AWS deployment fails:

- Confirm `aws sts get-caller-identity` works.
- Confirm your IAM role can create S3, DynamoDB, KMS, CloudWatch, CodeBuild, and IAM resources.
- Confirm the AWS region is correct.

If compliance blocks deployment:

- Read the finding in the UI.
- Update the Terraform template or company standards.
- Run compliance again.

## 15. Production Hardening Checklist

Before using this in production:

- Replace local in-memory storage with DynamoDB persistence.
- Use a GitHub App instead of a personal token.
- Use AWS SSO or GitHub OIDC instead of long-lived AWS credentials.
- Package each agent as an AgentCore runtime.
- Store secrets in AWS Secrets Manager.
- Enable authentication for the UI.
- Add branch protection to generated GitHub repositories.
- Add approval workflow for production deployments.
- Connect company standards to Confluence or an approved documentation source.
