"""Deterministic image measurements for verifiable edit contracts.

The functions in this module deliberately stop at measurement.  Threshold
selection and pass/fail decisions belong to the metric capability registry so
routes and prompt parsers never need metric-specific branches.

All v1 image inputs are OpenCV-style BGR ``uint8`` arrays.  Masks may be bool,
integer, or normalized floating-point arrays, but must match the image plane.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Final

import cv2
import numpy as np

from app.services.edit_contract_schema import (
    MetricEvaluationContext,
    MetricMeasurement,
)


HIGHLIGHT_CLIP_METRIC_ID: Final = "highlight_clip_ratio"
SHADOW_CLIP_METRIC_ID: Final = "shadow_clip_ratio"
PROTECTED_COLOR_METRIC_ID: Final = "protected_region_color_delta"
OUTSIDE_SCOPE_METRIC_ID: Final = "outside_edit_scope_change_ratio"

LUMA_METRIC_VERSION: Final = "bt709_encoded_luma_v1"
PROTECTED_COLOR_METRIC_VERSION: Final = "cie76_core_p95_v1"
OUTSIDE_SCOPE_METRIC_VERSION: Final = "cie76_outside_guard_v1"

DEFAULT_HIGHLIGHT_LUMA_THRESHOLD: Final = 250.0 / 255.0
DEFAULT_SHADOW_LUMA_THRESHOLD: Final = 5.0 / 255.0
DEFAULT_MASK_CORE_THRESHOLD: Final = 0.90
DEFAULT_COLOR_PERCENTILE: Final = 95.0
DEFAULT_COLOR_MINIMUM_SAMPLES: Final = 64
DEFAULT_EDIT_MASK_ACTIVITY_THRESHOLD: Final = 0.01
DEFAULT_GUARD_BAND_RATIO: Final = 0.01
DEFAULT_GUARD_BAND_MIN_PIXELS: Final = 2
DEFAULT_GUARD_BAND_MAX_PIXELS: Final = 16
DEFAULT_PERCEPTUAL_DELTA_THRESHOLD: Final = 2.3

_COMPARISON_EPSILON: Final = 1e-12


class EditContractMetricError(ValueError):
    """A metric could not make a trustworthy measurement."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        super().__init__(message)


MetricEvaluator = Callable[[MetricEvaluationContext], MetricMeasurement]


def evaluate_highlight_clip_ratio(
    context: MetricEvaluationContext,
) -> MetricMeasurement:
    """Measure the full-image ratio at or above the encoded-luma ceiling."""

    _require_context(
        context,
        metric_id=HIGHLIGHT_CLIP_METRIC_ID,
        metric_version=LUMA_METRIC_VERSION,
    )
    _, candidate = _require_image_pair(context)
    threshold = _metadata_float(
        context.metadata,
        "luminance_threshold",
        DEFAULT_HIGHLIGHT_LUMA_THRESHOLD,
        minimum=0.0,
        maximum=1.0,
    )
    luma = _encoded_bt709_luma(candidate)
    clipped_count = int(
        np.count_nonzero(luma >= threshold - _COMPARISON_EPSILON)
    )
    sample_count = int(luma.size)
    ratio = clipped_count / sample_count
    return MetricMeasurement(
        metric_id=HIGHLIGHT_CLIP_METRIC_ID,
        metric_version=LUMA_METRIC_VERSION,
        value=ratio,
        unit="ratio",
        sample_count=sample_count,
        details={
            "color_order": "bgr",
            "luma_standard": "bt709_encoded_srgb",
            "luminance_threshold": threshold,
            "clipped_count": clipped_count,
            "comparison": "greater_than_or_equal",
        },
    )


