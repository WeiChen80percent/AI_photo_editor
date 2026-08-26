from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from app.services.adaptive_policy import AXIS_POLICIES, AxisPolicy
from app.services.edit_schema import (
    EDIT_PARAMETER_SPECS,
    EDIT_REGIONS,
    MANUAL_PARAMETER_KEYS,
    default_mask_type_for_region,
)
from app.services.render_contract import (
    RenderContractError,
    get_engine_render_contract,
)
from app.services.semantic_normalizer import normalize_semantic_text


SEMANTIC_REGISTRY_VERSION = "semantic_registry_v1"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_ALIAS_ROLES = frozenset({"axis", "positive", "negative"})
_MATCH_KINDS = frozenset({"axis", "action", "descriptor", "observation"})
_MORPH_CLASSES = frozenset(
    {
        "en_progressive_regular",
        "en_progressive_drop_e",
        "en_progressive_double_final",
    }
)
_CONTROLLER_MODES = frozenset({"explicit_axis", "macro"})
_OBJECT_BINDINGS = frozenset(
    {"self_only", "self_or_region", "cross_axis_target"}
)
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


class RegistryValidationError(ValueError):
    pass


def normalize_alias_text(value: object) -> str:
    """Use the exact canonicalization used by the runtime slot extractor."""

    return normalize_semantic_text(str(value)).text


def _normalize_exact_alias_surface(value: object) -> str:
    """Normalize source spelling without folding language variants.

    Semantic matching intentionally folds simplified/traditional variants.
    Controller compatibility occasionally needs to preserve that exact source
    distinction, so this narrower key applies Unicode/case/space
    canonicalization but does not run the semantic variant map.
    """

    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _validate_morph_class(
    value: object | None,
    *,
    language: str,
    owner: str,
) -> str | None:
    if value is None:
        return None
    morph_class = _non_empty(value, f"{owner} morph_class").lower()
    if morph_class not in _MORPH_CLASSES:
        raise RegistryValidationError(
            f"{owner} morph_class must be one of {sorted(_MORPH_CLASSES)}"
        )
    if language != "en":
        raise RegistryValidationError(
            f"{owner} uses an English morph_class outside the en language pack"
        )
    return morph_class


def _normalized_morph_surface_forms(
    text: str,
    morph_class: str | None,
) -> tuple[tuple[str, str], ...]:
    base = normalize_alias_text(text)
    if morph_class is None:
        return ((base, "base"),)
    head, separator, tail = base.partition(" ")
    if morph_class == "en_progressive_regular":
        progressive = f"{head}ing"
    elif morph_class == "en_progressive_drop_e":
        if not head.endswith("e") or head.endswith("ee"):
            raise RegistryValidationError(
                f"{text!r} cannot use en_progressive_drop_e"
            )
        progressive = f"{head[:-1]}ing"
    elif morph_class == "en_progressive_double_final":
        if len(head) < 2 or not head[-1].isalpha():
            raise RegistryValidationError(
                f"{text!r} cannot use en_progressive_double_final"
            )
        progressive = f"{head}{head[-1]}ing"
    else:  # pragma: no cover - protected by registry validation
        raise RegistryValidationError(f"unsupported morph_class {morph_class!r}")
    generated = (
        f"{progressive}{separator}{tail}"
        if separator
        else progressive
    )
    forms = (
        (base, "base"),
        (normalize_alias_text(generated), "progressive"),
    )
    return tuple(dict.fromkeys(forms))


def _normalized_morph_forms(
    text: str,
    morph_class: str | None,
) -> tuple[str, ...]:
    """Compatibility view of morphology without losing runtime provenance."""

    return tuple(
        normalized_text
        for normalized_text, _surface_form_kind
        in _normalized_morph_surface_forms(text, morph_class)
    )


def has_semantic_word_boundaries(text: str, start: int, end: int) -> bool:
    """Require Unicode word boundaries while allowing intentional CJK mixing.

    Natural mixed prompts commonly omit spaces at a Latin/CJK script boundary,
    for example ``darker天空`` or ``天空darker``.  Those transitions remain
    legal.  A Latin alias embedded in a longer Latin/Greek/Cyrillic word does
    not become a semantic match merely because its neighbour is non-ASCII.
    """

    matched = text[start:end]
    if not matched:
        return False
    if (
        _requires_unicode_word_boundary(matched[0])
        and start > 0
        and _is_unicode_word_character(text[start - 1])
        and not _is_cjk_character(text[start - 1])
    ):
        return False
    if (
        _requires_unicode_word_boundary(matched[-1])
        and end < len(text)
        and _is_unicode_word_character(text[end])
        and not _is_cjk_character(text[end])
    ):
        return False
    return True


def _requires_unicode_word_boundary(char: str) -> bool:
    return _is_unicode_word_character(char) and not _is_cjk_character(char)


def _is_unicode_word_character(char: str) -> bool:
    category = unicodedata.category(char)
    return (
        category.startswith("L")
        or category.startswith("M")
        or category.startswith("N")
        or char == "_"
    )


def _is_cjk_character(char: str) -> bool:
    code_point = ord(char)
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x2FA1F
        or 0x3040 <= code_point <= 0x30FF
        or 0xAC00 <= code_point <= 0xD7AF
    )


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise RegistryValidationError(
            f"{field_name} must match {_IDENTIFIER_PATTERN.pattern}: {value!r}"
        )
    return normalized


def _non_empty(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise RegistryValidationError(f"{field_name} must not be empty")
    return normalized


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise RegistryValidationError(f"{field_name} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise RegistryValidationError(f"{field_name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise RegistryValidationError(f"{field_name} must be finite")
    return numeric


@dataclass(frozen=True, slots=True)
class _SharedSlotContract:
    value_type: type[str] | type[int] | type[bool]
    allowed_values: frozenset[str | int | bool]

    def validate(self, *, concept_id: str, value: object) -> None:
        if type(value) is not self.value_type or value not in self.allowed_values:
            allowed = sorted(
                self.allowed_values,
                key=lambda item: (type(item).__name__, repr(item)),
            )
            raise RegistryValidationError(
                f"shared concept {concept_id!r} has invalid value {value!r}; "
                f"expected {self.value_type.__name__} in {allowed!r}"
            )


_SHARED_SLOT_CONTRACTS: Mapping[str, _SharedSlotContract] = MappingProxyType(
    {
        "direction": _SharedSlotContract(int, frozenset({-1, 1})),
        "strength": _SharedSlotContract(
            str,
            frozenset({"subtle", "normal", "strong"}),
        ),
        "negation": _SharedSlotContract(bool, frozenset({True})),
        "operation": _SharedSlotContract(str, frozenset({"reset"})),
        "terminal": _SharedSlotContract(
            str,
            frozenset({"global_reset", "satisfied"}),
        ),
        "conjunction": _SharedSlotContract(
            str,
            frozenset({"and", "but"}),
        ),
        "guard": _SharedSlotContract(
            str,
            frozenset({"or", "exclude", "preserve"}),
        ),
        "relation": _SharedSlotContract(str, frozenset({"continue"})),
        "numeric_relation": _SharedSlotContract(
            str,
            frozenset({"absolute", "relative"}),
        ),
        "observation_modifier": _SharedSlotContract(
            str,
            frozenset({"too", "not_enough", "too_much", "mild"}),
        ),
        "state_link": _SharedSlotContract(bool, frozenset({True})),
        "effect_reference": _SharedSlotContract(bool, frozenset({True})),
        "generic_action": _SharedSlotContract(
            str,
            frozenset({"edit", "return_negative"}),
        ),
        "surface_action": _SharedSlotContract(
            str,
            frozenset({"remove"}),
        ),
        "anaphora": _SharedSlotContract(
            str,
            frozenset({"singular", "plural"}),
        ),
        "spatial_relation": _SharedSlotContract(
            str,
            frozenset({"front_of"}),
        ),
        "mechanism": _SharedSlotContract(str, frozenset({"with"})),
        "negated_comparative": _SharedSlotContract(
            str,
            frozenset({"less"}),
        ),
        "comparison_reference": _SharedSlotContract(
            str,
            frozenset({"degree"}),
        ),
        "scope": _SharedSlotContract(str, frozenset({"region"})),
        "region_context": _SharedSlotContract(str, frozenset({"all"})),
        "existential": _SharedSlotContract(bool, frozenset({True})),
        "clause_aspect": _SharedSlotContract(
            str,
            frozenset({"already", "still", "after"}),
        ),
        "clause_modal": _SharedSlotContract(
            str,
            frozenset({"can", "could", "would"}),
        ),
        "clause_subject": _SharedSlotContract(
            str,
            frozenset(
                {
                    "first_person",
                    "second_person",
                    "third_person",
                }
            ),
        ),
        "request_marker": _SharedSlotContract(bool, frozenset({True})),
        "request_predicate": _SharedSlotContract(
            str,
            frozenset({"desire", "imperative"}),
        ),
        "region_object": _SharedSlotContract(
            str,
            frozenset({"person"}),
        ),
        "region_support": _SharedSlotContract(
            str,
            frozenset({"generic", "subject"}),
        ),
        "region_constraint": _SharedSlotContract(
            str,
            frozenset({"person"}),
        ),
        "region_anaphora": _SharedSlotContract(
            str,
            frozenset({"singular", "plural"}),
        ),
        "semantic_attribute": _SharedSlotContract(
            str,
            frozenset({"tone", "detail", "parameter", "quality"}),
        ),
        "scope_quantifier": _SharedSlotContract(
            str,
            frozenset({"distributive"}),
        ),
        "compound_marker": _SharedSlotContract(
            str,
            frozenset({"together"}),
        ),
        "return_relation": _SharedSlotContract(
            str,
            frozenset({"return"}),
        ),
        "function_word": _SharedSlotContract(
            str,
            frozenset({"demonstrative", "determiner", "possessive"}),
        ),
        "numeric_unit": _SharedSlotContract(
            str,
            frozenset({"percent"}),
        ),
        "noise": _SharedSlotContract(bool, frozenset({True})),
    }
)


@dataclass(frozen=True, slots=True)
class AxisSchema:
    axis_id: str
    label: str
    group: str
    minimum: float
    maximum: float
    step: float
    neutral: float
    unit: str
    default_visible: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "axis_id",
            _identifier(self.axis_id, "schema axis_id"),
        )
        object.__setattr__(self, "label", _non_empty(self.label, "schema label"))
        object.__setattr__(self, "group", _non_empty(self.group, "schema group"))
        minimum = _finite(self.minimum, "schema minimum")
        maximum = _finite(self.maximum, "schema maximum")
        step = _finite(self.step, "schema step")
        neutral = _finite(self.neutral, "schema neutral")
        if minimum >= maximum:
            raise RegistryValidationError(
                f"schema minimum must be below maximum for {self.axis_id}"
            )
        if step <= 0:
            raise RegistryValidationError(
                f"schema step must be positive for {self.axis_id}"
            )
        if not minimum <= neutral <= maximum:
            raise RegistryValidationError(
                f"schema neutral must be in range for {self.axis_id}"
            )
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "neutral", neutral)
        object.__setattr__(self, "unit", str(self.unit))
        object.__setattr__(self, "default_visible", bool(self.default_visible))

    @classmethod
    def from_parameter_spec(
        cls,
        axis_id: str,
        spec: Mapping[str, object],
    ) -> AxisSchema:
        required = {
            "label",
            "group",
            "minimum",
            "maximum",
            "step",
            "neutral",
            "unit",
            "default_visible",
        }
        missing = required.difference(spec)
        if missing:
            raise RegistryValidationError(
                f"schema for {axis_id!r} is missing fields: {sorted(missing)}"
            )
        return cls(
            axis_id=axis_id,
            label=str(spec["label"]),
            group=str(spec["group"]),
            minimum=spec["minimum"],
            maximum=spec["maximum"],
            step=spec["step"],
            neutral=spec["neutral"],
            unit=str(spec["unit"]),
            default_visible=bool(spec["default_visible"]),
        )


@dataclass(frozen=True, slots=True)
class AxisAlias:
    text: str
    language: str
    role: str = "axis"
    match_kind: str | None = None
    requested_direction: int | None = None
    direction_multiplier: int = 1
    implies_change: bool = False
    morph_class: str | None = None
    controller_mode: str | None = None
    object_binding: str | None = None

    def __post_init__(self) -> None:
        text = _non_empty(self.text, "axis alias text")
        language = _non_empty(self.language, "axis alias language").lower()
        role = _non_empty(self.role, "axis alias role").lower()
        if role not in _ALIAS_ROLES:
            raise RegistryValidationError(
                f"axis alias role must be one of {sorted(_ALIAS_ROLES)}"
            )
        match_kind = (
            str(self.match_kind).strip().lower()
            if self.match_kind is not None
            else ("axis" if role == "axis" else "descriptor")
        )
        if match_kind not in _MATCH_KINDS:
            raise RegistryValidationError(
                f"axis alias match_kind must be one of {sorted(_MATCH_KINDS)}"
            )
        requested_direction = self.requested_direction
        implied_direction = {"positive": 1, "negative": -1}.get(role)
        if requested_direction is None:
            requested_direction = implied_direction
        if requested_direction not in {None, -1, 1}:
            raise RegistryValidationError(
                "axis alias requested_direction must be None, -1, or 1"
            )
        if role == "axis" and requested_direction is not None:
            raise RegistryValidationError(
                "role='axis' aliases cannot request a direction"
            )
        if implied_direction is not None and requested_direction != implied_direction:
            raise RegistryValidationError(
                "axis alias role and requested_direction disagree"
            )
        direction_multiplier = self.direction_multiplier
        if (
            isinstance(direction_multiplier, bool)
            or not isinstance(direction_multiplier, int)
            or direction_multiplier not in {-1, 1}
        ):
            raise RegistryValidationError(
                "axis alias direction_multiplier must be the integer -1 or 1"
            )
        if role != "axis" and direction_multiplier != 1:
            raise RegistryValidationError(
                "only role='axis' aliases may invert the surface direction"
            )
        if not isinstance(self.implies_change, bool):
            raise RegistryValidationError(
                "axis alias implies_change must be a boolean"
            )
        if self.implies_change and (
            role == "axis"
            or requested_direction not in {-1, 1}
            or match_kind not in {"action", "descriptor"}
        ):
            raise RegistryValidationError(
                "implies_change aliases need a directional action or descriptor"
            )
        morph_class = _validate_morph_class(
            self.morph_class,
            language=language,
            owner=f"axis alias {text!r}",
        )
        if match_kind == "action" and self.controller_mode is None:
            raise RegistryValidationError(
                f"action axis alias {text!r} must declare controller_mode"
            )
        controller_mode = (
            "macro"
            if self.controller_mode is None
            else _non_empty(
                self.controller_mode,
                f"axis alias {text!r} controller_mode",
            ).lower()
        )
        if controller_mode not in _CONTROLLER_MODES:
            raise RegistryValidationError(
                "axis alias controller_mode must be explicit_axis or macro"
            )
        if match_kind == "action" and self.object_binding is None:
            raise RegistryValidationError(
                f"action axis alias {text!r} must declare object_binding"
            )
        object_binding = (
            "self_only"
            if self.object_binding is None
            else _non_empty(
                self.object_binding,
                f"axis alias {text!r} object_binding",
            ).lower()
        )
        if object_binding not in _OBJECT_BINDINGS:
            raise RegistryValidationError(
                "axis alias object_binding must be self_only, "
                "self_or_region, or cross_axis_target"
            )
        if object_binding != "self_only" and match_kind != "action":
            raise RegistryValidationError(
                "non-default object_binding requires match_kind='action'"
            )
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "match_kind", match_kind)
        object.__setattr__(self, "requested_direction", requested_direction)
        object.__setattr__(self, "morph_class", morph_class)
        object.__setattr__(self, "controller_mode", controller_mode)
        object.__setattr__(self, "object_binding", object_binding)
        object.__setattr__(
            self,
            "direction_multiplier",
            direction_multiplier,
        )

    @property
    def normalized_text(self) -> str:
        return normalize_alias_text(self.text)

    @property
    def normalized_forms(self) -> tuple[str, ...]:
        return _normalized_morph_forms(self.text, self.morph_class)

    @property
    def normalized_surface_forms(self) -> tuple[tuple[str, str], ...]:
        return _normalized_morph_surface_forms(self.text, self.morph_class)


