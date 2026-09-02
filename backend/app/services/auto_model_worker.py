"""Image-only ResidualFusion worker.

This file is launched directly in a dedicated Python process.  It deliberately
does not import the official backend's ``app`` package; instead it places only
the delivered ResidualFusion backend on ``sys.path``.  That isolates the
delivery package's legacy ``app`` namespace without integrating its old API,
LLM, history, or Flutter code into the product backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any


OFFICIAL_BACKEND_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = OFFICIAL_BACKEND_DIR.parent
RESIDUAL_BACKEND_DIR = REPOSITORY_ROOT / "residual_fusion" / "backend"
if str(RESIDUAL_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(RESIDUAL_BACKEND_DIR))

from app.services.expert_c_v3_8_runtime import (  # noqa: E402
    create_expert_c_v3_8_result,
    warmup_expert_c_v3_8,
)


def _cuda_metadata() -> dict[str, Any]:
    try:
        import torch

        if not torch.cuda.is_available():
            return {"available": False}
        return {
            "available": True,
            "device": torch.cuda.get_device_name(torch.cuda.current_device()),
            "memory_allocated_bytes": int(torch.cuda.memory_allocated()),
            "max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        }
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"available": False, "diagnostic": str(exc)}


def _handle(payload: dict[str, Any]) -> dict[str, Any]:
    command = str(payload.get("command") or "enhance")
    if command == "ping":
        return {"status": "ok", "command": command, "pid": os.getpid()}
    if command == "warmup":
        started = time.perf_counter()
        metadata = warmup_expert_c_v3_8()
        return {
            "status": "ok",
            "command": command,
            "metadata": metadata,
            "cuda": _cuda_metadata(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    if command == "shutdown":
        return {"status": "ok", "command": command, "shutdown": True}
    if command != "enhance":
        raise ValueError(f"Unsupported worker command: {command}")

    source_path = Path(str(payload.get("source_path") or "")).resolve()
    result_path = Path(str(payload.get("result_path") or "")).resolve()
    started = time.perf_counter()
    render = create_expert_c_v3_8_result(
        original_path=source_path,
        result_path=result_path,
    )
    return {
        "status": "ok",
        "command": command,
        "render": render,
        "cuda": _cuda_metadata(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        shutdown = False
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("Worker request must be a JSON object")
            response = _handle(payload)
            shutdown = bool(response.get("shutdown"))
        except Exception as exc:  # keep the worker alive after one bad image
            response = {
                "status": "error",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=12),
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if shutdown:
            break


if __name__ == "__main__":
    main()
