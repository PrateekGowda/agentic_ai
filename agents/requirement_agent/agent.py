from typing import Any
import re

from common.llm import ask_llm_json

REQUIRED_FIELDS = {
    "name": "Application or platform name",
    "description": "Short description",
    "owner": "Owner email or team",
    "cost_center": "Cost center",
}

SERVICE_PROMPTS = {
    "lambda": "For Lambda, share runtime, trigger type (API/S3/SQS/EventBridge), timeout, and memory.",
    "ec2": "For EC2, share instance size, OS, public/private access, and expected traffic.",
    "s3": "For S3, share versioning, retention/lifecycle, and encryption preference (AES256/KMS).",
    "rds": "For RDS, share engine, size, backup retention, and public/private access.",
    "dynamodb": "For DynamoDB, share table keys and throughput pattern (on-demand/provisioned).",
    "vpc": "For VPC, share AZ count, public/private subnet layout, and NAT requirements.",
    "ecs": "For ECS, share launch type (Fargate/EC2), desired count, and load balancer needs.",
    "eks": "For EKS, share node sizing, scaling limits, and endpoint exposure preferences.",
    "apigateway": "For API Gateway, share auth type, custom domain requirement, and private/public exposure.",
}


def handle_requirement_message(payload: dict[str, Any]) -> dict[str, Any]:
    answers = dict(payload.get("answers", {}))
    original_message = str(payload.get("message", ""))
    message = original_message.lower()
    answers.update({key: value for key, value in _extract_answers(original_message).items() if not answers.get(key)})
    if any(not answers.get(key) for key in REQUIRED_FIELDS):
        llm_answers = _extract_answers_with_llm(original_message, answers)
        answers.update({key: value for key, value in llm_answers.items() if value and not answers.get(key)})
    _normalize_answers(answers)
    missing = [label for key, label in REQUIRED_FIELDS.items() if not answers.get(key)]

    if missing:
        service_prompt = _service_prompt(message)
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
                ] + (
                    [
                        {
                            "id": "service_details",
                            "label": service_prompt,
                            "required": False,
                        }
                    ]
                    if service_prompt
                    else []
                ),
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
    supported = {
        "s3-lambda-api",
        "vpc-baseline",
        "ec2-httpd",
        "s3-bucket",
        "lambda",
        "rds",
        "dynamodb",
        "ecs",
        "eks",
        "apigateway",
        "sns",
        "sqs",
    }
    if not answers.get("workload_type"):
        answers["workload_type"] = _infer_workload(str(answers.get("description", "")))
    elif str(answers.get("workload_type")) not in supported:
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
    if "eks" in message:
        return "eks"
    if "ecs" in message:
        return "ecs"
    if "rds" in message or "postgres" in message or "mysql" in message:
        return "rds"
    if "dynamodb" in message:
        return "dynamodb"
    if "apigateway" in message or "api gateway" in message:
        return "apigateway"
    if "sns" in message:
        return "sns"
    if "sqs" in message:
        return "sqs"
    if "ec2" in message or "httpd" in message:
        return "ec2-httpd"
    if "vpc" in message or "subnet" in message:
        return "vpc-baseline"
    if "lambda" in message or "api gateway" in message or "serverless" in message:
        return "s3-lambda-api"
    if "s3 bucket" in message or "bucket" in message:
        return "s3-bucket"
    return "s3-lambda-api"


def _service_prompt(message: str) -> str | None:
    lower = message.lower()
    for token, prompt in SERVICE_PROMPTS.items():
        if token in lower:
            return prompt
    return None


def _extract_answers(message: str) -> dict[str, str]:
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
