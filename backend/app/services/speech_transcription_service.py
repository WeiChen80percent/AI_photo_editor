from __future__ import annotations

import io
import logging
import math
import os
import threading
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np


logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000
MAX_AUDIO_DURATION_SECONDS = 15.0
MIN_AUDIO_DURATION_SECONDS = 0.20
MAX_AUDIO_BYTES = 5 * 1024 * 1024
SUPPORTED_CONTENT_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "application/octet-stream",
}


class SpeechTranscriptionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class NormalizedAudio:
    samples: np.ndarray
    duration_ms: int
    source_sample_rate: int
    source_channels: int


@dataclass(frozen=True)
class SpeechTranscriptionResult:
    transcript: str
    language: str
    language_source: str
    model_id: str
    device: str
    dtype: str
    audio_duration_ms: int
    timings: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "language": self.language,
            "language_source": self.language_source,
            "model_id": self.model_id,
            "device": self.device,
            "dtype": self.dtype,
            "audio_duration_ms": self.audio_duration_ms,
            "timings": dict(self.timings),
        }


def normalize_wav_audio(
    data: bytes,
    *,
    content_type: str | None,
    filename: str | None,
) -> NormalizedAudio:
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    normalized_filename = (filename or "").strip().lower()
    if (
        normalized_content_type not in SUPPORTED_CONTENT_TYPES
        or (
            normalized_content_type == "application/octet-stream"
            and not normalized_filename.endswith((".wav", ".wave"))
        )
    ):
        raise SpeechTranscriptionError(
            "unsupported_audio_format",
            "目前只支援 Chrome 錄音使用的 PCM16 WAV 音訊。",
            status_code=415,
            details={"supported_formats": ["pcm16_wav"]},
        )
    if not data:
        raise SpeechTranscriptionError(
            "invalid_audio",
            "音訊檔是空的，請重新錄音。",
            status_code=400,
        )
    if len(data) > MAX_AUDIO_BYTES:
        raise SpeechTranscriptionError(
            "audio_too_large",
            "音訊檔超過 5 MB，請錄製較短的指令。",
            status_code=413,
            details={"maximum_bytes": MAX_AUDIO_BYTES},
        )

    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()
            frames = wav_file.readframes(frame_count)
    except (EOFError, wave.Error) as error:
        raise SpeechTranscriptionError(
            "invalid_audio",
            "無法讀取這段 WAV 音訊，請重新錄音。",
            status_code=400,
        ) from error

    if compression != "NONE" or sample_width != 2 or channels not in {1, 2}:
        raise SpeechTranscriptionError(
            "unsupported_audio_format",
            "音訊必須是單聲道或雙聲道的 PCM16 WAV。",
            status_code=415,
            details={
                "channels": channels,
                "sample_width_bytes": sample_width,
                "compression": compression,
            },
        )
    if sample_rate < 8_000 or sample_rate > 96_000 or frame_count <= 0:
        raise SpeechTranscriptionError(
            "invalid_audio",
            "音訊取樣率或內容無效，請重新錄音。",
            status_code=400,
        )

    duration_seconds = frame_count / sample_rate
    duration_ms = int(round(duration_seconds * 1000))
    if duration_seconds < MIN_AUDIO_DURATION_SECONDS:
        raise SpeechTranscriptionError(
            "invalid_audio",
            "錄音時間太短，請重新說出完整指令。",
            status_code=400,
            details={"minimum_duration_ms": int(MIN_AUDIO_DURATION_SECONDS * 1000)},
        )
    if duration_seconds > MAX_AUDIO_DURATION_SECONDS:
        raise SpeechTranscriptionError(
            "audio_too_long",
            "錄音超過 15 秒，請改說較短的修圖指令。",
            status_code=413,
            details={"maximum_duration_ms": int(MAX_AUDIO_DURATION_SECONDS * 1000)},
        )

    expected_bytes = frame_count * channels * sample_width
    if len(frames) != expected_bytes:
        raise SpeechTranscriptionError(
            "invalid_audio",
            "WAV 音訊內容不完整，請重新錄音。",
            status_code=400,
        )

    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if channels == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1)
    samples = pcm / 32768.0

    rms = math.sqrt(float(np.mean(np.square(samples, dtype=np.float64))))
    peak = float(np.max(np.abs(samples)))
    if rms < 0.0005 or peak < 0.001:
        raise SpeechTranscriptionError(
            "no_speech",
            "沒有偵測到有效語音，請靠近麥克風後重試。",
            status_code=422,
        )

    if sample_rate != TARGET_SAMPLE_RATE:
        target_length = max(1, int(round(len(samples) * TARGET_SAMPLE_RATE / sample_rate)))
        source_positions = np.arange(len(samples), dtype=np.float64)
        target_positions = np.linspace(
            0,
            len(samples) - 1,
            num=target_length,
            dtype=np.float64,
        )
        samples = np.interp(target_positions, source_positions, samples).astype(np.float32)
    else:
        samples = samples.astype(np.float32, copy=False)

    return NormalizedAudio(
        samples=np.ascontiguousarray(samples),
        duration_ms=duration_ms,
        source_sample_rate=sample_rate,
        source_channels=channels,
    )


