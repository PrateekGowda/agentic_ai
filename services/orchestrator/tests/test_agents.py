from compliance_agent import run_compliance_checks
from deployer_agent import run_deployment_step
from requirement_agent import handle_requirement_message


def test_requirement_agent_returns_questions_when_required_fields_missing():
    result = handle_requirement_message({"message": "deploy", "answers": {"name": "demo"}})

    assert result["data"]["complete"] is False
    assert {question["id"] for question in result["data"]["questions"]} == {
        "description",
        "owner",
        "cost_center",
    }


def test_requirement_agent_builds_normalized_spec():
    result = handle_requirement_message(
        {
            "message": "deploy",
            "answers": {
                "name": "demo",
                "description": "demo workload",
                "owner": "platform@example.com",
                "cost_center": "CC-1",
            },
        }
    )

    assert result["data"]["complete"] is True
    assert result["data"]["spec"]["tags"]["ManagedBy"] == "agentcore-multi-agent-deployer"


def test_compliance_blocks_missing_tags():
    result = run_compliance_checks(
        {
            "spec": {
                "environment": "dev",
                "compliance_profile": "baseline",
                "tags": {"Owner": "platform@example.com"},
            }
        }
    )

    assert result["data"]["blocking"] is True
    assert result["data"]["findings"][0]["id"] == "TAG-001"


def test_deployer_requires_approval_before_apply():
    result = run_deployment_step({"approved": False, "findings": []})

    assert result["data"]["status"] == "awaiting_approval"
