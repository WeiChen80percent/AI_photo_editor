from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from torchvision.models import mobilenet_v3_small

from app.services.expert_c_v3_contract import V3_1_RENDER_PROFILE


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAINING_ROOT = REPO_ROOT / "training"
V3_ROOT = TRAINING_ROOT / "v3"
FINAL_ROOT = TRAINING_ROOT / "final"
ARCHIVE_ROOT = TRAINING_ROOT / "archived_research_scripts"
for path in (TRAINING_ROOT, V3_ROOT, FINAL_ROOT, ARCHIVE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import hsl_rendered_model as hsl  # noqa: E402
from expert_c_runtime import (  # noqa: E402
    MODEL_FEATURE_NAMES,
    analyze_image_state,
    apply_opencv_parameters_banding_safe,
    predict_parameters_from_state,
    select_model_features,
)
from predict_raw_expert_c import load_frozen_model  # noqa: E402


DEFAULT_RESULT_PATH = (
    TRAINING_ROOT
    / "outputs"
    / "expert_c_v3_1_joint_categorical_artifact_safe_full_research001"
    / "result.json"
)
DEFAULT_FREEZE_PATH = (
    V3_ROOT / "pre_training_freeze_joint_categorical_artifact_safe_full_research_v1.json"
)
DEVELOPMENT_RESULT_PATH = (
    TRAINING_ROOT
    / "outputs"
    / "expert_c_v3_1_artifact_safe_development001"
    / "result.json"
)
_INFERENCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class V31Bundle:
    model: nn.Module
    freeze: dict[str, Any]
    checkpoint: dict[str, Any]
    checkpoint_sha256: str
    v2_model: Any
    device: torch.device
    affine_bound: float
    development_result_sha256: str


class ClampedHead(nn.Module):
    def __init__(self, base: nn.Module, bound: float) -> None:
        super().__init__()
        self.base = base
        self.bound = bound

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return torch.clamp(self.base(value), -self.bound, self.bound)


class ConvNormAct(nn.Sequential):
    def __init__(self, input_channels: int, output_channels: int) -> None:
        super().__init__(
            nn.Conv2d(input_channels, output_channels, 3, padding=1, bias=False),
            nn.GroupNorm(8, output_channels),
            nn.SiLU(inplace=True),
        )


class LearnedGuide(nn.Module):
    def __init__(self, knot_count: int) -> None:
        super().__init__()
        self.color = nn.Conv2d(3, 1, kernel_size=1, bias=True)
        self.slopes = nn.Parameter(torch.zeros(3, knot_count))
        self.register_buffer(
            "knots",
            torch.linspace(0.0, 1.0, knot_count + 2)[1:-1],
        )

    def forward(self, rgb: torch.Tensor) -> torch.Tensor:
        guide = self.color(rgb)
        for channel in range(3):
            values = rgb[:, channel : channel + 1]
            hinges = F.relu(values.unsqueeze(2) - self.knots.view(1, 1, -1, 1, 1))
            guide = guide + torch.sum(
                hinges * self.slopes[channel].view(1, 1, -1, 1, 1), dim=2
            )
        return torch.clamp(guide, 0.0, 1.0)


class RuntimeJointSpatialHSLStudent(nn.Module):
    """Inference-only copy of the frozen architecture with no weight download."""

    def __init__(self, freeze: dict[str, Any]) -> None:
        super().__init__()
        config = freeze["model"]
        self.encoder = mobilenet_v3_small(weights=None).features
        self.frozen_through = int(config["frozen_encoder_through"])
        self.local = nn.Sequential(ConvNormAct(48, 64), ConvNormAct(64, 64))
        self.global_pool = nn.AdaptiveAvgPool2d(1)
        self.global_head = nn.Sequential(
            nn.Flatten(), nn.Linear(576, 128), nn.SiLU(), nn.Linear(128, 64)
        )
        self.local_fusion = nn.Conv2d(64, 64, 1)
        self.global_fusion = nn.Linear(64, 64)
        self.grid_depth = int(config["grid_depth"])
        self.coefficient_head = nn.Conv2d(64, 12 * self.grid_depth, 1)
        self.guide = LearnedGuide(int(config["guide_knot_count"]))
        identity = torch.zeros(1, 12, self.grid_depth, 1, 1)
        for channel in range(3):
            identity[0, channel * 4 + channel, :, 0, 0] = 1.0
        self.register_buffer("identity_grid", identity)
        self.register_buffer(
            "image_mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        )
        self.register_buffer(
            "image_std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        )
        state_hidden = int(config["state_hidden"])
        self.state_encoder = nn.Sequential(
            nn.Linear(int(freeze["data"]["state_feature_count"]), state_hidden),
            nn.SiLU(),
            nn.Linear(state_hidden, 64),
            nn.SiLU(),
        )
        self.state_to_local = nn.Linear(64, 64, bias=False)
        self.hsl_head = nn.Sequential(
            nn.Linear(128, int(config["head_hidden"])),
            nn.SiLU(),
            nn.Linear(int(config["head_hidden"]), 18),
        )
        self.risk_head = nn.Sequential(
            nn.Linear(128, int(config["risk_hidden"])),
            nn.SiLU(),
            nn.Linear(int(config["risk_hidden"]), 1),
        )


def _slice_coefficients(coefficients: torch.Tensor, guide: torch.Tensor) -> torch.Tensor:
    batch = coefficients.shape[0]
    height, width = guide.shape[-2:]
    horizontal = torch.linspace(-1.0, 1.0, width, device=guide.device, dtype=guide.dtype)
    vertical = torch.linspace(-1.0, 1.0, height, device=guide.device, dtype=guide.dtype)
    yy, xx = torch.meshgrid(vertical, horizontal, indexing="ij")
    sampling_grid = torch.stack(
        [xx.expand(batch, height, width), yy.expand(batch, height, width), guide[:, 0] * 2.0 - 1.0],
        dim=-1,
    ).unsqueeze(1)
    return F.grid_sample(
        coefficients,
        sampling_grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    ).squeeze(2)


def _forward_adaptive(
    model: RuntimeJointSpatialHSLStudent,
    base: torch.Tensor,
    state: torch.Tensor,
    freeze: dict[str, Any],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    analysis_size = int(freeze["data"]["analysis_size"])
    value = F.interpolate(base, size=(analysis_size, analysis_size), mode="bilinear", align_corners=False)
    value = (value - model.image_mean) / model.image_std
    local_source = None
    for index, layer in enumerate(model.encoder):
        value = layer(value)
        if index == 8:
            local_source = value
    if local_source is None:
        raise RuntimeError("MobileNet local feature was not captured")
    local = model.local(local_source)
    global_feature = model.global_head(model.global_pool(value))
    state_feature = model.state_encoder(state)
    fused = F.silu(
        model.local_fusion(local)
        + model.global_fusion(global_feature)[:, :, None, None]
        + model.state_to_local(state_feature)[:, :, None, None]
    )
    delta = model.coefficient_head(fused)
    batch, _, height, width = delta.shape
    coefficients = model.identity_grid + delta.view(batch, 12, model.grid_depth, height, width)
    sliced = _slice_coefficients(coefficients, model.guide(base))
    affine = sliced.view(batch, 3, 4, *base.shape[-2:])
    image_features = torch.cat([base, torch.ones_like(base[:, :1])], dim=1)
    local_candidate = torch.clamp(torch.sum(affine * image_features[:, None], dim=2), 0.0, 1.0)
    context = torch.cat([global_feature, state_feature], dim=1)
    normalized_hsl = torch.tanh(model.hsl_head(context))
    raw_candidate, _ = hsl.apply_hsl_torch(local_candidate, normalized_hsl, freeze["editor"])
    strength_logits = model.risk_head(context)
    probability = torch.softmax(strength_logits, dim=1)
    strength = torch.sum(probability * model.strength_values.to(strength_logits)[None], dim=1)
    candidate = torch.clamp(
        base + strength[:, None, None, None] * (raw_candidate - base), 0.0, 1.0
    )
    return candidate, strength, strength_logits, normalized_hsl


def create_expert_c_v3_result(
    *,
    original_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    original = _read_bgr(original_path)
    source_height, source_width = original.shape[:2]
    working, downsampled = _bounded_image(original, _maximum_render_side())
    bundle = _load_bundle()

    analysis_started = time.perf_counter()
    state = select_model_features(analyze_image_state(original))
    state_values = np.asarray([state[name] for name in MODEL_FEATURE_NAMES], dtype=np.float32)
    parameters = predict_parameters_from_state(state, model=bundle.v2_model, strength=1.0)
    base_bgr, resolved, base_banding_safety = (
        apply_opencv_parameters_banding_safe(working, parameters)
    )
    analysis_ms = (time.perf_counter() - analysis_started) * 1000.0

    standardized = (state_values - bundle.checkpoint["state_mean"]) / bundle.checkpoint["state_std"]
    base = _bgr_to_tensor(base_bgr, bundle.device)
    state_tensor = torch.from_numpy(standardized[None].astype(np.float32)).to(bundle.device)
    inference_started = time.perf_counter()
    with _INFERENCE_LOCK:
        with torch.no_grad(), torch.amp.autocast("cuda", enabled=bundle.device.type == "cuda"):
            candidate, selected_strength, strength_logits, normalized_hsl = _forward_adaptive(
                bundle.model, base, state_tensor, bundle.freeze
            )
        if bundle.device.type == "cuda":
            torch.cuda.synchronize()
    inference_ms = (time.perf_counter() - inference_started) * 1000.0

    output = _tensor_to_bgr(candidate[0])
    if downsampled:
        output = cv2.resize(output, (source_width, source_height), interpolation=cv2.INTER_LANCZOS4)
    _write_image(result_path, output)
    probabilities = torch.softmax(strength_logits.float(), dim=1)[0].cpu().numpy()
    strength_values = bundle.model.strength_values.detach().cpu().numpy()
    metadata = {
        "model": "expert_c_v3_1_artifact_safe",
        "checkpoint_sha256": bundle.checkpoint_sha256,
        "strength_policy": bundle.freeze["inference_policy"]["strength"],
        "selected_strength": round(float(selected_strength.item()), 6),
        "strength_probabilities": {
            str(float(value)): round(float(probabilities[index]), 6)
            for index, value in enumerate(strength_values.tolist())
        },
        "affine_delta_clamp": bundle.affine_bound,
        "v2_base_banding_guard": base_banding_safety,
        "joint_normalized_hsl": [
            round(float(value), 6)
            for value in normalized_hsl[0].float().cpu().numpy()
        ],
        "render_max_side": _maximum_render_side(),
        "source_size": [source_width, source_height],
        "working_size": [working.shape[1], working.shape[0]],
        "downsampled_for_safety": downsampled,
        "uses_gt_at_inference": False,
        "uses_llm_inside_renderer": False,
        "development_gate_passed": True,
        "development_result_sha256": bundle.development_result_sha256,
        "final_opened": False,
    }
    return {
        "engine": "opencv",
        "parameters": resolved,
        "mask_info": None,
        "render_profile": V3_1_RENDER_PROFILE,
        "render_variant": (
            "banding_safe_v2_base_plus_artifact_safe_local_affine_hsl"
        ),
        "render_safety": metadata,
        "timings_ms": {
            "image_analysis_and_v2_base": round(analysis_ms, 3),
            "v3_1_model": round(inference_ms, 3),
            "total": round((time.perf_counter() - total_started) * 1000.0, 3),
        },
        "explanation": (
            "Expert C V3.1 applied the banding-safe frozen V2 global base "
            "followed by "
            f"artifact-safe local affine/HSL residual at strength {selected_strength.item():.3f}."
        ),
    }


def warmup_expert_c_v3() -> dict[str, Any]:
    started = time.perf_counter()
    bundle = _load_bundle()
    return {
        "model": "expert_c_v3_1_artifact_safe",
        "device": str(bundle.device),
        "checkpoint_sha256": bundle.checkpoint_sha256,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


@lru_cache(maxsize=1)
def _load_bundle() -> V31Bundle:
    result = json.loads(DEFAULT_RESULT_PATH.read_text(encoding="utf-8"))
    freeze = json.loads(DEFAULT_FREEZE_PATH.read_text(encoding="utf-8"))
    if _sha256(DEFAULT_FREEZE_PATH) != result["freeze"]["sha256"]:
        raise RuntimeError("Expert C V3.1 freeze hash mismatch")
    checkpoint_path = Path(result["checkpoint"]["path"])
    if not checkpoint_path.is_absolute():
        checkpoint_path = (REPO_ROOT / checkpoint_path).resolve()
    checkpoint_sha256 = str(result["checkpoint"]["sha256"]).upper()
    if _sha256(checkpoint_path) != checkpoint_sha256:
        raise RuntimeError("Expert C V3.1 checkpoint hash mismatch")
    if result.get("promoted") is not False or result["data"]["training_count"] != 4600:
        raise RuntimeError("Unexpected Expert C V3.1 lifecycle or training count")
    development = json.loads(DEVELOPMENT_RESULT_PATH.read_text(encoding="utf-8"))
    if development.get("status") != "PASS" or development["gates"]["all_passed"] is not True:
        raise RuntimeError("Expert C V3.1 development gate did not pass")
    if development["candidate"]["checkpoint_sha256"] != checkpoint_sha256:
        raise RuntimeError("Development result references a different V3.1 checkpoint")
    if development["data"]["development_evaluation_count_for_v3_1"] != 1:
        raise RuntimeError("Unexpected V3.1 development evaluation count")
    if development["data"]["final_content_accessed"] is not False:
        raise RuntimeError("Final split lifecycle violation")
    development_result_sha256 = _sha256(DEVELOPMENT_RESULT_PATH)

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = RuntimeJointSpatialHSLStudent(freeze)
    strength_values = torch.tensor(freeze["evaluation"]["oracle_strengths"], dtype=torch.float32)
    model.risk_head[-1] = nn.Linear(int(freeze["model"]["risk_hidden"]), len(strength_values))
    model.register_buffer("strength_values", strength_values)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    affine_bound = float(freeze["inference_policy"]["affine_delta_clamp"])
    model.coefficient_head = ClampedHead(model.coefficient_head, affine_bound)
    model.to(device).eval()
    _, v2_model = load_frozen_model(TRAINING_ROOT / "baselines" / "raw_expert_c_supervised_v2.json")
    return V31Bundle(
        model=model,
        freeze=freeze,
        checkpoint=checkpoint,
        checkpoint_sha256=checkpoint_sha256,
        v2_model=v2_model,
        device=device,
        affine_bound=affine_bound,
        development_result_sha256=development_result_sha256,
    )


def _maximum_render_side() -> int:
    value = int(os.getenv("AI_PHOTO_V3_1_MAX_SIDE", "2560"))
    return max(512, min(value, 4096))


def _bounded_image(image: np.ndarray, maximum_side: int) -> tuple[np.ndarray, bool]:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= maximum_side:
        return image, False
    scale = maximum_side / float(longest)
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA), True


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


def _bgr_to_tensor(image: np.ndarray, device: torch.device) -> torch.Tensor:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
    return torch.from_numpy(np.ascontiguousarray(rgb[None])).to(device).float().div_(255.0)


def _tensor_to_bgr(value: torch.Tensor) -> np.ndarray:
    rgb = np.clip(np.rint(value.float().cpu().permute(1, 2, 0).numpy() * 255.0), 0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
