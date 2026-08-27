from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.services.speech_transcription_service import (
    MAX_AUDIO_BYTES,
    SpeechTranscriptionError,
    WhisperTranscriptionService,
    get_default_speech_transcription_service,
)


router = APIRouter(prefix="/speech", tags=["speech"])


def _transcription_timeout_seconds() -> float:
    raw_value = os.getenv("AI_PHOTO_ASR_TIMEOUT_SECONDS", "60").strip()
    try:
        value = float(raw_value)
    except ValueError:
        return 60.0
    return min(max(value, 5.0), 180.0)


def _http_error(error: SpeechTranscriptionError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.code,
            "message": error.message,
            **error.details,
        },
    )


@router.post("/transcribe")
async def transcribe_speech(
    audio: UploadFile = File(...),
    language_hint: str | None = Form(None),
    service: WhisperTranscriptionService = Depends(
        get_default_speech_transcription_service
    ),
):
    normalized_language_hint = (language_hint or "").strip().lower() or None
    if normalized_language_hint not in {None, "zh", "en"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_language_hint",
                "message": "language_hint 目前只接受 zh、en 或省略自動偵測。",
            },
        )

    try:
        data = await audio.read(MAX_AUDIO_BYTES + 1)
    finally:
        await audio.close()

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                service.transcribe,
                data,
                content_type=audio.content_type,
                filename=audio.filename,
                language_hint=normalized_language_hint,
            ),
            timeout=_transcription_timeout_seconds(),
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(
            status_code=504,
            detail={
                "code": "transcription_timeout",
                "message": "語音辨識等待過久，請稍後重新錄音。",
            },
        ) from error
    except SpeechTranscriptionError as error:
        raise _http_error(error) from error

    return result.to_dict()
