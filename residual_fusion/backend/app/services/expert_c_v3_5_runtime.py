from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import cv2
import joblib
import numpy as np

from app.services import expert_c_v3_runtime as v31
from app.services.expert_c_v3_contract import (
    V3_5_RENDER_PROFILE,
    V3_5_RENDER_PROFILE_VERSION,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_ROOT = REPO_ROOT / "training"
V3_ROOT = TRAINING_ROOT / "v3"
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

from predict_expert_c_v3_5 import mean_std, selector_features  # noqa: E402


SELECTOR_RESULT_PATH = (
    TRAINING_ROOT
    / "outputs"
    / "expert_c_v3_5_resolution_aligned_selector002"
    / "result.json"
)
DEVELOPMENT_RESULT_PATH = (
    TRAINING_ROOT / "outputs" / "expert_c_v3_5_development003" / "result.json"
)


def create_expert_c_v3_5_result(
    *,
    original_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    selector_result, artifact, development_sha = _load_selector_bundle()
    original = _read_bgr(original_path)

    selector_started = time.perf_counter()
    feature, proxy = selector_features(original)
    if len(feature) != int(artifact["feature_count"]):
        raise RuntimeError(f"V3.5 runtime feature contract mismatch: {len(feature)}")
    predicted_mean, predicted_std = mean_std(artifact["model"], feature)
    lower_confidence_bound = (
        predicted_mean
        - float(artifact["uncertainty_multiplier"]) * predicted_std
    )
    selected_action = (
        "v3_1"
        if lower_confidence_bound > float(artifact["margin"])
        else "untouched_input"
    )
    selector_ms = (time.perf_counter() - selector_started) * 1000.0

    selector_safety = {
        "model": "expert_c_v3_5_resolution_aligned_lcb_selector_plus_v3_1",
        "selected_action": selected_action,
        "predicted_v3_1_gain_over_input": round(predicted_mean, 6),
        "prediction_uncertainty": round(predicted_std, 6),
        "uncertainty_multiplier": float(artifact["uncertainty_multiplier"]),
        "lower_confidence_bound": round(lower_confidence_bound, 6),
        "margin": float(artifact["margin"]),
        "selector_sha256": selector_result["artifact"]["sha256"],
        "development_gate_passed": True,
        "development_result_sha256": development_sha,
        "final_opened": False,
        "uses_gt_at_inference": False,
        "uses_llm_inside_renderer": False,
        "proxy": proxy,
    }
    if selected_action == "untouched_input":
        _write_image(result_path, original)
        return {
            "engine": "opencv",
            "parameters": {},
            "mask_info": None,
            "render_profile": V3_5_RENDER_PROFILE,
            "render_variant": "v3_5_identity_passthrough",
            "render_safety": selector_safety,
            "timings_ms": {
                "v3_5_selector": round(selector_ms, 3),
                "total": round((time.perf_counter() - started) * 1000.0, 3),
            },
            "explanation": (
                "Expert C V3.5 retained the input because the frozen safety "
                "selector did not predict a sufficiently reliable V3.1 gain."
            ),
        }

    base_result = v31.create_expert_c_v3_result(
        original_path=original_path,
        result_path=result_path,
    )
    base_safety = dict(base_result.get("render_safety") or {})
    return {
        **base_result,
        "render_profile": V3_5_RENDER_PROFILE,
        "render_variant": "v3_5_selected_" + str(base_result["render_variant"]),
        "render_safety": {
            **selector_safety,
            "selected_renderer": {
                "render_profile": base_result["render_profile"],
                "render_safety": base_safety,
            },
        },
        "timings_ms": {
            **dict(base_result.get("timings_ms") or {}),
            "v3_5_selector": round(selector_ms, 3),
            "total": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "explanation": (
            "Expert C V3.5 predicted a reliable gain and selected the "
            "banding-safe V3.1 renderer. " + str(base_result["explanation"])
        ),
    }


def warmup_expert_c_v3_5() -> dict[str, Any]:
    started = time.perf_counter()
    result, artifact, development_sha = _load_selector_bundle()
    v31_info = v31.warmup_expert_c_v3()
    return {
        "model": "expert_c_v3_5_resolution_aligned_lcb_selector_plus_v3_1",
        "render_profile": V3_5_RENDER_PROFILE,
        "render_profile_version": V3_5_RENDER_PROFILE_VERSION,
        "selector_sha256": result["artifact"]["sha256"],
        "development_result_sha256": development_sha,
        "feature_count": int(artifact["feature_count"]),
        "v3_1": v31_info,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


@lru_cache(maxsize=1)
def _load_selector_bundle() -> tuple[dict[str, Any], dict[str, Any], str]:
    result = json.loads(SELECTOR_RESULT_PATH.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result["gates"]["all_passed"] is not True:
        raise RuntimeError("V3.5 train OOF gate did not pass")
    if result["data"]["train_count"] != 4600:
        raise RuntimeError("Unexpected V3.5 train count")
    if result["data"]["final_content_accessed"] is not False:
        raise RuntimeError("V3.5 final lifecycle violation")
    artifact_path = Path(result["artifact"]["path"])
    if _sha256(artifact_path) != result["artifact"]["sha256"]:
        raise RuntimeError("V3.5 selector hash mismatch")
    artifact = joblib.load(artifact_path)
    if int(artifact["feature_count"]) != 259:
        raise RuntimeError("Unexpected V3.5 feature contract")

    development = json.loads(DEVELOPMENT_RESULT_PATH.read_text(encoding="utf-8"))
    if development.get("status") != "PASS" or development["gates"]["all_passed"] is not True:
        raise RuntimeError("V3.5 development gate did not pass")
    if development["data"]["development_evaluation_count_for_v3_5"] != 1:
        raise RuntimeError("Unexpected V3.5 development evaluation count")
    if development["data"]["final_content_accessed"] is not False:
        raise RuntimeError("V3.5 final lifecycle violation")
    if development["candidate"]["selector_sha256"] != result["artifact"]["sha256"]:
        raise RuntimeError("Development result references a different V3.5 selector")
    return result, artifact, _sha256(DEVELOPMENT_RESULT_PATH)


def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower() or ".png"
    success, encoded = cv2.imencode(suffix, image)
    if not success:
        raise RuntimeError(f"Unable to encode image: {path}")
    encoded.tofile(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
