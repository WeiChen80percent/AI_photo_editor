import cv2
import numpy as np


ACTION_NAMES = [
    "exposure",
    "contrast",
    "saturation",
    "highlights",
    "shadows",
    "sharpness",
    "temperature",
    "tint",
    "vibrance",
    "orange_hue",
    "orange_saturation",
    "green_hue",
    "green_saturation",
    "blue_hue",
    "blue_saturation",
    "black_point",
    "white_point",
    "shadow_temperature",
    "highlight_temperature",
    "local_contrast",
]
ACTION_DIM = len(ACTION_NAMES)
ACTION_STEP = 0.08
MAX_EDIT_STEPS = 30
POLICY_IMAGE_MAX_SIDE = 256


def ensure_action_dim(actions):
    actions = np.asarray(actions, dtype=np.float32).flatten()
    if actions.size == ACTION_DIM:
        return actions

    padded = np.zeros(ACTION_DIM, dtype=np.float32)
    usable = min(actions.size, ACTION_DIM)
    padded[:usable] = actions[:usable]
    return padded


def _clip01(img):
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def _has_effect(*values, eps=1e-6):
    return any(abs(float(value)) > eps for value in values)


def _scaled_sigma(img, policy_sigma):
    scale = max(img.shape[:2]) / float(POLICY_IMAGE_MAX_SIDE)
    return float(np.clip(policy_sigma * scale, 0.5, 24.0))


def _lab_channels(img):
    lab = cv2.cvtColor(_clip01(img), cv2.COLOR_BGR2LAB)
    return cv2.split(lab)


def _merge_lab(l, a, b):
    lab = cv2.merge(
        (
            np.clip(l, 0.0, 100.0).astype(np.float32),
            np.asarray(a, dtype=np.float32),
            np.asarray(b, dtype=np.float32),
        )
    )
    return _clip01(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR))


def adjust_exposure(img, exposure_value):
    if not _has_effect(exposure_value):
        return img
    gamma = 2.0 ** (-float(exposure_value) * 1.5)
    return _clip01(np.power(_clip01(img), gamma))


def adjust_contrast(img, contrast_value):
    if not _has_effect(contrast_value):
        return img

    l, a, b = _lab_channels(img)
    l_norm = np.clip(l / 100.0, 0.0, 1.0)
    centered = l_norm - 0.5
    factor = np.exp(float(contrast_value))

    if contrast_value > 0:
        shaped = 0.5 + 0.5 * np.tanh(centered * factor * 2.0)
    else:
        shaped = 0.5 + centered * factor

    blend = min(abs(float(contrast_value)), 1.0)
    l_final = l_norm * (1.0 - blend) + shaped * blend
    return _merge_lab(l_final * 100.0, a, b)


def adjust_saturation(img, sat_value):
    if not _has_effect(sat_value):
        return img

    l, a, b = _lab_channels(img)
    multiplier = 1.0 + float(sat_value) * 0.5
    return _merge_lab(l, a * multiplier, b * multiplier)


def adjust_highlights_shadows(img, highlights, shadows):
    if not _has_effect(highlights, shadows):
        return img

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    high_x = np.clip((gray - 0.5) * 2.0, 0.0, 1.0)
    high_mask = high_x * high_x * (3.0 - 2.0 * high_x)
    shadow_x = np.clip((0.5 - gray) * 2.0, 0.0, 1.0)
    shadow_mask = shadow_x * shadow_x * (3.0 - 2.0 * shadow_x)

    high_mask = high_mask[..., np.newaxis]
    shadow_mask = shadow_mask[..., np.newaxis]
    out = img.copy()

    highlights = float(highlights)
    shadows = float(shadows)
    if highlights >= 0:
        out += (1.0 - out) * highlights * 0.45 * high_mask
    else:
        out += out * highlights * 0.45 * high_mask

    if shadows >= 0:
        out += (1.0 - out) * shadows * 0.45 * shadow_mask
    else:
        out += out * shadows * 0.45 * shadow_mask

    return _clip01(out)


def adjust_black_white_points(img, black_point, white_point):
    if not _has_effect(black_point, white_point):
        return img

    low = np.clip(float(black_point) * 0.08, -0.08, 0.18)
    high = np.clip(1.0 - float(white_point) * 0.08, 0.82, 1.08)
    if high <= low + 0.08:
        high = low + 0.08
    return _clip01((img - low) / (high - low))


def adjust_sharpness(img, detail):
    detail = float(detail)
    if abs(detail) < 1e-6:
        return img

    blurred = cv2.GaussianBlur(
        img,
        (0, 0),
        _scaled_sigma(img, 3.0),
    )
    if detail > 0:
        return _clip01(img + (img - blurred) * detail * 0.20)

    blend = min(abs(detail) * 0.08, 0.08)
    return _clip01(img * (1.0 - blend) + blurred * blend)


