from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class DeploymentStatus(StrEnum):
    requirements = "requirements"
    customizing = "customizing"
    repo_created = "repo_created"
    policy_check = "policy_check"
    awaiting_approval = "awaiting_approval"
    deploying = "deploying"
    remediating = "remediating"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"
    destroyed = "destroyed"


class DeploymentSpec(BaseModel):
    name: str
    description: str
    cloud: Literal["aws"] = "aws"
    region: str = "us-east-1"
    environment: Literal["dev", "test", "stage", "prod"] = "dev"
    workload_type: str = "s3-lambda-api"
    owner: str
    cost_center: str
    compliance_profile: Literal["baseline", "regulated"] = "baseline"
    github_visibility: Literal["private", "internal", "public"] = "private"
    tags: dict[str, str] = Field(default_factory=dict)
    standards_source: str | None = None


class RequirementMessage(BaseModel):
    message: str
    answers: dict[str, str] = Field(default_factory=dict)


class GitHubTokenRequest(BaseModel):
    token: str


class CustomizationQuestion(BaseModel):
    id: str
    label: str
    help_text: str | None = None
    default_value: str | None = None
    required: bool = True


class ComplianceFinding(BaseModel):
    id: str
    tool: Literal["opa", "checkov", "aws"]
    severity: Literal["low", "medium", "high", "critical"]
    title: str
    resource: str | None = None
    remediation: str
    blocking: bool


class DeploymentEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent: Literal["requirements", "provisioner", "deployer", "compliance", "destroyer"]
    severity: Literal["info", "warning", "error", "success"] = "info"
    status: DeploymentStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DeploymentSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: DeploymentStatus = DeploymentStatus.requirements
    spec: DeploymentSpec | None = None
    repository_url: str | None = None
    architecture_doc_url: str | None = None
    compliance_report_url: str | None = None
    customization_questions: list[CustomizationQuestion] = Field(default_factory=list)
    findings: list[ComplianceFinding] = Field(default_factory=list)
    events: list[DeploymentEvent] = Field(default_factory=list)
    approved: bool = False
    github_token: str | None = Field(default=None, exclude=True)
    github_token_configured: bool = False
    resources: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_event(self, event: DeploymentEvent) -> None:
        self.events.append(event)
        self.status = event.status
        self.updated_at = datetime.now(timezone.utc)
