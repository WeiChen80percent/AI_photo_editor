from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PHOTO_GIT_SCHEMA_VERSION = "photo_git_v1"
PHOTO_GIT_RENDERER_VERSION = "opencv_photo_git_v1"

PhotoGitOperation = Literal["merge", "selective_revert"]


class PhotoGitSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str | None = Field(default=None, max_length=40)
    mask_type: str | None = Field(default=None, max_length=60)
    parameters: list[str] = Field(default_factory=list, max_length=15)


class PhotoGitPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=80)
    operation: PhotoGitOperation
    target_edit_id: str = Field(min_length=1, max_length=80)
    source_edit_id: str | None = Field(default=None, max_length=80)
    revert_edit_id: str | None = Field(default=None, max_length=80)
    instruction: str = Field(default="", max_length=500)
    selectors: list[PhotoGitSelector] = Field(default_factory=list, max_length=12)
    resolutions: dict[str, str] = Field(default_factory=dict)


class PhotoGitExecutionRequest(PhotoGitPlanRequest):
    plan_hash: str = Field(min_length=64, max_length=64)


class PhotoGitCommitRequest(PhotoGitExecutionRequest):
    client_request_id: str = Field(min_length=1, max_length=128)