def adjust_temperature(img, temp_value):
    if not _has_effect(temp_value):
        return img

    l, a, b = _lab_channels(img)
    l_weight = np.clip(l / 100.0, 0.0, 1.0)
    b = b + float(temp_value) * 20.0 * l_weight
    return _merge_lab(l, a, b)


def adjust_tint(img, tint_value):
    if not _has_effect(tint_value):
        return img

    l, a, b = _lab_channels(img)
    l_weight = np.clip(l / 100.0, 0.0, 1.0)
    a = a + float(tint_value) * 20.0 * l_weight
    return _merge_lab(l, a, b)


def adjust_split_tone(img, shadow_temperature, highlight_temperature):
    if max(abs(float(shadow_temperature)), abs(float(highlight_temperature))) < 1e-6:
        return img

    l, a, b = _lab_channels(img)
    l_norm = np.clip(l / 100.0, 0.0, 1.0)
    shadow_x = np.clip((0.55 - l_norm) / 0.55, 0.0, 1.0)
    high_x = np.clip((l_norm - 0.45) / 0.55, 0.0, 1.0)
    shadow_mask = shadow_x * shadow_x * (3.0 - 2.0 * shadow_x)
    high_mask = high_x * high_x * (3.0 - 2.0 * high_x)

    b = b + float(shadow_temperature) * 12.0 * shadow_mask
    b = b + float(highlight_temperature) * 12.0 * high_mask
    return _merge_lab(l, a, b)


def adjust_vibrance(img, vibrance_value):
    if not _has_effect(vibrance_value):
        return img

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    boost_mask = 1.0 - s
    s = np.clip(s + float(vibrance_value) * 0.4 * s * boost_mask, 0.0, 1.0)
    return _clip01(cv2.cvtColor(cv2.merge((h, s, v)), cv2.COLOR_HSV2BGR))


def adjust_hsl_targeted(img, orange_h, orange_s, green_h, green_s, blue_h, blue_s):
    if not _has_effect(orange_h, orange_s, green_h, green_s, blue_h, blue_s):
        return img

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    dist_orange = np.minimum(np.abs(h - 25), 360 - np.abs(h - 25))
    mask_orange = np.clip(1.0 - dist_orange / 25.0, 0.0, 1.0)
    dist_green = np.minimum(np.abs(h - 105), 360 - np.abs(h - 105))
    mask_green = np.clip(1.0 - dist_green / 35.0, 0.0, 1.0)
    dist_blue = np.minimum(np.abs(h - 225), 360 - np.abs(h - 225))
    mask_blue = np.clip(1.0 - dist_blue / 45.0, 0.0, 1.0)

    h_shift = (mask_orange * orange_h + mask_green * green_h + mask_blue * blue_h) * 15.0
    h = np.mod(h + h_shift, 360.0)

    s_multiplier = 1.0 + (mask_orange * orange_s + mask_green * green_s + mask_blue * blue_s) * 0.5
    s = np.clip(s * s_multiplier, 0.0, 1.0)

    return _clip01(cv2.cvtColor(cv2.merge((h, s, v)), cv2.COLOR_HSV2BGR))


def adjust_local_contrast(img, amount):
    amount = float(amount)
    if abs(amount) < 1e-6:
        return img

    l, a, b = _lab_channels(img)
    l_norm = np.clip(l / 100.0, 0.0, 1.0)
    base = cv2.GaussianBlur(
        l_norm,
        (0, 0),
        _scaled_sigma(img, 8.0),
    )
    detail = l_norm - base
    l_out = np.clip(l_norm + detail * amount * 0.35, 0.0, 1.0)
    return _merge_lab(l_out * 100.0, a, b)


def apply_edits(image_bgr, actions):
    """
    Apply the RL slider vector to a BGR image.

    The first 15 slots keep the old layout. Slots 15-19 add black/white
    point, split-tone, and local-contrast controls.
    """
    actions = ensure_action_dim(actions)
    img = image_bgr.astype(np.float32) / 255.0

    img = adjust_temperature(img, actions[6])
    img = adjust_tint(img, actions[7])
    img = adjust_split_tone(img, actions[17], actions[18])

    img = adjust_exposure(img, actions[0])
    img = adjust_black_white_points(img, actions[15], actions[16])
    img = adjust_highlights_shadows(img, actions[3], actions[4])
    img = adjust_contrast(img, actions[1])
    img = adjust_local_contrast(img, actions[19])

    img = adjust_hsl_targeted(
        img,
        actions[9],
        actions[10],
        actions[11],
        actions[12],
        actions[13],
        actions[14],
    )
    img = adjust_saturation(img, actions[2])
    img = adjust_vibrance(img, actions[8])
    img = adjust_sharpness(img, actions[5])

    return np.clip(img * 255.0, 0, 255).astype(np.uint8)
