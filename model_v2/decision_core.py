from __future__ import annotations

import argparse
import base64
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps


OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_PLANNER_MODEL = "qwen2.5:7b"
DEFAULT_VLM_MODEL = "gemma3:4b"
DEFAULT_VLM_TIMEOUT = 120
DEFAULT_VISION_MAX_SIDE = 384
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
DEFAULT_OUTPUT_EXTENSION = ".jpg"


TOOL_LIMITS: dict[str, dict[str, tuple[float, float]]] = {
    "basic_tone": {
        "exposure_ev": (-1.5, 1.5),
        "contrast": (-0.4, 0.5),
        "black_level": (-0.18, 0.18),
        "white_level": (-0.18, 0.18),
        "highlights": (-0.45, 0.45),
        "shadows": (-0.45, 0.45),
    },
    "white_balance": {
        "temperature": (-1.0, 1.0),
        "tint": (-1.0, 1.0),
    },
    "color_intensity": {
        "saturation": (-0.6, 0.6),
        "vibrance": (-0.6, 0.6),
    },
    "channel_balance": {
        "red": (-0.22, 0.22),
        "green": (-0.22, 0.22),
        "blue": (-0.22, 0.22),
    },
    "regional_color_balance": {
        "shadow_hue": (0.0, 360.0),
        "shadow_chroma": (-1.0, 1.0),
        "shadow_luminance": (-1.0, 1.0),
        "shadow_saturation": (-1.0, 1.0),
        "shadow_brilliance": (-1.0, 1.0),
        "highlight_hue": (0.0, 360.0),
        "highlight_chroma": (-1.0, 1.0),
        "highlight_luminance": (-1.0, 1.0),
        "highlight_saturation": (-1.0, 1.0),
        "highlight_brilliance": (-1.0, 1.0),
    },
    "tone_curve": {
        "shadows": (-0.35, 0.35),
        "midtones": (-0.35, 0.35),
        "highlights": (-0.35, 0.35),
    },
    "vignette": {
        "correction": (-0.5, 0.5),
    },
    "hsl_zone": {
        "hue_shift": (-0.15, 0.15),
        "saturation": (-0.5, 0.5),
        "luminance": (-0.35, 0.35),
    },
    "sharpen": {
        "amount": (0.0, 0.8),
    },
}


HSL_ZONE_CENTERS = {
    "red": 0.0,
    "orange": 0.08,
    "yellow": 0.16,
    "green": 0.33,
    "cyan": 0.5,
    "blue": 0.62,
    "purple": 0.75,
    "magenta": 0.88,
}


@dataclass
class AgentConfig:
    image_path: Path
    output_path: Path
    prompt: str
    planner_model: str
    vlm_model: str | None
    ollama_url: str
    ollama_timeout: int
    vlm_timeout: int
    use_ollama: bool
    max_side: int
    vision_max_side: int
    metadata_path: Path | None


def main() -> int:
    if len(sys.argv) == 1:
        try:
            run_guided_session()
        except Exception as exc:
            print(f"[FAILED] {exc}", file=sys.stderr)
            return 1
        return 0

    args = parse_args()
    config = AgentConfig(
        image_path=resolve_input_path(args.image).resolve(),
        output_path=ensure_output_extension(args.output).resolve(),
        prompt=args.prompt.strip(),
        planner_model=args.planner_model,
        vlm_model=None if args.no_vlm else normalize_optional_model(args.vlm_model),
        ollama_url=args.ollama_url,
        ollama_timeout=args.ollama_timeout,
        vlm_timeout=args.vlm_timeout,
        use_ollama=not args.no_ollama,
        max_side=args.max_side,
        vision_max_side=args.vision_max_side,
        metadata_path=args.metadata.resolve() if args.metadata else None,
    )

    if args.interactive:
        try:
            run_interactive(config)
        except Exception as exc:
            print(f"[FAILED] {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        result = run_agent(config)
    except Exception as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Photo editing decision core. It keeps file paths out of the LLM, asks Ollama "
            "only for a JSON edit plan, validates the plan, and can export a PIL/OpenCV-style preview image."
        )
    )
    parser.add_argument("--image", type=Path, required=True, help="Input image path.")
    parser.add_argument("--output", type=Path, default=Path("output.jpg"))
    parser.add_argument(
        "--prompt",
        default="Restore this photo to a natural, clean, professional, and understated tone.",
    )
    parser.add_argument(
        "--planner-model",
        default=DEFAULT_PLANNER_MODEL,
        help="Text model that converts the user prompt and diagnostics into an executable JSON tool plan.",
    )
    parser.add_argument(
        "--vlm-model",
        default=DEFAULT_VLM_MODEL,
        help="Optional vision model for image diagnosis. The user prompt still drives the planner.",
    )
    parser.add_argument("--no-vlm", action="store_true", help="Skip VLM diagnosis and use local numeric diagnostics only.")
    parser.add_argument("--ollama-url", default=OLLAMA_URL)
    parser.add_argument(
        "--ollama-timeout",
        type=int,
        default=240,
        help="Seconds to wait for each Ollama request. Larger local VLMs can need longer on first load.",
    )
    parser.add_argument(
        "--vlm-timeout",
        type=int,
        default=DEFAULT_VLM_TIMEOUT,
        help="Seconds to wait for optional VLM diagnosis before continuing to the prompt planner.",
    )
    parser.add_argument(
        "--no-ollama",
        action="store_true",
        help="Use deterministic local diagnostics/planning only.",
    )
    parser.add_argument("--max-side", type=int, default=1600)
    parser.add_argument(
        "--vision-max-side",
        type=int,
        default=DEFAULT_VISION_MAX_SIDE,
        help="Maximum image side sent to the VLM. Smaller is faster; output rendering still uses --max-side.",
    )
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Keep accepting user edit prompts. Each turn uses the previous output as the next input.",
    )
    return parser.parse_args()


def run_guided_session() -> None:
    script_dir = Path(__file__).resolve().parent
    default_image = script_dir / "input.jpg"
    default_output = script_dir / "output.jpg"
    default_prompt = "Restore this photo to a natural, clean, professional look."

    print("=== Decision Core quick mode ===")
    print("Press Enter to accept the default shown in brackets.")
    print("After each edit, type another request to keep editing, or type 'exit' to quit.")

    image_path = prompt_input_path("Image path", default_image, Path("input.jpg"), script_dir)
    output_path = prompt_output_path("Output path", default_output, Path("output.jpg"), script_dir)
    use_vlm_text = input("Use VLM diagnosis? [y/N]: ").strip().lower()
    vlm_model = None
    vlm_timeout = DEFAULT_VLM_TIMEOUT
    vision_max_side = DEFAULT_VISION_MAX_SIDE
    if use_vlm_text in {"y", "yes"}:
        vlm_model = input(f"VLM model [{DEFAULT_VLM_MODEL}]: ").strip() or DEFAULT_VLM_MODEL
        vlm_timeout = prompt_int("VLM timeout seconds", DEFAULT_VLM_TIMEOUT)
        vision_max_side = prompt_int("VLM image max side", DEFAULT_VISION_MAX_SIDE)

    base_config = AgentConfig(
        image_path=image_path.resolve(),
        output_path=output_path.resolve(),
        prompt=default_prompt,
        planner_model=DEFAULT_PLANNER_MODEL,
        vlm_model=vlm_model,
        ollama_url=OLLAMA_URL,
        ollama_timeout=240,
        vlm_timeout=vlm_timeout,
        use_ollama=True,
        max_side=1600,
        vision_max_side=vision_max_side,
        metadata_path=None,
    )

    current_input = image_path.resolve()
    turn = 1
    while True:
        label = f"Edit request [{default_prompt}]" if turn == 1 else "Edit request"
        prompt = input(f"{label}: ").strip()
        if prompt.lower() in {"exit", "quit", "q"}:
            print("Exiting Decision Core.")
            return
        if not prompt:
            if turn == 1:
                prompt = default_prompt
            else:
                continue

        turn_output = output_path.resolve() if turn == 1 else numbered_output_path(output_path.resolve(), turn)
        turn_config = replace(
            base_config,
            image_path=current_input,
            output_path=turn_output,
            prompt=prompt,
        )
        result = run_agent(turn_config)
        print_run_summary(result)
        current_input = turn_output
        turn += 1


def prompt_path(
    label: str,
    default: Path,
    display_default: Path | None = None,
    base_dir: Path | None = None,
) -> Path:
    shown_default = display_default or default
    raw = input(f"{label} [{shown_default}]: ").strip()
    if not raw:
        return default
    path = Path(raw)
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    return path


def prompt_input_path(
    label: str,
    default: Path,
    display_default: Path | None = None,
    base_dir: Path | None = None,
) -> Path:
    return resolve_input_path(prompt_path(label, default, display_default, base_dir))


def prompt_output_path(
    label: str,
    default: Path,
    display_default: Path | None = None,
    base_dir: Path | None = None,
) -> Path:
    return ensure_output_extension(prompt_path(label, default, display_default, base_dir))


def resolve_input_path(path: Path) -> Path:
    if path.suffix:
        return path
    if path.exists():
        return path
    for extension in IMAGE_EXTENSIONS:
        candidate = path.with_suffix(extension)
        if candidate.exists():
            return candidate
    return path.with_suffix(DEFAULT_OUTPUT_EXTENSION)


def ensure_output_extension(path: Path) -> Path:
    if path.suffix:
        return path
    return path.with_suffix(DEFAULT_OUTPUT_EXTENSION)


