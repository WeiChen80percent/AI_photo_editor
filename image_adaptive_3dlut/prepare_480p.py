"""Pre-render aligned 480p FiveK pairs, matching the paper's training setup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data import align_pair, load_rgb, read_paired_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True)
    parser.add_argument("--target-manifest", default="")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-root", default="image_adaptive_3dlut/data_480p")
    parser.add_argument("--short-side", type=int, default=480)
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.short_side != 480:
        raise ValueError("Paper-faithful preprocessing requires --short-side 480")
    if not 1 <= args.quality <= 100:
        raise ValueError("JPEG quality must be in [1, 100]")
    data_root = Path(args.data_root)
    output_root = Path(args.output_root)
    records: dict[str, dict] = {}
    for index, manifest in enumerate(args.manifest):
        target_manifest = args.target_manifest if index == 0 and args.target_manifest else None
        for record in read_paired_manifest(manifest, target_manifest):
            records[str(record["id"])] = record
    if args.limit > 0:
        records = dict(list(records.items())[: args.limit])

    written = 0
    skipped = 0
    for index, record in enumerate(records.values(), 1):
        raw_output = output_root / str(record["raw"])
        target_output = output_root / str(record["target"])
        if not args.overwrite and raw_output.is_file() and target_output.is_file():
            skipped += 1
            continue
        source = load_rgb(data_root / str(record["raw"]))
        target = load_rgb(data_root / str(record["target"]))
        source, target = align_pair(
            source,
            target,
            resolution="480p",
            short_side=args.short_side,
        )
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        target_output.parent.mkdir(parents=True, exist_ok=True)
        source.save(raw_output, format="JPEG", quality=args.quality, subsampling=0)
        target.save(target_output, format="JPEG", quality=args.quality, subsampling=0)
        written += 1
        if index % 100 == 0:
            print(f"prepared {index}/{len(records)}")

    summary = {
        "pair_count": len(records),
        "written": written,
        "skipped": skipped,
        "output_root": str(output_root),
        "short_side": args.short_side,
        "jpeg_quality": args.quality,
    }
    (output_root / "prepare_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
