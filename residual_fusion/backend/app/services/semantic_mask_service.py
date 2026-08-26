from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


DEFAULT_SEGMENTATION_MODEL = "nvidia/segformer-b5-finetuned-ade-640-640"
SEMANTIC_TARGETS = {"sky", "person", "background"}
SEMANTIC_CACHE_VERSION = "segformer_ade20k_v3_disjoint_background"
_MODEL_TARGETS = ("sky", "person")
_MIN_COVERAGE = {"sky": 0.002, "person": 0.005}
_MIN_CONFIDENCE = {"sky": 0.55, "person": 0.65}
_OVERLAY_COLORS = {
    "sky": np.array([43, 135, 255], dtype=np.float32),
    "person": np.array([255, 92, 65], dtype=np.float32),
    "background": np.array([59, 178, 115], dtype=np.float32),
}


class SemanticMaskError(RuntimeError):
    pass


class SemanticTargetNotFoundError(SemanticMaskError):
    def __init__(self, target: str, reason: str, mask_info: dict[str, Any]):
        self.target = target
        self.reason = reason
        self.mask_info = mask_info
        super().__init__(f"Semantic target '{target}' was not found: {reason}")


@dataclass(frozen=True)
class SemanticMaskResult:
    raw_mask: np.ndarray
    feathered_mask: np.ndarray
    info: dict[str, Any]


