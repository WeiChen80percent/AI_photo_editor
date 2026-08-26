from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.services.edit_schema import validate_edit_parameters


STYLE_CATALOG_SCHEMA_VERSION = "style_catalog_v1"
STYLE_RENDERER_VERSION = "opencv_style_renderer_v1"
STYLE_WORKING_COLOR_SPACE = "srgb_rec709"
STYLE_REVIEW_STATES = frozenset(
    {"draft", "reviewed", "approved", "rejected"}
)
STYLE_FAMILIES = frozenset(
    {
        "natural_clean",
        "portrait_skin",
        "landscape_travel",
        "cinematic",
        "film_retro",
        "black_white",
        "night_neon",
        "pastel_creative",
    }
)

_STYLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "style_catalog"
    / "catalog.lock.json"
)
_ASSET_ROOT = (
    Path(__file__).resolve().parents[1]
    / "style_catalog"
    / "assets"
)


class StyleCatalogError(ValueError):
    code = "style_catalog_invalid"


class StyleNotFoundError(StyleCatalogError):
    code = "style_not_found"


class StyleVersionMismatchError(StyleCatalogError):
    code = "style_version_mismatch"


class StyleAssetError(StyleCatalogError):
    code = "style_asset_invalid"


@dataclass(frozen=True, slots=True)
class StyleDefinition:
    style_id: str
    version: str
    display_name_zh: str
    display_name_en: str
    aliases: tuple[str, ...]
    family: str
    tags: tuple[str, ...]
    description: str
    renderer: Mapping[str, Any]
    strength_default: float
    strength_minimum: float
    strength_maximum: float
    recipe_hash: str
    asset_hash: str
    source: Mapping[str, Any]
    review: Mapping[str, Any]
    known_limitations: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.style_id}@{self.version}"

    @property
    def approved(self) -> bool:
        return self.review.get("status") == "approved"

    @property
    def legacy_preset_name(self) -> str | None:
        value = self.renderer.get("legacy_preset_name")
        normalized = str(value or "").strip()
        return normalized or None

    @property
    def base_parameters(self) -> dict[str, float]:
        value = self.renderer.get("base_parameters")
        return validate_edit_parameters(
            value if isinstance(value, Mapping) else {}
        )

    @property
    def asset_path(self) -> Path | None:
        asset = self.renderer.get("lut")
        if not isinstance(asset, Mapping):
            return None
        relative = str(asset.get("path") or "").strip()
        if not relative:
            return None
        return _safe_asset_path(relative)

    def validate_strength(self, value: object | None) -> float:
        if value is None:
            return self.strength_default
        if isinstance(value, bool):
            raise StyleCatalogError("Style strength must be numeric")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise StyleCatalogError("Style strength must be numeric") from exc
        if not math.isfinite(numeric):
            raise StyleCatalogError("Style strength must be finite")
        if not self.strength_minimum <= numeric <= self.strength_maximum:
            raise StyleCatalogError(
                "Style strength is outside the catalog range "
                f"[{self.strength_minimum}, {self.strength_maximum}]"
            )
        return round(numeric, 4)

    def public_metadata(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "version": self.version,
            "display_name": {
                "zh": self.display_name_zh,
                "en": self.display_name_en,
            },
            "aliases": list(self.aliases),
            "family": self.family,
            "tags": list(self.tags),
            "description": self.description,
            "working_color_space": STYLE_WORKING_COLOR_SPACE,
            "renderer_version": STYLE_RENDERER_VERSION,
            "strength": {
                "default": self.strength_default,
                "minimum": self.strength_minimum,
                "maximum": self.strength_maximum,
            },
            "recipe_hash": self.recipe_hash,
            "asset_hash": self.asset_hash,
            "source": dict(self.source),
            "review": dict(self.review),
            "known_limitations": list(self.known_limitations),
            "legacy_preset_name": self.legacy_preset_name,
        }