def prompt_int(label: str, default: int) -> int:
    raw = input(f"{label} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"Invalid number, using {default}.")
        return default


def normalize_optional_model(model: str | None) -> str | None:
    if model is None:
        return None
    clean = model.strip()
    if clean.lower() in {"", "none", "null", "off", "false", "0"}:
        return None
    return clean


def run_agent(config: AgentConfig) -> dict[str, Any]:
    if not config.image_path.exists():
        raise FileNotFoundError(f"Input image does not exist: {config.image_path}")
    if not config.prompt:
        raise ValueError("Prompt cannot be empty.")

    image = load_image(config.image_path, config.max_side)
    local_diagnostics = compute_image_diagnostics(image)
    vlm_diagnostics = None
    vlm_raw = None
    planner_raw = None

    if config.use_ollama and config.vlm_model:
        try:
            vlm_raw = call_vlm_diagnostics(
                config.ollama_url,
                config.vlm_model,
                config.image_path,
                local_diagnostics,
                config.vision_max_side,
                config.vlm_timeout,
            )
            vlm_diagnostics = sanitize_vlm_diagnostics(parse_json_object(vlm_raw))
        except Exception as exc:
            vlm_diagnostics = {
                "status": "vlm_failed_local_diagnostics_used",
                "error": str(exc),
            }

    if config.use_ollama:
        try:
            planner_raw = call_planner(
                config.ollama_url,
                config.planner_model,
                config.prompt,
                local_diagnostics,
                vlm_diagnostics,
                config.ollama_timeout,
            )
            plan = parse_json_object(planner_raw)
            diagnostics = plan.get("diagnostics")
            if isinstance(diagnostics, dict):
                vlm_diagnostics = diagnostics
        except Exception as exc:
            plan = build_fallback_plan(config.prompt, local_diagnostics, str(exc))
    else:
        plan = build_fallback_plan(config.prompt, local_diagnostics, "Ollama disabled.")

    validated_plan = validate_plan(plan, config.prompt, local_diagnostics)
    output_image, applied_operations = apply_operations(image, validated_plan["operations"])

    if not applied_operations:
        raise RuntimeError(
            "No effective edit operations remained after validation. "
            "The agent refused to produce a no-op output."
        )

    save_image(output_image, config.output_path)
    if not config.output_path.exists() or config.output_path.stat().st_size == 0:
        raise RuntimeError(f"Export failed: {config.output_path}")

    metadata = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input_path": str(config.image_path),
        "output_path": str(config.output_path.resolve()),
        "prompt": config.prompt,
        "planner_model": config.planner_model,
        "vlm_model": config.vlm_model,
        "agent_mode": "hybrid_optional_vlm_text_planner" if config.use_ollama else "local_fallback_only",
        "local_diagnostics": local_diagnostics,
        "vlm_diagnostics": vlm_diagnostics,
        "vlm_raw": vlm_raw,
        "planner_raw": planner_raw,
        "validated_plan": validated_plan,
        "applied_operations": applied_operations,
    }
    metadata_path = config.metadata_path or config.output_path.with_suffix(
        config.output_path.suffix + ".metadata.json"
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "status": "success",
        "input_path": str(config.image_path),
        "output_path": str(config.output_path.resolve()),
        "metadata_path": str(metadata_path.resolve()),
        "vlm_summary": summarize_vlm(vlm_diagnostics),
        "applied_tools": [op["tool"] for op in applied_operations],
        "operation_details": summarize_operations(applied_operations),
        "intent_summary": validated_plan.get("intent_summary", ""),
        "warnings": validated_plan.get("warnings", []),
    }
    return {"summary": summary, "metadata": metadata}


def summarize_vlm(vlm_diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    if vlm_diagnostics is None:
        return {"used": False, "status": "not_requested"}
    if "error" in vlm_diagnostics:
        return {
            "used": True,
            "status": str(vlm_diagnostics.get("status") or "failed"),
            "error": str(vlm_diagnostics.get("error") or ""),
        }
    keys = [
        "exposure_status",
        "contrast_status",
        "white_balance_cast",
        "saturation_status",
        "vignette_status",
        "recommended_actions",
        "program_interpretation",
        "normalization_warnings",
        "evidence",
        "confidence",
    ]
    return {
        "used": True,
        "status": "success",
        **{key: vlm_diagnostics.get(key) for key in keys if key in vlm_diagnostics},
    }


def summarize_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "tool": operation.get("tool"),
            "params": operation.get("params", {}),
            "reason": operation.get("reason", ""),
            "mean_absolute_delta": operation.get("mean_absolute_delta"),
        }
        for operation in operations
    ]


def print_run_summary(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def run_interactive(config: AgentConfig) -> None:
    current_input = config.image_path
    print("=== Decision Core interactive mode ===")
    print(f"Input image: {current_input}")
    print("Type an edit request, or type 'exit' to quit.")

    turn = 1
    while True:
        try:
            prompt = input("Edit request: ").strip()
        except EOFError:
            print()
            return

        if prompt.lower() in {"exit", "quit", "q"}:
            print("Exiting Decision Core.")
            return
        if not prompt:
            continue

        turn_output = numbered_output_path(config.output_path, turn)
        turn_config = replace(
            config,
            image_path=current_input,
            output_path=turn_output,
            prompt=prompt,
            metadata_path=None,
        )
        result = run_agent(turn_config)
        print_run_summary(result)
        current_input = turn_output
        turn += 1


def numbered_output_path(path: Path, turn: int) -> Path:
    suffix = path.suffix or ".jpg"
    return path.with_name(f"{path.stem}_{turn:02d}{suffix}")


def load_image(path: Path, max_side: int) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image.copy()


def save_image(image: Image.Image, path: Path) -> None:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"Output path must be an image file, not {path.suffix!r}. "
            "Use output.jpg, output.png, or simply output."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        image.convert("RGB").save(path, format="JPEG", quality=95, subsampling=0, optimize=True)
    elif path.suffix.lower() == ".png":
        image.convert("RGB").save(path, format="PNG", optimize=True)
    else:
        image.convert("RGB").save(path)


def pil_to_float(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def float_to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8), mode="RGB")


def luminance(arr: np.ndarray) -> np.ndarray:
    return arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722


def saturation(arr: np.ndarray) -> np.ndarray:
    return np.max(arr, axis=2) - np.min(arr, axis=2)


def compute_image_diagnostics(image: Image.Image) -> dict[str, Any]:
    arr = pil_to_float(image)
    luma = luminance(arr)
    sat = saturation(arr)
    rgb_mean = np.mean(arr, axis=(0, 1))
    rgb_balance = rgb_mean / max(float(np.mean(rgb_mean)), 1e-6)
    edge_proxy = float(np.std(luma - gaussian_blur_array(luma, sigma=2.0)))
    height, width = luma.shape
    border = max(8, int(min(height, width) * 0.08))
    border_mask = np.zeros_like(luma, dtype=bool)
    border_mask[:border, :] = True
    border_mask[-border:, :] = True
    border_mask[:, :border] = True
    border_mask[:, -border:] = True
    center_mask = ~border_mask
    border_luma = float(np.mean(luma[border_mask]))
    center_luma = float(np.mean(luma[center_mask])) if np.any(center_mask) else float(np.mean(luma))

    blue_cast_score = float(rgb_balance[2] - rgb_balance[0])
    yellow_warm_score = float(rgb_balance[0] - rgb_balance[2])
    green_cast_score = float(rgb_balance[1] - (rgb_balance[0] + rgb_balance[2]) / 2.0)

    return {
        "mean_luma": round(float(np.mean(luma)), 5),
        "std_luma": round(float(np.std(luma)), 5),
        "mean_saturation": round(float(np.mean(sat)), 5),
        "clip_low_ratio": round(float(np.mean(arr <= 1.0 / 255.0)), 5),
        "clip_high_ratio": round(float(np.mean(arr >= 254.0 / 255.0)), 5),
        "rgb_mean": [round(float(v), 5) for v in rgb_mean],
        "rgb_balance": [round(float(v), 5) for v in rgb_balance],
        "blue_cast_score": round(blue_cast_score, 5),
        "yellow_warm_score": round(yellow_warm_score, 5),
        "green_cast_score": round(green_cast_score, 5),
        "edge_proxy": round(edge_proxy, 5),
        "border_luma": round(border_luma, 5),
        "center_luma": round(center_luma, 5),
        "vignette_score": round(center_luma - border_luma, 5),
        "heuristic_findings": heuristic_findings(
            mean_luma=float(np.mean(luma)),
            std_luma=float(np.std(luma)),
            mean_saturation=float(np.mean(sat)),
            blue_cast_score=blue_cast_score,
            yellow_warm_score=yellow_warm_score,
            green_cast_score=green_cast_score,
            vignette_score=center_luma - border_luma,
        ),
    }


def heuristic_findings(
    *,
    mean_luma: float,
    std_luma: float,
    mean_saturation: float,
    blue_cast_score: float,
    yellow_warm_score: float,
    green_cast_score: float,
    vignette_score: float,
) -> list[str]:
    findings: list[str] = []
    if mean_luma < 0.22:
        findings.append("under_exposed")
    elif mean_luma > 0.62:
        findings.append("over_exposed")
    if std_luma < 0.13:
        findings.append("low_global_contrast")
    elif std_luma > 0.28:
        findings.append("high_global_contrast")
    if mean_saturation < 0.12:
        findings.append("low_saturation")
    elif mean_saturation > 0.31:
        findings.append("high_saturation")
    if blue_cast_score > 0.16:
        findings.append("cool_blue_cast")
    elif yellow_warm_score > 0.16:
        findings.append("warm_yellow_cast")
    if green_cast_score > 0.12:
        findings.append("green_cast")
    elif green_cast_score < -0.12:
        findings.append("magenta_cast")
    if vignette_score > 0.08:
        findings.append("edge_vignette")
    return findings


def gaussian_blur_array(arr: np.ndarray, sigma: float) -> np.ndarray:
    image = Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8), mode="L")
    blurred = image.filter(ImageFilter.GaussianBlur(radius=sigma))
    return np.asarray(blurred, dtype=np.float32) / 255.0


def call_vlm_diagnostics(
    ollama_url: str,
    model: str,
    image_path: Path,
    local_diagnostics: dict[str, Any],
    vision_max_side: int,
    timeout: int,
) -> str:
    prompt = f"""
You are a technical photo diagnostician. Analyze the actual image, not the example text.
Return one JSON object only. Do not use markdown.

Use these exact enumerations:
- exposure_status: under_exposed, normal, over_exposed, mixed
- contrast_status: low, normal, high
- white_balance_cast: cool_blue, warm_yellow, green, magenta, neutral, mixed
- saturation_status: low, normal, high
- vignette_status: none, mild, strong

Important:
- Do not output a generic template.
- If the image is cool/blue, recommend warmer correction.
- If it is warm/yellow, recommend cooler correction.
- Positive exposure means brighter; negative exposure means darker.
- Mention confidence from 0.0 to 1.0 for each judgment.

Local numeric diagnostics from the program:
{json.dumps(local_diagnostics, ensure_ascii=False)}

Required JSON shape:
{{
  "exposure_status": "...",
  "contrast_status": "...",
  "white_balance_cast": "...",
  "saturation_status": "...",
  "vignette_status": "...",
  "evidence": ["short observation 1", "short observation 2"],
  "recommended_actions": ["action token 1", "action token 2"],
  "confidence": {{
    "exposure": 0.0,
    "white_balance": 0.0,
    "contrast": 0.0,
    "saturation": 0.0
  }}
}}
""".strip()
    return call_ollama_chat(
        ollama_url=ollama_url,
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt,
                "images": [base64_image(image_path, vision_max_side)],
            }
        ],
        temperature=0.0,
        num_predict=320,
        timeout=timeout,
    )


