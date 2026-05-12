from typing import Any


def run_deployment_step(payload: dict[str, Any]) -> dict[str, Any]:
    approved = payload.get("approved", False)
    findings = payload.get("findings", [])
    blocking = [finding for finding in findings if finding.get("blocking")]

    if blocking:
        return {
            "message": "Deployment is blocked by compliance findings.",
            "data": {
                "status": "blocked",
                "remediation_summary": "Fix blocking policy findings before apply.",
            },
        }

    if not approved:
        return {
            "message": "Terraform plan is ready and requires approval before apply.",
            "data": {"status": "awaiting_approval"},
        }

    return {
        "message": "Deployment completed successfully.",
        "data": {
            "status": "succeeded",
            "documentation": {
                "architecture": "ARCHITECTURE.md",
                "operations": "OPERATIONS.md",
                "compliance": "COMPLIANCE.md",
            },
        },
    }
