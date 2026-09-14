"""Paired Adobe FiveK loading without dependencies on the DeepLPF package."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageOps
from torch.utils.data import Dataset
from torchvision.transforms import functional as TF


def read_manifest(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            missing = {"id", "raw", "target"} - record.keys()
            if missing:
                raise ValueError(f"{path}:{line_number} missing fields: {sorted(missing)}")
            records.append(record)
    if not records:
        raise ValueError(f"Manifest is empty: {path}")
    return records


def load_rgb(path: str | Path) -> Image.Image:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    try:
        with Image.open(path) as image:
            result = ImageOps.exif_transpose(image).convert("RGB").copy()
    except Exception as exc:
        raise OSError(f"Cannot decode {path}: {exc}") from exc
    if min(result.size) <= 0:
        raise ValueError(f"Invalid image size {result.size}: {path}")
    return result


def _cover_resize(image: Image.Image, size: int) -> Image.Image:
    scale = max(size / image.width, size / image.height)
    width = max(size, round(image.width * scale))
    height = max(size, round(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def paired_transform(
    original: Image.Image,
    target: Image.Image,
    size: int,
    *,
    augment: bool,
) -> tuple[torch.Tensor, torch.Tensor]:
    if size < 64:
        raise ValueError("image_size must be at least 64")
    original = _cover_resize(original, size)
    target = _cover_resize(target, size)
    if augment:
        position_x, position_y = random.random(), random.random()
        flip = random.random() < 0.5
    else:
        position_x = position_y = 0.5
        flip = False

    def crop(image: Image.Image) -> Image.Image:
        left = round((image.width - size) * position_x)
        top = round((image.height - size) * position_y)
        return image.crop((left, top, left + size, top + size))

    original, target = crop(original), crop(target)
    if flip:
        original, target = ImageOps.mirror(original), ImageOps.mirror(target)
    return TF.to_tensor(original), TF.to_tensor(target)


class PairedFiveKDataset(Dataset[dict[str, torch.Tensor | str]]):
    def __init__(
        self,
        manifest: str | Path,
        data_root: str | Path,
        *,
        image_size: int = 512,
        augment: bool = False,
        limit: int = 0,
        subset_seed: int = 42,
    ) -> None:
        records = read_manifest(manifest)
        if 0 < limit < len(records):
            indices = list(range(len(records)))
            random.Random(subset_seed).shuffle(indices)
            records = [records[index] for index in indices[:limit]]
        self.records = records
        self.data_root = Path(data_root)
        self.image_size = int(image_size)
        self.augment = bool(augment)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor | str]:
        record = self.records[index]
        filename = str(record["id"])
        try:
            original = load_rgb(self.data_root / str(record["raw"]))
            target = load_rgb(self.data_root / str(record["target"]))
            input_tensor, target_tensor = paired_transform(
                original, target, self.image_size, augment=self.augment
            )
        except Exception as exc:
            raise type(exc)(f"Pair {filename}: {exc}") from exc
        return {"input": input_tensor, "target": target_tensor, "filename": filename}