def call_multimodal_planner(
    ollama_url: str,
    model: str,
    image_path: Path,
    prompt: str,
    local_diagnostics: dict[str, Any],
    vision_max_side: int,
    timeout: int,
) -> str:
    planner_prompt = f"""
You are a conservative professional photo repair agent.
Look at the actual image and produce one executable edit plan.
Return one JSON object only. Do not use markdown. Do not choose file paths.

The runtime already controls input/output paths. You are forbidden to mention or invent filenames.
The user prompt describes the desired correction, not the input filename.

Goals:
- Restore a natural, clean, professional, understated photo unless the user explicitly asks for a style.
- Diagnose the actual image instead of repeating a generic template.
- Prefer corrective edits over creative grading.
- Avoid no-op operations.
- Positive exposure_ev means brighten. Negative exposure_ev means darken.
- Negative black_level lowers lifted blacks and makes shadow blacks cleaner. Positive black_level lifts blacks.
- For cool/blue cast, use white_balance temperature > 0.
- For warm/yellow cast, use white_balance temperature < 0.
- For green cast, use white_balance tint > 0.
- For magenta cast, use white_balance tint < 0.
- Use tone_curve for gamma, non-linear midtone curve, or tonal response repair.
- Use highlight/shadow and tone_curve only when tone distribution needs it.
- Use vignette only when image corners are visibly darker/brighter than the center.
- Use hsl_zone only to neutralize a specific color cast or explicit color-zone request such as sky, skin, foliage, blues, greens, reds, or yellows.
- Use regional_color_balance for separate shadow/highlight color casts, not for global white balance.

Allowed tools and parameter ranges:
{json.dumps(TOOL_LIMITS, ensure_ascii=False)}

Allowed hsl_zone color values:
{list(HSL_ZONE_CENTERS)}

User prompt:
{prompt}

Local numeric diagnostics:
{json.dumps(local_diagnostics, ensure_ascii=False)}

Required JSON shape:
{{
  "diagnostics": {{
    "exposure_status": "under_exposed | normal | over_exposed | mixed",
    "contrast_status": "low | normal | high",
    "white_balance_cast": "cool_blue | warm_yellow | green | magenta | neutral | mixed",
    "saturation_status": "low | normal | high",
    "vignette_status": "none | mild | strong",
    "confidence": 0.0,
    "evidence": ["short concrete observation"]
  }},
  "intent_summary": "short summary",
  "operations": [
    {{
      "tool": "basic_tone",
      "params": {{
        "exposure_ev": 0.0,
        "contrast": 0.0,
        "black_level": 0.0,
        "white_level": 0.0,
        "highlights": 0.0,
        "shadows": 0.0
      }},
      "reason": "why this is needed"
    }},
    {{
      "tool": "white_balance",
      "params": {{"temperature": 0.0, "tint": 0.0}},
      "reason": "why this is needed"
    }},
    {{
      "tool": "tone_curve",
      "params": {{"shadows": 0.0, "midtones": 0.0, "highlights": 0.0}},
      "reason": "why this is needed"
    }},
    {{
      "tool": "hsl_zone",
      "params": {{"color": "blue", "hue_shift": 0.0, "saturation": 0.0, "luminance": 0.0}},
      "reason": "only when a specific color zone should be adjusted"
    }},
    {{
      "tool": "regional_color_balance",
      "params": {{
        "shadow_hue": 0.0,
        "shadow_chroma": 0.0,
        "shadow_luminance": 0.0,
        "shadow_saturation": 0.0,
        "shadow_brilliance": 0.0,
        "highlight_hue": 0.0,
        "highlight_chroma": 0.0,
        "highlight_luminance": 0.0,
        "highlight_saturation": 0.0,
        "highlight_brilliance": 0.0
      }},
      "reason": "only when shadows and highlights have different color casts"
    }}
  ],
  "warnings": []
}}
""".strip()
    return call_ollama_chat(
        ollama_url=ollama_url,
        model=model,
        messages=[
            {
                "role": "user",
                "content": planner_prompt,
                "images": [base64_image(image_path, vision_max_side)],
            }
        ],
        temperature=0.0,
        format_json=True,
        timeout=timeout,
    )


def call_planner(
    ollama_url: str,
    model: str,
    prompt: str,
    local_diagnostics: dict[str, Any],
    vlm_diagnostics: dict[str, Any] | None,
    timeout: int,
) -> str:
    planner_prompt = f"""
You are a conservative professional photo repair planner.
Return one JSON object only. Do not use markdown. Do not choose file paths.

The runtime already controls input/output paths. You are forbidden to mention or invent filenames.

Goal:
- Repair the photo according to the user prompt and diagnostics.
- Prefer natural, clean, professional, understated corrections.
- Do not add stylized color grading unless the user explicitly asks for a style such as cinematic, vintage, teal-orange, cyberpunk, or film look.
- Avoid no-op operations.
- Positive exposure_ev means brighten. Negative exposure_ev means darken.
- Negative black_level lowers lifted blacks and makes shadow blacks cleaner. Positive black_level lifts blacks.
- For cool/blue cast, use white_balance temperature > 0.
- For warm/yellow cast, use white_balance temperature < 0.
- For green cast, use white_balance tint > 0.
- For magenta cast, use white_balance tint < 0.
- Use tone_curve for gamma, non-linear midtone curve, or tonal response repair.
- Use hsl_zone for explicit color-zone requests such as sky, skin, foliage, blues, greens, reds, or yellows.
- Use regional_color_balance for separate shadow/highlight color casts, not for global white balance.

Allowed tools and parameter ranges:
{json.dumps(TOOL_LIMITS, ensure_ascii=False)}

Allowed hsl_zone color values:
{list(HSL_ZONE_CENTERS)}

User prompt:
{prompt}

Local diagnostics:
{json.dumps(local_diagnostics, ensure_ascii=False)}

VLM diagnostics:
{json.dumps(vlm_diagnostics, ensure_ascii=False)}

Required JSON shape:
{{
  "intent_summary": "short summary",
  "operations": [
    {{
      "tool": "basic_tone",
      "params": {{
        "exposure_ev": 0.0,
        "contrast": 0.0,
        "black_level": 0.0,
        "white_level": 0.0,
        "highlights": 0.0,
        "shadows": 0.0
      }},
      "reason": "why this is needed"
    }},
    {{
      "tool": "white_balance",
      "params": {{"temperature": 0.0, "tint": 0.0}},
      "reason": "why this is needed"
    }},
    {{
      "tool": "tone_curve",
      "params": {{"shadows": 0.0, "midtones": 0.0, "highlights": 0.0}},
      "reason": "why this is needed"
    }},
    {{
      "tool": "hsl_zone",
      "params": {{"color": "blue", "hue_shift": 0.0, "saturation": 0.0, "luminance": 0.0}},
      "reason": "only when a specific color zone should be adjusted"
    }},
    {{
      "tool": "regional_color_balance",
      "params": {{
        "shadow_hue": 0.0,
        "shadow_chroma": 0.0,
        "shadow_luminance": 0.0,
        "shadow_saturation": 0.0,
        "shadow_brilliance": 0.0,
        "highlight_hue": 0.0,
        "highlight_chroma": 0.0,
        "highlight_luminance": 0.0,
        "highlight_saturation": 0.0,
        "highlight_brilliance": 0.0
      }},
      "reason": "only when shadows and highlights have different color casts"
    }}
  ],
  "warnings": []
}}
""".strip()
    return call_ollama_chat(
        ollama_url=ollama_url,
        model=model,
        messages=[{"role": "user", "content": planner_prompt}],
        temperature=0.0,
        format_json=True,
        timeout=timeout,
    )


def call_ollama_chat(
    *,
    ollama_url: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    format_json: bool = True,
    num_predict: int = 700,
    timeout: int = 240,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": 4096,
        },
    }
    if format_json:
        payload["format"] = "json"
    request = urllib.request.Request(
        ollama_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama request failed for model {model}: {exc}") from exc
    message = data.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"Ollama response missing message.content: {data}")
    return content


def base64_image(path: Path, max_side: int | None = None) -> str:
    if not max_side or max_side <= 0:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object found in model output: {text[:300]}")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Model output JSON must be an object.")
    return data


