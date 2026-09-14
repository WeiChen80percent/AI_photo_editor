"""Tests for the conservative global histogram LUT."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch

from image_adaptive_3dlut.checkpoints import load_model_checkpoint as load_baseline

from .checkpoints import atomic_torch_save, load_model_checkpoint
from .losses import GlobalHistogramLoss
from .model import HistogramGlobal3DLUT, SoftColorHistogram


BASELINE = Path("image_adaptive_3dlut/trained_model/best.pt")


class GlobalHistogramTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.manual_seed(11)

    def test_histogram_channels_are_normalized(self) -> None:
        module = SoftColorHistogram(bins=16, sample_size=32)
        output = module(torch.rand(2, 3, 39, 51)).view(2, 5, 16)
        torch.testing.assert_close(output.sum(dim=-1), torch.ones(2, 5), atol=1e-6, rtol=0)

    def test_new_head_is_exact_identity_residual(self) -> None:
        model = HistogramGlobal3DLUT().eval()
        _, auxiliary = model(torch.rand(2, 3, 32, 40))
        torch.testing.assert_close(auxiliary["weight_delta"], torch.zeros(2, 3), atol=0, rtol=0)
        torch.testing.assert_close(auxiliary["strength"], torch.ones(2, 1), atol=0, rtol=0)

    @unittest.skipUnless(BASELINE.is_file(), "baseline checkpoint unavailable")
    def test_bootstrap_output_exactly_matches_baseline(self) -> None:
        device = torch.device("cpu")
        baseline, _ = load_baseline(BASELINE, device)
        candidate = HistogramGlobal3DLUT()
        candidate.initialize_from_baseline(BASELINE)
        baseline.eval()
        candidate.eval()
        image = torch.rand(1, 3, 64, 80)
        with torch.no_grad():
            first, _ = baseline(image)
            second, _ = candidate(image)
        torch.testing.assert_close(first, second, atol=1e-7, rtol=1e-6)

    def test_parameter_budget_is_close_to_baseline(self) -> None:
        model = HistogramGlobal3DLUT()
        self.assertEqual(model.parameter_count, 598_960)
        self.assertLess(model.parameter_count, 610_000)

    def test_head_only_stage_freezes_baseline(self) -> None:
        model = HistogramGlobal3DLUT()
        model.set_training_stage(head_only=True)
        self.assertFalse(model.luts.requires_grad)
        self.assertTrue(all(parameter.requires_grad for parameter in model.head_parameters()))
        self.assertTrue(all(not parameter.requires_grad for parameter in model.classifier.parameters()))

    def test_joint_stage_only_unfreezes_luts_and_final_classifier(self) -> None:
        model = HistogramGlobal3DLUT()
        model.set_training_stage(head_only=False)
        self.assertTrue(model.luts.requires_grad)
        self.assertTrue(all(parameter.requires_grad for parameter in model.final_classifier_parameters()))
        early = list(model.classifier.model[1].parameters())
        self.assertTrue(all(not parameter.requires_grad for parameter in early))

    def test_head_only_loss_backpropagates_without_touching_luts(self) -> None:
        model = HistogramGlobal3DLUT().train()
        model.set_training_stage(head_only=True)
        model.classifier.eval()
        source, target = torch.rand(1, 3, 48, 64), torch.rand(1, 3, 48, 64)
        prediction, auxiliary = model(source)
        values = GlobalHistogramLoss()(prediction, target, model.luts, auxiliary)
        values.total.backward()
        self.assertIsNone(model.luts.grad)
        final = model.histogram_head[-1]
        self.assertGreater(float(final.weight.grad.abs().sum()), 0.0)

    def test_joint_loss_reaches_luts(self) -> None:
        model = HistogramGlobal3DLUT().train()
        model.set_training_stage(head_only=False)
        source, target = torch.rand(1, 3, 48, 64), torch.rand(1, 3, 48, 64)
        prediction, auxiliary = model(source)
        values = GlobalHistogramLoss()(prediction, target, model.luts, auxiliary)
        values.total.backward()
        self.assertIsNotNone(model.luts.grad)
        self.assertGreater(float(model.luts.grad.abs().sum()), 0.0)

    def test_checkpoint_round_trip(self) -> None:
        model = HistogramGlobal3DLUT()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.pt"
            atomic_torch_save(
                {
                    "model_type": "histogram_global_3dlut",
                    "model": model.state_dict(),
                    "model_config": {
                        "lut_dim": 33,
                        "histogram_bins": 16,
                        "weight_delta_limit": 0.25,
                    },
                    "epoch": 0,
                },
                path,
            )
            restored, payload = load_model_checkpoint(path, torch.device("cpu"))
            self.assertEqual(payload["epoch"], 0)
            for first, second in zip(model.parameters(), restored.parameters(), strict=True):
                torch.testing.assert_close(first, second)


if __name__ == "__main__":
    unittest.main()

