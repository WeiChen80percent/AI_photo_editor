import json

import cv2
import gymnasium as gym
import numpy as np
from gymnasium import spaces

from cv2_filters import ACTION_DIM, apply_edits, ensure_action_dim


HIST_BINS = 8
HIST_DIM = HIST_BINS ** 3
LAB_STATS_DIM = 9
VGG_DIM = 4096
STYLE_ID_DIM = 2
SPATIAL_GRID_SIZE = 3
SPATIAL_LAB_DIM = SPATIAL_GRID_SIZE ** 2 * LAB_STATS_DIM

STYLE_STATS_DIM = LAB_STATS_DIM * 3
COLOR_FEATURE_DIM = HIST_DIM * 3
CONTROL_FEATURE_DIM = STYLE_ID_DIM + ACTION_DIM + STYLE_STATS_DIM
OBS_DIM = CONTROL_FEATURE_DIM + COLOR_FEATURE_DIM + VGG_DIM
SPATIAL_FEATURE_DIM = SPATIAL_LAB_DIM * 3
SPATIAL_OBS_DIM = OBS_DIM + SPATIAL_FEATURE_DIM
NO_REFERENCE_OBSERVATION_MODE = "raw_image_delta_v2"
SPATIAL_NO_REFERENCE_OBSERVATION_MODE = "raw_image_spatial_v3"
LEGACY_NO_REFERENCE_OBSERVATION_MODE = "raw_image_delta_v1"
SUPPORTED_NO_REFERENCE_OBSERVATION_MODES = {
    LEGACY_NO_REFERENCE_OBSERVATION_MODE,
    NO_REFERENCE_OBSERVATION_MODE,
    SPATIAL_NO_REFERENCE_OBSERVATION_MODE,
}
REFERENCE_CONDITIONED_OBSERVATION_MODE = "expert_target_delta_v2"
LEGACY_REFERENCE_OBSERVATION_MODE = "expert_target_delta_v1"
DEFAULT_INFERENCE_SAFETY_LIMITS = {
    "dark_fraction": 0.11,
    "bright_fraction": 0.015,
    "clipped_fraction": 0.06,
    "detail_ratio_max": 1.85,
    "detail_ratio_min": 0.45,
}
LEGACY_INFERENCE_SAFETY_LIMITS = {
    "dark_fraction": 0.025,
    "bright_fraction": 0.015,
    "clipped_fraction": 0.020,
    "detail_ratio_max": 1.85,
    "detail_ratio_min": 0.45,
}
SAFETY_BACKOFF_FACTORS = (0.5, 0.25, 0.125)
EXPERT_C_SCORE_VERSION = "expert_c_unified_v5"
EXPERT_C_SCORE_WEIGHTS = {
    "lab_mse": 0.25,
    "blurred_lab_mse": 0.35,
    "delta_e_mean": 0.020,
    "chroma_mse": 0.40,
    "hue_mse": 0.020,
    "hist_distance": 0.005,
    "stats_distance": 0.10,
}


def observation_dim_for_mode(observation_mode):
    if observation_mode == SPATIAL_NO_REFERENCE_OBSERVATION_MODE:
        return SPATIAL_OBS_DIM
    return OBS_DIM


def load_no_reference_metadata(metadata_path):
    metadata_path = metadata_path.resolve()
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"Missing model metadata: {metadata_path}. "
            "This model may predate the no-reference observation change; retrain it with train_cv2.py."
        )

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    if metadata.get("observation_mode") not in SUPPORTED_NO_REFERENCE_OBSERVATION_MODES:
        raise ValueError(
            f"Model metadata uses observation mode {metadata.get('observation_mode')!r}, "
            f"expected one of {sorted(SUPPORTED_NO_REFERENCE_OBSERVATION_MODES)!r}."
        )
    expected_dim = observation_dim_for_mode(metadata.get("observation_mode"))
    if metadata.get("observation_dim") != expected_dim:
        raise ValueError(
            f"Model metadata uses observation dim {metadata.get('observation_dim')!r}, "
            f"expected {expected_dim}."
        )
    if metadata.get("policy_uses_expert_target") is not False:
        raise ValueError("Model metadata does not confirm a no-reference policy.")
    policy_action_dim = int(metadata.get("policy_action_dim", ACTION_DIM))
    if policy_action_dim < ACTION_DIM:
        raise ValueError(
            f"Model metadata uses policy action dim {policy_action_dim}; "
            f"expected at least {ACTION_DIM}."
        )
    if metadata.get("edit_gate_enabled", False) and policy_action_dim <= ACTION_DIM:
        raise ValueError(
            "Model metadata enables the edit gate without an additional policy action."
        )
    return metadata


