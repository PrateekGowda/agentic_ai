import sys
from pathlib import Path

from orchestrator.agentcore import AgentCoreRuntimeClient
from orchestrator.github_client import GitHubRepositoryClient
from orchestrator.models import (
    ComplianceFinding,
    CustomizationQuestion,
    DeploymentEvent,
    DeploymentSession,
    DeploymentSpec,
    DeploymentStatus,
    RequirementMessage,
)
from orchestrator.settings import Settings

AGENTS_ROOT = Path(__file__).resolve().parents[4] / "agents"
if str(AGENTS_ROOT) not in sys.path:
    sys.path.append(str(AGENTS_ROOT))

from compliance_agent import run_compliance_checks  # noqa: E402
from deployer_agent import run_deployment_step  # noqa: E402
from provisioner_agent import provision_repository_payload  # noqa: E402
from requirement_agent import handle_requirement_message  # noqa: E402


class DeploymentWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.github = GitHubRepositoryClient(settings.github_token, settings.github_owner)
        self.requirements = AgentCoreRuntimeClient(
            settings.agentcore_requirement_runtime_arn,
            handle_requirement_message,
        )
        self.provisioner = AgentCoreRuntimeClient(
            settings.agentcore_provisioner_runtime_arn,
            provision_repository_payload,
        )
        self.deployer = AgentCoreRuntimeClient(settings.agentcore_deployer_runtime_arn, run_deployment_step)
        self.compliance = AgentCoreRuntimeClient(
            settings.agentcore_compliance_runtime_arn,
            run_compliance_checks,
        )

    async def gather_requirements(
        self,
        session: DeploymentSession,
        request: RequirementMessage,
    ) -> DeploymentSession:
        result = await self.requirements.invoke(request.model_dump())
        data = result.get("data", {})
        if not data.get("complete"):
            session.customization_questions = [
                CustomizationQuestion(**question) for question in data.get("questions", [])
            ]
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="requirements",
                    status=DeploymentStatus.requirements,
                    message=result["message"],
                )
            )
            return session

        session.spec = DeploymentSpec(**data["spec"])
        session.customization_questions = []
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="requirements",
                severity="success",
                status=DeploymentStatus.customizing,
                message=result["message"],
            )
        )
        return session

    async def provision(self, session: DeploymentSession) -> DeploymentSession:
        if not session.spec:
            raise ValueError("Requirements must be completed before provisioning.")

        result = await self.provisioner.invoke({"spec": session.spec.model_dump()})
        data = result["data"]
        repo = self.github.create_repository(data["repo_name"], private=data["private"])
        session.repository_url = repo.url
        session.customization_questions = [
            CustomizationQuestion(**question) for question in data["customization_questions"]
        ]
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="provisioner",
                severity="success",
                status=DeploymentStatus.repo_created,
                message=f"Created GitHub repository: {repo.url}",
                details={"files": sorted(data["files"].keys())},
            )
        )
        return session

    async def run_compliance(self, session: DeploymentSession) -> DeploymentSession:
        if not session.spec:
            raise ValueError("Requirements must be completed before compliance checks.")

        result = await self.compliance.invoke({"spec": session.spec.model_dump()})
        findings = result["data"]["findings"]
        session.findings = [ComplianceFinding(**finding) for finding in findings]
        status = DeploymentStatus.blocked if result["data"]["blocking"] else DeploymentStatus.awaiting_approval
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="compliance",
                severity="warning" if findings else "success",
                status=status,
                message=result["message"],
                details={"findings": findings},
            )
        )
        return session

    async def deploy(self, session: DeploymentSession) -> DeploymentSession:
        result = await self.deployer.invoke(
            {
                "approved": session.approved,
                "findings": [finding.model_dump() for finding in session.findings],
            }
        )
        status = DeploymentStatus(result["data"]["status"])
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="success" if status == DeploymentStatus.succeeded else "info",
                status=status,
                message=result["message"],
                details=result["data"],
            )
        )
        if status == DeploymentStatus.succeeded and session.repository_url:
            session.architecture_doc_url = f"{session.repository_url}/blob/main/ARCHITECTURE.md"
            session.compliance_report_url = f"{session.repository_url}/blob/main/COMPLIANCE.md"
        return session