def evaluate_shadow_clip_ratio(
    context: MetricEvaluationContext,
) -> MetricMeasurement:
    """Measure the full-image ratio at or below the encoded-luma floor."""

    _require_context(
        context,
        metric_id=SHADOW_CLIP_METRIC_ID,
        metric_version=LUMA_METRIC_VERSION,
    )
    _, candidate = _require_image_pair(context)
    threshold = _metadata_float(
        context.metadata,
        "luminance_threshold",
        DEFAULT_SHADOW_LUMA_THRESHOLD,
        minimum=0.0,
        maximum=1.0,
    )
    luma = _encoded_bt709_luma(candidate)
    clipped_count = int(
        np.count_nonzero(luma <= threshold + _COMPARISON_EPSILON)
    )
    sample_count = int(luma.size)
    ratio = clipped_count / sample_count
    return MetricMeasurement(
        metric_id=SHADOW_CLIP_METRIC_ID,
        metric_version=LUMA_METRIC_VERSION,
        value=ratio,
        unit="ratio",
        sample_count=sample_count,
        details={
            "color_order": "bgr",
            "luma_standard": "bt709_encoded_srgb",
            "luminance_threshold": threshold,
            "clipped_count": clipped_count,
            "comparison": "less_than_or_equal",
        },
    )


def evaluate_protected_region_color_delta(
    context: MetricEvaluationContext,
) -> MetricMeasurement:
    """Summarize CIE76 color change inside a trusted protected-mask core.

    The returned scalar is the configured high percentile (p95 by default),
    while mean, percentile, and maximum are retained for explanation.
    """

    _require_context(
        context,
        metric_id=PROTECTED_COLOR_METRIC_ID,
        metric_version=PROTECTED_COLOR_METRIC_VERSION,
    )
    baseline, candidate = _require_image_pair(context)
    mask = _require_normalized_mask(
        context.subject_mask,
        baseline.shape[:2],
        "subject_mask",
    )
    core_threshold = _metadata_float(
        context.metadata,
        "mask_core_threshold",
        DEFAULT_MASK_CORE_THRESHOLD,
        minimum=0.0,
        maximum=1.0,
    )
    minimum_samples = _metadata_int(
        context.metadata,
        "minimum_sample_count",
        DEFAULT_COLOR_MINIMUM_SAMPLES,
        minimum=1,
    )
    percentile = _metadata_float(
        context.metadata,
        "summary_percentile",
        DEFAULT_COLOR_PERCENTILE,
        minimum=50.0,
        maximum=100.0,
    )
    core = mask >= core_threshold - _COMPARISON_EPSILON
    sample_count = int(np.count_nonzero(core))
    if sample_count < minimum_samples:
        raise EditContractMetricError(
            "insufficient_subject_mask_core",
            "Protected-region mask does not contain enough trusted core pixels "
            f"({sample_count} < {minimum_samples}).",
        )

    delta = _cie76_delta(baseline, candidate)[core]
    mean_delta = float(np.mean(delta, dtype=np.float64))
    percentile_delta = float(np.percentile(delta, percentile))
    maximum_delta = float(np.max(delta))
    return MetricMeasurement(
        metric_id=PROTECTED_COLOR_METRIC_ID,
        metric_version=PROTECTED_COLOR_METRIC_VERSION,
        value=percentile_delta,
        # The registry's canonical unit is ``delta_e``; the exact CIE76
        # formula remains explicit in metric_version and details.
        unit="delta_e",
        sample_count=sample_count,
        details={
            "color_order": "bgr",
            "color_space": "opencv_cielab_float_d65",
            "delta_formula": "cie76",
            "subject_region": context.subject_region,
            "mask_core_threshold": core_threshold,
            "mask_core_coverage": sample_count / int(core.size),
            "mean_delta_e76": mean_delta,
            "percentile": percentile,
            "percentile_delta_e76": percentile_delta,
            "maximum_delta_e76": maximum_delta,
        },
    )