def calculate_histogram(img_bgr):
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    hist = cv2.calcHist(
        [img_rgb],
        [0, 1, 2],
        None,
        [HIST_BINS, HIST_BINS, HIST_BINS],
        [0, 256, 0, 256, 0, 256],
    )
    hist = hist.flatten().astype(np.float32)
    hist = hist / (np.sum(hist) + 1e-8)
    return hist.astype(np.float32)


def calculate_lab_stats(img_bgr):
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    mean = lab.reshape(-1, 3).mean(axis=0)
    std = lab.reshape(-1, 3).std(axis=0)
    luma_percentiles = np.percentile(lab[..., 0], [5, 50, 95]).astype(np.float32)
    return np.concatenate([mean, std, luma_percentiles]).astype(np.float32)


def calculate_spatial_lab_stats(img_bgr):
    """Summarize a 3x3 image grid so the policy can distinguish scene regions."""
    height, width = img_bgr.shape[:2]
    y_edges = np.linspace(0, height, SPATIAL_GRID_SIZE + 1, dtype=np.int32)
    x_edges = np.linspace(0, width, SPATIAL_GRID_SIZE + 1, dtype=np.int32)
    cells = []
    for row in range(SPATIAL_GRID_SIZE):
        for column in range(SPATIAL_GRID_SIZE):
            cell = img_bgr[
                y_edges[row] : y_edges[row + 1],
                x_edges[column] : x_edges[column + 1],
            ]
            cells.append(calculate_lab_stats(cell))
    result = np.concatenate(cells).astype(np.float32)
    if result.shape != (SPATIAL_LAB_DIM,):
        raise ValueError(
            f"Expected spatial LAB shape {(SPATIAL_LAB_DIM,)}, got {result.shape}."
        )
    return result


def compare_histograms(hist_a, hist_b):
    return float(
        cv2.compareHist(
            np.asarray(hist_a, dtype=np.float32).flatten(),
            np.asarray(hist_b, dtype=np.float32).flatten(),
            cv2.HISTCMP_BHATTACHARYYA,
        )
    )


