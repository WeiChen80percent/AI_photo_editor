"""A clean, interpretable hybrid parametric retouching architecture.

The network predicts editing parameters rather than an unconstrained RGB image:
monotone RGB curves, a global colour affine transform, and smooth local maps for
exposure, contrast, and saturation. All prediction heads start at identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
import torch.nn.functional as F
from torch import Tensor, nn


@dataclass(frozen=True)
class ModelConfig:
    curve_bins: int = 16
    analysis_size: int = 256
    base_channels: int = 24
    disable_curves: bool = False
    disable_affine: bool = False
    disable_local: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class ConvBlock(nn.Module):
    def __init__(self, input_channels: int, output_channels: int, *, stride: int = 1) -> None:
        super().__init__()
        groups = max(1, min(8, output_channels // 4))
        while output_channels % groups:
            groups -= 1
        self.layers = nn.Sequential(
            nn.Conv2d(input_channels, output_channels, 3, stride=stride, padding=1, bias=False),
            nn.GroupNorm(groups, output_channels),
            nn.SiLU(inplace=True),
            nn.Conv2d(output_channels, output_channels, 3, padding=1, groups=output_channels, bias=False),
            nn.Conv2d(output_channels, output_channels, 1, bias=False),
            nn.GroupNorm(groups, output_channels),
            nn.SiLU(inplace=True),
        )

    def forward(self, value: Tensor) -> Tensor:
        return self.layers(value)


def _luminance(image: Tensor) -> Tensor:
    weights = image.new_tensor((0.2126, 0.7152, 0.0722)).view(1, 3, 1, 1)
    return (image * weights).sum(dim=1, keepdim=True)


def _bounded_exposure(image: Tensor, ev: Tensor) -> Tensor:
    gain = torch.pow(image.new_tensor(2.0), ev)
    return image * gain / (1.0 + image * (gain - 1.0) + 1e-6)


def _contrast(image: Tensor, amount: Tensor) -> Tensor:
    safe = image.clamp(1e-4, 1.0 - 1e-4)
    logits = torch.logit(safe)
    return torch.sigmoid(logits * (1.0 + amount))


def _saturation(image: Tensor, amount: Tensor) -> Tensor:
    luminance = _luminance(image)
    return luminance + (image - luminance) * (1.0 + amount)


def _apply_affine(image: Tensor, parameters: Tensor) -> Tensor:
    batch = image.shape[0]
    delta_matrix = 0.12 * torch.tanh(parameters[:, :9]).view(batch, 3, 3)
    identity = torch.eye(3, device=image.device, dtype=image.dtype).unsqueeze(0)
    matrix = identity + delta_matrix
    bias = 0.06 * torch.tanh(parameters[:, 9:12]).view(batch, 3, 1, 1)
    transformed = torch.einsum("bij,bjhw->bihw", matrix, image) + bias
    return transformed.clamp(0.0, 1.0)


def _apply_monotone_curves(image: Tensor, logits: Tensor) -> tuple[Tensor, Tensor]:
    batch, channels, height, width = image.shape
    increments = F.softplus(logits) + 1e-4
    increments = increments / increments.sum(dim=-1, keepdim=True)
    points = torch.cat((increments.new_zeros(batch, 3, 1), increments.cumsum(dim=-1)), dim=-1)
    bins = logits.shape[-1]
    position = image.clamp(0.0, 1.0) * bins
    lower_index = position.floor().long().clamp(max=bins - 1)
    fraction = position - lower_index.to(position.dtype)
    flat_index = lower_index.reshape(batch, channels, -1)
    lower = points.gather(2, flat_index).reshape(batch, channels, height, width)
    upper = points.gather(2, flat_index + 1).reshape(batch, channels, height, width)
    return lower + fraction * (upper - lower), points


class HybridParametricRetouchNet(nn.Module):
    """Predict global and local, human-readable editing parameters."""

    architecture_version = "hybrid-parametric-v1"

    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        c = self.config.base_channels
        self.encoder = nn.Sequential(
            ConvBlock(3, c, stride=2),
            ConvBlock(c, c * 2, stride=2),
            ConvBlock(c * 2, c * 3, stride=2),
            ConvBlock(c * 3, c * 4, stride=2),
        )
        global_parameter_count = 3 * self.config.curve_bins + 12 + 3
        self.global_head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(c * 4, c * 4),
            nn.SiLU(inplace=True),
            nn.Linear(c * 4, global_parameter_count),
        )
        self.local_head = nn.Sequential(
            ConvBlock(c * 4, c * 2),
            nn.Conv2d(c * 2, 3, 1),
        )
        nn.init.zeros_(self.global_head[-1].weight)
        nn.init.zeros_(self.global_head[-1].bias)
        nn.init.zeros_(self.local_head[-1].weight)
        nn.init.zeros_(self.local_head[-1].bias)

    def _analysis_image(self, image: Tensor) -> Tensor:
        height, width = image.shape[-2:]
        scale = min(1.0, self.config.analysis_size / max(height, width))
        resized = (max(32, round(height * scale)), max(32, round(width * scale)))
        return F.interpolate(image, size=resized, mode="bilinear", align_corners=False)

    def forward_with_aux(self, image: Tensor) -> tuple[Tensor, dict[str, Tensor]]:
        if image.ndim != 4 or image.shape[1] != 3:
            raise ValueError(f"Expected BCHW RGB input, got {tuple(image.shape)}")
        features = self.encoder(self._analysis_image(image))
        parameters = self.global_head(features)
        curve_count = 3 * self.config.curve_bins
        curve_logits = parameters[:, :curve_count].view(-1, 3, self.config.curve_bins)
        affine_parameters = parameters[:, curve_count : curve_count + 12]
        controls = parameters[:, curve_count + 12 :].view(-1, 3, 1, 1)

        output = image
        global_ev = 2.0 * torch.tanh(controls[:, 0:1])
        global_contrast = 0.6 * torch.tanh(controls[:, 1:2])
        global_saturation = 0.6 * torch.tanh(controls[:, 2:3])
        output = _bounded_exposure(output, global_ev)
        output = _contrast(output, global_contrast)
        output = _saturation(output, global_saturation).clamp(0.0, 1.0)
        if not self.config.disable_affine:
            output = _apply_affine(output, affine_parameters)

        local_raw = self.local_head(features)
        local_raw = F.interpolate(local_raw, image.shape[-2:], mode="bilinear", align_corners=False)
        local_ev = 1.5 * torch.tanh(local_raw[:, 0:1])
        local_contrast = 0.5 * torch.tanh(local_raw[:, 1:2])
        local_saturation = 0.5 * torch.tanh(local_raw[:, 2:3])
        if not self.config.disable_local:
            output = _bounded_exposure(output, local_ev)
            output = _contrast(output, local_contrast)
            output = _saturation(output, local_saturation).clamp(0.0, 1.0)

        curve_points = image.new_empty(0)
        if not self.config.disable_curves:
            output, curve_points = _apply_monotone_curves(output, curve_logits)
        aux = {
            "global_parameters": parameters,
            "local_maps": torch.cat((local_ev, local_contrast, local_saturation), dim=1),
            "curve_points": curve_points,
        }
        return output.clamp(0.0, 1.0), aux

    def forward(self, image: Tensor) -> Tensor:
        return self.forward_with_aux(image)[0]


def load_checkpoint(path: str, device: str | torch.device = "cpu") -> HybridParametricRetouchNet:
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if payload.get("architecture_version") != HybridParametricRetouchNet.architecture_version:
        raise ValueError(f"Unsupported architecture in checkpoint: {payload.get('architecture_version')}")
    config = ModelConfig(**payload["model_config"])
    model = HybridParametricRetouchNet(config)
    model.load_state_dict(payload["model_state_dict"], strict=True)
    model.to(device).eval()
    return model
