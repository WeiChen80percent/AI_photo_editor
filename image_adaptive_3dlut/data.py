"""MIT-Adobe FiveK Expert C paired data loading."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Literal

import torch
from PIL import Image, ImageOps
from torch.utils.data import Dataset
from torchvision.transforms import functional as TF


ResolutionMode = Literal["480p", "original"]


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {source}")
    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON") from exc
            if "id" not in record:
                raise ValueError(f"{source}:{line_number}: missing id")
            records.append(record)
    if not records:
        raise ValueError(f"Manifest is empty: {source}")
    return records


def read_paired_manifest(
    input_manifest: str | Path,
    target_manifest: str | Path | None = None,
) -> list[dict[str, Any]]:
    inputs = _read_jsonl(input_manifest)
    if target_manifest is not None:
        targets = {str(record["id"]): record for record in _read_jsonl(target_manifest)}
        merged: list[dict[str, Any]] = []
        for record in inputs:
            item = dict(record)
            target = targets.get(str(item["id"]))
            if target is None:
                raise ValueError(f"Missing target for {item['id']}")
            item.update(target)
            merged.append(item)
        inputs = merged

    for record in inputs:
        missing = {"id", "raw", "target"} - set(record)
        if missing:
            raise ValueError(f"Record {record.get('id')} missing {sorted(missing)}")
    return inputs


def load_rgb(path: str | Path) -> Image.Image:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Image does not exist: {source}")
    try:
        with Image.open(source) as image:
            return ImageOps.exif_transpose(image).convert("RGB").copy()
    except Exception as exc:
        raise OSError(f"Cannot decode {source}: {exc}") from exc


def resize_short_side(image: Image.Image, short_side: int) -> Image.Image:
    if short_side < 1:
        raise ValueError("short_side must be positive")
    scale = short_side / min(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.BICUBIC)


def align_pair(
    source: Image.Image,
    target: Image.Image,
    *,
    resolution: ResolutionMode,
    short_side: int = 480,
) -> tuple[Image.Image, Image.Image]:
    source_ratio = source.width / source.height
    target_ratio = target.width / target.height
    relative_delta = abs(source_ratio - target_ratio) / max(source_ratio, target_ratio)
    if relative_delta > 0.05:
        raise ValueError(
            f"Pair aspect ratios are not aligned: {source.size} vs {target.size} "
            f"(relative delta {relative_delta:.3f})"
        )
    if resolution == "480p":
        source = resize_short_side(source, short_side)
    elif resolution != "original":
        raise ValueError(f"Unknown resolution mode: {resolution}")
    # Expert-rendered files can differ by a few border pixels.  Matching the
    # source geometry reproduces the author's aligned 480p pair layout.
    target = target.resize(source.size, Image.Resampling.BICUBIC)
    return source, target


def paper_train_augmentation(
    source: Image.Image,
    target: Image.Image,
    rng: random.Random,
) -> tuple[Image.Image, Image.Image]:
    """Crop, flip, then perturb input brightness/saturation as in official code."""

    ratio_h = rng.uniform(0.6, 1.0)
    ratio_w = rng.uniform(0.6, 1.0)
    crop_h = max(1, round(source.height * ratio_h))
    crop_w = max(1, round(source.width * ratio_w))
    top = rng.randint(0, source.height - crop_h)
    left = rng.randint(0, source.width - crop_w)
    box = (left, top, left + crop_w, top + crop_h)
    source = source.crop(box)
    target = target.crop(box)
    if rng.random() > 0.5:
        source = ImageOps.mirror(source)
        target = ImageOps.mirror(target)
    source = TF.adjust_brightness(source, rng.uniform(0.8, 1.2))
    source = TF.adjust_saturation(source, rng.uniform(0.8, 1.2))
    return source, target


class FiveKExpertCDataset(Dataset[dict[str, torch.Tensor | str]]):
    def __init__(
        self,
        manifest: str | Path,
        data_root: str | Path,
        *,
        target_manifest: str | Path | None = None,
        train: bool = False,
        resolution: ResolutionMode = "480p",
        short_side: int = 480,
        limit: int = 0,
        subset_seed: int = 42,
    ) -> None:
        records = read_paired_manifest(manifest, target_manifest)
        if 0 < limit < len(records):
            indices = list(range(len(records)))
            random.Random(subset_seed).shuffle(indices)
            records = [records[index] for index in indices[:limit]]
        self.records = records
        self.data_root = Path(data_root)
        self.train = bool(train)
        self.resolution = resolution
        self.short_side = int(short_side)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor | str]:
        record = self.records[index]
        identifier = str(record["id"])
        try:
            source = load_rgb(self.data_root / str(record["raw"]))
            target = load_rgb(self.data_root / str(record["target"]))
            source, target = align_pair(
                source,
                target,
                resolution=self.resolution,
                short_side=self.short_side,
            )
            if self.train:
                # DataLoader workers receive deterministic Python seeds from PyTorch.
                source, target = paper_train_augmentation(source, target, random)
            source_tensor = TF.to_tensor(source)
            target_tensor = TF.to_tensor(target)
        except Exception as exc:
            raise type(exc)(f"Pair {identifier}: {exc}") from exc
        return {"input": source_tensor, "target": target_tensor, "id": identifier}


def seed_worker(worker_id: int) -> None:
    del worker_id
    worker_seed = torch.initial_seed() % (2**32)
    random.seed(worker_seed)