@dataclass(frozen=True, slots=True)
class AxisTestSeed:
    text: str
    language: str
    expected_direction: int
    expected_strength: str = "normal"

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _non_empty(self.text, "test seed text"))
        object.__setattr__(
            self,
            "language",
            _non_empty(self.language, "test seed language").lower(),
        )
        if self.expected_direction not in {-1, 1}:
            raise RegistryValidationError(
                "test seed expected_direction must be -1 or 1"
            )
        strength = _non_empty(
            self.expected_strength,
            "test seed expected_strength",
        ).lower()
        if strength not in {"subtle", "normal", "strong"}:
            raise RegistryValidationError(
                "test seed expected_strength must be subtle, normal, or strong"
            )
        object.__setattr__(self, "expected_strength", strength)


@dataclass(frozen=True, slots=True)
class RenderCapability:
    engine: str
    parameter_key: str
    regions: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "engine", _identifier(self.engine, "engine"))
        object.__setattr__(
            self,
            "parameter_key",
            _identifier(self.parameter_key, "render parameter_key"),
        )
        regions = frozenset(
            _identifier(region, "render region") for region in self.regions
        )
        if not regions:
            raise RegistryValidationError(
                f"render capability for {self.parameter_key} needs a region"
            )
        object.__setattr__(self, "regions", regions)


@dataclass(frozen=True, slots=True)
class AliasControllerSurfaceOverride:
    """Exact-source compatibility override for one semantic alias.

    Semantic matching may fold spelling variants (for example simplified and
    traditional Chinese), while compatibility behavior occasionally needs to
    distinguish the source spelling.  This object intentionally stores only a
    partial controller contract; missing fields inherit from the owning
    ``AliasControllerContract``.
    """

    surface: str
    mode: str | None = None
    companions: bool | None = None
    default_strength: str | None = None
    relation: str | None = None

    def __post_init__(self) -> None:
        surface = _normalize_exact_alias_surface(
            _non_empty(self.surface, "controller override surface")
        )
        mode = _normalized_controller_mode(self.mode)
        companions = _validated_controller_companions(self.companions)
        default_strength = _normalized_controller_strength(
            self.default_strength
        )
        relation = _normalized_controller_relation(self.relation)
        if all(
            value is None
            for value in (
                mode,
                companions,
                default_strength,
                relation,
            )
        ):
            raise RegistryValidationError(
                "controller surface override must override at least one field"
            )
        object.__setattr__(self, "surface", surface)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "companions", companions)
        object.__setattr__(
            self,
            "default_strength",
            default_strength,
        )
        object.__setattr__(self, "relation", relation)


@dataclass(frozen=True, slots=True)
class AliasControllerContract:
    """Immutable compatibility metadata attached to one concept alias."""

    mode: str | None = None
    companions: bool | None = None
    default_strength: str | None = None
    relation: str | None = None
    source_overrides: tuple[AliasControllerSurfaceOverride, ...] = ()

    def __post_init__(self) -> None:
        mode = _normalized_controller_mode(self.mode)
        companions = _validated_controller_companions(self.companions)
        default_strength = _normalized_controller_strength(
            self.default_strength
        )
        relation = _normalized_controller_relation(self.relation)
        overrides = tuple(self.source_overrides)
        if any(
            not isinstance(item, AliasControllerSurfaceOverride)
            for item in overrides
        ):
            raise RegistryValidationError(
                "controller source_overrides must contain "
                "AliasControllerSurfaceOverride values"
            )
        surfaces = tuple(item.surface for item in overrides)
        if len(surfaces) != len(set(surfaces)):
            raise RegistryValidationError(
                "duplicate controller source override surface"
            )
        if all(
            value is None
            for value in (
                mode,
                companions,
                default_strength,
                relation,
            )
        ) and not overrides:
            raise RegistryValidationError(
                "controller contract must define at least one field or override"
            )
        _validate_resolved_controller_contract(
            mode=mode,
            companions=companions,
            relation=relation,
        )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "companions", companions)
        object.__setattr__(
            self,
            "default_strength",
            default_strength,
        )
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "source_overrides", overrides)

    def for_surface(
        self,
        raw_text: str,
    ) -> AliasControllerContract | None:
        """Resolve at most one exact-source override into a flat contract."""

        exact_surface = _normalize_exact_alias_surface(raw_text)
        override = next(
            (
                item
                for item in self.source_overrides
                if item.surface == exact_surface
            ),
            None,
        )
        if override is None:
            if not self.source_overrides:
                return self
            if all(
                value is None
                for value in (
                    self.mode,
                    self.companions,
                    self.default_strength,
                    self.relation,
                )
            ):
                return None
            return AliasControllerContract(
                mode=self.mode,
                companions=self.companions,
                default_strength=self.default_strength,
                relation=self.relation,
            )
        return AliasControllerContract(
            mode=(
                override.mode
                if override.mode is not None
                else self.mode
            ),
            companions=(
                override.companions
                if override.companions is not None
                else self.companions
            ),
            default_strength=(
                override.default_strength
                if override.default_strength is not None
                else self.default_strength
            ),
            relation=(
                override.relation
                if override.relation is not None
                else self.relation
            ),
        )


def _normalized_controller_mode(value: object | None) -> str | None:
    if value is None:
        return None
    mode = _non_empty(value, "controller mode").lower()
    if mode not in _CONTROLLER_MODES:
        raise RegistryValidationError(
            "controller mode must be explicit_axis or macro"
        )
    return mode


def _validated_controller_companions(
    value: object | None,
) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise RegistryValidationError(
            "controller companions must be a boolean"
        )
    return value


def _normalized_controller_strength(
    value: object | None,
) -> str | None:
    if value is None:
        return None
    strength = _non_empty(value, "controller default_strength").lower()
    if strength not in {"subtle", "normal", "strong"}:
        raise RegistryValidationError(
            "controller default_strength must be subtle, normal, or strong"
        )
    return strength


def _normalized_controller_relation(
    value: object | None,
) -> str | None:
    if value is None:
        return None
    relation = _non_empty(value, "controller relation").lower()
    if relation not in {"initial", "correct"}:
        raise RegistryValidationError(
            "controller relation must be initial or correct"
        )
    return relation


def _validate_resolved_controller_contract(
    *,
    mode: str | None,
    companions: bool | None,
    relation: str | None,
) -> None:
    if companions is True and mode == "explicit_axis":
        raise RegistryValidationError(
            "explicit_axis controller contracts cannot enable companions"
        )
    if relation == "initial" and mode == "explicit_axis":
        raise RegistryValidationError(
            "explicit_axis controller contracts cannot declare initial relation"
        )


@dataclass(frozen=True, slots=True)
class ConceptAlias:
    text: str
    language: str
    morph_class: str | None = None
    allow_same_id_axis_region_polysemy: bool = False
    controller_contract: AliasControllerContract | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "text",
            _non_empty(self.text, "concept alias text"),
        )
        object.__setattr__(
            self,
            "language",
            _non_empty(self.language, "concept alias language").lower(),
        )
        object.__setattr__(
            self,
            "morph_class",
            _validate_morph_class(
                self.morph_class,
                language=self.language,
                owner=f"concept alias {self.text!r}",
            ),
        )
        if not isinstance(
            self.allow_same_id_axis_region_polysemy,
            bool,
        ):
            raise RegistryValidationError(
                "axis/region polysemy contract must be a boolean"
            )
        controller_contract = self.controller_contract
        if (
            controller_contract is not None
            and not isinstance(
                controller_contract,
                AliasControllerContract,
            )
        ):
            raise RegistryValidationError(
                "controller_contract must be an AliasControllerContract"
            )
        if controller_contract is not None:
            normalized_forms = set(
                _normalized_morph_forms(self.text, self.morph_class)
            )
            for override in controller_contract.source_overrides:
                if normalize_alias_text(override.surface) not in normalized_forms:
                    raise RegistryValidationError(
                        "controller source override must canonicalize to its "
                        "own alias"
                    )
            controller_contract = AliasControllerContract(
                mode=controller_contract.mode,
                companions=controller_contract.companions,
                default_strength=controller_contract.default_strength,
                relation=controller_contract.relation,
                source_overrides=controller_contract.source_overrides,
            )
        object.__setattr__(
            self,
            "controller_contract",
            controller_contract,
        )

    @property
    def normalized_text(self) -> str:
        return normalize_alias_text(self.text)

    @property
    def normalized_forms(self) -> tuple[str, ...]:
        return _normalized_morph_forms(self.text, self.morph_class)

    @property
    def normalized_surface_forms(self) -> tuple[tuple[str, str], ...]:
        return _normalized_morph_surface_forms(self.text, self.morph_class)

    def controller_contract_for(
        self,
        raw_text: str,
    ) -> AliasControllerContract | None:
        if self.controller_contract is None:
            return None
        return self.controller_contract.for_surface(raw_text)


@dataclass(frozen=True, slots=True)
class EffectStateAlias:
    """One lexical state on a registry-defined visual-effect dimension."""

    text: str
    language: str
    state_direction: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "text",
            _non_empty(self.text, "effect-state alias text"),
        )
        object.__setattr__(
            self,
            "language",
            _non_empty(
                self.language,
                "effect-state alias language",
            ).lower(),
        )
        if (
            isinstance(self.state_direction, bool)
            or not isinstance(self.state_direction, int)
            or self.state_direction not in {-1, 1}
        ):
            raise RegistryValidationError(
                "effect-state alias direction must be the integer -1 or 1"
            )

    @property
    def normalized_text(self) -> str:
        return normalize_alias_text(self.text)


