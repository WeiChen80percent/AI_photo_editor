from __future__ import annotations

import atexit
from collections import deque
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading
import time
from typing import Any

import numpy as np
from PIL import Image, ImageOps
import torch
from torchvision.transforms import functional as TF

from app.services.auto_model_schema import (
    AutoModelError,
    AutoModelResult,
    EXPERT_FAITHFUL_MODEL_KEY,
    VIVID_MODEL_KEY,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    # The official server is normally launched from backend/, while the
    # delivered LUT package is a repository-root sibling of backend/.
    sys.path.insert(0, str(REPOSITORY_ROOT))
WEI_CHECKPOINT_PATH = (
    REPOSITORY_ROOT / "image_adaptive_3dlut" / "trained_model" / "best.pt"
)
WEI_CHECKPOINT_SHA256 = (
    "6A1BCB229A67F221851CC1314DF8279354BD807044F523DA5D83B62DB44C58FE"
)
KAI_WORKER_PATH = Path(__file__).resolve().with_name("auto_model_worker.py")
KAI_PACKAGE_MANIFEST = REPOSITORY_ROOT / "residual_fusion" / "PACKAGE_MANIFEST.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


class WeiLutAdapter:
    model_key = EXPERT_FAITHFUL_MODEL_KEY
    model_family = "image_adaptive_3dlut"

    def __init__(self, checkpoint_path: Path = WEI_CHECKPOINT_PATH):
        self.checkpoint_path = Path(checkpoint_path)
        self._lock = threading.Lock()
        self._model: torch.nn.Module | None = None
        self._checkpoint: dict[str, Any] | None = None
        self._device: torch.device | None = None
        self._load_ms: float | None = None

    def asset_identity(self) -> dict[str, Any]:
        actual = (
            _sha256_file(self.checkpoint_path)
            if self.checkpoint_path.is_file()
            else None
        )
        return {
            "provider": "hugging_face",
            "repository": "WeiChen80percent/image-adaptive-3dlut",
            "filename": "best.pt",
            "checkpoint_path": str(self.checkpoint_path),
            "expected_sha256": WEI_CHECKPOINT_SHA256,
            "actual_sha256": actual,
            "verified": actual == WEI_CHECKPOINT_SHA256,
        }

    def health(self) -> dict[str, Any]:
        identity = self.asset_identity()
        if not self.checkpoint_path.is_file():
            return {
                "model_key": self.model_key,
                "status": "unavailable",
                "code": "checkpoint_missing",
                "asset": identity,
                "loaded": False,
            }
        if not identity["verified"]:
            return {
                "model_key": self.model_key,
                "status": "unavailable",
                "code": "checkpoint_hash_mismatch",
                "asset": identity,
                "loaded": False,
            }
        return {
            "model_key": self.model_key,
            "status": "ready",
            "asset": identity,
            "loaded": self._model is not None,
            "device": str(self._device) if self._device is not None else None,
        }

    def warmup(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded(self._preferred_device())
            return self.health()

    def enhance(self, source_path: Path, result_path: Path) -> AutoModelResult:
        with self._lock:
            preferred = self._preferred_device()
            try:
                return self._enhance_locked(source_path, result_path, preferred)
            except (torch.OutOfMemoryError, RuntimeError) as exc:
                if not _is_cuda_oom(exc) or preferred.type != "cuda":
                    raise self._typed_error(exc) from exc
                self._release_model()
                torch.cuda.empty_cache()
                try:
                    result = self._enhance_locked(
                        source_path,
                        result_path,
                        torch.device("cpu"),
                    )
                except Exception as fallback_exc:
                    raise self._typed_error(fallback_exc) from fallback_exc
                return AutoModelResult(
                    model_key=result.model_key,
                    model_family=result.model_family,
                    runtime_metadata={
                        **result.runtime_metadata,
                        "cuda_fallback_reason": str(exc),
                    },
                    timings_ms=result.timings_ms,
                    warning_flags=(*result.warning_flags, "cuda_oom_cpu_fallback"),
                )
            except Exception as exc:
                raise self._typed_error(exc) from exc

    def _enhance_locked(
        self,
        source_path: Path,
        result_path: Path,
        device: torch.device,
    ) -> AutoModelResult:
        from image_adaptive_3dlut.data import load_rgb

        started = time.perf_counter()
        self._ensure_loaded(device)
        assert self._model is not None
        assert self._checkpoint is not None
        image = load_rgb(source_path)
        source = TF.to_tensor(image).unsqueeze(0).to(device)
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        inference_started = time.perf_counter()
        with torch.inference_mode():
            prediction, weights = self._model(source)
        inference_ms = (time.perf_counter() - inference_started) * 1000.0
        array = (
            prediction[0]
            .detach()
            .clamp(0.0, 1.0)
            .mul(255.0)
            .round()
            .to(torch.uint8)
            .permute(1, 2, 0)
            .cpu()
            .numpy()
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.asarray(array), mode="RGB").save(result_path, format="PNG")
        peak = (
            int(torch.cuda.max_memory_allocated(device))
            if device.type == "cuda"
            else None
        )
        return AutoModelResult(
            model_key=self.model_key,
            model_family=self.model_family,
            runtime_metadata={
                "checkpoint_epoch": int(self._checkpoint.get("epoch", -1)),
                "lut_weights": [float(value) for value in weights[0].detach().cpu()],
                "source_size": [int(image.width), int(image.height)],
                "device": str(device),
                "load_ms": self._load_ms,
                "cuda_peak_memory_allocated_bytes": peak,
            },
            timings_ms={
                "inference": round(inference_ms, 3),
                "total": round((time.perf_counter() - started) * 1000.0, 3),
            },
        )

    def _ensure_loaded(self, device: torch.device) -> None:
        if self._model is not None and self._device == device:
            return
        identity = self.asset_identity()
        if not self.checkpoint_path.is_file():
            raise AutoModelError(
                "auto_model_checkpoint_missing",
                "Expert-faithful LUT checkpoint is not installed.",
                model_key=self.model_key,
                status_code=503,
                retryable=True,
                details={"checkpoint_path": str(self.checkpoint_path)},
            )
        if not identity["verified"]:
            raise AutoModelError(
                "auto_model_checkpoint_hash_mismatch",
                "Expert-faithful LUT checkpoint failed integrity verification.",
                model_key=self.model_key,
                status_code=503,
                details=identity,
            )
        from image_adaptive_3dlut.checkpoints import load_model_checkpoint

        started = time.perf_counter()
        model, checkpoint = load_model_checkpoint(self.checkpoint_path, device)
        self._model = model.eval()
        self._checkpoint = checkpoint
        self._device = device
        self._load_ms = round((time.perf_counter() - started) * 1000.0, 3)

    def _release_model(self) -> None:
        self._model = None
        self._checkpoint = None
        self._device = None
        self._load_ms = None

    def _typed_error(self, exc: Exception) -> AutoModelError:
        if isinstance(exc, AutoModelError):
            return exc
        if _is_cuda_oom(exc):
            return AutoModelError(
                "auto_model_out_of_memory",
                "Expert-faithful LUT ran out of memory on CUDA and CPU fallback failed.",
                model_key=self.model_key,
                status_code=503,
                retryable=True,
                details={"exception": str(exc)},
            )
        return AutoModelError(
            "auto_model_inference_failed",
            f"Expert-faithful LUT inference failed: {exc}",
            model_key=self.model_key,
            status_code=503,
            retryable=True,
            details={"exception_type": type(exc).__name__},
        )

    @staticmethod
    def _preferred_device() -> torch.device:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class KaiResidualFusionAdapter:
    model_key = VIVID_MODEL_KEY
    model_family = "residual_fusion_v3_8"

    def __init__(self, *, timeout_seconds: float = 120.0):
        self.timeout_seconds = max(10.0, float(timeout_seconds))
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._responses: Queue[str] = Queue()
        self._stderr: deque[str] = deque(maxlen=40)
        self._cpu_mode = not torch.cuda.is_available()
        atexit.register(self.close)

    def asset_identity(self) -> dict[str, Any]:
        checkpoint = (
            REPOSITORY_ROOT
            / "residual_fusion"
            / "training"
            / "outputs"
            / "expert_c_v3_1_joint_categorical_artifact_safe_full_research001"
            / "joint_categorical_strength_full_train.pt"
        )
        expected = (
            "3E893D2A1FDB0F04F32259D20873F0A1E2F4C1FFE340435E8ABB71756259E4AB"
        )
        actual = _sha256_file(checkpoint) if checkpoint.is_file() else None
        package_sha = (
            _sha256_file(KAI_PACKAGE_MANIFEST)
            if KAI_PACKAGE_MANIFEST.is_file()
            else None
        )
        return {
            "provider": "git_release_package",
            "release": "ResidualFusion",
            "checkpoint_path": str(checkpoint),
            "expected_checkpoint_sha256": expected,
            "actual_checkpoint_sha256": actual,
            "checkpoint_verified": actual == expected,
            "package_manifest_path": str(KAI_PACKAGE_MANIFEST),
            "package_manifest_sha256": package_sha,
        }

    def health(self) -> dict[str, Any]:
        identity = self.asset_identity()
        if not Path(identity["checkpoint_path"]).is_file():
            return {
                "model_key": self.model_key,
                "status": "unavailable",
                "code": "checkpoint_missing",
                "asset": identity,
                "worker_running": False,
            }
        if not identity["checkpoint_verified"]:
            return {
                "model_key": self.model_key,
                "status": "unavailable",
                "code": "checkpoint_hash_mismatch",
                "asset": identity,
                "worker_running": False,
            }
        running = self._process is not None and self._process.poll() is None
        return {
            "model_key": self.model_key,
            "status": "ready",
            "asset": identity,
            "worker_running": running,
            "worker_mode": "cpu" if self._cpu_mode else "cuda_preferred",
        }

    def warmup(self) -> dict[str, Any]:
        with self._lock:
            response = self._request({"command": "warmup"})
            return {**self.health(), "warmup": response}

    def enhance(self, source_path: Path, result_path: Path) -> AutoModelResult:
        with self._lock:
            started = time.perf_counter()
            normalized_source = result_path.with_name(".kai_source.tmp.png")
            try:
                input_metadata = self._normalize_source(source_path, normalized_source)
                try:
                    response = self._request(
                        {
                            "command": "enhance",
                            "source_path": str(normalized_source.resolve()),
                            "result_path": str(result_path.resolve()),
                        }
                    )
                    warnings: tuple[str, ...] = ()
                except AutoModelError as exc:
                    if (
                        exc.code != "auto_model_out_of_memory"
                        or self._cpu_mode
                        or not _truthy_env(
                            "AI_PHOTO_AUTO_MODEL_CPU_FALLBACK",
                            "1",
                        )
                    ):
                        raise
                    self._stop_worker()
                    self._cpu_mode = True
                    response = self._request(
                        {
                            "command": "enhance",
                            "source_path": str(normalized_source.resolve()),
                            "result_path": str(result_path.resolve()),
                        }
                    )
                    warnings = ("cuda_oom_cpu_fallback",)
            finally:
                normalized_source.unlink(missing_ok=True)
            render = response.get("render")
            if not isinstance(render, dict):
                raise AutoModelError(
                    "auto_model_protocol_error",
                    "ResidualFusion worker returned no render metadata.",
                    model_key=self.model_key,
                    status_code=503,
                    retryable=True,
                )
            elapsed = round((time.perf_counter() - started) * 1000.0, 3)
            return AutoModelResult(
                model_key=self.model_key,
                model_family=self.model_family,
                runtime_metadata={
                    "render": render,
                    "cuda": response.get("cuda"),
                    "worker_mode": "cpu" if self._cpu_mode else "cuda_preferred",
                    "input_normalization": input_metadata,
                },
                timings_ms={
                    "model_total": float(response.get("elapsed_ms") or elapsed),
                    "total": elapsed,
                },
                warning_flags=warnings,
            )

    @staticmethod
    def _normalize_source(source_path: Path, normalized_path: Path) -> dict[str, Any]:
        try:
            with Image.open(source_path) as source:
                raw_size = [int(source.width), int(source.height)]
                orientation = int(source.getexif().get(274, 1))
                normalized = ImageOps.exif_transpose(source).convert("RGB").copy()
        except Exception as exc:
            raise AutoModelError(
                "auto_model_source_invalid",
                f"ResidualFusion could not decode the source image: {exc}",
                model_key=VIVID_MODEL_KEY,
                status_code=400,
                retryable=False,
            ) from exc
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(normalized_path, format="PNG")
        return {
            "exif_orientation": orientation,
            "raw_size": raw_size,
            "normalized_size": [int(normalized.width), int(normalized.height)],
            "format": "PNG",
        }

    def close(self) -> None:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = None
                return
            try:
                self._request({"command": "shutdown"}, timeout_seconds=5.0)
            except Exception:
                pass
            self._stop_worker()

    def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self._ensure_worker()
        assert self._process is not None
        assert self._process.stdin is not None
        if self._process.poll() is not None:
            raise self._worker_exit_error()
        try:
            self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._stop_worker()
            raise AutoModelError(
                "auto_model_worker_exited",
                f"ResidualFusion worker stopped before inference: {exc}",
                model_key=self.model_key,
                status_code=503,
                retryable=True,
                details={"stderr": list(self._stderr)},
            ) from exc
        effective_timeout = timeout_seconds or self.timeout_seconds
        deadline = time.monotonic() + effective_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_worker()
                raise AutoModelError(
                    "auto_model_timeout",
                    "ResidualFusion inference exceeded the configured timeout.",
                    model_key=self.model_key,
                    status_code=504,
                    retryable=True,
                    details={"timeout_seconds": effective_timeout},
                )
            try:
                line = self._responses.get(timeout=min(0.25, remaining))
                break
            except Empty:
                if self._process is None or self._process.poll() is not None:
                    raise self._worker_exit_error()
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            self._stop_worker()
            raise AutoModelError(
                "auto_model_protocol_error",
                "ResidualFusion worker returned invalid JSON.",
                model_key=self.model_key,
                status_code=503,
                retryable=True,
                details={"line": line[-500:]},
            ) from exc
        if not isinstance(response, dict) or response.get("status") != "ok":
            exception_type = str(response.get("exception_type") or "") if isinstance(response, dict) else ""
            message = str(response.get("message") or "ResidualFusion inference failed") if isinstance(response, dict) else "ResidualFusion inference failed"
            oom = "outofmemory" in exception_type.lower() or "out of memory" in message.lower()
            raise AutoModelError(
                "auto_model_out_of_memory" if oom else "auto_model_inference_failed",
                f"ResidualFusion inference failed: {message}",
                model_key=self.model_key,
                status_code=503,
                retryable=True,
                details={
                    "exception_type": exception_type,
                    "stderr": list(self._stderr),
                    "worker_traceback": response.get("traceback") if isinstance(response, dict) else None,
                },
            )
        return response

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._stop_worker()
        while not self._responses.empty():
            try:
                self._responses.get_nowait()
            except Empty:
                break
        env = os.environ.copy()
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "AI_PHOTO_SEGMENTATION_DEVICE": "cpu" if self._cpu_mode else "cuda",
                "AI_PHOTO_SEGMENTATION_HALF": "0" if self._cpu_mode else "1",
                "AI_PHOTO_SEGMENTATION_LOCAL_ONLY": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "AI_PHOTO_V3_1_MAX_SIDE": os.getenv("AI_PHOTO_V3_1_MAX_SIDE", "2560"),
                "AI_PHOTO_V3_8_STRICT_NOOP": os.getenv("AI_PHOTO_V3_8_STRICT_NOOP", "0"),
            }
        )
        if self._cpu_mode:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        self._process = subprocess.Popen(
            [sys.executable, str(KAI_WORKER_PATH)],
            cwd=str(REPOSITORY_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            daemon=True,
        ).start()

    def _read_stdout(self, stream: Any) -> None:
        for line in stream:
            if line.strip():
                self._responses.put(line)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            normalized = line.strip()
            if normalized:
                self._stderr.append(normalized[-1000:])

    def _stop_worker(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _worker_exit_error(self) -> AutoModelError:
        code = self._process.poll() if self._process is not None else None
        self._stop_worker()
        return AutoModelError(
            "auto_model_worker_exited",
            f"ResidualFusion worker exited unexpectedly (code {code}).",
            model_key=self.model_key,
            status_code=503,
            retryable=True,
            details={"exit_code": code, "stderr": list(self._stderr)},
        )


def _truthy_env(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _is_cuda_oom(exc: Exception) -> bool:
    return isinstance(exc, torch.OutOfMemoryError) or "out of memory" in str(exc).lower()


def build_default_auto_model_adapters() -> dict[str, Any]:
    return {
        EXPERT_FAITHFUL_MODEL_KEY: WeiLutAdapter(),
        VIVID_MODEL_KEY: KaiResidualFusionAdapter(),
    }
