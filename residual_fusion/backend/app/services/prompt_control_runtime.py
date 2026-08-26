from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any
import urllib.request

from app.services.prompt_control import (
    PROMPT_CONTROL_JSON_SCHEMA,
    PromptControl,
    apply_rule_based_runtime_guard,
    build_prompt_control_prompt,
    parse_prompt_control_response,
    resolve_prompt_control_rule_based,
)


DEFAULT_OLLAMA_MODEL = "ai-photo-prompt-control:exp007-v1"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


@dataclass(frozen=True)
class PromptControlResolution:
    control: PromptControl
    parser_source: str
    model: str | None
    raw_response: str | None
    fallback_reason: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control.to_dict(),
            "parser_source": self.parser_source,
            "model": self.model,
            "raw_response": self.raw_response,
            "fallback_reason": self.fallback_reason,
            "metadata": self.metadata,
        }


class OllamaPromptControlResolver:
    def __init__(
        self,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 90.0,
        keep_alive: str = "15m",
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout
        self.keep_alive = keep_alive

    def resolve(self, prompt: str) -> PromptControlResolution:
        if not _llm_enabled():
            return self._fallback(prompt, "AI_PHOTO_USE_LLM disabled")

        started = time.perf_counter()
        try:
            payload = {
                "model": self.model,
                "prompt": build_prompt_control_prompt(prompt),
                "stream": False,
                "format": PROMPT_CONTROL_JSON_SCHEMA,
                "options": {"temperature": 0, "num_predict": 96},
                "keep_alive": self.keep_alive,
            }
            request = urllib.request.Request(
                self.url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
            response_text = response_payload.get("response")
            if not isinstance(response_text, str):
                raise ValueError("Ollama response is missing the response string")
            primary = parse_prompt_control_response(response_text)
            guard_enabled = _runtime_guard_enabled()
            resolved = (
                apply_rule_based_runtime_guard(prompt, primary)
                if guard_enabled
                else primary
            )
            return PromptControlResolution(
                control=resolved,
                parser_source=(
                    "ollama_prompt_control_runtime_guard"
                    if guard_enabled
                    else "ollama_prompt_control_raw"
                ),
                model=self.model,
                raw_response=response_text,
                fallback_reason=None,
                metadata={
                    "latency_seconds": round(time.perf_counter() - started, 6),
                    "primary_control": primary.to_dict(),
                    "runtime_guard_enabled": guard_enabled,
                    "guard_changed_intent": resolved.intent != primary.intent,
                    "guard_changed_strength": resolved.strength != primary.strength,
                    "guard_added_constraints": [
                        name
                        for name in resolved.constraints
                        if name not in primary.constraints
                    ],
                    "ollama": _ollama_metadata(response_payload),
                },
            )
        except Exception as exc:
            return self._fallback(prompt, f"{type(exc).__name__}: {exc}", started)

    def _fallback(
        self,
        prompt: str,
        reason: str,
        started: float | None = None,
    ) -> PromptControlResolution:
        latency_seconds = (
            round(time.perf_counter() - started, 6) if started is not None else 0.0
        )
        primary = resolve_prompt_control_rule_based(prompt)
        guarded = apply_rule_based_runtime_guard(prompt, primary)
        return PromptControlResolution(
            control=guarded,
            parser_source="rule_based_fallback",
            model=self.model,
            raw_response=None,
            fallback_reason=reason,
            metadata={
                "latency_seconds": latency_seconds,
                "primary_control": primary.to_dict(),
            },
        )


def create_prompt_control_resolver(
    *,
    model: str | None = None,
    url: str | None = None,
    timeout: float = 90.0,
    keep_alive: str = "15m",
) -> OllamaPromptControlResolver:
    return OllamaPromptControlResolver(
        model=model or os.getenv("AI_PHOTO_PROMPT_MODEL", DEFAULT_OLLAMA_MODEL),
        url=url or os.getenv("AI_PHOTO_OLLAMA_URL", DEFAULT_OLLAMA_URL),
        timeout=timeout,
        keep_alive=keep_alive,
    )


def _llm_enabled() -> bool:
    value = os.getenv("AI_PHOTO_USE_LLM", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _runtime_guard_enabled() -> bool:
    value = os.getenv("AI_PHOTO_PROMPT_USE_GUARD", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _ollama_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_duration_ms": _nanoseconds_to_ms(payload.get("total_duration")),
        "load_duration_ms": _nanoseconds_to_ms(payload.get("load_duration")),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "prompt_eval_duration_ms": _nanoseconds_to_ms(
            payload.get("prompt_eval_duration")
        ),
        "eval_count": payload.get("eval_count"),
        "eval_duration_ms": _nanoseconds_to_ms(payload.get("eval_duration")),
    }


def _nanoseconds_to_ms(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1_000_000.0, 3)
