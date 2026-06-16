from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import decision_core as core
import param_covert
import tool as dt_tools


DEFAULT_DARKTABLE_CLI = Path("C:/Program Files/darktable/bin/darktable-cli.exe")
DEFAULT_DARKTABLE_TIMEOUT = 180
EPSILON = 0.003
DARKTABLE_BLACK_SCALE = 0.62
DARKTABLE_SATURATION_SCALE = 0.25
DARKTABLE_VIBRANCE_SCALE = 0.25
DARKTABLE_VIGNETTE_SCALE = 0.15
DARKTABLE_SHADHI_SCALE = 80.0

LEGACY_BASE_LAYERS: list[dict[str, str]] = [
    {"op": "colorin", "ver": "7", "params": "gz48eJzjZBgFowABWAbaAaNgwAEAMNgADg=="},
    {"op": "colorout", "ver": "5", "params": "gz35eJxjZBgFo4CBAQAEEAAC"},
    {"op": "gamma", "ver": "1", "params": "0000000000000000"},
    {"op": "flip", "ver": "2", "params": "ffffffff", "multi_name": "_builtin_auto"},
]

# darktable 5.x applies the JPEG/sRGB pipeline itself when exporting with
# --icc-type SRGB. The legacy base layers from the original prototype override
# colorout and make JPEG exports much brighter, so the DT agent omits them.
BASE_LAYERS: list[dict[str, str]] = []


@dataclass
class AgentDTConfig:
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
    darktable_cli: Path
    darktable_config_dir: Path | None
    darktable_timeout: int
    dry_run_xmp: bool = False


def main() -> int:
    if len(sys.argv) == 1:
        try:
            run_guided_session()
        except Exception as exc:
            print(f"[FAILED] {exc}", file=sys.stderr)
            return 1
        return 0

    args = parse_args()
    config = AgentDTConfig(
        image_path=core.resolve_input_path(args.image).resolve(),
        output_path=core.ensure_output_extension(args.output).resolve(),
        prompt=args.prompt.strip(),
        planner_model=args.planner_model,
        vlm_model=None if args.no_vlm else core.normalize_optional_model(args.vlm_model),
        ollama_url=args.ollama_url,
        ollama_timeout=args.ollama_timeout,
        vlm_timeout=args.vlm_timeout,
        use_ollama=not args.no_ollama,
        max_side=args.max_side,
        vision_max_side=args.vision_max_side,
        metadata_path=args.metadata.resolve() if args.metadata else None,
        darktable_cli=args.darktable_cli,
        darktable_config_dir=args.darktable_config_dir.resolve() if args.darktable_config_dir else None,
        darktable_timeout=args.darktable_timeout,
        dry_run_xmp=args.dry_run_xmp,
    )

    if args.interactive:
        try:
            run_interactive(config)
        except Exception as exc:
            print(f"[FAILED] {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        result = run_agent_dt(config)
    except Exception as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "PhotoAgent DT. It uses the decision core for prompt planning and validation, "
            "then exports the validated edit plan through darktable-cli/XMP."
        )
    )
    parser.add_argument("--image", type=Path, required=True, help="Input image path.")
    parser.add_argument("--output", type=Path, default=Path("output.jpg"))
    parser.add_argument(
        "--prompt",
        default="Restore this photo to a natural, clean, professional, and understated tone.",
    )
    parser.add_argument("--planner-model", default=core.DEFAULT_PLANNER_MODEL)
    parser.add_argument("--vlm-model", default=core.DEFAULT_VLM_MODEL)
    parser.add_argument("--no-vlm", action="store_true", help="Skip optional VLM diagnosis.")
    parser.add_argument("--ollama-url", default=core.OLLAMA_URL)
    parser.add_argument("--ollama-timeout", type=int, default=240)
    parser.add_argument("--vlm-timeout", type=int, default=core.DEFAULT_VLM_TIMEOUT)
    parser.add_argument("--no-ollama", action="store_true", help="Use deterministic local fallback planning.")
    parser.add_argument("--max-side", type=int, default=1600)
    parser.add_argument("--vision-max-side", type=int, default=core.DEFAULT_VISION_MAX_SIDE)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--interactive", action="store_true", help="Keep accepting edit prompts.")
    parser.add_argument("--darktable-cli", type=Path, default=default_darktable_cli())
    parser.add_argument(
        "--darktable-config-dir",
        type=Path,
        default=None,
        help=(
            "Dedicated darktable config directory. Defaults to .darktable-agent next to the output file "
            "so darktable-cli does not fight the GUI database lock."
        ),
    )
    parser.add_argument("--darktable-timeout", type=int, default=DEFAULT_DARKTABLE_TIMEOUT)
    parser.add_argument("--dry-run-xmp", action="store_true", help="Write XMP/metadata without exporting an image.")
    return parser.parse_args()