@dataclass(frozen=True, slots=True)
class RegionDefinition:
    region_id: str
    mask_type: str
    aliases: tuple[ConceptAlias, ...]
    attribute_axis_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "region_id",
            _identifier(self.region_id, "region_id"),
        )
        object.__setattr__(
            self,
            "mask_type",
            _identifier(self.mask_type, "mask_type"),
        )
        aliases = tuple(self.aliases)
        if not aliases:
            raise RegistryValidationError(
                f"region {self.region_id!r} needs at least one alias"
            )
        if any(alias.controller_contract is not None for alias in aliases):
            raise RegistryValidationError(
                "controller contracts are not valid on region aliases"
            )
        _validate_local_aliases(aliases, f"region {self.region_id!r}")
        object.__setattr__(self, "aliases", aliases)
        attribute_axis_ids = tuple(
            _identifier(axis_id, "region attribute axis_id")
            for axis_id in self.attribute_axis_ids
        )
        if len(attribute_axis_ids) != len(set(attribute_axis_ids)):
            raise RegistryValidationError(
                f"region {self.region_id!r} has duplicate attribute axes"
            )
        object.__setattr__(
            self,
            "attribute_axis_ids",
            attribute_axis_ids,
        )


@dataclass(frozen=True, slots=True)
class SharedConceptDefinition:
    concept_id: str
    slot: str
    value: str | int | float | bool
    aliases: tuple[ConceptAlias, ...]
    preposed_strength: str | None = None
    leading_axis_observation: bool = False
    observation_strength: str | None = None

    def __post_init__(self) -> None:
        concept_id = _identifier(self.concept_id, "shared concept_id")
        slot = _identifier(self.slot, "shared slot")
        object.__setattr__(self, "concept_id", concept_id)
        object.__setattr__(self, "slot", slot)
        if not isinstance(self.value, (str, int, float, bool)):
            raise RegistryValidationError(
                f"shared concept {concept_id!r} has a mutable value"
            )
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise RegistryValidationError(
                f"shared concept {concept_id!r} value must be finite"
            )
        contract = _SHARED_SLOT_CONTRACTS.get(slot)
        if contract is None:
            raise RegistryValidationError(
                f"shared concept {concept_id!r} uses unsupported shared slot "
                f"{slot!r}"
            )
        contract.validate(concept_id=concept_id, value=self.value)
        aliases = tuple(self.aliases)
        if not aliases:
            raise RegistryValidationError(
                f"shared concept {concept_id!r} needs an alias"
            )
        if any(
            alias.allow_same_id_axis_region_polysemy
            for alias in aliases
        ):
            raise RegistryValidationError(
                "axis/region polysemy contract is valid only on region aliases"
            )
        for alias in aliases:
            _validate_shared_alias_controller_contract(
                slot=slot,
                alias=alias,
            )
        _validate_local_aliases(aliases, f"shared concept {concept_id!r}")
        object.__setattr__(self, "aliases", aliases)
        preposed_strength = (
            str(self.preposed_strength).strip().lower()
            if self.preposed_strength is not None
            else None
        )
        if preposed_strength is not None:
            if (
                slot != "direction"
                or self.value != 1
                or preposed_strength
                not in {"subtle", "normal", "strong"}
            ):
                raise RegistryValidationError(
                    "preposed_strength requires a positive direction "
                    "concept and a supported strength value"
                )
        object.__setattr__(
            self,
            "preposed_strength",
            preposed_strength,
        )
        if not isinstance(self.leading_axis_observation, bool):
            raise RegistryValidationError(
                "leading_axis_observation must be a boolean"
            )
        if self.leading_axis_observation and slot != "conjunction":
            raise RegistryValidationError(
                "leading_axis_observation requires a conjunction slot"
            )
        observation_strength = (
            str(self.observation_strength).strip().lower()
            if self.observation_strength is not None
            else None
        )
        if observation_strength is not None:
            if (
                observation_strength
                not in {"subtle", "normal", "strong"}
                or slot
                not in {
                    "conjunction",
                    "clause_aspect",
                    "observation_modifier",
                }
                or (
                    slot == "conjunction"
                    and not self.leading_axis_observation
                )
            ):
                raise RegistryValidationError(
                    "observation_strength requires a supported observation "
                    "strength concept"
                )
        object.__setattr__(
            self,
            "observation_strength",
            observation_strength,
        )


def _validate_shared_alias_controller_contract(
    *,
    slot: str,
    alias: ConceptAlias,
) -> None:
    controller_contract = alias.controller_contract
    if controller_contract is None:
        return
    allowed_fields_by_slot = {
        "direction": frozenset({"mode"}),
        "strength": frozenset({"default_strength"}),
        "observation_modifier": frozenset({"default_strength"}),
        "negated_comparative": frozenset(
            {
                "mode",
                "companions",
                "default_strength",
                "relation",
            }
        ),
    }
    allowed_fields = allowed_fields_by_slot.get(slot)
    if allowed_fields is None:
        raise RegistryValidationError(
            f"controller contract is not supported on shared slot {slot!r}"
        )

    contracts = [controller_contract]
    contracts.extend(
        resolved_contract
        for override in controller_contract.source_overrides
        if (
            resolved_contract := controller_contract.for_surface(
                override.surface
            )
        )
        is not None
    )
    for contract in contracts:
        populated_fields = {
            field_name
            for field_name in (
                "mode",
                "companions",
                "default_strength",
                "relation",
            )
            if getattr(contract, field_name) is not None
        }
        unsupported = populated_fields.difference(allowed_fields)
        if unsupported:
            raise RegistryValidationError(
                f"controller contract on shared slot {slot!r} uses "
                f"unsupported fields {sorted(unsupported)!r}"
            )
        if contract.companions is True and contract.mode != "macro":
            raise RegistryValidationError(
                "enabled controller companions require macro mode"
            )
        if (
            contract.relation == "initial"
            and contract.mode != "macro"
        ):
            raise RegistryValidationError(
                "initial controller relation requires macro mode"
            )


@dataclass(frozen=True, slots=True)
class EffectDimensionDefinition:
    """A named observable effect that one or more edit axes can produce."""

    effect_id: str
    aliases: tuple[EffectStateAlias, ...] = ()

    def __post_init__(self) -> None:
        effect_id = _identifier(self.effect_id, "effect_id")
        aliases = tuple(self.aliases)
        _validate_local_aliases(
            aliases,
            f"effect dimension {effect_id!r}",
        )
        object.__setattr__(self, "effect_id", effect_id)
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True, slots=True)
class AxisEffectBinding:
    """Polarity of one observable effect under a positive axis change."""

    effect_id: str
    direction_multiplier: int
    canonical: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "effect_id",
            _identifier(self.effect_id, "axis effect_id"),
        )
        if (
            isinstance(self.direction_multiplier, bool)
            or not isinstance(self.direction_multiplier, int)
            or self.direction_multiplier not in {-1, 1}
        ):
            raise RegistryValidationError(
                "axis effect direction_multiplier must be the integer -1 or 1"
            )
        if not isinstance(self.canonical, bool):
            raise RegistryValidationError(
                "axis effect canonical marker must be a boolean"
            )


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    axis_id: str
    schema: AxisSchema
    policy: AxisPolicy
    render_capabilities: tuple[RenderCapability, ...]
    aliases: tuple[AxisAlias, ...]
    test_seeds: tuple[AxisTestSeed, ...]
    effect_bindings: tuple[AxisEffectBinding, ...] = ()

    def __post_init__(self) -> None:
        axis_id = _identifier(self.axis_id, "axis_id")
        object.__setattr__(self, "axis_id", axis_id)
        if self.schema.axis_id != axis_id:
            raise RegistryValidationError(
                f"schema axis {self.schema.axis_id!r} does not match {axis_id!r}"
            )
        if not isinstance(self.policy, AxisPolicy):
            raise RegistryValidationError(
                f"policy for {axis_id!r} must be an AxisPolicy"
            )
        policy = _freeze_policy(self.policy)
        object.__setattr__(self, "policy", policy)
        if policy.axis != axis_id:
            raise RegistryValidationError(
                f"policy axis {policy.axis!r} does not match {axis_id!r}"
            )
        _validate_policy_schema_parity(axis_id, self.schema, policy)

        capabilities = tuple(self.render_capabilities)
        if not capabilities:
            raise RegistryValidationError(
                f"axis {axis_id!r} needs at least one render capability"
            )
        engines: set[str] = set()
        for capability in capabilities:
            if capability.engine in engines:
                raise RegistryValidationError(
                    f"axis {axis_id!r} has duplicate render engine "
                    f"{capability.engine!r}"
                )
            engines.add(capability.engine)
        object.__setattr__(self, "render_capabilities", capabilities)

        aliases = tuple(self.aliases)
        if not aliases:
            raise RegistryValidationError(
                f"axis {axis_id!r} needs at least one alias"
            )
        _validate_local_aliases(aliases, f"axis {axis_id!r}")
        if not any(alias.role == "axis" for alias in aliases):
            raise RegistryValidationError(
                f"axis {axis_id!r} needs at least one role='axis' alias"
            )
        object.__setattr__(self, "aliases", aliases)

        test_seeds = tuple(self.test_seeds)
        if not test_seeds:
            raise RegistryValidationError(
                f"axis {axis_id!r} needs at least one test seed"
            )
        object.__setattr__(self, "test_seeds", test_seeds)

        effect_bindings = tuple(self.effect_bindings)
        effect_ids = [binding.effect_id for binding in effect_bindings]
        if len(effect_ids) != len(set(effect_ids)):
            raise RegistryValidationError(
                f"axis {axis_id!r} has duplicate effect bindings"
            )
        canonical_effects = [
            binding.effect_id
            for binding in effect_bindings
            if binding.canonical
        ]
        if len(canonical_effects) > 1:
            raise RegistryValidationError(
                f"axis {axis_id!r} has more than one canonical effect binding"
            )
        object.__setattr__(self, "effect_bindings", effect_bindings)


@dataclass(frozen=True, slots=True)
class AxisAliasBinding:
    axis_id: str
    role: str
    match_kind: str
    requested_direction: int | None
    direction_multiplier: int
    implies_change: bool
    controller_mode: str
    object_binding: str
    surface_form_kind: str
    alias: AxisAlias


@dataclass(frozen=True, slots=True)
class EffectStateBinding:
    effect_id: str
    state_direction: int
    alias: EffectStateAlias


def _validate_local_aliases(
    aliases: Sequence[AxisAlias | ConceptAlias | EffectStateAlias],
    owner: str,
) -> None:
    seen: set[tuple[str, str]] = set()
    for alias in aliases:
        normalized_forms = getattr(
            alias,
            "normalized_forms",
            (alias.normalized_text,),
        )
        for normalized_text in normalized_forms:
            key = (alias.language, normalized_text)
            if key in seen:
                raise RegistryValidationError(
                    f"duplicate alias form {normalized_text!r} in {owner}"
                )
            seen.add(key)


def _validate_policy_schema_parity(
    axis_id: str,
    schema: AxisSchema,
    policy: AxisPolicy,
) -> None:
    pairs = {
        "minimum": (schema.minimum, policy.minimum),
        "maximum": (schema.maximum, policy.maximum),
        "neutral": (schema.neutral, policy.neutral),
        "step/quantum": (schema.step, policy.quantum),
    }
    for field_name, (schema_value, policy_value) in pairs.items():
        if not math.isclose(schema_value, policy_value, abs_tol=1e-12):
            raise RegistryValidationError(
                f"{axis_id!r} {field_name} differs between schema and policy"
            )


def _freeze_policy(policy: AxisPolicy) -> AxisPolicy:
    if isinstance(policy.positive_seeds, _MAPPING_PROXY_TYPE) and isinstance(
        policy.negative_seeds,
        _MAPPING_PROXY_TYPE,
    ):
        return policy
    return AxisPolicy(
        axis=policy.axis,
        label=policy.label,
        unit=policy.unit,
        family=policy.family,
        transform=policy.transform,
        neutral=policy.neutral,
        minimum=policy.minimum,
        maximum=policy.maximum,
        quantum=policy.quantum,
        positive_intent=policy.positive_intent,
        negative_intent=policy.negative_intent,
        positive_seeds=MappingProxyType(
            {
                key: float(value)
                for key, value in policy.positive_seeds.items()
            }
        ),
        negative_seeds=MappingProxyType(
            {
                key: float(value)
                for key, value in policy.negative_seeds.items()
            }
        ),
        minimum_active=policy.minimum_active,
        minimum_visible_step=policy.minimum_visible_step,
        policy_version=policy.policy_version,
    )


@dataclass(frozen=True, slots=True)
class _RuntimeAliasBinding:
    normalized_text: str
    raw_text: str
    language: str
    namespace: str
    concept_id: str
    semantic_key: tuple[object, ...]
    axis_role: str | None = None
    match_kind: str | None = None
    requested_direction: int | None = None
    surface_form_kind: str = "base"
    allow_same_id_axis_region_polysemy: bool = False

    @property
    def description(self) -> str:
        return (
            f"{self.namespace}:{self.concept_id}[{self.language}]"
        )


