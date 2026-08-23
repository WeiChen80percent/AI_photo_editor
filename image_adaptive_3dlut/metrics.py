"""Paper-compatible PSNR, SSIM and CIE Lab Delta E evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from scipy.ndimage import uniform_filter
from scipy.signal import convolve2d
from skimage.color import rgb2lab


@dataclass(frozen=True)
class ImageMetrics:
    psnr: float
    ssim: float
    delta_e: float


def tensor_to_uint8(tensor: torch.Tensor) -> np.ndarray:
    if tensor.ndim == 4:
        if tensor.shape[0] != 1:
            raise ValueError("Only batch size 1 can be converted to a single image")
        tensor = tensor[0]
    if tensor.ndim != 3 or tensor.shape[0] != 3:
        raise ValueError(f"Expected [3,H,W], got {tuple(tensor.shape)}")
    array = tensor.detach().float().clamp(0.0, 1.0).mul(255.0).round()
    return array.byte().permute(1, 2, 0).cpu().numpy()


def calculate_metrics(prediction: torch.Tensor, target: torch.Tensor) -> ImageMetrics:
    pred = tensor_to_uint8(prediction)
    truth = tensor_to_uint8(target)
    difference = pred.astype(np.float64) - truth.astype(np.float64)
    mse = float(np.mean(np.square(difference)))
    psnr = float("inf") if mse == 0.0 else float(10.0 * np.log10((255.0**2) / mse))
    ssim = matlab_ssim_rgb(truth, pred)
    truth_lab = rgb2lab(truth.astype(np.float64) / 255.0)
    pred_lab = rgb2lab(pred.astype(np.float64) / 255.0)
    delta_e = float(np.linalg.norm(truth_lab - pred_lab, axis=2).mean())
    return ImageMetrics(psnr=psnr, ssim=ssim, delta_e=delta_e)


def _gaussian_window(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    coordinates = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    yy, xx = np.meshgrid(coordinates, coordinates, indexing="ij")
    window = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma * sigma))
    return window / window.sum()


def _ssim_channel(first: np.ndarray, second: np.ndarray) -> float:
    window = _gaussian_window()
    mu1 = convolve2d(first, window, mode="valid")
    mu2 = convolve2d(second, window, mode="valid")
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = convolve2d(first * first, window, mode="valid") - mu1_sq
    sigma2_sq = convolve2d(second * second, window, mode="valid") - mu2_sq
    sigma12 = convolve2d(first * second, window, mode="valid") - mu1_mu2
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    ssim_map = ((2.0 * mu1_mu2 + c1) * (2.0 * sigma12 + c2)) / (
        (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    )
    return float(ssim_map.mean())


def matlab_ssim_rgb(first: np.ndarray, second: np.ndarray) -> float:
    """Reproduce the authors' bundled ssim.m, averaged over RGB channels."""

    if first.shape != second.shape or first.ndim != 3 or first.shape[2] != 3:
        raise ValueError("SSIM expects two equally sized RGB arrays")
    first_float = first.astype(np.float64)
    second_float = second.astype(np.float64)
    factor = max(1, int(np.floor(min(first.shape[:2]) / 256.0 + 0.5)))
    if factor > 1:
        first_float = uniform_filter(first_float, size=(factor, factor, 1), mode="reflect")
        second_float = uniform_filter(second_float, size=(factor, factor, 1), mode="reflect")
        first_float = first_float[::factor, ::factor, :]
        second_float = second_float[::factor, ::factor, :]
    if min(first_float.shape[:2]) < 11:
        raise ValueError("Image is too small for the paper's 11x11 SSIM window")
    return float(
        np.mean(
            [
                _ssim_channel(first_float[..., channel], second_float[..., channel])
                for channel in range(3)
            ]
        )
    )


class MetricAccumulator:
    def __init__(self) -> None:
        self.count = 0
        self.psnr = 0.0
        self.ssim = 0.0
        self.delta_e = 0.0

    def update(self, metrics: ImageMetrics) -> None:
        self.count += 1
        self.psnr += metrics.psnr
        self.ssim += metrics.ssim
        self.delta_e += metrics.delta_e

    def averages(self) -> dict[str, float | int]:
        if self.count == 0:
            raise ValueError("No metrics accumulated")
        return {
            "count": self.count,
            "psnr": self.psnr / self.count,
            "ssim": self.ssim / self.count,
            "delta_e": self.delta_e / self.count,
        }


def save_comparison(
    path: str | Path,
    source: torch.Tensor,
    prediction: torch.Tensor,
    target: torch.Tensor,
    *,
    label: str = "RAW                         PREDICTION                  EXPERT C",
) -> None:
    images = [Image.fromarray(tensor_to_uint8(item)) for item in (source, prediction, target)]
    width, height = images[0].size
    canvas = Image.new("RGB", (width * 3, height + 28), "white")
    for index, image in enumerate(images):
        canvas.paste(image, (index * width, 28))
    ImageDraw.Draw(canvas).text((8, 7), label, fill="black")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=95)