def evaluate_outside_edit_scope_change_ratio(
    context: MetricEvaluationContext,
) -> MetricMeasurement:
    """Measure perceptible candidate changes outside a guarded edit mask."""

    _require_context(
        context,
        metric_id=OUTSIDE_SCOPE_METRIC_ID,
        metric_version=OUTSIDE_SCOPE_METRIC_VERSION,
    )
    baseline, candidate = _require_image_pair(context)
    edit_mask = _require_normalized_mask(
        context.edit_mask,
        baseline.shape[:2],
        "edit_mask",
    )
    activity_threshold = _metadata_float(
        context.metadata,
        "mask_activity_threshold",
        DEFAULT_EDIT_MASK_ACTIVITY_THRESHOLD,
        minimum=0.0,
        maximum=1.0,
    )
    active = edit_mask > activity_threshold
    active_count = int(np.count_nonzero(active))
    if active_count == 0:
        raise EditContractMetricError(
            "empty_edit_mask",
            "The effective feathered edit mask is empty.",
        )

    guard_pixels = _guard_band_pixels(context.metadata, baseline.shape[:2])
    guarded = _dilate_mask(active, guard_pixels)
    outside = np.logical_not(guarded)
    sample_count = int(np.count_nonzero(outside))
    minimum_samples = _metadata_int(
        context.metadata,
        "minimum_sample_count",
        1,
        minimum=1,
    )
    if sample_count < minimum_samples:
        raise EditContractMetricError(
            "empty_outside_scope_domain",
            "The guarded edit mask leaves no trustworthy outside-scope pixels "
            f"({sample_count} < {minimum_samples}).",
        )

    perceptual_threshold = _metadata_float(
        context.metadata,
        "perceptual_delta_threshold",
        DEFAULT_PERCEPTUAL_DELTA_THRESHOLD,
        minimum=0.0,
        inclusive_minimum=False,
    )
    delta = _cie76_delta(baseline, candidate)
    changed = delta >= perceptual_threshold - _COMPARISON_EPSILON
    changed_count = int(np.count_nonzero(np.logical_and(changed, outside)))
    ratio = changed_count / sample_count
    return MetricMeasurement(
        metric_id=OUTSIDE_SCOPE_METRIC_ID,
        metric_version=OUTSIDE_SCOPE_METRIC_VERSION,
        value=ratio,
        unit="ratio",
        sample_count=sample_count,
        details={
            "color_order": "bgr",
            "color_space": "opencv_cielab_float_d65",
            "delta_formula": "cie76",
            "subject_region": context.subject_region,
            "mask_activity_threshold": activity_threshold,
            "active_mask_count": active_count,
            "active_mask_coverage": active_count / int(active.size),
            "guard_band_pixels": guard_pixels,
            "outside_sample_count": sample_count,
            "perceptual_delta_threshold": perceptual_threshold,
            "changed_count": changed_count,
        },
    )


P0_METRIC_EVALUATORS: Mapping[str, MetricEvaluator] = MappingProxyType(
    {
        HIGHLIGHT_CLIP_METRIC_ID: evaluate_highlight_clip_ratio,
        SHADOW_CLIP_METRIC_ID: evaluate_shadow_clip_ratio,
        PROTECTED_COLOR_METRIC_ID: evaluate_protected_region_color_delta,
        OUTSIDE_SCOPE_METRIC_ID: evaluate_outside_edit_scope_change_ratio,
    }
)


def get_metric_evaluator(metric_id: str) -> MetricEvaluator:
    """Return a bound evaluator without metric-specific caller branching."""

    normalized = str(metric_id or "").strip().lower()
    try:
        return P0_METRIC_EVALUATORS[normalized]
    except KeyError as exc:
        raise EditContractMetricError(
            "unsupported_metric",
            f"No image evaluator is registered for metric {normalized!r}.",
        ) from exc


def evaluate_metric(context: MetricEvaluationContext) -> MetricMeasurement:
    """Dispatch a typed metric context through the evaluator registry."""

    return get_metric_evaluator(context.metric_id)(context)


def _require_context(
    context: MetricEvaluationContext,
    *,
    metric_id: str,
    metric_version: str,
) -> None:
    if not isinstance(context, MetricEvaluationContext):
        raise TypeError("context must be a MetricEvaluationContext")
    if context.metric_id != metric_id:
        raise EditContractMetricError(
            "metric_context_mismatch",
            f"Evaluator for {metric_id!r} received {context.metric_id!r}.",
        )
    if context.metric_version != metric_version:
        raise EditContractMetricError(
            "unsupported_metric_version",
            f"Metric {metric_id!r} does not implement version "
            f"{context.metric_version!r}.",
        )