def _validate_runtime_alias_collisions(
    *,
    axes: Iterable[AxisDefinition],
    regions: Iterable[RegionDefinition],
    shared_concepts: Iterable[SharedConceptDefinition],
    effect_dimensions: Iterable[EffectDimensionDefinition],
) -> None:
    """Audit aliases exactly as the runtime extractor sees them.

    Runtime extraction scans every language pack at once.  Language tags are
    provenance, not an isolated matching namespace, so equal canonical text in
    different packs may coexist only when it has the same semantic binding.
    Axis/region dual roles with the same canonical id remain intentionally
    ambiguous (for example highlights and shadows).
    """

    by_text: dict[str, list[_RuntimeAliasBinding]] = {}
    for definition in axes:
        for alias in definition.aliases:
            for (
                normalized_text,
                surface_form_kind,
            ) in alias.normalized_surface_forms:
                binding = _RuntimeAliasBinding(
                    normalized_text=normalized_text,
                    raw_text=alias.text,
                    language=alias.language,
                    namespace="axis",
                    concept_id=definition.axis_id,
                    semantic_key=(
                        "axis",
                        definition.axis_id,
                        alias.role,
                        alias.match_kind,
                        alias.requested_direction,
                        alias.direction_multiplier,
                        alias.implies_change,
                        alias.controller_mode,
                        alias.object_binding,
                        surface_form_kind,
                    ),
                    axis_role=alias.role,
                    match_kind=alias.match_kind,
                    requested_direction=alias.requested_direction,
                    surface_form_kind=surface_form_kind,
                )
                by_text.setdefault(binding.normalized_text, []).append(binding)

    for definition in regions:
        for alias in definition.aliases:
            binding = _RuntimeAliasBinding(
                normalized_text=alias.normalized_text,
                raw_text=alias.text,
                language=alias.language,
                namespace="region",
                concept_id=definition.region_id,
                semantic_key=(
                    "region",
                    definition.region_id,
                    definition.mask_type,
                ),
                allow_same_id_axis_region_polysemy=(
                    alias.allow_same_id_axis_region_polysemy
                ),
            )
            by_text.setdefault(binding.normalized_text, []).append(binding)

    for definition in shared_concepts:
        for alias in definition.aliases:
            for (
                normalized_text,
                surface_form_kind,
            ) in alias.normalized_surface_forms:
                binding = _RuntimeAliasBinding(
                    normalized_text=normalized_text,
                    raw_text=alias.text,
                    language=alias.language,
                    namespace="shared",
                    concept_id=definition.concept_id,
                    semantic_key=(
                        "shared",
                        definition.concept_id,
                        definition.slot,
                        type(definition.value).__name__,
                        definition.value,
                        definition.preposed_strength,
                        definition.leading_axis_observation,
                        definition.observation_strength,
                        alias.controller_contract,
                        surface_form_kind,
                    ),
                    surface_form_kind=surface_form_kind,
                )
                by_text.setdefault(binding.normalized_text, []).append(binding)

    for definition in effect_dimensions:
        for alias in definition.aliases:
            binding = _RuntimeAliasBinding(
                normalized_text=alias.normalized_text,
                raw_text=alias.text,
                language=alias.language,
                namespace="effect",
                concept_id=definition.effect_id,
                semantic_key=(
                    "effect",
                    definition.effect_id,
                    alias.state_direction,
                ),
                requested_direction=alias.state_direction,
            )
            by_text.setdefault(binding.normalized_text, []).append(binding)

    for normalized_text, bindings in by_text.items():
        for index, left in enumerate(bindings):
            for right in bindings[index + 1 :]:
                if left.semantic_key == right.semantic_key:
                    continue
                if _is_allowed_axis_region_polysemy(left, right):
                    continue
                collision_scope = (
                    "same-language"
                    if left.language == right.language
                    else "cross-language"
                )
                raise RegistryValidationError(
                    f"{collision_scope} runtime alias collision for "
                    f"{normalized_text!r}: {left.description} "
                    f"({left.raw_text!r}) vs {right.description} "
                    f"({right.raw_text!r})"
                )


def _is_allowed_axis_region_polysemy(
    left: _RuntimeAliasBinding,
    right: _RuntimeAliasBinding,
) -> bool:
    pair = {left.namespace, right.namespace}
    if pair != {"axis", "region"}:
        return False
    axis = left if left.namespace == "axis" else right
    region = left if left.namespace == "region" else right
    return (
        axis.concept_id == region.concept_id
        and axis.axis_role == "axis"
        and axis.match_kind == "axis"
        and axis.requested_direction is None
        and region.allow_same_id_axis_region_polysemy
    )


@dataclass(frozen=True, slots=True, init=False)
class ParameterRegistry:
    registry_version: str
    _axes: Mapping[str, AxisDefinition]
    _axis_aliases: Mapping[tuple[str, str], AxisAliasBinding]
    _regions: Mapping[str, RegionDefinition]
    _region_aliases: Mapping[tuple[str, str], RegionDefinition]
    _shared_concepts: Mapping[str, SharedConceptDefinition]
    _shared_aliases: Mapping[
        tuple[str, str, str],
        SharedConceptDefinition,
    ]
    _effect_dimensions: Mapping[str, EffectDimensionDefinition]
    _effect_aliases: Mapping[
        tuple[str, str],
        EffectStateBinding,
    ]

    def __init__(
        self,
        axes: Iterable[AxisDefinition],
        *,
        regions: Iterable[RegionDefinition] = (),
        shared_concepts: Iterable[SharedConceptDefinition] = (),
        effect_dimensions: Iterable[EffectDimensionDefinition] = (),
        registry_version: str = SEMANTIC_REGISTRY_VERSION,
    ) -> None:
        version = _non_empty(registry_version, "registry_version")
        axis_map: dict[str, AxisDefinition] = {}
        axis_aliases: dict[tuple[str, str], AxisAliasBinding] = {}
        for definition in axes:
            if definition.axis_id in axis_map:
                raise RegistryValidationError(
                    f"duplicate axis id {definition.axis_id!r}"
                )
            axis_map[definition.axis_id] = definition
            for alias in definition.aliases:
                for (
                    normalized_text,
                    surface_form_kind,
                ) in alias.normalized_surface_forms:
                    key = (alias.language, normalized_text)
                    if key in axis_aliases:
                        previous = axis_aliases[key]
                        raise RegistryValidationError(
                            f"axis alias collision for {normalized_text!r}: "
                            f"{previous.axis_id!r} vs {definition.axis_id!r}"
                        )
                    axis_aliases[key] = AxisAliasBinding(
                        axis_id=definition.axis_id,
                        role=alias.role,
                        match_kind=alias.match_kind,
                        requested_direction=alias.requested_direction,
                        direction_multiplier=alias.direction_multiplier,
                        implies_change=alias.implies_change,
                        controller_mode=alias.controller_mode,
                        object_binding=alias.object_binding,
                        surface_form_kind=surface_form_kind,
                        alias=alias,
                    )
        if not axis_map:
            raise RegistryValidationError("registry needs at least one axis")

        region_map: dict[str, RegionDefinition] = {}
        region_aliases: dict[tuple[str, str], RegionDefinition] = {}
        for definition in regions:
            if definition.region_id in region_map:
                raise RegistryValidationError(
                    f"duplicate region id {definition.region_id!r}"
                )
            region_map[definition.region_id] = definition
            for alias in definition.aliases:
                key = (alias.language, alias.normalized_text)
                if key in region_aliases:
                    previous = region_aliases[key]
                    raise RegistryValidationError(
                        f"region alias collision for {alias.text!r}: "
                        f"{previous.region_id!r} vs {definition.region_id!r}"
                    )
                region_aliases[key] = definition

        shared_map: dict[str, SharedConceptDefinition] = {}
        shared_aliases: dict[
            tuple[str, str, str],
            SharedConceptDefinition,
        ] = {}
        for definition in shared_concepts:
            if definition.concept_id in shared_map:
                raise RegistryValidationError(
                    f"duplicate shared concept id {definition.concept_id!r}"
                )
            shared_map[definition.concept_id] = definition
            for alias in definition.aliases:
                for normalized_text in alias.normalized_forms:
                    key = (
                        definition.slot,
                        alias.language,
                        normalized_text,
                    )
                    if key in shared_aliases:
                        previous = shared_aliases[key]
                        raise RegistryValidationError(
                            f"shared alias collision for {normalized_text!r} "
                            f"in slot {definition.slot!r}: "
                            f"{previous.concept_id!r} vs "
                            f"{definition.concept_id!r}"
                        )
                    shared_aliases[key] = definition

        effect_map: dict[str, EffectDimensionDefinition] = {}
        effect_aliases: dict[
            tuple[str, str],
            EffectStateBinding,
        ] = {}
        for definition in effect_dimensions:
            if definition.effect_id in effect_map:
                raise RegistryValidationError(
                    f"duplicate effect id {definition.effect_id!r}"
                )
            effect_map[definition.effect_id] = definition
            for alias in definition.aliases:
                key = (alias.language, alias.normalized_text)
                if key in effect_aliases:
                    previous = effect_aliases[key]
                    raise RegistryValidationError(
                        f"effect-state alias collision for {alias.text!r}: "
                        f"{previous.effect_id!r} vs {definition.effect_id!r}"
                    )
                effect_aliases[key] = EffectStateBinding(
                    effect_id=definition.effect_id,
                    state_direction=alias.state_direction,
                    alias=alias,
                )
        for definition in axis_map.values():
            for binding in definition.effect_bindings:
                if binding.effect_id not in effect_map:
                    raise RegistryValidationError(
                        f"axis {definition.axis_id!r} references unknown "
                        f"effect {binding.effect_id!r}"
                    )

        _validate_runtime_alias_collisions(
            axes=axis_map.values(),
            regions=region_map.values(),
            shared_concepts=shared_map.values(),
            effect_dimensions=effect_map.values(),
        )

        object.__setattr__(self, "registry_version", version)
        object.__setattr__(self, "_axes", MappingProxyType(axis_map))
        object.__setattr__(
            self,
            "_axis_aliases",
            MappingProxyType(axis_aliases),
        )
        object.__setattr__(self, "_regions", MappingProxyType(region_map))
        object.__setattr__(
            self,
            "_region_aliases",
            MappingProxyType(region_aliases),
        )
        object.__setattr__(
            self,
            "_shared_concepts",
            MappingProxyType(shared_map),
        )
        object.__setattr__(
            self,
            "_shared_aliases",
            MappingProxyType(shared_aliases),
        )
        object.__setattr__(
            self,
            "_effect_dimensions",
            MappingProxyType(effect_map),
        )
        object.__setattr__(
            self,
            "_effect_aliases",
            MappingProxyType(effect_aliases),
        )
        _validate_registry_test_seeds(self)

    @property
    def axes(self) -> Mapping[str, AxisDefinition]:
        return self._axes

    @property
    def regions(self) -> Mapping[str, RegionDefinition]:
        return self._regions

    @property
    def shared_concepts(self) -> Mapping[str, SharedConceptDefinition]:
        return self._shared_concepts

    @property
    def effect_dimensions(self) -> Mapping[str, EffectDimensionDefinition]:
        return self._effect_dimensions

    @property
    def axis_ids(self) -> tuple[str, ...]:
        return tuple(self._axes)

    def get_axis(self, axis_id: str) -> AxisDefinition:
        try:
            return self._axes[axis_id]
        except KeyError as exc:
            raise KeyError(f"unknown semantic axis {axis_id!r}") from exc

    def resolve_axis_alias(
        self,
        text: str,
        language: str,
    ) -> AxisAliasBinding | None:
        key = (str(language).strip().lower(), normalize_alias_text(text))
        return self._axis_aliases.get(key)

    def resolve_region_alias(
        self,
        text: str,
        language: str,
    ) -> RegionDefinition | None:
        key = (str(language).strip().lower(), normalize_alias_text(text))
        return self._region_aliases.get(key)

    def resolve_shared_alias(
        self,
        slot: str,
        text: str,
        language: str,
    ) -> SharedConceptDefinition | None:
        key = (
            str(slot),
            str(language).strip().lower(),
            normalize_alias_text(text),
        )
        return self._shared_aliases.get(key)

    def resolve_shared_alias_contract(
        self,
        slot: str,
        text: str,
        language: str,
    ) -> AliasControllerContract | None:
        """Resolve controller metadata for the exact matched source surface."""

        definition = self.resolve_shared_alias(slot, text, language)
        if definition is None:
            return None
        normalized_text = normalize_alias_text(text)
        matching_aliases = tuple(
            alias
            for alias in definition.aliases
            if alias.language == str(language).strip().lower()
            and normalized_text in alias.normalized_forms
        )
        if len(matching_aliases) != 1:
            raise RegistryValidationError(
                "shared alias contract resolution must identify exactly one "
                "registry alias"
            )
        return matching_aliases[0].controller_contract_for(text)

    def resolve_effect_alias(
        self,
        text: str,
        language: str,
    ) -> EffectStateBinding | None:
        key = (
            str(language).strip().lower(),
            normalize_alias_text(text),
        )
        return self._effect_aliases.get(key)

    def resolve_axis_effect(
        self,
        axis_id: str,
        effect_id: str,
        axis_direction: int,
    ) -> int | None:
        if (
            isinstance(axis_direction, bool)
            or not isinstance(axis_direction, int)
            or axis_direction not in {-1, 1}
        ):
            raise ValueError("axis_direction must be the integer -1 or 1")
        normalized_effect_id = _identifier(effect_id, "effect_id")
        binding = self.get_axis_effect_binding(
            axis_id,
            normalized_effect_id,
        )
        if binding is not None:
            return axis_direction * binding.direction_multiplier
        return None

    def get_axis_effect_binding(
        self,
        axis_id: str,
        effect_id: str,
    ) -> AxisEffectBinding | None:
        normalized_effect_id = _identifier(effect_id, "effect_id")
        return next(
            (
                binding
                for binding in self.get_axis(axis_id).effect_bindings
                if binding.effect_id == normalized_effect_id
            ),
            None,
        )

    def extend(
        self,
        *,
        axes: Iterable[AxisDefinition] = (),
        regions: Iterable[RegionDefinition] = (),
        shared_concepts: Iterable[SharedConceptDefinition] = (),
        effect_dimensions: Iterable[EffectDimensionDefinition] = (),
        registry_version: str | None = None,
    ) -> ParameterRegistry:
        return ParameterRegistry(
            (*self._axes.values(), *tuple(axes)),
            regions=(*self._regions.values(), *tuple(regions)),
            shared_concepts=(
                *self._shared_concepts.values(),
                *tuple(shared_concepts),
            ),
            effect_dimensions=(
                *self._effect_dimensions.values(),
                *tuple(effect_dimensions),
            ),
            registry_version=registry_version or self.registry_version,
        )


