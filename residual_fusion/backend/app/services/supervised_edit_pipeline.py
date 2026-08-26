from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.services.edit_plan import build_raw_parameter_edit_plan
from app.services.expert_c_v3_contract import (
    V3_1_RENDER_PROFILE,
    V3_1_RENDER_PROFILE_VERSION,
    V3_1_INTENTS,
    V3_5_RENDER_PROFILE,
    V3_5_RENDER_PROFILE_VERSION,
    V3_8_RENDER_PROFILE,
    V3_8_RENDER_PROFILE_VERSION,
    expert_c_v3_enabled,
    expert_c_v3_5_enabled,
    expert_c_v3_8_enabled,
)
from app.services.prompt_control_runtime import create_prompt_control_resolver
from app.services.prompt_intent_encoder import detect_prompt_intent
from app.services.selective_hybrid_predictor import (
    predict_selective_hybrid_from_path,
)
from app.services.supervised_opencv_processor import (
    SUPERVISED_RENDER_PROFILE,
    SUPERVISED_RENDER_PROFILE_VERSION,
)


SUPERVISED_PIPELINE_VERSION = "supervised_prompt_pipeline_v3_1"


def supervised_prompt_pipeline_enabled() -> bool:
    value = os.getenv("AI_PHOTO_USE_SUPERVISED_PIPELINE", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def should_use_supervised_prompt_edit(
    prompt: str,
    *,
    engine_name: str,
    has_parent_edit: bool,
    semantic_disposition: str | None,
) -> bool:
    """Route broad edits to the model without stealing precise local tools."""

    if not supervised_prompt_pipeline_enabled():
        return False
    if str(engine_name or "").strip().lower() != "opencv":
        return False
    if str(semantic_disposition or "").strip().lower() == "accepted":
        return False
    intent = detect_prompt_intent(prompt)
    if has_parent_edit:
        return intent == "auto_enhance"
    return intent is not None


def resolve_supervised_prompt_edit(
    prompt: str,
    image_path: Path,
) -> dict[str, Any]:
    resolution = create_prompt_control_resolver(
        timeout=float(os.getenv("AI_PHOTO_PROMPT_TIMEOUT", "30"))
    ).resolve(prompt)
    if expert_c_v3_enabled() and resolution.control.intent in V3_1_INTENTS:
        use_v3_8 = expert_c_v3_8_enabled()
        use_v3_5 = expert_c_v3_5_enabled() and not use_v3_8
        render_profile = V3_8_RENDER_PROFILE if use_v3_8 else (V3_5_RENDER_PROFILE if use_v3_5 else V3_1_RENDER_PROFILE)
        render_profile_version = V3_8_RENDER_PROFILE_VERSION if use_v3_8 else (V3_5_RENDER_PROFILE_VERSION if use_v3_5 else V3_1_RENDER_PROFILE_VERSION)
        edit_plan = build_raw_parameter_edit_plan(prompt=prompt, parameters={})
        edit_plan["render_profile"] = render_profile
        renderer_description = (
            "the frozen Expert C V3.8 5,000-pair continuous spatial renderer was selected."
            if use_v3_8
            else "the frozen Expert C V3.5 safety selector was selected."
            if use_v3_5
            else "the frozen Expert C V3.1 image-conditioned renderer was selected."
        )
        pipeline_metadata = {
            "schema_version": (
                "supervised_prompt_pipeline_v3_8"
                if use_v3_8
                else SUPERVISED_PIPELINE_VERSION
            ),
            "render_profile": render_profile,
            "render_profile_version": render_profile_version,
            "role": "initial_global_expert_c_edit",
            "lifecycle": (
                "post_final_demo_fit_inherits_one_shot_final"
                if use_v3_8
                else "development_passed_final_sealed"
            ),
        }
        return {
            "prompt": prompt,
            "resolved_intent": resolution.control.intent,
            "preset_name": None,
            "edit_plan": edit_plan,
            "parameters": {},
            "explanation": (
                "Supervised prompt control selected "
                f"{resolution.control.intent}/{resolution.control.strength}; "
                f"{renderer_description}"
            ),
            "parser_source": resolution.parser_source,
            "fallback_reason": resolution.fallback_reason,
            "prompt_control": resolution.to_dict(),
            "numeric_model": {
                "route": (
                    "expert_c_v3_8_post_final_5000_continuous_spatial"
                    if use_v3_8
                    else "expert_c_v3_5_safe_selector_plus_v3_1"
                    if use_v3_5
                    else "expert_c_v3_1_artifact_safe"
                ),
                "runtime": "pytorch_cuda_cached",
                "uses_prompt_as_renderer_input": False,
            },
            "supervised_pipeline": pipeline_metadata,
        }
    prediction = predict_selective_hybrid_from_path(
        image_path,
        resolution.control.intent,
    )
    edit_plan = build_raw_parameter_edit_plan(
        prompt=prompt,
        parameters=prediction.parameters,
    )
    edit_plan["render_profile"] = SUPERVISED_RENDER_PROFILE
    pipeline_metadata = {
        "schema_version": SUPERVISED_PIPELINE_VERSION,
        "render_profile": SUPERVISED_RENDER_PROFILE,
        "render_profile_version": SUPERVISED_RENDER_PROFILE_VERSION,
        "role": "initial_global_expert_c_edit",
    }
    return {
        "prompt": prompt,
        "resolved_intent": resolution.control.intent,
        "preset_name": None,
        "edit_plan": edit_plan,
        "parameters": prediction.parameters,
        "explanation": (
            "Supervised prompt control selected "
            f"{resolution.control.intent}/{resolution.control.strength}; "
            f"the frozen {prediction.route} NumPy regressor predicted OpenCV parameters."
        ),
        "parser_source": resolution.parser_source,
        "fallback_reason": resolution.fallback_reason,
        "prompt_control": resolution.to_dict(),
        "numeric_model": prediction.to_dict(),
        "supervised_pipeline": pipeline_metadata,
    }