def default_darktable_cli() -> Path:
    if DEFAULT_DARKTABLE_CLI.exists():
        return DEFAULT_DARKTABLE_CLI
    found = shutil.which("darktable-cli")
    return Path(found) if found else DEFAULT_DARKTABLE_CLI


def run_guided_session() -> None:
    script_dir = Path(__file__).resolve().parent
    default_image = script_dir / "input.jpg"
    default_output = script_dir / "output.jpg"
    default_prompt = "Restore this photo to a natural, clean, professional look."

    print("=== PhotoAgent DT quick mode ===")
    print("Press Enter to accept the default shown in brackets.")
    print("After each edit, type another request to keep editing, or type 'exit' to quit.")

    image_path = core.prompt_input_path("Image path", default_image, Path("input.jpg"), script_dir)
    output_path = core.prompt_output_path("Output path", default_output, Path("output.jpg"), script_dir)
    darktable_cli = prompt_darktable_cli(default_darktable_cli())
    darktable_timeout = core.prompt_int("darktable-cli timeout seconds", DEFAULT_DARKTABLE_TIMEOUT)

    use_vlm_text = input("Use VLM diagnosis? [y/N]: ").strip().lower()
    vlm_model = None
    vlm_timeout = core.DEFAULT_VLM_TIMEOUT
    vision_max_side = core.DEFAULT_VISION_MAX_SIDE
    if use_vlm_text in {"y", "yes"}:
        vlm_model = input(f"VLM model [{core.DEFAULT_VLM_MODEL}]: ").strip() or core.DEFAULT_VLM_MODEL
        vlm_timeout = core.prompt_int("VLM timeout seconds", core.DEFAULT_VLM_TIMEOUT)
        vision_max_side = core.prompt_int("VLM image max side", core.DEFAULT_VISION_MAX_SIDE)

    base_config = AgentDTConfig(
        image_path=image_path.resolve(),
        output_path=output_path.resolve(),
        prompt=default_prompt,
        planner_model=core.DEFAULT_PLANNER_MODEL,
        vlm_model=vlm_model,
        ollama_url=core.OLLAMA_URL,
        ollama_timeout=240,
        vlm_timeout=vlm_timeout,
        use_ollama=True,
        max_side=1600,
        vision_max_side=vision_max_side,
        metadata_path=None,
        darktable_cli=darktable_cli,
        darktable_config_dir=script_dir / ".darktable-agent",
        darktable_timeout=darktable_timeout,
    )

    current_input = image_path.resolve()
    turn = 1
    while True:
        label = f"Edit request [{default_prompt}]" if turn == 1 else "Edit request"
        prompt = input(f"{label}: ").strip()
        if prompt.lower() in {"exit", "quit", "q"}:
            print("Exiting PhotoAgent DT.")
            return
        if not prompt:
            if turn == 1:
                prompt = default_prompt
            else:
                continue

        turn_output = output_path.resolve() if turn == 1 else core.numbered_output_path(output_path.resolve(), turn)
        turn_config = replace(
            base_config,
            image_path=current_input,
            output_path=turn_output,
            prompt=prompt,
        )
        result = run_agent_dt(turn_config)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        current_input = turn_output
        turn += 1


