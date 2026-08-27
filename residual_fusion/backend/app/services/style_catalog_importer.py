from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from app.services.style_registry import (
    STYLE_CATALOG_SCHEMA_VERSION,
    STYLE_RENDERER_VERSION,
    STYLE_WORKING_COLOR_SPACE,
    StyleAssetError,
    StyleCatalogError,
    StyleRegistry,
    canonical_recipe_hash,
    file_sha256,
)


MAX_LUT_BYTES = 32 * 1024 * 1024
MIN_LUT_GRID_SIZE = 2
MAX_LUT_GRID_SIZE = 128
_LUT_SIZE_PATTERN = re.compile(r"^LUT_3D_SIZE\s+([0-9]+)\s*$")
_DOMAIN_PATTERN = re.compile(
    r"^DOMAIN_(MIN|MAX)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$"
)


def compile_style_catalog(
    *,
    source_path: Path,
    source_manifest_path: Path,
    asset_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Compile reviewable YAML into a hash-locked production catalog."""

    source_payload = _load_yaml_mapping(source_path)
    source_manifest = _load_yaml_mapping(source_manifest_path)
    sources = source_manifest.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        raise StyleCatalogError("Style source manifest has no allowlisted sources")

    style_values = source_payload.get("styles")
    if not isinstance(style_values, list) or not style_values:
        raise StyleCatalogError("Style source catalog has no styles")
    renderer_templates = source_payload.get("renderer_templates", {})
    if not isinstance(renderer_templates, Mapping):
        raise StyleCatalogError("renderer_templates must be an object")
    for template_id, template_value in renderer_templates.items():
        if not str(template_id).strip() or not isinstance(
            template_value,
            Mapping,
        ):
            raise StyleCatalogError(
                "Each renderer template needs an id and object value"
            )
    review_decisions = _review_decisions(source_payload.get("review_batches"))

    compiled_styles: list[dict[str, Any]] = []
    compiled_style_ids: set[str] = set()
    for raw_style in style_values:
        if not isinstance(raw_style, Mapping):
            raise StyleCatalogError("Each source style must be an object")
        style = json.loads(json.dumps(raw_style, ensure_ascii=False))
        style_id = str(style.get("style_id") or "").strip()
        if style_id in compiled_style_ids:
            raise StyleCatalogError(f"Duplicate source style_id: {style_id}")
        compiled_style_ids.add(style_id)
        source_ref = str(style.pop("source_ref", "") or "").strip()
        source = sources.get(source_ref)
        if not source_ref or not isinstance(source, Mapping):
            raise StyleCatalogError(
                f"{style_id or '<unknown>'} references a non-allowlisted source"
            )
        source_copy = json.loads(json.dumps(source, ensure_ascii=False))
        for field in ("source_url", "author", "license", "attribution"):
            if not str(source_copy.get(field) or "").strip():
                raise StyleCatalogError(
                    f"Allowlisted source {source_ref!r} is missing {field}"
                )
        if not isinstance(source_copy.get("redistribution_allowed"), bool):
            raise StyleCatalogError(
                f"Allowlisted source {source_ref!r} needs an explicit "
                "redistribution_allowed boolean"
            )
        review = style.get("review")
        if (
            isinstance(review, Mapping)
            and review.get("status") == "approved"
            and not source_copy["redistribution_allowed"]
        ):
            raise StyleCatalogError(
                f"{style_id} cannot be approved from a non-redistributable source"
            )
        renderer_template = str(
            style.pop("renderer_template", "") or ""
        ).strip()
        renderer_override = style.get("renderer", {})
        if not isinstance(renderer_override, Mapping):
            raise StyleCatalogError(f"{style_id}.renderer must be an object")
        if renderer_template:
            template = renderer_templates.get(renderer_template)
            if not isinstance(template, Mapping):
                raise StyleCatalogError(
                    f"{style_id} references unknown renderer template "
                    f"{renderer_template!r}"
                )
            renderer = _deep_merge(template, renderer_override)
        else:
            renderer = json.loads(
                json.dumps(renderer_override, ensure_ascii=False)
            )
        if not renderer:
            raise StyleCatalogError(f"{style_id}.renderer must not be empty")
        renderer.setdefault("version", STYLE_RENDERER_VERSION)
        renderer.setdefault(
            "working_color_space",
            STYLE_WORKING_COLOR_SPACE,
        )
        style["renderer"] = renderer
        decision = review_decisions.get(style_id)
        if decision is not None:
            style["review"] = _deep_merge(
                style.get("review", {}),
                decision,
            )
        recipe_hash = canonical_recipe_hash(renderer)
        style["recipe_hash"] = recipe_hash
        lut = renderer.get("lut")
        if isinstance(lut, Mapping):
            relative = str(lut.get("path") or "").strip()
            asset_path = _safe_join(asset_root, relative)
            validate_cube_lut(asset_path)
            style["asset_hash"] = file_sha256(asset_path)
        else:
            style["asset_hash"] = recipe_hash
        style["source"] = source_copy
        compiled_styles.append(style)
    unknown_review_ids = set(review_decisions).difference(compiled_style_ids)
    if unknown_review_ids:
        raise StyleCatalogError(
            "Review batch references unknown styles: "
            f"{sorted(unknown_review_ids)!r}"
        )

    payload = {
        "schema_version": STYLE_CATALOG_SCHEMA_VERSION,
        "catalog_version": str(
            source_payload.get("catalog_version") or ""
        ).strip(),
        "generated_at": str(source_payload.get("generated_at") or "").strip(),
        "styles": compiled_styles,
    }
    if not payload["catalog_version"] or not payload["generated_at"]:
        raise StyleCatalogError(
            "Source catalog needs catalog_version and generated_at"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    # Re-open through the production loader.  Compilation is successful only
    # if runtime validation sees the same immutable catalog.
    StyleRegistry(output_path)
    return payload


def validate_cube_lut(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved.suffix.lower() != ".cube":
        raise StyleAssetError("Only .cube LUT assets are accepted in v1")
    if not resolved.is_file():
        raise StyleAssetError(f"LUT asset does not exist: {resolved}")
    file_size = resolved.stat().st_size
    if file_size <= 0 or file_size > MAX_LUT_BYTES:
        raise StyleAssetError(
            f"LUT asset size must be between 1 and {MAX_LUT_BYTES} bytes"
        )
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StyleAssetError("LUT asset must be UTF-8 text") from exc

    grid_size: int | None = None
    domain_min = (0.0, 0.0, 0.0)
    domain_max = (1.0, 1.0, 1.0)
    samples: list[tuple[float, float, float]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("TITLE"):
            continue
        size_match = _LUT_SIZE_PATTERN.fullmatch(line)
        if size_match:
            if grid_size is not None:
                raise StyleAssetError("LUT_3D_SIZE may only appear once")
            grid_size = int(size_match.group(1))
            continue
        domain_match = _DOMAIN_PATTERN.fullmatch(line)
        if domain_match:
            values = tuple(
                _finite(part, f"line {line_number}")
                for part in domain_match.groups()[1:]
            )
            if domain_match.group(1) == "MIN":
                domain_min = values
            else:
                domain_max = values
            continue
        parts = line.split()
        if len(parts) != 3:
            raise StyleAssetError(
                f"Unsupported LUT directive or sample at line {line_number}"
            )
        samples.append(
            tuple(_finite(part, f"line {line_number}") for part in parts)
        )

    if grid_size is None:
        raise StyleAssetError("LUT_3D_SIZE is required")
    if not MIN_LUT_GRID_SIZE <= grid_size <= MAX_LUT_GRID_SIZE:
        raise StyleAssetError(
            f"LUT grid size must be {MIN_LUT_GRID_SIZE}..{MAX_LUT_GRID_SIZE}"
        )
    if len(samples) != grid_size ** 3:
        raise StyleAssetError(
            f"LUT has {len(samples)} samples; expected {grid_size ** 3}"
        )
    if any(low >= high for low, high in zip(domain_min, domain_max)):
        raise StyleAssetError("Each LUT domain minimum must be below maximum")
    if domain_min != (0.0, 0.0, 0.0) or domain_max != (1.0, 1.0, 1.0):
        raise StyleAssetError(
            "v1 only accepts explicit or implicit 0..1 sRGB/Rec.709 LUT domains"
        )
    flat = [component for sample in samples for component in sample]
    minimum_output = min(flat)
    maximum_output = max(flat)
    if minimum_output < -0.25 or maximum_output > 1.25:
        raise StyleAssetError(
            "LUT output exceeds the guarded -0.25..1.25 range"
        )
    _compile_ocio_processor(resolved)
    return {
        "path": str(resolved),
        "file_size": file_size,
        "grid_size": grid_size,
        "domain_min": list(domain_min),
        "domain_max": list(domain_max),
        "sample_count": len(samples),
        "minimum_output": minimum_output,
        "maximum_output": maximum_output,
        "sha256": file_sha256(resolved),
    }


def _compile_ocio_processor(path: Path) -> None:
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise StyleAssetError(
            "OpenColorIO is required to validate .cube assets"
        ) from exc
    try:
        config = ocio.Config.CreateRaw()
        transform = ocio.FileTransform(
            src=str(path.resolve()),
            interpolation=ocio.INTERP_TETRAHEDRAL,
        )
        config.getProcessor(transform).getDefaultCPUProcessor()
    except Exception as exc:
        raise StyleAssetError(
            f"OpenColorIO rejected LUT {path.name}: {exc}"
        ) from exc


def _safe_join(root: Path, relative: str) -> Path:
    if not relative:
        raise StyleAssetError("LUT asset path must not be empty")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise StyleAssetError(
            f"LUT asset path escapes the asset root: {relative!r}"
        ) from exc
    return candidate


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise StyleCatalogError(f"Missing YAML file: {path}") from exc
    except yaml.YAMLError as exc:
        raise StyleCatalogError(f"Invalid YAML file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StyleCatalogError(f"YAML root must be an object: {path}")
    return value


def _deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = json.loads(json.dumps(value, ensure_ascii=False))
    return merged


def _review_decisions(value: object) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise StyleCatalogError("review_batches must be a list")
    decisions: dict[str, dict[str, Any]] = {}
    for raw_batch in value:
        if not isinstance(raw_batch, Mapping):
            raise StyleCatalogError("Each review batch must be an object")
        status = str(raw_batch.get("status") or "").strip()
        if status not in {"reviewed", "approved", "rejected"}:
            raise StyleCatalogError(
                f"Unsupported review batch status: {status!r}"
            )
        style_ids = raw_batch.get("style_ids")
        if not isinstance(style_ids, list) or not style_ids:
            raise StyleCatalogError("Review batch needs style_ids")
        reviewed_at = str(raw_batch.get("reviewed_at") or "").strip()
        reviewer = str(raw_batch.get("reviewer") or "").strip()
        if status == "approved" and (not reviewed_at or not reviewer):
            raise StyleCatalogError(
                "Approved review batch needs reviewed_at and reviewer"
            )
        preview_directory = str(
            raw_batch.get("preview_directory") or ""
        ).strip().rstrip("/\\")
        decision_note = str(
            raw_batch.get("decision_note") or ""
        ).strip()
        for raw_style_id in style_ids:
            style_id = str(raw_style_id or "").strip()
            if not style_id or style_id in decisions:
                raise StyleCatalogError(
                    f"Invalid or duplicate review decision for {style_id!r}"
                )
            decision: dict[str, Any] = {"status": status}
            if reviewed_at:
                decision["reviewed_at"] = reviewed_at
            if reviewer:
                decision["reviewer"] = reviewer
            if preview_directory:
                decision["preview_path"] = (
                    f"{preview_directory}/{style_id}.jpg"
                )
            if decision_note:
                decision["decision_note"] = decision_note
            decisions[style_id] = decision
    return decisions


def _finite(value: object, field: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StyleAssetError(f"{field} contains a non-numeric value") from exc
    if not math.isfinite(numeric):
        raise StyleAssetError(f"{field} contains NaN or Infinity")
    return numeric


__all__ = [
    "MAX_LUT_BYTES",
    "compile_style_catalog",
    "validate_cube_lut",
]
