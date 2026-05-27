from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


EditIntentStrength = tuple[str, str]


def build_single_edit_plan(
    *,
    prompt: str,
    intent: str,
    strength: str,
) -> dict[str, Any]:
    return build_compound_edit_plan(
        prompt=prompt,
        intent_strengths=[(intent, strength)],
    )


def build_compound_edit_plan(
    *,
    prompt: str,
    intent_strengths: Iterable[EditIntentStrength],
) -> dict[str, Any]:
    edits = [
        {"intent": intent, "strength": strength}
        for intent, strength in intent_strengths
    ]
    return {
        "type": "edits",
        "prompt": prompt,
        "edits": edits,
        "preset_name": None,
        "raw_parameters": None,
    }


def build_preset_edit_plan(*, prompt: str, preset_name: str) -> dict[str, Any]:
    return {
        "type": "preset",
        "prompt": prompt,
        "edits": [],
        "preset_name": preset_name,
        "raw_parameters": None,
    }


def build_raw_parameter_edit_plan(
    *,
    prompt: str,
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "type": "raw_parameters",
        "prompt": prompt,
        "edits": [],
        "preset_name": None,
        "raw_parameters": dict(parameters or {}),
    }


def build_reference_edit_plan() -> dict[str, Any]:
    return {
        "type": "reference",
        "prompt": "",
        "edits": [],
        "preset_name": None,
        "raw_parameters": None,
    }
