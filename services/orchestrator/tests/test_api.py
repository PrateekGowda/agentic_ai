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
