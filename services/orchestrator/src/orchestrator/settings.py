from functools import lru_cache

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    aws_region: str = "us-east-1"
    github_token: str | None = None
    github_token_secret_arn: str | None = None
    github_owner: str | None = None
    reference_library_repo: str = "iac-codebase-agentic-ai"
    bedrock_model_id: str = "amazon.nova-pro-v1:0"
    agent_llm_enabled: bool = True
    company_standards_path: str = "./samples/company-standards.md"
    project_state_bucket: str = "hack-aib-tf-backend"
    terraform_runner_project_name: str = "agentcore-deployer-dev-terraform-runner"
    max_auto_remediation_retries: int = 5
    agentcore_memory_id: str | None = None

    agentcore_requirement_runtime_arn: str | None = None
    agentcore_provisioner_runtime_arn: str | None = None
    agentcore_deployer_runtime_arn: str | None = None
    agentcore_compliance_runtime_arn: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.github_token and settings.github_token_secret_arn:
        client = boto3.client("secretsmanager", region_name=settings.aws_region)
        secret = client.get_secret_value(SecretId=settings.github_token_secret_arn)
        settings.github_token = secret.get("SecretString")
    return settings
