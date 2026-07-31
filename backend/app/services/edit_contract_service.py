"""Deterministic execution boundary for verifiable prompt edit contracts."""

from __future__ import annotations

import copy
import hashlib
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.edit_contract_metrics import EditContractMetricError
from app.services.edit_contract_registry import (
    MetricCapabilityRegistry,
    get_default_metric_registry,
)
from app.services.edit_contract_scaling import (
    EditContractScalingError,
    scale_adaptive_v2_result,
)
from app.services.edit_contract_schema import (
    ContractConstraint,
    EditContractError,
    EditContractIR,
    EditContractReport,
    MetricCheck,
    MetricEvaluationContext,
    MetricMeasurement,
    compute_contract_hash,
)
from app.services.edit_contract_search import (
    EditContractSearchError,
    ScaleCandidateEvaluation,
    ScaleSearchPolicy,
    search_largest_safe_scale,
)
from app.services.edit_engines import build_engine_parameters
from app.services.opencv_processor import (
    prepare_opencv_edit_mask,
    read_opencv_image,
    render_opencv_image,
    write_opencv_image,
)
from app.services.semantic_mask_service import (
    SemanticTargetNotFoundError,
    get_semantic_region_mask,
)


CONTRACT_EXECUTOR_VERSION = "edit_contract_executor_v1"


@dataclass(frozen=True, slots=True)
class ContractCandidate:
    image: np.ndarray
    prompt_result: dict[str, Any]
    adaptive: dict[str, Any]
    parameters: dict[str, Any]
    mask_info: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ContractExecutionResult:
    prompt_result: dict[str, Any]
    adaptive: dict[str, Any]
    process_result: dict[str, Any]
    report: EditContractReport


