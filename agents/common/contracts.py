from typing import Any, TypedDict


class AgentResponse(TypedDict, total=False):
    message: str
    data: dict[str, Any]
    events: list[dict[str, Any]]
