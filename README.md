# AgentCore Multi-Agent Deployer

This repository contains a production-minded MVP for an Amazon Bedrock AgentCore multi-agent deployment platform.

It provides a simple web UI where a user can describe the infrastructure they need. Behind the UI, multiple agents gather requirements, create Terraform code, create a GitHub repository, run compliance checks, deploy the infrastructure, fix safe deployment issues, and publish documentation back to GitHub.

## What This Builds

- A web page to talk to the deployment agents.
- A backend API that controls the full deployment workflow.
- Four agents:
  - Requirement agent: asks questions and creates a deployment specification.
  - Provisioner agent: creates Terraform code and a GitHub repository.
  - Deployer agent: runs the deployment after approval and handles safe remediation.
  - Compliance agent: checks company standards before and after deployment.
- Terraform templates with encrypted S3 state, KMS, CloudWatch logging, tagging, and security guardrails.
- Policy checks using Checkov and OPA/Rego.
- Platform infrastructure code for AWS services such as S3, DynamoDB, CloudWatch, KMS, and CodeBuild.

## Repository Structure

- `apps/web`: User interface.
- `services/orchestrator`: Backend API and workflow controller.
- `agents`: The four agent implementations.
- `packages/contracts`: Shared UI/API data types.
- `templates/terraform`: Terraform templates generated into customer infrastructure repos.
- `policies`: Security and compliance rules.
- `iac/platform`: Terraform for deploying the platform itself.
- `samples`: Example company standards text.
- `SETUP_GUIDE.md`: Step-by-step setup guide for nontechnical users.

## How The Flow Works

1. Open the UI.
2. Enter basic details such as application name, owner, cost center, AWS region, and environment.
3. The requirement agent creates a standard deployment request.
4. The provisioner agent creates the GitHub infrastructure repository and prepares Terraform.
5. The compliance agent checks company standards.
6. A human approves the deployment.
7. The deployer agent runs the deployment.
8. The UI shows deployment status, GitHub link, compliance status, and documentation links.

## Quick Local Start

Use this only for local testing. For the full AWS setup, follow `SETUP_GUIDE.md`.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -e "services/orchestrator[dev]"
npm install
```

Start the backend:

```powershell
uvicorn orchestrator.main:app --reload --app-dir services/orchestrator/src
```

Start the UI in another terminal:

```powershell
npm run dev --workspace apps/web
```

Open:

```text
http://localhost:3000
```

## Required Tools

- AWS account.
- AWS CLI configured.
- GitHub account or GitHub organization.
- GitHub token or GitHub App for repository creation.
- Python 3.11 or newer.
- Node.js 20 or newer.
- Terraform 1.6 or newer.
- Checkov and OPA for policy checks.

## Safety Defaults

- Deployment requires human approval before apply.
- High-severity compliance failures block deployment.
- Generated infrastructure uses encrypted S3 backend state.
- Secrets are read from environment variables or AWS Secrets Manager, not committed.
- Each major action is captured as a deployment event.

## Current MVP Notes

The repository is ready as an MVP scaffold. AgentCore runtime resources are represented behind an adapter so local testing can run before AWS preview/runtime identifiers are available. After AgentCore access is enabled in your AWS account, wire the runtime ARNs in `.env` or through the platform Terraform outputs.
