from typing import Any


def run_compliance_checks(payload: dict[str, Any]) -> dict[str, Any]:
    spec = payload["spec"]
    findings: list[dict[str, Any]] = []

    required_tags = {"Environment", "Owner", "CostCenter", "ManagedBy"}
    missing_tags = sorted(required_tags - set(spec.get("tags", {})))
    if missing_tags:
        findings.append(
            {
                "id": "TAG-001",
                "tool": "opa",
                "severity": "high",
                "title": "Missing mandatory tags",
                "resource": "module.workload",
                "remediation": f"Add tags: {', '.join(missing_tags)}",
                "blocking": True,
            }
        )

    if spec.get("environment") == "prod" and spec.get("compliance_profile") == "baseline":
        findings.append(
            {
                "id": "PROFILE-001",
                "tool": "opa",
                "severity": "medium",
                "title": "Production should use regulated compliance profile",
                "resource": "deployment_spec",
                "remediation": "Set compliance_profile to regulated for production.",
                "blocking": False,
            }
        )

    return {
        "message": "Compliance checks completed.",
        "data": {
            "findings": findings,
            "blocking": any(finding["blocking"] for finding in findings),
        },
    }
