"""Conservative objective for histogram-conditioned global LUT fine-tuning."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from image_adaptive_3dlut.losses import LUTRegularization


def _gaussian_window(size: int = 11, sigma: float = 1.5) -> torch.Tensor:
    coordinates = torch.arange(size, dtype=torch.float32) - (size - 1) / 2
    kernel = torch.exp(-coordinates.square() / (2 * sigma**2))
    kernel = kernel / kernel.sum()
    return torch.outer(kernel, kernel).view(1, 1, size, size).repeat(3, 1, 1, 1)


def differentiable_ssim(first: torch.Tensor, second: torch.Tensor) -> torch.Tensor:
    window = _gaussian_window().to(device=first.device, dtype=first.dtype)
    mu1 = F.conv2d(first, window, padding=5, groups=3)
    mu2 = F.conv2d(second, window, padding=5, groups=3)
    mu1_sq, mu2_sq, mu12 = mu1.square(), mu2.square(), mu1 * mu2
    sigma1 = F.conv2d(first.square(), window, padding=5, groups=3) - mu1_sq
    sigma2 = F.conv2d(second.square(), window, padding=5, groups=3) - mu2_sq
    sigma12 = F.conv2d(first * second, window, padding=5, groups=3) - mu12
    c1, c2 = 0.01**2, 0.03**2
    score = ((2 * mu12 + c1) * (2 * sigma12 + c2)) / (
        (mu1_sq + mu2_sq + c1) * (sigma1 + sigma2 + c2) + 1e-8
    )
    return score.mean()


def rgb_to_lab(image: torch.Tensor) -> torch.Tensor:
    rgb = image.clamp(0.0, 1.0)
    linear = torch.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055).pow(2.4))
    red, green, blue = linear.unbind(dim=1)
    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / 1.08883
    epsilon, kappa = 216 / 24389, 24389 / 27

    def transform(value: torch.Tensor) -> torch.Tensor:
        return torch.where(
            value > epsilon,
            value.clamp_min(epsilon).pow(1 / 3),
            (kappa * value + 16) / 116,
        )

    fx, fy, fz = transform(x), transform(y), transform(z)
    return torch.stack((116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)), dim=1)


@dataclass(frozen=True)
class GlobalLossValues:
    total: torch.Tensor
    mse: torch.Tensor
    ssim: torch.Tensor
    delta_e: torch.Tensor
    smoothness: torch.Tensor
    monotonicity: torch.Tensor
    weight_delta_norm: torch.Tensor
    strength_anchor: torch.Tensor


class GlobalHistogramLoss(nn.Module):
    def __init__(
        self,
        *,
        lambda_smooth: float = 1e-4,
        lambda_monotonicity: float = 10.0,
        lambda_ssim: float = 0.01,
        lambda_delta_e: float = 0.01,
        lambda_weight_delta: float = 1e-3,
        lambda_strength_anchor: float = 1e-3,
    ) -> None:
        super().__init__()
        self.regularization = LUTRegularization(33)
        self.lambda_smooth = float(lambda_smooth)
        self.lambda_monotonicity = float(lambda_monotonicity)
        self.lambda_ssim = float(lambda_ssim)
        self.lambda_delta_e = float(lambda_delta_e)
        self.lambda_weight_delta = float(lambda_weight_delta)
        self.lambda_strength_anchor = float(lambda_strength_anchor)

    def forward(
        self,
        prediction: torch.Tensor,
        target: torch.Tensor,
        luts: torch.Tensor,
        auxiliary: dict[str, torch.Tensor],
    ) -> GlobalLossValues:
        mse = F.mse_loss(prediction, target)
        bounded = prediction.clamp(0.0, 1.0)
        ssim_loss = 1.0 - differentiable_ssim(bounded, target)
        lab_difference = rgb_to_lab(bounded) - rgb_to_lab(target)
        delta_e = torch.sqrt(lab_difference.square().sum(dim=1) + 1e-8).mean() / 100.0
        lut_tv, monotonicity = self.regularization(luts)
        smoothness = lut_tv + auxiliary["weights"].square().mean()
        weight_delta_norm = auxiliary["weight_delta"].square().mean()
        strength_anchor = (auxiliary["strength"] - 1.0).square().mean()
        total = (
            mse
            + self.lambda_ssim * ssim_loss
            + self.lambda_delta_e * delta_e
            + self.lambda_smooth * smoothness
            + self.lambda_monotonicity * monotonicity
            + self.lambda_weight_delta * weight_delta_norm
            + self.lambda_strength_anchor * strength_anchor
        )
        return GlobalLossValues(
            total,
            mse,
            ssim_loss,
            delta_e,
            smoothness,
            monotonicity,
            weight_delta_norm,
            strength_anchor,
        )

