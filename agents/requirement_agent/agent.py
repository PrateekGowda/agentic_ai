from typing import Any

REQUIRED_FIELDS = {
    "name": "Application or platform name",
    "description": "Short description",
    "owner": "Owner email or team",
    "cost_center": "Cost center",
}


def handle_requirement_message(payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("answers", {})
    message = str(payload.get("message", "")).lower()
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
            "ec2-httpd" if "ec2" in message or "httpd" in message else "s3-lambda-api",
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