def prompt_darktable_cli(default: Path) -> Path:
    raw = input(f"darktable-cli [{default}]: ").strip()
    return Path(raw) if raw else default


def run_interactive(config: AgentDTConfig) -> None:
    current_input = config.image_path
    print("=== PhotoAgent DT interactive mode ===")
    print(f"Input image: {current_input}")
    print("Type an edit request, or type 'exit' to quit.")

    turn = 1
    while True:
        prompt = input("Edit request: ").strip()
        if prompt.lower() in {"exit", "quit", "q"}:
            print("Exiting PhotoAgent DT.")
            return
        if not prompt:
            continue

        turn_output = core.numbered_output_path(config.output_path, turn)
        turn_config = replace(
            config,
            image_path=current_input,
            output_path=turn_output,
            prompt=prompt,
            metadata_path=None,
        )
        result = run_agent_dt(turn_config)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        current_input = turn_output
        turn += 1


def run_agent_dt(config: AgentDTConfig) -> dict[str, Any]:
    if not config.image_path.exists():
        raise FileNotFoundError(f"Input image does not exist: {config.image_path}")
    if not config.prompt:
        raise ValueError("Prompt cannot be empty.")

    plan_bundle = build_validated_plan(config)
    tool_run = execute_plan_with_darktable_tools(plan_bundle["validated_plan"], config)
    if not tool_run["applied_operations"]:
        warnings = "; ".join(tool_run["warnings"])
        raise RuntimeError(f"No darktable-supported operations remained after tool execution. {warnings}")

    xmp_path = tool_run["xmp_path"]

    metadata_tool_run = {
        **tool_run,
        "xmp_path": str(tool_run["xmp_path"].resolve()),
    }
    metadata = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": "darktable-cli",
        "input_path": str(config.image_path),
        "output_path": str(config.output_path.resolve()),
        "input_trace": discover_input_trace(config.image_path),
        "xmp_path": str(xmp_path.resolve()),
        "prompt": config.prompt,
        "planner_model": config.planner_model,
        "vlm_model": config.vlm_model,
        "agent_mode": "decision_core_darktable_export",
        "local_diagnostics": plan_bundle["local_diagnostics"],
        "vlm_diagnostics": plan_bundle["vlm_diagnostics"],
        "vlm_raw": plan_bundle["vlm_raw"],
        "planner_raw": plan_bundle["planner_raw"],
        "validated_plan": plan_bundle["validated_plan"],
        "available_darktable_tools": dt_tools.available_tool_summary(include_export=False),
        "darktable_layers": tool_run["layers"],
        "xmp_consistency": tool_run["xmp_consistency"],
        "darktable_config_dir": str(resolve_darktable_config_dir(config.output_path, config.darktable_config_dir)),
        "darktable_tool_execution": metadata_tool_run,
        "darktable_result": tool_run["darktable_result"],
    }
    metadata_path = config.metadata_path or config.output_path.with_suffix(
        config.output_path.suffix + ".metadata.json"
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = list(plan_bundle["validated_plan"].get("warnings", []))
    warnings.extend(tool_run["warnings"])
    summary = {
        "status": "success" if not config.dry_run_xmp else "xmp_written_dry_run",
        "backend": "darktable-cli",
        "input_path": str(config.image_path),
        "output_path": str(config.output_path.resolve()),
        "xmp_path": str(xmp_path.resolve()),
        "metadata_path": str(metadata_path.resolve()),
        "xmp_consistency": tool_run["xmp_consistency"],
        "vlm_summary": core.summarize_vlm(plan_bundle["vlm_diagnostics"]),
        "applied_tools": [op["tool"] for op in tool_run["applied_operations"]],
        "operation_details": tool_run["applied_operations"],
        "intent_summary": plan_bundle["validated_plan"].get("intent_summary", ""),
        "warnings": warnings,
    }
    return {"summary": summary, "metadata": metadata}


def build_validated_plan(config: AgentDTConfig) -> dict[str, Any]:
    image = core.load_image(config.image_path, config.max_side)
    local_diagnostics = core.compute_image_diagnostics(image)
    vlm_diagnostics = None
    vlm_raw = None
    planner_raw = None

    if config.use_ollama and config.vlm_model:
        try:
            vlm_raw = core.call_vlm_diagnostics(
                config.ollama_url,
                config.vlm_model,
                config.image_path,
                local_diagnostics,
                config.vision_max_side,
                config.vlm_timeout,
            )
            vlm_diagnostics = core.sanitize_vlm_diagnostics(core.parse_json_object(vlm_raw))
        except Exception as exc:
            vlm_diagnostics = {
                "status": "vlm_failed_local_diagnostics_used",
                "error": str(exc),
            }

    if config.use_ollama:
        try:
            planner_raw = core.call_planner(
                config.ollama_url,
                config.planner_model,
                config.prompt,
                local_diagnostics,
                vlm_diagnostics,
                config.ollama_timeout,
            )
            plan = core.parse_json_object(planner_raw)
            diagnostics = plan.get("diagnostics")
            if isinstance(diagnostics, dict):
                vlm_diagnostics = diagnostics
        except Exception as exc:
            plan = core.build_fallback_plan(config.prompt, local_diagnostics, str(exc))
    else:
        plan = core.build_fallback_plan(config.prompt, local_diagnostics, "Ollama disabled.")

    validated_plan = core.validate_plan(plan, config.prompt, local_diagnostics)
    return {
        "local_diagnostics": local_diagnostics,
        "vlm_diagnostics": vlm_diagnostics,
        "vlm_raw": vlm_raw,
        "planner_raw": planner_raw,
        "validated_plan": validated_plan,
    }


def discover_input_trace(image_path: Path) -> dict[str, Any]:
    xmp_path = image_path.with_suffix(image_path.suffix + ".xmp")
    metadata_path = image_path.with_suffix(image_path.suffix + ".metadata.json")
    return {
        "input_xmp_path": str(xmp_path.resolve()) if xmp_path.exists() else None,
        "input_metadata_path": str(metadata_path.resolve()) if metadata_path.exists() else None,
    }


def execute_plan_with_darktable_tools(
    validated_plan: dict[str, Any],
    config: AgentDTConfig,
) -> dict[str, Any]:
    dt_tools.current_adjustments = dt_tools.base_layers.copy()
    applied_operations: list[dict[str, Any]] = []
    warnings: list[str] = []
    color_balance: dict[str, Any] = {
        "shadow_hue": 0.0,
        "highlight_hue": 0.0,
        "saturation": 0.0,
        "hue_shift": 0.0,
        "brilliance": 0.0,
        "vibrance": 0.0,
        "contrast": 0.0,
        "saturation_formula": 1,
        "shadow_luminance": 0.0,
        "shadow_chroma": 0.0,
        "shadow_saturation": 0.0,
        "shadow_brilliance": 0.0,
        "highlight_luminance": 0.0,
        "highlight_chroma": 0.0,
        "highlight_saturation": 0.0,
        "highlight_brilliance": 0.0,
        "midtone_luminance": 0.0,
        "midtone_chroma": 0.0,
        "midtone_saturation": 0.0,
        "midtone_brilliance": 0.0,
        "global_luminance": 0.0,
        "global_chroma": 0.0,
        "global_hue": 0.0,
    }
    color_balance_sources: list[str] = []
    shadows_highlights_applied = False

    try:
        for operation in validated_plan.get("operations", []):
            source_tool = operation.get("tool")
            params = dict(operation.get("params") or {})
            reason = str(operation.get("reason") or "")

            if source_tool == "basic_tone":
                exposure = clamp_float(params.get("exposure_ev", 0.0), -1.5, 1.5)
                v2_black_level = clamp_float(params.get("black_level", 0.0), -0.18, 0.18)
                darktable_black = clamp_float(-v2_black_level * DARKTABLE_BLACK_SCALE, -0.18, 0.18)
                if abs(exposure) >= EPSILON or abs(darktable_black) >= EPSILON:
                    tool_params = {"exposure": exposure, "black_level": darktable_black}
                    result = dt_tools.adjust_exposure.invoke(tool_params)
                    operation_reason = reason
                    if abs(exposure) < EPSILON and abs(darktable_black) >= EPSILON:
                        operation_reason = (
                            "Clean the lifted black level while leaving exposure unchanged."
                            if not reason
                            else f"{reason} Black-level cleanup applied without exposure change."
                        )
                    applied_operations.append(
                        {
                            "tool": "adjust_exposure",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": operation_reason,
                            "result": result,
                            "mapping_notes": {
                                "black_level": (
                                    "decision_core uses negative black_level to lower lifted blacks; "
                                    "tool.py is darktable-native, so agent_dt passes darktable exposure.black."
                                )
                            },
                        }
                    )

                contrast = clamp_float(params.get("contrast", 0.0), -0.4, 0.5)
                if abs(contrast) >= EPSILON:
                    color_balance["contrast"] = clamp_float(color_balance["contrast"] + contrast, -1.0, 1.0)
                    color_balance_sources.append("basic_tone.contrast")

                highlights = clamp_float(params.get("highlights", 0.0), -0.45, 0.45)
                shadows = clamp_float(params.get("shadows", 0.0), -0.45, 0.45)
                if abs(highlights) >= 0.08 or abs(shadows) >= 0.08:
                    tool_params = shadows_highlights_params_from_v2(highlights, shadows)
                    result = dt_tools.adjust_shadows_highlights.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_shadows_highlights",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "shadows_highlights": (
                                    "decision_core uses abstract highlight/shadow deltas; "
                                    "agent_dt maps them to darktable shadhi shadows/highlights sliders."
                                )
                            },
                        }
                    )
                    shadows_highlights_applied = True
                continue

            if source_tool == "white_balance":
                temperature = clamp_float(params.get("temperature", 0.0), -1.0, 1.0)
                tint = clamp_float(params.get("tint", 0.0), -1.0, 1.0)
                if abs(temperature) >= EPSILON or abs(tint) >= EPSILON:
                    tool_params = temperature_coefficients_from_v2(temperature, tint)
                    result = dt_tools.adjust_temperature.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_temperature",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "temperature": (
                                    "decision_core uses abstract warmer/cooler values; "
                                    "tool.py receives darktable temperature RGB coefficients."
                                )
                            },
                        }
                    )
                continue

            if source_tool == "color_intensity":
                saturation = clamp_float(params.get("saturation", 0.0), -0.6, 0.6) * DARKTABLE_SATURATION_SCALE
                vibrance = clamp_float(params.get("vibrance", 0.0), -0.6, 0.6) * DARKTABLE_VIBRANCE_SCALE
                if abs(saturation) >= EPSILON or abs(vibrance) >= EPSILON:
                    color_balance["saturation"] = clamp_float(color_balance["saturation"] + saturation, -1.0, 1.0)
                    color_balance["vibrance"] = clamp_float(color_balance["vibrance"] + vibrance, -1.0, 1.0)
                    color_balance_sources.append("color_intensity")
                continue

            if source_tool == "tone_curve":
                shadows = clamp_float(params.get("shadows", 0.0), -0.35, 0.35)
                midtones = clamp_float(params.get("midtones", 0.0), -0.35, 0.35)
                highlights = clamp_float(params.get("highlights", 0.0), -0.35, 0.35)
                tool_params = rgb_levels_params_from_tone_curve(shadows, midtones, highlights)
                if (
                    abs(tool_params["black_point"]) >= EPSILON
                    or abs(tool_params["mid_gray"] - 0.5) >= EPSILON
                    or abs(tool_params["white_point"] - 1.0) >= EPSILON
                ):
                    result = dt_tools.adjust_rgb_levels.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_rgb_levels",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "tone_curve": (
                                    "decision_core uses abstract shadow/midtone/highlight curve values; "
                                    "agent_dt maps them to darktable rgblevels black point, mid-gray, and white point."
                                ),
                                "mid_gray": (
                                    "darktable rgblevels mid_gray below 0.5 brightens midtones; "
                                    "mid_gray above 0.5 darkens midtones."
                                ),
                            },
                        }
                    )
                if not shadows_highlights_applied and (abs(highlights) >= 0.08 or abs(shadows) >= 0.08):
                    tool_params = shadows_highlights_params_from_v2(
                        highlights=highlights * 0.7,
                        shadows=shadows * 0.7,
                    )
                    result = dt_tools.adjust_shadows_highlights.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_shadows_highlights",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": "Apply the shadow/highlight part of the tonal curve with darktable shadhi.",
                            "result": result,
                            "mapping_notes": {
                                "tone_curve_shadows_highlights": (
                                    "RGB levels handles mid-gray/gamma; shadhi handles explicit shadow/highlight zone changes."
                                )
                            },
                        }
                    )
                    shadows_highlights_applied = True
                continue

            if source_tool == "sharpen":
                amount = clamp_float(params.get("amount", 0.0), 0.0, 0.8)
                if amount >= EPSILON:
                    tool_params = {
                        "highlights": 0.5,
                        "shadows": 0.5,
                        "detail": clamp_float(0.25 + amount * 0.8, 0.0, 1.0),
                        "midtone_range": 0.5,
                    }
                    result = dt_tools.adjust_local_contrast.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_local_contrast",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "sharpen": (
                                    "decision_core uses abstract sharpness amount; "
                                    "agent_dt maps it to darktable bilat/local contrast detail."
                                )
                            },
                        }
                    )
                continue

            if source_tool == "hsl_zone":
                color = str(params.get("color") or "").strip().lower()
                hue_shift = clamp_float(params.get("hue_shift", 0.0), -0.15, 0.15)
                saturation = clamp_float(params.get("saturation", 0.0), -0.5, 0.5)
                luminance = clamp_float(params.get("luminance", 0.0), -0.35, 0.35)
                tool_params = color_zone_params_from_v2(color, hue_shift, saturation, luminance)
                if tool_params and any(abs(float(tool_params[key])) >= EPSILON for key in ("hue_shift", "saturation", "luminance")):
                    result = dt_tools.adjust_color_zones.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_color_zones",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "hsl_zone": (
                                    "decision_core selects a named color; agent_dt maps it to darktable "
                                    "colorzones hue-centered curves."
                                )
                            },
                        }
                    )
                else:
                    warnings.append(f"Skipped hsl_zone because color or deltas were ineffective: {params}")
                continue

            if source_tool == "regional_color_balance":
                regional_keys = {
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
                }
                changed = False
                for key, (low, high) in regional_keys.items():
                    value = clamp_float(params.get(key, 0.0), low, high)
                    if key.endswith("_hue") and abs(value) < EPSILON:
                        value = 0.0
                    color_balance[key] = value
                    changed = changed or abs(value) >= EPSILON
                if changed:
                    color_balance_sources.append("regional_color_balance")
                continue

            if source_tool == "vignette":
                correction = clamp_float(params.get("correction", 0.0), -0.5, 0.5)
                darktable_brightness = correction * DARKTABLE_VIGNETTE_SCALE
                if abs(darktable_brightness) >= EPSILON:
                    tool_params = {
                        "scale": 80.0,
                        "falloff_scale": 50.0,
                        "brightness": darktable_brightness,
                        "saturation": 0.0,
                        "center_x": 0.0,
                        "center_y": 0.0,
                        "autoratio": 1,
                        "whratio": 1.0,
                        "shape": 1.0,
                        "dithering": 0,
                        "unbound": 1,
                    }
                    result = dt_tools.adjust_vignette.invoke(tool_params)
                    applied_operations.append(
                        {
                            "tool": "adjust_vignette",
                            "source_operation": source_tool,
                            "tool_params": tool_params,
                            "source_params": params,
                            "reason": reason,
                            "result": result,
                            "mapping_notes": {
                                "vignette": (
                                    "decision_core uses abstract correction; "
                                    f"tool.py receives darktable vignette.brightness scaled by {DARKTABLE_VIGNETTE_SCALE}."
                                )
                            },
                        }
                    )
                continue

            warnings.append(f"Skipped unsupported darktable tool mapping for source operation: {source_tool}")

        color_balance_effect_keys = [
            "shadow_hue",
            "highlight_hue",
            "saturation",
            "hue_shift",
            "brilliance",
            "vibrance",
            "contrast",
            "shadow_luminance",
            "shadow_chroma",
            "shadow_saturation",
            "shadow_brilliance",
            "highlight_luminance",
            "highlight_chroma",
            "highlight_saturation",
            "highlight_brilliance",
            "midtone_luminance",
            "midtone_chroma",
            "midtone_saturation",
            "midtone_brilliance",
            "global_luminance",
            "global_chroma",
            "global_hue",
        ]
        if any(abs(float(color_balance[key])) >= EPSILON for key in color_balance_effect_keys):
            result = dt_tools.adjust_color_balance_rgb.invoke(color_balance)
            applied_operations.append(
                {
                    "tool": "adjust_color_balance_rgb",
                    "source_operation": "+".join(color_balance_sources) or "color_balance",
                    "tool_params": dict(color_balance),
                    "reason": "Aggregated darktable color balance RGB adjustment.",
                    "result": result,
                }
            )

        layers = [dict(layer) for layer in dt_tools.current_adjustments]
        xmp_path = config.output_path.with_suffix(config.output_path.suffix + ".xmp")
        param_covert.write_xmp(layers, xmp_path, config.image_path)
        xmp_consistency = param_covert.verify_xmp_matches_layers(layers, xmp_path)
        if not xmp_consistency["matches"]:
            raise RuntimeError(
                "Generated XMP does not match the executed darktable layers: "
                f"{xmp_consistency['mismatches']}"
            )

        darktable_result: dict[str, Any] = {"status": "dry_run_xmp_only"}
        if not config.dry_run_xmp:
            darktable_result = run_darktable_cli(
                config.darktable_cli,
                config.image_path,
                xmp_path,
                config.output_path,
                config.darktable_timeout,
                config.darktable_config_dir,
            )

        return {
            "layers": layers,
            "xmp_path": xmp_path,
            "xmp_consistency": xmp_consistency,
            "applied_operations": applied_operations,
            "warnings": warnings,
            "darktable_result": darktable_result,
        }
    finally:
        dt_tools.current_adjustments = dt_tools.base_layers.copy()


