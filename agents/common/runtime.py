"""HTTP runtime entrypoint for Amazon Bedrock AgentCore Runtime."""

import os
import time
from typing import Any
import json

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from compliance_agent import run_compliance_checks
from deployer_agent import run_deployment_step
from provisioner_agent import provision_repository_payload
from requirement_agent import handle_requirement_message

AGENTS = {
    "requirements": handle_requirement_message,
    "provisioner": provision_repository_payload,
    "deployer": run_deployment_step,
    "compliance": run_compliance_checks,
}

app = FastAPI(title="AgentCore Deployer Agent Runtime")


@app.get("/ping")
def ping() -> dict[str, Any]:
    return {"status": "Healthy", "time_of_last_update": int(time.time())}


@app.post("/invocations")
async def invoke(request: Request) -> dict[str, Any]:
    agent_name = os.getenv("AGENT_NAME", "requirements")
    agent = AGENTS.get(agent_name)
    if not agent:
        raise HTTPException(status_code=400, detail=f"Unknown AGENT_NAME: {agent_name}")
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invocation body must be JSON.") from exc
    payload = body.get("input", body)
    return agent(payload)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
