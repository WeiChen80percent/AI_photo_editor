from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    "__pycache__",
    ".dart_tool",
    ".plugin_symlinks",
    "build",
    "ephemeral",
    ".pytest_cache",
    ".venv",
}
EXCLUDED_NAMES = {".flutter-plugins", ".flutter-plugins-dependencies", "Modelfile.runtime"}
RUNTIME_OUTPUT_ROOTS = {
    ROOT / "outputs",
    ROOT / "comparisons",
    ROOT / "test",
    ROOT / "backend" / "storage",
}
REQUIRED = (
    ROOT / "run_residual_fusion.ps1",
    ROOT / "start_backend.ps1",
    ROOT / "start_frontend.ps1",
    ROOT / "predict_residual_fusion.py",
    ROOT / "install_prompt_model.ps1",
    ROOT / "requirements.txt",
    ROOT / "models" / "MODEL_ASSETS.json",
    ROOT / "models" / "Modelfile.prompt-control",
    ROOT / "backend" / "app" / "main.py",
    ROOT / "frontend" / "lib" / "main.dart",
    ROOT / "training" / "outputs" / "expert_c_v3_1_joint_categorical_artifact_safe_full_research001" / "joint_categorical_strength_full_train.pt",
    ROOT / "training" / "outputs" / "expert_c_v3_5_resolution_aligned_selector002" / "resolution_aligned_selector_v3_5.joblib",
    ROOT / "training" / "outputs" / "expert_c_v3_6_catastrophic_guard002" / "catastrophic_guard_v3_6.joblib",
    ROOT / "training" / "outputs" / "expert_c_continuous_spatial_strength_post_final_5000_demo001" / "artifact_manifest.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the ResidualFusion release package")
    parser.add_argument(
        "--strict-clean",
        action="store_true",
        help="Fail when generated Flutter/Python caches are present.",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def is_runtime_output(path: Path) -> bool:
    return any(root == path or root in path.parents for root in RUNTIME_OUTPUT_ROOTS)


def is_generated(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return path.name in EXCLUDED_NAMES or any(part in EXCLUDED_PARTS for part in relative.parts)


def main() -> None:
    args = parse_args()
    missing = [str(path) for path in REQUIRED if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing release files:\n" + "\n".join(missing))

    generated = [path for path in ROOT.rglob("*") if is_generated(path)]
    if args.strict_clean and generated:
        raise RuntimeError("Generated/cache files remain in release: " + str(generated[0]))

    continuous_path = (
        ROOT / "training" / "outputs" / "expert_c_continuous_spatial_strength_post_final_5000_demo001" / "result.json"
    )
    continuous = json.loads(continuous_path.read_text(encoding="utf-8"))
    manifest_path = ROOT / continuous["artifact"]["manifest_path"]
    if sha256(manifest_path) != continuous["artifact"]["manifest_sha256"]:
        raise RuntimeError("Continuous model manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for checkpoint in manifest["checkpoints"]:
        checkpoint_path = ROOT / checkpoint["path"]
        if sha256(checkpoint_path) != checkpoint["sha256"]:
            raise RuntimeError(f"Checkpoint hash mismatch: {checkpoint_path}")

    files = []
    total_bytes = 0
    for path in sorted(item for item in ROOT.rglob("*") if item.is_file()):
        if path.name == "PACKAGE_MANIFEST.json" or is_runtime_output(path) or is_generated(path):
            continue
        size = path.stat().st_size
        total_bytes += size
        files.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "size": size,
                "sha256": sha256(path),
            }
        )
    package = {
        "schema_version": "residual_fusion_package_manifest_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": "ResidualFusion",
        "file_count": len(files),
        "total_bytes_excluding_runtime_outputs": total_bytes,
        "external_dependencies": [
            "CUDA Python environment (package .venv or parent .venv)",
            "Ollama ai-photo-prompt-control:exp007-v1 for prompt mode",
            "local Hugging Face SegFormer cache",
            "Flutter SDK and Chrome for frontend mode",
        ],
        "intentionally_excluded": [
            "FiveK DNG/TIFF dataset",
            "training caches and OOF arrays",
            "old experiment outputs and screenshots",
            "frontend build/.dart_tool",
            "backend user sessions/uploads/results",
        ],
        "files": files,
    }
    (ROOT / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"root={ROOT}")
    print(f"files={len(files)}")
    print(f"size_mb={total_bytes / 1024 / 1024:.2f}")
    print(f"generated_cache_entries_ignored={len(generated)}")
    print("status=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"status=FAIL error={error}", file=sys.stderr)
        raise