def local_contrast_params_from_v2(highlights: float, shadows: float) -> dict[str, float]:
    return {
        "highlights": clamp_float(0.5 + max(highlights, 0.0) * 0.8, 0.05, 2.0),
        "shadows": clamp_float(0.5 + max(shadows, 0.0) * 0.8, 0.05, 2.0),
        "detail": clamp_float(0.25 + max(abs(highlights), abs(shadows)) * 0.6, 0.0, 1.0),
        "midtone_range": 0.5,
    }


def shadows_highlights_params_from_v2(highlights: float, shadows: float) -> dict[str, float]:
    return {
        "shadows": clamp_float(shadows * DARKTABLE_SHADHI_SCALE, -100.0, 100.0),
        "highlights": clamp_float(highlights * DARKTABLE_SHADHI_SCALE, -100.0, 100.0),
        "radius": 100.0,
        "whitepoint": 0.0,
        "compress": 50.0,
        "shadows_ccorrect": 100.0,
        "highlights_ccorrect": 50.0,
    }


def temperature_coefficients_from_v2(temperature: float, tint: float) -> dict[str, float | int]:
    return {
        "red": clamp_float(1.0 + temperature * 0.12 + tint * 0.04, 0.05, 8.0),
        "green": clamp_float(1.0 - tint * 0.06, 0.05, 8.0),
        "blue": clamp_float(1.0 - temperature * 0.12 + tint * 0.04, 0.05, 8.0),
        "various": 1.0,
        "preset": 2,
    }