class EditContractService:
    """Render, measure, search, and persist only one verified final image."""

    def __init__(
        self,
        *,
        registry: MetricCapabilityRegistry | None = None,
        search_policy: ScaleSearchPolicy | None = None,
    ) -> None:
        self.registry = registry or get_default_metric_registry()
        self.search_policy = search_policy or ScaleSearchPolicy()

    def execute(
        self,
        *,
        contract_ir: EditContractIR,
        prompt_result: Mapping[str, Any],
        adaptive: Mapping[str, Any] | None,
        selected_target_path: Path,
        selected_target_saved_path: str,
        target_edit_id: str,
        render_anchor_path: Path,
        render_anchor_saved_path: str,
        mask_source_path: Path,
        mask_source_saved_path: str,
        result_path: Path,
        engine_name: str = "opencv",
    ) -> ContractExecutionResult:
        started = time.perf_counter()
        if str(engine_name or "").strip().lower() != "opencv":
            raise EditContractError(
                code="contract_engine_unsupported",
                message="可驗證修圖合約 v1 目前只支援 OpenCV prompt 修圖。",
                disposition="unsupported",
                status_code=422,
            )
        if not isinstance(contract_ir, EditContractIR) or not contract_ir.is_fully_resolved:
            raise EditContractError(
                code="contract_ir_incomplete",
                message="修圖目標或保護條件尚未完整解析，未建立修圖版本。",
                disposition="rejected",
                status_code=422,
            )
        if not isinstance(adaptive, Mapping):
            raise EditContractError(
                code="contract_adaptive_plan_missing",
                message="合約操作沒有可縮放且可追蹤的 EditPlan，未建立修圖版本。",
                disposition="unsupported",
                status_code=422,
            )

        source_prompt_result = copy.deepcopy(dict(prompt_result))
        source_adaptive = copy.deepcopy(dict(adaptive))
        requested_plan = source_prompt_result.get("edit_plan")
        if not isinstance(requested_plan, Mapping):
            raise EditContractError(
                code="contract_edit_plan_missing",
                message="合約操作缺少正式 EditPlan，未建立修圖版本。",
                disposition="rejected",
                status_code=422,
            )
        requested_parameters = build_engine_parameters(
            "opencv",
            copy.deepcopy(dict(requested_plan)),
        )

        contract_hash = compute_contract_hash(
            contract_ir,
            target_identity=(
                f"{target_edit_id}:{_file_identity(selected_target_path)}"
            ),
            baseline_identity=_file_identity(selected_target_path),
            render_anchor_identity=_file_identity(render_anchor_path),
            requested_edit_plan=_canonical_plan_for_hash(requested_plan),
            requested_parameters=_numeric_parameters(requested_parameters),
            search_policy_version=self.search_policy.version,
        )

        try:
            baseline_image = read_opencv_image(
                selected_target_path,
                "contract selected target",
            )
            anchor_image = read_opencv_image(
                render_anchor_path,
                "contract render anchor",
            )
            mask_source_image = (
                anchor_image
                if mask_source_path.resolve() == render_anchor_path.resolve()
                else read_opencv_image(mask_source_path, "contract mask source")
            )
        except Exception as exc:
            raise EditContractError(
                code="contract_image_read_failed",
                message=f"無法讀取合約所需圖片：{exc}",
                disposition="rejected",
                status_code=500,
            ) from exc
        if baseline_image.shape != anchor_image.shape:
            raise EditContractError(
                code="contract_image_size_mismatch",
                message="選取版本與渲染錨點尺寸不同，無法可靠驗證合約。",
                disposition="unsupported",
                status_code=422,
            )

        try:
            prepared_edit_mask, edit_mask_info = prepare_opencv_edit_mask(
                original=anchor_image,
                parameters=requested_parameters,
                mask_source_path=mask_source_path,
                mask_source_image=mask_source_image,
            )
            subject_masks = self._prepare_subject_masks(
                constraints=contract_ir.constraints,
                mask_source_path=mask_source_path,
                output_shape=baseline_image.shape[:2],
            )
            enforcement = self._prepare_enforcement(
                constraints=contract_ir.constraints,
                subject_masks=subject_masks,
                edit_mask=prepared_edit_mask,
                output_shape=baseline_image.shape[:2],
                edit_mask_info=edit_mask_info,
            )
            baseline_measurements = self._baseline_measurements(
                constraints=contract_ir.constraints,
                baseline_image=baseline_image,
                subject_masks=subject_masks,
                edit_mask=enforcement["effective_edit_mask"],
            )
        except SemanticTargetNotFoundError as exc:
            raise EditContractError(
                code="contract_subject_mask_not_found",
                message=str(exc),
                disposition="unsupported",
                issues=({"mask_info": dict(exc.mask_info or {})},),
                status_code=422,
            ) from exc
        except EditContractMetricError as exc:
            status_code = 422 if _metric_error_is_capability(exc) else 500
            raise EditContractError(
                code=(
                    "contract_metric_unavailable"
                    if status_code == 422
                    else "contract_metric_failed"
                ),
                message=str(exc),
                disposition="unsupported" if status_code == 422 else "rejected",
                issues=({"reason": exc.code},),
                status_code=status_code,
            ) from exc
        except Exception as exc:
            raise EditContractError(
                code="contract_capability_failed",
                message=f"無法準備合約量測能力：{exc}",
                disposition="rejected",
                status_code=500,
            ) from exc

        # A scale-zero render must reconstruct the selected target before any
        # magnitude search starts.  This proves the adaptive snapshot still
        # describes the selected lineage and that subsequent scales change
        # only the current request contribution.  A mismatch is stale context,
        # not an invitation to render from the wrong anchor and commit anyway.
        try:
            zero_render = self._render_scaled_candidate(
                scale=0.0,
                source_prompt_result=source_prompt_result,
                source_adaptive=source_adaptive,
                anchor_image=anchor_image,
                mask_source_path=mask_source_path,
                mask_source_image=mask_source_image,
                prepared_edit_mask=prepared_edit_mask,
                edit_mask_info=edit_mask_info,
            )
        except EditContractScalingError as exc:
            raise EditContractError(
                code="contract_stale_context",
                message=f"選取版本的縮幅狀態已過期：{exc}",
                disposition="rejected",
                issues=({"reason": exc.code},),
                status_code=409,
            ) from exc
        except Exception as exc:
            raise EditContractError(
                code="contract_render_failed",
                message=f"無法重建選取版本：{exc}",
                disposition="rejected",
                status_code=500,
            ) from exc
        if not _reconstructs_selected_target(
            baseline_image,
            zero_render["image"],
            selected_target_path,
        ):
            raise EditContractError(
                code="contract_stale_context",
                message="渲染錨點與保留的 parent 狀態無法重建目前選取版本，未執行合約。",
                disposition="rejected",
                issues=(
                    {
                        "target_edit_id": target_edit_id,
                        "reason": "scale_zero_reconstruction_mismatch",
                    },
                ),
                status_code=409,
            )

        required_ids = frozenset(
            constraint.constraint_id for constraint in contract_ir.constraints
        )

        def evaluate_scale(scale: float) -> ScaleCandidateEvaluation[ContractCandidate]:
            render_started = time.perf_counter()
            rendered = self._render_scaled_candidate(
                scale=scale,
                source_prompt_result=source_prompt_result,
                source_adaptive=source_adaptive,
                anchor_image=anchor_image,
                mask_source_path=mask_source_path,
                mask_source_image=mask_source_image,
                prepared_edit_mask=prepared_edit_mask,
                edit_mask_info=edit_mask_info,
            )
            scaled = rendered.pop("scaled")
            candidate_image = _apply_protected_contribution_mask(
                baseline_image=baseline_image,
                zero_image=zero_render["image"],
                candidate_image=rendered["image"],
                protection_factor=enforcement["protection_factor"],
            )
            render_ms = _elapsed_ms(render_started)
            verification_started = time.perf_counter()
            checks = self._evaluate_checks(
                constraints=contract_ir.constraints,
                baseline_image=baseline_image,
                candidate_image=candidate_image,
                baseline_measurements=baseline_measurements,
                subject_masks=subject_masks,
                edit_mask=enforcement["effective_edit_mask"],
            )
            verification_ms = _elapsed_ms(verification_started)
            payload = ContractCandidate(
                image=candidate_image,
                prompt_result=scaled.prompt_result,
                adaptive=scaled.adaptive,
                parameters=rendered["parameters"],
                mask_info=(
                    copy.deepcopy(enforcement["mask_info"])
                ),
            )
            return ScaleCandidateEvaluation(
                payload=payload,
                checks=checks,
                has_effect=_has_perceptible_effect(
                    baseline_image,
                    candidate_image,
                ),
                render_ms=render_ms,
                verification_ms=verification_ms,
                diagnostics={"scale": scale},
            )

        try:
            search_result = search_largest_safe_scale(
                evaluate_scale,
                required_constraint_ids=required_ids,
                policy=self.search_policy,
            )
        except EditContractSearchError as exc:
            cause = exc.__cause__
            reason = getattr(cause, "code", exc.code)
            capability_failure = (
                isinstance(cause, EditContractMetricError)
                and _metric_error_is_capability(cause)
            )
            stale_failure = isinstance(cause, EditContractScalingError)
            status_code = 422 if capability_failure else (409 if stale_failure else 500)
            raise EditContractError(
                code=(
                    "contract_metric_unavailable"
                    if capability_failure
                    else (
                        "contract_stale_context"
                        if stale_failure
                        else "contract_search_failed"
                    )
                ),
                message=f"合約候選無法安全驗證：{exc}",
                disposition="unsupported" if capability_failure else "rejected",
                issues=({"reason": str(reason), "scale": exc.scale},),
                status_code=status_code,
            ) from exc

        if not search_result.succeeded or search_result.payload is None:
            disposition = (
                "rejected" if search_result.status == "no_change" else "unsupported"
            )
            status_code = 409 if search_result.status == "no_change" else 422
            code = (
                "contract_no_change"
                if search_result.status == "no_change"
                else "contract_unsatisfied"
            )
            message = (
                "符合限制的候選沒有可辨識修圖效果，未建立重複版本。"
                if search_result.status == "no_change"
                else "在安全搜尋範圍內找不到同時符合所有限制的有效結果。"
            )
            raise EditContractError(
                code=code,
                message=message,
                disposition=disposition,
                issues=(
                    {
                        "status": search_result.status,
                        "stop_reason": search_result.stop_reason,
                        "attempts": [
                            attempt.as_dict() for attempt in search_result.attempts
                        ],
                    },
                ),
                status_code=status_code,
            )

        selected = search_result.payload
        write_started = time.perf_counter()
        try:
            write_opencv_image(result_path, selected.image)
        except Exception as exc:
            try:
                if result_path.exists():
                    result_path.unlink()
            except OSError:
                pass
            raise EditContractError(
                code="contract_result_write_failed",
                message=f"合約已驗證但無法保存正式結果：{exc}",
                disposition="rejected",
                status_code=500,
            ) from exc
        image_write_ms = _elapsed_ms(write_started)
        total_ms = _elapsed_ms(started)
        report = EditContractReport(
            contract_ir=contract_ir,
            status=search_result.status,
            contract_hash=contract_hash,
            target_edit_id=target_edit_id,
            selected_target_baseline_path=selected_target_saved_path,
            render_anchor_path=render_anchor_saved_path,
            mask_source_path=mask_source_saved_path,
            requested_edit_plan=copy.deepcopy(dict(requested_plan)),
            requested_parameter_vector=_numeric_parameters(requested_parameters),
            requested_scale=1.0,
            search_policy_version=search_result.policy_version,
            applied_scale=search_result.selected_scale,
            actual_parameter_vector=_numeric_parameters(selected.parameters),
            checks=search_result.final_checks,
            attempts=search_result.attempts,
            timings={
                "search": search_result.elapsed_ms,
                "image_write": image_write_ms,
                "total": total_ms,
            },
        )
        explanation = (
            "所有修圖限制已由正式候選像素驗證通過。"
            if search_result.status == "passed"
            else (
                "原要求強度未通過全部限制；已採用可驗證的最大安全幅度 "
                f"{float(search_result.selected_scale or 0.0) * 100:.1f}%。"
            )
        )
        process_result = {
            "engine": "opencv",
            "parameters": copy.deepcopy(selected.parameters),
            "mask_info": copy.deepcopy(selected.mask_info),
            "timings_ms": {
                "contract_search": round(search_result.elapsed_ms, 3),
                "image_write": round(image_write_ms, 3),
                "total": round(total_ms, 3),
            },
            "explanation": explanation,
        }
        return ContractExecutionResult(
            prompt_result=copy.deepcopy(selected.prompt_result),
            adaptive=copy.deepcopy(selected.adaptive),
            process_result=process_result,
            report=report,
        )

    def _render_scaled_candidate(
        self,
        *,
        scale: float,
        source_prompt_result: Mapping[str, Any],
        source_adaptive: Mapping[str, Any],
        anchor_image: np.ndarray,
        mask_source_path: Path,
        mask_source_image: np.ndarray,
        prepared_edit_mask: np.ndarray | None,
        edit_mask_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        scaled = scale_adaptive_v2_result(
            prompt_result=source_prompt_result,
            adaptive=source_adaptive,
            scale=scale,
        )
        scaled_plan = scaled.prompt_result.get("edit_plan")
        if not isinstance(scaled_plan, Mapping):
            raise EditContractScalingError(
                "contract_scaled_plan_missing",
                "Scaled adaptive result has no EditPlan.",
            )
        parameters = build_engine_parameters("opencv", dict(scaled_plan))
        rendered = render_opencv_image(
            original=anchor_image,
            parameters=parameters,
            reference=None,
            mask_source_path=mask_source_path,
            mask_source_image=mask_source_image,
            prepared_mask=prepared_edit_mask,
            prepared_mask_info=(
                None if edit_mask_info is None else dict(edit_mask_info)
            ),
        )
        rendered["scaled"] = scaled
        return rendered

    def _prepare_enforcement(
        self,
        *,
        constraints: tuple[ContractConstraint, ...],
        subject_masks: Mapping[str, np.ndarray],
        edit_mask: np.ndarray | None,
        output_shape: tuple[int, int],
        edit_mask_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Apply registry-declared enforcement without metric-ID branches."""

        protection_factor = np.ones(output_shape, dtype=np.float32)
        strategies: list[str] = []
        protected_masks: list[str] = []
        dispatch = {
            "scale_search": lambda _constraint: None,
            "protected_mask_then_search": lambda constraint: _exclude_subject(
                constraint,
                subject_masks=subject_masks,
                protection_factor=protection_factor,
                protected_masks=protected_masks,
            ),
        }
        for constraint in constraints:
            definition = self.registry.get(constraint.metric_id)
            strategy = str(
                getattr(definition, "enforcement_strategy", "scale_search")
            )
            handler = dispatch.get(strategy)
            if handler is None:
                raise RuntimeError(
                    f"No contract enforcement handler for {strategy!r}."
                )
            handler(constraint)
            if strategy not in strategies:
                strategies.append(strategy)

        has_protection = bool(protected_masks)
        if edit_mask is None:
            effective_edit_mask = (
                protection_factor.copy() if has_protection else None
            )
        else:
            effective_edit_mask = np.clip(
                np.asarray(edit_mask, dtype=np.float32) * protection_factor,
                0.0,
                1.0,
            )
        if has_protection and not np.any(effective_edit_mask > 0.01):
            raise EditContractMetricError(
                "empty_edit_mask",
                "Protected regions leave no effective pixels for this edit.",
            )

        mask_info = None if edit_mask_info is None else dict(edit_mask_info)
        if has_protection:
            mask_info = dict(mask_info or {})
            mask_info["contract_enforcement"] = {
                "strategies": strategies,
                "protected_mask_types": protected_masks,
                "effective_edit_coverage": float(
                    np.count_nonzero(effective_edit_mask > 0.01)
                    / effective_edit_mask.size
                ),
            }
        return {
            "protection_factor": protection_factor if has_protection else None,
            "effective_edit_mask": effective_edit_mask,
            "mask_info": mask_info,
        }

    def _prepare_subject_masks(
        self,
        *,
        constraints: tuple[ContractConstraint, ...],
        mask_source_path: Path,
        output_shape: tuple[int, int],
    ) -> dict[str, np.ndarray]:
        masks: dict[str, np.ndarray] = {}
        for constraint in constraints:
            if "subject_mask" not in constraint.capability_requirements:
                continue
            mask_type = constraint.mask_type
            if mask_type in masks:
                continue
            if not mask_type.startswith("semantic_"):
                raise EditContractMetricError(
                    "unsupported_subject_mask",
                    f"Unsupported contract subject mask {mask_type!r}.",
                )
            target = constraint.subject_region
            semantic = get_semantic_region_mask(mask_source_path, target)
            mask = np.asarray(semantic.feathered_mask, dtype=np.float32)
            if mask.shape != output_shape:
                mask = cv2.resize(
                    mask,
                    (output_shape[1], output_shape[0]),
                    interpolation=cv2.INTER_LINEAR,
                )
            masks[mask_type] = np.clip(mask, 0.0, 1.0)
        return masks

    def _baseline_measurements(
        self,
        *,
        constraints: tuple[ContractConstraint, ...],
        baseline_image: np.ndarray,
        subject_masks: Mapping[str, np.ndarray],
        edit_mask: np.ndarray | None,
    ) -> dict[str, MetricMeasurement]:
        measurements: dict[str, MetricMeasurement] = {}
        for constraint in constraints:
            self.registry.validate_constraint(constraint)
            definition = self.registry.get(constraint.metric_id)
            context = MetricEvaluationContext(
                metric_id=constraint.metric_id,
                metric_version=constraint.metric_version,
                baseline_image=baseline_image,
                candidate_image=baseline_image,
                subject_region=constraint.subject_region,
                subject_mask=subject_masks.get(constraint.mask_type),
                edit_mask=edit_mask,
            )
            measured = definition.evaluator(context)
            _validate_measurement_identity(measured, constraint, definition)
            measurements[constraint.constraint_id] = measured
        return measurements

    def _evaluate_checks(
        self,
        *,
        constraints: tuple[ContractConstraint, ...],
        baseline_image: np.ndarray,
        candidate_image: np.ndarray,
        baseline_measurements: Mapping[str, MetricMeasurement],
        subject_masks: Mapping[str, np.ndarray],
        edit_mask: np.ndarray | None,
    ) -> tuple[MetricCheck, ...]:
        checks: list[MetricCheck] = []
        for constraint in constraints:
            self.registry.validate_constraint(constraint)
            definition = self.registry.get(constraint.metric_id)
            context = MetricEvaluationContext(
                metric_id=constraint.metric_id,
                metric_version=constraint.metric_version,
                baseline_image=baseline_image,
                candidate_image=candidate_image,
                subject_region=constraint.subject_region,
                subject_mask=subject_masks.get(constraint.mask_type),
                edit_mask=edit_mask,
            )
            measured = definition.evaluator(context)
            baseline = baseline_measurements[constraint.constraint_id]
            _validate_measurement_identity(measured, constraint, definition)
            _validate_measurement_identity(baseline, constraint, definition)
            effective = _effective_threshold(constraint, baseline)
            passed = _measurement_passes(
                constraint,
                baseline=baseline,
                measured=measured,
                effective_threshold=effective,
            )
            checks.append(
                MetricCheck(
                    constraint_id=constraint.constraint_id,
                    metric_id=constraint.metric_id,
                    metric_version=constraint.metric_version,
                    operator=constraint.operator,
                    unit=constraint.unit,
                    policy_threshold=constraint.threshold,
                    effective_threshold=effective,
                    baseline_value=baseline.value,
                    candidate_value=measured.value,
                    threshold_source=constraint.threshold_source,
                    passed=passed,
                    details={
                        **dict(measured.details),
                        "baseline_sample_count": baseline.sample_count,
                        "candidate_sample_count": measured.sample_count,
                        **(
                            {
                                "baseline_event_count": _measurement_count(baseline),
                                "candidate_event_count": _measurement_count(measured),
                            }
                            if constraint.operator == "no_worse_than_baseline"
                            else {}
                        ),
                        "executor_version": CONTRACT_EXECUTOR_VERSION,
                    },
                )
            )
        return tuple(checks)


def _effective_threshold(
    constraint: ContractConstraint,
    baseline: MetricMeasurement,
) -> float:
    if constraint.operator == "<=":
        return float(constraint.threshold)
    if constraint.operator != "no_worse_than_baseline":
        raise EditContractMetricError(
            "unsupported_contract_operator",
            f"Unsupported operator {constraint.operator!r}.",
        )
    return max(float(constraint.threshold), float(baseline.value))


def _measurement_passes(
    constraint: ContractConstraint,
    *,
    baseline: MetricMeasurement,
    measured: MetricMeasurement,
    effective_threshold: float,
) -> bool:
    numeric_pass = measured.value <= effective_threshold + 1e-12
    if constraint.operator != "no_worse_than_baseline":
        return numeric_pass
    # Below the versioned safety cap, a qualitative protection may use the
    # remaining safe headroom.  Once the selected baseline already exceeds
    # that cap, fallback means strictly no worse: numeric tolerance must never
    # amount to permission for one additional clipped pixel.
    baseline_exceeds_cap = baseline.value > float(constraint.threshold) + 1e-12
    if not baseline_exceeds_cap:
        return numeric_pass
    return numeric_pass and _measurement_count(measured) <= _measurement_count(
        baseline
    )


def _measurement_count(measurement: MetricMeasurement) -> int:
    for key in ("clipped_count", "changed_count"):
        value = measurement.details.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return int(round(float(measurement.value) * measurement.sample_count))


def _validate_measurement_identity(
    measurement: MetricMeasurement,
    constraint: ContractConstraint,
    definition: Any,
) -> None:
    definition_identity = {
        "metric_id": str(getattr(definition, "metric_id", constraint.metric_id)),
        "metric_version": str(
            getattr(definition, "metric_version", constraint.metric_version)
        ),
        "unit": str(getattr(definition, "unit", constraint.unit)),
    }
    constraint_identity = {
        "metric_id": constraint.metric_id,
        "metric_version": constraint.metric_version,
        "unit": constraint.unit,
    }
    if definition_identity != constraint_identity:
        raise EditContractMetricError(
            "metric_identity_mismatch",
            "Metric definition identity does not match the validated constraint.",
        )
    mismatches = {
        "metric_id": (measurement.metric_id, definition_identity["metric_id"]),
        "metric_version": (
            measurement.metric_version,
            definition_identity["metric_version"],
        ),
        "unit": (measurement.unit, definition_identity["unit"]),
    }
    wrong = {
        field: {"actual": actual, "expected": expected}
        for field, (actual, expected) in mismatches.items()
        if actual != expected
    }
    if wrong:
        raise EditContractMetricError(
            "metric_identity_mismatch",
            f"Metric evaluator identity does not match its definition: {wrong!r}.",
        )


def _exclude_subject(
    constraint: ContractConstraint,
    *,
    subject_masks: Mapping[str, np.ndarray],
    protection_factor: np.ndarray,
    protected_masks: list[str],
) -> None:
    if constraint.mask_type in protected_masks:
        return
    subject = subject_masks.get(constraint.mask_type)
    if subject is None:
        raise EditContractMetricError(
            "missing_mask",
            f"Protected mask {constraint.mask_type!r} is unavailable.",
        )
    if subject.shape != protection_factor.shape:
        raise EditContractMetricError(
            "mask_shape_mismatch",
            "Protected mask does not match the effective edit mask.",
        )
    np.multiply(
        protection_factor,
        1.0 - np.clip(subject.astype(np.float32), 0.0, 1.0),
        out=protection_factor,
    )
    protected_masks.append(constraint.mask_type)


def _apply_protected_contribution_mask(
    *,
    baseline_image: np.ndarray,
    zero_image: np.ndarray,
    candidate_image: np.ndarray,
    protection_factor: np.ndarray | None,
) -> np.ndarray:
    if protection_factor is None:
        return candidate_image
    contribution = (
        candidate_image.astype(np.float32) - zero_image.astype(np.float32)
    )
    composed = baseline_image.astype(np.float32) + contribution * protection_factor[..., None]
    return np.clip(np.rint(composed), 0, 255).astype(np.uint8)


def _reconstructs_selected_target(
    baseline_image: np.ndarray,
    zero_image: np.ndarray,
    selected_target_path: Path,
) -> bool:
    if np.array_equal(baseline_image, zero_image):
        return True
    # Feathered local compositing may round a mathematically unchanged uint8
    # channel by one code value.  This numeric renderer tolerance proves no
    # visible parent contribution is missing; it is not a metric allowance and
    # cannot admit a genuinely different selected version.
    direct_delta = np.abs(
        baseline_image.astype(np.int16) - zero_image.astype(np.int16)
    )
    if int(np.max(direct_delta)) <= 1:
        return True
    suffix = selected_target_path.suffix.lower()
    extension = suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    ok, encoded = cv2.imencode(extension, zero_image)
    if not ok:
        raise RuntimeError(
            f"Could not encode scale-zero reconstruction as {extension}."
        )
    persisted = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if persisted is None:
        return False
    persisted_delta = np.abs(
        baseline_image.astype(np.int16) - persisted.astype(np.int16)
    )
    return int(np.max(persisted_delta)) <= 1


def _metric_error_is_capability(error: EditContractMetricError) -> bool:
    return error.code in {
        "empty_edit_mask",
        "empty_outside_scope_domain",
        "insufficient_subject_mask_core",
        "missing_mask",
        "unsupported_subject_mask",
    }


def _has_perceptible_effect(
    baseline: np.ndarray,
    candidate: np.ndarray,
) -> bool:
    if baseline.shape != candidate.shape:
        return False
    delta = np.max(
        np.abs(candidate.astype(np.int16) - baseline.astype(np.int16)),
        axis=2,
    )
    changed = int(np.count_nonzero(delta >= 2))
    minimum_changed = max(1, int(math.ceil(delta.size * 0.00001)))
    return changed >= minimum_changed


def _numeric_parameters(parameters: Mapping[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in parameters.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Non-finite engine parameter {key!r}")
        numeric[str(key)] = number
    return numeric


def _canonical_plan_for_hash(value: Any) -> Any:
    """Remove storage/lineage identifiers while preserving edit semantics."""

    volatile_keys = {
        "anchor_edit_id",
        "anchor_image_path",
        "edit_id",
        "episode_id",
        "scope_episode_id",
        "scope_key",
        "session_id",
        "style_anchor_image_path",
        "style_source_edit_id",
    }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_plan_for_hash(item)
            for key, item in value.items()
            if str(key) not in volatile_keys
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_plan_for_hash(item) for item in value]
    return copy.deepcopy(value)


def _file_identity(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


__all__ = [
    "CONTRACT_EXECUTOR_VERSION",
    "ContractCandidate",
    "ContractExecutionResult",
    "EditContractService",
]