def build_parameter_registry(
    *,
    axis_ids: Sequence[str],
    parameter_specs: Mapping[str, Mapping[str, object]],
    axis_policies: Mapping[str, AxisPolicy],
    render_capabilities: Mapping[
        str,
        Iterable[RenderCapability | str],
    ],
    axis_aliases: Mapping[str, Iterable[AxisAlias]],
    test_seeds: Mapping[str, Iterable[AxisTestSeed]],
    axis_effects: Mapping[
        str,
        Iterable[AxisEffectBinding],
    ] | None = None,
    regions: Iterable[RegionDefinition] = (),
    shared_concepts: Iterable[SharedConceptDefinition] = (),
    effect_dimensions: Iterable[EffectDimensionDefinition] = (),
    registry_version: str = SEMANTIC_REGISTRY_VERSION,
) -> ParameterRegistry:
    definitions: list[AxisDefinition] = []
    seen_axis_ids: set[str] = set()
    normalized_axis_ids: list[str] = []
    for raw_axis_id in axis_ids:
        axis_id = _identifier(raw_axis_id, "axis_id")
        if axis_id in seen_axis_ids:
            raise RegistryValidationError(f"duplicate axis id {axis_id!r}")
        seen_axis_ids.add(axis_id)
        normalized_axis_ids.append(axis_id)

    _validate_exact_axis_source(
        "schema",
        parameter_specs,
        seen_axis_ids,
    )
    _validate_exact_axis_source(
        "policy",
        axis_policies,
        seen_axis_ids,
    )
    _validate_exact_axis_source(
        "render capability",
        render_capabilities,
        seen_axis_ids,
    )
    _validate_exact_axis_source(
        "aliases",
        axis_aliases,
        seen_axis_ids,
    )
    _validate_exact_axis_source(
        "test seeds",
        test_seeds,
        seen_axis_ids,
    )
    normalized_axis_effects = (
        {
            axis_id: ()
            for axis_id in normalized_axis_ids
        }
        if axis_effects is None
        else axis_effects
    )
    _validate_exact_axis_source(
        "axis effects",
        normalized_axis_effects,
        seen_axis_ids,
    )

    for axis_id in normalized_axis_ids:
        capabilities: list[RenderCapability] = []
        for capability in render_capabilities[axis_id]:
            if isinstance(capability, RenderCapability):
                resolved_capability = capability
            else:
                resolved_capability = RenderCapability(
                    engine=str(capability),
                    parameter_key=axis_id,
                    regions=frozenset(EDIT_REGIONS),
                )
            _validate_known_engine_capability(axis_id, resolved_capability)
            capabilities.append(resolved_capability)
        definitions.append(
            AxisDefinition(
                axis_id=axis_id,
                schema=AxisSchema.from_parameter_spec(
                    axis_id,
                    parameter_specs[axis_id],
                ),
                policy=axis_policies[axis_id],
                render_capabilities=tuple(capabilities),
                aliases=tuple(axis_aliases[axis_id]),
                test_seeds=tuple(test_seeds[axis_id]),
                effect_bindings=tuple(normalized_axis_effects[axis_id]),
            )
        )
    return ParameterRegistry(
        definitions,
        regions=regions,
        shared_concepts=shared_concepts,
        effect_dimensions=effect_dimensions,
        registry_version=registry_version,
    )


def _validate_exact_axis_source(
    source_name: str,
    source: Mapping[str, object],
    declared_axis_ids: set[str],
) -> None:
    source_axis_ids = {str(axis_id) for axis_id in source}
    missing = declared_axis_ids.difference(source_axis_ids)
    if missing:
        raise RegistryValidationError(
            f"missing {source_name} for axis {sorted(missing)[0]!r}"
        )
    unexpected = source_axis_ids.difference(declared_axis_ids)
    if unexpected:
        raise RegistryValidationError(
            f"unexpected {source_name} for undeclared axes: "
            f"{sorted(unexpected)}"
        )


def _validate_known_engine_capability(
    axis_id: str,
    capability: RenderCapability,
) -> None:
    contract = get_engine_render_contract(capability.engine)
    if contract is None:
        return
    if capability.engine == "opencv" and capability.parameter_key != axis_id:
        raise RegistryValidationError(
            "OpenCV v1 render parameter_key must match its semantic axis: "
            f"axis={axis_id!r}, parameter_key={capability.parameter_key!r}"
        )
    try:
        contract.require_capability(
            parameter_key=capability.parameter_key,
            regions=capability.regions,
        )
    except RenderContractError as exc:
        raise RegistryValidationError(
            f"invalid render capability for axis {axis_id!r}: {exc}"
        ) from exc


def _validate_registry_test_seeds(registry: ParameterRegistry) -> None:
    """Prove every seed resolves to its declared axis and direction."""

    for definition in registry.axes.values():
        for seed in definition.test_seeds:
            resolved_axes, resolved_directions = _resolve_test_seed(
                registry,
                seed,
            )
            if resolved_axes != {definition.axis_id}:
                raise RegistryValidationError(
                    f"test seed {seed.text!r} for {definition.axis_id!r} "
                    f"resolves axes {sorted(resolved_axes)!r}"
                )
            if resolved_directions != {seed.expected_direction}:
                raise RegistryValidationError(
                    f"test seed {seed.text!r} for {definition.axis_id!r} "
                    f"resolves directions {sorted(resolved_directions)!r}, "
                    f"expected {seed.expected_direction}"
                )


def _resolve_test_seed(
    registry: ParameterRegistry,
    seed: AxisTestSeed,
) -> tuple[set[str], set[int]]:
    text = normalize_alias_text(seed.text)
    language = seed.language
    patterns: list[
        tuple[
            str,
            str,
            str | int,
            int | None,
            str | None,
            int,
        ]
    ] = []
    for definition in registry.axes.values():
        for alias in definition.aliases:
            if alias.language != language:
                continue
            patterns.append(
                (
                    alias.normalized_text,
                    "axis",
                    definition.axis_id,
                    alias.requested_direction,
                    alias.match_kind,
                    alias.direction_multiplier,
                )
            )
    for concept in registry.shared_concepts.values():
        if concept.slot != "direction" or concept.value not in {-1, 1}:
            continue
        for alias in concept.aliases:
            if alias.language != language:
                continue
            patterns.append(
                (
                    alias.normalized_text,
                    "direction",
                    int(concept.value),
                    int(concept.value),
                    None,
                    1,
                )
            )

    resolved_axes: set[str] = set()
    axis_matches: list[tuple[str, str | None, int | None, int]] = []
    shared_directions: set[int] = set()
    index = 0
    while index < len(text):
        matches = [
            pattern
            for pattern in patterns
            if text.startswith(pattern[0], index)
            and has_semantic_word_boundaries(
                text,
                index,
                index + len(pattern[0]),
            )
        ]
        if not matches:
            index += 1
            continue
        longest = max(len(pattern[0]) for pattern in matches)
        winners = [pattern for pattern in matches if len(pattern[0]) == longest]
        for (
            _,
            kind,
            value,
            direction,
            match_kind,
            direction_multiplier,
        ) in winners:
            if kind == "axis":
                resolved_axes.add(str(value))
                axis_matches.append(
                    (
                        str(value),
                        match_kind,
                        direction,
                        direction_multiplier,
                    )
                )
            elif direction is not None:
                shared_directions.add(direction)
        index += longest

    resolved_directions: set[int] = set()
    for _, match_kind, fused_direction, direction_multiplier in axis_matches:
        if (
            match_kind == "action"
            and fused_direction in {-1, 1}
            and shared_directions
        ):
            resolved_directions.update(
                int(fused_direction) * direction
                for direction in shared_directions
            )
            continue
        if fused_direction is not None:
            resolved_directions.add(int(fused_direction))
        resolved_directions.update(
            direction * direction_multiplier
            for direction in shared_directions
        )
    return resolved_axes, resolved_directions


def _axis_aliases(
    *values: tuple[str, str, str],
) -> tuple[AxisAlias, ...]:
    return tuple(
        AxisAlias(text=text, language=language, role=role)
        for text, language, role in values
    )


def _test_seeds(
    *values: tuple[str, str, int] | tuple[str, str, int, str],
) -> tuple[AxisTestSeed, ...]:
    seeds: list[AxisTestSeed] = []
    for value in values:
        text, language, direction, *strength = value
        seeds.append(
            AxisTestSeed(
                text=text,
                language=language,
                expected_direction=direction,
                expected_strength=strength[0] if strength else "normal",
            )
        )
    return tuple(seeds)


