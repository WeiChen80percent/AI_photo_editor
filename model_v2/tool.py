from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from langchain_core.tools import tool

import param_covert


EXPOSURE_BLENDOP = "gz08eJxjYGBgYAFiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+AwGIBgCbGCeh"
BILAT_BLENDOP = "gz10eJxjYGBgYAJiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAG2yHQc="
DEFAULT_BLENDOP = "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="

# Kept only as a reference to the original prototype. darktable 5.x builds the
# correct JPEG/sRGB base pipeline itself when exporting with --icc-type SRGB.
# Adding these old base layers to a JPEG XMP can make exports much brighter.
legacy_base_layers = [
    {"op": "colorin", "ver": "7", "params": "gz48eJzjZBgFowABWAbaAaNgwAEAMNgADg=="},
    {"op": "colorout", "ver": "5", "params": "gz35eJxjZBgFo4CBAQAEEAAC"},
    {"op": "gamma", "ver": "1", "params": "0000000000000000"},
    {"op": "flip", "ver": "2", "params": "ffffffff", "multi_name": "_builtin_auto"},
]

base_layers: list[dict[str, str]] = []
current_adjustments = base_layers.copy()

DARKTABLE_TOOL_SPECS = {
    "adjust_exposure": {
        "darktable_operation": "exposure",
        "iop_file": "iop/exposure.c",
        "version": "7",
        "purpose": "Exposure and darktable-native black level correction.",
        "params": ["exposure", "black_level"],
    },
    "adjust_local_contrast": {
        "darktable_operation": "bilat",
        "iop_file": "iop/bilat.c",
        "version": "3",
        "purpose": "Local contrast/detail via darktable bilat module.",
        "params": ["highlights", "shadows", "detail", "midtone_range"],
    },
    "adjust_shadows_highlights": {
        "darktable_operation": "shadhi",
        "iop_file": "iop/shadhi.c",
        "version": "5",
        "purpose": "Dedicated shadows/highlights tonal-zone correction.",
        "params": [
            "shadows",
            "highlights",
            "radius",
            "whitepoint",
            "compress",
            "shadows_ccorrect",
            "highlights_ccorrect",
        ],
    },
    "adjust_color_balance_rgb": {
        "darktable_operation": "colorbalancergb",
        "iop_file": "iop/colorbalancergb.c",
        "version": "5",
        "purpose": "Saturation, vibrance, contrast, hue shift, and precise shadow/highlight color balance.",
        "params": [
            "shadow_hue",
            "shadow_luminance",
            "shadow_chroma",
            "shadow_saturation",
            "shadow_brilliance",
            "highlight_hue",
            "highlight_luminance",
            "highlight_chroma",
            "highlight_saturation",
            "highlight_brilliance",
            "saturation",
            "hue_shift",
            "brilliance",
            "vibrance",
            "contrast",
            "saturation_formula",
        ],
    },
    "adjust_temperature": {
        "darktable_operation": "temperature",
        "iop_file": "iop/temperature.c",
        "version": "4",
        "purpose": "White balance using darktable-native RGB coefficients.",
        "params": ["red", "green", "blue", "various", "preset"],
    },
    "adjust_rgb_levels": {
        "darktable_operation": "rgblevels",
        "iop_file": "iop/rgblevels.c",
        "version": "1",
        "purpose": "Black point, white point, and mid-gray/gamma-like tone correction.",
        "params": ["black_point", "mid_gray", "white_point", "autoscale", "preserve_colors"],
    },
    "adjust_color_zones": {
        "darktable_operation": "colorzones",
        "iop_file": "iop/colorzones.c",
        "version": "5",
        "purpose": "Hue-selected HSL color-zone correction.",
        "params": ["color_center", "hue_shift", "saturation", "luminance", "width", "strength"],
    },
    "adjust_vignette": {
        "darktable_operation": "vignette",
        "iop_file": "iop/vignette.c",
        "version": "4",
        "purpose": "Vignette brightness/saturation correction.",
        "params": [
            "scale",
            "falloff_scale",
            "brightness",
            "saturation",
            "center_x",
            "center_y",
            "autoratio",
            "whratio",
            "shape",
            "dithering",
            "unbound",
        ],
    },
    "apply_xmp_and_export": {
        "darktable_operation": "export",
        "iop_file": None,
        "version": None,
        "purpose": "Write the accumulated XMP history and export with darktable-cli.",
        "params": ["image_path", "output_path"],
    },
}


def _upsert_layer(new_layer: dict[str, str]) -> None:
    for i, layer in enumerate(current_adjustments):
        if layer["op"] == new_layer["op"]:
            current_adjustments[i] = new_layer
            return
    current_adjustments.append(new_layer)


def _darktable_path(path: Path | str) -> str:
    return Path(path).resolve().as_posix()


