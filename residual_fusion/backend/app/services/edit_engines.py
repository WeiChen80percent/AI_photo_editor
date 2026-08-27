from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.opencv_parameter_mapper import build_opencv_parameters_from_plan
from app.services.expert_c_v3_contract import (
    V3_5_RENDER_PROFILE,
    V3_8_RENDER_PROFILE,
    is_expert_c_v3_plan,
)
from app.services.opencv_processor import (
    create_compound_local_opencv_result,
    create_opencv_result,
)
from app.services.style_plan import resolve_style_plan
from app.services.style_renderer import create_opencv_style_result
from app.services.supervised_opencv_processor import (
    create_supervised_opencv_result,
    is_supervised_render_plan,
    resolve_supervised_parameters,
)


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
        if is_expert_c_v3_plan(edit_plan):
            return {}
        if is_supervised_render_plan(edit_plan):
            return resolve_supervised_parameters(
                edit_plan.get("raw_parameters")
            )
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
    if normalized == "opencv" and str(edit_plan.get("type") or "") == "compound_local":
        if reference_path is not None:
            raise ValueError("Compound local rendering does not accept a reference image")
        raw_operations = edit_plan.get("operations")
        if not isinstance(raw_operations, list):
            raise ValueError("Compound local edit plan requires an operations list")
        operation_parameters: list[dict[str, Any]] = []
        for operation in raw_operations:
            if not isinstance(operation, dict):
                raise ValueError("Compound local operation must be an object")
            child_plan = operation.get("edit_plan")
            if not isinstance(child_plan, dict):
                raise ValueError("Compound local operation requires an edit plan")
            operation_parameters.append(
                build_engine_parameters(normalized, child_plan)
            )
        return create_compound_local_opencv_result(
            original_path=original_path,
            result_path=result_path,
            operation_parameters=operation_parameters,
            mask_source_path=mask_source_path,
        )
    if normalized == "opencv" and is_expert_c_v3_plan(edit_plan):
        if reference_path is not None:
            raise ValueError("The Expert C V3 render profile does not accept a reference image")
        if str(edit_plan.get("render_profile") or "") == V3_5_RENDER_PROFILE:
            from app.services.expert_c_v3_5_runtime import create_expert_c_v3_5_result

            return create_expert_c_v3_5_result(
                original_path=original_path,
                result_path=result_path,
            )
        if str(edit_plan.get("render_profile") or "") == V3_8_RENDER_PROFILE:
            from app.services.expert_c_v3_8_runtime import create_expert_c_v3_8_result

            return create_expert_c_v3_8_result(
                original_path=original_path,
                result_path=result_path,
            )
        from app.services.expert_c_v3_runtime import create_expert_c_v3_result

        return create_expert_c_v3_result(
            original_path=original_path,
            result_path=result_path,
        )
    parameters = build_engine_parameters(normalized, edit_plan)
    if normalized == "opencv":
        if is_supervised_render_plan(edit_plan):
            if reference_path is not None:
                raise ValueError(
                    "The supervised Expert C render profile does not accept a reference image"
                )
            return create_supervised_opencv_result(
                original_path=original_path,
                result_path=result_path,
                parameters=parameters,
            )
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
