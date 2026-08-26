"""Executable engine capability contracts shared by registry and renderer.

Semantic metadata may only claim an OpenCV parameter or region that the
processor explicitly implements.  Keeping this boundary separate from the
language registry prevents a newly declared semantic axis from being accepted
as renderable before its pixel handler exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from app.services.edit_schema import (
    EDIT_PARAMETER_SPECS,
    EDIT_REGION_MASK_TYPES,
    MANUAL_PARAMETER_KEYS,
)


class RenderContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EngineRenderContract:
    engine: str
    public_parameter_keys: frozenset[str]
    internal_parameter_keys: frozenset[str]
    region_masks: Mapping[str, str]

    @property
    def all_parameter_keys(self) -> frozenset[str]:
        return self.public_parameter_keys | self.internal_parameter_keys

    @property
    def regions(self) -> frozenset[str]:
        return frozenset(self.region_masks)

    def require_capability(
        self,
        *,
        parameter_key: str,
        regions: Iterable[str],
    ) -> None:
        if parameter_key not in self.public_parameter_keys:
            raise RenderContractError(
                f"{self.engine!r} has no public pixel handler for "
                f"parameter {parameter_key!r}"
            )
        declared_regions = frozenset(str(region) for region in regions)
        unsupported = declared_regions.difference(self.regions)
        if unsupported:
            raise RenderContractError(
                f"{self.engine!r} has no mask handler for regions "
                f"{sorted(unsupported)!r}"
            )


# These keys correspond one-for-one with the explicit calls in
# ``opencv_processor.create_opencv_result``.  Adding a schema/semantic axis is
# intentionally insufficient: this contract and the processor pipeline must be
# extended together before the registry may advertise OpenCV capability.
OPENCV_PUBLIC_PIXEL_HANDLERS = frozenset(
    {
        "exposure",
        "brightness",
        "contrast",
        "highlights",
        "shadows",
        "whites",
        "blacks",
        "saturation",
        "vibrance",
        "temperature",
        "white_balance_tint",
        "sharpen",
        "clarity",
        "dehaze",
        "vignette",
    }
)
OPENCV_INTERNAL_PIXEL_HANDLERS = frozenset({"reference_tint"})
OPENCV_REGION_MASK_CONTRACT: Mapping[str, str] = MappingProxyType(
    dict(EDIT_REGION_MASK_TYPES)
)

OPENCV_RENDER_CONTRACT = EngineRenderContract(
    engine="opencv",
    public_parameter_keys=OPENCV_PUBLIC_PIXEL_HANDLERS,
    internal_parameter_keys=OPENCV_INTERNAL_PIXEL_HANDLERS,
    region_masks=OPENCV_REGION_MASK_CONTRACT,
)

ENGINE_RENDER_CONTRACTS: Mapping[str, EngineRenderContract] = MappingProxyType(
    {"opencv": OPENCV_RENDER_CONTRACT}
)


def get_engine_render_contract(
    engine: str,
) -> EngineRenderContract | None:
    return ENGINE_RENDER_CONTRACTS.get(str(engine).strip().lower())


def validate_builtin_render_contracts() -> None:
    manual_keys = frozenset(MANUAL_PARAMETER_KEYS)
    if OPENCV_PUBLIC_PIXEL_HANDLERS != manual_keys:
        missing = manual_keys.difference(OPENCV_PUBLIC_PIXEL_HANDLERS)
        unexpected = OPENCV_PUBLIC_PIXEL_HANDLERS.difference(manual_keys)
        raise RenderContractError(
            "OpenCV public handler/schema mismatch: "
            f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
        )
    schema_keys = frozenset(EDIT_PARAMETER_SPECS)
    if OPENCV_RENDER_CONTRACT.all_parameter_keys != schema_keys:
        missing = schema_keys.difference(
            OPENCV_RENDER_CONTRACT.all_parameter_keys
        )
        unexpected = OPENCV_RENDER_CONTRACT.all_parameter_keys.difference(
            schema_keys
        )
        raise RenderContractError(
            "OpenCV total handler/schema mismatch: "
            f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}"
        )
    expected_regions = frozenset(EDIT_REGION_MASK_TYPES)
    if OPENCV_RENDER_CONTRACT.regions != expected_regions:
        raise RenderContractError(
            "OpenCV region/mask contract differs from edit schema"
        )


validate_builtin_render_contracts()


__all__ = [
    "ENGINE_RENDER_CONTRACTS",
    "EngineRenderContract",
    "OPENCV_INTERNAL_PIXEL_HANDLERS",
    "OPENCV_PUBLIC_PIXEL_HANDLERS",
    "OPENCV_REGION_MASK_CONTRACT",
    "OPENCV_RENDER_CONTRACT",
    "RenderContractError",
    "get_engine_render_contract",
    "validate_builtin_render_contracts",
]
