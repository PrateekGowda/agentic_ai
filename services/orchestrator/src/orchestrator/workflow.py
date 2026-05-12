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
            self._add_chat_message(session, "assistant", f"{message} Check deployment logs for the CodeBuild failure details.")
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
        set -euo pipefail
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
        terraform apply -auto-approve -input=false
        APPLY_EXIT=$?
        if [ "$APPLY_EXIT" -ne 0 ]; then
          python3 - <<'PY'
import json, os
payload = {"verified": False, "stage": "terraform_apply", "error": "terraform apply failed"}
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

    async def chat(self, session: DeploymentSession, request: RequirementMessage) -> DeploymentSession:
        self._add_chat_message(session, "user", request.message)
        self._remember(session, "USER", request.message, {"stage": "chat"})
        if self._is_greeting(request.message):
            message = (
                "Hello, I am AI Agent for IaC orchestration. What can I do for you? "
                "You can ask me to create or update AWS infrastructure, for example: "
                "`create an EC2 web server in dev owner platform team cc1001`, "
                "`create an encrypted S3 bucket`, or `update instance type to t3.small`."
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
        approval_mode = self._extract_approval_mode(request.message)
        if approval_mode:
            session.resources["approval_mode"] = approval_mode
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
            if session.status == DeploymentStatus.succeeded:
                self._add_chat_message(session, "assistant", "Deployment is complete and AWS resources were verified. Review the repository, artifacts, and resource details on the right.")
            else:
                self._add_chat_message(session, "assistant", "Deployment did not verify successfully. Review the terminal logs and CodeBuild link in the deployment details.")
            return session

        answers = dict(session.resources.get("chat_answers", {}))
        self._merge_answers(answers, request.answers)
        extracted = self._extract_chat_answers(request.message)
        self._apply_pending_answer(session, answers, request.message, extracted)
        self._merge_answers(answers, extracted)
        session.resources["chat_answers"] = answers

        if session.spec and self._is_update_request(request.message, extracted):
            session = await self._update_existing_project(session, extracted, request.message)
            return session

        if session.spec and session.status == DeploymentStatus.succeeded:
            message = (
                "This project is already deployed and verified. Tell me what to update, for example "
                "`update instance type to t3.small` or `change region to us-west-2`, and I will prepare a new GitHub diff."
            )
            self._add_chat_message(session, "assistant", message)
            session.add_event(
                DeploymentEvent(
                    session_id=session.id,
                    agent="requirements",
                    severity="info",
                    status=session.status,
                    message="No update requested for an already deployed project.",
                )
            )
            self.persist_state(session)
            return session

        missing = [key for key in ("name", "description", "owner", "cost_center") if not answers.get(key)]
        if missing:
            question = self._missing_question(missing[0])
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

        session = await self.gather_requirements(session, RequirementMessage(message=request.message, answers=answers))
        if not session.spec:
            return session
        self._add_chat_message(
            session,
            "assistant",
            self._implementation_plan(session.spec),
        )
        session = await self.provision(session)
        if session.status == DeploymentStatus.failed:
            return session
        session = await self.run_compliance(session)
        if session.status == DeploymentStatus.awaiting_approval:
            mode = session.resources.get("approval_mode", "auto")
            if mode == "skip":
                session.approved = True
                self._add_chat_message(session, "assistant", "Approval was skipped by request. Deployment is starting now.")
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
                    self._add_chat_message(session, "assistant", "Deployment is complete and AWS resources were verified. Review the repository, artifacts, and resource details on the right.")
                else:
                    self._add_chat_message(session, "assistant", "Deployment did not verify successfully. Review the terminal logs and CodeBuild link in the deployment details.")
                return session
            if mode == "manual":
                approval_message = (
                    "Architecture, Terraform, and compliance checks are ready. "
                    "I will wait for approval. Type `approve` when you want to deploy."
                )
            else:
                session.resources["auto_deploy_after_seconds"] = 180
                approval_message = (
                    "Architecture, Terraform, and compliance checks are ready. "
                    "Review the GitHub links and type `approve` to deploy now. "
                    "If there is no response, I will auto-deploy in about 3 minutes."
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

    def _missing_question(self, key: str) -> str:
        questions = {
            "name": "What project name should I use?",
            "description": "Please describe what you want this project to build.",
            "owner": "Who is the owner email or team for this project?",
            "cost_center": "What cost center should I tag on the resources?",
        }
        return questions[key]