def sanitize_vlm_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Keep VLM free-text advice aligned with the structured diagnostic labels."""
    sanitized = dict(diagnostics)
    cast = str(sanitized.get("white_balance_cast") or "").strip().lower()
    expected_actions = {
        "cool_blue": "Use warmer white balance: temperature > 0.",
        "warm_yellow": "Use cooler white balance: temperature < 0.",
        "green": "Reduce green cast: tint > 0.",
        "magenta": "Reduce magenta cast: tint < 0.",
        "neutral": "Do not change white balance unless the user asks for it.",
    }
    expected = expected_actions.get(cast)
    if not expected:
        return sanitized

    raw_actions = sanitized.get("recommended_actions")
    actions = [str(action) for action in raw_actions] if isinstance(raw_actions, list) else []
    filtered_actions: list[str] = []
    warnings: list[str] = []
    for action in actions:
        action_text = action.lower()
        if cast == "warm_yellow" and mentions_white_balance(action_text) and contradicts_warm_yellow(action_text):
            warnings.append(f"Dropped contradictory VLM action: {action}")
            continue
        if cast == "cool_blue" and mentions_white_balance(action_text) and contradicts_cool_blue(action_text):
            warnings.append(f"Dropped contradictory VLM action: {action}")
            continue
        filtered_actions.append(action)

    sanitized["recommended_actions"] = [expected] + [
        action for action in filtered_actions if action.strip() and action.strip() != expected
    ]
    sanitized["program_interpretation"] = expected
    if warnings:
        sanitized["normalization_warnings"] = warnings
    return sanitized


def mentions_white_balance(text: str) -> bool:
    return any(token in text for token in ["white balance", "temperature", "cast", "tint"])


def contradicts_warm_yellow(text: str) -> bool:
    return any(token in text for token in ["warmer", "warm up", "increase temperature"])


def contradicts_cool_blue(text: str) -> bool:
    return any(token in text for token in ["cooler", "cool down", "decrease temperature"])


def build_fallback_plan(
    prompt: str,
    diagnostics: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    findings = set(diagnostics.get("heuristic_findings") or [])
    text = prompt.lower()
    operations: list[dict[str, Any]] = []

    tone: dict[str, float] = {
        "exposure_ev": 0.0,
        "contrast": 0.0,
        "black_level": 0.0,
        "white_level": 0.0,
        "highlights": 0.0,
        "shadows": 0.0,
    }
    if "too bright" in text or "overexposed" in text or "太亮" in text or "過曝" in text:
        tone["exposure_ev"] = -0.45
        tone["highlights"] = -0.16
    elif "too dark" in text or "underexposed" in text or "太暗" in text or "偏暗" in text:
        tone["exposure_ev"] = 0.45
        tone["shadows"] = 0.12
    elif "over_exposed" in findings:
        tone["exposure_ev"] = -0.25
        tone["highlights"] = -0.08
    elif "under_exposed" in findings:
        tone["exposure_ev"] = 0.25
        tone["shadows"] = 0.08

    if "low_global_contrast" in findings or "low contrast" in text or "對比太低" in text:
        tone["contrast"] = 0.18
        tone["black_level"] = -0.04
    if "black" in text or "黑階" in text:
        tone["black_level"] = min(tone["black_level"], -0.04)
    if any(abs(value) > 0.001 for value in tone.values()):
        operations.append({"tool": "basic_tone", "params": tone, "reason": "fallback tone correction"})

    curve_terms = [
        "gamma",
        "tone curve",
        "curve",
        "midtone",
        "midtones",
        "middle tone",
        "tonal response",
        "non-linear",
        "nonlinear",
    ]
    if any(term in text for term in curve_terms):
        midtones = 0.12
        if any(term in text for term in ["darken midtone", "darken midtones", "lower midtone", "lower midtones"]):
            midtones = -0.14
        elif any(term in text for term in ["brighten midtone", "brighten midtones", "lift midtone", "lift midtones"]):
            midtones = 0.16
        operations.append(
            {
                "tool": "tone_curve",
                "params": {"shadows": 0.0, "midtones": midtones, "highlights": 0.0},
                "reason": "fallback gamma or midtone curve correction",
            }
        )

    wb = {"temperature": 0.0, "tint": 0.0}
    style_wb_intent = prompt_white_balance_style_intent(prompt)
    if style_wb_intent == "cooler":
        wb["temperature"] = -prompt_style_temperature_target(prompt)
    elif style_wb_intent == "warmer":
        wb["temperature"] = prompt_style_temperature_target(prompt)
    elif "cool_blue_cast" in findings or "偏藍" in text or "cool" in text:
        wb["temperature"] = 0.28
    if "warm_yellow_cast" in findings or "偏黃" in text or "yellow" in text:
        wb["temperature"] = -0.28
    if "green_cast" in findings or "偏綠" in text:
        wb["tint"] = 0.22
    if "magenta_cast" in findings or "洋紅" in text:
        wb["tint"] = -0.22
    if any(abs(value) > 0.001 for value in wb.values()):
        operations.append({"tool": "white_balance", "params": wb, "reason": "fallback white balance correction"})

    color = {"saturation": 0.0, "vibrance": 0.0}
    if "high_saturation" in findings or "太鮮豔" in text or "too saturated" in text:
        color["saturation"] = -0.16
        color["vibrance"] = -0.08
    elif "low_saturation" in findings or "washed out" in text or "褪色" in text:
        color["saturation"] = 0.12
        color["vibrance"] = 0.12
    if any(abs(value) > 0.001 for value in color.values()):
        operations.append({"tool": "color_intensity", "params": color, "reason": "fallback saturation correction"})

    hsl_operation = prompt_hsl_zone_operation(text)
    if hsl_operation:
        operations.append(hsl_operation)

    if "edge_vignette" in findings or "vignette" in text or "暗角" in text:
        operations.append(
            {
                "tool": "vignette",
                "params": {"correction": 0.18},
                "reason": "fallback vignette correction",
            }
        )

    if not operations:
        operations.append(
            {
                "tool": "basic_tone",
                "params": {
                    "exposure_ev": 0.0,
                    "contrast": 0.08,
                    "black_level": -0.02,
                    "white_level": 0.0,
                    "highlights": 0.0,
                    "shadows": 0.0,
                },
                "reason": "minimal natural correction because no specific issue was detected",
            }
        )

    return {
        "intent_summary": "Fallback natural photo repair plan.",
        "operations": operations,
        "warnings": [f"Planner fallback used: {reason}"],
    }


def validate_plan(
    plan: dict[str, Any],
    prompt: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operations = plan.get("operations")
    if not isinstance(operations, list):
        operations = []
    validated_operations: list[dict[str, Any]] = []
    warnings = list(plan.get("warnings") or [])
    prompt_is_stylized = contains_style_request(prompt)
    prompt_is_color_style_only = prompt_requests_color_style_only(prompt)

    for operation in operations:
        if not isinstance(operation, dict):
            warnings.append("Dropped malformed operation.")
            continue
        tool = str(operation.get("tool") or "").strip()
        if tool not in TOOL_LIMITS:
            warnings.append(f"Dropped unsupported tool: {tool}")
            continue
        if tool == "basic_tone" and (prompt_is_narrow_color_request(prompt) or prompt_is_color_style_only):
            warnings.append("Dropped tone operation because the prompt only requested color or white-balance repair.")
            continue
        if tool == "white_balance" and prompt_is_narrow_tone_request(prompt):
            warnings.append("Dropped white_balance operation because the prompt only requested exposure/contrast/black-level repair.")
            continue
        if tool == "color_intensity" and prompt_is_color_style_only and not prompt_requests_color_intensity_edit(prompt):
            warnings.append("Dropped color_intensity operation because the prompt only requested a cooler/warmer style.")
            continue
        if (
            tool == "color_intensity"
            and prompt_requests_direct_noncolor_edit(prompt)
            and not prompt_requests_color_intensity_edit(prompt)
            and not prompt_requests_restoration_context(prompt)
        ):
            warnings.append("Dropped color_intensity operation because the prompt only requested tone/detail editing.")
            continue
        if tool == "hsl_zone" and prompt_is_narrow_tone_request(prompt) and not prompt_has_color_zone_request(prompt):
            warnings.append("Dropped hsl_zone operation because the prompt only requested exposure/contrast/black-level/gamma repair.")
            continue
        if tool == "hsl_zone" and not prompt_is_stylized and not prompt_has_color_zone_request(prompt):
            warnings.append("Dropped hsl_zone operation because the prompt did not explicitly request a color-zone edit.")
            continue
        if tool in {"channel_balance", "regional_color_balance"} and not prompt_is_stylized:
            reason = str(operation.get("reason") or "").lower()
            if not any(token in reason for token in ["neutral", "cast", "shadow", "highlight", "白平衡", "偏色"]):
                warnings.append(f"Dropped likely stylization operation: {tool}")
                continue

        params = operation.get("params")
        if not isinstance(params, dict):
            params = {}
        clean_params: dict[str, Any] = {}
        limits = TOOL_LIMITS[tool]
        for key, (low, high) in limits.items():
            value = params.get(key, 0.0)
            if tool == "hsl_zone" and key in {"hue_shift", "saturation", "luminance"}:
                clean_params[key] = clamp_float(value, low, high)
            elif tool != "hsl_zone":
                clean_params[key] = clamp_float(value, low, high)

        if tool == "hsl_zone":
            color = str(params.get("color") or "").strip().lower()
            if color not in HSL_ZONE_CENTERS:
                warnings.append(f"Dropped hsl_zone with unsupported color: {color}")
                continue
            clean_params["color"] = color
        elif tool == "regional_color_balance":
            clean_params = normalize_regional_color_balance_params(clean_params, prompt, warnings)

        if is_noop(tool, clean_params):
            warnings.append(f"Dropped no-op operation: {tool}")
            continue
        validated_operations.append(
            {
                "tool": tool,
                "params": clean_params,
                "reason": str(operation.get("reason") or ""),
            }
        )

    validated_operations = enforce_diagnostic_guards(
        validated_operations,
        prompt,
        diagnostics or {},
        warnings,
    )
    validated_operations = enforce_exposure_guards(
        validated_operations,
        prompt,
        diagnostics or {},
        warnings,
    )
    validated_operations = enforce_prompt_required_tools(
        validated_operations,
        prompt,
        diagnostics or {},
        warnings,
    )
    validated_operations = enforce_direct_edit_strength(
        validated_operations,
        prompt,
        diagnostics or {},
        warnings,
    )
    validated_operations = drop_noop_operations(validated_operations, warnings)

    if not validated_operations:
        fallback = build_fallback_plan(prompt, diagnostics or {"heuristic_findings": []}, "validated plan had no effective operations")
        warnings.extend(fallback.get("warnings", []))
        return validate_plan({**fallback, "warnings": warnings}, prompt, diagnostics)

    return {
        "intent_summary": str(plan.get("intent_summary") or ""),
        "operations": validated_operations,
        "warnings": warnings,
    }


def enforce_diagnostic_guards(
    operations: list[dict[str, Any]],
    prompt: str,
    diagnostics: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    findings = set(diagnostics.get("heuristic_findings") or [])
    style_wb_intent = prompt_white_balance_style_intent(prompt)
    prompt_cast = prompt_white_balance_intent(prompt)
    if prompt_is_narrow_tone_request(prompt) and not prompt_cast and not style_wb_intent:
        return operations
    if not findings and not prompt_cast and not style_wb_intent:
        return operations

    wb_op = next((op for op in operations if op["tool"] == "white_balance"), None)
    if wb_op is None and (
        style_wb_intent
        or prompt_cast
        or findings.intersection({"cool_blue_cast", "warm_yellow_cast", "green_cast", "magenta_cast"})
    ):
        wb_op = {
            "tool": "white_balance",
            "params": {"temperature": 0.0, "tint": 0.0},
            "reason": "added by diagnostic guard to neutralize requested or measured color cast",
        }
        operations.append(wb_op)

    if wb_op is None:
        return operations

    params = wb_op["params"]
    adjusted: list[str] = []
    reason_override: str | None = None

    style_strength = prompt_style_intensity(prompt)
    style_strength_label = prompt_style_strength_label(prompt)
    if style_wb_intent == "cooler":
        target = -prompt_style_temperature_target(prompt)
        if params.get("temperature", 0.0) > target:
            params["temperature"] = target
            adjusted.append(f"prompt cooler/blue style -> {style_strength} cooler temperature")
        reason_override = f"Apply a {style_strength_label} cooler white balance for the requested style while preserving natural skin and shadows."
    elif style_wb_intent == "warmer":
        target = prompt_style_temperature_target(prompt)
        if params.get("temperature", 0.0) < target:
            params["temperature"] = target
            adjusted.append(f"prompt warmer style -> {style_strength} warmer temperature")
        reason_override = f"Apply a {style_strength_label} warmer white balance for the requested style while preserving natural skin and shadows."

    if style_wb_intent and not prompt_mentions_tint_axis(prompt):
        if abs(float(params.get("tint", 0.0))) >= 0.003:
            params["tint"] = 0.0
            adjusted.append("style white balance -> neutral tint")

    if style_wb_intent:
        pass
    elif prompt_cast == "cool_blue_cast":
        if params.get("temperature", 0.0) <= 0.05:
            params["temperature"] = 0.28
            adjusted.append("prompt cool_blue_cast -> warmer temperature")
    elif prompt_cast == "warm_yellow_cast":
        if params.get("temperature", 0.0) >= -0.05:
            params["temperature"] = -0.28
            adjusted.append("prompt warm_yellow_cast -> cooler temperature")
    else:
        if "cool_blue_cast" in findings and params.get("temperature", 0.0) <= 0.05:
            params["temperature"] = 0.28
            adjusted.append("cool_blue_cast -> warmer temperature")
        elif "warm_yellow_cast" in findings and params.get("temperature", 0.0) >= -0.05:
            params["temperature"] = -0.28
            adjusted.append("warm_yellow_cast -> cooler temperature")

    if prompt_cast == "green_cast":
        if params.get("tint", 0.0) <= 0.05:
            params["tint"] = 0.22
            adjusted.append("prompt green_cast -> magenta tint")
    elif prompt_cast == "magenta_cast":
        if params.get("tint", 0.0) >= -0.05:
            params["tint"] = -0.22
            adjusted.append("prompt magenta_cast -> green tint")
    elif prompt_cast is None and not style_wb_intent:
        if "green_cast" in findings and params.get("tint", 0.0) <= 0.05:
            params["tint"] = 0.22
            adjusted.append("green_cast -> magenta tint")
        elif "magenta_cast" in findings and params.get("tint", 0.0) >= -0.05:
            params["tint"] = -0.22
            adjusted.append("magenta_cast -> green tint")

    if prompt_cast in {"cool_blue_cast", "warm_yellow_cast"} and not prompt_mentions_tint_axis(prompt):
        if abs(float(params.get("tint", 0.0))) >= 0.003:
            params["tint"] = 0.0
            adjusted.append("cool/warm cast repair -> neutral tint")

    if adjusted:
        warnings.append("Diagnostic guard adjusted white_balance: " + "; ".join(adjusted))
        reason = wb_op.get("reason") or ""
        wb_op["reason"] = f"{reason_override or reason} Guarded by local color diagnostics.".strip()
    return operations


def enforce_exposure_guards(
    operations: list[dict[str, Any]],
    prompt: str,
    diagnostics: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    tone_op = next((op for op in operations if op["tool"] == "basic_tone"), None)
    if tone_op is None:
        return operations

    params = tone_op["params"]
    exposure = float(params.get("exposure_ev", 0.0))
    if abs(exposure) < 0.003:
        return operations

    intent = prompt_exposure_intent(prompt)
    if intent["brighten"] or intent["darken"]:
        return operations
    if not (intent["normalize"] or prompt_requests_natural_repair(prompt)):
        return operations

    mean_luma = safe_float(diagnostics.get("mean_luma"), default=0.0)
    findings = set(diagnostics.get("heuristic_findings") or [])
    adjusted = False
    if exposure > 0 and mean_luma >= 0.24:
        params["exposure_ev"] = 0.0
        adjusted = True
    elif exposure > 0.12:
        params["exposure_ev"] = 0.12
        adjusted = True
    elif exposure < 0 and mean_luma <= 0.45:
        params["exposure_ev"] = 0.0
        adjusted = True
    elif exposure < -0.18:
        params["exposure_ev"] = -0.18
        adjusted = True

    if adjusted:
        warnings.append(
            "Exposure guard softened planner exposure because the prompt asked for natural normalization, not explicit brightening/darkening."
        )
        reason = tone_op.get("reason") or ""
        tone_op["reason"] = f"{reason} Exposure guarded for natural restoration.".strip()

    contrast = float(params.get("contrast", 0.0))
    if prompt_contrast_intent(prompt) and "low_global_contrast" in findings and contrast < 0.18:
        params["contrast"] = 0.18
        warnings.append("Contrast guard raised contrast for low-global-contrast restoration.")
        reason = tone_op.get("reason") or ""
        tone_op["reason"] = f"{reason} Contrast guarded for low-contrast restoration.".strip()
    return operations


def enforce_prompt_required_tools(
    operations: list[dict[str, Any]],
    prompt: str,
    diagnostics: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    intents = prompt_tool_intents(prompt)
    tools = {operation["tool"] for operation in operations}
    color_op = next((operation for operation in operations if operation["tool"] == "color_intensity"), None)
    vignette_op = next((operation for operation in operations if operation["tool"] == "vignette"), None)
    regional_op = next((operation for operation in operations if operation["tool"] == "regional_color_balance"), None)

    saturation_target = saturation_reduction_target(prompt)
    if intents.get("reduce_saturation") and color_op is not None:
        params = color_op["params"]
        if abs(saturation_target) < 0.003:
            params["saturation"] = 0.0
            params["vibrance"] = 0.0
            warnings.append("Prompt guard neutralized color_intensity because the prompt only forbids saturation increase.")
        elif params.get("saturation", 0.0) > -0.01 or params.get("saturation", 0.0) < saturation_target:
            params["saturation"] = saturation_target
            params["vibrance"] = min(params.get("vibrance", 0.0), 0.0)
            warnings.append("Prompt guard corrected color_intensity direction for saturation reduction.")
    elif intents.get("reduce_saturation") and "color_intensity" not in tools and abs(saturation_target) >= 0.003:
        operations.append(
            {
                "tool": "color_intensity",
                "params": {"saturation": saturation_target, "vibrance": 0.0},
                "reason": "Added because the user explicitly requested saturation normalization or reduction.",
            }
        )
        warnings.append("Prompt guard added color_intensity for saturation reduction.")
        tools.add("color_intensity")

    if not intents.get("reduce_saturation") and intents.get("increase_saturation") and color_op is not None:
        params = color_op["params"]
        if params.get("saturation", 0.0) < 0.01:
            params["saturation"] = 0.1
            params["vibrance"] = max(params.get("vibrance", 0.0), 0.08)
            warnings.append("Prompt guard corrected color_intensity direction for saturation increase.")
    elif not intents.get("reduce_saturation") and intents.get("increase_saturation") and "color_intensity" not in tools:
        operations.append(
            {
                "tool": "color_intensity",
                "params": {"saturation": 0.1, "vibrance": 0.08},
                "reason": "Added because the user explicitly requested more color intensity.",
            }
        )
        warnings.append("Prompt guard added color_intensity for saturation increase.")
        tools.add("color_intensity")

    vignette_target = vignette_reduction_target(diagnostics)
    if intents.get("reduce_vignette") and vignette_op is not None:
        params = vignette_op["params"]
        if params.get("correction", 0.0) <= 0.0 or params.get("correction", 0.0) > vignette_target:
            params["correction"] = vignette_target
            warnings.append("Prompt guard corrected vignette direction for vignette reduction.")
    elif intents.get("reduce_vignette") and "vignette" not in tools:
        operations.append(
            {
                "tool": "vignette",
                "params": {"correction": vignette_target},
                "reason": "Added because the user explicitly requested vignette reduction.",
            }
        )
        warnings.append("Prompt guard added vignette correction.")
        tools.add("vignette")

    if prompt_requests_regional_color_cast_repair(prompt):
        target = default_regional_color_balance_target()
        if regional_op is not None:
            regional_op["params"] = normalize_regional_color_balance_params(
                {**target, **regional_op.get("params", {})},
                prompt,
                warnings,
            )
            warnings.append("Prompt guard normalized regional_color_balance for shadow/highlight color-cast repair.")
        elif "regional_color_balance" not in tools:
            operations.append(
                {
                    "tool": "regional_color_balance",
                    "params": target,
                    "reason": "Added because the user explicitly requested shadow/highlight color-cast neutralization.",
                }
            )
            warnings.append("Prompt guard added regional_color_balance for shadow/highlight color-cast repair.")
            tools.add("regional_color_balance")

    return operations


def enforce_direct_edit_strength(
    operations: list[dict[str, Any]],
    prompt: str,
    diagnostics: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if prompt_requests_restoration_context(prompt) and not prompt_white_balance_style_intent(prompt):
        return operations

    exposure_target = direct_exposure_target(prompt, diagnostics)
    if exposure_target is not None:
        tone_op = get_or_add_operation(
            operations,
            "basic_tone",
            {
                "exposure_ev": 0.0,
                "contrast": 0.0,
                "black_level": 0.0,
                "white_level": 0.0,
                "highlights": 0.0,
                "shadows": 0.0,
            },
            "Added for direct brightness edit.",
        )
        tone_op["params"]["exposure_ev"] = exposure_target
        warnings.append(f"Direct edit strength set exposure_ev to {exposure_target}.")

    contrast_target = direct_contrast_target(prompt, diagnostics)
    if contrast_target is not None:
        tone_op = get_or_add_operation(
            operations,
            "basic_tone",
            {
                "exposure_ev": 0.0,
                "contrast": 0.0,
                "black_level": 0.0,
                "white_level": 0.0,
                "highlights": 0.0,
                "shadows": 0.0,
            },
            "Added for direct contrast edit.",
        )
        tone_op["params"]["contrast"] = contrast_target
        warnings.append(f"Direct edit strength set contrast to {contrast_target}.")

    color_target = direct_color_intensity_target(prompt)
    if color_target is not None:
        color_op = get_or_add_operation(
            operations,
            "color_intensity",
            {"saturation": 0.0, "vibrance": 0.0},
            "Added for direct color-intensity edit.",
        )
        color_op["params"]["saturation"] = color_target["saturation"]
        color_op["params"]["vibrance"] = color_target["vibrance"]
        warnings.append(
            "Direct edit strength set color_intensity to "
            f"saturation={color_target['saturation']}, vibrance={color_target['vibrance']}."
        )

    sharpen_target = direct_sharpen_target(prompt)
    if sharpen_target is not None:
        sharpen_op = get_or_add_operation(
            operations,
            "sharpen",
            {"amount": 0.0},
            "Added for direct sharpness/detail edit.",
        )
        sharpen_op["params"]["amount"] = sharpen_target
        warnings.append(f"Direct edit strength set sharpen amount to {sharpen_target}.")

    return operations


def get_or_add_operation(
    operations: list[dict[str, Any]],
    tool: str,
    default_params: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    operation = next((item for item in operations if item.get("tool") == tool), None)
    if operation is not None:
        operation["params"] = {**default_params, **dict(operation.get("params") or {})}
        if not operation.get("reason"):
            operation["reason"] = reason
        return operation
    operation = {"tool": tool, "params": dict(default_params), "reason": reason}
    operations.append(operation)
    return operation


def normalize_regional_color_balance_params(
    params: dict[str, Any],
    prompt: str,
    warnings: list[str],
) -> dict[str, Any]:
    clean = dict(params)
    if not prompt_requests_regional_color_cast_repair(prompt):
        return clean

    target = default_regional_color_balance_target()
    if abs(float(clean.get("shadow_hue", 0.0))) < 0.003 and abs(float(clean.get("shadow_chroma", 0.0))) < 0.003:
        clean["shadow_hue"] = target["shadow_hue"]
        clean["shadow_chroma"] = target["shadow_chroma"]
    elif abs(float(clean.get("shadow_chroma", 0.0))) < 0.003:
        clean["shadow_chroma"] = target["shadow_chroma"]

    if abs(float(clean.get("highlight_hue", 0.0))) < 0.003 and abs(float(clean.get("highlight_chroma", 0.0))) < 0.003:
        clean["highlight_hue"] = target["highlight_hue"]
        clean["highlight_chroma"] = target["highlight_chroma"]
    elif abs(float(clean.get("highlight_chroma", 0.0))) < 0.003:
        clean["highlight_chroma"] = target["highlight_chroma"]

    if not prompt_mentions_regional_luminance(prompt):
        if abs(float(clean.get("shadow_luminance", 0.0))) >= 0.003 or abs(float(clean.get("highlight_luminance", 0.0))) >= 0.003:
            warnings.append("Regional color-balance guard removed shadow/highlight luminance changes from color-cast repair.")
        clean["shadow_luminance"] = 0.0
        clean["highlight_luminance"] = 0.0
        clean["shadow_brilliance"] = 0.0
        clean["highlight_brilliance"] = 0.0

    if abs(float(clean.get("shadow_saturation", 0.0))) < 0.003:
        clean["shadow_saturation"] = target["shadow_saturation"]
    if abs(float(clean.get("highlight_saturation", 0.0))) < 0.003:
        clean["highlight_saturation"] = target["highlight_saturation"]
    return clean


def default_regional_color_balance_target() -> dict[str, float]:
    return {
        "shadow_hue": 30.0,
        "shadow_chroma": 0.08,
        "shadow_luminance": 0.0,
        "shadow_saturation": -0.04,
        "shadow_brilliance": 0.0,
        "highlight_hue": 300.0,
        "highlight_chroma": 0.06,
        "highlight_luminance": 0.0,
        "highlight_saturation": -0.03,
        "highlight_brilliance": 0.0,
    }


def prompt_requests_regional_color_cast_repair(prompt: str) -> bool:
    text = prompt.lower()
    has_regions = ("shadow" in text or "shadows" in text) and ("highlight" in text or "highlights" in text)
    has_color_cast = any(term in text for term in ["color cast", "color casts", "cast", "tint", "color balance", "neutralize"])
    return has_regions and has_color_cast


def prompt_mentions_regional_luminance(prompt: str) -> bool:
    text = prompt.lower()
    return any(
        term in text
        for term in [
            "shadow luminance",
            "highlight luminance",
            "brighten shadows",
            "darken shadows",
            "brighten highlights",
            "darken highlights",
            "lift shadows",
            "reduce highlights",
        ]
    )


def prompt_mentions_tint_axis(prompt: str) -> bool:
    text = prompt.lower()
    return any(term in text for term in ["green cast", "magenta cast", "too green", "too magenta", "greenish", "magenta", "purple cast", "tint"])


def drop_noop_operations(
    operations: list[dict[str, Any]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for operation in operations:
        if is_noop(operation["tool"], operation["params"]):
            warnings.append(f"Dropped no-op operation after guards: {operation['tool']}")
            continue
        kept.append(operation)
    return kept


def prompt_tool_intents(prompt: str) -> dict[str, bool]:
    text = prompt.lower()
    reduce_sat_terms = [
        "reduce saturation",
        "lower saturation",
        "decrease saturation",
        "less saturated",
        "normalize saturation",
        "natural saturation",
        "do not increase saturation",
        "不要增加飽和",
        "降低飽和",
        "減少飽和",
        "飽和度自然",
        "飽和度正常",
    ]
    increase_sat_terms = [
        "increase saturation",
        "more saturated",
        "more vibrant",
        "increase vibrance",
        "提高飽和",
        "增加飽和",
        "更鮮豔",
    ]
    reduce_vignette_terms = [
        "reduce vignette",
        "remove vignette",
        "correct vignette",
        "less vignette",
        "reduce dark corners",
        "remove dark corners",
        "暗角",
        "暈影",
    ]
    negative_increase_sat_terms = [
        "do not increase saturation",
        "don't increase saturation",
        "do not add saturation",
        "without increasing saturation",
        "not increase saturation",
    ]
    reduce_saturation = any(term in text for term in reduce_sat_terms)
    increase_saturation = any(term in text for term in increase_sat_terms)
    if any(term in text for term in negative_increase_sat_terms):
        increase_saturation = False
    return {
        "reduce_saturation": reduce_saturation,
        "increase_saturation": increase_saturation,
        "reduce_vignette": any(term in text for term in reduce_vignette_terms),
    }


def prompt_requests_color_intensity_edit(prompt: str) -> bool:
    text = prompt.lower()
    intents = prompt_tool_intents(prompt)
    direct_terms = [
        "saturation",
        "vibrance",
        "vivid",
        "color intensity",
        "colour intensity",
        "more colorful",
        "more colourful",
        "less colorful",
        "less colourful",
        "more muted",
        "less muted",
        "鮮豔",
        "飽和",
    ]
    return intents["reduce_saturation"] or intents["increase_saturation"] or any(term in text for term in direct_terms)


def prompt_requests_direct_noncolor_edit(prompt: str) -> bool:
    exposure_intent = prompt_exposure_intent(prompt)
    return (
        exposure_intent["brighten"]
        or exposure_intent["darken"]
        or prompt_contrast_edit_direction(prompt) is not None
        or direct_sharpen_target(prompt) is not None
    )


def prompt_has_color_zone_request(prompt: str) -> bool:
    text = prompt.lower()
    explicit_zone_terms = [
        "sky",
        "skin",
        "foliage",
        "grass",
        "leaf",
        "leaves",
        "hsl",
        "color zone",
        "specific color",
    ]
    if not any(term in text for term in explicit_zone_terms):
        return False
    color_terms = [
        "red",
        "orange",
        "yellow",
        "green",
        "cyan",
        "blue",
        "purple",
        "magenta",
    ]
    action_terms = [
        "more",
        "less",
        "reduce",
        "increase",
        "saturat",
        "brighter",
        "darker",
        "natural",
        "fluorescent",
        "too vivid",
        "bluer",
        "redder",
        "greener",
    ]
    return any(term in text for term in color_terms) and any(term in text for term in action_terms)


def prompt_hsl_zone_operation(text: str) -> dict[str, Any] | None:
    color: str | None = None
    if not any(term in text for term in ["sky", "skin", "foliage", "grass", "leaf", "leaves", "hsl", "color zone", "specific color"]):
        return None
    if "sky" in text or "blue" in text or "bluer" in text:
        color = "blue"
    elif "skin" in text:
        color = "orange"
    elif "foliage" in text or "grass" in text or "leaf" in text or "leaves" in text or "green" in text:
        color = "green"
    elif "yellow" in text:
        color = "yellow"
    elif "red" in text:
        color = "red"
    elif "cyan" in text:
        color = "cyan"
    elif "purple" in text:
        color = "purple"
    elif "magenta" in text:
        color = "magenta"
    if color is None:
        return None

    saturation = 0.0
    luminance = 0.0
    hue_shift = 0.0
    increase_requested = any(term in text for term in ["more", "increase", "bluer", "greener", "redder", "more saturated"])
    reduce_requested = any(term in text for term in ["less", "reduce", "too vivid", "fluorescent", "not too"])
    if increase_requested:
        saturation = 0.14
    elif reduce_requested:
        saturation = -0.12
    if "brighter" in text or "lighter" in text:
        luminance = 0.08
    elif "darker" in text:
        luminance = -0.08

    if abs(saturation) < 0.003 and abs(luminance) < 0.003 and abs(hue_shift) < 0.003:
        return None
    return {
        "tool": "hsl_zone",
        "params": {
            "color": color,
            "hue_shift": hue_shift,
            "saturation": saturation,
            "luminance": luminance,
        },
        "reason": f"fallback color-zone adjustment for {color}",
    }


def prompt_exposure_intent(prompt: str) -> dict[str, bool]:
    text = prompt.lower()
    brighten_terms = [
        "brighten",
        "brighter",
        "increase exposure",
        "raise exposure",
        "lighten",
        "underexposed",
        "under-exposed",
        "make it brighter",
        "變亮",
        "提高曝光",
        "增加曝光",
        "欠曝",
    ]
    darken_terms = [
        "darken",
        "darker",
        "decrease exposure",
        "lower exposure",
        "reduce exposure",
        "make it darker",
        "overexposed",
        "over-exposed",
        "變暗",
        "降低曝光",
        "減少曝光",
        "過曝",
    ]
    normalize_terms = [
        "normalize exposure",
        "natural exposure",
        "restore natural exposure",
        "slightly normalize exposure",
        "balanced exposure",
        "自然曝光",
        "正常曝光",
        "校正曝光",
    ]
    return {
        "brighten": any(term in text for term in brighten_terms),
        "darken": any(term in text for term in darken_terms),
        "normalize": any(term in text for term in normalize_terms),
    }


def direct_exposure_target(prompt: str, diagnostics: dict[str, Any]) -> float | None:
    intent = prompt_exposure_intent(prompt)
    if not intent["brighten"] and not intent["darken"]:
        return None
    base = {
        "slight": 0.18,
        "default": 0.35,
        "noticeable": 0.5,
        "strong": 0.65,
    }[prompt_style_intensity(prompt)]
    mean_luma = safe_float(diagnostics.get("mean_luma"), default=0.0)
    clip_ratio = safe_float(diagnostics.get("clip_ratio"), default=0.0)
    if intent["brighten"] and not intent["darken"]:
        target = base
        if clip_ratio > 0.025 or mean_luma >= 0.78:
            target = 0.0
        elif mean_luma >= 0.68:
            target = min(target, 0.12)
        elif mean_luma >= 0.58:
            target = min(target, 0.22)
        return round(target, 5)
    if intent["darken"] and not intent["brighten"]:
        target = -base
        if mean_luma <= 0.16:
            target = 0.0
        elif mean_luma <= 0.24:
            target = max(target, -0.12)
        elif mean_luma <= 0.32:
            target = max(target, -0.22)
        return round(target, 5)
    return None


def prompt_contrast_intent(prompt: str) -> bool:
    text = prompt.lower()
    terms = [
        "increase contrast",
        "restore contrast",
        "more contrast",
        "moderate contrast",
        "適中對比",
        "增加對比",
        "提高對比",
        "恢復對比",
    ]
    return any(term in text for term in terms)


def prompt_contrast_edit_direction(prompt: str) -> str | None:
    text = prompt.lower()
    increase_terms = [
        "increase contrast",
        "more contrast",
        "higher contrast",
        "boost contrast",
        "punchier",
        "punchy",
        "more depth",
        "crisper contrast",
        "對比更高",
        "增加對比",
    ]
    decrease_terms = [
        "decrease contrast",
        "lower contrast",
        "less contrast",
        "reduce contrast",
        "softer contrast",
        "soft contrast",
        "降低對比",
        "柔和一點",
    ]
    if any(term in text for term in decrease_terms):
        return "decrease"
    if any(term in text for term in increase_terms):
        return "increase"
    return None


def direct_contrast_target(prompt: str, diagnostics: dict[str, Any]) -> float | None:
    direction = prompt_contrast_edit_direction(prompt)
    if direction is None:
        return None
    base = {
        "slight": 0.08,
        "default": 0.16,
        "noticeable": 0.24,
        "strong": 0.32,
    }[prompt_style_intensity(prompt)]
    std_luma = safe_float(diagnostics.get("std_luma"), default=0.0)
    if direction == "increase":
        target = base
        if std_luma >= 0.33:
            target = min(target, 0.1)
        return round(target, 5)
    target = -base
    if std_luma <= 0.14:
        target = max(target, -0.08)
    return round(target, 5)


def direct_color_intensity_target(prompt: str) -> dict[str, float] | None:
    text = prompt.lower()
    intents = prompt_tool_intents(prompt)
    if intents["reduce_saturation"] and abs(saturation_reduction_target(prompt)) < 0.003:
        return None
    increase_terms = [
        "more vivid",
        "vivid",
        "more vibrant",
        "vibrant",
        "more colorful",
        "more colourful",
        "richer colors",
        "richer colours",
        "鮮豔",
        "更有色彩",
    ]
    decrease_terms = [
        "less vivid",
        "less vibrant",
        "less colorful",
        "less colourful",
        "more muted",
        "muted",
        "desaturate",
        "less saturated",
        "降低飽和",
        "低飽和",
    ]
    if intents["reduce_saturation"] or any(term in text for term in decrease_terms):
        base = {
            "slight": -0.08,
            "default": -0.14,
            "noticeable": -0.22,
            "strong": -0.3,
        }[prompt_style_intensity(prompt)]
        return {"saturation": round(base, 5), "vibrance": round(base * 0.55, 5)}
    if intents["increase_saturation"] or any(term in text for term in increase_terms):
        base = {
            "slight": 0.08,
            "default": 0.16,
            "noticeable": 0.24,
            "strong": 0.32,
        }[prompt_style_intensity(prompt)]
        return {"saturation": round(base, 5), "vibrance": round(base * 0.65, 5)}
    return None


def direct_sharpen_target(prompt: str) -> float | None:
    text = prompt.lower()
    terms = [
        "sharpen",
        "sharper",
        "more sharp",
        "more detail",
        "more detailed",
        "more clarity",
        "clearer details",
        "crisper",
        "清晰",
        "銳利",
        "細節",
    ]
    if not any(term in text for term in terms):
        return None
    return {
        "slight": 0.2,
        "default": 0.35,
        "noticeable": 0.5,
        "strong": 0.65,
    }[prompt_style_intensity(prompt)]


def saturation_reduction_target(prompt: str) -> float:
    text = prompt.lower()
    neutral_terms = [
        "do not increase saturation",
        "don't increase saturation",
        "do not add saturation",
        "without increasing saturation",
        "not increase saturation",
        "keep color intensity natural",
        "keep color intensity believable",
        "keep saturation believable",
    ]
    if any(term in text for term in neutral_terms):
        return 0.0
    slight_terms = [
        "slightly reduce saturation",
        "slight saturation reduction",
        "normalize saturation",
        "natural saturation",
        "keep saturation believable",
        "適中飽和",
        "自然飽和",
    ]
    if any(term in text for term in slight_terms):
        return -0.08
    return -0.12


def vignette_reduction_target(diagnostics: dict[str, Any]) -> float:
    vignette_score = safe_float(diagnostics.get("vignette_score"), default=0.0)
    if vignette_score < 0.06:
        return 0.10
    if vignette_score < 0.12:
        return 0.18
    return 0.26


def prompt_requests_natural_repair(prompt: str) -> bool:
    text = prompt.lower()
    terms = [
        "restore",
        "natural",
        "clean",
        "professional",
        "understated",
        "normalize",
        "repair",
        "還原",
        "修回",
        "自然",
        "乾淨",
        "專業",
        "不誇張",
    ]
    return any(term in text for term in terms)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return numeric


def prompt_requests_restoration_context(prompt: str) -> bool:
    text = prompt.lower()
    terms = [
        "restore",
        "correct",
        "neutralize",
        "repair",
        "fix",
        "normalize",
        "ground truth",
        "gt",
        "degraded",
        "distorted",
        "poorly graded",
        "color cast",
        "white balance",
        "還原",
        "修正",
        "校正",
        "中和",
        "白平衡",
        "偏色",
    ]
    return any(term in text for term in terms)


def prompt_is_narrow_color_request(prompt: str) -> bool:
    text = prompt.lower()
    color_terms = [
        "white balance",
        "color cast",
        "cool blue",
        "blue cast",
        "warm yellow",
        "yellow cast",
        "warmer",
        "cooler",
        "saturation",
        "color intensity",
        "白平衡",
        "色偏",
        "飽和",
    ]
    tone_terms = [
        "exposure",
        "brighten",
        "darken",
        "underexposed",
        "overexposed",
        "contrast",
        "black level",
        "highlight",
        "shadow",
        "tone curve",
        "gamma",
        "midtone",
        "midtones",
        "tonal response",
        "曝光",
        "變亮",
        "變暗",
        "對比",
        "黑階",
        "高光",
        "陰影",
        "曲線",
    ]
    return any(term in text for term in color_terms) and not any(term in text for term in tone_terms)


def prompt_is_narrow_tone_request(prompt: str) -> bool:
    text = prompt.lower()
    tone_terms = [
        "exposure",
        "brighten",
        "darken",
        "contrast",
        "black level",
        "black levels",
        "lifted black",
        "clean black",
        "moderate contrast",
        "tone curve",
        "gamma",
        "midtone",
        "midtones",
        "tonal response",
        "自然曝光",
        "曝光",
        "對比",
        "黑階",
        "黑位",
    ]
    color_terms = [
        "white balance",
        "color cast",
        "cool blue",
        "blue cast",
        "warm yellow",
        "yellow cast",
        "warmer",
        "cooler",
        "saturation",
        "vibrance",
        "白平衡",
        "色偏",
        "飽和",
    ]
    return any(term in text for term in tone_terms) and not any(term in text for term in color_terms)


def prompt_white_balance_intent(prompt: str) -> str | None:
    text = prompt.lower()
    cool_terms = ["cool blue", "blue cast", "bluish", "too blue", "warmer", "偏藍", "偏冷", "冷色"]
    warm_terms = ["warm yellow", "yellow cast", "too yellow", "too warm", "cooler", "偏黃", "偏暖", "暖色"]
    green_terms = ["green cast", "too green", "greenish", "偏綠"]
    magenta_terms = ["magenta cast", "too magenta", "purple cast", "偏紫", "偏洋紅"]

    if any(term in text for term in cool_terms):
        return "cool_blue_cast"
    if any(term in text for term in warm_terms):
        return "warm_yellow_cast"
    if any(term in text for term in green_terms):
        return "green_cast"
    if any(term in text for term in magenta_terms):
        return "magenta_cast"
    return None


def prompt_white_balance_style_intent(prompt: str) -> str | None:
    text = prompt.lower()
    repair_terms = [
        "correct",
        "neutralize",
        "restore",
        "repair",
        "fix",
        "white balance",
        "color cast",
        "cast",
        "natural professional",
        "自然",
        "修正",
        "校正",
        "還原",
        "白平衡",
        "偏色",
    ]
    explicit_style_terms = [
        "style",
        "tone",
        "look",
        "mood",
        "cinematic",
        "film",
        "color grading",
        "色調",
        "風格",
        "電影",
    ]
    if any(term in text for term in repair_terms) and not any(term in text for term in explicit_style_terms):
        return None

    cooler_terms = [
        "make this photo cooler",
        "make this picture cooler",
        "make this image cooler",
        "make the photo cooler",
        "make the picture cooler",
        "make the image cooler",
        "make this photo look cooler",
        "make this picture look cooler",
        "make this image look cooler",
        "make it cooler",
        "look cooler",
        "slightly cooler",
        "noticeably cooler",
        "more cool",
        "cooler tone",
        "cooler look",
        "cooler color",
        "cooler colour",
        "cool cinematic",
        "blue cinematic",
        "blue tone",
        "cool tone",
        "冷一點",
        "更冷",
        "冷色調",
    ]
    warmer_terms = [
        "make this photo warmer",
        "make this picture warmer",
        "make this image warmer",
        "make the photo warmer",
        "make the picture warmer",
        "make the image warmer",
        "make this photo look warmer",
        "make this picture look warmer",
        "make this image look warmer",
        "make it warmer",
        "look warmer",
        "slightly warmer",
        "noticeably warmer",
        "more warm",
        "warmer tone",
        "warmer look",
        "warmer color",
        "warmer colour",
        "warm cinematic",
        "warm tone",
        "暖一點",
        "更暖",
        "暖色調",
    ]
    if any(term in text for term in cooler_terms):
        return "cooler"
    if any(term in text for term in warmer_terms):
        return "warmer"
    return None


def prompt_requests_noticeable_style(prompt: str) -> bool:
    return prompt_style_intensity(prompt) in {"noticeable", "strong"}


def prompt_style_intensity(prompt: str) -> str:
    text = prompt.lower()
    strong_terms = [
        "strong",
        "strongly",
        "much",
        "very",
        "dramatic",
        "dramatically",
        "heavy",
        "intense",
        "significant",
        "significantly",
        "大幅",
        "很",
        "非常",
    ]
    noticeable_terms = [
        "noticeably",
        "clearly",
        "obvious",
        "obviously",
        "visible",
        "more visible",
        "明顯",
    ]
    slight_terms = [
        "slight",
        "slightly",
        "subtle",
        "subtly",
        "a little",
        "a bit",
        "soft",
        "gentle",
        "gently",
        "稍微",
        "些微",
        "一點",
    ]
    if any(term in text for term in strong_terms):
        return "strong"
    if any(term in text for term in noticeable_terms):
        return "noticeable"
    if any(term in text for term in slight_terms):
        return "slight"
    return "default"


def prompt_style_temperature_target(prompt: str) -> float:
    intensity = prompt_style_intensity(prompt)
    return {
        "slight": 0.28,
        "default": 0.45,
        "noticeable": 0.6,
        "strong": 0.75,
    }[intensity]


def prompt_style_strength_label(prompt: str) -> str:
    return {
        "slight": "subtle",
        "default": "visible",
        "noticeable": "noticeable",
        "strong": "strong",
    }[prompt_style_intensity(prompt)]


def prompt_requests_color_style_only(prompt: str) -> bool:
    if not prompt_white_balance_style_intent(prompt):
        return False
    text = prompt.lower()
    explicit_tone_actions = [
        "brighten",
        "darken",
        "increase exposure",
        "decrease exposure",
        "raise exposure",
        "lower exposure",
        "increase contrast",
        "decrease contrast",
        "lift shadows",
        "darken shadows",
        "black level",
        "highlight",
    ]
    return not any(term in text for term in explicit_tone_actions)


def contains_style_request(prompt: str) -> bool:
    text = prompt.lower()
    style_terms = [
        "cinematic",
        "vintage",
        "film",
        "teal",
        "orange",
        "cyberpunk",
        "復古",
        "電影",
        "底片",
        "風格",
    ]
    return any(term in text for term in style_terms)


def clamp_float(value: Any, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    if not math.isfinite(numeric):
        numeric = 0.0
    return round(max(low, min(high, numeric)), 5)


def is_noop(tool: str, params: dict[str, Any]) -> bool:
    if tool == "basic_tone":
        return all(abs(float(params.get(key, 0.0))) < 0.003 for key in TOOL_LIMITS[tool])
    if tool == "hsl_zone":
        return all(abs(float(params.get(key, 0.0))) < 0.003 for key in ["hue_shift", "saturation", "luminance"])
    return all(abs(float(params.get(key, 0.0))) < 0.003 for key in TOOL_LIMITS[tool])


def apply_operations(
    image: Image.Image,
    operations: list[dict[str, Any]],
) -> tuple[Image.Image, list[dict[str, Any]]]:
    arr = pil_to_float(image)
    applied: list[dict[str, Any]] = []
    for operation in operations:
        tool = operation["tool"]
        params = operation["params"]
        before = arr.copy()
        if tool == "basic_tone":
            arr = apply_basic_tone(arr, params)
        elif tool == "white_balance":
            arr = apply_white_balance(arr, params)
        elif tool == "color_intensity":
            arr = apply_color_intensity(arr, params)
        elif tool == "channel_balance":
            arr = apply_channel_balance(arr, params)
        elif tool == "regional_color_balance":
            arr = apply_regional_color_balance(arr, params)
        elif tool == "tone_curve":
            arr = apply_tone_curve(arr, params)
        elif tool == "vignette":
            arr = apply_vignette_correction(arr, params)
        elif tool == "hsl_zone":
            arr = apply_hsl_zone(arr, params)
        elif tool == "sharpen":
            arr = pil_to_float(apply_sharpen(float_to_pil(arr), params))
        arr = np.clip(arr, 0.0, 1.0)
        delta = float(np.mean(np.abs(arr - before)))
        if delta > 0.0005:
            applied.append({**operation, "mean_absolute_delta": round(delta, 6)})
    return float_to_pil(arr), applied


def apply_basic_tone(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    exposure_ev = params.get("exposure_ev", 0.0)
    contrast = params.get("contrast", 0.0)
    black_level = params.get("black_level", 0.0)
    white_level = params.get("white_level", 0.0)
    highlights = params.get("highlights", 0.0)
    shadows = params.get("shadows", 0.0)

    out = np.clip(arr * (2.0**exposure_ev), 0.0, 1.0)
    if abs(black_level) > 0:
        out = np.clip(out + black_level * (1.0 - out), 0.0, 1.0)
    if abs(white_level) > 0:
        out = np.clip(out + white_level * out, 0.0, 1.0)
    if abs(contrast) > 0:
        out = np.clip((out - 0.5) * (1.0 + contrast) + 0.5, 0.0, 1.0)

    luma = luminance(out)[..., None]
    if abs(highlights) > 0:
        mask = smoothstep(0.45, 0.95, luma)
        out = np.clip(out + mask * highlights * 0.45, 0.0, 1.0)
    if abs(shadows) > 0:
        mask = 1.0 - smoothstep(0.05, 0.55, luma)
        out = np.clip(out + mask * shadows * 0.45, 0.0, 1.0)
    return out


def apply_white_balance(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    temperature = params.get("temperature", 0.0)
    tint = params.get("tint", 0.0)
    # Positive temperature warms: more red, less blue. Positive tint counters green cast.
    multipliers = np.array(
        [
            1.0 + 0.16 * temperature + 0.04 * tint,
            1.0 - 0.10 * tint,
            1.0 - 0.16 * temperature + 0.04 * tint,
        ],
        dtype=np.float32,
    )
    out = arr * multipliers.reshape(1, 1, 3)
    return np.clip(out, 0.0, 1.0)


def apply_color_intensity(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    sat_adjust = params.get("saturation", 0.0)
    vibrance = params.get("vibrance", 0.0)
    luma = luminance(arr)[..., None]
    current_sat = saturation(arr)[..., None]
    sat_scale = 1.0 + sat_adjust
    vibrance_scale = 1.0 + vibrance * (1.0 - np.clip(current_sat * 2.2, 0.0, 1.0))
    out = luma + (arr - luma) * sat_scale * vibrance_scale
    return np.clip(out, 0.0, 1.0)


def apply_channel_balance(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    multipliers = np.array(
        [
            1.0 + params.get("red", 0.0),
            1.0 + params.get("green", 0.0),
            1.0 + params.get("blue", 0.0),
        ],
        dtype=np.float32,
    )
    return np.clip(arr * multipliers.reshape(1, 1, 3), 0.0, 1.0)


def apply_regional_color_balance(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    out = arr.copy()
    luma = luminance(out)[..., None]
    shadow_mask = 1.0 - smoothstep(0.08, 0.55, luma)
    highlight_mask = smoothstep(0.45, 0.92, luma)

    out = apply_regional_color_balance_zone(out, params, "shadow", shadow_mask)
    out = apply_regional_color_balance_zone(out, params, "highlight", highlight_mask)
    return np.clip(out, 0.0, 1.0)


def apply_regional_color_balance_zone(
    arr: np.ndarray,
    params: dict[str, float],
    prefix: str,
    mask: np.ndarray,
) -> np.ndarray:
    luminance_delta = params.get(f"{prefix}_luminance", 0.0)
    saturation_delta = params.get(f"{prefix}_saturation", 0.0)
    brilliance_delta = params.get(f"{prefix}_brilliance", 0.0)
    hue = params.get(f"{prefix}_hue", 0.0)
    chroma = params.get(f"{prefix}_chroma", 0.0)

    out = arr
    if abs(luminance_delta) > 0:
        out = np.clip(out + mask * luminance_delta * 0.18, 0.0, 1.0)
    if abs(saturation_delta) > 0 or abs(brilliance_delta) > 0:
        local_luma = luminance(out)[..., None]
        out = local_luma + (out - local_luma) * (1.0 + mask * (saturation_delta + brilliance_delta * 0.5))
    if abs(hue) > 0.003 and abs(chroma) > 0.003:
        target = hue_degrees_to_rgb(hue).reshape(1, 1, 3)
        amount = np.clip(abs(chroma) * 0.18, 0.0, 0.18)
        if chroma > 0:
            out = out * (1.0 - mask * amount) + target * (mask * amount)
        else:
            local_luma = luminance(out)[..., None]
            out = local_luma + (out - local_luma) * (1.0 + mask * chroma * 0.3)
    return np.clip(out, 0.0, 1.0)


def hue_degrees_to_rgb(hue: float) -> np.ndarray:
    hue = (float(hue) % 360.0) / 60.0
    c = 1.0
    x = c * (1.0 - abs(hue % 2.0 - 1.0))
    if hue < 1:
        rgb = (c, x, 0.0)
    elif hue < 2:
        rgb = (x, c, 0.0)
    elif hue < 3:
        rgb = (0.0, c, x)
    elif hue < 4:
        rgb = (0.0, x, c)
    elif hue < 5:
        rgb = (x, 0.0, c)
    else:
        rgb = (c, 0.0, x)
    return np.array(rgb, dtype=np.float32)


def apply_tone_curve(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    luma = luminance(arr)[..., None]
    shadow_mask = 1.0 - smoothstep(0.05, 0.48, luma)
    mid_mask = 1.0 - np.abs(luma - 0.5) * 2.0
    mid_mask = np.clip(mid_mask, 0.0, 1.0)
    highlight_mask = smoothstep(0.52, 0.95, luma)
    delta = (
        shadow_mask * params.get("shadows", 0.0)
        + mid_mask * params.get("midtones", 0.0)
        + highlight_mask * params.get("highlights", 0.0)
    )
    return np.clip(arr + delta * 0.35, 0.0, 1.0)


def apply_vignette_correction(arr: np.ndarray, params: dict[str, float]) -> np.ndarray:
    correction = params.get("correction", 0.0)
    height, width = arr.shape[:2]
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xv, yv = np.meshgrid(x, y)
    distance = np.clip(np.sqrt(xv * xv + yv * yv), 0.0, 1.0)
    if correction >= 0:
        factor = 1.0 + correction * distance[..., None]
    else:
        factor = 1.0 + correction * (1.0 - distance[..., None])
    return np.clip(arr * factor, 0.0, 1.0)


def apply_hsl_zone(arr: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    image = float_to_pil(arr).convert("HSV")
    hsv = np.asarray(image, dtype=np.float32) / 255.0
    hue = hsv[..., 0]
    sat = hsv[..., 1]
    val = hsv[..., 2]
    center = HSL_ZONE_CENTERS[str(params["color"])]
    dist = np.abs(((hue - center + 0.5) % 1.0) - 0.5)
    mask = np.clip(1.0 - dist / 0.12, 0.0, 1.0)
    mask *= np.clip(sat * 2.0, 0.0, 1.0)
    hue = (hue + mask * params.get("hue_shift", 0.0)) % 1.0
    sat = np.clip(sat * (1.0 + mask * params.get("saturation", 0.0)), 0.0, 1.0)
    val = np.clip(val + mask * params.get("luminance", 0.0), 0.0, 1.0)
    out = np.stack([hue, sat, val], axis=2)
    out_img = Image.fromarray(np.clip(out * 255.0, 0, 255).astype(np.uint8), mode="HSV").convert("RGB")
    return pil_to_float(out_img)


def apply_sharpen(image: Image.Image, params: dict[str, float]) -> Image.Image:
    amount = params.get("amount", 0.0)
    if amount <= 0:
        return image
    blurred = image.filter(ImageFilter.GaussianBlur(radius=1.0))
    arr = pil_to_float(image)
    blur_arr = pil_to_float(blurred)
    return float_to_pil(np.clip(arr * (1.0 + amount) - blur_arr * amount, 0.0, 1.0))


def smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    x = np.clip((value - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


if __name__ == "__main__":
    raise SystemExit(main())
