from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


AUTO_MODEL_COMPARISON_SCHEMA_VERSION = "auto_model_comparison_v1"
AUTO_MODEL_HISTORY_SCHEMA_VERSION = "auto_model_history_v1"
VISUAL_ANCHOR_SCHEMA_VERSION = "visual_anchor_v1"

EXPERT_FAITHFUL_MODEL_KEY = "expert_faithful_lut"
VIVID_MODEL_KEY = "vivid_residual_fusion"
AUTO_MODEL_KEYS = (
    EXPERT_FAITHFUL_MODEL_KEY,
    VIVID_MODEL_KEY,
)


class AutoModelError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        model_key: str | None = None,
        status_code: int = 422,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.model_key = model_key
        self.status_code = status_code
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.model_key:
            payload["model_key"] = self.model_key
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass(frozen=True)
class AutoModelResult:
    model_key: str
    model_family: str
    runtime_metadata: dict[str, Any]
    timings_ms: dict[str, float]
    warning_flags: tuple[str, ...] = ()


class AutoEnhanceAdapter(Protocol):
    model_key: str
    model_family: str

    def enhance(self, source_path: Path, result_path: Path) -> AutoModelResult:
        ...

    def warmup(self) -> dict[str, Any]:
        ...

    def health(self) -> dict[str, Any]:
        ...

    def asset_identity(self) -> dict[str, Any]:
        ...