def calculate_weighted_lab_mse(img_bgr, expert_bgr):
    img_lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    expert_lab = cv2.cvtColor(expert_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    weights = np.array([1.10, 1.25, 1.25], dtype=np.float32)
    return float(np.mean(np.square((img_lab - expert_lab) * weights)))


def calculate_blurred_lab_mse(img_bgr, expert_bgr, sigma=3.0):
    img_lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    expert_lab = cv2.cvtColor(expert_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    img_lab = cv2.GaussianBlur(img_lab, (0, 0), sigmaX=sigma, sigmaY=sigma)
    expert_lab = cv2.GaussianBlur(expert_lab, (0, 0), sigmaX=sigma, sigmaY=sigma)
    weights = np.array([1.10, 1.25, 1.25], dtype=np.float32)
    return float(np.mean(np.square((img_lab - expert_lab) * weights)))


def calculate_lab_color_metrics(img_bgr, expert_bgr):
    img_lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    expert_lab = (
        cv2.cvtColor(expert_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    )
    weights = np.array([1.10, 1.25, 1.25], dtype=np.float32)
    weighted_diff = (img_lab - expert_lab) * weights
    delta_e_mean = float(
        np.mean(np.sqrt(np.sum(np.square(weighted_diff), axis=2)))
    )

    neutral = 128.0 / 255.0
    img_ab = img_lab[..., 1:] - neutral
    expert_ab = expert_lab[..., 1:] - neutral
    img_chroma = np.linalg.norm(img_ab, axis=2)
    expert_chroma = np.linalg.norm(expert_ab, axis=2)
    chroma_mse = float(np.mean(np.square(img_chroma - expert_chroma)))

    img_hue = np.arctan2(img_ab[..., 1], img_ab[..., 0])
    expert_hue = np.arctan2(expert_ab[..., 1], expert_ab[..., 0])
    hue_delta = np.abs(img_hue - expert_hue)
    hue_delta = np.minimum(hue_delta, 2.0 * np.pi - hue_delta) / np.pi
    hue_weight = np.clip(
        np.minimum(img_chroma, expert_chroma) / 0.20,
        0.0,
        1.0,
    )
    hue_mse = float(np.mean(np.square(hue_delta) * hue_weight))
    return {
        "delta_e_mean": delta_e_mean,
        "chroma_mse": chroma_mse,
        "hue_mse": hue_mse,
    }


def style_feature_score(current_hist, current_lab_stats, expert_hist, expert_lab_stats):
    hist_distance = compare_histograms(current_hist, expert_hist)
    stats_distance = float(
        np.mean(
            np.abs(
                np.asarray(current_lab_stats, dtype=np.float32)
                - np.asarray(expert_lab_stats, dtype=np.float32)
            )
        )
    )
    score = 0.005 * hist_distance + 0.10 * stats_distance
    details = {
        "hist_distance": hist_distance,
        "stats_distance": stats_distance,
    }
    return float(score), details


def combine_expert_c_metrics(details):
    """Combine aligned color metrics into the single score used everywhere."""
    missing = sorted(set(EXPERT_C_SCORE_WEIGHTS) - set(details))
    if missing:
        raise KeyError(f"Missing Expert C score metrics: {missing}")
    return float(
        sum(
            EXPERT_C_SCORE_WEIGHTS[name] * float(details[name])
            for name in EXPERT_C_SCORE_WEIGHTS
        )
    )


def paired_style_score(img_bgr, expert_bgr):
    current_hist = calculate_histogram(img_bgr)
    expert_hist = calculate_histogram(expert_bgr)
    current_stats = calculate_lab_stats(img_bgr)
    expert_stats = calculate_lab_stats(expert_bgr)
    _, details = style_feature_score(
        current_hist,
        current_stats,
        expert_hist,
        expert_stats,
    )

    lab_mse = calculate_weighted_lab_mse(img_bgr, expert_bgr)
    blurred_lab_mse = calculate_blurred_lab_mse(img_bgr, expert_bgr)
    color_metrics = calculate_lab_color_metrics(img_bgr, expert_bgr)
    details = {
        **details,
        **color_metrics,
        "lab_mse": lab_mse,
        "blurred_lab_mse": blurred_lab_mse,
    }
    score = combine_expert_c_metrics(details)
    return float(score), details


def _assemble_observation(
    style_id,
    sliders,
    current_hist,
    current_lab_stats,
    reference_hist,
    reference_lab_stats,
    vgg_feature,
    current_spatial_stats=None,
    reference_spatial_stats=None,
):
    sliders = ensure_action_dim(sliders)
    style_id = np.asarray(style_id, dtype=np.float32)
    current_hist = np.asarray(current_hist, dtype=np.float32)
    current_lab_stats = np.asarray(current_lab_stats, dtype=np.float32)
    reference_hist = np.asarray(reference_hist, dtype=np.float32)
    reference_lab_stats = np.asarray(reference_lab_stats, dtype=np.float32)
    vgg_feature = np.asarray(vgg_feature, dtype=np.float32)

    hist_delta = current_hist - reference_hist
    stats_delta = current_lab_stats - reference_lab_stats

    observation_parts = [
        style_id,
        sliders,
        current_lab_stats,
        reference_lab_stats,
        stats_delta,
        current_hist,
        reference_hist,
        hist_delta,
    ]
    expected_dim = OBS_DIM
    if current_spatial_stats is not None or reference_spatial_stats is not None:
        current_spatial_stats = np.asarray(
            current_spatial_stats,
            dtype=np.float32,
        )
        reference_spatial_stats = np.asarray(
            reference_spatial_stats,
            dtype=np.float32,
        )
        if current_spatial_stats.shape != (SPATIAL_LAB_DIM,):
            raise ValueError(
                "Expected current spatial LAB shape "
                f"{(SPATIAL_LAB_DIM,)}, got {current_spatial_stats.shape}."
            )
        if reference_spatial_stats.shape != (SPATIAL_LAB_DIM,):
            raise ValueError(
                "Expected reference spatial LAB shape "
                f"{(SPATIAL_LAB_DIM,)}, got {reference_spatial_stats.shape}."
            )
        observation_parts.extend(
            [
                current_spatial_stats,
                reference_spatial_stats,
                current_spatial_stats - reference_spatial_stats,
            ]
        )
        expected_dim = SPATIAL_OBS_DIM
    observation_parts.append(vgg_feature)
    observation = np.concatenate(observation_parts).astype(np.float32)
    if observation.shape != (expected_dim,):
        raise ValueError(
            f"Expected observation shape {(expected_dim,)}, got {observation.shape}."
        )
    return observation


def build_no_reference_observation_from_features(
    style_id,
    sliders,
    current_hist,
    current_lab_stats,
    raw_hist,
    raw_lab_stats,
    vgg_feature,
    step_fraction=0.0,
):
    """Build a policy state using only information available at deployment."""
    policy_context = np.asarray(style_id, dtype=np.float32).copy()
    if policy_context.shape != (STYLE_ID_DIM,):
        raise ValueError(
            f"Expected style/context shape {(STYLE_ID_DIM,)}, got {policy_context.shape}."
        )
    policy_context[1] = float(np.clip(step_fraction, 0.0, 1.0))
    return _assemble_observation(
        policy_context,
        sliders,
        current_hist,
        current_lab_stats,
        raw_hist,
        raw_lab_stats,
        vgg_feature,
    )


def build_spatial_no_reference_observation_from_features(
    style_id,
    sliders,
    current_hist,
    current_lab_stats,
    raw_hist,
    raw_lab_stats,
    current_spatial_stats,
    raw_spatial_stats,
    vgg_feature,
    step_fraction=0.0,
):
    """Build a no-reference state with deployment-available 3x3 LAB features."""
    policy_context = np.asarray(style_id, dtype=np.float32).copy()
    if policy_context.shape != (STYLE_ID_DIM,):
        raise ValueError(
            f"Expected style/context shape {(STYLE_ID_DIM,)}, got {policy_context.shape}."
        )
    policy_context[1] = float(np.clip(step_fraction, 0.0, 1.0))
    return _assemble_observation(
        policy_context,
        sliders,
        current_hist,
        current_lab_stats,
        raw_hist,
        raw_lab_stats,
        vgg_feature,
        current_spatial_stats=current_spatial_stats,
        reference_spatial_stats=raw_spatial_stats,
    )


def build_reference_conditioned_observation_from_features(
    expert_id,
    sliders,
    current_hist,
    current_lab_stats,
    expert_hist,
    expert_lab_stats,
    vgg_feature,
    step_fraction=None,
):
    """Build the legacy v3/v4/v5 state that includes the paired expert target."""
    policy_context = np.asarray(expert_id, dtype=np.float32).copy()
    if policy_context.shape != (STYLE_ID_DIM,):
        raise ValueError(
            f"Expected expert/context shape {(STYLE_ID_DIM,)}, got {policy_context.shape}."
        )
    if step_fraction is not None:
        policy_context[1] = float(np.clip(step_fraction, 0.0, 1.0))
    return _assemble_observation(
        policy_context,
        sliders,
        current_hist,
        current_lab_stats,
        expert_hist,
        expert_lab_stats,
        vgg_feature,
    )


def calculate_image_risk_stats(img_bgr):
    """Return deployment-available indicators for clipping and detail damage."""
    image_float = img_bgr.astype(np.float32) / 255.0
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
    luma = lab[..., 0]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    detail_energy = float(np.mean(np.abs(cv2.Laplacian(gray, cv2.CV_32F))))
    return {
        "dark_fraction": float(np.mean(luma < 0.06)),
        "bright_fraction": float(np.mean(luma > 0.96)),
        "clipped_fraction": float(
            np.mean((image_float <= 0.01) | (image_float >= 0.99))
        ),
        "detail_energy": detail_energy,
    }


def _unsafe_relative_change(raw_stats, edited_stats, safety_limits=None):
    limits = {
        **LEGACY_INFERENCE_SAFETY_LIMITS,
        **(safety_limits or {}),
    }
    detail_ratio = edited_stats["detail_energy"] / max(raw_stats["detail_energy"], 1e-5)
    reasons = []
    if (
        edited_stats["dark_fraction"]
        > raw_stats["dark_fraction"] + limits["dark_fraction"]
    ):
        reasons.append("shadow_crush")
    if (
        edited_stats["bright_fraction"]
        > raw_stats["bright_fraction"] + limits["bright_fraction"]
    ):
        reasons.append("highlight_clip")
    if (
        edited_stats["clipped_fraction"]
        > raw_stats["clipped_fraction"] + limits["clipped_fraction"]
    ):
        reasons.append("channel_clip")
    if detail_ratio > limits["detail_ratio_max"]:
        reasons.append("over_sharpen")
    if detail_ratio < limits["detail_ratio_min"]:
        reasons.append("detail_loss")
    return reasons


def run_no_reference_policy(
    model,
    vec_env,
    raw_img,
    vgg_feature,
    action_step,
    max_steps,
    observation_mode=None,
    safety_stop=True,
    min_steps=3,
    safety_limits=None,
    safety_backoff=False,
    edit_gate_enabled=False,
    edit_gate_stop_threshold=0.12,
    slider_limits=None,
    return_info=False,
):
    """Run a no-reference edit with optional deployment-only safety stopping."""
    style_id = np.array([1.0, 0.0], dtype=np.float32)
    raw_hist = calculate_histogram(raw_img)
    raw_stats = calculate_lab_stats(raw_img)
    uses_spatial = observation_mode == SPATIAL_NO_REFERENCE_OBSERVATION_MODE
    raw_spatial_stats = (
        calculate_spatial_lab_stats(raw_img) if uses_spatial else None
    )
    raw_risk_stats = calculate_image_risk_stats(raw_img)
    current_img = raw_img.copy()
    current_sliders = np.zeros(ACTION_DIM, dtype=np.float32)
    stop_reason = "max_steps"
    steps_used = 0
    attempted_steps = 0
    small_action_count = 0
    safety_backoffs = 0
    gate_strengths = []
    uses_progress = observation_mode == NO_REFERENCE_OBSERVATION_MODE
    uses_progress = uses_progress or uses_spatial
    if slider_limits is None:
        slider_limits = np.ones(ACTION_DIM, dtype=np.float32)
    else:
        slider_limits = np.asarray(slider_limits, dtype=np.float32).flatten()
        if slider_limits.shape != (ACTION_DIM,):
            raise ValueError(
                f"Expected slider limits shape {(ACTION_DIM,)}, "
                f"got {slider_limits.shape}."
            )
        slider_limits = np.clip(slider_limits, 0.05, 1.0)

    for step_index in range(max_steps):
        current_hist = calculate_histogram(current_img)
        current_stats = calculate_lab_stats(current_img)
        step_fraction = (
            step_index / max(max_steps, 1)
            if uses_progress
            else 0.0
        )
        if uses_spatial:
            observation = build_spatial_no_reference_observation_from_features(
                style_id,
                current_sliders,
                current_hist,
                current_stats,
                raw_hist,
                raw_stats,
                calculate_spatial_lab_stats(current_img),
                raw_spatial_stats,
                vgg_feature,
                step_fraction=step_fraction,
            )
        else:
            observation = build_no_reference_observation_from_features(
                style_id,
                current_sliders,
                current_hist,
                current_stats,
                raw_hist,
                raw_stats,
                vgg_feature,
                step_fraction=step_fraction,
            )
        normalized_observation = vec_env.normalize_obs(observation.reshape(1, -1))[0]
        action, _ = model.predict(normalized_observation, deterministic=True)
        policy_action = np.clip(
            np.asarray(action, dtype=np.float32).flatten(),
            -1.0,
            1.0,
        )
        action = ensure_action_dim(policy_action[:ACTION_DIM])
        gate_strength = 1.0
        if edit_gate_enabled:
            if policy_action.size <= ACTION_DIM:
                raise ValueError(
                    "Edit-gated inference requires one policy action after "
                    "the image sliders."
                )
            gate_strength = float(
                np.clip((policy_action[ACTION_DIM] + 1.0) * 0.5, 0.0, 1.0)
            )
            gate_strengths.append(gate_strength)
            attempted_steps = step_index + 1
            if gate_strength < edit_gate_stop_threshold:
                stop_reason = "low_edit_confidence"
                break

        candidate_sliders = np.clip(
            current_sliders + action * action_step * gate_strength,
            -slider_limits,
            slider_limits,
        )
        candidate_img = apply_edits(raw_img.copy(), candidate_sliders)
        attempted_steps = step_index + 1

        if safety_stop and attempted_steps >= min_steps:
            unsafe_reasons = _unsafe_relative_change(
                raw_risk_stats,
                calculate_image_risk_stats(candidate_img),
                safety_limits=safety_limits,
            )
            if np.any(np.abs(candidate_sliders) >= 0.985):
                unsafe_reasons.append("slider_bound")
            if unsafe_reasons:
                if not safety_backoff:
                    stop_reason = "+".join(unsafe_reasons)
                    break
                accepted_backoff = False
                slider_delta = candidate_sliders - current_sliders
                for backoff_factor in SAFETY_BACKOFF_FACTORS:
                    backoff_sliders = np.clip(
                        current_sliders + slider_delta * backoff_factor,
                        -1.0,
                        1.0,
                    )
                    backoff_img = apply_edits(raw_img.copy(), backoff_sliders)
                    backoff_reasons = _unsafe_relative_change(
                        raw_risk_stats,
                        calculate_image_risk_stats(backoff_img),
                        safety_limits=safety_limits,
                    )
                    if (
                        not backoff_reasons
                        and not np.any(np.abs(backoff_sliders) >= 0.985)
                    ):
                        candidate_sliders = backoff_sliders
                        candidate_img = backoff_img
                        safety_backoffs += 1
                        accepted_backoff = True
                        break
                if not accepted_backoff:
                    stop_reason = "+".join(unsafe_reasons)
                    break

        effective_action = (
            candidate_sliders - current_sliders
        ) / max(float(action_step), 1e-8)
        if np.allclose(candidate_sliders, current_sliders, atol=1e-6):
            stop_reason = "no_safe_progress"
            if safety_stop and attempted_steps >= min_steps:
                break

        current_sliders = candidate_sliders
        current_img = candidate_img
        steps_used = attempted_steps

        if float(np.mean(np.abs(effective_action))) < 0.025:
            small_action_count += 1
        else:
            small_action_count = 0
        if safety_stop and steps_used >= min_steps and small_action_count >= 2:
            stop_reason = "converged"
            break

    result_info = {
        "steps_used": int(steps_used),
        "attempted_steps": int(attempted_steps),
        "stop_reason": stop_reason,
        "safety_stop": bool(safety_stop),
        "safety_backoffs": int(safety_backoffs),
        "safety_backoff_enabled": bool(safety_backoff),
        "edit_gate_enabled": bool(edit_gate_enabled),
        "mean_gate_strength": float(np.mean(gate_strengths))
        if gate_strengths
        else 1.0,
        "last_gate_strength": float(gate_strengths[-1])
        if gate_strengths
        else 1.0,
    }
    if return_info:
        return current_img, current_sliders, result_info
    return current_img, current_sliders


class ObservationOnlyEnv(gym.Env):
    """Minimal environment used only to load VecNormalize during inference."""

    metadata = {}

    def __init__(self, observation_dim=OBS_DIM, action_dim=ACTION_DIM):
        super().__init__()
        self.observation_dim = int(observation_dim)
        self.action_dim = int(action_dim)
        self.action_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(self.action_dim,),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(self.observation_dim,),
            dtype=np.float32,
        )

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        return np.zeros(self.observation_dim, dtype=np.float32), {}

    def step(self, action):
        return (
            np.zeros(self.observation_dim, dtype=np.float32),
            0.0,
            False,
            False,
            {},
        )
