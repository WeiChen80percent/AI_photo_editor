import pickle
from pathlib import Path

import cv2
import gymnasium as gym
import numpy as np
from gymnasium import spaces

from cv2_filters import (
    ACTION_DIM,
    ACTION_STEP,
    MAX_EDIT_STEPS,
    POLICY_IMAGE_MAX_SIDE,
    apply_edits,
    ensure_action_dim,
)
from paths import resolve_data_dir, resolve_rl_file
from rl_features import (
    NO_REFERENCE_OBSERVATION_MODE,
    REFERENCE_CONDITIONED_OBSERVATION_MODE,
    SPATIAL_NO_REFERENCE_OBSERVATION_MODE,
    VGG_DIM,
    build_no_reference_observation_from_features,
    build_reference_conditioned_observation_from_features,
    build_spatial_no_reference_observation_from_features,
    calculate_histogram,
    calculate_image_risk_stats,
    calculate_lab_stats,
    calculate_spatial_lab_stats,
    combine_expert_c_metrics,
    observation_dim_for_mode,
)


LAB_WEIGHTS = np.array([1.10, 1.25, 1.25], dtype=np.float32)
V3_REWARD_PROFILE = "v3"
V5_REWARD_PROFILE = "v5"
REWARD_PROFILES = {V3_REWARD_PROFILE, V5_REWARD_PROFILE}
OBSERVATION_MODES = {
    NO_REFERENCE_OBSERVATION_MODE,
    REFERENCE_CONDITIONED_OBSERVATION_MODE,
    SPATIAL_NO_REFERENCE_OBSERVATION_MODE,
}
TRAINING_RISK_ALLOWANCE = {
    "dark_fraction": 0.08,
    "bright_fraction": 0.010,
    "clipped_fraction": 0.05,
}


