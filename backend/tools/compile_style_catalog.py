from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.style_catalog_importer import compile_style_catalog


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile the reviewed style YAML into a production lock file."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "catalog.source.yaml",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "sources.yaml",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "assets",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "catalog.lock.json",
    )
    args = parser.parse_args()
    payload = compile_style_catalog(
        source_path=args.source,
        source_manifest_path=args.sources,
        asset_root=args.assets,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "catalog_version": payload["catalog_version"],
                "style_count": len(payload["styles"]),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