def _darktable_cli_path() -> str:
    default_path = Path("C:/Program Files/darktable/bin/darktable-cli.exe")
    if default_path.exists():
        return str(default_path)
    found = shutil.which("darktable-cli")
    if found:
        return found
    return str(default_path)


def _newest_file(folder: Path) -> Path | None:
    files = [path for path in folder.rglob("*") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def available_tool_summary(include_export: bool = True) -> list[dict[str, object]]:
    names = list(DARKTABLE_TOOL_SPECS)
    if not include_export:
        names = [name for name in names if name != "apply_xmp_and_export"]
    return [{"name": name, **DARKTABLE_TOOL_SPECS[name]} for name in names]


@tool
def adjust_exposure(exposure: float = 0.0, black_level: float = 0.0) -> str:
    """
    Adjust darktable's exposure module.

    Matches iop/exposure.c dt_iop_exposure_params_t v7:
    mode, black, exposure, deflicker_percentile, deflicker_target_level,
    compensate_exposure_bias, compensate_hilite_pres.

    exposure: darktable exposure value, range -18.0 to 18.0.
    black_level: darktable exposure.black exactly, range -1.0 to 1.0.
      - Positive values deepen/crush blacks.
      - Negative values lift blacks and create milky or faded shadows.
    """
    params = param_covert.encode_exposure_params(exposure, black_level)
    _upsert_layer({"op": "exposure", "ver": "7", "params": params, "blendop": EXPOSURE_BLENDOP})
    print(f"Executing exposure adjustment: exposure={exposure}, black={black_level}")
    return "Exposure adjustment applied"


@tool
def adjust_local_contrast(
    highlights: float = 0.50,
    shadows: float = 0.50,
    detail: float = 0.25,
    midtone_range: float = 0.5,
) -> str:
    """
    Adjust darktable's local contrast module, operation name bilat.

    Matches iop/bilat.c dt_iop_bilat_params_t v3:
    mode, sigma_r, sigma_s, detail, midtone.

    highlights maps to sigma_r, shadows maps to sigma_s, detail maps to detail,
    and midtone_range maps to midtone.
    """
    params = param_covert.encode_contrast_params(highlights, shadows, detail, midtone_range)
    _upsert_layer({"op": "bilat", "ver": "3", "params": params, "blendop": BILAT_BLENDOP})
    print(
        "Executing local contrast adjustment: "
        f"highlights={highlights}, shadows={shadows}, detail={detail}, midtone={midtone_range}"
    )
    return "Local contrast adjustment applied"


@tool
def adjust_shadows_highlights(
    shadows: float = 0.0,
    highlights: float = 0.0,
    radius: float = 100.0,
    whitepoint: float = 0.0,
    compress: float = 50.0,
    shadows_ccorrect: float = 100.0,
    highlights_ccorrect: float = 50.0,
) -> str:
    """
    Adjust darktable's shadows and highlights module, operation name shadhi.

    Matches iop/shadhi.c dt_iop_shadhi_params_t v5:
    order, radius, shadows, whitepoint, highlights, reserved2, compress,
    shadows_ccorrect, highlights_ccorrect, flags, low_approximation,
    shadhi_algo.

    shadows/highlights are darktable-native slider values in [-100, 100].
    Positive shadows lifts shadows; negative highlights recovers highlights.
    """
    params = param_covert.encode_shadows_highlights_params(
        shadows=shadows,
        highlights=highlights,
        radius=radius,
        whitepoint=whitepoint,
        compress=compress,
        shadows_ccorrect=shadows_ccorrect,
        highlights_ccorrect=highlights_ccorrect,
    )
    _upsert_layer({"op": "shadhi", "ver": "5", "params": params, "blendop": DEFAULT_BLENDOP})
    print(
        "Executing shadows/highlights adjustment: "
        f"shadows={shadows}, highlights={highlights}, radius={radius}, compress={compress}"
    )
    return "Shadows/highlights adjustment applied"


@tool
def adjust_color_balance_rgb(
    shadow_hue: float = 0.0,
    highlight_hue: float = 0.0,
    saturation: float = 0.0,
    hue_shift: float = 0.0,
    brilliance: float = 0.0,
    vibrance: float = 0.0,
    contrast: float = 0.0,
    saturation_formula: int = 1,
    shadow_luminance: float = 0.0,
    shadow_chroma: float = 0.0,
    shadow_saturation: float = 0.0,
    shadow_brilliance: float = 0.0,
    highlight_luminance: float = 0.0,
    highlight_chroma: float = 0.0,
    highlight_saturation: float = 0.0,
    highlight_brilliance: float = 0.0,
    midtone_luminance: float = 0.0,
    midtone_chroma: float = 0.0,
    midtone_saturation: float = 0.0,
    midtone_brilliance: float = 0.0,
    global_luminance: float = 0.0,
    global_chroma: float = 0.0,
    global_hue: float = 0.0,
) -> str:
    """
    Adjust darktable's color balance RGB module.

    Matches iop/colorbalancergb.c dt_iop_colorbalancergb_params_t v5,
    packed as 32 floats plus one int and zlib/base64 encoded with the gz03
    darktable params prefix.

    Exposed fields:
    shadow_hue -> shadows_H with shadows_C activated.
    highlight_hue -> highlights_H with highlights_C activated.
    shadow/highlight luminance, chroma, saturation, brilliance -> regional controls.
    saturation -> saturation_global.
    hue_shift -> hue_angle.
    brilliance -> brilliance_global.
    vibrance -> vibrance.
    contrast -> contrast.
    saturation_formula: 0 = JzAzBz (2021), 1 = darktable UCS (2022), darktable default.
    """
    params = param_covert.encode_colorbalancergb_params(
        shadow_hue,
        highlight_hue,
        saturation,
        hue_shift,
        brilliance,
        vibrance,
        contrast,
        saturation_formula=saturation_formula,
        shadow_luminance=shadow_luminance,
        shadow_chroma=shadow_chroma,
        shadow_saturation=shadow_saturation,
        shadow_brilliance=shadow_brilliance,
        highlight_luminance=highlight_luminance,
        highlight_chroma=highlight_chroma,
        highlight_saturation=highlight_saturation,
        highlight_brilliance=highlight_brilliance,
        midtone_luminance=midtone_luminance,
        midtone_chroma=midtone_chroma,
        midtone_saturation=midtone_saturation,
        midtone_brilliance=midtone_brilliance,
        global_luminance=global_luminance,
        global_chroma=global_chroma,
        global_hue=global_hue,
    )
    _upsert_layer({"op": "colorbalancergb", "ver": "5", "params": params, "blendop": EXPOSURE_BLENDOP})
    print(
        "Executing color balance rgb adjustment: "
        f"shadow_hue={shadow_hue}, highlight_hue={highlight_hue}, saturation={saturation}, "
        f"hue_shift={hue_shift}, brilliance={brilliance}, vibrance={vibrance}, contrast={contrast}, "
        f"shadow_luminance={shadow_luminance}, highlight_luminance={highlight_luminance}"
    )
    return "Color balance RGB adjustment applied"


@tool
def adjust_temperature(
    red: float = 1.0,
    green: float = 1.0,
    blue: float = 1.0,
    various: float = 1.0,
    preset: int = 2,
) -> str:
    """
    Adjust darktable's temperature module using native RGB coefficients.

    Matches iop/temperature.c dt_iop_temperature_params_t v4:
    float red, green, blue, various; int preset.

    Practical guidance:
    - Warmer: increase red and/or decrease blue, e.g. red=1.08, blue=0.92.
    - Cooler: decrease red and/or increase blue, e.g. red=0.92, blue=1.08.
    - preset=2 means DT_IOP_TEMP_USER.
    """
    params = param_covert.encode_temperature_params(red, green, blue, various, preset)
    _upsert_layer({"op": "temperature", "ver": "4", "params": params, "blendop": EXPOSURE_BLENDOP})
    print(f"Executing temperature adjustment: red={red}, green={green}, blue={blue}, various={various}, preset={preset}")
    return "Temperature adjustment applied"


@tool
def adjust_rgb_levels(
    black_point: float = 0.0,
    mid_gray: float = 0.5,
    white_point: float = 1.0,
    autoscale: int = 0,
    preserve_colors: int = 1,
) -> str:
    """
    Adjust darktable's RGB levels module.

    Matches iop/rgblevels.c dt_iop_rgblevels_params_t v1:
    autoscale, preserve_colors, levels[3][3].

    black_point and white_point set the input range. mid_gray changes the
    gamma-like midtone response:
    - mid_gray < 0.5 brightens midtones.
    - mid_gray > 0.5 darkens midtones.
    """
    params = param_covert.encode_rgblevels_params(
        black_point,
        mid_gray,
        white_point,
        autoscale,
        preserve_colors,
    )
    _upsert_layer({"op": "rgblevels", "ver": "1", "params": params, "blendop": DEFAULT_BLENDOP})
    print(
        "Executing RGB levels adjustment: "
        f"black_point={black_point}, mid_gray={mid_gray}, white_point={white_point}, "
        f"autoscale={autoscale}, preserve_colors={preserve_colors}"
    )
    return "RGB levels adjustment applied"


@tool
def adjust_color_zones(
    color_center: float = 0.62,
    hue_shift: float = 0.0,
    saturation: float = 0.0,
    luminance: float = 0.0,
    width: float = 0.16,
    strength: float = 0.0,
) -> str:
    """
    Adjust darktable's color zones module using hue-selected HSL controls.

    Matches iop/colorzones.c dt_iop_colorzones_params_t v5:
    channel, curve[3][20], curve_num_nodes[3], curve_type[3],
    strength, mode, splines_version.

    color_center is normalized hue in [0, 1]. The generated curves only move
    the selected hue band and keep the rest of the image on the neutral 0.5 line.
    """
    params = param_covert.encode_colorzones_params(
        color_center=color_center,
        hue_shift=hue_shift,
        saturation=saturation,
        luminance=luminance,
        width=width,
        strength=strength,
    )
    _upsert_layer({"op": "colorzones", "ver": "5", "params": params, "blendop": DEFAULT_BLENDOP})
    print(
        "Executing color zones adjustment: "
        f"color_center={color_center}, hue_shift={hue_shift}, saturation={saturation}, luminance={luminance}"
    )
    return "Color zones adjustment applied"


@tool
def adjust_vignette(
    scale: float = 80.0,
    falloff_scale: float = 50.0,
    brightness: float = -0.5,
    saturation: float = -0.5,
    center_x: float = 0.0,
    center_y: float = 0.0,
    autoratio: int = 0,
    whratio: float = 1.0,
    shape: float = 1.0,
    dithering: int = 0,
    unbound: int = 1,
) -> str:
    """
    Adjust darktable's vignette module.

    Matches iop/vignette.c dt_iop_vignette_params_t v4:
    scale, falloff_scale, brightness, saturation, center x/y, autoratio,
    whratio, shape, dithering, unbound.

    brightness < 0 darkens edges; brightness > 0 brightens edges.
    saturation < 0 desaturates edges; saturation > 0 increases edge saturation.
    """
    params = param_covert.encode_vignette_params(
        scale,
        falloff_scale,
        brightness,
        saturation,
        center_x,
        center_y,
        autoratio,
        whratio,
        shape,
        dithering,
        unbound,
    )
    _upsert_layer({"op": "vignette", "ver": "4", "params": params, "blendop": DEFAULT_BLENDOP})
    print(
        "Executing vignette adjustment: "
        f"scale={scale}, falloff_scale={falloff_scale}, brightness={brightness}, saturation={saturation}"
    )
    return "Vignette adjustment applied"


@tool
def apply_xmp_and_export(image_path, output_path) -> str:
    """
    Export the current darktable XMP history to a real image with darktable-cli.

    This writes the current adjustment list to XMP, calls darktable-cli with
    --icc-type SRGB, an in-memory library, and forward-slash paths for Windows
    compatibility, then moves the exported file to output_path.
    """
    global current_adjustments

    image = Path(image_path)
    output = Path(output_path)
    if not output.suffix:
        output = output.with_suffix(".jpg")
    output.parent.mkdir(parents=True, exist_ok=True)

    xmp_path = output.with_suffix(output.suffix + ".xmp")
    param_covert.write_xmp(current_adjustments, xmp_path, image)
    export_dir = output.parent / ".darktable-tool-exports" / f"{output.stem}-{int(time.time() * 1000)}"
    export_dir.mkdir(parents=True, exist_ok=True)
    config_dir = output.parent / ".darktable-tool"
    config_dir.mkdir(parents=True, exist_ok=True)

    command = [
        _darktable_cli_path(),
        _darktable_path(image),
        _darktable_path(xmp_path),
        _darktable_path(export_dir),
        "--out-ext",
        output.suffix.lstrip("."),
        "--icc-type",
        "SRGB",
        "--apply-custom-presets",
        "false",
        "--core",
        "--configdir",
        _darktable_path(config_dir),
        "--library",
        ":memory:",
    ]

    try:
        print(f"Outputting: {output}...")
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            return f"Export failed, darktable-cli exited with {completed.returncode}: {message}"

        exported_file = _newest_file(export_dir)
        if exported_file is None:
            message = (completed.stderr or completed.stdout or "").strip()
            return f"Export failed: darktable-cli reported success but no file was created. {message}"

        exported_file.replace(output)
        try:
            export_dir.rmdir()
        except OSError:
            pass

        print("Export successful!")
        return f"Applied XMP and exported to {output}"
    except subprocess.TimeoutExpired:
        return "Export failed: darktable-cli timed out after 180 seconds."
    except FileNotFoundError:
        return "Export failed: darktable-cli not found. Please ensure it is installed or added to PATH."
    finally:
        current_adjustments = base_layers.copy()


AVAILABLE_TOOLS = [
    adjust_exposure,
    adjust_local_contrast,
    adjust_shadows_highlights,
    adjust_color_balance_rgb,
    adjust_temperature,
    adjust_rgb_levels,
    adjust_color_zones,
    adjust_vignette,
    apply_xmp_and_export,
]
