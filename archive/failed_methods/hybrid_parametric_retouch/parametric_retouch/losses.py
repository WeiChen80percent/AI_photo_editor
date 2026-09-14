"""Independent losses for paired Expert-C retouching."""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor, nn


def _ssim(image: Tensor, target: Tensor, window: int = 7) -> Tensor:
    padding = window // 2
    mean_x = F.avg_pool2d(image, window, stride=1, padding=padding)
    mean_y = F.avg_pool2d(target, window, stride=1, padding=padding)
    variance_x = F.avg_pool2d(image * image, window, 1, padding) - mean_x.square()
    variance_y = F.avg_pool2d(target * target, window, 1, padding) - mean_y.square()
    covariance = F.avg_pool2d(image * target, window, 1, padding) - mean_x * mean_y
    c1, c2 = 0.01**2, 0.03**2
    score = ((2 * mean_x * mean_y + c1) * (2 * covariance + c2)) / (
        (mean_x.square() + mean_y.square() + c1) * (variance_x + variance_y + c2) + 1e-8
    )
    return score.clamp(-1.0, 1.0).mean()


def _srgb_to_linear(image: Tensor) -> Tensor:
    return torch.where(image <= 0.04045, image / 12.92, ((image + 0.055) / 1.055).pow(2.4))


def _rgb_to_lab(image: Tensor) -> Tensor:
    rgb = _srgb_to_linear(image.clamp(0.0, 1.0))
    matrix = image.new_tensor(
        ((0.4124564, 0.3575761, 0.1804375),
         (0.2126729, 0.7151522, 0.0721750),
         (0.0193339, 0.1191920, 0.9503041))
    )
    xyz = torch.einsum("ij,bjhw->bihw", matrix, rgb)
    white = image.new_tensor((0.95047, 1.0, 1.08883)).view(1, 3, 1, 1)
    xyz = xyz / white
    delta = 6.0 / 29.0
    f = torch.where(xyz > delta**3, xyz.clamp_min(1e-8).pow(1.0 / 3.0), xyz / (3 * delta**2) + 4.0 / 29.0)
    lightness = 116 * f[:, 1:2] - 16
    a = 500 * (f[:, 0:1] - f[:, 1:2])
    b = 200 * (f[:, 1:2] - f[:, 2:3])
    return torch.cat((lightness / 100.0, a / 128.0, b / 128.0), dim=1)


def _gradient(image: Tensor) -> tuple[Tensor, Tensor]:
    return image[..., :, 1:] - image[..., :, :-1], image[..., 1:, :] - image[..., :-1, :]


def _total_variation(value: Tensor) -> Tensor:
    horizontal, vertical = _gradient(value)
    return horizontal.abs().mean() + vertical.abs().mean()


class RetouchLoss(nn.Module):
    def __init__(
        self,
        *,
        pixel_weight: float = 0.55,
        lab_weight: float = 0.25,
        ssim_weight: float = 0.15,
        gradient_weight: float = 0.05,
        local_smoothness_weight: float = 2e-4,
        parameter_weight: float = 1e-6,
    ) -> None:
        super().__init__()
        self.weights = {
            "pixel": pixel_weight,
            "lab": lab_weight,
            "ssim": ssim_weight,
            "gradient": gradient_weight,
            "local_smoothness": local_smoothness_weight,
            "parameter": parameter_weight,
        }

    def forward(self, prediction: Tensor, target: Tensor, aux: dict[str, Tensor]) -> tuple[Tensor, dict[str, Tensor]]:
        difference = prediction - target
        pixel = torch.sqrt(difference.square() + 1e-6).mean()
        lab = (_rgb_to_lab(prediction) - _rgb_to_lab(target)).abs().mean()
        ssim = 1.0 - _ssim(prediction, target)
        pred_dx, pred_dy = _gradient(prediction)
        target_dx, target_dy = _gradient(target)
        gradient = (pred_dx - target_dx).abs().mean() + (pred_dy - target_dy).abs().mean()
        local_smoothness = _total_variation(aux["local_maps"])
        parameter = aux["global_parameters"].square().mean()
        terms = {
            "pixel": pixel,
            "lab": lab,
            "ssim": ssim,
            "gradient": gradient,
            "local_smoothness": local_smoothness,
            "parameter": parameter,
        }
        total = sum(self.weights[name] * value for name, value in terms.items())
        return total, terms
