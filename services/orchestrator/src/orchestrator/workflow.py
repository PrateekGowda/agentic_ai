import sys
from pathlib import Path

from orchestrator.agentcore import AgentCoreRuntimeClient
from orchestrator.aws_resources import Ec2HttpdManager
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
from provisioner_agent.agent import render_terraform  # noqa: E402
from requirement_agent import handle_requirement_message  # noqa: E402


class DeploymentWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
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
        self.ec2_httpd = Ec2HttpdManager(settings.aws_region)

    def _github(self, session: DeploymentSession) -> GitHubRepositoryClient:
        return GitHubRepositoryClient(
            token=session.github_token or self.settings.github_token,
            owner=self.settings.github_owner,
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
        github = self._github(session)
        repo = github.create_repository(data["repo_name"], private=data["private"])
        session.repository_url = repo.url
        files = dict(data["files"])
        template_root = (
            Path(__file__).resolve().parents[4]
            / "templates"
            / "terraform"
            / session.spec.workload_type
        )
        if template_root.exists():
            for path, content in render_terraform(session.spec.model_dump(), template_root).items():
                files[f"terraform/{path}"] = content

        for path, content in files.items():
            github.upsert_file(
                repo_full_name=repo.full_name,
                path=path,
                content=content,
                message=f"Generate {path}",
            )

        provision_message = (
            f"Created GitHub repository and committed generated infrastructure: {repo.url}"
            if github.is_configured
            else (
                "Prepared generated infrastructure files. Configure GITHUB_TOKEN or "
                f"GITHUB_TOKEN_SECRET_ARN to create and update the real repository: {repo.url}"
            )
        )
        session.customization_questions = [
            CustomizationQuestion(**question) for question in data["customization_questions"]
        ]
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="provisioner",
                severity="success",
                status=DeploymentStatus.repo_created,
                message=provision_message,
                details={"files": sorted(files.keys())},
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

    async def run_automatic(
        self,
        session: DeploymentSession,
        request: RequirementMessage,
    ) -> DeploymentSession:
        session = await self.gather_requirements(session, request)
        if not session.spec:
            return session

        session = await self.provision(session)
        session = await self.run_compliance(session)
        if session.status == DeploymentStatus.blocked:
            return session

        session.approved = True
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="info",
                status=DeploymentStatus.deploying,
                message="No blocking compliance findings found; automatic dev deployment approved.",
            )
        )
        session = await self.deploy(session)
        if session.spec and session.spec.workload_type == "ec2-httpd":
            session = await self.run_ec2_httpd_test(session)
        return session

    async def run_ec2_httpd_test(self, session: DeploymentSession) -> DeploymentSession:
        project_name = session.spec.name if session.spec else f"agentcore-{session.id[:8]}"
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="info",
                status=DeploymentStatus.deploying,
                message="Starting EC2 httpd test: security group, instance, and user-data install.",
            )
        )
        resources = self.ec2_httpd.create(session.id, project_name)
        session.resources["ec2_httpd"] = resources
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="success",
                status=DeploymentStatus.succeeded,
                message=f"EC2 httpd test is running at {resources.get('url')}",
                details=resources,
            )
        )
        return session

    async def destroy(self, session: DeploymentSession) -> DeploymentSession:
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="destroyer",
                severity="warning",
                status=DeploymentStatus.remediating,
                message="Destroy started for resources tracked by this project.",
            )
        )
        if session.resources.get("ec2_httpd"):
            result = self.ec2_httpd.destroy(session.resources["ec2_httpd"])
            session.resources["ec2_httpd_destroy"] = result
        else:
            result = {"message": "No EC2 httpd resources tracked for this project."}

        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="destroyer",
                severity="success",
                status=DeploymentStatus.destroyed,
                message="Destroy completed for tracked project resources.",
                details=result,
            )
        )
        return session