class StyleRegistry:
    def __init__(self, catalog_path: Path = _CATALOG_PATH):
        self.catalog_path = catalog_path.resolve()
        payload = _load_json(self.catalog_path)
        if payload.get("schema_version") != STYLE_CATALOG_SCHEMA_VERSION:
            raise StyleCatalogError(
                "Unsupported style catalog schema version: "
                f"{payload.get('schema_version')!r}"
            )
        styles_value = payload.get("styles")
        if not isinstance(styles_value, list) or not styles_value:
            raise StyleCatalogError("Style catalog must contain styles")

        definitions = tuple(_parse_style(item) for item in styles_value)
        by_key: dict[str, StyleDefinition] = {}
        latest: dict[str, StyleDefinition] = {}
        aliases: dict[str, list[StyleDefinition]] = {}
        for style in definitions:
            if style.key in by_key:
                raise StyleCatalogError(
                    f"Duplicate style version in catalog: {style.key}"
                )
            by_key[style.key] = style
            current = latest.get(style.style_id)
            if current is None or _semver(style.version) > _semver(current.version):
                latest[style.style_id] = style
            for surface in (
                style.style_id,
                style.display_name_zh,
                style.display_name_en,
                *style.aliases,
            ):
                normalized = normalize_style_text(surface)
                aliases.setdefault(normalized, []).append(style)

        self.catalog_version = _required_text(
            payload.get("catalog_version"),
            "catalog_version",
        )
        self.generated_at = _required_text(
            payload.get("generated_at"),
            "generated_at",
        )
        self._definitions = definitions
        self._by_key = MappingProxyType(by_key)
        self._latest = MappingProxyType(latest)
        self._aliases = MappingProxyType(
            {key: tuple(values) for key, values in aliases.items()}
        )

    def list_styles(
        self,
        *,
        approved_only: bool = True,
    ) -> tuple[StyleDefinition, ...]:
        values = tuple(self._latest.values())
        if approved_only:
            values = tuple(style for style in values if style.approved)
        return tuple(
            sorted(
                values,
                key=lambda style: (
                    style.family,
                    style.display_name_en.casefold(),
                    style.style_id,
                ),
            )
        )

    def resolve(
        self,
        style_id: object,
        version: object | None = None,
        *,
        approved_only: bool = True,
    ) -> StyleDefinition:
        normalized_id = str(style_id or "").strip().lower()
        if version is None or not str(version).strip():
            style = self._latest.get(normalized_id)
        else:
            style = self._by_key.get(
                f"{normalized_id}@{str(version).strip()}"
            )
        if style is None:
            raise StyleNotFoundError(
                f"Unknown style: {normalized_id or style_id!r}"
            )
        if approved_only and not style.approved:
            raise StyleNotFoundError(
                f"Style is not approved for production: {style.key}"
            )
        _verify_style_asset(style)
        return style

    def exact_alias_candidates(
        self,
        value: object,
        *,
        approved_only: bool = True,
    ) -> tuple[StyleDefinition, ...]:
        normalized = normalize_style_text(value)
        candidates = self._aliases.get(normalized, ())
        if approved_only:
            candidates = tuple(style for style in candidates if style.approved)
        return _deduplicate_latest(candidates)

    def prompt_alias_candidates(
        self,
        prompt: object,
        *,
        approved_only: bool = True,
    ) -> tuple[StyleDefinition, ...]:
        normalized = normalize_style_text(prompt)
        matches: list[tuple[int, StyleDefinition]] = []
        for alias, styles in self._aliases.items():
            if len(alias) < 3 or alias not in normalized:
                continue
            for style in styles:
                if approved_only and not style.approved:
                    continue
                matches.append((len(alias), style))
        if not matches:
            return ()
        longest = max(length for length, _ in matches)
        return _deduplicate_latest(
            style for length, style in matches if length == longest
        )

    def payload(self) -> dict[str, Any]:
        styles = self.list_styles()
        families: dict[str, int] = {}
        for style in styles:
            families[style.family] = families.get(style.family, 0) + 1
        return {
            "schema_version": STYLE_CATALOG_SCHEMA_VERSION,
            "catalog_version": self.catalog_version,
            "generated_at": self.generated_at,
            "renderer_version": STYLE_RENDERER_VERSION,
            "working_color_space": STYLE_WORKING_COLOR_SPACE,
            "style_count": len(styles),
            "families": families,
            "styles": [style.public_metadata() for style in styles],
        }


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: StyleRegistry | None = None


def get_style_registry() -> StyleRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = StyleRegistry()
    return _REGISTRY


def reset_style_registry_cache() -> None:
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


def normalize_style_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[\s\-_]+", " ", text)
    text = re.sub(r"[，。！？、,.!?;:：；\"'「」『』（）()]+", "", text)
    return text.strip()


