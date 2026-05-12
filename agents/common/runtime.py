"""HTTP runtime entrypoint for Amazon Bedrock AgentCore Runtime."""

import os
import time
from typing import Any
import base64
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


def _parse_invocation_body(raw_body: bytes) -> dict[str, Any]:
    raw_text = raw_body.decode("utf-8") if raw_body else "{}"
    candidates = [raw_text, raw_text.strip().strip("'")]
    try:
        decoded = base64.b64decode(raw_text, validate=True).decode("utf-8")
        candidates.append(decoded)
    except Exception:
        pass

    for candidate in candidates:
        try:
            parsed = json.loads(candidate or "{}")
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise HTTPException(status_code=400, detail="Invocation body must be a JSON object.")


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
    body = _parse_invocation_body(raw_body)
    payload = body.get("input", body)
    return agent(payload)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
