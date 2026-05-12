from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    aws_region: str = "us-east-1"
    github_token: str | None = None
    github_owner: str | None = None
    company_standards_path: str = "./samples/company-standards.md"

    agentcore_requirement_runtime_arn: str | None = None
    agentcore_provisioner_runtime_arn: str | None = None
    agentcore_deployer_runtime_arn: str | None = None
    agentcore_compliance_runtime_arn: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
