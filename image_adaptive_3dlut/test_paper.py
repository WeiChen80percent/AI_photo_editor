"""Unit tests for the paper-faithful model and training primitives."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import torch
from PIL import Image

from .data import FiveKExpertCDataset
from .losses import LUTRegularization, PairedLUTLoss
from .metrics import calculate_metrics
from .model import ImageAdaptive3DLUT, apply_lut_trilinear, build_identity_lut


def reference_trilinear(lut: torch.Tensor, image: torch.Tensor) -> torch.Tensor:
    """Small independent CPU reference for one LUT and one image."""

    dim = lut.shape[-1]
    coordinates = image[0].permute(1, 2, 0).clamp(0, 1) * (dim - 1)
    lower = coordinates.floor().long()
    upper = (lower + 1).clamp(max=dim - 1)
    fraction = coordinates - lower.float()
    result = torch.zeros_like(image[0])
    for blue_corner in (0, 1):
        for green_corner in (0, 1):
            for red_corner in (0, 1):
                r_index = upper[..., 0] if red_corner else lower[..., 0]
                g_index = upper[..., 1] if green_corner else lower[..., 1]
                b_index = upper[..., 2] if blue_corner else lower[..., 2]
                r_weight = fraction[..., 0] if red_corner else 1.0 - fraction[..., 0]
                g_weight = fraction[..., 1] if green_corner else 1.0 - fraction[..., 1]
                b_weight = fraction[..., 2] if blue_corner else 1.0 - fraction[..., 2]
                values = lut[:, b_index, g_index, r_index]
                result += values * (r_weight * g_weight * b_weight).unsqueeze(0)
    return result.unsqueeze(0)


class PaperModelTests(unittest.TestCase):
    def test_identity_lut_reconstructs_image(self) -> None:
        image = torch.rand(2, 3, 19, 23)
        identity = build_identity_lut(33)
        output = apply_lut_trilinear(identity, image)
        torch.testing.assert_close(output, image, rtol=0, atol=2e-6)

    def test_grid_sample_matches_independent_reference(self) -> None:
        torch.manual_seed(7)
        lut = torch.rand(3, 5, 5, 5)
        image = torch.rand(1, 3, 7, 9)
        actual = apply_lut_trilinear(lut, image)
        expected = reference_trilinear(lut, image)
        torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-6)

    def test_interpolation_backpropagates_to_lut_and_image(self) -> None:
        lut = torch.rand(1, 3, 5, 5, 5, requires_grad=True)
        image = torch.rand(1, 3, 8, 8, requires_grad=True)
        apply_lut_trilinear(lut, image).mean().backward()
        self.assertIsNotNone(lut.grad)
        self.assertIsNotNone(image.grad)
        self.assertTrue(torch.isfinite(lut.grad).all())
        self.assertTrue(torch.isfinite(image.grad).all())

    def test_paper_model_is_under_600k_parameters(self) -> None:
        model = ImageAdaptive3DLUT()
        self.assertLess(model.parameter_count, 600_000)
        self.assertGreater(model.parameter_count, 590_000)

    def test_identity_and_zero_luts_are_monotonic(self) -> None:
        model = ImageAdaptive3DLUT()
        _, monotonicity = LUTRegularization()(model.luts)
        self.assertEqual(float(monotonicity), 0.0)

    def test_full_loss_and_optimizer_step(self) -> None:
        torch.manual_seed(11)
        model = ImageAdaptive3DLUT()
        criterion = PairedLUTLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)
        source = torch.rand(1, 3, 64, 64)
        target = torch.rand(1, 3, 64, 64)
        prediction, weights = model(source)
        losses = criterion(prediction, target, model.luts, weights)
        losses.total.backward()
        optimizer.step()
        self.assertTrue(torch.isfinite(losses.total))

    def test_identical_images_have_perfect_metrics(self) -> None:
        image = torch.rand(1, 3, 64, 64)
        metrics = calculate_metrics(image, image)
        self.assertEqual(metrics.psnr, float("inf"))
        self.assertAlmostEqual(metrics.ssim, 1.0, places=12)
        self.assertAlmostEqual(metrics.delta_e, 0.0, places=12)


class DatasetTests(unittest.TestCase):
    def test_manifest_pair_loads_without_geometry_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").mkdir()
            (root / "c").mkdir()
            array = torch.arange(24 * 32 * 3, dtype=torch.uint8).reshape(24, 32, 3).numpy()
            Image.fromarray(array).save(root / "raw" / "one.png")
            Image.fromarray(array).save(root / "c" / "one.png")
            manifest = root / "pairs.jsonl"
            manifest.write_text(
                json.dumps({"id": "one", "raw": "raw/one.png", "target": "c/one.png"}) + "\n",
                encoding="utf-8",
            )
            dataset = FiveKExpertCDataset(manifest, root, resolution="original")
            item = dataset[0]
            self.assertEqual(tuple(item["input"].shape), (3, 24, 32))
            torch.testing.assert_close(item["input"], item["target"])


if __name__ == "__main__":
    unittest.main()
