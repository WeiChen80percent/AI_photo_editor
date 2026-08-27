from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


COMMAND_SCHEMA_VERSION = "command_plan_v1"

CommandType = Literal[
    "edit_prompt",
    "manual_adjust",
    "apply_style",
    "photo_git_merge",
    "photo_git_revert",
    "unknown",
]
CommandDisposition = Literal[
    "ready",
    "clarification_required",
    "conflict",
    "unsupported",
]
CommandConfirmationPolicy = Literal[
    "execute_after_apply",
    "preview_then_confirm",
    "none",
]


class CommandPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=80)
    selected_edit_id: str | None = Field(default=None, max_length=80)
    locale: str | None = Field(default=None, max_length=20)


class CommandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    raw_text: str = Field(min_length=1)


class CommandLocalizedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zh: str = Field(min_length=1)
    en: str = Field(min_length=1)


class CommandClarificationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=1, max_length=80)
    label: CommandLocalizedText
    action: dict[str, Any] = Field(default_factory=dict)


class CommandClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    question: CommandLocalizedText
    options: list[CommandClarificationOption] = Field(
        default_factory=list,
        max_length=5,
    )


class ResolvedCommandPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = COMMAND_SCHEMA_VERSION
    disposition: CommandDisposition
    command_type: CommandType
    original_instruction: str = Field(min_length=1, max_length=500)
    session_id: str | None = None
    selected_edit_id: str | None = None
    target_edit_id: str | None = None
    source_edit_id: str | None = None
    revert_edit_id: str | None = None
    normalized_slots: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, CommandEvidence] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    confirmation_policy: CommandConfirmationPolicy
    history_fingerprint: str | None = None
    plan_hash: str = Field(min_length=64, max_length=64)
    parser_source: str = Field(min_length=1, max_length=80)
    summary: CommandLocalizedText
    clarification: CommandClarification | None = None


__all__ = [
    "COMMAND_SCHEMA_VERSION",
    "CommandClarification",
    "CommandClarificationOption",
    "CommandDisposition",
    "CommandEvidence",
    "CommandLocalizedText",
    "CommandPlanRequest",
    "CommandType",
    "ResolvedCommandPlan",
]
