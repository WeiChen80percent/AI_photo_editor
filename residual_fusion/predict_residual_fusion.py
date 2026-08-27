from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time


PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.expert_c_v3_8_runtime import create_expert_c_v3_8_result  # noqa: E402


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the packaged ResidualFusion image model")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def discover_images(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image extension: {path.suffix}")
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(path)
    iterator = path.rglob("*") if recursive else path.glob("*")
    return sorted(item for item in iterator if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    images = discover_images(input_path, args.recursive)
    if not images:
        raise FileNotFoundError(f"No supported images found: {input_path}")
    if output_dir.exists() and not args.overwrite:
        raise FileExistsError(f"Output exists; choose another directory or pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    records: list[dict[str, object]] = []
    for index, image_path in enumerate(images, start=1):
        relative = image_path.relative_to(input_path) if input_path.is_dir() else Path(image_path.name)
        output_path = output_dir / relative.with_suffix(".png")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        render = create_expert_c_v3_8_result(original_path=image_path, result_path=output_path)
        record = {
            "schema_version": "residual_fusion_packaged_inference_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": "residual_fusion",
            "uses_prompt": False,
            "uses_llm": False,
            "uses_gt_at_inference": False,
            "input_path": str(image_path),
            "input_sha256": sha256(image_path),
            "output_path": str(output_path),
            "output_sha256": sha256(output_path),
            "render": render,
        }
        output_path.with_suffix(".json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        records.append(record)
        print(
            f"[{index}/{len(images)}] {image_path.name} -> {output_path.name} "
            f"action={render['render_safety']['selected_action']}",
            flush=True,
        )

    summary = {
        "schema_version": "residual_fusion_packaged_batch_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": "residual_fusion",
        "count": len(records),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "records": records,
    }
    (output_dir / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"output={output_dir}")
    print("status=PASS")


if __name__ == "__main__":
    main()
