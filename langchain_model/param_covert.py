import struct
import base64
import zlib

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
    
def encode_colorbalancergb_params(shadow_hue: float=0.0, highlight_hue: float=0.0, saturation: float=0.0, hue_shift: float=0.0, brilliance: float=0.0, vibrance: float=0.0, contrast: float=0.0, as_hex: bool = False):
    
    params = [0.0] * 32 + [1]
    # Non-zero defualt 
    params[12] = 1.0     # shadows_weight (Index 12)
    params[14] = 1.0     # highlights_weight (Index 14)
    params[28] = 0.1845  # mask_grey_fulcrum (Index 28)
    params[30] = 0.1845  # grey_fulcrum (Index 30)
    params[32] = 1       # saturation_formula (最後一位, int: UCS 2022)
    
    # Handle shadow tinting: if Hue is non-zero, automatically enable Chroma (Index 1) 
    # to ensure the tinting effect is visible.
    if shadow_hue != 0:
        params[2] = shadow_hue
        params[1] = 0.15  # Apply base chroma to activate the color tint.
        
    # Handle highlight tinting: if Hue is non-zero, automatically enable Chroma (Index 7).
    if highlight_hue != 0:
        params[8] = highlight_hue
        params[7] = 0.15  # Apply base chroma to activate the color tint.

    # Populate remaining global parameters
    params[19] = saturation   # global saturation
    params[23] = hue_shift    # hue angle shift
    params[24] = brilliance   # global brilliance
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