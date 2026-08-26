from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


TRAINING_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TRAINING_ROOT.parent
DEFAULT_REGISTRY = TRAINING_ROOT / "baselines" / "raw_expert_c_supervised_v2.json"


def load_frozen_model(
    registry_path: Path = DEFAULT_REGISTRY,
) -> tuple[dict[str, Any], Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != "raw_expert_c_baseline_v1":
        raise ValueError("Unsupported raw Expert C baseline registry")
    if registry.get("status") != "fresh_holdout_passed":
        raise ValueError("Raw Expert C baseline has not passed fresh holdout")
    artifact = registry["model"]["artifact"]
    artifact_path = (REPO_ROOT / artifact["path"]).resolve()
    if _sha256(artifact_path) != str(artifact["sha256"]).upper():
        raise ValueError("Raw Expert C model artifact hash mismatch")
    model = np.load(artifact_path, allow_pickle=False)
    if str(model["schema_version"][0]) != "raw_expert_c_regressor_v1":
        raise ValueError("Unsupported raw Expert C model artifact")
    if str(model["feature_set"][0]) != "state":
        raise ValueError("Runtime only supports the frozen state feature model")
    return registry, model


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
