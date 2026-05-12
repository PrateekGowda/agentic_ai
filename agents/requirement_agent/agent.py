from typing import Any
import re

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
    answers.update({key: value for key, value in _extract_answers(original_message).items() if not answers.get(key)})
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


def _infer_workload(message: str) -> str:
    if "ec2" in message or "httpd" in message:
        return "ec2-httpd"
    if "s3 bucket" in message or "bucket" in message:
        return "s3-bucket"
    return "s3-lambda-api"


def _extract_answers(message: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
    if email:
        answers["owner"] = email.group(0)
    cost_center = re.search(r"(?:cost\s*center|cost_center|cc)[:\s-]*([A-Za-z]{0,4}-?\d{3,})", message, re.I)
    if cost_center:
        answers["cost_center"] = cost_center.group(1).upper()
    region = re.search(r"\b[a-z]{2}-[a-z]+-\d\b", message)
    if region:
        answers["region"] = region.group(0)
    environment = re.search(r"\b(dev|test|stage|prod)\b", message, re.I)
    if environment:
        answers["environment"] = environment.group(1).lower()
    name = re.search(r"(?:project|application|app|bucket)\s+name\s*(?:is|:)?\s*([A-Za-z0-9-_]+)", message, re.I)
    if name:
        answers["name"] = name.group(1)
    elif "s3" in message.lower() and "bucket" in message.lower():
        answers["name"] = "chat-s3-bucket"
    if message.strip():
        answers["description"] = message.strip()[:500]
    answers["workload_type"] = _infer_workload(message.lower())
    return answers
