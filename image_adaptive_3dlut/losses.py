"""Paper-faithful paired loss for Image-Adaptive 3D LUT."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class LossValues:
    total: torch.Tensor
    mse: torch.Tensor
    smoothness: torch.Tensor
    monotonicity: torch.Tensor
    weight_norm: torch.Tensor
    lut_tv: torch.Tensor


class LUTRegularization(nn.Module):
    """Smoothness and monotonicity terms from equations 11-13 of the paper."""

    def __init__(self, dim: int = 33) -> None:
        super().__init__()
        if dim < 2:
            raise ValueError("LUT dimension must be at least 2")
        weight_r = torch.ones(1, 3, dim, dim, dim - 1)
        weight_g = torch.ones(1, 3, dim, dim - 1, dim)
        weight_b = torch.ones(1, 3, dim - 1, dim, dim)
        weight_r[..., 0] *= 2.0
        weight_r[..., -1] *= 2.0
        weight_g[..., 0, :] *= 2.0
        weight_g[..., -1, :] *= 2.0
        weight_b[..., 0, :, :] *= 2.0
        weight_b[..., -1, :, :] *= 2.0
        self.register_buffer("weight_r", weight_r)
        self.register_buffer("weight_g", weight_g)
        self.register_buffer("weight_b", weight_b)

    def forward(self, luts: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if luts.ndim != 5 or luts.shape[1] != 3:
            raise ValueError(f"Expected basis LUTs [N,3,D,D,D], got {tuple(luts.shape)}")
        diff_r = luts[..., :-1] - luts[..., 1:]
        diff_g = luts[..., :-1, :] - luts[..., 1:, :]
        diff_b = luts[..., :-1, :, :] - luts[..., 1:, :, :]

        # The original code calculates the mean for each LUT and then sums LUTs.
        reduce_dims = (1, 2, 3, 4)
        tv_per_lut = (
            (diff_r.square() * self.weight_r).mean(dim=reduce_dims)
            + (diff_g.square() * self.weight_g).mean(dim=reduce_dims)
            + (diff_b.square() * self.weight_b).mean(dim=reduce_dims)
        )
        monotonicity_per_lut = (
            F.relu(diff_r).mean(dim=reduce_dims)
            + F.relu(diff_g).mean(dim=reduce_dims)
            + F.relu(diff_b).mean(dim=reduce_dims)
        )
        return tv_per_lut.sum(), monotonicity_per_lut.sum()


class PairedLUTLoss(nn.Module):
    def __init__(
        self,
        *,
        lut_dim: int = 33,
        lambda_smooth: float = 1e-4,
        lambda_monotonicity: float = 10.0,
    ) -> None:
        super().__init__()
        self.regularization = LUTRegularization(lut_dim)
        self.lambda_smooth = float(lambda_smooth)
        self.lambda_monotonicity = float(lambda_monotonicity)

    def forward(
        self,
        prediction: torch.Tensor,
        target: torch.Tensor,
        luts: torch.Tensor,
        weights: torch.Tensor,
    ) -> LossValues:
        mse = F.mse_loss(prediction, target)
        lut_tv, monotonicity = self.regularization(luts)
        weight_norm = weights.square().mean()
        smoothness = lut_tv + weight_norm
        total = mse + self.lambda_smooth * smoothness + self.lambda_monotonicity * monotonicity
        return LossValues(total, mse, smoothness, monotonicity, weight_norm, lut_tv)