class SemanticMaskService:
    """Lazy, process-resident SegFormer service with content-addressed disk masks."""

    def __init__(
        self,
        cache_root: Path,
        *,
        model_id: str | None = None,
        device: str | None = None,
        use_half: bool | None = None,
        local_files_only: bool | None = None,
    ):
        self.cache_root = Path(cache_root)
        self.model_id = model_id or os.getenv(
            "AI_PHOTO_SEGMENTATION_MODEL",
            DEFAULT_SEGMENTATION_MODEL,
        )
        self.requested_device = device or os.getenv(
            "AI_PHOTO_SEGMENTATION_DEVICE",
            "auto",
        )
        self.requested_half = (
            _env_flag("AI_PHOTO_SEGMENTATION_HALF", True)
            if use_half is None
            else use_half
        )
        self.local_files_only = (
            _env_flag("AI_PHOTO_SEGMENTATION_LOCAL_ONLY", True)
            if local_files_only is None
            else local_files_only
        )
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device: Any = None
        self._dtype_name: str | None = None
        self._load_ms: float | None = None
        self._model_lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._cache_locks_guard = threading.Lock()
        self._cache_locks: dict[str, threading.Lock] = {}
        self._warmup_lock = threading.Lock()
        self._warmed = False
        self._warmup_inference_ms: float | None = None

    def warmup(self) -> dict[str, Any]:
        self._ensure_model()
        if not self._warmed:
            with self._warmup_lock:
                if not self._warmed:
                    prediction = self._predict_targets(
                        Image.new("RGB", (640, 640), (127, 127, 127))
                    )
                    self._warmup_inference_ms = float(prediction["inference_ms"])
        return {
            "model_id": self.model_id,
            "device": str(self._device),
            "dtype": self._dtype_name,
            "load_ms": self._load_ms,
            "warmup_inference_ms": self._warmup_inference_ms,
        }

    def get_mask(
        self,
        image_path: Path,
        target: str,
        *,
        force_refresh: bool = False,
    ) -> SemanticMaskResult:
        normalized_target = str(target or "").strip().lower()
        if normalized_target not in SEMANTIC_TARGETS:
            raise ValueError(f"Unsupported semantic mask target: {target}")

        source_path = Path(image_path).resolve()
        if not source_path.is_file():
            raise SemanticMaskError(f"Semantic mask source does not exist: {source_path}")

        request_started = time.perf_counter()
        source_sha256 = _sha256_file(source_path)
        cache_id = hashlib.sha256(
            f"{SEMANTIC_CACHE_VERSION}|{self.model_id}|{source_sha256}".encode("utf-8")
        ).hexdigest()[:24]
        cache_dir = self.cache_root / cache_id
        cache_lock = self._cache_lock(cache_id)

        with cache_lock:
            metadata = None if force_refresh else self._load_cache(cache_dir, source_sha256)
            cache_hit = metadata is not None
            if metadata is None:
                metadata = self._generate_cache(
                    source_path=source_path,
                    source_sha256=source_sha256,
                    cache_id=cache_id,
                    cache_dir=cache_dir,
                )
            metadata, artifact_cache_hit = self._materialize_target_artifacts(
                source_path=source_path,
                cache_dir=cache_dir,
                metadata=metadata,
                target=normalized_target,
                force_refresh=force_refresh or not cache_hit,
            )

        target_info = dict(metadata["targets"][normalized_target])
        raw_path = cache_dir / target_info["raw_mask_file"]
        feathered_path = cache_dir / target_info["feathered_mask_file"]
        overlay_path = cache_dir / target_info["overlay_file"]
        raw_mask = _read_mask(raw_path, grayscale=True)
        feathered_mask = _read_mask(feathered_path, grayscale=True).astype(np.float32) / 255.0
        request_ms = _elapsed_ms(request_started)
        info = {
            "target": normalized_target,
            "source": target_info["source"],
            "source_image_path": str(source_path),
            "source_sha256": source_sha256,
            "model_id": self.model_id,
            "model_device": metadata.get("model_device"),
            "model_dtype": metadata.get("model_dtype"),
            "cache_id": cache_id,
            "cache_hit": cache_hit,
            "artifact_cache_hit": artifact_cache_hit,
            "raw_mask_path": str(raw_path.resolve()),
            "feathered_mask_path": str(feathered_path.resolve()),
            "overlay_path": str(overlay_path.resolve()),
            "coverage": target_info["coverage"],
            "confidence": target_info["confidence"],
            "found": target_info["found"],
            "failure_reason": target_info.get("failure_reason"),
            "excluded_targets": target_info.get("excluded_targets", []),
            "timings_ms": {
                **metadata.get("timings_ms", {}),
                "request": round(request_ms, 3),
            },
        }
        if not target_info["found"]:
            raise SemanticTargetNotFoundError(
                normalized_target,
                str(target_info.get("failure_reason") or "target_not_found"),
                info,
            )
        return SemanticMaskResult(
            raw_mask=raw_mask,
            feathered_mask=np.clip(feathered_mask, 0.0, 1.0),
            info=info,
        )

    def _generate_cache(
        self,
        *,
        source_path: Path,
        source_sha256: str,
        cache_id: str,
        cache_dir: Path,
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        with Image.open(source_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        prediction = self._predict_targets(image)

        post_started = time.perf_counter()
        cache_dir.mkdir(parents=True, exist_ok=True)
        target_metadata: dict[str, dict[str, Any]] = {}
        for target in _MODEL_TARGETS:
            raw_mask = _postprocess_binary_mask(
                prediction["masks"][target].astype(np.uint8) * 255,
                target,
            )
            native_name = f"{target}_native.png"
            _write_mask(cache_dir / native_name, raw_mask)
            coverage = float(np.count_nonzero(raw_mask)) / float(raw_mask.size)
            confidence = float(prediction["confidences"][target])
            found, failure_reason = _evaluate_target(target, coverage, confidence)
            target_metadata[target] = {
                "source": "segformer_ade20k",
                "coverage": round(coverage, 6),
                "confidence": round(confidence, 6),
                "found": found,
                "failure_reason": failure_reason,
                "native_mask_file": native_name,
            }

        person_info = target_metadata["person"]
        background_found = bool(person_info["found"])
        person_native = _read_mask(
            cache_dir / person_info["native_mask_file"],
            grayscale=True,
        )
        sky_info = target_metadata["sky"]
        sky_native = _read_mask(
            cache_dir / sky_info["native_mask_file"],
            grayscale=True,
        )
        background_native, background_source, excluded_targets = (
            _compose_background_native_mask(
                person_native,
                sky_native,
                person_found=background_found,
                sky_found=bool(sky_info["found"]),
            )
        )
        background_coverage = (
            float(np.count_nonzero(background_native))
            / float(background_native.size)
        )
        if background_found and background_coverage < 0.002:
            background_found = False
            background_failure_reason = "coverage_below_0.002"
        elif background_found:
            background_failure_reason = None
        else:
            background_failure_reason = "person_not_found_for_background"
        background_native_name = "background_native.png"
        _write_mask(cache_dir / background_native_name, background_native)
        target_metadata["background"] = {
            "source": background_source,
            "coverage": round(background_coverage, 6),
            "confidence": person_info["confidence"],
            "found": background_found,
            "failure_reason": background_failure_reason,
            "excluded_targets": excluded_targets,
            "native_mask_file": background_native_name,
        }
        postprocess_ms = _elapsed_ms(post_started)
        metadata = {
            "cache_version": SEMANTIC_CACHE_VERSION,
            "cache_id": cache_id,
            "model_id": self.model_id,
            "model_device": prediction["device"],
            "model_dtype": prediction["dtype"],
            "source_image_path": str(source_path),
            "source_sha256": source_sha256,
            "source_size": [image.width, image.height],
            "targets": target_metadata,
            "timings_ms": {
                "model_load": prediction["model_load_ms"],
                "preprocess": prediction["preprocess_ms"],
                "inference": prediction["inference_ms"],
                "postprocess_and_native_cache": round(postprocess_ms, 3),
                "cache_generation_total": round(_elapsed_ms(total_started), 3),
            },
        }
        _write_json_atomic(cache_dir / "metadata.json", metadata)
        return metadata

    def _materialize_target_artifacts(
        self,
        *,
        source_path: Path,
        cache_dir: Path,
        metadata: dict[str, Any],
        target: str,
        force_refresh: bool,
    ) -> tuple[dict[str, Any], bool]:
        target_info = dict(metadata["targets"][target])
        raw_path = cache_dir / f"{target}_raw.png"
        feathered_path = cache_dir / f"{target}_feathered.png"
        overlay_path = cache_dir / f"{target}_overlay.jpg"
        artifact_cache_hit = (
            not force_refresh
            and raw_path.is_file()
            and feathered_path.is_file()
            and overlay_path.is_file()
        )
        if artifact_cache_hit:
            target_info.update(
                {
                    "raw_mask_file": raw_path.name,
                    "feathered_mask_file": feathered_path.name,
                    "overlay_file": overlay_path.name,
                }
            )
            metadata["targets"][target] = target_info
            return metadata, True

        materialize_started = time.perf_counter()
        with Image.open(source_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        native_mask = _read_mask(
            cache_dir / target_info["native_mask_file"],
            grayscale=True,
        )
        resized = np.asarray(
            Image.fromarray(native_mask, mode="L").resize(
                image.size,
                resample=Image.Resampling.NEAREST,
            )
        )
        raw_mask = _postprocess_binary_mask(resized, target)
        coverage = float(np.count_nonzero(raw_mask)) / float(raw_mask.size)
        confidence = float(target_info["confidence"])
        if target in _MODEL_TARGETS:
            found, failure_reason = _evaluate_target(target, coverage, confidence)
        else:
            found = bool(target_info["found"])
            failure_reason = target_info.get("failure_reason")
            if not found:
                raw_mask = np.zeros_like(raw_mask)
                coverage = 0.0
        artifact_info = _write_target_artifacts(
            cache_dir=cache_dir,
            image=image,
            target=target,
            raw_mask=raw_mask,
            coverage=coverage,
            confidence=confidence,
            found=found,
            failure_reason=failure_reason,
            source=str(target_info["source"]),
        )
        target_info.update(artifact_info)
        target_info["native_mask_file"] = metadata["targets"][target][
            "native_mask_file"
        ]
        metadata["targets"][target] = target_info
        metadata.setdefault("timings_ms", {})[
            f"materialize_{target}"
        ] = round(_elapsed_ms(materialize_started), 3)
        _write_json_atomic(cache_dir / "metadata.json", metadata)
        return metadata, False

    def _predict_targets(self, image: Image.Image) -> dict[str, Any]:
        self._ensure_model()
        torch = self._torch
        with self._inference_lock:
            preprocess_started = time.perf_counter()
            inputs = self._processor(images=image, return_tensors="pt")
            pixel_values = inputs["pixel_values"].to(self._device)
            if self._dtype_name == "float16":
                pixel_values = pixel_values.half()
            preprocess_ms = _elapsed_ms(preprocess_started)

            _synchronize(torch, self._device)
            inference_started = time.perf_counter()
            with torch.inference_mode():
                outputs = self._model(pixel_values=pixel_values)
            _synchronize(torch, self._device)
            inference_ms = _elapsed_ms(inference_started)

            probabilities = outputs.logits[0].float().softmax(dim=0)
            class_map = probabilities.argmax(dim=0)
            labels = {
                int(key): str(value).strip().lower()
                for key, value in self._model.config.id2label.items()
            }
            target_ids = {
                target: next(
                    (index for index, label in labels.items() if label == target),
                    None,
                )
                for target in _MODEL_TARGETS
            }
            missing = [target for target, class_id in target_ids.items() if class_id is None]
            if missing:
                raise SemanticMaskError(f"Segmentation model lacks labels: {missing}")

            masks = {}
            confidences = {}
            for target, class_id in target_ids.items():
                binary = class_map.eq(class_id)
                masks[target] = binary.cpu().numpy().astype(bool)
                confidences[target] = (
                    float(probabilities[class_id][binary].mean().item())
                    if bool(binary.any())
                    else 0.0
                )
            del inputs, pixel_values, outputs, probabilities, class_map
            self._warmed = True

        return {
            "masks": masks,
            "confidences": confidences,
            "device": str(self._device),
            "dtype": self._dtype_name,
            "model_load_ms": round(float(self._load_ms or 0.0), 3),
            "preprocess_ms": round(preprocess_ms, 3),
            "inference_ms": round(inference_ms, 3),
        }

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            load_started = time.perf_counter()
            try:
                import torch
                from transformers import AutoImageProcessor, SegformerForSemanticSegmentation

                device = _resolve_torch_device(torch, self.requested_device)
                processor = AutoImageProcessor.from_pretrained(
                    self.model_id,
                    local_files_only=self.local_files_only,
                )
                model = SegformerForSemanticSegmentation.from_pretrained(
                    self.model_id,
                    local_files_only=self.local_files_only,
                )
                model.eval().to(device)
                use_half = device.type == "cuda" and self.requested_half
                if use_half:
                    model.half()
                self._torch = torch
                self._device = device
                self._processor = processor
                self._model = model
                self._dtype_name = "float16" if use_half else "float32"
                self._load_ms = _elapsed_ms(load_started)
            except Exception as exc:
                raise SemanticMaskError(
                    f"Unable to load segmentation model {self.model_id}: {exc}"
                ) from exc

    def _load_cache(
        self,
        cache_dir: Path,
        source_sha256: str,
    ) -> dict[str, Any] | None:
        metadata_path = cache_dir / "metadata.json"
        if not metadata_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if metadata.get("cache_version") != SEMANTIC_CACHE_VERSION:
            return None
        if metadata.get("model_id") != self.model_id:
            return None
        if metadata.get("source_sha256") != source_sha256:
            return None
        targets = metadata.get("targets")
        if not isinstance(targets, dict):
            return None
        for target in SEMANTIC_TARGETS:
            info = targets.get(target)
            if not isinstance(info, dict):
                return None
            native_mask_file = str(info.get("native_mask_file") or "")
            if not native_mask_file or not (cache_dir / native_mask_file).is_file():
                return None
        return metadata

    def _cache_lock(self, cache_id: str) -> threading.Lock:
        with self._cache_locks_guard:
            return self._cache_locks.setdefault(cache_id, threading.Lock())


def _evaluate_target(
    target: str,
    coverage: float,
    confidence: float,
) -> tuple[bool, str | None]:
    if coverage < _MIN_COVERAGE[target]:
        return False, f"coverage_below_{_MIN_COVERAGE[target]:.3f}"
    if confidence < _MIN_CONFIDENCE[target]:
        return False, f"confidence_below_{_MIN_CONFIDENCE[target]:.2f}"
    return True, None

def _compose_background_native_mask(
    person_mask: np.ndarray,
    sky_mask: np.ndarray,
    *,
    person_found: bool,
    sky_found: bool,
) -> tuple[np.ndarray, str, list[str]]:
    """Build a portrait background without overlapping a detected sky."""

    if person_mask.shape != sky_mask.shape:
        raise ValueError("Person and sky masks must have identical shapes")
    if not person_found:
        return (
            np.zeros_like(person_mask),
            "inverse_person_mask",
            ["person"],
        )

    excluded = np.where(person_mask > 127, 255, 0).astype(np.uint8)
    excluded_targets = ["person"]
    source = "inverse_person_mask"
    if sky_found:
        excluded = cv2.bitwise_or(
            excluded,
            np.where(sky_mask > 127, 255, 0).astype(np.uint8),
        )
        excluded_targets.append("sky")
        source = "inverse_person_and_sky_mask"
    return cv2.bitwise_not(excluded), source, excluded_targets



def _postprocess_binary_mask(mask: np.ndarray, target: str) -> np.ndarray:
    binary = np.where(mask > 127, 255, 0).astype(np.uint8)
    height, width = binary.shape
    kernel_size = 3 if min(height, width) < 1600 else 5
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
    )
    minimum_area_ratio = 0.00005 if target == "person" else 0.0001
    minimum_area = max(8, int(height * width * minimum_area_ratio))
    cleaned = np.zeros_like(binary)
    for component in range(1, component_count):
        if int(stats[component, cv2.CC_STAT_AREA]) >= minimum_area:
            cleaned[labels == component] = 255
    return cleaned


def _feather_binary_mask(raw_mask: np.ndarray) -> np.ndarray:
    height, width = raw_mask.shape
    sigma = min(12.0, max(1.5, min(height, width) * 0.0025))
    kernel_radius = max(2, int(round(sigma * 3.0)))
    kernel_size = kernel_radius * 2 + 1
    feathered = cv2.GaussianBlur(
        raw_mask.astype(np.float32) / 255.0,
        (kernel_size, kernel_size),
        sigmaX=sigma,
        sigmaY=sigma,
    )
    return np.clip(feathered, 0.0, 1.0)


def _write_target_artifacts(
    *,
    cache_dir: Path,
    image: Image.Image,
    target: str,
    raw_mask: np.ndarray,
    coverage: float,
    confidence: float,
    found: bool,
    failure_reason: str | None,
    source: str,
) -> dict[str, Any]:
    raw_name = f"{target}_raw.png"
    feathered_name = f"{target}_feathered.png"
    overlay_name = f"{target}_overlay.jpg"
    feathered = _feather_binary_mask(raw_mask)
    _write_mask(cache_dir / raw_name, raw_mask)
    _write_mask(
        cache_dir / feathered_name,
        np.round(feathered * 255.0).astype(np.uint8),
    )
    _write_overlay(
        cache_dir / overlay_name,
        image=image,
        feathered_mask=feathered,
        target=target,
    )
    return {
        "source": source,
        "coverage": round(coverage, 6),
        "confidence": round(confidence, 6),
        "found": found,
        "failure_reason": failure_reason,
        "raw_mask_file": raw_name,
        "feathered_mask_file": feathered_name,
        "overlay_file": overlay_name,
    }


def _write_overlay(
    path: Path,
    *,
    image: Image.Image,
    feathered_mask: np.ndarray,
    target: str,
) -> None:
    rgb = np.asarray(image, dtype=np.float32)
    color = _OVERLAY_COLORS[target]
    alpha = feathered_mask[:, :, np.newaxis] * 0.55
    overlay = rgb * (1.0 - alpha) + color * alpha
    Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8), mode="RGB").save(
        path,
        quality=90,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", mask)
    if not success:
        raise SemanticMaskError(f"Unable to encode semantic mask: {path}")
    encoded.tofile(path)


def _read_mask(path: Path, *, grayscale: bool) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    image = cv2.imdecode(data, flag)
    if image is None:
        raise SemanticMaskError(f"Unable to read cached semantic mask: {path}")
    return image


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _resolve_torch_device(torch: Any, requested: str) -> Any:
    normalized = str(requested or "auto").strip().lower()
    if normalized == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if normalized == "cuda" and not torch.cuda.is_available():
        raise SemanticMaskError("CUDA was requested for segmentation but is unavailable.")
    if normalized not in {"cuda", "cpu"}:
        raise SemanticMaskError(f"Unsupported segmentation device: {requested}")
    return torch.device(normalized)


def _synchronize(torch: Any, device: Any) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


_DEFAULT_SERVICE: SemanticMaskService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_default_semantic_mask_service() -> SemanticMaskService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        with _DEFAULT_SERVICE_LOCK:
            if _DEFAULT_SERVICE is None:
                backend_dir = Path(__file__).resolve().parents[2]
                _DEFAULT_SERVICE = SemanticMaskService(
                    backend_dir / "storage" / "masks"
                )
    return _DEFAULT_SERVICE


def get_semantic_region_mask(
    image_path: Path,
    target: str,
) -> SemanticMaskResult:
    return get_default_semantic_mask_service().get_mask(image_path, target)
