from langchain_core.tools import tool
import param_covert
import subprocess
import os

base_layers = [
    {"op": "colorin", "ver": "7", "params": "gz48eJzjZBgFowABWAbaAaNgwAEAMNgADg=="},
    {"op": "colorout", "ver": "5", "params": "gz35eJxjZBgFo4CBAQAEEAAC"},
    {"op": "gamma", "ver": "1", "params": "0000000000000000"},
    {"op": "flip", "ver": "2", "params": "ffffffff", "multi_name": "_builtin_auto"}
]

current_adjustments = base_layers.copy()

@tool
def adjust_exposure(exposure: float=0.0, black_level: float=0.0) -> str:
    """
    Adjust the basic lighting of the photo.
    Call this tool when the user complains that the photo is too dark or too bright.

    Parameter Descriptions and Usage Scenarios:
    exposure (float, range -18.0 to 18.0): Exposure. 
        - Default is 0.0 (no change). 
        - Provide a POSITIVE value (e.g. 1.50): When the user says "the overall image is too dark, please brighten it". 
        - Provide a NEGATIVE value (e.g. -1.50): when they say "too bright" or "overexposed."
        
    black_level (float, range -1.0 to 1.0): Black level. Adjusts the black point of the image. 
        - Default is 0.0 (no change).
        - Provide a POSITIVE value (e.g. 0.02): When the user wants to "deepen the shadows" or "increase contrast" by crushing the dark areas (makes blacks purer and heavier). 
        - Provide a NEGATIVE value (e.g. -0.02): When the user wants a "faded film", "vintage matte", or "milky shadows" look (lifts the blacks into grays).
    """
    exposure_params = param_covert.encode_exposure_params(exposure, black_level)
    new_layer = {"op": "exposure", "ver": "7", "params": exposure_params, "blendop": "gz08eJxjYGBgYAFiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+AwGIBgCbGCeh"}
    
    found = False
    for i, layer in enumerate(current_adjustments):
        if layer['op'] == 'exposure':
            current_adjustments[i] = new_layer
            found = True
            break
    if not found:
        current_adjustments.append(new_layer)
    
    print(f"Executing lighting adjustment: Exposure={exposure}, Black level={black_level}")
    return "Basic lighting adjustment applied"

@tool 
def adjust_local_contrast(highlights: float=0.50, shadows: float=0.50, detail: float=0.25, midtone_range: float=0.5) -> str:
    """
    Enhance the structural detail, clarity, and three-dimensional feel of the photo without changing overall exposure.
    Call this tool when the user feels the photo looks "flat", "hazy", "soft", or when they want to "enhance textures" and "pop the details".

    Parameter Descriptions and Usage Scenarios:
    highlights (float, range 0.0 to 100.0): Adjusts how much local contrast is applied to bright areas (sigma_r). 
        - Default is 0.5. 
        - Increase this if the user wants more detail in the clouds or bright surfaces.
        - Decrease this if they say the "bright areas look too harsh" or want a "softer highlight" effect.
    
    shadows (float, range 0.0 to 100.0): Adjusts how much local contrast is applied to dark areas (sigma_s). 
        - Default is 0.5. 
        - Increase this to bring out textures in dark rocks or shadows.
        - Decrease this if the user wants a "milder shadow" effect or if they say "the dark areas look too noisy after increasing detail."
    
    detail (float, range -1.0 to 4.0): Local detail intensity. 
        - 0.25 is the default value.
        - Provide values > 0.25 (up to 4.0): When the user wants to "enhance clarity", "emphasize textures", or make the image look "sharper and more punchy".
        - Provide values < 0.0 (down to -1.0): When the user wants a "soft dream" or "skin smoothing" effect by reducing local contrast.
    
    midtone_range (float, range 0.001 to 1.0): Defines the width of the midtones affected by the filter.
        - Default is 0.5. 
        - Usually, keep this at 0.5 unless a specific tonal range needs isolation.
    """
    
    contrast_params = param_covert.encode_contrast_params(highlights, shadows, detail, midtone_range)
    new_layer = {"op": "bilat", "ver": "3", "params": contrast_params, "blendop": "gz10eJxjYGBgYAJiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAG2yHQc="}
    
    found = False
    for i, layer in enumerate(current_adjustments):
        if layer['op'] == 'bilat':
            current_adjustments[i] = new_layer
            found = True
            break
    if not found:
        current_adjustments.append(new_layer)
    
    print(f"Executing local contrast adjustment: Highlights={highlights}, Shadows={shadows}, Detail={detail}, Midtone range={midtone_range}")
    return "Local contrast adjustment applied"
    
