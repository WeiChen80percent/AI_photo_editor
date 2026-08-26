from __future__ import annotations

import os
from typing import Any


V3_1_RENDER_PROFILE = "expert_c_v3_1_artifact_safe_banding_v2"
V3_1_RENDER_PROFILE_VERSION = "1.1"
V3_5_RENDER_PROFILE = "expert_c_v3_5_safe_selector_banding_v1"
V3_5_RENDER_PROFILE_VERSION = "1.0"
V3_8_RENDER_PROFILE = "expert_c_v3_8_post_final_5000_continuous_spatial_v1"
V3_8_RENDER_PROFILE_VERSION = "1.0"
V3_1_INTENTS = frozenset({"auto_enhance", "restore_natural"})


def expert_c_v3_enabled() -> bool:
    return expert_c_v3_1_enabled() or expert_c_v3_5_enabled() or expert_c_v3_8_enabled()


def expert_c_v3_1_enabled() -> bool:
    value = os.getenv("AI_PHOTO_USE_EXPERT_C_V3_1", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def expert_c_v3_5_enabled() -> bool:
    value = os.getenv("AI_PHOTO_USE_EXPERT_C_V3_5", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def expert_c_v3_8_enabled() -> bool:
    value = os.getenv("AI_PHOTO_USE_EXPERT_C_V3_8", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_expert_c_v3_plan(edit_plan: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(edit_plan, dict)
        and str(edit_plan.get("render_profile") or "")
        in {V3_1_RENDER_PROFILE, V3_5_RENDER_PROFILE, V3_8_RENDER_PROFILE}
    )
