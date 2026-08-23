"""Checkpoint utilities shared by training, evaluation and inference."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch

from .model import ImageAdaptive3DLUT


def atomic_torch_save(payload: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    torch.save(payload, temporary)
    os.replace(temporary, destination)


def load_model_checkpoint(
    path: str | Path,
    device: torch.device,
) -> tuple[ImageAdaptive3DLUT, dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Checkpoint does not exist: {source}")
    payload = torch.load(source, map_location=device, weights_only=False)
    if "model" not in payload:
        raise ValueError(f"Unsupported checkpoint format: {source}")
    model = ImageAdaptive3DLUT(
        num_luts=int(payload.get("num_luts", 3)),
        lut_dim=int(payload.get("lut_dim", 33)),
    ).to(device)
    model.load_state_dict(payload["model"], strict=True)
    return model, payload