def canonical_recipe_hash(renderer: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        renderer,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_style(value: object) -> StyleDefinition:
    if not isinstance(value, Mapping):
        raise StyleCatalogError("Each catalog style must be an object")
    style_id = _required_text(value.get("style_id"), "style_id").lower()
    if _STYLE_ID_PATTERN.fullmatch(style_id) is None:
        raise StyleCatalogError(f"Invalid style_id: {style_id!r}")
    version = _required_text(value.get("version"), f"{style_id}.version")
    if _SEMVER_PATTERN.fullmatch(version) is None:
        raise StyleCatalogError(
            f"Invalid semantic version for {style_id}: {version!r}"
        )
    names = value.get("display_name")
    if not isinstance(names, Mapping):
        raise StyleCatalogError(f"{style_id}.display_name must be an object")
    display_name_zh = _required_text(
        names.get("zh"),
        f"{style_id}.display_name.zh",
    )
    display_name_en = _required_text(
        names.get("en"),
        f"{style_id}.display_name.en",
    )
    aliases = _string_tuple(value.get("aliases"), f"{style_id}.aliases")
    family = _required_text(value.get("family"), f"{style_id}.family")
    if family not in STYLE_FAMILIES:
        raise StyleCatalogError(
            f"{style_id}.family must be one of {sorted(STYLE_FAMILIES)!r}"
        )
    tags = _string_tuple(value.get("tags"), f"{style_id}.tags")
    description = _required_text(
        value.get("description"),
        f"{style_id}.description",
    )
    renderer_value = value.get("renderer")
    if not isinstance(renderer_value, Mapping):
        raise StyleCatalogError(f"{style_id}.renderer must be an object")
    renderer = _validate_renderer(style_id, renderer_value)

    strength = value.get("strength")
    if not isinstance(strength, Mapping):
        raise StyleCatalogError(f"{style_id}.strength must be an object")
    minimum = _finite(strength.get("minimum"), f"{style_id}.strength.minimum")
    maximum = _finite(strength.get("maximum"), f"{style_id}.strength.maximum")
    default = _finite(strength.get("default"), f"{style_id}.strength.default")
    if minimum < 0 or maximum > 1 or minimum > default or default > maximum:
        raise StyleCatalogError(
            f"{style_id}.strength must satisfy 0 <= minimum <= default "
            "<= maximum <= 1"
        )

    recipe_hash = _required_text(
        value.get("recipe_hash"),
        f"{style_id}.recipe_hash",
    )
    if _SHA256_PATTERN.fullmatch(recipe_hash) is None:
        raise StyleCatalogError(f"{style_id}.recipe_hash is not SHA-256")
    actual_recipe_hash = canonical_recipe_hash(renderer)
    if recipe_hash != actual_recipe_hash:
        raise StyleVersionMismatchError(
            f"{style_id}@{version} recipe hash mismatch"
        )

    asset_hash = _required_text(
        value.get("asset_hash"),
        f"{style_id}.asset_hash",
    )
    if _SHA256_PATTERN.fullmatch(asset_hash) is None:
        raise StyleCatalogError(f"{style_id}.asset_hash is not SHA-256")

    source = _mapping_copy(value.get("source"), f"{style_id}.source")
    for field in ("source_url", "author", "license", "attribution"):
        _required_text(source.get(field), f"{style_id}.source.{field}")
    if not isinstance(source.get("redistribution_allowed"), bool):
        raise StyleCatalogError(
            f"{style_id}.source.redistribution_allowed must be boolean"
        )

    review = _mapping_copy(value.get("review"), f"{style_id}.review")
    status = _required_text(review.get("status"), f"{style_id}.review.status")
    if status not in STYLE_REVIEW_STATES:
        raise StyleCatalogError(
            f"{style_id}.review.status must be one of "
            f"{sorted(STYLE_REVIEW_STATES)!r}"
        )
    if status == "approved":
        if not source["redistribution_allowed"]:
            raise StyleCatalogError(
                f"{style_id} cannot be approved without redistribution rights"
            )
        _required_text(
            review.get("reviewed_at"),
            f"{style_id}.review.reviewed_at",
        )
        _required_text(
            review.get("reviewer"),
            f"{style_id}.review.reviewer",
        )
        _required_text(
            review.get("preview_path"),
            f"{style_id}.review.preview_path",
        )

    style = StyleDefinition(
        style_id=style_id,
        version=version,
        display_name_zh=display_name_zh,
        display_name_en=display_name_en,
        aliases=aliases,
        family=family,
        tags=tags,
        description=description,
        renderer=MappingProxyType(renderer),
        strength_default=default,
        strength_minimum=minimum,
        strength_maximum=maximum,
        recipe_hash=recipe_hash,
        asset_hash=asset_hash,
        source=MappingProxyType(source),
        review=MappingProxyType(review),
        known_limitations=_string_tuple(
            value.get("known_limitations"),
            f"{style_id}.known_limitations",
            allow_empty=True,
        ),
    )
    _verify_style_asset(style)
    return style


def _validate_renderer(
    style_id: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    renderer = json.loads(json.dumps(value, ensure_ascii=False))
    if renderer.get("version") != STYLE_RENDERER_VERSION:
        raise StyleCatalogError(
            f"{style_id}.renderer.version must be {STYLE_RENDERER_VERSION!r}"
        )
    if renderer.get("working_color_space") != STYLE_WORKING_COLOR_SPACE:
        raise StyleCatalogError(
            f"{style_id}.renderer.working_color_space must be "
            f"{STYLE_WORKING_COLOR_SPACE!r}"
        )
    kind = _required_text(renderer.get("kind"), f"{style_id}.renderer.kind")
    if kind not in {"recipe", "legacy_preset"}:
        raise StyleCatalogError(
            f"{style_id}.renderer.kind is unsupported: {kind!r}"
        )
    if kind == "legacy_preset":
        _required_text(
            renderer.get("legacy_preset_name"),
            f"{style_id}.renderer.legacy_preset_name",
        )
    else:
        base = renderer.get("base_parameters")
        if base is not None and not isinstance(base, Mapping):
            raise StyleCatalogError(
                f"{style_id}.renderer.base_parameters must be an object"
            )
        if isinstance(base, Mapping):
            unknown = set(base).difference(
                validate_edit_parameters(base).keys()
            )
            if unknown:
                raise StyleCatalogError(
                    f"{style_id} has unsupported base parameters: "
                    f"{sorted(unknown)!r}"
                )
    lut = renderer.get("lut")
    if lut is not None:
        if not isinstance(lut, Mapping):
            raise StyleCatalogError(
                f"{style_id}.renderer.lut must be an object"
            )
        relative = _required_text(
            lut.get("path"),
            f"{style_id}.renderer.lut.path",
        )
        if Path(relative).suffix.lower() != ".cube":
            raise StyleAssetError(
                f"{style_id} first release only accepts .cube LUT assets"
            )
        if lut.get("input_color_space") != STYLE_WORKING_COLOR_SPACE:
            raise StyleAssetError(
                f"{style_id} LUT input color space must be "
                f"{STYLE_WORKING_COLOR_SPACE!r}"
            )
        _safe_asset_path(relative)
    return renderer


def _verify_style_asset(style: StyleDefinition) -> None:
    asset_path = style.asset_path
    if asset_path is None:
        if style.asset_hash != style.recipe_hash:
            raise StyleAssetError(
                f"{style.key} inline recipe asset hash must equal recipe hash"
            )
        return
    if not asset_path.is_file():
        raise StyleAssetError(
            f"Missing style LUT asset for {style.key}: {asset_path}"
        )
    actual = file_sha256(asset_path)
    if actual != style.asset_hash:
        raise StyleAssetError(
            f"Style LUT asset hash mismatch for {style.key}"
        )


def _safe_asset_path(relative: str) -> Path:
    candidate = (_ASSET_ROOT / relative).resolve()
    try:
        candidate.relative_to(_ASSET_ROOT.resolve())
    except ValueError as exc:
        raise StyleAssetError(
            f"Style asset path escapes the catalog root: {relative!r}"
        ) from exc
    return candidate


def _deduplicate_latest(
    values: Any,
) -> tuple[StyleDefinition, ...]:
    latest: dict[str, StyleDefinition] = {}
    for style in values:
        current = latest.get(style.style_id)
        if current is None or _semver(style.version) > _semver(current.version):
            latest[style.style_id] = style
    return tuple(
        sorted(
            latest.values(),
            key=lambda style: (style.style_id, _semver(style.version)),
        )
    )


def _semver(value: str) -> tuple[int, int, int]:
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise StyleCatalogError(f"Invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _required_text(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise StyleCatalogError(f"{field} must not be empty")
    return normalized


def _string_tuple(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise StyleCatalogError(f"{field} must be a list")
    result = tuple(_required_text(item, field) for item in value)
    if not result and not allow_empty:
        raise StyleCatalogError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise StyleCatalogError(f"{field} contains duplicates")
    return result


def _mapping_copy(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StyleCatalogError(f"{field} must be an object")
    return json.loads(json.dumps(value, ensure_ascii=False))


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise StyleCatalogError(f"{field} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StyleCatalogError(f"{field} must be numeric") from exc
    if not math.isfinite(numeric):
        raise StyleCatalogError(f"{field} must be finite")
    return numeric


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise StyleCatalogError(
            f"Style catalog lock file is missing: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise StyleCatalogError(
            f"Style catalog lock file is invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise StyleCatalogError("Style catalog root must be an object")
    return payload


__all__ = [
    "STYLE_CATALOG_SCHEMA_VERSION",
    "STYLE_FAMILIES",
    "STYLE_RENDERER_VERSION",
    "STYLE_WORKING_COLOR_SPACE",
    "StyleAssetError",
    "StyleCatalogError",
    "StyleDefinition",
    "StyleNotFoundError",
    "StyleRegistry",
    "StyleVersionMismatchError",
    "canonical_recipe_hash",
    "file_sha256",
    "get_style_registry",
    "normalize_style_text",
    "reset_style_registry_cache",
]