class WhisperTranscriptionService:
    def __init__(
        self,
        *,
        model_id: str | None = None,
        requested_device: str | None = None,
        requested_dtype: str | None = None,
    ) -> None:
        self.model_id = model_id or os.getenv(
            "AI_PHOTO_ASR_MODEL", "openai/whisper-large-v3-turbo"
        ).strip()
        self.requested_device = (
            requested_device or os.getenv("AI_PHOTO_ASR_DEVICE", "auto")
        ).strip().lower()
        self.requested_dtype = (
            requested_dtype or os.getenv("AI_PHOTO_ASR_DTYPE", "auto")
        ).strip().lower()
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._processor: Any | None = None
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device = "unloaded"
        self._dtype_name = "unloaded"

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    def transcribe(
        self,
        data: bytes,
        *,
        content_type: str | None,
        filename: str | None,
        language_hint: str | None = None,
    ) -> SpeechTranscriptionResult:
        total_started = time.perf_counter()
        preprocess_started = time.perf_counter()
        audio = normalize_wav_audio(
            data,
            content_type=content_type,
            filename=filename,
        )
        preprocess_ms = _elapsed_ms(preprocess_started)

        with self._inference_lock:
            inference_started = time.perf_counter()
            self._ensure_loaded()
            transcript, resolved_language = self._run_inference(
                audio.samples,
                language_hint=language_hint,
            )
            inference_ms = _elapsed_ms(inference_started)

        transcript = normalize_transcript_text(transcript, resolved_language)
        if not transcript:
            raise SpeechTranscriptionError(
                "no_speech",
                "沒有辨識到可用文字，請重新錄音。",
                status_code=422,
            )

        return SpeechTranscriptionResult(
            transcript=transcript,
            language=resolved_language,
            language_source=(
                "hint"
                if language_hint
                else "detected"
                if resolved_language != "auto"
                else "auto"
            ),
            model_id=self.model_id,
            device=self._device,
            dtype=self._dtype_name,
            audio_duration_ms=audio.duration_ms,
            timings={
                "preprocess_ms": preprocess_ms,
                "inference_ms": inference_ms,
                "total_ms": _elapsed_ms(total_started),
            },
        )

    def _ensure_loaded(self) -> None:
        if self.is_loaded:
            return
        with self._load_lock:
            if self.is_loaded:
                return
            try:
                import torch
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

                device = self._resolve_device(torch)
                dtype, dtype_name = self._resolve_dtype(torch, device)
                processor = AutoProcessor.from_pretrained(self.model_id)
                model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    self.model_id,
                    dtype=dtype,
                )
                model.to(device)
                model.eval()
            except SpeechTranscriptionError:
                raise
            except Exception as error:
                logger.exception("Unable to load local Whisper model %s", self.model_id)
                self._processor = None
                self._model = None
                raise SpeechTranscriptionError(
                    "model_unavailable",
                    "本機語音模型目前無法載入，請確認模型檔與執行環境。",
                    status_code=503,
                    details={"model_id": self.model_id},
                ) from error

            self._torch = torch
            self._processor = processor
            self._model = model
            self._device = device
            self._dtype_name = dtype_name
            logger.info(
                "Local Whisper model is ready: model=%s device=%s dtype=%s",
                self.model_id,
                device,
                dtype_name,
            )

    def _resolve_device(self, torch: Any) -> str:
        requested = self.requested_device
        if requested not in {"auto", "cpu", "cuda"}:
            raise SpeechTranscriptionError(
                "model_unavailable",
                "AI_PHOTO_ASR_DEVICE 必須是 auto、cpu 或 cuda。",
                status_code=503,
                details={"requested_device": requested},
            )
        if requested == "cpu":
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        if requested == "cuda":
            logger.warning("CUDA was requested for ASR but is unavailable; using CPU.")
        return "cpu"

    def _resolve_dtype(self, torch: Any, device: str) -> tuple[Any, str]:
        requested = self.requested_dtype
        if requested == "auto":
            return (torch.float16, "float16") if device == "cuda" else (
                torch.float32,
                "float32",
            )
        if requested in {"float16", "fp16"}:
            if device != "cuda":
                raise SpeechTranscriptionError(
                    "model_unavailable",
                    "CPU 模式不支援此專案的 float16 Whisper 設定，請改用 auto 或 float32。",
                    status_code=503,
                )
            return torch.float16, "float16"
        if requested in {"float32", "fp32"}:
            return torch.float32, "float32"
        raise SpeechTranscriptionError(
            "model_unavailable",
            "AI_PHOTO_ASR_DTYPE 必須是 auto、float16 或 float32。",
            status_code=503,
            details={"requested_dtype": requested},
        )

    def _run_inference(
        self,
        samples: np.ndarray,
        *,
        language_hint: str | None,
    ) -> tuple[str, str]:
        assert self._torch is not None
        assert self._processor is not None
        assert self._model is not None
        try:
            inputs = self._processor(
                samples,
                sampling_rate=TARGET_SAMPLE_RATE,
                return_tensors="pt",
            )
            input_features = inputs.input_features.to(self._device)
            if self._dtype_name == "float16":
                input_features = input_features.to(self._torch.float16)
            resolved_language = language_hint or self._detect_language(
                input_features
            )
            generation_options: dict[str, Any] = {
                "task": "transcribe",
            }
            if resolved_language != "auto":
                generation_options["language"] = resolved_language
            with self._torch.inference_mode():
                generated_ids = self._model.generate(
                    input_features,
                    **generation_options,
                )
            decoded = self._processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )
            return (decoded[0] if decoded else "", resolved_language)
        except SpeechTranscriptionError:
            raise
        except Exception as error:
            logger.exception("Local Whisper inference failed")
            raise SpeechTranscriptionError(
                "transcription_failed",
                "語音辨識失敗，請稍後重新錄音。",
                status_code=500,
            ) from error

    def _detect_language(self, input_features: Any) -> str:
        assert self._model is not None
        language_ids = self._model.detect_language(input_features=input_features)
        language_id = int(language_ids[0].item())
        language_tokens = getattr(
            self._model.generation_config,
            "lang_to_id",
            {},
        )
        for token, token_id in language_tokens.items():
            if int(token_id) != language_id:
                continue
            if token.startswith("<|") and token.endswith("|>"):
                return token[2:-2]
            return token
        logger.warning("Whisper returned an unknown language token id: %s", language_id)
        return "auto"


@lru_cache(maxsize=1)
def _traditional_chinese_converter() -> Any:
    from opencc import OpenCC

    return OpenCC("s2twp")


def normalize_transcript_text(transcript: str, language: str) -> str:
    normalized = transcript.strip()
    if normalized and language == "zh":
        normalized = _traditional_chinese_converter().convert(normalized)
    return normalized.strip()


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


@lru_cache(maxsize=1)
def get_default_speech_transcription_service() -> WhisperTranscriptionService:
    return WhisperTranscriptionService()
