"""Image-Adaptive 3D LUT model from Zeng et al., TPAMI 2020/2022.

The network structure, initialization and LUT fusion follow the authors'
paired-training implementation.  The obsolete custom CUDA interpolation is
replaced by PyTorch's differentiable 5-D grid_sample.  For a 5-D input,
``mode='bilinear'`` performs trilinear interpolation.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


def build_identity_lut(dim: int = 33) -> torch.Tensor:
    """Return an identity LUT with layout [RGB, B, G, R]."""

    if dim < 2:
        raise ValueError("LUT dimension must be at least 2")
    values = torch.linspace(0.0, 1.0, dim, dtype=torch.float32)
    blue, green, red = torch.meshgrid(values, values, values, indexing="ij")
    return torch.stack((red, green, blue), dim=0)


def apply_lut_trilinear(lut: torch.Tensor, image: torch.Tensor) -> torch.Tensor:
    """Apply one image-adaptive 3D LUT per image using trilinear interpolation.

    Args:
        lut: ``[B, 3, D, D, D]`` or ``[3, D, D, D]`` in [B, G, R] lattice order.
        image: ``[B, 3, H, W]`` RGB values in [0, 1].
    """

    if image.ndim != 4 or image.shape[1] != 3:
        raise ValueError(f"Expected image [B,3,H,W], got {tuple(image.shape)}")
    if lut.ndim == 4:
        lut = lut.unsqueeze(0)
    if lut.ndim != 5 or lut.shape[1] != 3:
        raise ValueError(f"Expected LUT [B,3,D,D,D], got {tuple(lut.shape)}")
    if lut.shape[0] == 1 and image.shape[0] > 1:
        lut = lut.expand(image.shape[0], -1, -1, -1, -1)
    if lut.shape[0] != image.shape[0]:
        raise ValueError("LUT and image batch sizes differ")

    # grid_sample coordinates are ordered x/y/z, hence R/G/B for a LUT stored
    # as [channels, B, G, R].  D_out=1 lets the 5-D operator transform a 2-D image.
    grid = image.permute(0, 2, 3, 1).unsqueeze(1)
    grid = grid.mul(2.0).sub(1.0)
    output = F.grid_sample(
        lut,
        grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )
    return output.squeeze(2)


def _downsample_block(in_channels: int, out_channels: int, *, norm: bool) -> list[nn.Module]:
    layers: list[nn.Module] = [
        nn.Conv2d(in_channels, out_channels, 3, stride=2, padding=1),
        nn.LeakyReLU(0.2),
    ]
    if norm:
        layers.append(nn.InstanceNorm2d(out_channels, affine=True))
    return layers


class WeightPredictor(nn.Module):
    """The paper's 256x256 CNN that predicts content-dependent LUT weights."""

    def __init__(self, num_luts: int = 3, classifier_size: int = 256) -> None:
        super().__init__()
        if num_luts < 1:
            raise ValueError("num_luts must be positive")
        if classifier_size != 256:
            raise ValueError("The paper-faithful classifier size is 256")
        self.num_luts = num_luts
        self.classifier_size = classifier_size
        self.model = nn.Sequential(
            nn.Upsample(size=(256, 256), mode="bilinear", align_corners=False),
            nn.Conv2d(3, 16, 3, stride=2, padding=1),
            nn.LeakyReLU(0.2),
            nn.InstanceNorm2d(16, affine=True),
            *_downsample_block(16, 32, norm=True),
            *_downsample_block(32, 64, norm=True),
            *_downsample_block(64, 128, norm=True),
            *_downsample_block(128, 128, norm=False),
            nn.Dropout(p=0.5),
            nn.Conv2d(128, num_luts, 8, padding=0),
        )
        self.reset_parameters()

    def reset_parameters(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.xavier_normal_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, (nn.BatchNorm2d, nn.InstanceNorm2d)):
                if module.weight is not None:
                    nn.init.normal_(module.weight, 1.0, 0.02)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        # Same near-identity initialization as the authors' paired code.
        final = self.model[-1]
        assert isinstance(final, nn.Conv2d)
        nn.init.constant_(final.bias, 1.0)

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        weights = self.model(image)
        if weights.shape[-2:] != (1, 1):
            raise RuntimeError(f"Weight predictor returned {tuple(weights.shape)}")
        return weights[:, :, 0, 0]


class ImageAdaptive3DLUT(nn.Module):
    """Three learnable basis LUTs plus the paper's small CNN weight predictor."""

    def __init__(self, num_luts: int = 3, lut_dim: int = 33) -> None:
        super().__init__()
        if num_luts != 3:
            raise ValueError("The paper-faithful paired model uses exactly 3 LUTs")
        if lut_dim != 33:
            raise ValueError("The paper-faithful paired model uses 33^3 LUTs")
        basis = torch.zeros(num_luts, 3, lut_dim, lut_dim, lut_dim)
        basis[0].copy_(build_identity_lut(lut_dim))
        self.luts = nn.Parameter(basis)
        self.classifier = WeightPredictor(num_luts=num_luts)
        self.num_luts = num_luts
        self.lut_dim = lut_dim

    def fused_lut(self, weights: torch.Tensor) -> torch.Tensor:
        if weights.ndim != 2 or weights.shape[1] != self.num_luts:
            raise ValueError(f"Expected weights [B,{self.num_luts}], got {tuple(weights.shape)}")
        return torch.einsum("bn,ncdhw->bcdhw", weights, self.luts)

    def forward(self, image: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        weights = self.classifier(image)
        output = apply_lut_trilinear(self.fused_lut(weights), image)
        return output, weights

    @property
    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())

