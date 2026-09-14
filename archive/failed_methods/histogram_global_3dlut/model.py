"""Histogram-conditioned global residual 3D LUT model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F

from image_adaptive_3dlut.model import WeightPredictor, apply_lut_trilinear, build_identity_lut


class SoftColorHistogram(nn.Module):
    """Differentiable RGB, luminance and saturation histograms."""

    def __init__(self, bins: int = 16, sample_size: int = 128) -> None:
        super().__init__()
        if bins < 4:
            raise ValueError("bins must be at least four")
        self.bins = int(bins)
        self.sample_size = int(sample_size)
        self.register_buffer("centers", torch.linspace(0.0, 1.0, bins))
        self.bin_width = 1.0 / (bins - 1)

    @property
    def output_dim(self) -> int:
        return self.bins * 5

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        if image.ndim != 4 or image.shape[1] != 3:
            raise ValueError(f"Expected [B,3,H,W], got {tuple(image.shape)}")
        sampled = F.interpolate(
            image,
            size=(self.sample_size, self.sample_size),
            mode="bilinear",
            align_corners=False,
        ).clamp(0.0, 1.0)
        red, green, blue = sampled.unbind(dim=1)
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        saturation = sampled.amax(dim=1) - sampled.amin(dim=1)
        channels = torch.cat((sampled, luminance[:, None], saturation[:, None]), dim=1)
        values = channels.flatten(2).unsqueeze(-1)
        assignments = F.relu(1.0 - (values - self.centers).abs() / self.bin_width)
        histogram = assignments.mean(dim=2)
        histogram = histogram / histogram.sum(dim=-1, keepdim=True).clamp_min(1e-8)
        return histogram.flatten(1)


class HistogramGlobal3DLUT(nn.Module):
    """Baseline-compatible global LUT with histogram residual conditioning."""

    def __init__(
        self,
        *,
        lut_dim: int = 33,
        histogram_bins: int = 16,
        weight_delta_limit: float = 0.25,
    ) -> None:
        super().__init__()
        if lut_dim != 33:
            raise ValueError("The baseline-compatible LUT dimension is 33")
        if weight_delta_limit <= 0:
            raise ValueError("weight_delta_limit must be positive")
        self.lut_dim = int(lut_dim)
        self.num_luts = 3
        self.histogram_bins = int(histogram_bins)
        self.weight_delta_limit = float(weight_delta_limit)

        basis = torch.zeros(3, 3, lut_dim, lut_dim, lut_dim)
        basis[0].copy_(build_identity_lut(lut_dim))
        self.luts = nn.Parameter(basis)
        self.classifier = WeightPredictor(num_luts=3)
        self.histogram = SoftColorHistogram(histogram_bins)
        self.histogram_head = nn.Sequential(
            nn.Linear(self.histogram.output_dim, 64),
            nn.LeakyReLU(0.2),
            nn.Linear(64, 4),
        )
        self.reset_histogram_head()

    def reset_histogram_head(self) -> None:
        first = self.histogram_head[0]
        final = self.histogram_head[-1]
        assert isinstance(first, nn.Linear) and isinstance(final, nn.Linear)
        nn.init.xavier_normal_(first.weight)
        nn.init.zeros_(first.bias)
        nn.init.zeros_(final.weight)
        nn.init.zeros_(final.bias)

    def fused_lut(self, weights: torch.Tensor) -> torch.Tensor:
        if weights.ndim != 2 or weights.shape[1] != 3:
            raise ValueError(f"Expected [B,3] LUT weights, got {tuple(weights.shape)}")
        return torch.einsum("bn,ncdhw->bcdhw", weights, self.luts)

    def forward(self, image: torch.Tensor) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        cnn_weights = self.classifier(image)
        histogram_features = self.histogram(image)
        histogram_output = self.histogram_head(histogram_features)
        weight_delta = self.weight_delta_limit * torch.tanh(histogram_output[:, :3])
        strength = 2.0 * torch.sigmoid(histogram_output[:, 3:4])
        weights = cnn_weights + weight_delta
        lut_output = apply_lut_trilinear(self.fused_lut(weights), image)
        output = image + strength.view(-1, 1, 1, 1) * (lut_output - image)
        return output, {
            "weights": weights,
            "cnn_weights": cnn_weights,
            "weight_delta": weight_delta,
            "strength": strength,
            "histogram_features": histogram_features,
        }

    def initialize_from_baseline(self, checkpoint: str | Path) -> dict[str, Any]:
        source = Path(checkpoint)
        if not source.is_file():
            raise FileNotFoundError(f"Baseline checkpoint does not exist: {source}")
        payload = torch.load(source, map_location="cpu", weights_only=False)
        state = payload.get("model")
        if not isinstance(state, dict):
            raise ValueError(f"Unsupported baseline checkpoint: {source}")
        with torch.no_grad():
            self.luts.copy_(state["luts"])
        classifier_state = {
            key.removeprefix("classifier."): value
            for key, value in state.items()
            if key.startswith("classifier.")
        }
        self.classifier.load_state_dict(classifier_state, strict=True)
        return {
            "checkpoint": str(source),
            "baseline_epoch": int(payload.get("epoch", -1)),
            "baseline_best_psnr": float(payload.get("best_psnr", float("nan"))),
        }

    def set_training_stage(self, *, head_only: bool) -> None:
        for parameter in self.classifier.parameters():
            parameter.requires_grad_(False)
        self.luts.requires_grad_(not head_only)
        for parameter in self.classifier.model[-1].parameters():
            parameter.requires_grad_(not head_only)
        for parameter in self.histogram_head.parameters():
            parameter.requires_grad_(True)

    def head_parameters(self) -> list[nn.Parameter]:
        return list(self.histogram_head.parameters())

    def final_classifier_parameters(self) -> list[nn.Parameter]:
        return list(self.classifier.model[-1].parameters())

    @property
    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())

