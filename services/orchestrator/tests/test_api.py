from fastapi.testclient import TestClient

from orchestrator.main import app


def test_full_happy_path_without_external_credentials():
    client = TestClient(app)

    session = client.post("/sessions").json()
    session_id = session["id"]

    requirements = client.post(
        f"/sessions/{session_id}/requirements",
        json={
            "message": "deploy",
            "answers": {
                "name": "demo-api",
                "description": "demo workload",
                "owner": "platform@example.com",
                "cost_center": "CC-1",
                "region": "us-east-1",
                "environment": "dev",
            },
        },
    ).json()
    assert requirements["status"] == "customizing"

    provisioned = client.post(f"/sessions/{session_id}/provision").json()
    assert provisioned["repository_url"].endswith("/demo-api-dev-infra")

    compliance = client.post(f"/sessions/{session_id}/compliance").json()
    assert compliance["status"] == "awaiting_approval"

    client.post(f"/sessions/{session_id}/approve")
    deployed = client.post(f"/sessions/{session_id}/deploy").json()
    assert deployed["status"] == "succeeded"
    assert deployed["architecture_doc_url"].endswith("/ARCHITECTURE.md")


def test_automatic_run_completes_happy_path_without_external_credentials():
    client = TestClient(app)
    session = client.post("/sessions").json()

    result = client.post(
        f"/sessions/{session['id']}/run",
        json={
            "message": "deploy automatically",
            "answers": {
                "name": "auto-api",
                "description": "automatic workflow",
                "owner": "platform@example.com",
                "cost_center": "CC-2",
                "region": "us-east-1",
                "environment": "dev",
            },
        },
    ).json()

    assert result["status"] == "succeeded"
    assert result["repository_url"].endswith("/auto-api-dev-infra")
    assert [event["agent"] for event in result["events"]] == [
        "requirements",
        "provisioner",
        "compliance",
        "deployer",
        "deployer",
    ]
