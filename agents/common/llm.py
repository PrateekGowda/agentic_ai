from __future__ import annotations

import json
import os
from typing import Any


def llm_enabled() -> bool:
    return os.getenv("AGENT_LLM_ENABLED", "false").lower() in {"1", "true", "yes"}


def _converse(system_prompt: str, user_prompt: str, max_tokens: int = 4000, temperature: float = 0.1) -> str:
    import boto3
    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    response = client.converse(
        modelId=os.getenv("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0"),
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return "".join(
        item.get("text", "")
        for item in response.get("output", {}).get("message", {}).get("content", [])
    )


def ask_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any] | None:
    if not llm_enabled():
        return None
    try:
        text = _converse(system_prompt, user_prompt)
        return _parse_json_object(text)
    except Exception:
        return None


def ask_llm_text(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str | None:
    """Return free-form LLM text. Used by the general-purpose fallback agent."""
    if not llm_enabled():
        return None
    try:
        return _converse(system_prompt, user_prompt, max_tokens=max_tokens, temperature=0.7).strip() or None
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