class Cv2Env(gym.Env):
    """Train from raw-image observations while using expert images only for reward."""

    def __init__(
        self,
        data_dir=None,
        keys_file="train_keys.txt",
        env_id=0,
        max_steps=None,
        action_step=None,
        reward_profile=V5_REWARD_PROFILE,
        observation_mode=NO_REFERENCE_OBSERVATION_MODE,
        image_keys=None,
        policy_action_dim=ACTION_DIM,
        use_edit_gate=False,
        edit_gate_stop_threshold=0.12,
    ):
        super().__init__()
        if reward_profile not in REWARD_PROFILES:
            raise ValueError(f"Unknown reward_profile '{reward_profile}'. Expected one of {sorted(REWARD_PROFILES)}.")
        if observation_mode not in OBSERVATION_MODES:
            raise ValueError(
                f"Unknown observation_mode '{observation_mode}'. "
                f"Expected one of {sorted(OBSERVATION_MODES)}."
            )

        self.data_dir = resolve_data_dir(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.env_id = env_id
        self.max_side = POLICY_IMAGE_MAX_SIDE
        self.max_steps = MAX_EDIT_STEPS if max_steps is None else int(max_steps)
        self.action_step = ACTION_STEP if action_step is None else float(action_step)
        self.reward_profile = reward_profile
        self.observation_mode = observation_mode
        self.policy_action_dim = int(policy_action_dim)
        self.use_edit_gate = bool(use_edit_gate)
        self.edit_gate_stop_threshold = float(edit_gate_stop_threshold)
        if self.policy_action_dim < ACTION_DIM:
            raise ValueError(
                f"policy_action_dim must be at least {ACTION_DIM}."
            )
        if self.use_edit_gate and self.policy_action_dim <= ACTION_DIM:
            raise ValueError(
                "use_edit_gate requires one action after the image sliders."
            )
        self.chosen_expert = "C"

        if image_keys is None:
            keys_path = Path(keys_file)
            if not keys_path.exists():
                keys_path = resolve_rl_file(keys_file)
            with open(keys_path, "r", encoding="utf-8") as f:
                self.image_keys = [line.strip() for line in f if line.strip()]
        else:
            self.image_keys = [str(key).strip() for key in image_keys if str(key).strip()]
        if not self.image_keys:
            raise ValueError("No image keys were provided to Cv2Env.")

        self.action_space = spaces.Box(
            low=-1.0,
            high=1.0,
            shape=(self.policy_action_dim,),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(observation_dim_for_mode(observation_mode),),
            dtype=np.float32,
        )

        vgg_path = Path("vgg_cache_5000.pkl")
        if not vgg_path.exists():
            vgg_path = resolve_rl_file("vgg_cache_5000.pkl")
        with open(vgg_path, "rb") as f:
            raw_vgg_cache = pickle.load(f)
        self.vgg_cache = {}
        for k, v in raw_vgg_cache.items():
            feature = np.asarray(v, dtype=np.float32).flatten()
            if feature.shape != (VGG_DIM,):
                raise ValueError(
                    f"VGG feature for {k!r} has shape {feature.shape}; expected {(VGG_DIM,)}."
                )
            self.vgg_cache[k] = feature / (np.linalg.norm(feature) + 1e-8)

        self.current_img_id = None
        self.current_style_id = None
        self.current_raw_img = None
        self.current_raw_hist = None
        self.current_raw_lab_stats = None
        self.current_raw_spatial_stats = None
        self.current_raw_risk_stats = None
        self.current_edited_img = None
        self.current_expert_img = None
        self.current_expert_lab = None
        self.current_expert_lab_blurred = None
        self.current_expert_hist = None
        self.current_expert_lab_stats = None
        self.current_vgg_feature = None
        self.current_sliders = None
        self.previous_distance = None
        self.current_gate_strength = 1.0

        print(
            f"[Env {self.env_id}] Environment initialized with {len(self.image_keys)} "
            f"image keys ({self.observation_mode})."
        )

    def _load_raw_image(self, image_id):
        img = cv2.imread(str(self.raw_dir / image_id))
        if img is None:
            raise FileNotFoundError(f"Could not read raw image: {self.raw_dir / image_id}")

        h, w = img.shape[:2]
        if max(h, w) > self.max_side:
            scale = self.max_side / max(h, w)
            size = (max(int(w * scale), 1), max(int(h * scale), 1))
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        return img

    def _load_expert_image(self, image_id, shape_hw):
        expert_path = self.data_dir / self.chosen_expert.lower() / image_id
        expert_img = cv2.imread(str(expert_path))
        if expert_img is None:
            raise FileNotFoundError(f"Could not read expert image: {expert_path}")

        h, w = shape_hw
        return cv2.resize(expert_img, (w, h), interpolation=cv2.INTER_AREA)

    def _make_state(self, current_hist, current_lab_stats):
        step_fraction = self.current_step / max(self.max_steps, 1)
        if self.observation_mode == REFERENCE_CONDITIONED_OBSERVATION_MODE:
            return build_reference_conditioned_observation_from_features(
                self.current_style_id,
                self.current_sliders,
                current_hist,
                current_lab_stats,
                self.current_expert_hist,
                self.current_expert_lab_stats,
                self.current_vgg_feature,
                step_fraction=step_fraction,
            )
        return self._make_no_reference_state(
            current_hist,
            current_lab_stats,
            step_fraction=step_fraction,
            observation_mode=self.observation_mode,
        )

    def _make_no_reference_state(
        self,
        current_hist=None,
        current_lab_stats=None,
        step_fraction=None,
        observation_mode=None,
    ):
        if observation_mode is None:
            observation_mode = self.observation_mode
        if current_hist is None:
            current_hist = calculate_histogram(self.current_edited_img)
        if current_lab_stats is None:
            current_lab_stats = calculate_lab_stats(self.current_edited_img)
        if step_fraction is None:
            step_fraction = self.current_step / max(self.max_steps, 1)
        if observation_mode == SPATIAL_NO_REFERENCE_OBSERVATION_MODE:
            if self.current_raw_spatial_stats is None:
                self.current_raw_spatial_stats = calculate_spatial_lab_stats(
                    self.current_raw_img
                )
            return build_spatial_no_reference_observation_from_features(
                self.current_style_id,
                self.current_sliders,
                current_hist,
                current_lab_stats,
                self.current_raw_hist,
                self.current_raw_lab_stats,
                calculate_spatial_lab_stats(self.current_edited_img),
                self.current_raw_spatial_stats,
                self.current_vgg_feature,
                step_fraction=step_fraction,
            )
        return build_no_reference_observation_from_features(
            self.current_style_id,
            self.current_sliders,
            current_hist,
            current_lab_stats,
            self.current_raw_hist,
            self.current_raw_lab_stats,
            self.current_vgg_feature,
            step_fraction=step_fraction,
        )

    def make_no_reference_state(
        self,
        observation_mode=NO_REFERENCE_OBSERVATION_MODE,
    ):
        """Expose the deployment state while a reference-conditioned teacher runs."""
        return self._make_no_reference_state(observation_mode=observation_mode)

    def _calculate_distance(self, img_bgr, current_hist, current_lab_stats):
        img_lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
        diff = img_lab - self.current_expert_lab
        weighted_diff = diff * LAB_WEIGHTS
        blurred_lab = cv2.GaussianBlur(img_lab, (0, 0), sigmaX=3.0, sigmaY=3.0)
        blurred_diff = (blurred_lab - self.current_expert_lab_blurred) * LAB_WEIGHTS

        lab_mse = float(np.mean(np.square(weighted_diff)))
        blurred_lab_mse = float(np.mean(np.square(blurred_diff)))
        l_mse = float(np.mean(np.square(diff[..., 0])))
        ab_mse = float(np.mean(np.square(diff[..., 1:])))
        delta_e_mean = float(np.mean(np.sqrt(np.sum(np.square(weighted_diff), axis=2))))

        neutral = 128.0 / 255.0
        current_ab = img_lab[..., 1:] - neutral
        expert_ab = self.current_expert_lab[..., 1:] - neutral
        current_chroma = np.linalg.norm(current_ab, axis=2)
        expert_chroma = np.linalg.norm(expert_ab, axis=2)
        chroma_mse = float(np.mean(np.square(current_chroma - expert_chroma)))
        current_hue = np.arctan2(current_ab[..., 1], current_ab[..., 0])
        expert_hue = np.arctan2(expert_ab[..., 1], expert_ab[..., 0])
        hue_delta = np.abs(current_hue - expert_hue)
        hue_delta = np.minimum(hue_delta, 2.0 * np.pi - hue_delta) / np.pi
        hue_weight = np.clip(np.minimum(current_chroma, expert_chroma) / 0.20, 0.0, 1.0)
        hue_mse = float(np.mean(np.square(hue_delta) * hue_weight))
        hist_distance = float(
            cv2.compareHist(
                current_hist.astype(np.float32),
                self.current_expert_hist.astype(np.float32),
                cv2.HISTCMP_BHATTACHARYYA,
            )
        )
        stats_distance = float(np.mean(np.abs(current_lab_stats - self.current_expert_lab_stats)))

        percentile_distance = float(np.mean(np.abs(current_lab_stats[-3:] - self.current_expert_lab_stats[-3:])))

        details = {
            "lab_mse": lab_mse,
            "blurred_lab_mse": blurred_lab_mse,
            "l_mse": l_mse,
            "ab_mse": ab_mse,
            "delta_e_mean": delta_e_mean,
            "chroma_mse": chroma_mse,
            "hue_mse": hue_mse,
            "hist_distance": hist_distance,
            "stats_distance": stats_distance,
            "percentile_distance": percentile_distance,
        }
        distance = combine_expert_c_metrics(details)
        return float(distance), details

    def _calculate_safety_penalty(self):
        sliders = self.current_sliders

        exposure = float(sliders[0])
        contrast = float(sliders[1])
        saturation = float(sliders[2])
        highlights = float(sliders[3])
        shadows = float(sliders[4])
        vibrance = float(sliders[8])
        black_point = float(sliders[15])
        white_point = float(sliders[16])
        white_balance = sliders[[6, 7, 17, 18]]
        hsl_controls = sliders[9:15]
        detail_controls = sliders[[5, 19]]

        edit_magnitude = float(np.mean(np.square(sliders)))
        slider_extreme = float(np.mean(np.square(np.maximum(np.abs(sliders) - 0.92, 0.0))))
        tone_extreme = float(
            np.mean(
                np.square(
                    np.maximum(
                        np.abs(
                            np.array(
                                [exposure, contrast, highlights, shadows, black_point, white_point],
                                dtype=np.float32,
                            )
                        )
                        - 0.80,
                        0.0,
                    )
                )
            )
        )
        color_extreme = float(
            np.mean(
                np.square(
                    np.maximum(
                        np.abs(np.array([saturation, vibrance], dtype=np.float32)) - 0.85,
                        0.0,
                    )
                )
            )
        )
        white_balance_extreme = float(
            np.mean(np.square(np.maximum(np.abs(white_balance) - 0.70, 0.0)))
        )
        hsl_extreme = float(
            np.mean(np.square(np.maximum(np.abs(hsl_controls) - 0.75, 0.0)))
        )
        detail_extreme = float(
            np.mean(np.square(np.maximum(np.abs(detail_controls) - 0.75, 0.0)))
        )

        overlap_pairs = (
            (2, 8),
            (1, 19),
            (6, 17),
            (6, 18),
            (0, 15),
            (0, 16),
        )
        redundant_controls = float(
            np.mean(
                [
                    min(abs(float(sliders[left])), abs(float(sliders[right]))) ** 2
                    for left, right in overlap_pairs
                ]
            )
        )

        risk_stats = calculate_image_risk_stats(self.current_edited_img)
        raw_risk = self.current_raw_risk_stats
        shadow_crush = max(
            risk_stats["dark_fraction"]
            - raw_risk["dark_fraction"]
            - TRAINING_RISK_ALLOWANCE["dark_fraction"],
            0.0,
        )
        highlight_clip = max(
            risk_stats["bright_fraction"]
            - raw_risk["bright_fraction"]
            - TRAINING_RISK_ALLOWANCE["bright_fraction"],
            0.0,
        )
        channel_clip = max(
            risk_stats["clipped_fraction"]
            - raw_risk["clipped_fraction"]
            - TRAINING_RISK_ALLOWANCE["clipped_fraction"],
            0.0,
        )
        detail_ratio = risk_stats["detail_energy"] / max(
            raw_risk["detail_energy"],
            1e-5,
        )
        over_sharpen = max(detail_ratio - 1.55, 0.0)
        detail_loss = max(0.60 - detail_ratio, 0.0)
        close_image_scale = 1.0
        if self.initial_distance < 0.004:
            close_image_scale += (0.004 - self.initial_distance) / 0.004 * 2.0

        penalty = (
            0.006 * close_image_scale * edit_magnitude
            + 0.025 * slider_extreme
            + 0.015 * tone_extreme
            + 0.012 * color_extreme
            + 0.012 * white_balance_extreme
            + 0.010 * hsl_extreme
            + 0.008 * detail_extreme
            + 0.008 * redundant_controls
            + 0.12 * shadow_crush
            + 0.20 * highlight_clip
            + 0.08 * channel_clip
            + 0.004 * over_sharpen
            + 0.006 * detail_loss
        )
        details = {
            "safety_penalty": float(penalty),
            "v5_edit_magnitude": edit_magnitude,
            "v5_slider_extreme": slider_extreme,
            "v5_tone_extreme": tone_extreme,
            "v5_color_extreme": color_extreme,
            "v5_white_balance_extreme": white_balance_extreme,
            "v5_hsl_extreme": hsl_extreme,
            "v5_detail_extreme": detail_extreme,
            "v5_redundant_controls": redundant_controls,
            "v5_shadow_crush": shadow_crush,
            "v5_highlight_clip": highlight_clip,
            "v5_channel_clip": channel_clip,
            "v5_over_sharpen": over_sharpen,
            "v5_detail_loss": detail_loss,
            "v5_detail_ratio": detail_ratio,
        }
        return float(penalty), details

    def evaluate_sliders(self, sliders):
        """Score a complete slider vector against the current training target."""
        previous_sliders = self.current_sliders
        previous_edited_img = self.current_edited_img
        try:
            self.current_sliders = np.clip(
                ensure_action_dim(sliders),
                -1.0,
                1.0,
            )
            self.current_edited_img = apply_edits(
                self.current_raw_img.copy(),
                self.current_sliders,
            )
            current_hist = calculate_histogram(self.current_edited_img)
            current_lab_stats = calculate_lab_stats(self.current_edited_img)
            distance, distance_details = self._calculate_distance(
                self.current_edited_img,
                current_hist,
                current_lab_stats,
            )
            safety_penalty, safety_details = self._calculate_safety_penalty()
            return {
                "distance": distance,
                "safety_penalty": safety_penalty,
                **distance_details,
                **safety_details,
            }
        finally:
            self.current_sliders = previous_sliders
            self.current_edited_img = previous_edited_img

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        requested_image_id = None if options is None else options.get("image_id")
        if requested_image_id is not None:
            if requested_image_id not in self.image_keys:
                raise KeyError(
                    f"Requested image {requested_image_id!r} is not part of this environment."
                )
            self.current_img_id = requested_image_id
        else:
            self.current_img_id = self.np_random.choice(self.image_keys)

        if self.chosen_expert == "C":
            self.current_style_id = np.array([1.0, 0.0], dtype=np.float32)
        else:
            self.current_style_id = np.array([0.0, 1.0], dtype=np.float32)

        self.current_raw_img = self._load_raw_image(self.current_img_id)
        self.current_edited_img = self.current_raw_img.copy()
        self.current_raw_hist = calculate_histogram(self.current_raw_img)
        self.current_raw_lab_stats = calculate_lab_stats(self.current_raw_img)
        self.current_raw_spatial_stats = (
            calculate_spatial_lab_stats(self.current_raw_img)
            if self.observation_mode == SPATIAL_NO_REFERENCE_OBSERVATION_MODE
            else None
        )
        self.current_raw_risk_stats = calculate_image_risk_stats(self.current_raw_img)

        h, w = self.current_raw_img.shape[:2]
        expert_img = self._load_expert_image(self.current_img_id, (h, w))
        self.current_expert_img = expert_img
        self.current_expert_lab = cv2.cvtColor(expert_img, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0
        self.current_expert_lab_blurred = cv2.GaussianBlur(
            self.current_expert_lab,
            (0, 0),
            sigmaX=3.0,
            sigmaY=3.0,
        )
        self.current_expert_hist = calculate_histogram(expert_img)
        self.current_expert_lab_stats = calculate_lab_stats(expert_img)

        self.current_step = 0
        self.current_sliders = np.zeros(ACTION_DIM, dtype=np.float32)
        self.current_gate_strength = 1.0

        current_hist = calculate_histogram(self.current_edited_img)
        current_lab_stats = calculate_lab_stats(self.current_edited_img)
        self.initial_distance, _ = self._calculate_distance(
            self.current_edited_img,
            current_hist,
            current_lab_stats,
        )
        self.previous_distance = self.initial_distance

        if self.current_img_id not in self.vgg_cache:
            raise KeyError(
                f"Missing VGG feature for {self.current_img_id!r}. Run precompute.py again."
            )
        self.current_vgg_feature = self.vgg_cache[self.current_img_id]

        return self._make_state(current_hist, current_lab_stats), {}

    def step(self, action):
        self.current_step += 1
        policy_action = np.clip(
            np.asarray(action, dtype=np.float32).flatten(),
            -1.0,
            1.0,
        )
        action = ensure_action_dim(policy_action[:ACTION_DIM])
        gate_terminated = False
        self.current_gate_strength = 1.0
        if self.use_edit_gate:
            if policy_action.size <= ACTION_DIM:
                raise ValueError(
                    "Edit-gated environment received no gate action."
                )
            self.current_gate_strength = float(
                np.clip(
                    (policy_action[ACTION_DIM] + 1.0) * 0.5,
                    0.0,
                    1.0,
                )
            )
            gate_terminated = (
                self.current_gate_strength < self.edit_gate_stop_threshold
            )

        applied_gate_strength = (
            0.0 if gate_terminated else self.current_gate_strength
        )
        self.current_sliders += (
            action * self.action_step * applied_gate_strength
        )
        self.current_sliders = np.clip(self.current_sliders, -1.0, 1.0)
        self.current_edited_img = apply_edits(self.current_raw_img.copy(), self.current_sliders)

        current_hist = calculate_histogram(self.current_edited_img)
        current_lab_stats = calculate_lab_stats(self.current_edited_img)
        new_distance, details = self._calculate_distance(
            self.current_edited_img,
            current_hist,
            current_lab_stats,
        )

        progress = self.previous_distance - new_distance
        distance_scale = max(self.initial_distance, 0.005)
        normalized_progress = progress / distance_scale
        action_penalty = float(
            np.mean(np.square(action * applied_gate_strength)) * 0.003
        )
        safety_penalty, safety_details = self._calculate_safety_penalty()
        regression_penalty = 0.0

        if self.reward_profile == V3_REWARD_PROFILE:
            reward = (
                normalized_progress
                - new_distance * 0.25
                - action_penalty
                - safety_penalty * 0.25
            )
        else:
            regression_penalty = float(max(-progress - 0.0003, 0.0) * 8.0)
            reward = (
                normalized_progress
                - new_distance * 2.0
                - action_penalty
                - safety_penalty
                - regression_penalty
            )
        terminated = gate_terminated or self.current_step >= self.max_steps
        if terminated:
            improvement_ratio = (self.initial_distance - new_distance) / distance_scale
            if self.reward_profile == V3_REWARD_PROFILE:
                reward += improvement_ratio * 0.25
                reward -= new_distance * 0.50
                reward -= safety_penalty * 0.25
            else:
                reward += improvement_ratio * 0.50
                reward -= new_distance * 5.0
                reward -= safety_penalty * 0.5

        self.previous_distance = new_distance
        state = self._make_state(current_hist, current_lab_stats)

        info = {
            "distance": new_distance,
            "reward": float(reward),
            "reward_profile": self.reward_profile,
            "observation_uses_expert_target": (
                self.observation_mode == REFERENCE_CONDITIONED_OBSERVATION_MODE
            ),
            "normalized_progress": float(normalized_progress),
            "action_penalty": action_penalty,
            "regression_penalty": regression_penalty,
            "edit_gate_enabled": self.use_edit_gate,
            "gate_strength": float(self.current_gate_strength),
            "gate_terminated": bool(gate_terminated),
            **details,
            **safety_details,
        }
        return state, float(reward), terminated, False, info
