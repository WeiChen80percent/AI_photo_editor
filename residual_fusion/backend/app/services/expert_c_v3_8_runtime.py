from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import cv2
import numpy as np
import torch

from app.services import expert_c_v3_runtime as v31
from app.services.expert_c_v3_contract import V3_8_RENDER_PROFILE, V3_8_RENDER_PROFILE_VERSION
from app.services.semantic_mask_service import SemanticTargetNotFoundError, get_default_semantic_mask_service


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_ROOT = REPO_ROOT / "training"
from app.services.expert_c_v3_8_runtime_support import (
    catastrophic_probability,
    load_catastrophic_guard,
    load_continuous_models,
    load_selector,
    mean_std,
    selector_features,
    sha256,
    soft_region_weights,
)


CONTINUOUS_RESULT_PATH = TRAINING_ROOT / "outputs" / "expert_c_continuous_spatial_strength_post_final_5000_demo001" / "result.json"
FINAL_RESULT_PATH = TRAINING_ROOT / "outputs" / "expert_c_continuous_spatial_strength_final002" / "result.json"
RELEASE_AUDIT_PATH = TRAINING_ROOT / "outputs" / "expert_c_v3_8_post_final_5000_release_audit001" / "result.json"
_INFERENCE_LOCK = threading.Lock()
CONSERVATIVE_FALLBACK_MULTIPLIER = 0.4
CONSERVATIVE_FALLBACK_FLOOR = 0.2
CONSERVATIVE_FALLBACK_CAP = 0.4


@dataclass(frozen=True)
class V38Bundle:
    continuous_result: dict[str, Any]
    continuous_models: tuple[torch.nn.Module, ...]
    deployment_lambda: float
    v35_result: dict[str, Any]
    v35_artifact: dict[str, Any]
    v36_result: dict[str, Any]
    v36_deployment: dict[str, Any]
    v36_artifact: dict[str, Any]
    final_result: dict[str, Any]
    release_audit: dict[str, Any]
    device: torch.device


