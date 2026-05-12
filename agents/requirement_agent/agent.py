from typing import Any
import re

from common.llm import ask_llm_json

REQUIRED_FIELDS = {
    "name": "Application or platform name",
    "description": "Short description",
    "owner": "Owner email or team",
    "cost_center": "Cost center",
}


def handle_requirement_message(payload: dict[str, Any]) -> dict[str, Any]:
    answers = dict(payload.get("answers", {}))
    original_message = str(payload.get("message", ""))
    message = original_message.lower()
    llm_answers = _extract_answers_with_llm(original_message, answers)
    answers.update({key: value for key, value in llm_answers.items() if value and not answers.get(key)})
    answers.update({key: value for key, value in _extract_answers(original_message).items() if not answers.get(key)})
    _normalize_answers(answers)
    missing = [label for key, label in REQUIRED_FIELDS.items() if not answers.get(key)]

    if missing:
        return {
            "message": "I need a few details before generating infrastructure.",
            "data": {
                "complete": False,
                "questions": [
                    {
                        "id": key,
                        "label": label,
                        "required": True,
                    }
                    for key, label in REQUIRED_FIELDS.items()
                    if not answers.get(key)
                ],
            },
        }

    spec = {
        "name": answers["name"],
        "description": answers["description"],
        "cloud": "aws",
        "region": answers.get("region", "us-east-1"),
        "environment": answers.get("environment", "dev"),
        "workload_type": answers.get(
            "workload_type",
            _infer_workload(message),
        ),
        "owner": answers["owner"],
        "cost_center": answers["cost_center"],
        "compliance_profile": answers.get("compliance_profile", "baseline"),
        "github_visibility": answers.get("github_visibility", "private"),
        "tags": {
            "Environment": answers.get("environment", "dev"),
            "Owner": answers["owner"],
            "CostCenter": answers["cost_center"],
            "ManagedBy": "agentcore-multi-agent-deployer",
        },
        "standards_source": answers.get("standards_source"),
    }

    return {
        "message": "Requirements are complete and ready for Terraform generation.",
        "data": {"complete": True, "spec": spec},
    }


def _normalize_answers(answers: dict[str, Any]) -> None:
    if answers.get("environment") not in {"dev", "test", "stage", "prod"}:
        answers["environment"] = "dev"
    if answers.get("compliance_profile") not in {"baseline", "regulated"}:
        answers["compliance_profile"] = "baseline"
    if answers.get("github_visibility") not in {"private", "internal", "public"}:
        answers["github_visibility"] = "private"
    if answers.get("workload_type") not in {"s3-lambda-api", "vpc-baseline", "ec2-httpd", "s3-bucket"}:
        answers["workload_type"] = _infer_workload(str(answers.get("description", "")))


def _extract_answers_with_llm(message: str, existing: dict[str, Any]) -> dict[str, str]:
    result = ask_llm_json(
        "You extract infrastructure requirements from natural language. Return only JSON.",
        (
            "Extract fields for an AWS Terraform request. Valid workload_type values are "
            "ec2-httpd, s3-bucket, s3-lambda-api, vpc-baseline. Include only fields you are confident about. "
            "Fields: name, description, owner, cost_center, region, environment, workload_type, "
            "github_visibility, compliance_profile.\n"
            f"Existing answers: {existing}\n"
            f"User message: {message}"
        ),
    )
    if not result:
        return {}
    return {str(key): str(value) for key, value in result.items() if value is not None}


def _infer_workload(message: str) -> str:
    if "ec2" in message or "httpd" in message:
        return "ec2-httpd"
    if "s3 bucket" in message or "bucket" in message:
        return "s3-bucket"
    return "s3-lambda-api"


def _extract_answers(message: str) -> dict[str, str]:
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
    environment = re.search(r"\b(dev|test|stage|prod)\b", message, re.I)
    if environment:
        answers["environment"] = environment.group(1).lower()
    name = _extract_project_name(message)
    if name:
        answers["name"] = name
    elif "s3" in lower and "bucket" in lower:
        answers["name"] = "chat-s3-bucket"
    if message.strip() and len(message.split()) > 3:
        answers["description"] = message.strip()[:500]
    answers["workload_type"] = _infer_workload(lower)
    return answers


def _extract_project_name(message: str) -> str | None:
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
