from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.services.edit_schema import EDIT_PARAMETER_SPECS, MANUAL_PARAMETER_KEYS


AXIS_POLICY_VERSION = "axis_policy_v2"
ADAPTIVE_AXIS_ORDER = tuple(MANUAL_PARAMETER_KEYS)


@dataclass(frozen=True)
class AxisPolicy:
    axis: str
    label: str
    unit: str
    family: str
    transform: str
    neutral: float
    minimum: float
    maximum: float
    quantum: float
    positive_intent: str
    negative_intent: str
    positive_seeds: Mapping[str, float]
    negative_seeds: Mapping[str, float]
    minimum_active: float | None = None
    minimum_visible_step: float | None = None
    policy_version: str = AXIS_POLICY_VERSION

    def __post_init__(self) -> None:
        identifier_fields = (
            "axis",
            "label",
            "family",
            "transform",
            "positive_intent",
            "negative_intent",
            "policy_version",
        )
        for field_name in identifier_fields:
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"AxisPolicy {field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "unit", str(self.unit))
        if self.family not in {"signed", "ratio", "one_sided_amount"}:
            raise ValueError(f"Unsupported AxisPolicy family: {self.family!r}")
        if self.transform not in {"linear", "log"}:
            raise ValueError(
                f"Unsupported AxisPolicy transform: {self.transform!r}"
            )
        if self.positive_intent == self.negative_intent:
            raise ValueError("AxisPolicy positive/negative intents must differ")

        numeric_fields = ("neutral", "minimum", "maximum", "quantum")
        numeric: dict[str, float] = {}
        for field_name in numeric_fields:
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"AxisPolicy {field_name} must be finite")
            numeric[field_name] = value
            object.__setattr__(self, field_name, value)
        if numeric["minimum"] >= numeric["maximum"]:
            raise ValueError("AxisPolicy minimum must be below maximum")
        if not numeric["minimum"] <= numeric["neutral"] <= numeric["maximum"]:
            raise ValueError("AxisPolicy neutral must lie inside its range")
        if numeric["quantum"] <= 0:
            raise ValueError("AxisPolicy quantum must be positive")

        minimum_active = self.minimum_active
        if minimum_active is not None:
            minimum_active = float(minimum_active)
            if (
                not math.isfinite(minimum_active)
                or minimum_active <= 0
                or not numeric["minimum"]
                <= minimum_active
                <= numeric["maximum"]
            ):
                raise ValueError(
                    "AxisPolicy minimum_active must be finite, positive, "
                    "and inside the parameter range"
                )
            object.__setattr__(self, "minimum_active", minimum_active)
        if self.transform == "log" and (
            minimum_active is None and numeric["minimum"] <= 0
        ):
            raise ValueError(
                "Log AxisPolicy with a non-positive minimum needs "
                "minimum_active"
            )

        visible_step = self.minimum_visible_step
        if visible_step is not None:
            visible_step = float(visible_step)
            if not math.isfinite(visible_step) or visible_step <= 0:
                raise ValueError(
                    "AxisPolicy minimum_visible_step must be positive"
                )
            object.__setattr__(
                self,
                "minimum_visible_step",
                visible_step,
            )

        positive = _validate_seed_table(
            self.positive_seeds,
            axis=self.axis,
            direction=1,
            minimum=numeric["minimum"],
            maximum=numeric["maximum"],
            neutral=numeric["neutral"],
            one_sided=self.one_sided,
        )
        negative = _validate_seed_table(
            self.negative_seeds,
            axis=self.axis,
            direction=-1,
            minimum=numeric["minimum"],
            maximum=numeric["maximum"],
            neutral=numeric["neutral"],
            one_sided=self.one_sided,
        )
        object.__setattr__(self, "positive_seeds", MappingProxyType(positive))
        object.__setattr__(self, "negative_seeds", MappingProxyType(negative))

    @property
    def one_sided(self) -> bool:
        return self.family == "one_sided_amount"

    def seed_target(self, direction: int, strength: str) -> float:
        table = self.positive_seeds if direction > 0 else self.negative_seeds
        normalized = strength if strength in table else "normal"
        return float(table[normalized])

    def as_dict(self) -> dict[str, object]:
        return {
            "axis": self.axis,
            "label": self.label,
            "unit": self.unit,
            "family": self.family,
            "transform": self.transform,
            "neutral": self.neutral,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "quantum": self.quantum,
            "minimum_active": self.minimum_active,
            "minimum_visible_step": self.minimum_visible_step or self.quantum,
            "allowed_directions": [1, -1],
            "positive_intent": self.positive_intent,
            "negative_intent": self.negative_intent,
            "positive_seeds": dict(self.positive_seeds),
            "negative_seeds": dict(self.negative_seeds),
            "policy_version": self.policy_version,
            "boundary_behavior": (
                "negative_requires_existing_amount"
                if self.one_sided
                else "clamp_and_converge"
            ),
        }