def create_expert_c_v3_8_result(*, original_path: Path, result_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    bundle = _load_bundle()
    original = _read_bgr(original_path)
    feature, proxy = selector_features(original)
    predicted_mean, predicted_std = mean_std(bundle.v35_artifact["model"], feature)
    lower_bound = predicted_mean - float(bundle.v35_artifact["uncertainty_multiplier"]) * predicted_std
    risk_probability = catastrophic_probability(bundle.v36_artifact["model"], feature)
    active = (
        lower_bound > float(bundle.v35_artifact["margin"])
        and risk_probability <= float(bundle.v36_artifact["maximum_risk_probability"])
    )
    strict_noop = _strict_noop_enabled()
    selected_action = (
        "continuous_spatial_blend"
        if active
        else "untouched_input"
        if strict_noop
        else "conservative_spatial_blend"
    )
    safety = {
        "model": "expert_c_v3_8_post_final_5000_demo",
        "selected_action": selected_action,
        "v3_5_lower_confidence_bound": round(float(lower_bound), 6),
        "v3_5_margin": float(bundle.v35_artifact["margin"]),
        "v3_6_risk_probability": round(float(risk_probability), 6),
        "v3_6_maximum_risk_probability": float(bundle.v36_artifact["maximum_risk_probability"]),
        "continuous_manifest_sha256": bundle.continuous_result["artifact"]["manifest_sha256"],
        "release_audit_sha256": sha256(RELEASE_AUDIT_PATH),
        "pre_final_final_result_sha256": sha256(FINAL_RESULT_PATH),
        "pre_final_final_status": bundle.final_result["status"],
        "post_final_fit_inherits_pre_final_evidence": True,
        "uses_gt_at_inference": False,
        "uses_llm_inside_renderer": False,
        "safety_gates_passed": active,
        "strict_noop_enabled": strict_noop,
        "deployment_fallback_policy": "visible_conservative_spatial_v1",
        "proxy": proxy,
    }
    if not active and strict_noop:
        _write_image(result_path, original)
        safety["sky_person_rest_strengths"] = [0.0, 0.0, 0.0]
        return {
            "engine": "opencv",
            "parameters": {},
            "mask_info": None,
            "render_profile": V3_8_RENDER_PROFILE,
            "render_variant": "v3_8_identity_passthrough",
            "render_safety": safety,
            "timings_ms": {"total": round((time.perf_counter() - started) * 1000.0, 3)},
            "explanation": "Expert C V3.8 retained the input because the frozen V3.5/V3.6 safety gates did not both pass.",
        }

    scratch = result_path.with_name(f".{result_path.stem}.v31.tmp.png")
    try:
        base_result = v31.create_expert_c_v3_result(original_path=original_path, result_path=scratch)
        candidate = _read_bgr(scratch)
    finally:
        scratch.unlink(missing_ok=True)
    if candidate.shape != original.shape:
        raise RuntimeError("V3.1 candidate shape mismatch")
    height, width = original.shape[:2]
    sky, sky_info = _semantic_mask(original_path, "sky", (height, width))
    person, person_info = _semantic_mask(original_path, "person", (height, width))
    side = 64
    sky64 = cv2.resize(sky.astype(np.uint8), (side, side), interpolation=cv2.INTER_NEAREST) > 0
    person64 = cv2.resize(person.astype(np.uint8), (side, side), interpolation=cv2.INTER_NEAREST) > 0
    weights = soft_region_weights(sky64, person64, 1.5).astype(np.float32)
    input64 = cv2.resize(original, (side, side), interpolation=cv2.INTER_AREA)
    candidate64 = cv2.resize(candidate, (side, side), interpolation=cv2.INTER_AREA)
    input_rgb = cv2.cvtColor(input64, cv2.COLOR_BGR2RGB).transpose(2, 0, 1).astype(np.float32) / 255.0
    candidate_rgb = cv2.cvtColor(candidate64, cv2.COLOR_BGR2RGB).transpose(2, 0, 1).astype(np.float32) / 255.0
    value = torch.from_numpy(np.concatenate([input_rgb, candidate_rgb - input_rgb, weights], axis=0)[None]).to(bundle.device)
    with _INFERENCE_LOCK, torch.inference_mode():
        strengths = torch.stack([torch.sigmoid(model(value)).float() for model in bundle.continuous_models]).mean(dim=0)
        strengths = (1.0 - bundle.deployment_lambda) + bundle.deployment_lambda * strengths
    strength_values = strengths[0].cpu().numpy().astype(np.float32)
    if not active:
        strength_values = np.clip(
            strength_values * CONSERVATIVE_FALLBACK_MULTIPLIER,
            CONSERVATIVE_FALLBACK_FLOOR,
            CONSERVATIVE_FALLBACK_CAP,
        )
    alpha64 = np.einsum("r,rhw->hw", strength_values, weights)
    alpha = cv2.resize(alpha64, (width, height), interpolation=cv2.INTER_LINEAR)[..., None]
    output = np.clip(np.rint(original.astype(np.float32) * (1.0 - alpha) + candidate.astype(np.float32) * alpha), 0, 255).astype(np.uint8)
    _write_image(result_path, output)
    safety.update({
        "sky_person_rest_strengths": [round(float(value), 6) for value in strength_values],
        "deployment_lambda": bundle.deployment_lambda,
        "conservative_fallback_multiplier": (
            CONSERVATIVE_FALLBACK_MULTIPLIER if not active else None
        ),
        "conservative_fallback_range": (
            [CONSERVATIVE_FALLBACK_FLOOR, CONSERVATIVE_FALLBACK_CAP]
            if not active
            else None
        ),
        "selected_renderer": {"render_profile": base_result["render_profile"], "render_safety": base_result.get("render_safety")},
    })
    variant_prefix = "v3_8_continuous_spatial_" if active else "v3_8_conservative_spatial_"
    explanation = (
        "Expert C V3.8 passed both safety gates and applied the 5,000-pair continuous sky/person/rest blend. "
        if active
        else "Expert C V3.8 safety gates requested caution; the demo deployment applied a bounded 20%-40% spatial blend instead of returning an unchanged image. "
    )
    return {
        **base_result,
        "render_profile": V3_8_RENDER_PROFILE,
        "render_variant": variant_prefix + str(base_result["render_variant"]),
        "mask_info": {"sky": sky_info, "person": person_info},
        "render_safety": safety,
        "timings_ms": {**dict(base_result.get("timings_ms") or {}), "total": round((time.perf_counter() - started) * 1000.0, 3)},
        "explanation": explanation + str(base_result["explanation"]),
    }


def warmup_expert_c_v3_8() -> dict[str, Any]:
    started = time.perf_counter()
    bundle = _load_bundle()
    v31_info = v31.warmup_expert_c_v3()
    segmentation = get_default_semantic_mask_service().warmup()
    return {
        "model": "expert_c_v3_8_post_final_5000_demo",
        "render_profile": V3_8_RENDER_PROFILE,
        "render_profile_version": V3_8_RENDER_PROFILE_VERSION,
        "device": str(bundle.device),
        "checkpoint_count": len(bundle.continuous_models),
        "deployment_lambda": bundle.deployment_lambda,
        "continuous_manifest_sha256": bundle.continuous_result["artifact"]["manifest_sha256"],
        "release_audit_status": bundle.release_audit["status"],
        "v3_1": v31_info,
        "segmentation": segmentation,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


@lru_cache(maxsize=1)
def _load_bundle() -> V38Bundle:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    continuous_result, models, deployment_lambda = load_continuous_models(CONTINUOUS_RESULT_PATH, device)
    v35_result, v35_artifact = load_selector()
    v36_result, v36_deployment, v36_artifact = load_catastrophic_guard()
    final_result = json.loads(FINAL_RESULT_PATH.read_text(encoding="utf-8"))
    release_audit = json.loads(RELEASE_AUDIT_PATH.read_text(encoding="utf-8"))
    if final_result.get("status") != "PASS_FINAL" or final_result["data"]["final_evaluation_count_for_candidate"] != 1:
        raise RuntimeError("V3.8 pre-final evidence is invalid")
    if release_audit.get("status") != "PASS_POST_FINAL_DEMO_RELEASE_AUDIT":
        raise RuntimeError("V3.8 post-final release audit did not pass")
    return V38Bundle(continuous_result, tuple(models), deployment_lambda, v35_result, v35_artifact, v36_result, v36_deployment, v36_artifact, final_result, release_audit, device)


def _semantic_mask(path: Path, target: str, shape: tuple[int, int]) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        result = get_default_semantic_mask_service().get_mask(path, target)
        return result.raw_mask > 0, result.info
    except SemanticTargetNotFoundError as error:
        return np.zeros(shape, dtype=bool), error.mask_info


def _strict_noop_enabled() -> bool:
    return os.getenv("AI_PHOTO_V3_8_STRICT_NOOP", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix.lower() or ".png", image)
    if not success:
        raise RuntimeError(f"Unable to encode image: {path}")
    encoded.tofile(path)
