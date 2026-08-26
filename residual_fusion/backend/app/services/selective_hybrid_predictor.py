from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np

from app.services.image_state_analyzer_v2 import (
    MODEL_FEATURE_NAMES,
    analyze_image_state,
    select_model_features,
)
from app.services.intent_parameter_profiles import apply_intent_parameter_profile
from app.services.prompt_intent_encoder import PROMPT_INTENT_NAMES


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = (
    REPO_ROOT / "training" / "baselines" / "selective_hybrid_numpy_v1.json"
)


@dataclass(frozen=True)
class SelectiveHybridPrediction:
    parameters: dict[str, float]
    intent: str
    route: str
    image_state_version: str
    artifact_path: str
    artifact_sha256: str
    timing_seconds: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": self.parameters,
            "intent": self.intent,
            "route": self.route,
            "image_state_version": self.image_state_version,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "runtime": "numpy",
            "timing_seconds": self.timing_seconds,
        }


def predict_selective_hybrid_from_path(
    image_path: Path,
    intent: str,
    *,
    registry_path: Path = DEFAULT_REGISTRY,
) -> SelectiveHybridPrediction:
    image = _read_bgr(image_path)
    return predict_selective_hybrid_parameters(
        image,
        intent,
        registry_path=registry_path,
    )


def predict_selective_hybrid_parameters(
    image: np.ndarray,
    intent: str,
    *,
    registry_path: Path = DEFAULT_REGISTRY,
) -> SelectiveHybridPrediction:
    if intent not in PROMPT_INTENT_NAMES:
        raise ValueError(f"Unsupported prompt intent: {intent}")

    started = time.perf_counter()
    registry = _load_registry(str(registry_path.resolve()))
    route = registry["routing"][intent]
    artifact = registry["artifacts"][route]
    artifact_path = (REPO_ROOT / artifact["path"]).resolve()
    artifact_sha256 = str(artifact["sha256"]).upper()
    model = _load_numpy_model(str(artifact_path), artifact_sha256)

    analysis_started = time.perf_counter()
    state = analyze_image_state(image)
    selected_state = select_model_features(state)
    analysis_seconds = time.perf_counter() - analysis_started

    inference_started = time.perf_counter()
    vector = _build_model_vector(selected_state, intent, route, model)
    standardized = (
        vector - model["feature_mean"]
    ) / model["feature_std"]
    hidden0 = np.maximum(
        standardized.astype(np.float32) @ model["layer0_weight"].T
        + model["layer0_bias"],
        0.0,
    )
    hidden1 = np.maximum(
        hidden0 @ model["layer1_weight"].T + model["layer1_bias"],
        0.0,
    )
    logits = hidden1 @ model["layer2_weight"].T + model["layer2_bias"]
    normalized = 1.0 / (1.0 + np.exp(-np.clip(logits, -60.0, 60.0)))
    values = model["parameter_lows"] + normalized.astype(np.float64) * (
        model["parameter_highs"] - model["parameter_lows"]
    )
    parameters = {
        name: round(float(values[index]), 4)
        for index, name in enumerate(model["parameter_names"])
    }
    if route == "white_balance_expert":
        parameters = {
            name: round(value, 4)
            for name, value in apply_intent_parameter_profile(
                parameters,
                intent,
            ).items()
        }
    inference_seconds = time.perf_counter() - inference_started

    return SelectiveHybridPrediction(
        parameters=parameters,
        intent=intent,
        route=route,
        image_state_version=str(state["version"]),
        artifact_path=str(artifact_path),
        artifact_sha256=artifact_sha256,
        timing_seconds={
            "image_analysis": round(analysis_seconds, 6),
            "model_inference": round(inference_seconds, 6),
            "total": round(time.perf_counter() - started, 6),
        },
    )


def _build_model_vector(
    state: dict[str, float],
    intent: str,
    route: str,
    model: dict[str, Any],
) -> np.ndarray:
    feature_names = model["feature_names"]
    if feature_names != MODEL_FEATURE_NAMES:
        raise ValueError("NumPy artifact image feature contract does not match v2")
    values = [float(state[name]) for name in feature_names]
    if route == "shared_v2_state_intent":
        intent_names = model["prompt_intent_names"]
        if intent_names != PROMPT_INTENT_NAMES:
            raise ValueError("NumPy artifact prompt intent contract does not match")
        values.extend(1.0 if name == intent else 0.0 for name in intent_names)
    elif route != "white_balance_expert":
        raise ValueError(f"Unsupported selective-hybrid route: {route}")
    return np.asarray(values, dtype=np.float64)


@lru_cache(maxsize=1)
def _load_registry(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _load_numpy_model(path: str, expected_sha256: str) -> dict[str, Any]:
    artifact_path = Path(path)
    actual_sha256 = _sha256(artifact_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"NumPy model hash mismatch: {actual_sha256} != {expected_sha256}"
        )
    with np.load(artifact_path, allow_pickle=False) as archive:
        model = {name: archive[name].copy() for name in archive.files}
    if str(model["schema_version"].item()) != "tiny_regressor_numpy_v1":
        raise ValueError("Unsupported NumPy model schema")
    model["feature_names"] = tuple(str(item) for item in model["feature_names"])
    model["prompt_intent_names"] = tuple(
        str(item) for item in model["prompt_intent_names"]
    )
    model["parameter_names"] = tuple(str(item) for item in model["parameter_names"])
    return model


def _read_bgr(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read image: {path}")
    return image


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