def _frozen(values: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType({key: float(value) for key, value in values.items()})


def _validate_seed_table(
    values: Mapping[str, float],
    *,
    axis: str,
    direction: int,
    minimum: float,
    maximum: float,
    neutral: float,
    one_sided: bool,
) -> dict[str, float]:
    required = {"subtle", "normal", "strong"}
    if set(values) != required:
        raise ValueError(
            f"AxisPolicy {axis!r} seed keys must be exactly "
            f"{sorted(required)!r}"
        )
    result: dict[str, float] = {}
    for strength in ("subtle", "normal", "strong"):
        value = float(values[strength])
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(
                f"AxisPolicy {axis!r} {strength} seed is outside its range"
            )
        if one_sided:
            if value <= neutral:
                raise ValueError(
                    f"AxisPolicy {axis!r} one-sided seed must exceed neutral"
                )
        elif direction > 0 and value <= neutral:
            raise ValueError(
                f"AxisPolicy {axis!r} positive seed must exceed neutral"
            )
        elif direction < 0 and value >= neutral:
            raise ValueError(
                f"AxisPolicy {axis!r} negative seed must be below neutral"
            )
        result[strength] = value
    distances = [abs(result[key] - neutral) for key in ("subtle", "normal", "strong")]
    if not distances[0] < distances[1] < distances[2]:
        raise ValueError(
            f"AxisPolicy {axis!r} seed strengths must be strictly monotonic"
        )
    return result


def _policy(
    axis: str,
    *,
    family: str,
    transform: str,
    positive_intent: str,
    negative_intent: str,
    positive_seeds: Mapping[str, float],
    negative_seeds: Mapping[str, float],
    minimum_active: float | None = None,
) -> AxisPolicy:
    spec = EDIT_PARAMETER_SPECS[axis]
    return AxisPolicy(
        axis=axis,
        label=str(spec["label"]),
        unit=str(spec["unit"]),
        family=family,
        transform=transform,
        neutral=float(spec["neutral"]),
        minimum=float(spec["minimum"]),
        maximum=float(spec["maximum"]),
        quantum=float(spec["step"]),
        positive_intent=positive_intent,
        negative_intent=negative_intent,
        positive_seeds=_frozen(positive_seeds),
        negative_seeds=_frozen(negative_seeds),
        minimum_active=minimum_active,
        minimum_visible_step=float(spec["step"]),
    )


AXIS_POLICIES: Mapping[str, AxisPolicy] = MappingProxyType(
    {
        "exposure": _policy(
            "exposure",
            family="signed",
            transform="linear",
            positive_intent="increase_exposure",
            negative_intent="decrease_exposure",
            positive_seeds={"subtle": 0.25, "normal": 0.5, "strong": 1.0},
            negative_seeds={"subtle": -0.25, "normal": -0.5, "strong": -1.0},
        ),
        "brightness": _policy(
            "brightness",
            family="signed",
            transform="linear",
            positive_intent="brighten",
            negative_intent="darken",
            positive_seeds={"subtle": 10.0, "normal": 18.0, "strong": 30.0},
            negative_seeds={"subtle": -10.0, "normal": -18.0, "strong": -30.0},
        ),
        "contrast": _policy(
            "contrast",
            family="ratio",
            transform="log",
            positive_intent="increase_contrast",
            negative_intent="decrease_contrast",
            positive_seeds={"subtle": 1.08, "normal": 1.15, "strong": 1.3},
            negative_seeds={"subtle": 0.94, "normal": 0.88, "strong": 0.75},
        ),
        "highlights": _policy(
            "highlights",
            family="signed",
            transform="linear",
            positive_intent="raise_highlights",
            negative_intent="lower_highlights",
            positive_seeds={"subtle": 15.0, "normal": 30.0, "strong": 50.0},
            negative_seeds={"subtle": -15.0, "normal": -30.0, "strong": -50.0},
        ),
        "shadows": _policy(
            "shadows",
            family="signed",
            transform="linear",
            positive_intent="raise_shadows",
            negative_intent="lower_shadows",
            positive_seeds={"subtle": 15.0, "normal": 30.0, "strong": 50.0},
            negative_seeds={"subtle": -15.0, "normal": -30.0, "strong": -50.0},
        ),
        "whites": _policy(
            "whites",
            family="signed",
            transform="linear",
            positive_intent="raise_whites",
            negative_intent="lower_whites",
            positive_seeds={"subtle": 12.0, "normal": 25.0, "strong": 45.0},
            negative_seeds={"subtle": -12.0, "normal": -25.0, "strong": -45.0},
        ),
        "blacks": _policy(
            "blacks",
            family="signed",
            transform="linear",
            positive_intent="raise_blacks",
            negative_intent="lower_blacks",
            positive_seeds={"subtle": 12.0, "normal": 25.0, "strong": 45.0},
            negative_seeds={"subtle": -12.0, "normal": -25.0, "strong": -45.0},
        ),
        "saturation": _policy(
            "saturation",
            family="ratio",
            transform="log",
            positive_intent="vivid",
            negative_intent="natural",
            positive_seeds={"subtle": 1.1, "normal": 1.18, "strong": 1.28},
            negative_seeds={"subtle": 0.94, "normal": 0.88, "strong": 0.8},
            minimum_active=0.01,
        ),
        "vibrance": _policy(
            "vibrance",
            family="signed",
            transform="linear",
            positive_intent="increase_vibrance",
            negative_intent="reduce_vibrance",
            positive_seeds={"subtle": 0.12, "normal": 0.25, "strong": 0.45},
            negative_seeds={"subtle": -0.12, "normal": -0.25, "strong": -0.45},
        ),
        "temperature": _policy(
            "temperature",
            family="signed",
            transform="linear",
            positive_intent="warm",
            negative_intent="cool",
            positive_seeds={"subtle": 8.0, "normal": 15.0, "strong": 25.0},
            negative_seeds={"subtle": -8.0, "normal": -15.0, "strong": -25.0},
        ),
        "white_balance_tint": _policy(
            "white_balance_tint",
            family="signed",
            transform="linear",
            positive_intent="shift_tint_magenta",
            negative_intent="shift_tint_green",
            positive_seeds={"subtle": 6.0, "normal": 12.0, "strong": 22.0},
            negative_seeds={"subtle": -6.0, "normal": -12.0, "strong": -22.0},
        ),
        "sharpen": _policy(
            "sharpen",
            family="one_sided_amount",
            transform="linear",
            positive_intent="sharpen",
            negative_intent="reduce_sharpen",
            positive_seeds={"subtle": 0.22, "normal": 0.38, "strong": 0.55},
            negative_seeds={"subtle": 0.08, "normal": 0.18, "strong": 0.3},
        ),
        "clarity": _policy(
            "clarity",
            family="one_sided_amount",
            transform="linear",
            positive_intent="increase_clarity",
            negative_intent="reduce_clarity",
            positive_seeds={"subtle": 0.12, "normal": 0.24, "strong": 0.45},
            negative_seeds={"subtle": 0.06, "normal": 0.12, "strong": 0.22},
        ),
        "dehaze": _policy(
            "dehaze",
            family="one_sided_amount",
            transform="linear",
            positive_intent="dehaze",
            negative_intent="reduce_dehaze",
            positive_seeds={"subtle": 0.22, "normal": 0.38, "strong": 0.55},
            negative_seeds={"subtle": 0.08, "normal": 0.18, "strong": 0.3},
        ),
        "vignette": _policy(
            "vignette",
            family="one_sided_amount",
            transform="linear",
            positive_intent="increase_vignette",
            negative_intent="reduce_vignette",
            positive_seeds={"subtle": 0.08, "normal": 0.16, "strong": 0.3},
            negative_seeds={"subtle": 0.04, "normal": 0.08, "strong": 0.15},
        ),
    }
)


if tuple(AXIS_POLICIES) != ADAPTIVE_AXIS_ORDER:
    raise RuntimeError(
        "Adaptive policy keys must exactly match the public manual parameter schema"
    )

_DECLARED_INTENTS = tuple(
    intent
    for policy in AXIS_POLICIES.values()
    for intent in (policy.positive_intent, policy.negative_intent)
)
if len(_DECLARED_INTENTS) != len(set(_DECLARED_INTENTS)):
    raise RuntimeError("Adaptive policy intents must be globally unique")


INTENT_TO_AXIS_DIRECTION: Mapping[str, tuple[str, int]] = MappingProxyType(
    {
        **{
            policy.positive_intent: (policy.axis, 1)
            for policy in AXIS_POLICIES.values()
        },
        **{
            policy.negative_intent: (policy.axis, -1)
            for policy in AXIS_POLICIES.values()
        },
    }
)


def policy_registry_payload() -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for axis, policy in AXIS_POLICIES.items():
        spec = EDIT_PARAMETER_SPECS[axis]
        payload[axis] = {
            **policy.as_dict(),
            "label_en": str(spec["label_en"]),
            "labels": {
                "zh": str(spec["label"]),
                "en": str(spec["label_en"]),
            },
            "group": str(spec["group"]),
            "default_visible": bool(spec["default_visible"]),
        }
    return payload


def coordinate(policy: AxisPolicy, value: float) -> float:
    numeric = float(value)
    if policy.transform == "log":
        floor = policy.minimum_active or max(policy.minimum, policy.quantum)
        return math.log(max(numeric, floor))
    return numeric


def from_coordinate(policy: AxisPolicy, value: float) -> float:
    if policy.transform == "log":
        return math.exp(float(value))
    return float(value)


def quantize(policy: AxisPolicy, value: float) -> float:
    bounded = min(max(float(value), policy.minimum), policy.maximum)
    steps = round((bounded - policy.minimum) / policy.quantum)
    decimals = _step_decimals(policy.quantum)
    return round(policy.minimum + steps * policy.quantum, decimals)


def active_quantize(policy: AxisPolicy, value: float) -> float:
    quantized = quantize(policy, value)
    if policy.minimum_active is not None and quantized < policy.minimum_active:
        return quantize(policy, policy.minimum_active)
    return quantized


def distance(policy: AxisPolicy, left: float, right: float) -> float:
    return abs(coordinate(policy, right) - coordinate(policy, left))


def advance(policy: AxisPolicy, current: float, direction: int, step: float) -> float:
    return from_coordinate(policy, coordinate(policy, current) + direction * step)


def midpoint(policy: AxisPolicy, lower: float, upper: float) -> float:
    return from_coordinate(
        policy,
        (coordinate(policy, lower) + coordinate(policy, upper)) / 2.0,
    )


def seed_distance(policy: AxisPolicy, direction: int, strength: str) -> float:
    target = policy.seed_target(direction, strength)
    return distance(policy, policy.neutral, target)


def _step_decimals(step: float) -> int:
    text = f"{float(step):.12f}".rstrip("0")
    return len(text.split(".", 1)[1]) if "." in text else 0
