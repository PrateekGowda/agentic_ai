import sys
import re
from pathlib import Path

from orchestrator.agentcore import AgentCoreRuntimeClient
from orchestrator.aws_resources import Ec2HttpdManager, S3BucketManager
from orchestrator.github_client import GitHubRepositoryClient
from orchestrator.memory import AgentCoreMemory
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
from orchestrator.state_archive import S3ProjectStateArchive

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
        self.s3_bucket = S3BucketManager(settings.aws_region)
        self.archive = S3ProjectStateArchive(settings.project_state_bucket, settings.aws_region)
        self.memory = AgentCoreMemory(settings.agentcore_memory_id, settings.aws_region)

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
            self._remember(session, "ASSISTANT", result["message"], {"stage": "requirements"})
            return session

        session.spec = DeploymentSpec(**data["spec"])
        session.customization_questions = []
        session.resources["chat_answers"] = session.spec.model_dump(mode="json")
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="requirements",
                severity="success",
                status=DeploymentStatus.customizing,
                message=result["message"],
            )
        )
        self._remember(session, "ASSISTANT", result["message"], {"stage": "requirements"})
        self.persist_state(session)
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

        github_verification = None
        if github.is_configured:
            github_verification = github.read_file(repo.full_name, "README.md")

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
                details={
                    "files": sorted(files.keys()),
                    "github_read_verified": bool(github_verification),
                },
            )
        )
        self.persist_state(session)
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
        self.persist_state(session)
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
        if status == DeploymentStatus.succeeded and session.spec and session.spec.workload_type == "s3-bucket":
            session = await self.run_s3_bucket_test(session)
        self.persist_state(session)
        return session

    async def chat(self, session: DeploymentSession, request: RequirementMessage) -> DeploymentSession:
        self._add_chat_message(session, "user", request.message)
        self._remember(session, "USER", request.message, {"stage": "chat"})
        if self._is_approval(request.message) and session.status == DeploymentStatus.awaiting_approval:
            session.approved = True
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=DeploymentStatus.deploying,
                    message="Approval received in chat. Deployment is starting.",
                )
            )
            session = await self.deploy(session)
            self._add_chat_message(session, "assistant", "Deployment is complete. Review the repository, artifacts, and resource details on the right.")
            return session

        answers = dict(session.resources.get("chat_answers", {}))
        self._merge_answers(answers, request.answers)
        self._merge_answers(answers, self._extract_chat_answers(request.message))
        session.resources["chat_answers"] = answers
        missing = [key for key in ("name", "description", "owner", "cost_center") if not answers.get(key)]
        if missing:
            question = self._missing_question(missing[0])
            self._add_chat_message(session, "assistant", question)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="requirements",
                    status=DeploymentStatus.requirements,
                    message=question,
                    details={"missing": missing, "remembered_context": self.memory.load_session_context(session.id)},
                )
            )
            self._remember(session, "ASSISTANT", question, {"stage": "requirements"})
            self.persist_state(session)
            return session

        session = await self.gather_requirements(session, RequirementMessage(message=request.message, answers=answers))
        if not session.spec:
            return session
        self._add_chat_message(
            session,
            "assistant",
            f"I have the requirements for `{session.spec.name}`. I am creating architecture, Terraform, and GitHub documentation now.",
        )
        session = await self.provision(session)
        session = await self.run_compliance(session)
        if session.status == DeploymentStatus.awaiting_approval:
            approval_message = (
                "Architecture, Terraform, and compliance checks are ready. "
                "Review the GitHub links, then type `approve` to deploy."
            )
            self._add_chat_message(session, "assistant", approval_message)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=DeploymentStatus.awaiting_approval,
                    message="Review the GitHub architecture and compliance details, then type 'approve' to deploy.",
                    details={
                        "repository_url": session.repository_url,
                        "architecture_doc_url": session.architecture_doc_url,
                        "findings": [finding.model_dump() for finding in session.findings],
                    },
                )
            )
            self.persist_state(session)
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

    async def run_s3_bucket_test(self, session: DeploymentSession) -> DeploymentSession:
        project_name = session.spec.name if session.spec else f"agentcore-{session.id[:8]}"
        if session.resources.get("s3_bucket"):
            return session
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="info",
                status=DeploymentStatus.deploying,
                message="Creating S3 bucket with encryption, versioning, tags, and public access block.",
            )
        )
        resources = self.s3_bucket.create(session.id, project_name)
        session.resources["s3_bucket"] = resources
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="success",
                status=DeploymentStatus.succeeded,
                message=f"S3 bucket created: {resources['bucket_uri']}",
                details=resources,
            )
        )
        self.persist_state(session)
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
        self.persist_state(session)
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
        elif session.resources.get("s3_bucket"):
            result = self.s3_bucket.destroy(session.resources["s3_bucket"])
            session.resources["s3_bucket_destroy"] = result
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
        self.persist_state(session)
        return session

    def persist_state(self, session: DeploymentSession) -> DeploymentSession:
        try:
            state = self.archive.persist(session)
            session.resources["project_state"] = state
        except Exception as exc:
            session.resources["project_state_error"] = str(exc)
        return session

    def _remember(self, session: DeploymentSession, role: str, text: str, metadata: dict[str, str]) -> None:
        try:
            self.memory.remember(session.id, role, text, metadata)
            session.resources["agentcore_memory_id"] = self.settings.agentcore_memory_id
        except Exception as exc:
            session.resources["agentcore_memory_error"] = str(exc)

    def _add_chat_message(self, session: DeploymentSession, role: str, content: str) -> None:
        messages = list(session.resources.get("chat_messages", []))
        messages.append({"role": role, "content": content})
        session.resources["chat_messages"] = messages[-30:]

    def _merge_answers(self, answers: dict[str, str], updates: dict[str, str]) -> None:
        for key, value in updates.items():
            if not value:
                continue
            if key in {"region", "environment", "workload_type"} or not answers.get(key):
                answers[key] = value

    def _extract_chat_answers(self, message: str) -> dict[str, str]:
        answers: dict[str, str] = {}
        lower = message.lower()
        email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
        if email:
            answers["owner"] = email.group(0)
        else:
            owner = re.search(
                r"(?:owner|owned by|team|application owner)\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z0-9 ._-]{1,60})",
                message,
                re.I,
            )
            if owner:
                answers["owner"] = owner.group(1).strip(" .")
        cost_center = re.search(
            r"(?:cost\s*cent(?:er|re)|cost_center|charge\s*code|billing\s*code|cc)\s*(?:is|:|-)?\s*([A-Za-z]{0,6}-?\s*\d{2,})",
            message,
            re.I,
        )
        if cost_center:
            answers["cost_center"] = cost_center.group(1).replace(" ", "").upper()
        elif re.fullmatch(r"\s*(?:cc)?\s*\d{3,}\s*", message, re.I):
            answers["cost_center"] = message.strip().upper().replace(" ", "")
        region = re.search(r"\b[a-z]{2}-[a-z]+-\d\b", message)
        if region:
            answers["region"] = region.group(0)
        env = re.search(r"\b(dev|test|stage|prod)\b", message, re.I)
        if env:
            answers["environment"] = env.group(1).lower()
        name = self._extract_project_name(message)
        if name:
            answers["name"] = name
        elif "s3" in lower and "bucket" in lower:
            answers["name"] = "chat-s3-bucket"
        if message.strip() and len(message.split()) > 3:
            answers["description"] = message.strip()[:500]
        if "ec2" in lower or "httpd" in lower:
            answers["workload_type"] = "ec2-httpd"
        elif "s3 bucket" in lower or "bucket" in lower:
            answers["workload_type"] = "s3-bucket"
        return answers

    def _extract_project_name(self, message: str) -> str | None:
        patterns = [
            r"\bproject\s+name\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\b(?:project|application|app)\s+(?:called|named)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\b(?:project|application|app)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\bbucket\s+name\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\bbucket\s+(?:called|named)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
        ]
        ignored = {"project", "bucket", "called", "named", "name", "create"}
        for pattern in patterns:
            match = re.search(pattern, message, re.I)
            if match and match.group(1).lower() not in ignored:
                return match.group(1)
        return None

    def _is_approval(self, message: str) -> bool:
        return message.strip().lower() in {"approve", "approved", "yes", "go", "deploy", "proceed"}

    def _missing_question(self, key: str) -> str:
        questions = {
            "name": "What project name should I use?",
            "description": "Please describe what you want this project to build.",
            "owner": "Who is the owner email or team for this project?",
            "cost_center": "What cost center should I tag on the resources?",
        }
        return questions[key]
