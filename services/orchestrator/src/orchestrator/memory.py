from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3


class AgentCoreMemory:
    def __init__(self, memory_id: str | None, region: str) -> None:
        self.memory_id = memory_id
        self.client = boto3.client("bedrock-agentcore", region_name=region) if memory_id else None

    def remember(self, session_id: str, role: str, text: str, metadata: dict[str, str] | None = None) -> None:
        if not self.client or not self.memory_id or not text.strip():
            return
        self.client.create_event(
            memoryId=self.memory_id,
            actorId="agentcore-deployer-user",
            sessionId=self._session_id(session_id),
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "content": {"text": text[:100000]},
                        "role": role,
                    }
                }
            ],
            metadata={
                key: {"stringValue": value[:256]}
                for key, value in (metadata or {}).items()
                if value is not None
            },
        )

    def load_session_context(self, session_id: str) -> list[str]:
        if not self.client or not self.memory_id:
            return []
        response = self.client.list_events(
            memoryId=self.memory_id,
            actorId="agentcore-deployer-user",
            sessionId=self._session_id(session_id),
            includePayloads=True,
        )
        messages: list[str] = []
        for event in response.get("events", [])[-10:]:
            for item in event.get("payload", []):
                text = item.get("conversational", {}).get("content", {}).get("text")
                if text:
                    messages.append(text)
        return messages

    def _session_id(self, session_id: str) -> str:
        return session_id.replace("_", "-")[:100]
