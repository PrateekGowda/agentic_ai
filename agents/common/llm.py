from __future__ import annotations

import json
import os
from typing import Any


def llm_enabled() -> bool:
    return os.getenv("AGENT_LLM_ENABLED", "false").lower() in {"1", "true", "yes"}


def ask_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
    if not llm_enabled():
        return None
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        response = client.converse(
            modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"),
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": 4000, "temperature": 0.1},
        )
        text = "".join(
            item.get("text", "")
            for item in response.get("output", {}).get("message", {}).get("content", [])
        )
        return _parse_json_object(text)
    except Exception:
        return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    candidates = [text.strip()]
    if "```" in text:
        for part in text.split("```"):
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned.removeprefix("json").strip()
            candidates.append(cleaned)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(candidate[start : end + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
    return None