def _require_bgr_uint8(value: Any, label: str) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise EditContractMetricError(
            "invalid_image",
            f"{label} must be a numpy array.",
        )
    if value.dtype != np.uint8:
        raise EditContractMetricError(
            "invalid_image_dtype",
            f"{label} must use uint8 BGR pixels.",
        )
    if value.ndim != 3 or value.shape[2] != 3 or value.shape[0] <= 0 or value.shape[1] <= 0:
        raise EditContractMetricError(
            "invalid_image_shape",
            f"{label} must have non-empty HxWx3 BGR shape.",
        )
    return value


def _require_image_pair(
    context: MetricEvaluationContext,
) -> tuple[np.ndarray, np.ndarray]:
    baseline = _require_bgr_uint8(context.baseline_image, "baseline_image")
    candidate = _require_bgr_uint8(context.candidate_image, "candidate_image")
    if baseline.shape != candidate.shape:
        raise EditContractMetricError(
            "image_shape_mismatch",
            "Baseline and candidate images must have identical dimensions.",
        )
    return baseline, candidate


def _require_normalized_mask(
    value: Any,
    expected_shape: tuple[int, int],
    label: str,
) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise EditContractMetricError(
            "missing_mask" if value is None else "invalid_mask",
            f"{label} must be a numpy array.",
        )
    if value.ndim != 2 or value.shape != expected_shape:
        raise EditContractMetricError(
            "mask_shape_mismatch",
            f"{label} must match image plane {expected_shape!r}.",
        )
    if value.dtype == np.bool_:
        normalized = value.astype(np.float32)
    elif np.issubdtype(value.dtype, np.integer):
        if np.issubdtype(value.dtype, np.signedinteger) and int(value.min()) < 0:
            raise EditContractMetricError(
                "invalid_mask_range",
                f"{label} cannot contain negative values.",
            )
        observed_maximum = int(value.max())
        # Binary masks are often materialized as uint8 0/1 instead of bool.
        # Preserve that canonical meaning rather than shrinking 1 to 1/255.
        maximum = 1.0 if observed_maximum <= 1 else float(np.iinfo(value.dtype).max)
        normalized = value.astype(np.float32) / maximum
    elif np.issubdtype(value.dtype, np.floating):
        normalized = value.astype(np.float32)
    else:
        raise EditContractMetricError(
            "invalid_mask_dtype",
            f"{label} must contain boolean, integer, or floating-point values.",
        )
    if not np.all(np.isfinite(normalized)):
        raise EditContractMetricError(
            "non_finite_mask",
            f"{label} contains non-finite values.",
        )
    minimum = float(normalized.min())
    maximum = float(normalized.max())
    if minimum < -_COMPARISON_EPSILON or maximum > 1.0 + _COMPARISON_EPSILON:
        raise EditContractMetricError(
            "invalid_mask_range",
            f"{label} must be normalized to [0, 1].",
        )
    return np.clip(normalized, 0.0, 1.0)


def _encoded_bt709_luma(image: np.ndarray) -> np.ndarray:
    normalized = image.astype(np.float64) / 255.0
    blue, green, red = cv2.split(normalized)
    return 0.0722 * blue + 0.7152 * green + 0.2126 * red


