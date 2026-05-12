from collections.abc import Callable
import json
from uuid import uuid4
from typing import Any

import boto3


class AgentCoreRuntimeClient:
    """Thin adapter around AgentCore runtime calls.

    The MVP keeps local Python agents as a fallback so development can proceed
    before runtime ARNs are provisioned by `iac/platform`.
    """

    def __init__(self, runtime_arn: str | None, fallback: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.runtime_arn = runtime_arn
        self.fallback = fallback

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.runtime_arn:
            return self.fallback(payload)

        client = boto3.client("bedrock-agentcore")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=f"session-{uuid4()}",
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = response["response"].read()
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body)
