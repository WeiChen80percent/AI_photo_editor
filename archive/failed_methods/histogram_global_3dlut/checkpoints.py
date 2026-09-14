"""Checkpoint helpers for the global histogram LUT model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from image_adaptive_3dlut.checkpoints import atomic_torch_save

from .model import HistogramGlobal3DLUT


def load_model_checkpoint(
    path: str | Path,
    device: torch.device,
) -> tuple[HistogramGlobal3DLUT, dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Checkpoint does not exist: {source}")
    payload = torch.load(source, map_location=device, weights_only=False)
    if payload.get("model_type") != "histogram_global_3dlut":
        raise ValueError(f"Not a histogram-global checkpoint: {source}")
    config = payload.get("model_config", {})
    model = HistogramGlobal3DLUT(
        lut_dim=int(config.get("lut_dim", 33)),
        histogram_bins=int(config.get("histogram_bins", 16)),
        weight_delta_limit=float(config.get("weight_delta_limit", 0.25)),
    ).to(device)
    model.load_state_dict(payload["model"], strict=True)
    return model, payload


__all__ = ["atomic_torch_save", "load_model_checkpoint"]

