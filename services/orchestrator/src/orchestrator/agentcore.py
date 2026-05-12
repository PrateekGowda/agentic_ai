from collections.abc import Callable
from typing import Any


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

        # Wire this to the Bedrock AgentCore Runtime SDK once the runtime is deployed.
        # Keeping the boundary explicit avoids leaking provider details into agents.
        return self.fallback(payload | {"agentcore_runtime_arn": self.runtime_arn})
