from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.opencv_parameter_mapper import build_opencv_parameters_from_plan
from app.services.opencv_processor import create_opencv_result
from app.services.style_plan import resolve_style_plan
from app.services.style_renderer import create_opencv_style_result


SUPPORTED_EDIT_ENGINES = {"opencv"}
DEFAULT_EDIT_ENGINE = "opencv"


def normalize_engine_name(engine_name: str | None) -> str:
    normalized = (engine_name or DEFAULT_EDIT_ENGINE).strip().lower()
    if not normalized:
        normalized = DEFAULT_EDIT_ENGINE
    if normalized not in SUPPORTED_EDIT_ENGINES:
        raise ValueError(f"Unsupported edit engine: {engine_name}")
    return normalized


def build_engine_parameters(
    engine_name: str | None,
    edit_plan: dict[str, Any],
) -> dict[str, float]:
    normalized = normalize_engine_name(engine_name)
    if normalized == "opencv":
        return build_opencv_parameters_from_plan(edit_plan)
    raise ValueError(f"Unsupported edit engine: {engine_name}")


def create_engine_result(
    *,
    engine_name: str | None,
    original_path: Path,
    reference_path: Path | None,
    result_path: Path,
    edit_plan: dict[str, Any],
    mask_source_path: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_engine_name(engine_name)
    parameters = build_engine_parameters(normalized, edit_plan)
    if normalized == "opencv":
        if str(edit_plan.get("type") or "") == "style":
            resolved_style = resolve_style_plan(edit_plan)
            return create_opencv_style_result(
                original_path=original_path,
                result_path=result_path,
                style=resolved_style.style,
                parameters=parameters,
                strength=resolved_style.strength,
            )
        return create_opencv_result(
            original_path=original_path,
            reference_path=reference_path,
            result_path=result_path,
            parameters=parameters,
            mask_source_path=mask_source_path,
        )
    raise ValueError(f"Unsupported edit engine: {engine_name}")