@tool
def adjust_color_balance_rgb(shadow_hue: float=0.0, highlight_hue: float=0.0, saturation: float=0.0, hue_shift: float=0.0, brilliance: float=0.0, vibrance: float=0.0, contrast: float=0.0) -> str:
    """
    Adjust the global color grading, vividness, and artistic atmosphere of the photo. 
    Call this tool when the photo looks "dull", "pale", or "washed out", or when the user wants to apply a specific "cinematic look", "warm/cool vibe", or "color correction".

    Parameter Descriptions and Usage Scenarios:
    shadow_hue (float, range 0.0 to 360.0): [Index 2] The color angle for tinting the darkest areas (shadows).
        - Default is 0.0.
        - Set to ~210 for "cool blue shadows" (cinematic teal look).
        - Set to ~30 for "warm brown/red shadows" (retro look).

    highlight_hue (float, range 0.0 to 360.0): [Index 8] The color angle for tinting the brightest areas (highlights).
        - Default is 0.0.
        - Set to ~40 to create a "warm sunset glow" in the highlights.
        - Set to ~200 to add a "cold, tech-vibe" to the bright areas.

    saturation (float, range -1.0 to 1.0): [Index 19] Overall color intensity (global saturation).
        - Default is 0.0.
        - Increase this for "highly intense colors" across the whole image.
        - Set exactly to -1.0 when the user explicitly asks to "make it a black and white photo".

    hue_shift (float, range -180.0 to 180.0): [Index 23] Shifts the overall hue of the entire image.
        - Default is 0.0.
        - Provide positive values to shift towards magenta/red
        - Provide negative values to shift towards green/cyan.
        - Great for correcting global color casts (e.g., "the photo looks too green").

    brilliance (float, range -1.0 to 1.0): [Index 24] Adjusts the brightness of colors (v2) without affecting the white point.
        - Default is 0.0.
        - Increase this to make the colors feel "cleaner" and "brighter".
        - Decrease this to make the colors feel "deeper" and "moodier".

    vibrance (float, range -1.0 to 1.0): [Index 29] Perceptual vividness (v4) that intelligently boosts colors while protecting skin tones.
        - Default is 0.0.
        - Increase this (e.g., 0.2 to 0.5) when the user wants "richer colors" naturally.
        - Decrease this if the colors look "too neon" or "unnatural".

    contrast (float, range -1.0 to 1.0): [Index 31] Perceptual contrast (v4) based on the modern UCS 2022 color model.
        - Default is 0.0.
        - Increase this (e.g., 0.1 to 0.3) if the image looks "flat" or needs more "drama" and "punch".
        - Decrease this for a "faded", "softer", or "vintage" look.
    """
    
    contrast_params = param_covert.encode_colorbalancergb_params(shadow_hue, highlight_hue, saturation, hue_shift, brilliance, vibrance, contrast)
    new_layer = {"op": "colorbalancergb", "ver": "5", "params": contrast_params, "blendop": "gz08eJxjYGBgYAFiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+AwGIBgCbGCeh"}
    
    found = False
    for i, layer in enumerate(current_adjustments):
        if layer['op'] == 'colorbalancergb':
            current_adjustments[i] = new_layer
            found = True
            break
    if not found:
        current_adjustments.append(new_layer)
    
    print(f"Executing color balance rgb adjustment: Shadow hue={shadow_hue}, Hight hue={highlight_hue}, Saturation={saturation}, Hue shift={hue_shift}, Brilliance={brilliance}, Vibrance={vibrance}, Contrast={contrast}")
    return "Color balance rgb adjustment applied"
       
# @tool
# def apply_LUTs_preset(preset_name: str, intensity: float = 1.0) -> str:
#     """
#     Applies a color grading Look-Up Table (LUT) or predefined style preset to the image.
#     Call this tool when the user wants to change the overall "vibe", "mood", "film look", or "color palette" of the photo.
    
#     Parameter Descriptions and Usage Scenarios:
#         preset_name: (string) The specific style to apply. 
#                     CRITICAL: You MUST choose EXACTLY ONE from the following list:
#                     "cinematic" (teal and orange, moody, high contrast)
#                     "vintage_film" (warm, faded, nostalgic, retro film look)
#                     "japanese_airy" (bright, low contrast, cyan shadows, clean and fresh)
#                     "black_and_white" (monochrome, classic)
#                     "cyberpunk" (neon pink, purple, and blue, dark nights)
                     
#         intensity: (float, range: 0.0 to 1.0) The opacity or strength of the LUT effect. 
#                     Use 0.1 to 0.3 if the user asks for "subtle", "a little bit", or "light".
#                     Use 0.4 to 0.7 for "normal" or "medium" requests.
#                     Use 0.8 to 1.0 for "strong", "heavy", or explicit style requests. Default is 1.0.
#     """
#     print(f"Applying LUT preset: Preset={preset_name}, Intensity={intensity}")
#     return f"LUT preset '{preset_name}' applied with intensity {intensity}"

@tool
def apply_xmp_and_export(image_path, output_path)-> str:
    """
    Use darktable-cli to apply XMP to photo and export the result.
    image_path: The path to the original photo (e.g., "sample1.jpg") Use sample1.jpg for testing
    xmp_path: The path to the XMP file containing the editing instructions (e.g., "sample1.jpg.xmp")
    output_path: The desired path for the exported photo (e.g., "result_brightened.jpg")
    """
    
    # Windows 通常在 C:/Program Files/darktable/bin/darktable-cli.exe
    # Linux/Mac 通常直接輸入 darktable-cli
    global current_adjustments
    xmp_path = param_covert.generate_minimal_xmp(current_adjustments, base_filename=image_path)
    
    cmd = [
        "C:/Program Files/darktable/bin/darktable-cli.exe",
        image_path,
        xmp_path,
        output_path
    ]
    
    try:
        print(f"Outputting: {output_path}...")
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Export successful!")
    except subprocess.CalledProcessError as e:
        print(f"Export failed, error code: {e.stderr.decode('utf-8')}")
    except FileNotFoundError:
        print("Error: darktable-cli not found. Please ensure it is installed and added to the system PATH.")
    
    current_adjustments = base_layers.copy()
    return f"Applied XMP and exported to {output_path}"

AVAILABLE_TOOLS = [adjust_exposure, adjust_local_contrast, adjust_color_balance_rgb, apply_xmp_and_export]
    