def _cie76_delta(baseline: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    baseline_lab = cv2.cvtColor(
        baseline.astype(np.float32) / 255.0,
        cv2.COLOR_BGR2LAB,
    ).astype(np.float64)
    candidate_lab = cv2.cvtColor(
        candidate.astype(np.float32) / 255.0,
        cv2.COLOR_BGR2LAB,
    ).astype(np.float64)
    difference = candidate_lab - baseline_lab
    return np.sqrt(np.sum(np.square(difference), axis=2, dtype=np.float64))


def _guard_band_pixels(
    metadata: Mapping[str, Any],
    image_shape: tuple[int, int],
) -> int:
    explicit = metadata.get("guard_band_pixels")
    if explicit is not None:
        return _coerce_int(explicit, "guard_band_pixels", minimum=0)
    ratio = _metadata_float(
        metadata,
        "guard_band_ratio",
        DEFAULT_GUARD_BAND_RATIO,
        minimum=0.0,
        maximum=0.25,
    )
    minimum = _metadata_int(
        metadata,
        "guard_band_min_pixels",
        DEFAULT_GUARD_BAND_MIN_PIXELS,
        minimum=0,
    )
    maximum = _metadata_int(
        metadata,
        "guard_band_max_pixels",
        DEFAULT_GUARD_BAND_MAX_PIXELS,
        minimum=minimum,
    )
    scaled = int(math.floor(min(image_shape) * ratio + 0.5))
    return max(minimum, min(maximum, scaled))


def _dilate_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius == 0:
        return mask.copy()
    size = radius * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cv2.dilate(mask.astype(np.uint8), kernel, iterations=1) > 0


def _metadata_float(
    metadata: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    inclusive_minimum: bool = True,
) -> float:
    raw = metadata.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise EditContractMetricError(
            "invalid_metric_configuration",
            f"Metric metadata {key!r} must be numeric.",
        )
    value = float(raw)
    if not math.isfinite(value):
        raise EditContractMetricError(
            "invalid_metric_configuration",
            f"Metric metadata {key!r} must be finite.",
        )
    if minimum is not None:
        invalid_minimum = value < minimum if inclusive_minimum else value <= minimum
        if invalid_minimum:
            relation = ">=" if inclusive_minimum else ">"
            raise EditContractMetricError(
                "invalid_metric_configuration",
                f"Metric metadata {key!r} must be {relation} {minimum}.",
            )
    if maximum is not None and value > maximum:
        raise EditContractMetricError(
            "invalid_metric_configuration",
            f"Metric metadata {key!r} must be <= {maximum}.",
        )
    return value


def _metadata_int(
    metadata: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
) -> int:
    return _coerce_int(metadata.get(key, default), key, minimum=minimum)


def _coerce_int(value: Any, key: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EditContractMetricError(
            "invalid_metric_configuration",
            f"Metric metadata {key!r} must be an integer.",
        )
    if value < minimum:
        raise EditContractMetricError(
            "invalid_metric_configuration",
            f"Metric metadata {key!r} must be >= {minimum}.",
        )
    return value


__all__ = [
    "DEFAULT_COLOR_MINIMUM_SAMPLES",
    "DEFAULT_COLOR_PERCENTILE",
    "DEFAULT_EDIT_MASK_ACTIVITY_THRESHOLD",
    "DEFAULT_GUARD_BAND_MAX_PIXELS",
    "DEFAULT_GUARD_BAND_MIN_PIXELS",
    "DEFAULT_GUARD_BAND_RATIO",
    "DEFAULT_HIGHLIGHT_LUMA_THRESHOLD",
    "DEFAULT_MASK_CORE_THRESHOLD",
    "DEFAULT_PERCEPTUAL_DELTA_THRESHOLD",
    "DEFAULT_SHADOW_LUMA_THRESHOLD",
    "EditContractMetricError",
    "HIGHLIGHT_CLIP_METRIC_ID",
    "LUMA_METRIC_VERSION",
    "MetricEvaluator",
    "OUTSIDE_SCOPE_METRIC_ID",
    "OUTSIDE_SCOPE_METRIC_VERSION",
    "P0_METRIC_EVALUATORS",
    "PROTECTED_COLOR_METRIC_ID",
    "PROTECTED_COLOR_METRIC_VERSION",
    "SHADOW_CLIP_METRIC_ID",
    "evaluate_highlight_clip_ratio",
    "evaluate_metric",
    "evaluate_outside_edit_scope_change_ratio",
    "evaluate_protected_region_color_delta",
    "evaluate_shadow_clip_ratio",
    "get_metric_evaluator",
]
