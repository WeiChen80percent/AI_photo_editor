import struct
import base64
import zlib
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET


DEFAULT_BLENDOP = "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="


def build_xmp_text(params_list, derived_from="sample1.jpg"):
    """Build a darktable XMP sidecar from already-encoded history layers."""
    history_entries = []
    for i, item in enumerate(params_list):
        multi_name = escape(item.get("multi_name", ""))
        blendop_ver = escape(item.get("blendop_ver", "14"))
        blendop_params = escape(item.get("blendop", DEFAULT_BLENDOP))

        entry = f"""      <rdf:li
       darktable:num="{i}"
       darktable:operation="{escape(item['op'])}"
       darktable:enabled="1"
       darktable:modversion="{escape(item['ver'])}"
       darktable:params="{escape(item['params'])}"
       darktable:multi_name="{multi_name}"
       darktable:multi_name_hand_edited="0"
       darktable:multi_priority="0"
       darktable:blendop_version="{blendop_ver}"
       darktable:blendop_params="{blendop_params}"/>"""
        history_entries.append(entry)

    history_items_str = "\n".join(history_entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:darktable="http://darktable.sf.net/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmpMM:DerivedFrom="{escape(str(derived_from))}"
    darktable:xmp_version="5"
    darktable:raw_params="0"
    darktable:auto_presets_applied="1"
    darktable:history_end="{len(params_list)}"
    darktable:iop_order_version="5">
   <darktable:masks_history>
    <rdf:Seq/>
   </darktable:masks_history>
   <darktable:history>
    <rdf:Seq>
{history_items_str}
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""


def write_xmp(params_list, xmp_path, derived_from="sample1.jpg"):
    """Write a darktable XMP sidecar and return its path."""
    xmp_path = Path(xmp_path)
    xmp_path.parent.mkdir(parents=True, exist_ok=True)
    xmp_path.write_text(build_xmp_text(params_list, derived_from), encoding="utf-8")
    return xmp_path


def read_xmp_history_layers(xmp_path):
    """Read the darktable operation history from a generated XMP sidecar."""
    xmp_path = Path(xmp_path)
    root = ET.parse(xmp_path).getroot()
    rdf_ns = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    dt_ns = "{http://darktable.sf.net/}"
    layers = []
    for elem in root.findall(f".//{rdf_ns}li"):
        if f"{dt_ns}operation" not in elem.attrib:
            continue
        layers.append(
            {
                "op": elem.attrib[f"{dt_ns}operation"],
                "ver": elem.attrib[f"{dt_ns}modversion"],
                "params": elem.attrib[f"{dt_ns}params"],
                "blendop": elem.attrib.get(f"{dt_ns}blendop_params", DEFAULT_BLENDOP),
                "blendop_ver": elem.attrib.get(f"{dt_ns}blendop_version", "14"),
            }
        )
    return layers


def verify_xmp_matches_layers(params_list, xmp_path):
    """Verify that the XMP history exactly matches the encoded layers."""
    expected = [
        (
            item["op"],
            item["ver"],
            item["params"],
            item.get("blendop", DEFAULT_BLENDOP),
            item.get("blendop_ver", "14"),
        )
        for item in params_list
    ]
    actual_layers = read_xmp_history_layers(xmp_path)
    actual = [
        (
            item["op"],
            item["ver"],
            item["params"],
            item.get("blendop", DEFAULT_BLENDOP),
            item.get("blendop_ver", "14"),
        )
        for item in actual_layers
    ]
    mismatches = []
    for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
        if expected_item != actual_item:
            mismatches.append(
                {
                    "index": index,
                    "expected": expected_item,
                    "actual": actual_item,
                }
            )
    if len(expected) != len(actual):
        mismatches.append({"expected_count": len(expected), "actual_count": len(actual)})
    return {
        "matches": expected == actual,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "operations": [item["op"] for item in actual_layers],
        "mismatches": mismatches,
    }

def generate_minimal_xmp(params_list, base_filename="sample1.jpg"):
    history_end = len(params_list) # The number of history steps is determined by the length of params_list
    
    # 1.list of history entries
    history_entries = []
    for i, item in enumerate(params_list):
        multi_name = item.get('multi_name', '') # 使用 dict.get() 來設定預設值，避免 KeyError
        blendop_ver = item.get('blendop_ver', '14') # 預設使用正常的混合模式 (blendop_version 14, 以及標準無遮罩的 Base64)
        blendop_params = item.get('blendop', 'gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU=')
        
        entry = f"""      <rdf:li
       darktable:num="{i}"
       darktable:operation="{item['op']}"
       darktable:enabled="1"
       darktable:modversion="{item['ver']}"
       darktable:params="{item['params']}"
       darktable:multi_name="{multi_name}"
       darktable:multi_name_hand_edited="0"
       darktable:multi_priority="0"
       darktable:blendop_version="{blendop_ver}"
       darktable:blendop_params="{blendop_params}"/>"""
        history_entries.append(entry)

    history_items_str = "\n".join(history_entries) # All history entries are separated by newlines
    
    # 2. outward XMP string template
    full_xmp = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0-Exiv2">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:darktable="http://darktable.sf.net/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmpMM:DerivedFrom="{base_filename}"
    darktable:xmp_version="5"
    darktable:raw_params="0"
    darktable:auto_presets_applied="1"
    darktable:history_end="{history_end}"
    darktable:iop_order_version="5">
   <darktable:masks_history>
    <rdf:Seq/>
   </darktable:masks_history>
   <darktable:history>
    <rdf:Seq>
{history_items_str}
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""
    
    # 3. write to .xmp file
    file_name = f"{base_filename}.xmp"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(full_xmp)

    return file_name

def encode_exposure_params(exposure_val: float = 0.0, black_val: float = 0.0, as_hex: bool = True):
  
    # 根據官方 struct 定義生成 exposure v7 參數
    # :param exposure_val: 曝光值 (-18.0 ~ 18.0)
    # :param black_val: 黑準位修正 (-1.0 ~ 1.0)
    # :param as_hex: 是否回傳 16 進位字串 
   
    # Default values based on typical usage and the official struct definition
    mode = 0                        # EXPOSURE_MODE_MANUAL
    deflicker_percentile = 50.0      # Defult 50.0
    deflicker_target_level = -4.0    # Defult -4.0
    compensate_exposure_bias = 0     # FALSE (0)
    compensate_hilite_pres = 1       # TRUE (1)

    # Pack it into binary (Little-endian)
    # Format: i (int), f (float), f, f, f, i (gboolean), i (gboolean)
    packed_data = struct.pack('<iffffii', 
                               mode, 
                               black_val, 
                               exposure_val, 
                               deflicker_percentile, 
                               deflicker_target_level, 
                               compensate_exposure_bias, 
                               compensate_hilite_pres)

    if as_hex:
        # Return as hexadecimal string 
        return packed_data.hex()
    else:
        # Return as Base64 string
        return base64.b64encode(packed_data).decode('utf-8')

def encode_contrast_params(highlights: float=0.50, shadows: float=0.50, detail: float=0.25, midtone_range: float=0.5, as_hex: bool = True):
  
    # 根據官方 struct 定義生成 exposure v7 參數
    # :param exposure_val: 曝光值 (-18.0 ~ 18.0)
    # :param black_val: 黑準位修正 (-1.0 ~ 1.0)
    # :param as_hex: 是否回傳 16 進位字串 
   
    # Default values based on typical usage and the official struct definition
    mode = 1                         # Laplacian Filter
   
    # Pack it into binary (Little-endian)
    # Format: i (int), f (float), f, f, f
    packed_data = struct.pack('<iffff', 
                               mode,
                               highlights,
                               shadows,
                               detail,
                               midtone_range)

    if as_hex:
        # Return as hexadecimal string 
        return packed_data.hex()
    else:
        # Return as Base64 string
        return base64.b64encode(packed_data).decode('utf-8')


def encode_rgblevels_params(
    black_point: float = 0.0,
    mid_gray: float = 0.5,
    white_point: float = 1.0,
    autoscale: int = 0,
    preserve_colors: int = 1,
    as_hex: bool = True,
):
    """
    Encode darktable RGB levels v1 params.

    Matches iop/rgblevels.c dt_iop_rgblevels_params_t:
    dt_iop_rgblevels_autoscale_t autoscale;
    dt_iop_rgb_norms_t preserve_colors;
    float levels[3][3].

    autoscale: 0 = linked RGB channels, 1 = independent RGB channels.
    preserve_colors: 1 corresponds to darktable's luminance-preserving default
    used by rgbcurve/rgblevels in the bundled iop sources.
    """
    black_point = max(0.0, min(float(black_point), 0.95))
    white_point = max(black_point + 0.05, min(float(white_point), 1.0))
    mid_gray = max(black_point + 0.01, min(float(mid_gray), white_point - 0.01))
    levels = [black_point, mid_gray, white_point] * 3
    packed_data = struct.pack('<ii' + 'f' * 9, int(autoscale), int(preserve_colors), *levels)
    if as_hex:
        return packed_data.hex()
    return base64.b64encode(packed_data).decode('utf-8')


def encode_shadows_highlights_params(
    shadows: float = 0.0,
    highlights: float = 0.0,
    radius: float = 100.0,
    whitepoint: float = 0.0,
    compress: float = 50.0,
    shadows_ccorrect: float = 100.0,
    highlights_ccorrect: float = 50.0,
    order: int = 0,
    reserved2: float = 0.0,
    flags: int = 127,
    low_approximation: float = 0.000001,
    shadhi_algo: int = 1,
    as_hex: bool = True,
):
    """
    Encode darktable shadows/highlights (operation shadhi) v5 params.

    Matches iop/shadhi.c dt_iop_shadhi_params_t:
    order, radius, shadows, whitepoint, highlights, reserved2, compress,
    shadows_ccorrect, highlights_ccorrect, flags, low_approximation,
    shadhi_algo.

    shadows/highlights are darktable-native slider values in [-100, 100].
    Positive shadows lifts shadows; negative highlights recovers highlights.
    """
    shadows = max(-100.0, min(float(shadows), 100.0))
    highlights = max(-100.0, min(float(highlights), 100.0))
    radius = max(0.1, min(float(radius), 500.0))
    whitepoint = max(-10.0, min(float(whitepoint), 10.0))
    compress = max(0.0, min(float(compress), 100.0))
    shadows_ccorrect = max(0.0, min(float(shadows_ccorrect), 100.0))
    highlights_ccorrect = max(0.0, min(float(highlights_ccorrect), 100.0))
    packed_data = struct.pack(
        '<i' + 'f' * 8 + 'I' + 'f' + 'i',
        int(order),
        radius,
        shadows,
        whitepoint,
        highlights,
        reserved2,
        compress,
        shadows_ccorrect,
        highlights_ccorrect,
        int(flags),
        low_approximation,
        int(shadhi_algo),
    )
    if as_hex:
        return packed_data.hex()
    return base64.b64encode(packed_data).decode('utf-8')


def encode_colorzones_params(
    color_center: float = 0.62,
    hue_shift: float = 0.0,
    saturation: float = 0.0,
    luminance: float = 0.0,
    width: float = 0.16,
    strength: float = 0.0,
    channel: int = 2,
    mode: int = 0,
    splines_version: int = 1,
    curve_type: int = 1,
    as_hex: bool = True,
):
    """
    Encode darktable color zones v5 params for a conservative hue-selected HSL zone.

    Matches iop/colorzones.c dt_iop_colorzones_params_t:
    channel, curve[3][20], curve_num_nodes[3], curve_type[3], strength,
    mode, splines_version.

    The generated curves select pixels by hue and adjust only a local band around
    color_center. Curves stay at 0.5 outside the selected color, which means no
    adjustment in darktable's color zones module.
    """
    color_center = float(color_center) % 1.0
    hue_shift = max(-0.15, min(float(hue_shift), 0.15))
    saturation = max(-0.5, min(float(saturation), 0.5))
    luminance = max(-0.35, min(float(luminance), 0.35))
    width = max(0.04, min(float(width), 0.35))
    strength = max(-200.0, min(float(strength), 200.0))

    node_count = 8
    xs = [(index + 0.5) / node_count for index in range(node_count)]
    curves: list[list[tuple[float, float]]] = []
    deltas = [luminance * 0.18, saturation * 0.20, hue_shift]

    for delta in deltas:
        nodes: list[tuple[float, float]] = []
        for x in xs:
            distance = abs(((x - color_center + 0.5) % 1.0) - 0.5)
            weight = max(0.0, 1.0 - distance / width)
            y = max(0.0, min(1.0, 0.5 + delta * weight))
            nodes.append((x, y))
        nodes.extend([(0.0, 0.0)] * (20 - node_count))
        curves.append(nodes)

    packed_values: list[float] = []
    for channel_nodes in curves:
        for x, y in channel_nodes:
            packed_values.extend([x, y])

    packed_data = struct.pack(
        '<i' + 'f' * 120 + 'i' * 3 + 'i' * 3 + 'f' + 'i' + 'i',
        int(channel),
        *packed_values,
        node_count,
        node_count,
        node_count,
        int(curve_type),
        int(curve_type),
        int(curve_type),
        strength,
        int(mode),
        int(splines_version),
    )
    if as_hex:
        return packed_data.hex()
    return base64.b64encode(packed_data).decode('utf-8')
    
def encode_colorbalancergb_params(
    shadow_hue: float = 0.0,
    highlight_hue: float = 0.0,
    saturation: float = 0.0,
    hue_shift: float = 0.0,
    brilliance: float = 0.0,
    vibrance: float = 0.0,
    contrast: float = 0.0,
    saturation_formula: int = 1,
    as_hex: bool = False,
    *,
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
):
    
    params = [0.0] * 32 + [saturation_formula]
    # Non-zero defualt 
    params[12] = 1.0     # shadows_weight (Index 12)
    params[14] = 1.0     # highlights_weight (Index 14)
    params[28] = 0.1845  # mask_grey_fulcrum (Index 28)
    params[30] = 0.1845  # grey_fulcrum (Index 30)
    params[32] = saturation_formula  # 0 = JzAzBz (2021), 1 = darktable UCS (2022), darktable default
    
    # Handle shadow tinting: if Hue is non-zero, automatically enable Chroma (Index 1) 
    # to ensure the tinting effect is visible.
    params[0] = max(-1.0, min(float(shadow_luminance), 1.0))
    params[3] = max(-1.0, min(float(midtone_luminance), 1.0))
    params[6] = max(-1.0, min(float(highlight_luminance), 1.0))
    params[9] = max(-1.0, min(float(global_luminance), 1.0))
    params[10] = max(0.0, min(float(global_chroma), 1.0))
    params[11] = float(global_hue) % 360.0 if global_hue else 0.0

    if shadow_chroma != 0:
        params[1] = max(0.0, min(float(shadow_chroma), 1.0))
    if midtone_chroma != 0:
        params[4] = max(0.0, min(float(midtone_chroma), 1.0))
    if highlight_chroma != 0:
        params[7] = max(0.0, min(float(highlight_chroma), 1.0))

    if shadow_hue != 0:
        params[2] = shadow_hue
        params[1] = max(params[1], 0.15)  # Apply base chroma to activate the color tint.
        
    # Handle highlight tinting: if Hue is non-zero, automatically enable Chroma (Index 7).
    if highlight_hue != 0:
        params[8] = highlight_hue
        params[7] = max(params[7], 0.15)  # Apply base chroma to activate the color tint.

    # Populate remaining global parameters
    params[15] = max(-1.0, min(float(shadow_chroma), 1.0))
    params[16] = max(-1.0, min(float(highlight_chroma), 1.0))
    params[17] = max(-1.0, min(float(global_chroma), 1.0))
    params[18] = max(-1.0, min(float(midtone_chroma), 1.0))
    params[19] = saturation   # global saturation
    params[20] = max(-1.0, min(float(highlight_saturation), 1.0))
    params[21] = max(-1.0, min(float(midtone_saturation), 1.0))
    params[22] = max(-1.0, min(float(shadow_saturation), 1.0))
    params[23] = hue_shift    # hue angle shift
    params[24] = brilliance   # global brilliance
    params[25] = max(-1.0, min(float(highlight_brilliance), 1.0))
    params[26] = max(-1.0, min(float(midtone_brilliance), 1.0))
    params[27] = max(-1.0, min(float(shadow_brilliance), 1.0))
    params[29] = vibrance     # global vibrance
    params[31] = contrast     # perceptual contrast
        
    packed_data = struct.pack('<' + 'f' * 32 + 'i', *params)
    compressed = zlib.compress(packed_data, level=6)
    
    if as_hex:
        # Return as hexadecimal string 
        return packed_data.hex()
    else:
        # Return as Base64 string
        b64_data = base64.b64encode(compressed).decode('ascii')
        
        return f"gz03{b64_data}"

def encode_temperature_params(
    red: float = 1.0,
    green: float = 1.0,
    blue: float = 1.0,
    various: float = 1.0,
    preset: int = 2,
    as_hex: bool = True,
):
    """
    Encode darktable temperature v4 params.

    Matches iop/temperature.c dt_iop_temperature_params_t:
    float red, green, blue, various; int preset.
    preset: -1 unknown, 0 as-shot, 1 spot, 2 user, 3 D65, 4 D65 late.
    """
    packed_data = struct.pack('<ffffi', red, green, blue, various, int(preset))
    if as_hex:
        return packed_data.hex()
    return base64.b64encode(packed_data).decode('utf-8')

def encode_vignette_params(
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
    as_hex: bool = True,
):
    """
    Encode darktable vignette v4 params.

    Matches iop/vignette.c dt_iop_vignette_params_t:
    float scale, falloff_scale, brightness, saturation;
    center as two floats; gboolean autoratio; float whratio, shape;
    dt_iop_dither_t dithering; gboolean unbound.
    """
    packed_data = struct.pack(
        '<ffffffiffii',
        scale,
        falloff_scale,
        brightness,
        saturation,
        center_x,
        center_y,
        int(autoratio),
        whratio,
        shape,
        int(dithering),
        int(unbound),
    )
    if as_hex:
        return packed_data.hex()
    return base64.b64encode(packed_data).decode('utf-8')
