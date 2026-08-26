from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import joblib
import numpy as np
import torch
from torch import nn

from app.services import expert_c_v3_runtime as v31


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_ROOT = REPO_ROOT / "training"
SELECTOR_RESULT_PATH = (
    TRAINING_ROOT / "outputs" / "expert_c_v3_5_resolution_aligned_selector002" / "result.json"
)
V36_RESULT_PATH = (
    TRAINING_ROOT / "outputs" / "expert_c_v3_6_catastrophic_guard002" / "result.json"
)
V36_DEPLOYMENT_PATH = (
    TRAINING_ROOT / "outputs" / "expert_c_v3_6_deployment_feature_contract002" / "result.json"
)
PROXY_SIDE = 128


class SpatialValueNet(nn.Module):
    def __init__(self, output_count: int) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(6, 32, 5, stride=2, padding=2),
            nn.GroupNorm(8, 32),
            nn.SiLU(),
            nn.Conv2d(32, 48, 3, stride=2, padding=1),
            nn.GroupNorm(8, 48),
            nn.SiLU(),
            nn.Conv2d(48, 64, 3, stride=2, padding=1),
            nn.GroupNorm(8, 64),
            nn.SiLU(),
            nn.Conv2d(64, 64, 3, padding=1),
            nn.GroupNorm(8, 64),
            nn.SiLU(),
        )
        self.head = nn.Sequential(
            nn.Linear(64 + 3 * 64 + 3 * 8 * 8, 192),
            nn.SiLU(),
            nn.Dropout(0.1),
            nn.Linear(192, output_count),
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        feature = self.encoder(value[:, :6])
        masks = nn.functional.interpolate(value[:, 6:9], size=feature.shape[-2:], mode="area")
        global_pool = feature.mean(dim=(-2, -1))
        denominator = masks.sum(dim=(-2, -1)).clamp_min(1e-4)
        region_pool = torch.einsum("bchw,brhw->brc", feature, masks) / denominator.unsqueeze(-1)
        summary = torch.cat([global_pool, region_pool.flatten(1), masks.flatten(1)], dim=1)
        return self.head(summary)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def resolve_package_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def soft_region_weights(sky: np.ndarray, person: np.ndarray, sigma: float) -> np.ndarray:
    rest = ~(sky | person)
    hard = np.stack([sky, person, rest]).astype(np.float32)
    soft = np.stack(
        [cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma) for mask in hard]
    )
    return soft / np.maximum(np.sum(soft, axis=0, keepdims=True), 1e-6)


