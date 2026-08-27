"""Local schema-constrained candidate provider for unified editor commands.

The model may classify one command and quote exact evidence. It never receives
or returns edit UUIDs, paths, render parameters, plan hashes, or conflict
choices; deterministic command planning remains authoritative.
"""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


GroundedCommandProvider = Callable[[str, Mapping[str, Any]], object]


def get_default_grounded_command_provider() -> GroundedCommandProvider | None:
    enabled = os.getenv("AI_PHOTO_USE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    return call_ollama_grounded_command_candidate


def call_ollama_grounded_command_candidate(
    raw_instruction: str,
    candidate_schema: Mapping[str, Any],
) -> object:
    instruction = str(raw_instruction)
    if not instruction.strip():
        raise ValueError("Grounded command instruction must not be empty")
    command_types = [
        str(item)
        for item in candidate_schema.get("allowed_command_types", ())
    ]
    if not command_types:
        raise ValueError("Grounded command schema has no command types")

    url = os.getenv(
        "AI_PHOTO_OLLAMA_URL",
        "http://127.0.0.1:11434/api/generate",
    )
    model = os.getenv("AI_PHOTO_OLLAMA_MODEL", "llama3.2:3b")
    timeout = _positive_float_env("AI_PHOTO_OLLAMA_TIMEOUT", 8.0)
    num_predict = _positive_int_env("AI_PHOTO_OLLAMA_NUM_PREDICT", 256)
    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command_type": {"type": "string", "enum": command_types},
            "evidence": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["command_type", "evidence", "confidence"],
    }
    payload = {
        "model": model,
        "prompt": _candidate_prompt(instruction, command_types),
        "stream": False,
        "format": response_schema,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (socket.timeout, TimeoutError) as exc:
        raise TimeoutError("Grounded command provider timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError("Grounded command provider timed out") from exc
        raise
    if not isinstance(response_payload, Mapping):
        raise ValueError("Ollama response must be a JSON object")
    response_text = response_payload.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("Ollama response did not contain a command candidate")
    candidate = json.loads(response_text)
    if not isinstance(candidate, Mapping):
        raise ValueError("Grounded command candidate must be a JSON object")
    return dict(candidate)


def _candidate_prompt(instruction: str, command_types: list[str]) -> str:
    allowed = ", ".join(command_types)
    return f"""
Classify exactly one user request for a photo editor. Return one JSON object
and no markdown. Allowed command_type values: {allowed}.

Use edit_prompt for an ordinary visual edit, manual_adjust only for an explicit
named parameter with an exact numeric operation, apply_style for one named
style, photo_git_merge for combining two versions, and photo_git_revert for
selectively undoing a version contribution. Use unknown for missing or mixed
actions. Copy the exact words that support the command into evidence. Do not
invent UUIDs, paths, parameters, style names, version numbers, APIs, conflict
answers, or actions not present in the instruction.

USER_INSTRUCTION:
{instruction}
""".strip()


def _positive_float_env(name: str, fallback: float) -> float:
    value = float(os.getenv(name, str(fallback)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def _positive_int_env(name: str, fallback: int) -> int:
    value = int(os.getenv(name, str(fallback)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


__all__ = [
    "GroundedCommandProvider",
    "call_ollama_grounded_command_candidate",
    "get_default_grounded_command_provider",
]
