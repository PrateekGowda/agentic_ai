import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
from github import GithubException

from orchestrator.agentcore import AgentCoreRuntimeClient
from orchestrator.aws_readonly import query_aws_account
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
        self._feedback(session, "requirements", "Analyzing chat context and required project fields.")
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
            session.resources["pending_answer_key"] = data.get("questions", [{}])[0].get("id")
            self._remember(session, "ASSISTANT", result["message"], {"stage": "requirements"})
            return session

        session.spec = DeploymentSpec(**data["spec"])
        session.resources.pop("pending_answer_key", None)
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

        self._feedback(session, "provisioner", "Generating Terraform with LLM assistance and deterministic fallback.")
        result = await self.provisioner.invoke({"spec": session.spec.model_dump()})
        data = result["data"]
        files = dict(data["files"])
        if data.get("generation_mode") != "agent_generated":
            files = {}
        validation_errors = self._validate_generated_files(session.spec, files)
        if validation_errors:
            files = self._repair_generated_files(data, files)
            validation_errors = self._validate_generated_files(session.spec, files)

        if validation_errors:
            message = "Generated Terraform failed validation, so I did not push it to GitHub."
            self._add_chat_message(session, "assistant", f"{message} Issues: {'; '.join(validation_errors)}")
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="provisioner",
                    severity="error",
                    status=DeploymentStatus.failed,
                    message=message,
                    details={"validation_errors": validation_errors, "files": sorted(files.keys())},
                )
            )
            self.persist_state(session)
            return session

        github = self._github(session)
        try:
            repo = github.create_repository(data["repo_name"], private=data["private"])
        except GithubException as exc:
            message = self._github_error_message(exc)
            self._add_chat_message(session, "assistant", message)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="provisioner",
                    severity="error",
                    status=DeploymentStatus.failed,
                    message=message,
                    details={
                        "github_status": exc.status,
                        "github_owner": self.settings.github_owner,
                        "repo_name": data.get("repo_name"),
                    },
                )
            )
            self.persist_state(session)
            return session
        session.repository_url = repo.url

        changed_files = []
        for path, content in files.items():
            changed = github.upsert_file(
                repo_full_name=repo.full_name,
                path=path,
                content=content,
                message=f"Generate {path}",
            )
            if changed:
                changed_files.append(path)

        github_verification = None
        terraform_verification = None
        if github.is_configured:
            github_verification = github.read_file(repo.full_name, "README.md")
            terraform_verification = github.read_file(repo.full_name, "terraform/main.tf")

        reference_library = self._update_reference_library(github, data.get("reference_files", {}))
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
                    "changed_files": sorted(changed_files),
                    "validation": "passed",
                    "github_read_verified": bool(github_verification),
                    "terraform_read_verified": self._expected_terraform_marker(session.spec) in (terraform_verification or ""),
                    "reference_library": reference_library,
                },
            )
        )
        self.persist_state(session)
        return session

    def _github_error_message(self, exc: GithubException) -> str:
        if exc.status == 403:
            return (
                "GitHub repo creation failed because the default token in Secrets Manager "
                "does not have permission to create repositories. Update the secret with a "
                "classic PAT that has `repo` scope, or a fine-grained token for the target "
                "owner with repository creation/administration access, then send the request again."
            )
        return f"GitHub repo creation failed with status {exc.status}: {exc.data.get('message', str(exc))}"

    def _repair_generated_files(self, data: dict[str, object], files: dict[str, str]) -> dict[str, str]:
        repaired = dict(files)
        for path, content in dict(data.get("repair_files") or {}).items():
            if isinstance(path, str) and isinstance(content, str):
                repaired[path] = content
        repaired.setdefault(
            "terraform/README.md",
            "Run `terraform init`, `terraform plan`, and `terraform apply` through the approved pipeline.\n",
        )
        return repaired

    def _update_reference_library(self, github: GitHubRepositoryClient, files: dict[str, str]) -> dict[str, object]:
        if not github.is_configured or not files or not self.settings.reference_library_repo:
            return {"updated": False}
        repo_name = self.settings.reference_library_repo.split("/")[-1]
        try:
            repo = github.create_repository(repo_name, private=False)
            changed_files = []
            for path, content in files.items():
                if github.upsert_file(repo.full_name, path, content, f"Update reference pattern {path}"):
                    changed_files.append(path)
            return {
                "updated": True,
                "url": repo.url,
                "changed_files": sorted(changed_files),
            }
        except Exception as exc:
            return {"updated": False, "error": str(exc)}

    def _validate_generated_files(self, spec: DeploymentSpec, files: dict[str, str]) -> list[str]:
        errors: list[str] = []
        required = ["README.md", "ARCHITECTURE.md", "COMPLIANCE.md", "terraform/main.tf", "terraform/variables.tf", "terraform/backend.tf"]
        for path in required:
            if not files.get(path, "").strip():
                errors.append(f"missing or empty {path}")

        main_tf = files.get("terraform/main.tf", "")
        marker = self._expected_terraform_marker(spec)
        if marker and marker not in main_tf:
            errors.append(f"terraform/main.tf does not include expected {spec.workload_type} resource marker `{marker}`")
        unresolved = [placeholder for placeholder in ("${name}", "${region}", "${environment}", "${owner}", "${cost_center}") if placeholder in main_tf]
        if unresolved:
            errors.append(f"terraform/main.tf contains unresolved template variables: {', '.join(unresolved)}")
        if main_tf.count("{") != main_tf.count("}"):
            errors.append("terraform/main.tf has unbalanced braces")

        terraform = shutil.which("terraform")
        if terraform and not errors:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for path, content in files.items():
                    if path.startswith("terraform/") and path.endswith(".tf"):
                        target = root / path.removeprefix("terraform/")
                        target.write_text(content, encoding="utf-8")
                try:
                    fmt = subprocess.run([terraform, "fmt", "-check"], cwd=root, capture_output=True, text=True, timeout=30)
                    if fmt.returncode != 0:
                        errors.append(f"terraform fmt failed: {(fmt.stdout or fmt.stderr).strip()}")
                    init = subprocess.run([terraform, "init", "-backend=false"], cwd=root, capture_output=True, text=True, timeout=60)
                    if init.returncode == 0:
                        validate = subprocess.run([terraform, "validate"], cwd=root, capture_output=True, text=True, timeout=60)
                        if validate.returncode != 0:
                            errors.append(f"terraform validate failed: {(validate.stdout or validate.stderr).strip()}")
                except subprocess.TimeoutExpired:
                    pass
        return errors

    def _expected_terraform_marker(self, spec: DeploymentSpec) -> str:
        markers = {
            "ec2-httpd": "aws_instance",
            "s3-bucket": "aws_s3_bucket",
            "s3-lambda-api": "aws_lambda_function",
            "vpc-baseline": "aws_vpc",
        }
        return markers.get(spec.workload_type, "")

    async def run_compliance(self, session: DeploymentSession) -> DeploymentSession:
        if not session.spec:
            raise ValueError("Requirements must be completed before compliance checks.")

        self._feedback(session, "compliance", "Reviewing generated project context against security and tagging baseline.")
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
        if not session.spec or not session.repository_url:
            guidance = (
                "Deployment cannot start yet. I still need completed requirements and a generated "
                "GitHub repository. Please continue the chat flow so I can gather details and prepare the project."
            )
            self._add_chat_message(session, "assistant", guidance)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="warning",
                    status=session.status,
                    message="Deployment requested before requirements/provisioning completed.",
                    details={"has_spec": bool(session.spec), "has_repository_url": bool(session.repository_url)},
                )
            )
            self.persist_state(session)
            return session

        if not session.approved:
            self._add_chat_message(session, "assistant", "I am waiting for approval before deployment. Please approve when ready.")
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=DeploymentStatus.awaiting_approval,
                    message="Deployment requested without approval.",
                )
            )
            self.persist_state(session)
            return session

        if session.status not in {DeploymentStatus.awaiting_approval, DeploymentStatus.deploying, DeploymentStatus.repo_created}:
            self._add_chat_message(
                session,
                "assistant",
                f"Deployment is not allowed from the current state `{session.status}`. "
                "Complete requirements, provisioning, and compliance first.",
            )
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="warning",
                    status=session.status,
                    message="Deployment requested from invalid workflow state.",
                    details={"current_status": session.status},
                )
            )
            self.persist_state(session)
            return session

        if session.resources.get("halt_requested"):
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="warning",
                    status=session.status,
                    message="Deployment is halted by user request. Send `resume deployment` to continue.",
                )
            )
            self.persist_state(session)
            return session
        self._feedback(session, "deployer", "Applying the approved deployment plan and tracking created resources.")
        result = await self.deployer.invoke(
            {
                "approved": session.approved,
                "findings": [finding.model_dump() for finding in session.findings],
            }
        )
        status = DeploymentStatus(result["data"]["status"])
        if status != DeploymentStatus.succeeded:
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=status,
                    message=result["message"],
                    details=result["data"],
                )
            )
            self.persist_state(session)
            return session

        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="info",
                status=DeploymentStatus.deploying,
                message="Approval received. Starting Terraform apply in CodeBuild and waiting for resource verification.",
                details={"runner": self.settings.terraform_runner_project_name, "repository_url": session.repository_url},
            )
        )
        self.persist_state(session)
        apply_result = self._run_terraform_apply(session)
        if not apply_result.get("verified"):
            message = "Terraform apply did not complete with verified running resources."
            error_summary = self._describe_deploy_issue(apply_result)
            self._add_chat_message(
                session,
                "assistant",
                f"{message}\n\n{error_summary}\n\nI will try auto-remediation and redeploy unless you type `stop`.",
            )
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="error",
                    status=DeploymentStatus.failed,
                    message=message,
                    details=apply_result,
                )
            )
            session.resources["last_apply_error"] = apply_result
            precondition_error = str(apply_result.get("error", "")).lower()
            if "missing completed spec or repository url" not in precondition_error:
                remediated = await self._auto_remediate_and_retry(session, apply_result)
                if remediated:
                    return session
            self.persist_state(session)
            return session

        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="success",
                status=DeploymentStatus.succeeded,
                message="Terraform apply completed and AWS resources were verified as running/available.",
                details=apply_result,
            )
        )
        session.resources["terraform_deployment"] = apply_result
        if session.repository_url:
            session.architecture_doc_url = f"{session.repository_url}/blob/main/ARCHITECTURE.md"
            session.compliance_report_url = f"{session.repository_url}/blob/main/COMPLIANCE.md"
        self.persist_state(session)
        return session

    def _describe_deploy_issue(self, apply_result: dict[str, object]) -> str:
        error = str(apply_result.get("error", "")).strip() or "Unknown deployment error."
        if "AccessDenied" in error:
            return "AWS denied access while applying infrastructure. Check IAM permissions for the runner role."
        if "NoCredentialProviders" in error or "security token" in error.lower():
            return "The deploy runner does not have valid AWS credentials configured."
        if "already exists" in error.lower():
            return "A resource with the same name already exists. Consider changing project/resource naming."
        logs_url = apply_result.get("logs_url")
        if logs_url:
            return f"Deployment failed. Review CodeBuild logs for root cause: {logs_url}"
        return f"Deployment failed with error: {error}"

    async def _auto_remediate_and_retry(self, session: DeploymentSession, apply_result: dict[str, object]) -> bool:
        if session.resources.get("halt_requested"):
            return False
        max_retries = max(0, int(self.settings.max_auto_remediation_retries))
        start_attempt = int(session.resources.get("remediation_attempt", 0))
        if start_attempt >= max_retries:
            self._add_chat_message(
                session,
                "assistant",
                "Auto-remediation retry limit reached. Please update inputs or tell me what to change, then retry.",
            )
            return False

        for attempt in range(start_attempt + 1, max_retries + 1):
            if session.resources.get("halt_requested"):
                self._add_chat_message(session, "assistant", "Auto-remediation stopped by user request.")
                self.persist_state(session)
                return False

            session.resources["remediation_attempt"] = attempt
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="warning",
                    status=DeploymentStatus.remediating,
                    message=f"Auto-remediation attempt {attempt}/{max_retries} started.",
                    details={"last_error": apply_result},
                )
            )
            self._add_thinking(session, f"Remediating deployment error (attempt {attempt}/{max_retries})...")
            self.persist_state(session)

            # Regenerate Terraform and re-run compliance before trying deploy again.
            try:
                session = await self.provision(session)
                if session.status == DeploymentStatus.failed:
                    continue
                session = await self.run_compliance(session)
                if session.status == DeploymentStatus.blocked:
                    self._add_chat_message(
                        session,
                        "assistant",
                        "Auto-remediation produced compliance blocking findings. Please review and update the request.",
                    )
                    self.persist_state(session)
                    return False
            except Exception as exc:
                session.add_event(
                    DeploymentEvent(
                        session_id=session.id,
                        agent="deployer",
                        severity="error",
                        status=DeploymentStatus.failed,
                        message=f"Auto-remediation internal error on attempt {attempt}.",
                        details={"error": str(exc)},
                    )
                )
                self._add_chat_message(
                    session,
                    "assistant",
                    f"Auto-remediation stopped due to internal error: {exc}",
                )
                self.persist_state(session)
                return False

            session.approved = True
            retry_result = self._run_terraform_apply(session)
            if retry_result.get("verified"):
                session.resources["terraform_deployment"] = retry_result
                session.add_event(
                    DeploymentEvent(
                        session_id=session.id,
                        agent="deployer",
                        severity="success",
                        status=DeploymentStatus.succeeded,
                        message=f"Auto-remediation succeeded on attempt {attempt}.",
                        details=retry_result,
                    )
                )
                self._add_chat_message(
                    session,
                    "assistant",
                    f"I fixed and redeployed the infrastructure successfully on attempt {attempt}.",
                )
                self.persist_state(session)
                return True

            apply_result = retry_result
            session.resources["last_apply_error"] = apply_result
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="error",
                    status=DeploymentStatus.failed,
                    message=f"Auto-remediation attempt {attempt} failed.",
                    details=apply_result,
                )
            )
            self._add_chat_message(
                session,
                "assistant",
                f"Auto-remediation attempt {attempt} failed: {self._describe_deploy_issue(apply_result)}",
            )
            self.persist_state(session)

        self._add_chat_message(
            session,
            "assistant",
            "I could not auto-remediate after multiple attempts. Tell me `stop` to halt or provide updated requirements to continue.",
        )
        return False

    def _run_terraform_apply(self, session: DeploymentSession) -> dict[str, object]:
        if not session.spec or not session.repository_url:
            return {"verified": False, "error": "Missing completed spec or repository URL."}
        github = self._github(session)
        if not github.is_configured:
            if self.settings.app_env == "local":
                return {
                    "verified": True,
                    "dry_run": True,
                    "resources": [{"type": session.spec.workload_type, "state": "verified-local-dry-run"}],
                }
            return {"verified": False, "error": "GitHub token is required so CodeBuild can clone the generated repository."}

        repo_full_name = self._repo_full_name(session.repository_url)
        result_key = f"{session.spec.name}/{session.id}/deployments/terraform-apply.json"
        environment = [
            {"name": "GITHUB_REPOSITORY", "value": repo_full_name, "type": "PLAINTEXT"},
            {"name": "AWS_REGION", "value": session.spec.region, "type": "PLAINTEXT"},
            {"name": "WORKLOAD_TYPE", "value": session.spec.workload_type, "type": "PLAINTEXT"},
            {"name": "PROJECT_STATE_BUCKET", "value": self.settings.project_state_bucket, "type": "PLAINTEXT"},
            {"name": "RESULT_KEY", "value": result_key, "type": "PLAINTEXT"},
        ]
        if self.settings.github_token_secret_arn:
            environment.append({"name": "GITHUB_TOKEN", "value": self.settings.github_token_secret_arn, "type": "SECRETS_MANAGER"})
        elif session.github_token or self.settings.github_token:
            environment.append({"name": "GITHUB_TOKEN", "value": session.github_token or self.settings.github_token or "", "type": "PLAINTEXT"})

        codebuild = boto3.client("codebuild", region_name=self.settings.aws_region)
        build = codebuild.start_build(
            projectName=self.settings.terraform_runner_project_name,
            buildspecOverride=self._terraform_apply_buildspec(),
            environmentVariablesOverride=environment,
        )["build"]
        build_id = build["id"]
        build_arn = build.get("arn")
        logs_url = build.get("logs", {}).get("deepLink")
        session.resources["terraform_apply"] = {
            "build_id": build_id,
            "build_arn": build_arn,
            "logs_url": logs_url,
            "result_s3_uri": f"s3://{self.settings.project_state_bucket}/{result_key}",
        }

        terminal_statuses = {"SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"}
        status = "IN_PROGRESS"
        for _ in range(360):
            current = codebuild.batch_get_builds(ids=[build_id])["builds"][0]
            status = current["buildStatus"]
            logs_url = current.get("logs", {}).get("deepLink") or logs_url
            if status in terminal_statuses:
                break
            time.sleep(10)

        result = self._read_deployment_result(result_key)
        result.update(
            {
                "verified": bool(status == "SUCCEEDED" and result.get("verified")),
                "codebuild_status": status,
                "build_id": build_id,
                "build_arn": build_arn,
                "logs_url": logs_url,
                "result_s3_uri": f"s3://{self.settings.project_state_bucket}/{result_key}",
                "repository": repo_full_name,
            }
        )
        return result

    def _run_terraform_destroy(self, session: DeploymentSession) -> dict[str, object]:
        if not session.spec or not session.repository_url:
            return {"destroyed": False, "error": "Missing completed spec or repository URL."}
        github = self._github(session)
        if not github.is_configured:
            if self.settings.app_env == "local":
                return {"destroyed": True, "dry_run": True}
            return {"destroyed": False, "error": "GitHub token is required so CodeBuild can clone the generated repository."}

        repo_full_name = self._repo_full_name(session.repository_url)
        result_key = f"{session.spec.name}/{session.id}/deployments/terraform-destroy.json"
        environment = [
            {"name": "GITHUB_REPOSITORY", "value": repo_full_name, "type": "PLAINTEXT"},
            {"name": "AWS_REGION", "value": session.spec.region, "type": "PLAINTEXT"},
            {"name": "PROJECT_STATE_BUCKET", "value": self.settings.project_state_bucket, "type": "PLAINTEXT"},
            {"name": "RESULT_KEY", "value": result_key, "type": "PLAINTEXT"},
        ]
        if self.settings.github_token_secret_arn:
            environment.append({"name": "GITHUB_TOKEN", "value": self.settings.github_token_secret_arn, "type": "SECRETS_MANAGER"})
        elif session.github_token or self.settings.github_token:
            environment.append({"name": "GITHUB_TOKEN", "value": session.github_token or self.settings.github_token or "", "type": "PLAINTEXT"})

        codebuild = boto3.client("codebuild", region_name=self.settings.aws_region)
        build = codebuild.start_build(
            projectName=self.settings.terraform_runner_project_name,
            buildspecOverride=self._terraform_destroy_buildspec(),
            environmentVariablesOverride=environment,
        )["build"]
        build_id = build["id"]
        logs_url = build.get("logs", {}).get("deepLink")
        session.resources["terraform_destroy"] = {
            "build_id": build_id,
            "logs_url": logs_url,
            "result_s3_uri": f"s3://{self.settings.project_state_bucket}/{result_key}",
        }

        terminal_statuses = {"SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"}
        status = "IN_PROGRESS"
        for _ in range(360):
            current = codebuild.batch_get_builds(ids=[build_id])["builds"][0]
            status = current["buildStatus"]
            logs_url = current.get("logs", {}).get("deepLink") or logs_url
            if status in terminal_statuses:
                break
            time.sleep(10)

        result = self._read_deployment_result(result_key)
        result.update(
            {
                "destroyed": bool(status == "SUCCEEDED" and result.get("destroyed")),
                "codebuild_status": status,
                "build_id": build_id,
                "logs_url": logs_url,
                "result_s3_uri": f"s3://{self.settings.project_state_bucket}/{result_key}",
                "repository": repo_full_name,
            }
        )
        return result

    def _read_deployment_result(self, key: str) -> dict[str, object]:
        try:
            response = self.archive.s3.get_object(Bucket=self.settings.project_state_bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except Exception as exc:
            return {"verified": False, "error": f"Unable to read Terraform deployment result: {exc}"}

    def _repo_full_name(self, repository_url: str) -> str:
        suffix = repository_url.removeprefix("https://github.com/").removesuffix(".git").strip("/")
        if "/" not in suffix:
            raise ValueError(f"Unable to parse GitHub repository URL: {repository_url}")
        return suffix

    def _terraform_apply_buildspec(self) -> str:
        return r"""version: 0.2

phases:
  pre_build:
    commands:
      - |
        set -eu
        TOKEN_ENCODED=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["GITHUB_TOKEN"], safe=""))')
        git clone --depth 1 "https://x-access-token:${TOKEN_ENCODED}@github.com/${GITHUB_REPOSITORY}.git" repo
        cd repo/terraform
        terraform init -input=false -reconfigure
        terraform validate
  build:
    commands:
      - |
        set +e
        cd repo/terraform
        terraform apply -auto-approve -input=false > /tmp/terraform-apply.log 2>&1
        APPLY_EXIT=$?
        if [ "$APPLY_EXIT" -ne 0 ]; then
          python3 - <<'PY'
        import json
        from pathlib import Path
        lines = Path("/tmp/terraform-apply.log").read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-40:]) if lines else "No terraform apply logs available."
        payload = {"verified": False, "stage": "terraform_apply", "error": "terraform apply failed", "error_tail": tail}
        open("/tmp/deployment-result.json", "w", encoding="utf-8").write(json.dumps(payload, indent=2))
        PY
          exit "$APPLY_EXIT"
        fi
        python3 - <<'PY'
        import json
        import os
        import subprocess
        import sys
        import time

        import boto3

        region = os.environ["AWS_REGION"]
        workload = os.environ["WORKLOAD_TYPE"]
        state = json.loads(subprocess.check_output(["terraform", "state", "pull"], text=True))
        resources = state.get("resources", [])
        verified = []

        def attrs(resource_type):
            for resource in resources:
                if resource.get("type") == resource_type:
                    for instance in resource.get("instances", []):
                        yield instance.get("attributes", {})

        try:
            if workload == "s3-lambda-api":
                functions = list(attrs("aws_lambda_function"))
                if not functions:
                    raise RuntimeError("Terraform state has no aws_lambda_function resource")
                client = boto3.client("lambda", region_name=region)
                for item in functions:
                    name = item.get("function_name") or item.get("id")
                    if not name:
                        raise RuntimeError("Lambda function resource is missing function_name/id")
                    state_name = "Unknown"
                    for _ in range(24):
                        config = client.get_function_configuration(FunctionName=name)
                        state_name = config.get("State", "Unknown")
                        if state_name == "Active":
                            break
                        time.sleep(5)
                    if state_name != "Active":
                        raise RuntimeError(f"Lambda function {name} is {state_name}, not Active")
                    verified.append({"type": "lambda", "name": name, "state": state_name})
            elif workload == "s3-bucket":
                buckets = list(attrs("aws_s3_bucket"))
                if not buckets:
                    raise RuntimeError("Terraform state has no aws_s3_bucket resource")
                client = boto3.client("s3", region_name=region)
                for item in buckets:
                    name = item.get("bucket") or item.get("id")
                    if not name:
                        raise RuntimeError("S3 bucket resource is missing bucket/id")
                    client.head_bucket(Bucket=name)
                    verified.append({"type": "s3_bucket", "name": name, "state": "available"})
            elif workload == "ec2-httpd":
                instances = list(attrs("aws_instance"))
                if not instances:
                    raise RuntimeError("Terraform state has no aws_instance resource")
                client = boto3.client("ec2", region_name=region)
                for item in instances:
                    instance_id = item.get("id")
                    if not instance_id:
                        raise RuntimeError("EC2 instance resource is missing id")
                    state_name = "unknown"
                    for _ in range(36):
                        response = client.describe_instances(InstanceIds=[instance_id])
                        state_name = response["Reservations"][0]["Instances"][0]["State"]["Name"]
                        if state_name == "running":
                            break
                        time.sleep(5)
                    if state_name != "running":
                        raise RuntimeError(f"EC2 instance {instance_id} is {state_name}, not running")
                    verified.append({"type": "ec2_instance", "id": instance_id, "state": state_name})
            elif workload == "vpc-baseline":
                vpcs = list(attrs("aws_vpc"))
                if not vpcs:
                    raise RuntimeError("Terraform state has no aws_vpc resource")
                client = boto3.client("ec2", region_name=region)
                for item in vpcs:
                    vpc_id = item.get("id")
                    if not vpc_id:
                        raise RuntimeError("VPC resource is missing id")
                    client.describe_vpcs(VpcIds=[vpc_id])
                    verified.append({"type": "vpc", "id": vpc_id, "state": "available"})
            else:
                if not resources:
                    raise RuntimeError("Terraform apply completed but state is empty")
                verified.append({"type": "terraform_state", "count": len(resources), "state": "present"})

            payload = {"verified": True, "stage": "resource_verification", "resources": verified}
        except Exception as exc:
            payload = {"verified": False, "stage": "resource_verification", "error": str(exc)}
            open("/tmp/deployment-result.json", "w", encoding="utf-8").write(json.dumps(payload, indent=2))
            sys.exit(1)

        open("/tmp/deployment-result.json", "w", encoding="utf-8").write(json.dumps(payload, indent=2))
        PY
  post_build:
    commands:
      - |
        if [ -f /tmp/deployment-result.json ]; then
          aws s3 cp /tmp/deployment-result.json "s3://${PROJECT_STATE_BUCKET}/${RESULT_KEY}" --sse AES256
        else
          printf '{"verified": false, "error": "deployment result file was not produced"}' > /tmp/deployment-result.json
          aws s3 cp /tmp/deployment-result.json "s3://${PROJECT_STATE_BUCKET}/${RESULT_KEY}" --sse AES256
        fi
"""

    def _terraform_destroy_buildspec(self) -> str:
        return r"""version: 0.2

phases:
  pre_build:
    commands:
      - |
        set -eu
        TOKEN_ENCODED=$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["GITHUB_TOKEN"], safe=""))')
        git clone --depth 1 "https://x-access-token:${TOKEN_ENCODED}@github.com/${GITHUB_REPOSITORY}.git" repo
        cd repo/terraform
        terraform init -input=false -reconfigure
  build:
    commands:
      - |
        set +e
        cd repo/terraform
        terraform destroy -auto-approve -input=false > /tmp/terraform-destroy.log 2>&1
        DESTROY_EXIT=$?
        if [ "$DESTROY_EXIT" -ne 0 ]; then
          python3 - <<'PY'
        import json
        from pathlib import Path
        lines = Path("/tmp/terraform-destroy.log").read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-40:]) if lines else "No terraform destroy logs available."
        payload = {"destroyed": False, "stage": "terraform_destroy", "error": "terraform destroy failed", "error_tail": tail}
        open("/tmp/deployment-result.json", "w", encoding="utf-8").write(json.dumps(payload, indent=2))
        PY
          exit "$DESTROY_EXIT"
        fi
        python3 - <<'PY'
        import json
        import subprocess
        state_list = subprocess.run(["terraform", "state", "list"], capture_output=True, text=True)
        remaining = [line for line in state_list.stdout.splitlines() if line.strip()]
        payload = {"destroyed": len(remaining) == 0, "remaining_resources": remaining}
        open("/tmp/deployment-result.json", "w", encoding="utf-8").write(json.dumps(payload, indent=2))
        PY
  post_build:
    commands:
      - |
        if [ -f /tmp/deployment-result.json ]; then
          aws s3 cp /tmp/deployment-result.json "s3://${PROJECT_STATE_BUCKET}/${RESULT_KEY}" --sse AES256
        else
          printf '{"destroyed": false, "error": "destroy result file was not produced"}' > /tmp/deployment-result.json
          aws s3 cp /tmp/deployment-result.json "s3://${PROJECT_STATE_BUCKET}/${RESULT_KEY}" --sse AES256
        fi
"""

    # ------------------------------------------------------------------ intent routing

    def _classify_intent(self, message: str, session: DeploymentSession) -> str:
        """Classify the user message into one of several intents."""
        lower = message.lower().strip()

        if self._is_stop_command(message):
            return "stop"
        if self._is_resume_command(message):
            return "resume"

        # Greetings
        if self._is_greeting(message):
            return "greeting"

        # Approval
        if self._is_approval(message) and session.status == DeploymentStatus.awaiting_approval:
            return "approval"

        # Destroy from chat
        if any(w in lower for w in ("destroy", "delete this project", "tear down")):
            if session.spec:
                return "destroy"

        # AWS read-only account query
        aws_query_keywords = (
            "show", "list", "what", "how many", "how much", "describe", "read",
            "which", "get", "find", "display",
        )
        aws_resource_keywords = (
            "bucket", "s3", "lambda", "function", "ec2", "instance", "vpc",
            "rds", "database", "ecs", "ecr", "iam role", "alarm", "cloudwatch",
            "cloudformation", "stack", "dynamodb", "table", "secret", "codebuild",
            "sns", "sqs", "account", "region", "running", "resources in", "aws resources",
        )
        is_query = any(kw in lower for kw in aws_query_keywords) and any(kw in lower for kw in aws_resource_keywords)
        if is_query:
            return "aws_read"

        # Update existing project
        if session.spec and self._is_update_request(message, {}):
            return "update"

        # Infra creation if there are clear infra signals
        infra_keywords = (
            "create", "build", "provision", "deploy", "make", "setup", "spin up",
            "new project", "new bucket", "new lambda", "new ec2", "new vpc",
            "ec2-httpd", "s3-bucket", "s3-lambda", "vpc-baseline",
        )
        if any(kw in lower for kw in infra_keywords):
            return "infra_create"

        # If already collecting requirements, continue that flow
        if session.resources.get("pending_answer_key"):
            return "infra_create"

        # If there are partially filled answers and no project yet, continue
        answers = session.resources.get("chat_answers", {})
        if not session.spec and answers.get("name"):
            return "infra_create"

        # Default: general LLM conversation
        return "general_chat"

    async def chat(self, session: DeploymentSession, request: RequirementMessage) -> DeploymentSession:
        self._add_chat_message(session, "user", request.message)
        self._remember(session, "USER", request.message, {"stage": "chat"})

        # Extract approval mode preference from any message
        approval_mode = self._extract_approval_mode(request.message)
        if approval_mode:
            session.resources["approval_mode"] = approval_mode

        intent = self._classify_intent(request.message, session)

        # ---- GREETING ----
        if intent == "greeting":
            message = (
                "Hello! I am your AI Agent for AWS Infrastructure as Code orchestration.\n\n"
                "Here is what I can do for you:\n"
                "- **Create infrastructure** — S3 buckets, Lambda functions, EC2 instances, VPCs, and more\n"
                "- **Update deployed projects** — change instance type, add encryption, update schedules\n"
                "- **Read your AWS account** — list buckets, EC2 instances, Lambda functions, RDS, ECS, and any AWS resource\n"
                "- **Manage deployments** — approve, destroy tracked project resources\n"
                "- **Answer questions** — infrastructure best practices, AWS costs, architecture advice\n\n"
                "What can I help you with today?"
            )
            self._add_chat_message(session, "assistant", message)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="requirements",
                    severity="info",
                    status=DeploymentStatus.requirements,
                    message=message,
                )
            )
            self.persist_state(session)
            return session

        # ---- STOP / HALT ----
        if intent == "stop":
            session.resources["halt_requested"] = True
            session.resources.pop("auto_deploy_after_seconds", None)
            session.resources.pop("auto_deploy_scheduled", None)
            if session.resources.get("terraform_apply", {}).get("build_id"):
                build_id = str(session.resources["terraform_apply"]["build_id"])
                try:
                    boto3.client("codebuild", region_name=self.settings.aws_region).stop_build(id=build_id)
                    self._add_chat_message(session, "assistant", "Stop requested. I have signaled CodeBuild to stop the running Terraform job.")
                except Exception:
                    self._add_chat_message(session, "assistant", "Stop requested. I will halt further retries and workflow actions.")
            else:
                self._add_chat_message(session, "assistant", "Stop requested. I will halt further workflow actions.")
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="warning",
                    status=session.status,
                    message="Workflow halted by user.",
                )
            )
            self.persist_state(session)
            return session

        # ---- RESUME ----
        if intent == "resume":
            session.resources["halt_requested"] = False
            self._add_chat_message(session, "assistant", "Resumed. Tell me to continue deployment, update the project, or run a new request.")
            self.persist_state(session)
            return session

        # ---- APPROVAL ----
        if intent == "approval":
            session.approved = True
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=DeploymentStatus.deploying,
                    message="Approval received in chat. Running Terraform apply and verifying AWS resources.",
                )
            )
            session = await self.deploy(session)
            if session.status == DeploymentStatus.succeeded:
                self._add_chat_message(
                    session, "assistant",
                    "Deployment is complete and AWS resources were verified. "
                    "You can view the GitHub repository and artifacts in the sidebar."
                )
            else:
                self._add_chat_message(
                    session, "assistant",
                    "Deployment did not verify successfully. Check the execution logs for the CodeBuild details."
                )
            return session

        # ---- DESTROY ----
        if intent == "destroy":
            self._add_chat_message(
                session, "assistant",
                f"I will destroy only the resources tracked in the project state for `{session.spec.name if session.spec else 'this project'}`. "
                "Resources not tracked by this project state file will not be touched."
            )
            session = await self.destroy(session)
            self._add_chat_message(session, "assistant", "Destroy completed for all tracked project resources.")
            return session

        # ---- AWS READ-ONLY QUERY ----
        if intent == "aws_read":
            self._add_thinking(session, "Reading your AWS account...")
            region = session.spec.region if session.spec else self.settings.aws_region
            result = query_aws_account(request.message, region)
            self._add_chat_message(session, "assistant", result["answer"])
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="requirements",
                    severity="info",
                    status=session.status,
                    message=f"AWS read query answered: {request.message[:100]}",
                    details={"item_count": len(result.get("data", []))},
                )
            )
            self.persist_state(session)
            return session

        # ---- UPDATE EXISTING PROJECT ----
        if intent == "update":
            extracted = self._extract_chat_answers(request.message)
            session = await self._update_existing_project(session, extracted, request.message)
            return session

        # ---- INFRA CREATE (full multi-step flow) ----
        if intent == "infra_create":
            return await self._handle_infra_create(session, request)

        # ---- GENERAL LLM CHAT FALLBACK ----
        return await self._handle_general_chat(session, request.message)

    async def _handle_infra_create(
        self,
        session: DeploymentSession,
        request: RequirementMessage,
    ) -> DeploymentSession:
        """Run the existing multi-step requirement → provision → compliance → approval flow."""
        if session.resources.get("halt_requested"):
            self._add_chat_message(session, "assistant", "Workflow is currently halted. Send `resume` to continue.")
            self.persist_state(session)
            return session
        answers = dict(session.resources.get("chat_answers", {}))
        self._merge_answers(answers, request.answers)
        extracted = self._extract_chat_answers(request.message)
        self._apply_pending_answer(session, answers, request.message, extracted)
        self._merge_answers(answers, extracted)
        session.resources["chat_answers"] = answers

        if session.spec and session.status == DeploymentStatus.succeeded and not self._is_update_request(request.message, extracted):
            self._add_chat_message(
                session, "assistant",
                "This project is already deployed. Tell me what to change — for example "
                "`update instance type to t3.small` or `enable versioning` — and I will regenerate Terraform and prepare a new diff."
            )
            self.persist_state(session)
            return session

        missing = [key for key in ("name", "description", "owner", "cost_center") if not answers.get(key)]
        if missing:
            question = self._missing_question(missing[0], answers)
            self._add_chat_message(session, "assistant", question)
            session.resources["pending_answer_key"] = missing[0]
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

        service_question = self._service_requirements_question(answers, request.message)
        if service_question and not answers.get(service_question["id"]):
            self._add_chat_message(session, "assistant", service_question["question"])
            session.resources["pending_answer_key"] = service_question["id"]
            session.resources["service_question_asked"] = service_question["id"]
            self.persist_state(session)
            return session

        service_notes = [value for key, value in answers.items() if key.startswith("service_") and value]
        if service_notes and "service details:" not in answers.get("description", "").lower():
            answers["description"] = (
                f"{answers.get('description', '').strip()} "
                f"Service details: {'; '.join(service_notes)}"
            ).strip()

        self._add_thinking(session, "Analyzing requirements...")
        session = await self.gather_requirements(session, RequirementMessage(message=request.message, answers=answers))
        if not session.spec:
            return session

        self._add_chat_message(session, "assistant", self._implementation_plan(session.spec))
        self._add_thinking(session, "Generating Terraform and documentation...")
        session = await self.provision(session)
        if session.status == DeploymentStatus.failed:
            return session

        self._add_thinking(session, "Running compliance checks...")
        session = await self.run_compliance(session)
        if session.status == DeploymentStatus.awaiting_approval:
            mode = session.resources.get("approval_mode", "auto")
            if mode == "skip":
                session.approved = True
                self._add_chat_message(session, "assistant", "Skipping approval as requested. Starting deployment now.")
                session.add_event(
                    DeploymentEvent(
                        session_id=session.id,
                        agent="deployer",
                        severity="info",
                        status=DeploymentStatus.deploying,
                        message="User requested direct deployment without approval.",
                    )
                )
                session = await self.deploy(session)
                if session.status == DeploymentStatus.succeeded:
                    self._add_chat_message(session, "assistant", "Deployment is complete and AWS resources were verified.")
                else:
                    self._add_chat_message(session, "assistant", "Deployment did not verify successfully. Check the execution logs.")
                return session
            if mode == "manual":
                approval_message = (
                    "Architecture, Terraform, and compliance checks are ready. "
                    "I will wait here for your approval. Type `approve` or click the Approve button when ready."
                )
            else:
                session.resources["auto_deploy_after_seconds"] = 180
                approval_message = (
                    "Architecture, Terraform, and compliance checks are ready. "
                    "Review the GitHub repository and click **Approve & Deploy** to proceed. "
                    "I will auto-deploy in about 3 minutes if there is no response."
                )
            self._add_chat_message(session, "assistant", approval_message)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="deployer",
                    severity="info",
                    status=DeploymentStatus.awaiting_approval,
                    message=approval_message,
                    details={
                        "repository_url": session.repository_url,
                        "architecture_doc_url": session.architecture_doc_url,
                        "findings": [finding.model_dump() for finding in session.findings],
                        "approval_mode": mode,
                    },
                )
            )
            self.persist_state(session)
        return session

    async def _handle_general_chat(self, session: DeploymentSession, message: str) -> DeploymentSession:
        """General LLM conversation fallback for anything not handled by specialized agents."""
        self._add_thinking(session, "Thinking...")

        # Build conversation context from recent chat history
        chat_history = session.resources.get("chat_messages", [])
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in chat_history[-10:]
            if m.get("role") in {"user", "assistant"}
        )

        project_context = ""
        if session.spec:
            project_context = (
                f"\nCurrent project: {session.spec.name} ({session.spec.workload_type}) "
                f"in {session.spec.region}/{session.spec.environment}, status={session.status}"
            )

        system_prompt = (
            "You are an expert AWS infrastructure AI assistant integrated with an IaC orchestration platform. "
            "You can answer questions about AWS services, infrastructure best practices, costs, security, and architecture. "
            "You can also help users understand their deployed projects. "
            "When a user wants to create or update infrastructure, tell them to describe what they want built. "
            "When a user asks about their AWS account resources, tell them you can query S3, Lambda, EC2, VPCs, RDS, ECS, IAM, etc. "
            "Be concise, helpful, and conversational. Do not generate Terraform or code unless specifically asked."
        )
        user_prompt = (
            f"{history_text}\n{project_context}\n\nUSER: {message}"
            if history_text or project_context
            else message
        )

        try:
            from common.llm import ask_llm_text  # noqa: PLC0415
            response = ask_llm_text(system_prompt, user_prompt, max_tokens=1500)
        except Exception:
            response = None

        if not response:
            response = (
                "I am your AWS infrastructure assistant. I can help you:\n"
                "- Create infrastructure (EC2, S3, Lambda, VPC, etc.)\n"
                "- Update and manage deployed projects\n"
                "- Query your AWS account resources (read-only)\n"
                "- Answer questions about AWS and infrastructure best practices\n\n"
                "What would you like to do?"
            )

        self._add_chat_message(session, "assistant", response)
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="requirements",
                severity="info",
                status=session.status,
                message=f"General LLM response for: {message[:100]}",
            )
        )
        self.persist_state(session)
        return session

    def _add_thinking(self, session: DeploymentSession, message: str) -> None:
        """Add a transient thinking/progress message visible in the chat."""
        messages = list(session.resources.get("chat_messages", []))
        messages.append({"role": "thinking", "content": message})
        session.resources["chat_messages"] = messages[-40:]

    async def _update_existing_project(
        self,
        session: DeploymentSession,
        answers: dict[str, str],
        message: str,
    ) -> DeploymentSession:
        if not session.spec:
            return session
        fingerprint = json.dumps({"message": message.strip().lower(), "updates": answers}, sort_keys=True)
        if session.resources.get("last_update_fingerprint") == fingerprint and session.status in {
            DeploymentStatus.customizing,
            DeploymentStatus.repo_created,
            DeploymentStatus.awaiting_approval,
            DeploymentStatus.deploying,
        }:
            self._add_chat_message(session, "assistant", "I already accepted that update and it is still in progress or waiting for approval.")
            self.persist_state(session)
            return session
        spec_data = session.spec.model_dump(mode="json")
        for key in ("description", "region", "environment", "owner", "cost_center", "workload_type"):
            if key == "description" and "description" not in message.lower():
                continue
            if answers.get(key):
                spec_data[key] = answers[key]
        tags = dict(spec_data.get("tags", {}))
        if instance_type := answers.get("instance_type"):
            tags["InstanceType"] = instance_type
        spec_data["tags"] = tags
        if spec_data == session.spec.model_dump(mode="json"):
            self._add_chat_message(session, "assistant", "I did not find a resource change in that message, so I did not regenerate or redeploy the project.")
            self.persist_state(session)
            return session
        session.spec = DeploymentSpec(**spec_data)
        session.resources["chat_answers"] = {**answers, **session.spec.model_dump(mode="json")}
        session.resources["last_update_fingerprint"] = fingerprint
        session.resources.pop("auto_deploy_after_seconds", None)
        session.resources.pop("auto_deploy_scheduled", None)
        session.approved = False
        self._add_chat_message(
            session,
            "assistant",
            "I understood this as an update to the existing project. I will regenerate Terraform, validate the diff, update GitHub, then rerun compliance.",
        )
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="requirements",
                severity="info",
                status=DeploymentStatus.customizing,
                message="Existing project update request accepted from chat.",
                details={"message": message, "updates": answers},
            )
        )
        session = await self.provision(session)
        if session.status == DeploymentStatus.failed:
            return session
        session = await self.run_compliance(session)
        self._add_chat_message(session, "assistant", "Update is ready. Review the GitHub diff and type `approve` to deploy the updated project.")
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
        return await self.deploy(session)

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
        create_ssh_key = bool(session.spec and self._requires_ssh_key(session.spec.description))
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="deployer",
                severity="info",
                status=DeploymentStatus.deploying,
                message=(
                    "Starting EC2 httpd test with requested SSH key artifact."
                    if create_ssh_key
                    else "Starting EC2 httpd test with SSM-first access, security group, instance, and user-data install."
                ),
            )
        )
        resources = self.ec2_httpd.create(session.id, project_name, create_ssh_key=create_ssh_key)
        private_key_pem = resources.pop("private_key_pem", None)
        if private_key_pem:
            attachment = self.archive.put_text_artifact(
                session,
                f"{project_name}-{session.id[:8]}.pem",
                private_key_pem,
                content_type="application/x-pem-file",
            )
            resources["ssh_private_key_attachment"] = attachment
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
        if session.resources.get("halt_requested"):
            self._add_chat_message(session, "assistant", "Destroy is halted by user request. Send `resume` to continue.")
            self.persist_state(session)
            return session
        session.add_event(
            DeploymentEvent(
                session_id=session.id,
                agent="destroyer",
                severity="warning",
                status=DeploymentStatus.remediating,
                message="Destroy started for resources tracked by this project.",
            )
        )
        if session.spec and session.repository_url:
            result = self._run_terraform_destroy(session)
            session.resources["terraform_destroy"] = result
            if not result.get("destroyed"):
                session.add_event(
                    DeploymentEvent(
                        session_id=session.id,
                        agent="destroyer",
                        severity="error",
                        status=DeploymentStatus.failed,
                        message="Terraform destroy failed for tracked project resources.",
                        details=result,
                    )
                )
                self._add_chat_message(session, "assistant", self._describe_deploy_issue(result))
                self.persist_state(session)
                return session
        elif session.resources.get("ec2_httpd"):
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

    def _feedback(self, session: DeploymentSession, agent: str, message: str) -> None:
        feedback = list(session.resources.get("agent_feedback", []))
        feedback.append({"agent": agent, "message": message})
        session.resources["agent_feedback"] = feedback[-50:]

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
                r"(?:owner|owned by|team|application owner)\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z0-9 ._-]{1,60}?)(?=\s+(?:cost\s*cent(?:er|re)|cost_center|charge\s*code|billing\s*code|cc\s*\d|cc\d|in\s+[a-z]{2}-[a-z]+-\d\b|dev\b|test\b|stage\b|prod\b|wait\b|manual\b|skip\b|direct\b)|[,.]|$)",
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
            value = cost_center.group(1).replace(" ", "").upper()
            answers["cost_center"] = f"CC{value}" if value.isdigit() and re.search(r"\bcc\s*\d", message, re.I) else value
        elif re.fullmatch(r"\s*(?:cc)?\s*\d{3,}\s*", message, re.I):
            answers["cost_center"] = message.strip().upper().replace(" ", "")
        region = re.search(r"\b[a-z]{2}-[a-z]+-\d\b", message)
        if region:
            answers["region"] = region.group(0)
        instance_type = re.search(r"\b(?:instance\s*type|size)\s*(?:is|:|-)?\s*([a-z][0-9][a-z]?\.[a-z0-9]+)\b", message, re.I)
        if not instance_type:
            instance_type = re.search(r"\b(t[234][a-z]?\.[a-z0-9]+|m[567][a-z]?\.[a-z0-9]+|c[567][a-z]?\.[a-z0-9]+)\b", message, re.I)
        if instance_type:
            answers["instance_type"] = instance_type.group(1).lower()
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
        elif "vpc" in lower or "subnet" in lower:
            answers["workload_type"] = "vpc-baseline"
        elif any(token in lower for token in ("lambda", "api gateway", "apigateway", "serverless")):
            answers["workload_type"] = "s3-lambda-api"
        elif "rds" in lower:
            answers["workload_type"] = "rds"
        elif "dynamodb" in lower:
            answers["workload_type"] = "dynamodb"
        elif "ecs" in lower:
            answers["workload_type"] = "ecs"
        elif "eks" in lower:
            answers["workload_type"] = "eks"
        return answers

    def _apply_pending_answer(
        self,
        session: DeploymentSession,
        answers: dict[str, str],
        message: str,
        extracted: dict[str, str],
    ) -> None:
        pending = session.resources.get("pending_answer_key")
        value = message.strip()
        if not pending or not value or extracted.get(str(pending)):
            return
        if str(pending).startswith("service_"):
            if len(value.split()) <= 60:
                answers[str(pending)] = value
                session.resources.pop("pending_answer_key", None)
            return
        if len(value.split()) <= 6:
            answers[str(pending)] = value
            session.resources.pop("pending_answer_key", None)

    def _is_update_request(self, message: str, extracted: dict[str, str]) -> bool:
        lower = message.lower()
        update_words = ("update", "change", "modify", "set ", "increase", "decrease", "resize", "make it")
        return any(word in lower for word in update_words) or bool(extracted.get("instance_type"))

    def _extract_project_name(self, message: str) -> str | None:
        patterns = [
            r"\bproject\s+name\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\b(?:project|application|app)\s+(?:called|named)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\b(?:project|application|app)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
            r"\b(?:ec2|server|instance)\s+(?:called|named)\s+([A-Za-z][A-Za-z0-9-_]{2,})",
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

    def _is_stop_command(self, message: str) -> bool:
        lower = message.strip().lower()
        return any(term in lower for term in {"stop", "halt", "cancel deployment", "pause deployment", "abort"})

    def _is_resume_command(self, message: str) -> bool:
        lower = message.strip().lower()
        return any(term in lower for term in {"resume", "continue", "restart deployment", "unpause"})

    def _is_greeting(self, message: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", message.lower()).strip()
        return normalized in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}

    def _extract_approval_mode(self, message: str) -> str | None:
        lower = message.lower()
        if any(phrase in lower for phrase in ("skip approval", "no approval", "without approval", "go directly", "direct deploy", "deploy directly")):
            return "skip"
        if any(phrase in lower for phrase in ("wait for approval", "manual approval", "do not auto", "don't auto", "approval required")):
            return "manual"
        return None

    def _implementation_plan(self, spec: DeploymentSpec) -> str:
        if spec.workload_type == "ec2-httpd":
            workload = "an EC2 instance with HTTPD, user data bootstrap, and a security group allowing HTTP on port 80"
        elif spec.workload_type == "s3-bucket":
            workload = "an S3 bucket with public access blocked, versioning enabled, encryption, and required tags"
        else:
            workload = f"a `{spec.workload_type}` workload with Terraform and company baseline controls"
        return (
            f"I have the requirements for `{spec.name}`. Implementation plan: create {workload} in "
            f"`{spec.region}` for `{spec.environment}`, generate Terraform and docs, validate the code, "
            "push only valid changes to GitHub, run compliance, then handle approval/deployment."
        )

    def _requires_ssh_key(self, description: str) -> bool:
        lower = description.lower()
        return any(term in lower for term in ("pem", "ssh key", "key pair", "private key"))

    def _missing_question(self, key: str, answers: dict[str, str] | None = None) -> str:
        answers = answers or {}
        workload = answers.get("workload_type", "")
        questions = {
            "name": "What project name should I use?",
            "description": (
                "Please describe what you want this project to build "
                "(for example: service type, traffic pattern, private/public, and expected scale)."
            ),
            "owner": "Who is the owner email or team for this project?",
            "cost_center": "What cost center should I tag on the resources?",
        }
        if key == "description" and workload == "ec2-httpd":
            return "Describe your EC2 app (public/private, expected traffic, and instance size if known)."
        if key == "description" and workload == "s3-bucket":
            return "Describe your S3 use case (documents/logs/backups, retention, and access pattern)."
        return questions[key]

    def _service_requirements_question(self, answers: dict[str, str], message: str) -> dict[str, str] | None:
        lower = f"{message.lower()} {answers.get('description', '').lower()}"
        prompts = [
            ("lambda", "service_lambda_requirements", "For Lambda, what runtime, timeout, and trigger type do you want (API, schedule, SQS, or S3)?"),
            ("ec2", "service_ec2_requirements", "For EC2, should it be public or private, and what instance size/OS do you prefer?"),
            ("s3", "service_s3_requirements", "For S3, do you need versioning, lifecycle retention, and encryption with KMS or AES256?"),
            ("rds", "service_rds_requirements", "For RDS, what engine (postgres/mysql), expected size, backup retention, and public/private access?"),
            ("dynamodb", "service_dynamodb_requirements", "For DynamoDB, what table keys do you need and expected read/write capacity pattern?"),
            ("vpc", "service_vpc_requirements", "For VPC, how many AZs/public/private subnets and do you need NAT gateways?"),
            ("ecs", "service_ecs_requirements", "For ECS, do you want Fargate or EC2 launch type, desired task count, and ALB exposure?"),
            ("eks", "service_eks_requirements", "For EKS, what node sizing, scaling limits, and public/private endpoint preference?"),
            ("sns", "service_sns_requirements", "For SNS, what topic type and subscription endpoints (email/http/sqs) do you need?"),
            ("sqs", "service_sqs_requirements", "For SQS, do you need standard or FIFO queue, and what visibility timeout / DLQ policy?"),
            ("apigateway", "service_apigw_requirements", "For API Gateway, should it be public or private, and do you need custom domain + auth?"),
        ]
        for token, key, question in prompts:
            if token in lower:
                return {"id": key, "question": question}
        return None