def load_continuous_models(
    result_path: Path,
    device: torch.device,
) -> tuple[dict[str, Any], list[SpatialValueNet], float]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result["status"] not in {"COMPLETE_TRAIN_ONLY", "COMPLETE_POST_FINAL_DEMO_FIT"}:
        raise RuntimeError("Continuous full-train artifact is incomplete")
    manifest_path = resolve_package_path(result["artifact"]["manifest_path"])
    if sha256(manifest_path) != result["artifact"]["manifest_sha256"]:
        raise RuntimeError("Continuous manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    models: list[SpatialValueNet] = []
    for item in manifest["checkpoints"]:
        checkpoint_path = resolve_package_path(item["path"])
        if sha256(checkpoint_path) != item["sha256"]:
            raise RuntimeError(f"Continuous checkpoint hash mismatch: {checkpoint_path}")
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        model = SpatialValueNet(3)
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        model.to(device).eval()
        models.append(model)
    return result, models, float(manifest["deployment_lambda"])


def _luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _mean_saturation(rgb: np.ndarray) -> float:
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    return float(np.mean((maximum - minimum) / np.maximum(maximum, 1e-6)))


def _clip_ratio(rgb: np.ndarray) -> float:
    return float(np.mean((rgb <= (2.0 / 255.0)) | (rgb >= (253.0 / 255.0))))


def _change_features(before_bgr: np.ndarray, after_bgr: np.ndarray) -> list[float]:
    before = before_bgr[..., ::-1].astype(np.float32) / 255.0
    after = after_bgr[..., ::-1].astype(np.float32) / 255.0
    residual = after - before
    before_luma = _luma(before)
    after_luma = _luma(after)
    return [
        float(np.mean(np.abs(residual))),
        float(np.percentile(np.abs(residual), 95)),
        float(np.mean(residual[..., 0])),
        float(np.mean(residual[..., 1])),
        float(np.mean(residual[..., 2])),
        float(np.mean(after_luma) - np.mean(before_luma)),
        float(np.std(after_luma) - np.std(before_luma)),
        float(_mean_saturation(after) - _mean_saturation(before)),
        float(_clip_ratio(after) - _clip_ratio(before)),
    ]


def load_selector() -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(SELECTOR_RESULT_PATH.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result["gates"]["all_passed"] is not True:
        raise RuntimeError("V3.5 nested OOF gate did not pass")
    if result["data"]["train_count"] != 4600:
        raise RuntimeError("Unexpected V3.5 training count")
    if result["data"]["development_content_accessed"] is not False:
        raise RuntimeError("V3.5 development lifecycle violation")
    if result["data"]["final_content_accessed"] is not False:
        raise RuntimeError("V3.5 final lifecycle violation")
    artifact_path = resolve_package_path(result["artifact"]["path"])
    if sha256(artifact_path) != result["artifact"]["sha256"]:
        raise RuntimeError("V3.5 selector hash mismatch")
    artifact = joblib.load(artifact_path)
    if artifact["feature_count"] != 259 or artifact["promoted"] is not False:
        raise RuntimeError("Unexpected V3.5 selector contract")
    return result, artifact


def selector_features(original: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    from expert_c_runtime import (
        MODEL_FEATURE_NAMES,
        analyze_image_state,
        apply_opencv_parameters,
        predict_parameters_from_state,
        select_model_features,
    )

    bundle = v31._load_bundle()
    state = select_model_features(analyze_image_state(original))
    state_values = np.asarray([state[name] for name in MODEL_FEATURE_NAMES], dtype=np.float32)
    proxy = cv2.resize(original, (PROXY_SIDE, PROXY_SIDE), interpolation=cv2.INTER_AREA)
    parameters = predict_parameters_from_state(state, model=bundle.v2_model, strength=1.0)
    v2_proxy, resolved = apply_opencv_parameters(proxy, parameters)
    standardized = (state_values - bundle.checkpoint["state_mean"]) / bundle.checkpoint["state_std"]
    base = v31._bgr_to_tensor(v2_proxy, bundle.device)
    state_tensor = torch.from_numpy(standardized[None].astype(np.float32)).to(bundle.device)
    captured: dict[str, torch.Tensor] = {}
    hooks = [
        bundle.model.encoder[8].register_forward_hook(
            lambda _module, _inputs, output: captured.__setitem__("local", output)
        ),
        bundle.model.global_head.register_forward_hook(
            lambda _module, _inputs, output: captured.__setitem__("global", output)
        ),
        bundle.model.state_encoder.register_forward_hook(
            lambda _module, _inputs, output: captured.__setitem__("state", output)
        ),
    ]
    try:
        with v31._INFERENCE_LOCK:
            with torch.no_grad(), torch.amp.autocast("cuda", enabled=bundle.device.type == "cuda"):
                candidate, strength, logits, normalized_hsl = v31._forward_adaptive(
                    bundle.model, base, state_tensor, bundle.freeze
                )
    finally:
        for hook in hooks:
            hook.remove()
    v31_proxy = v31._tensor_to_bgr(candidate[0])
    probabilities = torch.softmax(logits.float(), dim=1)[0].cpu().numpy()
    base_features = np.asarray(
        state_values.tolist()
        + [float(strength.item())]
        + probabilities.tolist()
        + normalized_hsl[0].float().cpu().numpy().tolist()
        + _change_features(proxy, v2_proxy)
        + _change_features(v2_proxy, v31_proxy)
        + _change_features(proxy, v31_proxy),
        dtype=np.float32,
    )
    embedding = np.concatenate(
        [
            captured["local"].float().mean(dim=(2, 3)).cpu().numpy()[0],
            captured["global"].float().cpu().numpy()[0],
            captured["state"].float().cpu().numpy()[0],
        ]
    ).astype(np.float32)
    feature = np.concatenate([base_features, embedding]).astype(np.float32)
    return feature, {
        "state": state,
        "v2_parameters": resolved,
        "selected_strength": round(float(strength.item()), 6),
        "strength_probabilities": [round(float(item), 6) for item in probabilities],
        "joint_normalized_hsl": [
            round(float(item), 6) for item in normalized_hsl[0].float().cpu().numpy()
        ],
        "proxy_side": PROXY_SIDE,
        "base_feature_count": int(len(base_features)),
        "embedding_feature_count": int(len(embedding)),
    }


def mean_std(model: Any, feature: np.ndarray) -> tuple[float, float]:
    predictions = np.asarray(
        [estimator.predict(feature[None])[0] for estimator in model.estimators_],
        dtype=np.float64,
    )
    return float(predictions.mean()), float(predictions.std())


def load_catastrophic_guard() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = json.loads(V36_RESULT_PATH.read_text(encoding="utf-8"))
    deployment = json.loads(V36_DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result["gates"]["all_passed"] is not True:
        raise RuntimeError("V3.6 nested OOF gate did not pass")
    if deployment.get("status") != "PASS" or deployment["gates"]["all_passed"] is not True:
        raise RuntimeError("V3.6 deployment feature contract did not pass")
    if result["data"]["train_count"] != 4600:
        raise RuntimeError("Unexpected V3.6 train count")
    if result["data"]["development_content_accessed"] is not False:
        raise RuntimeError("V3.6 development lifecycle violation")
    if result["data"]["final_content_accessed"] is not False:
        raise RuntimeError("V3.6 final lifecycle violation")
    artifact_path = resolve_package_path(result["artifact"]["path"])
    if sha256(artifact_path) != result["artifact"]["sha256"]:
        raise RuntimeError("V3.6 guard hash mismatch")
    artifact = joblib.load(artifact_path)
    if artifact["feature_count"] != 259 or artifact["promoted"] is not False:
        raise RuntimeError("Unexpected V3.6 guard contract")
    return result, deployment, artifact


def catastrophic_probability(model: Any, feature: np.ndarray) -> float:
    probabilities = model.predict_proba(feature[None])
    classes = model.classes_.tolist()
    if 1 not in classes:
        return 0.0
    return float(probabilities[0, classes.index(1)])