DEFAULT_AXIS_ALIASES: Mapping[str, tuple[AxisAlias, ...]] = MappingProxyType(
    {
        "exposure": (
            AxisAlias("exposure", "en", "axis"),
            AxisAlias("exposure value", "en", "axis"),
            AxisAlias("曝光", "zh", "axis"),
            AxisAlias("曝光值", "zh", "axis"),
            AxisAlias(
                "overexposed",
                "en",
                "negative",
                match_kind="observation",
                requested_direction=-1,
            ),
            AxisAlias(
                "underexposed",
                "en",
                "positive",
                match_kind="observation",
                requested_direction=1,
            ),
            AxisAlias(
                "under-exposed",
                "en",
                "positive",
                match_kind="observation",
                requested_direction=1,
            ),
            AxisAlias(
                "過曝",
                "zh",
                "negative",
                match_kind="observation",
                requested_direction=-1,
            ),
            AxisAlias(
                "欠曝",
                "zh",
                "positive",
                match_kind="observation",
                requested_direction=1,
            ),
        ),
        "brightness": (
            AxisAlias("brightness", "en", "axis"),
            AxisAlias("亮度", "zh", "axis"),
            AxisAlias("bright", "en", "positive"),
            AxisAlias("light", "en", "positive"),
            AxisAlias(
                "brighter",
                "en",
                "positive",
                implies_change=True,
            ),
            AxisAlias("dark", "en", "negative"),
            AxisAlias(
                "darker",
                "en",
                "negative",
                implies_change=True,
            ),
            AxisAlias("亮", "zh", "positive"),
            AxisAlias("暗", "zh", "negative"),
            AxisAlias(
                "brighten",
                "en",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "lighten",
                "en",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "darken",
                "en",
                "negative",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "dim",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "調亮",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="cross_axis_target",
            ),
            AxisAlias(
                "提亮",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="cross_axis_target",
            ),
            AxisAlias(
                "調暗",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="macro",
                object_binding="cross_axis_target",
            ),
            AxisAlias(
                "壓暗",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="cross_axis_target",
            ),
        ),
        "contrast": (
            *_axis_aliases(
                ("contrast", "en", "axis"),
                ("對比", "zh", "axis"),
                ("對比度", "zh", "axis"),
                ("反差", "zh", "axis"),
                ("contrasty", "en", "positive"),
                ("punchier", "en", "positive"),
                ("對比強", "zh", "positive"),
                ("強", "zh", "positive"),
                ("對比弱", "zh", "negative"),
                ("柔和", "zh", "negative"),
            ),
            AxisAlias(
                "flat",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "平平",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "平",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "衝",
                "zh",
                "negative",
                match_kind="observation",
            ),
        ),
        "highlights": (
            AxisAlias("highlights", "en", "axis"),
            AxisAlias("highlight", "en", "axis"),
            AxisAlias(
                "highlight detail",
                "en",
                "axis",
                direction_multiplier=-1,
            ),
            AxisAlias("bright tones", "en", "axis"),
            AxisAlias("highlight presence", "en", "axis"),
            AxisAlias("高光", "zh", "axis"),
            AxisAlias("亮部", "zh", "axis"),
            AxisAlias(
                "lift highlights",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "recover highlights",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "提亮高光",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "高光壓低",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "亮部壓",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "爆亮",
                "zh",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "爆掉",
                "zh",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "壓低高光",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "shadows": (
            AxisAlias("shadows", "en", "axis"),
            AxisAlias("shadow", "en", "axis"),
            AxisAlias("dark tones", "en", "axis"),
            AxisAlias("dark-tone detail", "en", "axis"),
            AxisAlias("shadow detail", "en", "axis"),
            AxisAlias("陰影", "zh", "axis"),
            AxisAlias("暗部", "zh", "axis"),
            AxisAlias(
                "lifted",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "crushed",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "deeper",
                "en",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "darker shadow detail",
                "en",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "crush",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "open shadows",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "recover",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "lift shadows",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "deepen",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "deepen shadows",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "提亮暗部",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "壓暗陰影",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "打開暗部",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "打開陰影",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "打開",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "沉",
                "zh",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "浮",
                "zh",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "死黑",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "加深",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "壓深",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "whites": (
            AxisAlias("whites", "en", "axis"),
            AxisAlias("white point", "en", "axis"),
            AxisAlias("bright whites", "en", "positive"),
            AxisAlias("dim whites", "en", "negative"),
            AxisAlias(
                "raise whites",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "lower whites",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("白位", "zh", "axis"),
            AxisAlias("白色色階", "zh", "axis"),
            AxisAlias(
                "提高白位",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "降低白位",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "blacks": (
            AxisAlias("blacks", "en", "axis"),
            AxisAlias("black point", "en", "axis"),
            AxisAlias("lifted blacks", "en", "positive"),
            AxisAlias("deeper blacks", "en", "negative"),
            AxisAlias(
                "raise blacks",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "lower blacks",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("黑位", "zh", "axis"),
            AxisAlias("黑色色階", "zh", "axis"),
            AxisAlias(
                "抬高黑位",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "壓低黑位",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "saturation": (
            AxisAlias("saturation", "en", "axis"),
            AxisAlias("color", "en", "axis"),
            AxisAlias("colors", "en", "axis"),
            AxisAlias("colour", "en", "axis"),
            AxisAlias("colours", "en", "axis"),
            AxisAlias("飽和度", "zh", "axis"),
            AxisAlias("鮮豔度", "zh", "axis"),
            AxisAlias("色彩", "zh", "axis"),
            AxisAlias("顏色", "zh", "axis"),
            AxisAlias("飽和", "zh", "axis"),
            AxisAlias("saturated", "en", "positive"),
            AxisAlias(
                "richer",
                "en",
                "positive",
                implies_change=True,
            ),
            AxisAlias("vivid", "en", "positive"),
            AxisAlias("vibrant", "en", "positive"),
            AxisAlias("colorful", "en", "positive"),
            AxisAlias("colourful", "en", "positive"),
            AxisAlias(
                "saturate",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "washed out",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "washed-out",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "washed",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "intense",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias("desaturated", "en", "negative"),
            AxisAlias(
                "oversaturated",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias("muted", "en", "negative"),
            AxisAlias("subdued", "en", "negative"),
            AxisAlias("natural", "en", "negative"),
            AxisAlias(
                "desaturate",
                "en",
                "negative",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "mute",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("鮮豔", "zh", "positive"),
            AxisAlias("艷麗", "zh", "positive"),
            AxisAlias("濃", "zh", "positive"),
            AxisAlias("飽滿", "zh", "positive"),
            AxisAlias("活", "zh", "positive"),
            AxisAlias("淡", "zh", "negative"),
            AxisAlias("低調", "zh", "negative"),
            AxisAlias(
                "退飽和",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("自然", "zh", "negative"),
            AxisAlias("清淡", "zh", "negative"),
        ),
        "vibrance": (
            AxisAlias("vibrance", "en", "axis"),
            AxisAlias("natural saturation", "en", "axis"),
            AxisAlias(
                "increase vibrance",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "reduce vibrance",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("自然飽和度", "zh", "axis"),
            AxisAlias("色彩活力", "zh", "axis"),
            AxisAlias(
                "增加自然飽和度",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "降低自然飽和度",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "temperature": (
            AxisAlias(
                "temperature",
                "en",
                "axis",
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "color temperature",
                "en",
                "axis",
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "colour temperature",
                "en",
                "axis",
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "warmth",
                "en",
                "axis",
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "色溫",
                "zh",
                "axis",
                controller_mode="explicit_axis",
            ),
            AxisAlias("warm tone", "en", "positive"),
            AxisAlias("warmer tone", "en", "positive"),
            AxisAlias("cool tone", "en", "negative"),
            AxisAlias("cool tones", "en", "negative"),
            AxisAlias("warmer", "en", "positive"),
            AxisAlias("cooler", "en", "negative"),
            AxisAlias("暖", "zh", "positive"),
            AxisAlias("暖色", "zh", "positive"),
            AxisAlias("黃", "zh", "positive"),
            AxisAlias("冷", "zh", "negative"),
            AxisAlias("冷色", "zh", "negative"),
            AxisAlias("藍", "zh", "negative"),
            AxisAlias(
                "warm",
                "en",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "cool",
                "en",
                "negative",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
        ),
        "white_balance_tint": (
            AxisAlias("white balance tint", "en", "axis"),
            AxisAlias("green magenta balance", "en", "axis"),
            AxisAlias("magenta tint", "en", "positive"),
            AxisAlias("green tint", "en", "negative"),
            AxisAlias(
                "shift tint magenta",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "shift tint green",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias("白平衡色偏", "zh", "axis"),
            AxisAlias("綠洋紅平衡", "zh", "axis"),
            AxisAlias("偏洋紅", "zh", "positive"),
            AxisAlias("偏綠", "zh", "negative"),
            AxisAlias(
                "往洋紅調整",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "往綠色調整",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "sharpen": (
            AxisAlias("sharpness", "en", "axis"),
            AxisAlias("sharpening", "en", "axis"),
            AxisAlias("銳利度", "zh", "axis"),
            AxisAlias("銳利感", "zh", "axis"),
            AxisAlias("銳化感", "zh", "axis"),
            AxisAlias("sharp", "en", "positive"),
            AxisAlias(
                "sharper",
                "en",
                "positive",
                implies_change=True,
            ),
            AxisAlias("soft", "en", "negative"),
            AxisAlias("soft focus", "en", "negative"),
            AxisAlias("銳利", "zh", "positive"),
            AxisAlias(
                "利",
                "zh",
                "positive",
                controller_mode="explicit_axis",
            ),
            AxisAlias("柔焦", "zh", "negative"),
            AxisAlias("銳", "zh", "positive"),
            AxisAlias(
                "軟",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "糊",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "oversharpened",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "over-sharpened",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "過度銳化",
                "zh",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "sharpen",
                "en",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_or_region",
            ),
            AxisAlias(
                "soften",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="cross_axis_target",
            ),
            AxisAlias(
                "銳化",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "clarity": (
            AxisAlias("clarity", "en", "axis"),
            AxisAlias("definition", "en", "axis"),
            AxisAlias(
                "clearer",
                "en",
                "positive",
                implies_change=True,
            ),
            AxisAlias("清晰度", "zh", "axis"),
            AxisAlias("通透度", "zh", "axis"),
            AxisAlias("清晰感", "zh", "axis"),
            AxisAlias("局部對比", "zh", "axis"),
            AxisAlias("清楚", "zh", "positive"),
            AxisAlias("清晰", "zh", "positive"),
        ),
        "dehaze": (
            AxisAlias(
                "dehaze",
                "en",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_only",
            ),
            AxisAlias("haze removal", "en", "axis"),
            AxisAlias(
                "haze",
                "en",
                "axis",
                direction_multiplier=-1,
            ),
            AxisAlias("dehazing", "en", "axis"),
            AxisAlias(
                "去霧",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_only",
            ),
            AxisAlias(
                "除霧",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="macro",
                object_binding="self_only",
            ),
            AxisAlias(
                "霧感",
                "zh",
                "axis",
                direction_multiplier=-1,
            ),
            AxisAlias(
                "霧氣",
                "zh",
                "axis",
                direction_multiplier=-1,
            ),
            AxisAlias(
                "霧",
                "zh",
                "axis",
                direction_multiplier=-1,
            ),
            AxisAlias(
                "remove haze",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "hazy",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "foggy",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "dehazed",
                "en",
                "negative",
                match_kind="observation",
            ),
            AxisAlias(
                "hazier",
                "en",
                "negative",
                match_kind="descriptor",
                implies_change=True,
            ),
            AxisAlias(
                "leave more haze",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "clear haze",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "clear the haze",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "veil",
                "en",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "層霧",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "霧霧",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "有霧",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "沒散",
                "zh",
                "positive",
                match_kind="observation",
            ),
            AxisAlias(
                "清",
                "zh",
                "positive",
                match_kind="descriptor",
                implies_change=True,
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "霧感去掉",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "霧氣加",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "留點霧氣",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
        "vignette": (
            AxisAlias("vignette", "en", "axis"),
            AxisAlias("vignetting", "en", "axis"),
            AxisAlias("暗角", "zh", "axis"),
            AxisAlias("暗角感", "zh", "axis"),
            AxisAlias("more vignette", "en", "positive"),
            AxisAlias("less vignette", "en", "negative"),
            AxisAlias(
                "add vignette",
                "en",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "remove vignette",
                "en",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "vignette lighter",
                "en",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "darker vignette",
                "en",
                "positive",
                match_kind="descriptor",
            ),
            AxisAlias(
                "lighter vignette",
                "en",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "fainter vignette",
                "en",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "暗角淺",
                "zh",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "暗角淡",
                "zh",
                "negative",
                match_kind="descriptor",
            ),
            AxisAlias(
                "暗角感淡",
                "zh",
                "negative",
                match_kind="descriptor",
                controller_mode="explicit_axis",
            ),
            AxisAlias(
                "增加暗角",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "做出暗角",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "減少暗角",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "加深暗角",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "暗角壓",
                "zh",
                "positive",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "去掉暗角",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
            AxisAlias(
                "去掉",
                "zh",
                "negative",
                match_kind="action",
                controller_mode="explicit_axis",
                object_binding="self_only",
            ),
        ),
    }
)


DEFAULT_TEST_SEEDS: Mapping[str, tuple[AxisTestSeed, ...]] = MappingProxyType(
    {
        "exposure": _test_seeds(
            ("increase exposure", "en", 1),
            ("decrease exposure", "en", -1),
            ("增加曝光", "zh", 1),
            ("降低曝光", "zh", -1),
        ),
        "brightness": _test_seeds(
            ("make it brighter", "en", 1),
            ("make it darker", "en", -1),
            ("亮一點", "zh", 1, "subtle"),
            ("暗一點", "zh", -1, "subtle"),
        ),
        "contrast": _test_seeds(
            ("increase contrast", "en", 1),
            ("reduce contrast", "en", -1),
            ("提高對比", "zh", 1),
            ("降低對比", "zh", -1),
        ),
        "highlights": _test_seeds(
            ("raise highlights", "en", 1),
            ("lower highlights", "en", -1),
            ("提高高光", "zh", 1),
            ("壓低高光", "zh", -1),
        ),
        "shadows": _test_seeds(
            ("raise shadows", "en", 1),
            ("lower shadows", "en", -1),
            ("提亮暗部", "zh", 1),
            ("壓暗陰影", "zh", -1),
        ),
        "whites": _test_seeds(
            ("raise whites", "en", 1),
            ("lower whites", "en", -1),
            ("提高白位", "zh", 1),
            ("降低白位", "zh", -1),
        ),
        "blacks": _test_seeds(
            ("raise blacks", "en", 1),
            ("lower blacks", "en", -1),
            ("抬高黑位", "zh", 1),
            ("壓低黑位", "zh", -1),
        ),
        "saturation": _test_seeds(
            ("increase saturation", "en", 1),
            ("reduce saturation", "en", -1),
            ("增加飽和度", "zh", 1),
            ("降低飽和度", "zh", -1),
        ),
        "vibrance": _test_seeds(
            ("increase vibrance", "en", 1),
            ("reduce vibrance", "en", -1),
            ("增加自然飽和度", "zh", 1),
            ("降低自然飽和度", "zh", -1),
        ),
        "temperature": _test_seeds(
            ("make it warmer", "en", 1),
            ("make it cooler", "en", -1),
            ("色溫暖一點", "zh", 1, "subtle"),
            ("色溫冷一點", "zh", -1, "subtle"),
        ),
        "white_balance_tint": _test_seeds(
            ("shift tint magenta", "en", 1),
            ("shift tint green", "en", -1),
            ("往洋紅調整", "zh", 1),
            ("往綠色調整", "zh", -1),
        ),
        "sharpen": _test_seeds(
            ("increase sharpness", "en", 1),
            ("reduce sharpness", "en", -1),
            ("增加銳利度", "zh", 1),
            ("降低銳利度", "zh", -1),
        ),
        "clarity": _test_seeds(
            ("increase clarity", "en", 1),
            ("reduce clarity", "en", -1),
            ("增加清晰度", "zh", 1),
            ("降低清晰度", "zh", -1),
        ),
        "dehaze": _test_seeds(
            ("increase dehaze", "en", 1),
            ("reduce dehaze", "en", -1),
            ("去霧多一點", "zh", 1, "subtle"),
            ("減少去霧", "zh", -1),
        ),
        "vignette": _test_seeds(
            ("increase vignette", "en", 1),
            ("reduce vignette", "en", -1),
            ("增加暗角", "zh", 1),
            ("減少暗角", "zh", -1),
        ),
    }
)


def _concept_aliases(
    *values: tuple[str, str] | tuple[str, str, str],
) -> tuple[ConceptAlias, ...]:
    aliases: list[ConceptAlias] = []
    for value in values:
        text, language, *morph_class = value
        aliases.append(
            ConceptAlias(
                text=text,
                language=language,
                morph_class=morph_class[0] if morph_class else None,
            )
        )
    return tuple(aliases)


DEFAULT_REGIONS: tuple[RegionDefinition, ...] = (
    RegionDefinition(
        "all",
        default_mask_type_for_region("all"),
        _concept_aliases(
            ("all", "en"),
            ("overall", "en"),
            ("whole", "en"),
            ("whole photo", "en"),
            ("whole shot", "en"),
            ("whole image", "en"),
            ("entire image", "en"),
            ("全部", "zh"),
            ("整張", "zh"),
            ("整張圖", "zh"),
            ("全圖", "zh"),
            ("整體", "zh"),
        ),
    ),
    RegionDefinition(
        "sky",
        default_mask_type_for_region("sky"),
        _concept_aliases(("sky", "en"), ("天空", "zh")),
    ),
    RegionDefinition(
        "person",
        default_mask_type_for_region("person"),
        _concept_aliases(
            ("person", "en"),
            ("people", "en"),
            ("portrait", "en"),
            ("人物", "zh"),
            ("人像", "zh"),
            ("人", "zh"),
        ),
    ),
    RegionDefinition(
        "background",
        default_mask_type_for_region("background"),
        _concept_aliases(("background", "en"), ("背景", "zh")),
    ),
    RegionDefinition(
        "shadows",
        default_mask_type_for_region("shadows"),
        _concept_aliases(
            ("shadow areas", "en"),
            ("dark areas", "en"),
            ("陰影區域", "zh"),
            ("暗部區域", "zh"),
        ),
    ),
    RegionDefinition(
        "highlights",
        default_mask_type_for_region("highlights"),
        _concept_aliases(
            ("highlight areas", "en"),
            ("bright areas", "en"),
            ("高光區域", "zh"),
            ("亮部區域", "zh"),
        ),
    ),
    RegionDefinition(
        "center",
        default_mask_type_for_region("center"),
        _concept_aliases(
            ("at the center", "en"),
            ("in the center", "en"),
            ("center", "en"),
            ("middle", "en"),
            ("中央區域", "zh"),
            ("中心區域", "zh"),
            ("中間區域", "zh"),
            ("中央", "zh"),
            ("中心", "zh"),
        ),
    ),
    RegionDefinition(
        "edges",
        default_mask_type_for_region("edges"),
        _concept_aliases(
            ("around the edges", "en"),
            ("at the edges", "en"),
            ("edge", "en"),
            ("edges", "en"),
            ("borders", "en"),
            ("邊緣", "zh"),
            ("四周", "zh"),
        ),
        attribute_axis_ids=("sharpen", "vignette"),
    ),
)


DEFAULT_SHARED_CONCEPTS: tuple[SharedConceptDefinition, ...] = (
    SharedConceptDefinition(
        "direction_positive",
        "direction",
        1,
        _concept_aliases(
            ("increase", "en", "en_progressive_drop_e"),
            ("raise", "en", "en_progressive_drop_e"),
            ("boost", "en", "en_progressive_regular"),
            ("enhance", "en", "en_progressive_drop_e"),
            ("strengthen", "en", "en_progressive_regular"),
            ("turn up", "en", "en_progressive_regular"),
            ("bring up", "en", "en_progressive_regular"),
            ("lift", "en", "en_progressive_regular"),
            ("add", "en", "en_progressive_regular"),
            ("stronger", "en"),
            ("higher", "en"),
            ("high", "en"),
            ("增加", "zh"),
            ("加", "zh"),
            ("加重", "zh"),
            ("提高", "zh"),
            ("增強", "zh"),
            ("調高", "zh"),
            ("上調", "zh"),
            ("拉高", "zh"),
            ("往上調", "zh"),
            ("往上補", "zh"),
            ("提起來", "zh"),
            ("拉起來", "zh"),
            ("補", "zh"),
            ("提", "zh"),
            ("加強", "zh"),
            ("高", "zh"),
            ("大", "zh"),
            ("抬", "zh"),
        )
        + (
            ConceptAlias(
                "up",
                "en",
                controller_contract=AliasControllerContract(mode="macro"),
            ),
        ),
    ),
    SharedConceptDefinition(
        "direction_negative",
        "direction",
        -1,
        _concept_aliases(
            ("decrease", "en", "en_progressive_drop_e"),
            ("lower", "en", "en_progressive_regular"),
            ("reduce", "en", "en_progressive_drop_e"),
            ("cut", "en", "en_progressive_double_final"),
            ("turn down", "en", "en_progressive_regular"),
            ("dial back", "en", "en_progressive_regular"),
            ("tone down", "en", "en_progressive_drop_e"),
            ("pull back", "en", "en_progressive_regular"),
            ("rein in", "en", "en_progressive_regular"),
            ("ease off", "en", "en_progressive_drop_e"),
            ("ease", "en", "en_progressive_drop_e"),
            ("remove", "en", "en_progressive_drop_e"),
            ("off", "en"),
            ("out", "en"),
            ("low", "en"),
            ("weak", "en"),
            ("降低", "zh"),
            ("降", "zh"),
            ("減", "zh"),
            ("減少", "zh"),
            ("調低", "zh"),
            ("下調", "zh"),
            ("壓低", "zh"),
            ("往下", "zh"),
            ("往下降", "zh"),
            ("往下調", "zh"),
            ("往下壓", "zh"),
            ("壓下去", "zh"),
            ("壓", "zh"),
            ("去", "zh"),
            ("減弱", "zh"),
            ("柔", "zh"),
            ("弱化", "zh"),
            ("收", "zh"),
            ("少", "zh"),
            ("低", "zh"),
            ("小", "zh"),
        )
        + (
            ConceptAlias(
                "down",
                "en",
                controller_contract=AliasControllerContract(mode="macro"),
            ),
        ),
    ),
    SharedConceptDefinition(
        "comparative_more",
        "direction",
        1,
        _concept_aliases(
            ("more", "en"),
            ("more so", "en"),
            ("更", "zh"),
            ("比較", "zh"),
            ("多", "zh"),
        ),
        preposed_strength="strong",
    ),
    SharedConceptDefinition(
        "comparative_less",
        "direction",
        -1,
        _concept_aliases(
            ("less", "en"),
            ("not as", "en"),
            ("沒那麼", "zh"),
            ("退", "zh"),
            ("退掉", "zh"),
            ("收回來", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "negated_comparative_less",
        "negated_comparative",
        "less",
        (
            *_concept_aliases(
                ("not so", "en"),
                ("not that", "en"),
            ),
            ConceptAlias(
                text="不要那麼",
                language="zh",
                controller_contract=AliasControllerContract(
                    mode="macro",
                    companions=True,
                    default_strength="normal",
                    relation="initial",
                ),
            ),
            *(
                ConceptAlias(
                    text=text,
                    language="zh",
                    controller_contract=AliasControllerContract(
                        mode="explicit_axis",
                        companions=False,
                        default_strength="subtle",
                        relation="correct",
                    ),
                )
                for text in ("別那麼", "別這麼")
            ),
        ),
    ),
    SharedConceptDefinition(
        "degree_comparison_reference",
        "comparison_reference",
        "degree",
        _concept_aliases(
            ("那麼", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "strength_subtle",
        "strength",
        "subtle",
        _concept_aliases(
            ("a little bit", "en"),
            ("a touch", "en"),
            ("touch", "en"),
            ("slight", "en"),
            ("slightly", "en"),
            ("a little", "en"),
            ("a bit", "en"),
            ("somewhat", "en"),
            ("gently", "en"),
            ("subtly", "en"),
            ("稍微", "zh"),
            ("一點點", "zh"),
            ("微微", "zh"),
            ("少許", "zh"),
            ("小幅", "zh"),
        )
        + (
            ConceptAlias(
                text="一點",
                language="zh",
                controller_contract=AliasControllerContract(
                    source_overrides=(
                        AliasControllerSurfaceOverride(
                            surface="一点",
                            default_strength="normal",
                        ),
                    ),
                ),
            ),
        ),
    ),
    SharedConceptDefinition(
        "strength_normal",
        "strength",
        "normal",
        _concept_aliases(
            ("normally", "en"),
            ("moderately", "en"),
            ("medium", "en"),
            ("適中", "zh"),
            ("正常幅度", "zh"),
            ("一些", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "strength_strong",
        "strength",
        "strong",
        _concept_aliases(
            ("strongly", "en"),
            ("strong", "en"),
            ("a lot", "en"),
            ("much", "en"),
            ("significantly", "en"),
            ("dramatically", "en"),
            ("way", "en"),
            ("far", "en"),
            ("most", "en"),
            ("very", "en"),
            ("really", "en"),
            ("大幅", "zh"),
            ("明顯", "zh"),
            ("很多", "zh"),
            ("非常", "zh"),
            ("真的", "zh"),
            ("強烈", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "negation",
        "negation",
        True,
        _concept_aliases(
            ("not", "en"),
            ("do not", "en"),
            ("no", "en"),
            ("never", "en"),
            ("不要", "zh"),
            ("別", "zh"),
            ("不必", "zh"),
            ("不用", "zh"),
            ("不", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "axis_reset",
        "operation",
        "reset",
        _concept_aliases(
            ("reset", "en"),
            ("restore", "en"),
            ("back to neutral", "en"),
            ("neutral value", "en"),
            ("重設", "zh"),
            ("重置", "zh"),
            ("還原", "zh"),
            ("回到中性", "zh"),
            ("歸零", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "global_reset",
        "terminal",
        "global_reset",
        _concept_aliases(
            ("back to original", "en"),
            ("return to the original", "en"),
            ("restore the original", "en"),
            ("restore original", "en"),
            ("reset all", "en"),
            ("reset all adjustments", "en"),
            ("start over", "en"),
            ("恢復原圖", "zh"),
            ("回到原圖", "zh"),
            ("全部重設", "zh"),
            ("重新開始", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "satisfied",
        "terminal",
        "satisfied",
        _concept_aliases(
            ("just right", "en"),
            ("good now", "en"),
            ("that works", "en"),
            ("這樣剛好", "zh"),
            ("剛好", "zh"),
            ("可以了", "zh"),
            ("這樣就好", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "conjunction",
        "conjunction",
        "and",
        _concept_aliases(
            ("and", "en"),
            ("plus", "en"),
            ("also", "en"),
            ("和", "zh"),
            ("並且", "zh"),
            ("又", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "persistent_state_conjunction",
        "conjunction",
        "and",
        _concept_aliases(("還有", "zh")),
        leading_axis_observation=True,
        observation_strength="subtle",
    ),
    SharedConceptDefinition(
        "contrastive_conjunction",
        "conjunction",
        "but",
        _concept_aliases(
            ("but", "en"),
            ("while", "en"),
            ("但是", "zh"),
            ("但", "zh"),
            ("不過", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "preservation",
        "guard",
        "preserve",
        _concept_aliases(
            ("preserve", "en"),
            ("retain", "en"),
            ("maintain", "en"),
            ("keep", "en"),
            ("leave", "en"),
            ("保留", "zh"),
            ("保持", "zh"),
            ("維持", "zh"),
            ("留著", "zh"),
            ("留", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "disjunction",
        "guard",
        "or",
        _concept_aliases(
            ("or", "en"),
            ("either", "en"),
            ("或者", "zh"),
            ("或", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "disjunction_or_still",
        "guard",
        "or",
        _concept_aliases(
            ("還是", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "exclusion",
        "guard",
        "exclude",
        _concept_aliases(
            ("without", "en"),
            ("except", "en"),
            ("leave alone", "en"),
            ("除外", "zh"),
            ("排除", "zh"),
            ("不要動", "zh"),
            ("保持不變", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "relation_continue",
        "relation",
        "continue",
        _concept_aliases(
            ("again", "en"),
            ("further", "en"),
            ("再", "zh"),
            ("再來", "zh"),
            ("繼續", "zh"),
            ("還要", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "numeric_absolute",
        "numeric_relation",
        "absolute",
        _concept_aliases(
            ("set to", "en"),
            ("change to", "en"),
            ("back to", "en"),
            ("=", "en"),
            ("to", "en"),
            ("at", "en"),
            ("調到", "zh"),
            ("設為", "zh"),
            ("改成", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "numeric_relative",
        "numeric_relation",
        "relative",
        _concept_aliases(
            ("by", "en"),
            ("delta", "en"),
            ("幅度", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "observation_too",
        "observation_modifier",
        "too",
        _concept_aliases(
            ("too", "en"),
            ("過於", "zh"),
            ("太", "zh"),
            ("過", "zh"),
            ("很", "zh"),
            ("過頭", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "observation_not_enough",
        "observation_modifier",
        "not_enough",
        _concept_aliases(
            ("not enough", "en"),
            ("too little", "en"),
            ("insufficient", "en"),
            ("lacks", "en"),
            ("dull", "en"),
            ("blocked", "en"),
            ("blocked up", "en"),
            ("不夠", "zh"),
            ("不太夠", "zh"),
            ("不足", "zh"),
            ("堵住", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "sufficiency_enough",
        "observation_modifier",
        "not_enough",
        _concept_aliases(
            ("enough", "en"),
        ),
    ),
    SharedConceptDefinition(
        "observation_too_much",
        "observation_modifier",
        "too_much",
        _concept_aliases(
            ("too much", "en"),
            ("harsh", "en"),
            ("過多", "zh"),
            ("過度", "zh"),
        )
        + tuple(
            ConceptAlias(
                text=text,
                language="en",
                controller_contract=AliasControllerContract(
                    default_strength="subtle",
                ),
            )
            for text in ("overdone", "heavy")
        )
        + tuple(
            ConceptAlias(
                text=text,
                language="zh",
                controller_contract=AliasControllerContract(
                    default_strength="subtle",
                ),
            )
            for text in ("太多", "太重", "刺眼", "硬")
        ),
    ),
    SharedConceptDefinition(
        "observation_mild",
        "observation_modifier",
        "mild",
        _concept_aliases(
            ("有點", "zh"),
            ("偏", "zh"),
        ),
        observation_strength="subtle",
    ),
    SharedConceptDefinition(
        "state_link",
        "state_link",
        True,
        _concept_aliases(
            ("is looking", "en"),
            ("are looking", "en"),
            ("looks", "en"),
            ("look", "en"),
            ("feels", "en"),
            ("feel", "en"),
            ("seems", "en"),
            ("appears", "en"),
            ("does", "en"),
            ("is", "en"),
            ("are", "en"),
            ("has", "en"),
            ("have", "en"),
            ("看起來", "zh"),
            ("感覺", "zh"),
            ("顯得", "zh"),
            ("像", "zh"),
            ("有", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "effect_reference",
        "effect_reference",
        True,
        _concept_aliases(
            ("effect", "en"),
            ("adjustment", "en"),
            ("效果", "zh"),
            ("調整幅度", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "generic_edit_action",
        "generic_action",
        "edit",
        _concept_aliases(
            ("change", "en"),
            ("adjust", "en"),
            ("set", "en"),
            ("turn", "en"),
            ("調整", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "return_negative_action",
        "generic_action",
        "return_negative",
        _concept_aliases(
            ("dial", "en"),
        ),
    ),
    SharedConceptDefinition(
        "surface_remove_action",
        "surface_action",
        "remove",
        _concept_aliases(
            ("clear", "en"),
            ("清掉", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "anaphora_singular",
        "anaphora",
        "singular",
        _concept_aliases(
            ("it", "en"),
            ("它", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "anaphora_plural",
        "anaphora",
        "plural",
        _concept_aliases(
            ("them", "en"),
            ("它們", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "spatial_front_of",
        "spatial_relation",
        "front_of",
        _concept_aliases(
            ("in front of", "en"),
            ("前面", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "mechanism_with",
        "mechanism",
        "with",
        _concept_aliases(
            ("with", "en"),
            ("using", "en"),
            ("用", "zh"),
            ("以", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "region_scope",
        "scope",
        "region",
        _concept_aliases(
            ("around", "en"),
            ("within", "en"),
            ("only", "en"),
            ("in", "en"),
            ("on", "en"),
            ("for", "en"),
            ("只調", "zh"),
            ("只要", "zh"),
            ("只有", "zh"),
            ("只", "zh"),
            ("在", "zh"),
            ("中的", "zh"),
            ("周圍", "zh"),
            ("周邊", "zh"),
            ("邊上", "zh"),
            ("臉上", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "contextual_all",
        "region_context",
        "all",
        _concept_aliases(
            ("image", "en"),
            ("photo", "en"),
            ("picture", "en"),
            ("shot", "en"),
            ("frame", "en"),
            ("everything", "en"),
            ("照片", "zh"),
            ("圖片", "zh"),
            ("畫面", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "existential",
        "existential",
        True,
        _concept_aliases(
            ("there is", "en"),
            ("there are", "en"),
        ),
    ),
    SharedConceptDefinition(
        "clause_aspect_already",
        "clause_aspect",
        "already",
        _concept_aliases(
            ("already", "en"),
            ("已經", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "clause_aspect_still",
        "clause_aspect",
        "still",
        _concept_aliases(
            ("still", "en"),
            ("還", "zh"),
            ("仍然", "zh"),
        ),
        observation_strength="subtle",
    ),
    SharedConceptDefinition(
        "clause_aspect_after",
        "clause_aspect",
        "after",
        _concept_aliases(
            ("後", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "clause_modal_can",
        "clause_modal",
        "can",
        _concept_aliases(("can", "en")),
    ),
    SharedConceptDefinition(
        "clause_modal_could",
        "clause_modal",
        "could",
        _concept_aliases(("could", "en")),
    ),
    SharedConceptDefinition(
        "clause_modal_would",
        "clause_modal",
        "would",
        _concept_aliases(("would", "en")),
    ),
    SharedConceptDefinition(
        "clause_subject_first_person",
        "clause_subject",
        "first_person",
        _concept_aliases(
            ("i", "en"),
            ("we", "en"),
        ),
    ),
    SharedConceptDefinition(
        "clause_subject_second_person",
        "clause_subject",
        "second_person",
        _concept_aliases(("you", "en")),
    ),
    SharedConceptDefinition(
        "clause_subject_third_person",
        "clause_subject",
        "third_person",
        _concept_aliases(
            ("he", "en"),
            ("she", "en"),
            ("they", "en"),
        ),
    ),
    SharedConceptDefinition(
        "request_marker",
        "request_marker",
        True,
        _concept_aliases(
            ("please", "en"),
            ("kindly", "en"),
        ),
    ),
    SharedConceptDefinition(
        "request_desire_predicate",
        "request_predicate",
        "desire",
        _concept_aliases(
            ("would like", "en"),
            ("want", "en"),
            ("need", "en"),
            ("needs", "en"),
            ("prefer", "en"),
        ),
    ),
    SharedConceptDefinition(
        "request_imperative_predicate",
        "request_predicate",
        "imperative",
        _concept_aliases(
            ("make", "en"),
            ("let", "en"),
            ("get", "en"),
            ("give", "en"),
            ("push", "en"),
            ("pull", "en"),
            ("take", "en"),
            ("bring", "en"),
            ("rein", "en"),
            ("use", "en"),
        ),
    ),
    SharedConceptDefinition(
        "person_object",
        "region_object",
        "person",
        _concept_aliases(("me", "en")),
    ),
    SharedConceptDefinition(
        "generic_region_support",
        "region_support",
        "generic",
        _concept_aliases(
            ("area", "en"),
            ("區域", "zh"),
            ("地方", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "subject_region_support",
        "region_support",
        "subject",
        _concept_aliases(
            ("subject", "en"),
            ("主體", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "content_region_support",
        "region_constraint",
        "person",
        _concept_aliases(
            ("膚色", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "region_anaphora_singular",
        "region_anaphora",
        "singular",
        _concept_aliases(
            ("there", "en"),
            ("那塊", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "region_anaphora_plural",
        "region_anaphora",
        "plural",
        _concept_aliases(
            ("their", "en"),
        ),
    ),
    SharedConceptDefinition(
        "tone_attribute",
        "semantic_attribute",
        "tone",
        _concept_aliases(
            ("tone", "en"),
        ),
    ),
    SharedConceptDefinition(
        "detail_attribute",
        "semantic_attribute",
        "detail",
        _concept_aliases(
            ("detail", "en"),
            ("細節", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "parameter_attribute",
        "semantic_attribute",
        "parameter",
        _concept_aliases(
            ("parameter", "en"),
            ("parameters", "en"),
            ("value", "en"),
            ("values", "en"),
            ("參數", "zh"),
            ("值", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "quality_attribute",
        "semantic_attribute",
        "quality",
        _concept_aliases(
            ("質感", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "distributive_scope",
        "scope_quantifier",
        "distributive",
        _concept_aliases(
            ("都", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "compound_together",
        "compound_marker",
        "together",
        _concept_aliases(
            ("together", "en"),
            ("一起", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "return_relation",
        "return_relation",
        "return",
        _concept_aliases(
            ("return", "en"),
            ("back", "en"),
            ("調回", "zh"),
            ("回", "zh"),
            ("回來", "zh"),
        ),
    ),
    SharedConceptDefinition(
        "determiner",
        "function_word",
        "determiner",
        _concept_aliases(
            ("the", "en"),
            ("a", "en"),
            ("an", "en"),
        ),
    ),
    SharedConceptDefinition(
        "demonstrative",
        "function_word",
        "demonstrative",
        _concept_aliases(
            ("this", "en"),
            ("that", "en"),
        ),
    ),
    SharedConceptDefinition(
        "possessive",
        "function_word",
        "possessive",
        _concept_aliases(
            ("my", "en"),
        ),
    ),
    SharedConceptDefinition(
        "percent_unit",
        "numeric_unit",
        "percent",
        _concept_aliases(
            ("%", "en"),
            ("percent", "en"),
            ("percentage", "en"),
        ),
    ),
    SharedConceptDefinition(
        "polite_wrapper",
        "noise",
        True,
        _concept_aliases(
            ("just", "en"),
            ("now", "en"),
            ("first", "en"),
            ("some", "en"),
            ("from", "en"),
            ("toward", "en"),
            ("to the", "en"),
            ("been", "en"),
            ("over", "en"),
            ("'s", "en"),
            ("of", "en"),
            ("幫我", "zh"),
            ("麻煩", "zh"),
            ("請", "zh"),
            ("可以", "zh"),
            ("能不能", "zh"),
            ("把", "zh"),
            ("將", "zh"),
            ("讓", "zh"),
            ("讓人感覺", "zh"),
            ("弄得", "zh"),
            ("弄", "zh"),
            ("這張", "zh"),
            ("變得", "zh"),
            ("往", "zh"),
            ("拉", "zh"),
            ("先", "zh"),
            ("就好", "zh"),
            ("要", "zh"),
            ("做出", "zh"),
            ("調", "zh"),
            ("點", "zh"),
            ("些", "zh"),
            ("得", "zh"),
            ("一下", "zh"),
            ("了", "zh"),
            ("的", "zh"),
            ("吧", "zh"),
            ("嗎", "zh"),
        ),
    ),
)


DEFAULT_RENDER_CAPABILITIES: Mapping[
    str,
    tuple[RenderCapability, ...],
] = MappingProxyType(
    {
        axis_id: (
            RenderCapability(
                engine="opencv",
                parameter_key=axis_id,
                regions=frozenset(EDIT_REGIONS),
            ),
        )
        for axis_id in MANUAL_PARAMETER_KEYS
    }
)

DEFAULT_EFFECT_DIMENSIONS: tuple[EffectDimensionDefinition, ...] = (
    EffectDimensionDefinition("brightness"),
    EffectDimensionDefinition(
        "visual_darkness",
        (
            EffectStateAlias("黑", "zh", 1),
        ),
    ),
    EffectDimensionDefinition(
        "visual_depth",
        (
            EffectStateAlias("深", "zh", 1),
        ),
    ),
    EffectDimensionDefinition(
        "visual_openness",
        (
            EffectStateAlias("open", "en", 1),
        ),
    ),
)

DEFAULT_AXIS_EFFECTS: Mapping[
    str,
    tuple[AxisEffectBinding, ...],
] = MappingProxyType(
    {
        "exposure": (),
        "brightness": (
            AxisEffectBinding("brightness", 1, canonical=True),
        ),
        "contrast": (),
        "highlights": (),
        "shadows": (
            AxisEffectBinding("visual_darkness", -1),
            AxisEffectBinding("visual_depth", -1),
            AxisEffectBinding("visual_openness", 1),
        ),
        "whites": (),
        "blacks": (),
        "saturation": (),
        "vibrance": (),
        "temperature": (),
        "white_balance_tint": (),
        "sharpen": (),
        "clarity": (),
        "dehaze": (),
        "vignette": (
            AxisEffectBinding("brightness", -1),
            AxisEffectBinding("visual_darkness", 1),
            AxisEffectBinding("visual_depth", 1),
            AxisEffectBinding("visual_openness", -1),
        ),
    }
)


def build_default_parameter_registry() -> ParameterRegistry:
    """Adapt current schema/policy sources without redefining numeric metadata."""

    return build_parameter_registry(
        axis_ids=MANUAL_PARAMETER_KEYS,
        parameter_specs={
            axis_id: EDIT_PARAMETER_SPECS[axis_id]
            for axis_id in MANUAL_PARAMETER_KEYS
        },
        axis_policies=AXIS_POLICIES,
        render_capabilities=DEFAULT_RENDER_CAPABILITIES,
        axis_aliases=DEFAULT_AXIS_ALIASES,
        test_seeds=DEFAULT_TEST_SEEDS,
        axis_effects=DEFAULT_AXIS_EFFECTS,
        regions=DEFAULT_REGIONS,
        shared_concepts=DEFAULT_SHARED_CONCEPTS,
        effect_dimensions=DEFAULT_EFFECT_DIMENSIONS,
    )


DEFAULT_PARAMETER_REGISTRY = build_default_parameter_registry()