def color_zone_params_from_v2(
    color: str,
    hue_shift: float,
    saturation: float,
    luminance: float,
) -> dict[str, float] | None:
    center = core.HSL_ZONE_CENTERS.get(color)
    if center is None:
        return None
    return {
        "color_center": center,
        "hue_shift": hue_shift,
        "saturation": saturation,
        "luminance": luminance,
        "width": 0.16,
        "strength": 0.0,
    }


def rgb_levels_params_from_tone_curve(
    shadows: float,
    midtones: float,
    highlights: float,
) -> dict[str, float | int]:
    black_point = clamp_float(max(0.0, -shadows) * 0.10, 0.0, 0.12)
    white_point = clamp_float(1.0 - max(0.0, highlights) * 0.10, 0.82, 1.0)
    mid_gray = clamp_float(0.5 - midtones * 0.28, 0.25, 0.75)
    mid_gray = clamp_float(mid_gray, black_point + 0.02, white_point - 0.02)
    return {
        "black_point": black_point,
        "mid_gray": mid_gray,
        "white_point": white_point,
        "autoscale": 0,
        "preserve_colors": 1,
    }


def run_darktable_cli(
    darktable_cli: Path,
    image_path: Path,
    xmp_path: Path,
    output_path: Path,
    timeout: int,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    executable = resolve_darktable_executable(darktable_cli)
    resolved_config_dir = resolve_darktable_config_dir(output_path, config_dir)
    resolved_config_dir.mkdir(parents=True, exist_ok=True)
    library_path = ":memory:"
    export_dir = resolved_config_dir / "exports" / f"{output_path.stem}-{int(time.time() * 1000)}"
    export_dir.mkdir(parents=True, exist_ok=True)
    out_ext = (output_path.suffix or ".jpg").lstrip(".")
    command = [
        executable,
        darktable_path(image_path),
        darktable_path(xmp_path),
        darktable_path(export_dir),
        "--out-ext",
        out_ext,
        "--icc-type",
        "SRGB",
        "--apply-custom-presets",
        "false",
        "--core",
        "--configdir",
        darktable_path(resolved_config_dir),
        "--library",
        library_path,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"darktable-cli timed out after {timeout} seconds.") from exc
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"darktable-cli not found: {darktable_cli}. "
            "Install darktable or pass --darktable-cli with the correct path."
        ) from exc

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"darktable-cli failed with code {completed.returncode}: {message}")

    exported_file = newest_exported_file(export_dir)
    if exported_file is None:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"darktable-cli reported success but no exported file was found. {message}")

    exported_file.replace(output_path)
    try:
        export_dir.rmdir()
    except OSError:
        pass

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"darktable-cli finished but did not create a valid output: {output_path}")

    return {
        "status": "success",
        "command": command,
        "config_dir": str(resolved_config_dir),
        "library_path": library_path,
        "temporary_export_dir": str(export_dir),
        "exported_source": str(exported_file),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def newest_exported_file(export_dir: Path) -> Path | None:
    candidates = [path for path in export_dir.rglob("*") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def darktable_path(path: Path) -> str:
    return path.resolve().as_posix()


def resolve_darktable_config_dir(output_path: Path, config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir.resolve()
    return (output_path.parent / ".darktable-agent").resolve()


def resolve_darktable_executable(darktable_cli: Path) -> str:
    if darktable_cli.exists():
        return str(darktable_cli)
    found = shutil.which(str(darktable_cli))
    if found:
        return found
    return str(darktable_cli)


def clamp_float(value: Any, low: float, high: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return max(low, min(high, numeric))


if __name__ == "__main__":
    raise SystemExit(main())
