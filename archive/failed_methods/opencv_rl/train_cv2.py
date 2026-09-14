import argparse
import copy
import json
import multiprocessing
import os
import pickle
import random
import shutil
import time
from pathlib import Path

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import cv2
import gymnasium as gym
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecNormalize

from cv2_env import Cv2Env, V3_REWARD_PROFILE, V5_REWARD_PROFILE
from cv2_filters import ACTION_DIM, POLICY_IMAGE_MAX_SIDE, apply_edits
from model_paths import (
    CURRENT_ACTION_STEP,
    CURRENT_BC_MODEL_PATH,
    CURRENT_CHECKPOINT_DIR,
    CURRENT_DISTILL_ACTIONS_PATH,
    CURRENT_DISTILL_OBSERVATIONS_PATH,
    CURRENT_DISTILL_WEIGHTS_PATH,
    CURRENT_HISTORY_DIR,
    CURRENT_MAX_EDIT_STEPS,
    CURRENT_METADATA_PATH,
    CURRENT_MODEL_NAME,
    CURRENT_MODEL_PATH,
    CURRENT_TEACHER_MODEL_PATH,
    CURRENT_TEACHER_TARGETS_PATH,
    CURRENT_TEACHER_VEC_NORMALIZE_PATH,
    CURRENT_TRAINING_ARTIFACT_DIR,
    CURRENT_VALIDATION_DISTILL_ACTIONS_PATH,
    CURRENT_VALIDATION_DISTILL_OBSERVATIONS_PATH,
    CURRENT_VALIDATION_DISTILL_WEIGHTS_PATH,
    CURRENT_VALIDATION_TEACHER_TARGETS_PATH,
    CURRENT_VEC_NORMALIZE_PATH,
)
from paths import DEFAULT_DATA_DIR, resolve_rl_file
from rl_features import (
    COLOR_FEATURE_DIM,
    CONTROL_FEATURE_DIM,
    DEFAULT_INFERENCE_SAFETY_LIMITS,
    EXPERT_C_SCORE_VERSION,
    OBS_DIM,
    REFERENCE_CONDITIONED_OBSERVATION_MODE,
    SPATIAL_FEATURE_DIM,
    SPATIAL_NO_REFERENCE_OBSERVATION_MODE,
    SPATIAL_OBS_DIM,
    VGG_DIM,
    ObservationOnlyEnv,
    build_spatial_no_reference_observation_from_features,
    calculate_histogram,
    calculate_lab_stats,
    calculate_spatial_lab_stats,
    observation_dim_for_mode,
    paired_style_score,
    run_no_reference_policy,
)


DEFAULT_TEACHER_TIMESTEPS = 750_000
DEFAULT_STUDENT_TIMESTEPS = 750_000
DEFAULT_BC_EPOCHS = 16
DEFAULT_NUM_ENVS = 8
DEFAULT_DEVICE = "cpu"
DEFAULT_BC_DEVICE = "cpu"
DEFAULT_VALIDATION_COUNT = 250
VALIDATION_SAMPLE_COUNT = 250
TEACHER_VALIDATION_INTERVAL_TIMESTEPS = 125_000
STUDENT_VALIDATION_INTERVAL_TIMESTEPS = 50_000
TEACHER_MAX_EDIT_STEPS = 20
TEACHER_ACTION_STEP = 0.08
PPO_GAMMA = 0.96
TRAIN_SEED = 42
TEACHER_LEARNING_RATE = 8e-5
STUDENT_LEARNING_RATE = 5e-6
TEACHER_ENT_COEF = 0.003
STUDENT_ENT_COEF = 0.0
BC_LEARNING_RATE = 2e-4
BC_BATCH_SIZE = 256
BC_WEIGHT_DECAY = 2e-4
BC_EARLY_STOP_PATIENCE = 3
BC_MIN_VALIDATION_IMPROVEMENT = 5e-4
DISTILL_STEP_INDICES = tuple(range(CURRENT_MAX_EDIT_STEPS))
DISTILL_MIN_TARGET_STEPS = 5
DISTILL_STOP_STEPS = 2
DISTILL_DATASET_VERSION = 3
TEACHER_TARGET_VERSION = 3
STUDENT_OBSERVATION_MODE = SPATIAL_NO_REFERENCE_OBSERVATION_MODE
STUDENT_EARLY_STOP_PATIENCE = 2
MIN_VALIDATION_IMPROVEMENT = 1e-5
STUDENT_POLICY_ACTION_DIM = ACTION_DIM + 1
STUDENT_EDIT_GATE_ENABLED = True
STUDENT_EDIT_GATE_STOP_THRESHOLD = 0.12
STUDENT_NO_EDIT_QUANTILE = 0.20
STUDENT_FULL_EDIT_QUANTILE = 0.55
MAX_CANONICAL_ACTIVE_CONTROLS = 12
REDUNDANT_ACTION_PAIRS = (
    (2, 8),
    (1, 19),
    (6, 17),
    (6, 18),
    (0, 15),
    (0, 16),
)

# The 10-step student can move each slider by at most 0.8. Lower limits on
# detail and overlapping controls make the distilled targets less destructive.
STUDENT_SLIDER_LIMITS = np.array(
    [
        0.78,
        0.72,
        0.72,
        0.72,
        0.72,
        0.58,
        0.68,
        0.62,
        0.68,
        0.62,
        0.68,
        0.62,
        0.68,
        0.62,
        0.68,
        0.68,
        0.68,
        0.62,
        0.62,
        0.58,
    ],
    dtype=np.float32,
)


TRAIN_CONFIG = {
    "label": "teacher-distilled no-reference model",
    "model_name": CURRENT_MODEL_NAME,
    "model_path": CURRENT_MODEL_PATH,
    "vec_normalize_path": CURRENT_VEC_NORMALIZE_PATH,
    "metadata_path": CURRENT_METADATA_PATH,
    "history_dir": CURRENT_HISTORY_DIR,
    "checkpoint_dir": CURRENT_CHECKPOINT_DIR,
    "artifact_dir": CURRENT_TRAINING_ARTIFACT_DIR,
    "teacher_model_path": CURRENT_TEACHER_MODEL_PATH,
    "teacher_vec_path": CURRENT_TEACHER_VEC_NORMALIZE_PATH,
    "teacher_targets_path": CURRENT_TEACHER_TARGETS_PATH,
    "validation_teacher_targets_path": CURRENT_VALIDATION_TEACHER_TARGETS_PATH,
    "distill_observations_path": CURRENT_DISTILL_OBSERVATIONS_PATH,
    "distill_actions_path": CURRENT_DISTILL_ACTIONS_PATH,
    "distill_weights_path": CURRENT_DISTILL_WEIGHTS_PATH,
    "validation_distill_observations_path": (
        CURRENT_VALIDATION_DISTILL_OBSERVATIONS_PATH
    ),
    "validation_distill_actions_path": CURRENT_VALIDATION_DISTILL_ACTIONS_PATH,
    "validation_distill_weights_path": CURRENT_VALIDATION_DISTILL_WEIGHTS_PATH,
    "bc_model_path": CURRENT_BC_MODEL_PATH,
}


def _read_keys(filename):
    path = resolve_rl_file(filename)
    if not path.exists():
        raise FileNotFoundError(f"Missing split file: {path}")
    with open(path, "r", encoding="utf-8") as file:
        keys = [line.strip() for line in file if line.strip()]
    if not keys:
        raise ValueError(f"No image keys found in {path}.")
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate image keys found in {path}.")
    return keys


def _load_vgg_cache():
    cache_path = resolve_rl_file("vgg_cache_5000.pkl")
    with open(cache_path, "rb") as file:
        raw_cache = pickle.load(file)

    cache = {}
    for image_id, value in raw_cache.items():
        feature = np.asarray(value, dtype=np.float32).flatten()
        if feature.shape != (VGG_DIM,):
            raise ValueError(
                f"VGG feature for {image_id!r} has shape {feature.shape}; "
                f"expected {(VGG_DIM,)}."
            )
        cache[image_id] = feature / (np.linalg.norm(feature) + 1e-8)
    return cache


def _resize_policy_image(image):
    height, width = image.shape[:2]
    if max(height, width) <= POLICY_IMAGE_MAX_SIDE:
        return image
    scale = POLICY_IMAGE_MAX_SIDE / max(height, width)
    size = (max(int(width * scale), 1), max(int(height * scale), 1))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _load_policy_image(image_id, subdirectory="raw"):
    path = DEFAULT_DATA_DIR / subdirectory / image_id
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return _resize_policy_image(image)


def split_fit_validation(train_keys, validation_count, seed):
    if validation_count < 1:
        raise ValueError("validation_count must be at least 1.")
    if validation_count >= len(train_keys):
        raise ValueError("validation_count must be smaller than the training split.")
    shuffled = list(train_keys)
    random.Random(seed).shuffle(shuffled)
    validation_keys = sorted(shuffled[:validation_count])
    fit_keys = sorted(shuffled[validation_count:])
    return fit_keys, validation_keys


def validate_training_inputs(deep_check=False):
    train_keys = _read_keys("train_keys.txt")
    test_keys = _read_keys("test_keys.txt")
    overlap = sorted(set(train_keys) & set(test_keys))
    if overlap:
        raise ValueError(
            f"Train/test split overlap contains {len(overlap)} images, including {overlap[:5]}."
        )

    all_keys = train_keys + test_keys
    for label, directory in (
        ("raw", DEFAULT_DATA_DIR / "raw"),
        ("Expert C", DEFAULT_DATA_DIR / "c"),
    ):
        missing = [image_id for image_id in all_keys if not (directory / image_id).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Missing {len(missing)} {label} images under {directory}, "
                f"including {missing[:5]}."
            )

    cache_path = resolve_rl_file("vgg_cache_5000.pkl")
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Missing VGG cache: {cache_path}. Run precompute.py before training."
        )
    with open(cache_path, "rb") as file:
        vgg_cache = pickle.load(file)

    missing_vgg = [image_id for image_id in all_keys if image_id not in vgg_cache]
    if missing_vgg:
        raise KeyError(
            f"Missing {len(missing_vgg)} VGG entries, including {missing_vgg[:5]}."
        )
    invalid_vgg = [
        image_id
        for image_id in all_keys
        if np.asarray(vgg_cache[image_id]).size != VGG_DIM
    ]
    if invalid_vgg:
        raise ValueError(
            f"Invalid VGG feature size for {len(invalid_vgg)} images, "
            f"including {invalid_vgg[:5]}."
        )

    zero_test = np.zeros((32, 48, 3), dtype=np.uint8)
    if not np.array_equal(apply_edits(zero_test, np.zeros(ACTION_DIM)), zero_test):
        raise RuntimeError("The zero slider vector does not preserve the input image.")

    if deep_check:
        sample_key = train_keys[0]
        boundary_env = Cv2Env(
            image_keys=[sample_key],
            max_steps=TEACHER_MAX_EDIT_STEPS,
            action_step=TEACHER_ACTION_STEP,
            reward_profile=V3_REWARD_PROFILE,
            observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
            env_id=-1,
        )
        try:
            teacher_state, _ = boundary_env.reset(
                seed=TRAIN_SEED,
                options={"image_id": sample_key},
            )
            shared_score, _ = paired_style_score(
                boundary_env.current_raw_img,
                boundary_env.current_expert_img,
            )
            if not np.isclose(
                shared_score,
                boundary_env.initial_distance,
                atol=1e-7,
            ):
                raise RuntimeError(
                    "Environment reward distance and evaluation score diverged."
                )
            student_state = boundary_env.make_no_reference_state(
                observation_mode=STUDENT_OBSERVATION_MODE
            )
            if (
                teacher_state.shape != (OBS_DIM,)
                or student_state.shape != (SPATIAL_OBS_DIM,)
            ):
                raise RuntimeError("Teacher/student observation shape check failed.")
            if np.array_equal(teacher_state, student_state):
                raise RuntimeError(
                    "Teacher and student states are unexpectedly identical."
                )
            original_expert_hist = boundary_env.current_expert_hist.copy()
            original_expert_stats = boundary_env.current_expert_lab_stats.copy()
            boundary_env.current_expert_hist = np.roll(original_expert_hist, 1)
            boundary_env.current_expert_lab_stats = original_expert_stats + 0.1
            student_after_expert_change = boundary_env.make_no_reference_state(
                observation_mode=STUDENT_OBSERVATION_MODE
            )
            teacher_after_expert_change = boundary_env._make_state(
                calculate_histogram(boundary_env.current_edited_img),
                calculate_lab_stats(boundary_env.current_edited_img),
            )
            if not np.array_equal(student_state, student_after_expert_change):
                raise RuntimeError(
                    "Student observation changed when only Expert C features changed."
                )
            if np.array_equal(teacher_state, teacher_after_expert_change):
                raise RuntimeError(
                    "Teacher observation did not react to changed Expert C features."
                )
        finally:
            boundary_env.close()

    print(
        f"Preflight passed: {len(train_keys)} train / {len(test_keys)} test images, "
        "disjoint splits, complete Expert C pairs, and valid raw-image VGG features."
    )
    if deep_check:
        print(
            "Information-boundary check passed: teacher sees Expert C features; "
            "student observation contains raw/current-image information only."
        )
    return train_keys, test_keys


class MultiModalFeatureExtractor(BaseFeaturesExtractor):
    """Feature extractor for controls, color state, and raw-image VGG context."""

    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 256):
        super().__init__(observation_space, features_dim)
        self.control_dim = CONTROL_FEATURE_DIM
        self.color_dim = COLOR_FEATURE_DIM
        self.vgg_dim = VGG_DIM

        self.control_net = nn.Sequential(
            nn.Linear(self.control_dim, 96),
            nn.LayerNorm(96),
            nn.ReLU(),
            nn.Linear(96, 64),
            nn.ReLU(),
        )
        self.color_net = nn.Sequential(
            nn.Linear(self.color_dim, 512),
            nn.LayerNorm(512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
        )
        self.vgg_net = nn.Sequential(
            nn.Linear(self.vgg_dim, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
        )
        self.fusion_net = nn.Sequential(
            nn.Linear(64 + 256 + 256, 384),
            nn.ReLU(),
            nn.Linear(384, features_dim),
            nn.ReLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        control_features = self.control_net(observations[:, : self.control_dim])
        color_start = self.control_dim
        color_end = color_start + self.color_dim
        color_features = self.color_net(observations[:, color_start:color_end])
        vgg_features = self.vgg_net(observations[:, color_end:])
        return self.fusion_net(
            torch.cat([control_features, color_features, vgg_features], dim=1)
        )


class SpatialMultiModalFeatureExtractor(BaseFeaturesExtractor):
    """Feature extractor with a separate branch for 3x3 regional LAB context."""

    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 320):
        super().__init__(observation_space, features_dim)
        self.control_dim = CONTROL_FEATURE_DIM
        self.color_dim = COLOR_FEATURE_DIM
        self.spatial_dim = SPATIAL_FEATURE_DIM
        self.vgg_dim = VGG_DIM

        self.control_net = nn.Sequential(
            nn.Linear(self.control_dim, 96),
            nn.LayerNorm(96),
            nn.ReLU(),
            nn.Linear(96, 64),
            nn.ReLU(),
        )
        self.color_net = nn.Sequential(
            nn.Linear(self.color_dim, 512),
            nn.LayerNorm(512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
        )
        self.spatial_net = nn.Sequential(
            nn.Linear(self.spatial_dim, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
        )
        self.vgg_net = nn.Sequential(
            nn.Linear(self.vgg_dim, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
        )
        self.fusion_net = nn.Sequential(
            nn.Linear(64 + 256 + 128 + 256, 448),
            nn.ReLU(),
            nn.Linear(448, features_dim),
            nn.ReLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        control_end = self.control_dim
        color_end = control_end + self.color_dim
        spatial_end = color_end + self.spatial_dim
        control_features = self.control_net(observations[:, :control_end])
        color_features = self.color_net(
            observations[:, control_end:color_end]
        )
        spatial_features = self.spatial_net(
            observations[:, color_end:spatial_end]
        )
        vgg_features = self.vgg_net(observations[:, spatial_end:])
        return self.fusion_net(
            torch.cat(
                [
                    control_features,
                    color_features,
                    spatial_features,
                    vgg_features,
                ],
                dim=1,
            )
        )


class GeneralizedSpatialFeatureExtractor(BaseFeaturesExtractor):
    """Student extractor with a fixed VGG bottleneck to reduce image memorization."""

    def __init__(self, observation_space: gym.spaces.Box, features_dim: int = 256):
        super().__init__(observation_space, features_dim)
        self.control_dim = CONTROL_FEATURE_DIM
        self.color_dim = COLOR_FEATURE_DIM
        self.spatial_dim = SPATIAL_FEATURE_DIM
        generator = torch.Generator()
        generator.manual_seed(20_260_731)
        projection = torch.randn(
            VGG_DIM,
            256,
            generator=generator,
            dtype=torch.float32,
        ) / np.sqrt(float(VGG_DIM))
        self.register_buffer("vgg_projection", projection)

        self.control_net = nn.Sequential(
            nn.Linear(self.control_dim, 96),
            nn.LayerNorm(96),
            nn.SiLU(),
            nn.Linear(96, 64),
            nn.SiLU(),
        )
        self.color_net = nn.Sequential(
            nn.Linear(self.color_dim, 384),
            nn.LayerNorm(384),
            nn.SiLU(),
            nn.Linear(384, 224),
            nn.SiLU(),
        )
        self.spatial_net = nn.Sequential(
            nn.Linear(self.spatial_dim, 256),
            nn.LayerNorm(256),
            nn.SiLU(),
            nn.Linear(256, 160),
            nn.SiLU(),
        )
        self.vgg_net = nn.Sequential(
            nn.Linear(256, 96),
            nn.LayerNorm(96),
            nn.SiLU(),
        )
        self.fusion_net = nn.Sequential(
            nn.Linear(64 + 224 + 160 + 96, 320),
            nn.LayerNorm(320),
            nn.SiLU(),
            nn.Linear(320, features_dim),
            nn.SiLU(),
        )

    def forward(self, observations: torch.Tensor) -> torch.Tensor:
        control_end = self.control_dim
        color_end = control_end + self.color_dim
        spatial_end = color_end + self.spatial_dim
        control_features = self.control_net(observations[:, :control_end])
        color_features = self.color_net(
            observations[:, control_end:color_end]
        )
        spatial_features = self.spatial_net(
            observations[:, color_end:spatial_end]
        )
        projected_vgg = observations[:, spatial_end:] @ self.vgg_projection
        vgg_features = self.vgg_net(projected_vgg)
        return self.fusion_net(
            torch.cat(
                [
                    control_features,
                    color_features,
                    spatial_features,
                    vgg_features,
                ],
                dim=1,
            )
        )


def set_global_seed(seed):
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    try:
        torch.use_deterministic_algorithms(True, warn_only=True)
    except Exception:
        pass
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def build_policy_kwargs(observation_mode):
    if observation_mode == STUDENT_OBSERVATION_MODE:
        return {
            "features_extractor_class": GeneralizedSpatialFeatureExtractor,
            "features_extractor_kwargs": {"features_dim": 256},
            "net_arch": {"pi": [256, 192], "vf": [256, 192]},
            "log_std_init": -3.0,
        }
    return {
        "features_extractor_class": MultiModalFeatureExtractor,
        "features_extractor_kwargs": {"features_dim": 256},
        "net_arch": {"pi": [256, 256], "vf": [256, 256]},
        "log_std_init": -2.2,
    }


def make_env(
    env_id,
    history_dir,
    image_keys,
    reward_profile,
    observation_mode,
    action_step,
    max_steps,
    policy_action_dim=ACTION_DIM,
    use_edit_gate=False,
):
    def _init():
        env = Cv2Env(
            image_keys=image_keys,
            env_id=env_id,
            reward_profile=reward_profile,
            observation_mode=observation_mode,
            action_step=action_step,
            max_steps=max_steps,
            policy_action_dim=policy_action_dim,
            use_edit_gate=use_edit_gate,
            edit_gate_stop_threshold=STUDENT_EDIT_GATE_STOP_THRESHOLD,
        )
        history_dir.mkdir(parents=True, exist_ok=True)
        return Monitor(env, str(history_dir / f"env_{env_id}"))

    return _init


def build_training_env(
    history_dir,
    image_keys,
    seed,
    reward_profile,
    observation_mode,
    action_step,
    max_steps,
    num_envs,
    policy_action_dim=ACTION_DIM,
    use_edit_gate=False,
):
    raw_env = SubprocVecEnv(
        [
            make_env(
                env_id,
                history_dir,
                image_keys,
                reward_profile,
                observation_mode,
                action_step,
                max_steps,
                policy_action_dim,
                use_edit_gate,
            )
            for env_id in range(num_envs)
        ]
    )
    raw_env.seed(seed)
    return VecNormalize(
        raw_env,
        norm_obs=True,
        norm_reward=True,
        clip_obs=10.0,
        gamma=PPO_GAMMA,
    )


def resolve_device(device):
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is False.")
    return device


def create_ppo_model(
    env,
    seed,
    device,
    learning_rate,
    ent_coef,
    observation_mode,
    clip_range=0.2,
    n_epochs=10,
    target_kl=None,
):
    rollout_size = 512 * env.num_envs
    batch_size = min(1024, rollout_size)
    while rollout_size % batch_size != 0:
        batch_size //= 2
    return PPO(
        "MlpPolicy",
        env,
        policy_kwargs=build_policy_kwargs(observation_mode),
        verbose=1,
        learning_rate=learning_rate,
        ent_coef=ent_coef,
        n_steps=512,
        batch_size=batch_size,
        gamma=PPO_GAMMA,
        gae_lambda=0.92,
        clip_range=clip_range,
        n_epochs=n_epochs,
        target_kl=target_kl,
        device=resolve_device(device),
        seed=seed,
    )


def _save_model_and_vec(model, vec_env, model_path, vec_path):
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    Path(vec_path).parent.mkdir(parents=True, exist_ok=True)
    model.save(str(model_path))
    vec_env.save(str(vec_path))


def _write_validation_record(history_path, record):
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, sort_keys=True) + "\n")


def _teacher_selection_score(distance, safety_penalty, sliders):
    sliders = np.asarray(sliders, dtype=np.float32)
    complexity = float(np.mean(np.abs(sliders)))
    active_fraction = float(np.mean(np.abs(sliders) >= 0.03))
    redundant_controls = float(
        np.mean(
            [
                min(
                    abs(float(sliders[left])),
                    abs(float(sliders[right])),
                )
                ** 2
                for left, right in REDUNDANT_ACTION_PAIRS
            ]
        )
    )
    return float(
        distance
        + 0.75 * safety_penalty
        + 0.0008 * complexity
        + 0.0004 * active_fraction
        + 0.0020 * redundant_controls
    )


def evaluate_reference_teacher(model, vec_env, image_keys):
    eval_env = Cv2Env(
        image_keys=image_keys,
        env_id=-2,
        max_steps=TEACHER_MAX_EDIT_STEPS,
        action_step=TEACHER_ACTION_STEP,
        reward_profile=V3_REWARD_PROFILE,
        observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
    )
    best_scores = []
    improved = 0
    try:
        for image_id in image_keys:
            observation, _ = eval_env.reset(options={"image_id": image_id})
            raw_score = float(eval_env.initial_distance)
            best_score = raw_score
            for _ in range(TEACHER_MAX_EDIT_STEPS):
                normalized = vec_env.normalize_obs(observation.reshape(1, -1))[0]
                action, _ = model.predict(normalized, deterministic=True)
                observation, _, terminated, _, info = eval_env.step(action)
                score = _teacher_selection_score(
                    info["distance"],
                    info["safety_penalty"],
                    eval_env.current_sliders,
                )
                best_score = min(best_score, score)
                if terminated:
                    break
            best_scores.append(best_score)
            improved += int(best_score < raw_score)
    finally:
        eval_env.close()

    objective = float(np.mean(best_scores))
    return objective, {
        "mean_teacher_selection_score": objective,
        "improved_count": int(improved),
        "image_count": len(image_keys),
    }


def evaluate_no_reference_student(model, vec_env, image_keys, vgg_cache):
    raw_scores = []
    final_scores = []
    mse_improved = 0
    unsafe_stops = 0

    for image_id in image_keys:
        raw_img = _load_policy_image(image_id, "raw")
        edited_img, _, inference_info = run_no_reference_policy(
            model,
            vec_env,
            raw_img,
            vgg_cache[image_id],
            action_step=CURRENT_ACTION_STEP,
            max_steps=CURRENT_MAX_EDIT_STEPS,
            observation_mode=STUDENT_OBSERVATION_MODE,
            safety_stop=True,
            safety_limits=DEFAULT_INFERENCE_SAFETY_LIMITS,
            safety_backoff=True,
            edit_gate_enabled=STUDENT_EDIT_GATE_ENABLED,
            edit_gate_stop_threshold=STUDENT_EDIT_GATE_STOP_THRESHOLD,
            slider_limits=STUDENT_SLIDER_LIMITS,
            return_info=True,
        )

        # Expert C is loaded only after the no-reference policy has finished.
        expert_img = _load_policy_image(image_id, "c")
        if expert_img.shape[:2] != raw_img.shape[:2]:
            expert_img = cv2.resize(
                expert_img,
                (raw_img.shape[1], raw_img.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
        raw_score, raw_details = paired_style_score(raw_img, expert_img)
        final_score, final_details = paired_style_score(edited_img, expert_img)
        raw_scores.append(raw_score)
        final_scores.append(final_score)
        mse_improved += int(final_details["lab_mse"] < raw_details["lab_mse"])
        unsafe_stops += int(
            inference_info["stop_reason"]
            not in {"max_steps", "converged", "low_edit_confidence"}
        )

    raw_values = np.asarray(raw_scores, dtype=np.float32)
    final_values = np.asarray(final_scores, dtype=np.float32)
    regression_rate = float(np.mean(final_values >= raw_values))
    unsafe_rate = unsafe_stops / max(len(image_keys), 1)
    close_mask = raw_values <= np.quantile(raw_values, 0.25)
    far_mask = raw_values >= np.quantile(raw_values, 0.75)
    close_regression_rate = float(
        np.mean(final_values[close_mask] >= raw_values[close_mask])
    )
    far_improvement_rate = float(
        np.mean(final_values[far_mask] < raw_values[far_mask])
    )
    severe_regression_rate = float(
        np.mean((final_values - raw_values) > 0.003)
    )
    relative_gain = (raw_values - final_values) / np.maximum(raw_values, 0.005)
    mean_final_score = float(np.mean(final_values))
    objective = (
        mean_final_score
        + 0.0040 * regression_rate
        + 0.0040 * close_regression_rate
        + 0.0030 * (1.0 - far_improvement_rate)
        + 0.0040 * severe_regression_rate
        + 0.0010 * unsafe_rate
    )
    return float(objective), {
        "mean_raw_style_score": float(np.mean(raw_values)),
        "mean_final_style_score": mean_final_score,
        "regression_rate": regression_rate,
        "close_quartile_regression_rate": close_regression_rate,
        "far_quartile_improvement_rate": far_improvement_rate,
        "severe_regression_rate": severe_regression_rate,
        "mean_relative_style_gain": float(np.mean(relative_gain)),
        "unsafe_stop_rate": unsafe_rate,
        "mse_improved_count": int(mse_improved),
        "image_count": len(image_keys),
    }


def learn_with_validation(
    model,
    vec_env,
    total_timesteps,
    stage_name,
    evaluator,
    checkpoint_dir,
    validation_history_path,
    evaluate_initial=False,
    validation_interval_timesteps=TEACHER_VALIDATION_INTERVAL_TIMESTEPS,
    early_stop_patience=None,
    post_chunk_hook=None,
):
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    latest_model_path = checkpoint_dir / f"{stage_name}_latest"
    latest_vec_path = checkpoint_dir / f"{stage_name}_latest_vec.pkl"
    best_model_path = checkpoint_dir / f"{stage_name}_best"
    best_vec_path = checkpoint_dir / f"{stage_name}_best_vec.pkl"

    completed = 0
    best_objective = float("inf")
    best_metrics = None
    first_learn_call = True
    no_improvement_rounds = 0
    if evaluate_initial:
        initial_objective, initial_metrics = evaluator(model, vec_env)
        best_objective = float(initial_objective)
        best_metrics = {
            "stage": stage_name,
            "requested_total_timesteps": int(total_timesteps),
            "completed_timesteps": 0,
            "model_timesteps": int(model.num_timesteps),
            "objective": best_objective,
            "checkpoint_source": "behavior_cloning_initialization",
            **initial_metrics,
        }
        _write_validation_record(validation_history_path, best_metrics)
        _save_model_and_vec(
            model,
            vec_env,
            best_model_path,
            best_vec_path,
        )
        print(
            f"[{stage_name}] Behavior-cloning validation objective: "
            f"{best_objective:.6f}"
        )

    while completed < total_timesteps:
        requested = min(
            validation_interval_timesteps,
            total_timesteps - completed,
        )
        before = model.num_timesteps
        model.learn(
            total_timesteps=requested,
            reset_num_timesteps=first_learn_call,
            progress_bar=False,
        )
        first_learn_call = False
        trained = max(model.num_timesteps - before, requested)
        completed += trained
        if post_chunk_hook is not None:
            post_chunk_hook(model, vec_env, completed)

        objective, metrics = evaluator(model, vec_env)
        record = {
            "stage": stage_name,
            "requested_total_timesteps": int(total_timesteps),
            "completed_timesteps": int(min(completed, total_timesteps)),
            "model_timesteps": int(model.num_timesteps),
            "objective": float(objective),
            **metrics,
        }
        _write_validation_record(validation_history_path, record)
        _save_model_and_vec(
            model,
            vec_env,
            latest_model_path,
            latest_vec_path,
        )
        if objective < best_objective - MIN_VALIDATION_IMPROVEMENT:
            best_objective = objective
            best_metrics = record
            no_improvement_rounds = 0
            _save_model_and_vec(
                model,
                vec_env,
                best_model_path,
                best_vec_path,
            )
            print(
                f"[{stage_name}] New best validation objective: "
                f"{best_objective:.6f}"
            )
        else:
            no_improvement_rounds += 1
            print(
                f"[{stage_name}] Validation objective: {objective:.6f} "
                f"(best {best_objective:.6f}; "
                f"no improvement {no_improvement_rounds})"
            )
            if (
                early_stop_patience is not None
                and no_improvement_rounds >= early_stop_patience
            ):
                print(
                    f"[{stage_name}] Early stopping after "
                    f"{no_improvement_rounds} validation regressions."
                )
                break

    return best_model_path, best_vec_path, best_metrics


def _load_inference_normalizer(
    vec_path,
    observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
    action_dim=ACTION_DIM,
):
    observation_dim = observation_dim_for_mode(observation_mode)
    dummy_env = DummyVecEnv(
        [lambda: ObservationOnlyEnv(observation_dim, action_dim=action_dim)]
    )
    vec_env = VecNormalize.load(str(vec_path), dummy_env)
    vec_env.training = False
    vec_env.norm_reward = False
    return vec_env


def _canonicalize_teacher_sliders(sliders):
    sliders = np.asarray(sliders, dtype=np.float32).flatten()
    sliders = np.clip(sliders, -STUDENT_SLIDER_LIMITS, STUDENT_SLIDER_LIMITS)
    sliders[np.abs(sliders) < 0.02] = 0.0
    return sliders


def _score_teacher_sliders(env, sliders):
    metrics = env.evaluate_sliders(sliders)
    score = _teacher_selection_score(
        metrics["distance"],
        metrics["safety_penalty"],
        sliders,
    )
    return float(score), metrics


def _sparsify_teacher_sliders(env, sliders, raw_distance):
    """Remove redundant controls while preserving a useful Expert C edit."""
    current = _canonicalize_teacher_sliders(sliders)
    current_score, current_metrics = _score_teacher_sliders(env, current)
    score_limit = current_score + max(0.00008, raw_distance * 0.015)

    for left, right in REDUNDANT_ACTION_PAIRS:
        if min(abs(float(current[left])), abs(float(current[right]))) < 0.03:
            continue
        alternatives = []
        for index in (left, right):
            candidate = current.copy()
            candidate[index] = 0.0
            score, metrics = _score_teacher_sliders(env, candidate)
            alternatives.append((score, candidate, metrics))
        best_score, best_candidate, best_metrics = min(
            alternatives,
            key=lambda value: value[0],
        )
        if best_score <= score_limit:
            current = best_candidate
            current_score = best_score
            current_metrics = best_metrics

    active_indices = np.flatnonzero(np.abs(current) >= 0.03)
    if len(active_indices) > MAX_CANONICAL_ACTIVE_CONTROLS:
        removal_count = len(active_indices) - MAX_CANONICAL_ACTIVE_CONTROLS
        removal_order = sorted(
            active_indices,
            key=lambda index: abs(float(current[index])),
        )
        removed = removal_order[:removal_count]
        candidate = current.copy()
        candidate[removed] = 0.0
        candidate_score, candidate_metrics = _score_teacher_sliders(
            env,
            candidate,
        )
        minimum_gain = max(0.00008, raw_distance * 0.02)
        while (
            raw_distance - float(candidate_metrics["distance"])
            < minimum_gain
            and removed
        ):
            restore_index = removed.pop()
            candidate[restore_index] = current[restore_index]
            candidate_score, candidate_metrics = _score_teacher_sliders(
                env,
                candidate,
            )
        if (
            raw_distance - float(candidate_metrics["distance"])
        ) >= minimum_gain:
            current = candidate
            current_score = candidate_score
            current_metrics = candidate_metrics

    current[np.abs(current) < 0.03] = 0.0
    return current, current_metrics, current_score


def _calculate_student_edit_strengths(
    raw_distances,
    target_distances,
    slider_targets,
):
    raw_distances = np.asarray(raw_distances, dtype=np.float32)
    target_distances = np.asarray(target_distances, dtype=np.float32)
    slider_targets = np.asarray(slider_targets, dtype=np.float32)
    useful = np.max(np.abs(slider_targets), axis=1) >= 0.03
    strengths = np.zeros(len(slider_targets), dtype=np.float32)
    if not np.any(useful):
        return strengths

    useful_raw = raw_distances[useful]
    no_edit_threshold = float(
        np.quantile(useful_raw, STUDENT_NO_EDIT_QUANTILE)
    )
    full_edit_threshold = float(
        np.quantile(useful_raw, STUDENT_FULL_EDIT_QUANTILE)
    )
    denominator = max(full_edit_threshold - no_edit_threshold, 1e-6)
    necessity = np.clip(
        (raw_distances - no_edit_threshold) / denominator,
        0.0,
        1.0,
    )
    necessity = necessity * necessity * (3.0 - 2.0 * necessity)
    gain_ratio = (
        raw_distances - target_distances
    ) / np.maximum(raw_distances, 0.005)
    reliability = np.clip(gain_ratio / 0.25, 0.5, 1.0)
    strengths[useful] = necessity[useful] * reliability[useful]
    strengths[strengths < 0.12] = 0.0
    return strengths.astype(np.float32)


def _teacher_targets_match(path, image_keys):
    if not path.exists():
        return False
    try:
        with np.load(path, allow_pickle=False) as target_data:
            saved_keys = [str(value) for value in target_data["keys"]]
            target_version = int(target_data["target_version"])
            edit_strengths = np.asarray(
                target_data["edit_strengths"],
                dtype=np.float32,
            )
    except (KeyError, OSError, ValueError):
        return False
    return (
        target_version == TEACHER_TARGET_VERSION
        and saved_keys == list(image_keys)
        and edit_strengths.shape == (len(image_keys),)
    )


def generate_teacher_targets(
    teacher_model_path,
    teacher_vec_path,
    image_keys,
    output_path,
    device,
):
    print("Stage 2/4: generating safe slider targets with the reference teacher.")
    teacher_model = PPO.load(str(teacher_model_path), device=resolve_device(device))
    teacher_vec = _load_inference_normalizer(
        teacher_vec_path,
        REFERENCE_CONDITIONED_OBSERVATION_MODE,
    )
    teacher_env = Cv2Env(
        image_keys=image_keys,
        env_id=-3,
        max_steps=TEACHER_MAX_EDIT_STEPS,
        action_step=TEACHER_ACTION_STEP,
        reward_profile=V3_REWARD_PROFILE,
        observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
    )

    target_sliders = np.zeros((len(image_keys), ACTION_DIM), dtype=np.float32)
    raw_distances = np.zeros(len(image_keys), dtype=np.float32)
    target_distances = np.zeros(len(image_keys), dtype=np.float32)
    best_steps = np.zeros(len(image_keys), dtype=np.int16)
    accepted = 0

    try:
        for index, image_id in enumerate(image_keys):
            observation, _ = teacher_env.reset(options={"image_id": image_id})
            raw_distance = float(teacher_env.initial_distance)
            best_score = raw_distance
            best_sliders = np.zeros(ACTION_DIM, dtype=np.float32)
            best_step = 0

            for step_index in range(TEACHER_MAX_EDIT_STEPS):
                normalized = teacher_vec.normalize_obs(observation.reshape(1, -1))[0]
                action, _ = teacher_model.predict(normalized, deterministic=True)
                observation, _, terminated, _, info = teacher_env.step(action)
                score = _teacher_selection_score(
                    info["distance"],
                    info["safety_penalty"],
                    teacher_env.current_sliders,
                )
                if score < best_score:
                    best_score = score
                    best_sliders = teacher_env.current_sliders.copy()
                    best_step = step_index + 1
                if terminated:
                    break

            (
                canonical_sliders,
                canonical_metrics,
                canonical_score,
            ) = _sparsify_teacher_sliders(
                teacher_env,
                best_sliders,
                raw_distance,
            )
            minimum_gain = max(0.00008, raw_distance * 0.02)
            distance_gain = raw_distance - canonical_metrics["distance"]
            if canonical_score < raw_distance and distance_gain >= minimum_gain:
                target_sliders[index] = canonical_sliders
                target_distances[index] = canonical_metrics["distance"]
                best_steps[index] = best_step
                accepted += 1
            else:
                target_distances[index] = raw_distance

            raw_distances[index] = raw_distance
            if (index + 1) % 250 == 0 or index + 1 == len(image_keys):
                print(
                    f"Teacher targets: {index + 1}/{len(image_keys)} "
                    f"({accepted} useful edits)"
                )
    finally:
        teacher_env.close()
        teacher_vec.close()

    edit_strengths = _calculate_student_edit_strengths(
        raw_distances,
        target_distances,
        target_sliders,
    )
    active_counts = np.sum(np.abs(target_sliders) >= 0.03, axis=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        target_version=np.asarray(TEACHER_TARGET_VERSION, dtype=np.int16),
        keys=np.asarray(image_keys),
        sliders=target_sliders,
        raw_distances=raw_distances,
        target_distances=target_distances,
        best_steps=best_steps,
        edit_strengths=edit_strengths,
    )
    print(
        f"Saved {accepted}/{len(image_keys)} non-zero teacher targets to "
        f"{output_path}; mean active controls={np.mean(active_counts):.2f}, "
        f"student no-edit labels={int(np.sum(edit_strengths <= 0.0))}."
    )


def build_distillation_dataset(
    targets_path,
    observations_path,
    actions_path,
    weights_path,
    vgg_cache,
):
    print("Stage 3/4: building no-reference states and behavior-cloning targets.")
    with np.load(targets_path, allow_pickle=False) as target_data:
        image_keys = [str(value) for value in target_data["keys"]]
        slider_targets = np.asarray(
            target_data["sliders"],
            dtype=np.float32,
        ).copy()
        raw_distances = np.asarray(
            target_data["raw_distances"],
            dtype=np.float32,
        )
        target_distances = np.asarray(
            target_data["target_distances"],
            dtype=np.float32,
        )
        edit_strengths = np.asarray(
            target_data["edit_strengths"],
            dtype=np.float32,
        )
    sample_count = len(image_keys) * len(DISTILL_STEP_INDICES)

    observations_path.parent.mkdir(parents=True, exist_ok=True)
    observation_store = np.lib.format.open_memmap(
        observations_path,
        mode="w+",
        dtype=np.float16,
        shape=(sample_count, SPATIAL_OBS_DIM),
    )
    action_store = np.lib.format.open_memmap(
        actions_path,
        mode="w+",
        dtype=np.float32,
        shape=(sample_count, STUDENT_POLICY_ACTION_DIM),
    )
    weight_store = np.lib.format.open_memmap(
        weights_path,
        mode="w+",
        dtype=np.float32,
        shape=(sample_count,),
    )

    style_context = np.array([1.0, 0.0], dtype=np.float32)
    raw_scale = max(float(np.percentile(raw_distances, 90)), 1e-5)
    effective_targets = slider_targets * edit_strengths[:, np.newaxis]
    target_magnitudes = np.mean(np.abs(effective_targets), axis=1)
    magnitude_scale = max(float(np.percentile(target_magnitudes, 90)), 1e-5)
    row = 0
    for index, (image_id, target_sliders, edit_strength) in enumerate(
        zip(image_keys, slider_targets, edit_strengths)
    ):
        effective_target = target_sliders * float(edit_strength)
        raw_img = _load_policy_image(image_id, "raw")
        raw_hist = calculate_histogram(raw_img)
        raw_stats = calculate_lab_stats(raw_img)
        raw_spatial_stats = calculate_spatial_lab_stats(raw_img)
        gain = max(
            float(raw_distances[index] - target_distances[index]),
            0.0,
        )
        gain_ratio = gain / max(float(raw_distances[index]), 0.005)
        target_magnitude = float(target_magnitudes[index])
        if edit_strength <= 0.0:
            image_weight = 4.0
        else:
            image_weight = (
                1.0
                + 1.50 * min(float(raw_distances[index]) / raw_scale, 1.0)
                + 1.25 * min(gain_ratio, 1.0)
                + 0.75 * min(target_magnitude / magnitude_scale, 1.0)
            )

        required_steps = int(
            np.ceil(
                np.max(np.abs(target_sliders))
                / max(CURRENT_ACTION_STEP, 1e-6)
            )
        )
        target_steps = int(
            np.clip(
                max(DISTILL_MIN_TARGET_STEPS, required_steps),
                1,
                CURRENT_MAX_EDIT_STEPS - DISTILL_STOP_STEPS,
            )
        )
        image_rng = np.random.default_rng(TRAIN_SEED + index)
        for step_index in DISTILL_STEP_INDICES:
            progress = min(step_index / max(target_steps, 1), 1.0)
            recovery_state = (
                edit_strength > 0.0
                and step_index > target_steps
                and (step_index - target_steps) % 2 == 1
            )
            if recovery_state:
                progress = 0.85
            current_sliders = effective_target * progress
            if (
                edit_strength > 0.0
                and 0 < step_index < target_steps
                and step_index % 2 == 0
            ):
                noise_scale = 0.035 + 0.015 * (
                    step_index / max(target_steps, 1)
                )
                current_sliders = np.clip(
                    current_sliders
                    + image_rng.normal(
                        0.0,
                        noise_scale,
                        ACTION_DIM,
                    ).astype(np.float32)
                    - effective_target * 0.10,
                    -STUDENT_SLIDER_LIMITS,
                    STUDENT_SLIDER_LIMITS,
                )
            current_img = apply_edits(raw_img.copy(), current_sliders)
            current_hist = calculate_histogram(current_img)
            current_stats = calculate_lab_stats(current_img)
            observation = build_spatial_no_reference_observation_from_features(
                style_context,
                current_sliders,
                current_hist,
                current_stats,
                raw_hist,
                raw_stats,
                calculate_spatial_lab_stats(current_img),
                raw_spatial_stats,
                vgg_cache[image_id],
                step_fraction=step_index / CURRENT_MAX_EDIT_STEPS,
            )
            remaining_steps = max(target_steps - step_index, 1)
            if recovery_state:
                remaining_steps = 1
            after_target = step_index >= target_steps and not recovery_state
            if edit_strength > 0.0 and not after_target:
                target_action = (
                    effective_target - current_sliders
                ) / (
                    remaining_steps
                    * CURRENT_ACTION_STEP
                    * max(float(edit_strength), 0.05)
                )
                gate_action = 2.0 * float(edit_strength) - 1.0
            else:
                target_action = np.zeros(ACTION_DIM, dtype=np.float32)
                gate_action = -1.0
            policy_target = np.concatenate(
                [
                    np.clip(target_action, -1.0, 1.0),
                    np.asarray([gate_action], dtype=np.float32),
                ]
            )
            observation_store[row] = observation.astype(np.float16)
            action_store[row] = policy_target
            weight_store[row] = image_weight * (
                1.35 if step_index == 0 else 1.0
            )
            row += 1

        if (index + 1) % 250 == 0 or index + 1 == len(image_keys):
            print(f"Distillation states: {index + 1}/{len(image_keys)} images")

    observation_store.flush()
    action_store.flush()
    weight_store.flush()
    del observation_store
    del action_store
    del weight_store
    print(
        f"Saved {sample_count} no-reference imitation samples "
        f"({observations_path.name}, {actions_path.name}, "
        f"{weights_path.name}; dataset v{DISTILL_DATASET_VERSION})."
    )


def fit_observation_normalizer(vec_env, observations_path, batch_size=512):
    observations = np.load(observations_path, mmap_mode="r")
    for start in range(0, len(observations), batch_size):
        batch = np.asarray(
            observations[start : start + batch_size],
            dtype=np.float32,
        )
        vec_env.obs_rms.update(batch)


def _behavior_clone_losses(predicted_actions, action_tensor):
    predicted_edits = predicted_actions[:, :ACTION_DIM]
    target_edits = action_tensor[:, :ACTION_DIM]
    predicted_gate = predicted_actions[:, ACTION_DIM]
    target_gate = action_tensor[:, ACTION_DIM]
    non_zero = (target_gate > -0.75).float()

    element_loss = F.smooth_l1_loss(
        predicted_edits,
        target_edits,
        reduction="none",
    ).mean(dim=1)
    magnitude_loss = F.smooth_l1_loss(
        predicted_edits.abs().mean(dim=1),
        target_edits.abs().mean(dim=1),
        reduction="none",
    )
    direction_loss = 1.0 - F.cosine_similarity(
        predicted_edits,
        target_edits,
        dim=1,
        eps=1e-6,
    )
    direction_loss = direction_loss * non_zero
    gate_loss = F.smooth_l1_loss(
        predicted_gate,
        target_gate,
        reduction="none",
    )
    predicted_overlap = torch.stack(
        [
            torch.minimum(
                predicted_edits[:, left].abs(),
                predicted_edits[:, right].abs(),
            ).square()
            for left, right in REDUNDANT_ACTION_PAIRS
        ],
        dim=1,
    ).mean(dim=1)
    target_overlap = torch.stack(
        [
            torch.minimum(
                target_edits[:, left].abs(),
                target_edits[:, right].abs(),
            ).square()
            for left, right in REDUNDANT_ACTION_PAIRS
        ],
        dim=1,
    ).mean(dim=1)
    excess_overlap_loss = F.relu(predicted_overlap - target_overlap)
    per_sample_loss = (
        element_loss
        + 0.35 * magnitude_loss
        + 0.08 * direction_loss
        + 0.05 * excess_overlap_loss
        + 0.45 * gate_loss
    )
    return per_sample_loss, non_zero


def _evaluate_behavior_clone(
    policy,
    vec_env,
    observations_path,
    actions_path,
    device,
):
    observations = np.load(observations_path, mmap_mode="r")
    target_actions = np.load(actions_path, mmap_mode="r")
    predictions = []
    policy.set_training_mode(False)
    with torch.inference_mode():
        for start in range(0, len(observations), BC_BATCH_SIZE):
            raw_observations = np.asarray(
                observations[start : start + BC_BATCH_SIZE],
                dtype=np.float32,
            )
            normalized = vec_env.normalize_obs(raw_observations)
            observation_tensor = torch.as_tensor(
                normalized,
                dtype=torch.float32,
                device=device,
            )
            distribution = policy.get_distribution(observation_tensor)
            predictions.append(
                distribution.distribution.mean.detach().cpu().numpy()
            )
    predicted = np.concatenate(predictions, axis=0)
    targets = np.asarray(target_actions, dtype=np.float32)
    predicted_edits = predicted[:, :ACTION_DIM]
    target_edits = targets[:, :ACTION_DIM]
    active = np.abs(target_edits) >= 0.10
    active_sign_accuracy = float(
        np.mean(
            np.sign(predicted_edits[active])
            == np.sign(target_edits[active])
        )
    ) if np.any(active) else 1.0
    target_norm = np.linalg.norm(target_edits, axis=1)
    valid_direction = target_norm >= 0.10
    cosine = np.sum(predicted_edits * target_edits, axis=1) / np.maximum(
        np.linalg.norm(predicted_edits, axis=1) * target_norm,
        1e-8,
    )
    mean_cosine = float(np.mean(cosine[valid_direction])) if np.any(
        valid_direction
    ) else 1.0
    edit_mae = float(np.mean(np.abs(predicted_edits - target_edits)))
    gate_mae = float(
        np.mean(np.abs(predicted[:, ACTION_DIM] - targets[:, ACTION_DIM]))
    )
    magnitude_correlation = float(
        np.corrcoef(
            np.mean(np.abs(predicted_edits), axis=1),
            np.mean(np.abs(target_edits), axis=1),
        )[0, 1]
    )
    if not np.isfinite(magnitude_correlation):
        magnitude_correlation = 0.0
    objective = (
        edit_mae
        + 0.20 * gate_mae
        + 0.05 * (1.0 - mean_cosine)
        + 0.05 * (1.0 - active_sign_accuracy)
    )
    return {
        "objective": float(objective),
        "edit_mae": edit_mae,
        "gate_mae": gate_mae,
        "active_sign_accuracy": active_sign_accuracy,
        "mean_cosine": mean_cosine,
        "magnitude_correlation": magnitude_correlation,
        "sample_count": int(len(targets)),
    }


def behavior_clone_student(
    model,
    vec_env,
    observations_path,
    actions_path,
    weights_path,
    epochs,
    seed,
    bc_device,
    validation_observations_path=None,
    validation_actions_path=None,
    validation_history_path=None,
    learning_rate=BC_LEARNING_RATE,
    max_samples=None,
    label="Behavior cloning",
):
    observations = np.load(observations_path, mmap_mode="r")
    target_actions = np.load(actions_path, mmap_mode="r")
    target_weights = np.load(weights_path, mmap_mode="r")
    if not (
        len(observations) == len(target_actions) == len(target_weights)
    ):
        raise ValueError(
            "Distillation observations/actions/weights have different lengths."
        )

    device = torch.device(resolve_device(bc_device))
    policy = model.policy
    policy.to(device)
    policy.set_training_mode(True)
    optimizer = torch.optim.AdamW(
        policy.parameters(),
        lr=learning_rate,
        weight_decay=BC_WEIGHT_DECAY,
    )
    rng = np.random.default_rng(seed)
    best_state = None
    best_metrics = None
    no_improvement_rounds = 0

    for epoch in range(epochs):
        if max_samples is not None and max_samples < len(observations):
            indices = rng.choice(
                len(observations),
                size=max_samples,
                replace=False,
            )
            indices = rng.permutation(indices)
        else:
            indices = rng.permutation(len(observations))
        epoch_loss = 0.0
        batch_count = 0
        for start in range(0, len(indices), BC_BATCH_SIZE):
            batch_indices = indices[start : start + BC_BATCH_SIZE]
            raw_observations = np.asarray(
                observations[batch_indices],
                dtype=np.float32,
            )
            normalized = vec_env.normalize_obs(raw_observations)
            observation_tensor = torch.as_tensor(
                normalized,
                dtype=torch.float32,
                device=device,
            )
            action_tensor = torch.as_tensor(
                np.asarray(target_actions[batch_indices], dtype=np.float32),
                dtype=torch.float32,
                device=device,
            )
            weight_tensor = torch.as_tensor(
                np.asarray(target_weights[batch_indices], dtype=np.float32),
                dtype=torch.float32,
                device=device,
            )
            weight_tensor = weight_tensor / weight_tensor.mean().clamp_min(1e-6)

            distribution = policy.get_distribution(observation_tensor)
            predicted_actions = distribution.distribution.mean
            per_sample_loss, non_zero = _behavior_clone_losses(
                predicted_actions,
                action_tensor,
            )
            loss = (
                per_sample_loss
                * weight_tensor
                * (1.0 + 0.25 * non_zero)
            ).mean()

            zero_mask = non_zero < 0.5
            if torch.any(zero_mask):
                loss = (
                    loss
                    + 0.20
                    * predicted_actions[zero_mask, :ACTION_DIM].square().mean()
                )

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_loss += float(loss.detach().cpu())
            batch_count += 1

        mean_train_loss = epoch_loss / max(batch_count, 1)
        message = (
            f"{label} epoch {epoch + 1}/{epochs}: "
            f"loss={mean_train_loss:.6f}"
        )
        if (
            validation_observations_path is not None
            and validation_actions_path is not None
        ):
            metrics = _evaluate_behavior_clone(
                policy,
                vec_env,
                validation_observations_path,
                validation_actions_path,
                device,
            )
            message += (
                f", val={metrics['objective']:.5f}, "
                f"sign={metrics['active_sign_accuracy']:.3f}, "
                f"cos={metrics['mean_cosine']:.3f}, "
                f"gate_mae={metrics['gate_mae']:.3f}"
            )
            if validation_history_path is not None:
                _write_validation_record(
                    validation_history_path,
                    {
                        "stage": "behavior_cloning",
                        "epoch": int(epoch + 1),
                        "train_loss": float(mean_train_loss),
                        **metrics,
                    },
                )
            if (
                best_metrics is None
                or metrics["objective"]
                < best_metrics["objective"] - BC_MIN_VALIDATION_IMPROVEMENT
            ):
                best_metrics = metrics
                best_metrics["best_epoch"] = int(epoch + 1)
                best_state = copy.deepcopy(policy.state_dict())
                no_improvement_rounds = 0
            else:
                no_improvement_rounds += 1
        print(message)
        if (
            best_metrics is not None
            and no_improvement_rounds >= BC_EARLY_STOP_PATIENCE
        ):
            print(
                f"{label} early stopping at epoch {epoch + 1}; "
                f"best epoch={best_metrics['best_epoch']}."
            )
            break

        policy.set_training_mode(True)

    if best_state is not None:
        policy.load_state_dict(best_state)
    policy.to(model.device)
    policy.set_training_mode(False)
    return best_metrics or {}


def warn_existing_outputs(config):
    candidates = [
        config["model_path"].with_suffix(".zip"),
        config["vec_normalize_path"],
        config["metadata_path"],
        config["history_dir"],
        config["checkpoint_dir"],
        config["artifact_dir"],
    ]
    existing = [path for path in candidates if path.exists()]
    if existing:
        print("Generated outputs for the current model will be refreshed:")
        for path in existing:
            print(f"  {path}")
    print("Historical v3/v4/v5 model files are not modified by this training run.")


def prepare_run_directories(config, seed):
    for path in (config["history_dir"], config["checkpoint_dir"]):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    config["artifact_dir"].mkdir(parents=True, exist_ok=True)
    manifest = {
        "started_at": time.time(),
        "seed": int(seed),
        "pipeline": "sparse_teacher_gated_no_reference_ppo_v3",
        "expert_c_score_version": EXPERT_C_SCORE_VERSION,
    }
    (config["history_dir"] / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def save_training_metadata(
    config,
    seed,
    teacher_steps,
    student_steps,
    bc_epochs,
    num_envs,
    device,
    bc_device,
    fit_count,
    validation_count,
    best_validation_metrics,
    bc_validation_metrics,
):
    metadata = {
        "schema_version": 4,
        "pipeline": "sparse_teacher_gated_no_reference_ppo_v3",
        "observation_mode": STUDENT_OBSERVATION_MODE,
        "observation_dim": SPATIAL_OBS_DIM,
        "policy_action_dim": STUDENT_POLICY_ACTION_DIM,
        "policy_uses_expert_target": False,
        "reward_uses_expert_target": True,
        "teacher_uses_expert_target": True,
        "teacher_target_type": "sparse_safe_slider_vector_with_edit_strength",
        "teacher_target_version": TEACHER_TARGET_VERSION,
        "distillation_dataset_version": DISTILL_DATASET_VERSION,
        "distillation_states_per_image": len(DISTILL_STEP_INDICES),
        "student_initialization": "validated_weighted_behavior_cloning",
        "student_rl_fine_tune": bool(student_steps > 0),
        "student_rl_behavior_anchor": False,
        "student_early_stop_patience": STUDENT_EARLY_STOP_PATIENCE,
        "test_split_used_for_training_or_selection": False,
        "reward_profile_version": EXPERT_C_SCORE_VERSION,
        "expert_c_score_version": EXPERT_C_SCORE_VERSION,
        "seed": int(seed),
        "teacher_timesteps": int(teacher_steps),
        "behavior_cloning_epochs": int(bc_epochs),
        "student_ppo_timesteps_requested": int(student_steps),
        "student_ppo_timesteps_completed": int(
            best_validation_metrics.get("completed_timesteps", 0)
        ),
        "fit_image_count": int(fit_count),
        "validation_image_count": int(validation_count),
        "validation_eval_sample_count": int(
            min(VALIDATION_SAMPLE_COUNT, validation_count)
        ),
        "action_parameter_count": ACTION_DIM,
        "action_parameter_strategy": "20_controls_sparse_canonical_targets",
        "edit_gate_enabled": STUDENT_EDIT_GATE_ENABLED,
        "edit_gate_stop_threshold": STUDENT_EDIT_GATE_STOP_THRESHOLD,
        "inference_slider_limits": STUDENT_SLIDER_LIMITS.tolist(),
        "teacher_action_step": TEACHER_ACTION_STEP,
        "action_step": CURRENT_ACTION_STEP,
        "max_edit_steps": CURRENT_MAX_EDIT_STEPS,
        "teacher_max_edit_steps": TEACHER_MAX_EDIT_STEPS,
        "policy_image_max_side": POLICY_IMAGE_MAX_SIDE,
        "inference_safety_stop": True,
        "inference_min_steps": 3,
        "inference_safety_limits": DEFAULT_INFERENCE_SAFETY_LIMITS,
        "inference_safety_backoff": True,
        "num_envs": int(num_envs),
        "ppo_device": resolve_device(device),
        "behavior_cloning_device": resolve_device(bc_device),
        "ppo_gamma": PPO_GAMMA,
        "behavior_cloning_validation": bc_validation_metrics,
        "best_validation": best_validation_metrics,
    }
    config["metadata_path"].write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def train(
    seed=TRAIN_SEED,
    teacher_steps=DEFAULT_TEACHER_TIMESTEPS,
    student_steps=DEFAULT_STUDENT_TIMESTEPS,
    bc_epochs=DEFAULT_BC_EPOCHS,
    num_envs=DEFAULT_NUM_ENVS,
    device=DEFAULT_DEVICE,
    bc_device=DEFAULT_BC_DEVICE,
    validation_count=DEFAULT_VALIDATION_COUNT,
    reuse_teacher=False,
):
    if num_envs < 1:
        raise ValueError("num_envs must be at least 1.")
    if teacher_steps < 1 or student_steps < 0 or bc_epochs < 1:
        raise ValueError(
            "Teacher steps and BC epochs must be positive; "
            "student steps may be zero for a BC-only run."
        )

    config = TRAIN_CONFIG
    print(
        f"Starting {config['model_name']} with the teacher-student RL pipeline."
    )
    print(
        "Final policy boundary: raw/current image features only; Expert C is "
        "available to the teacher and training reward, never student inference."
    )
    print(
        f"Budget: teacher PPO={teacher_steps:,}, BC={bc_epochs} epochs, "
        f"student PPO={student_steps:,}."
    )
    print(
        f"Reproducibility: seed={seed}, envs={num_envs}, "
        f"PPO device={resolve_device(device)}, BC device={resolve_device(bc_device)}."
    )
    set_global_seed(seed)
    train_keys, _ = validate_training_inputs()
    fit_keys, validation_keys = split_fit_validation(
        train_keys,
        validation_count,
        seed,
    )
    validation_sample = list(validation_keys)
    print(
        f"Training partition: {len(fit_keys)} fit / {len(validation_keys)} validation; "
        "the 500-image test split remains untouched."
    )

    warn_existing_outputs(config)
    prepare_run_directories(config, seed)
    validation_history_path = config["history_dir"] / "validation_history.jsonl"
    validation_history_path.write_text("", encoding="utf-8")
    vgg_cache = _load_vgg_cache()

    teacher_ready = (
        config["teacher_model_path"].with_suffix(".zip").exists()
        and config["teacher_vec_path"].exists()
    )
    teacher_was_reused = reuse_teacher and teacher_ready
    if teacher_was_reused:
        print("Stage 1/4: reusing the existing reference teacher.")
    else:
        print("Stage 1/4: training a reference-conditioned PPO teacher.")
        teacher_env = build_training_env(
            config["history_dir"] / "stage1_reference_teacher",
            fit_keys,
            seed,
            reward_profile=V3_REWARD_PROFILE,
            observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
            action_step=TEACHER_ACTION_STEP,
            max_steps=TEACHER_MAX_EDIT_STEPS,
            num_envs=num_envs,
        )
        teacher_model = create_ppo_model(
            teacher_env,
            seed,
            device,
            learning_rate=TEACHER_LEARNING_RATE,
            ent_coef=TEACHER_ENT_COEF,
            observation_mode=REFERENCE_CONDITIONED_OBSERVATION_MODE,
        )
        teacher_evaluator = lambda model, normalizer: evaluate_reference_teacher(
            model,
            normalizer,
            validation_sample,
        )
        teacher_best_model, teacher_best_vec, _ = learn_with_validation(
            teacher_model,
            teacher_env,
            teacher_steps,
            "teacher",
            teacher_evaluator,
            config["checkpoint_dir"],
            validation_history_path,
        )
        teacher_env.close()
        shutil.copy2(
            teacher_best_model.with_suffix(".zip"),
            config["teacher_model_path"].with_suffix(".zip"),
        )
        shutil.copy2(teacher_best_vec, config["teacher_vec_path"])

    if not (
        teacher_was_reused
        and _teacher_targets_match(config["teacher_targets_path"], fit_keys)
    ):
        generate_teacher_targets(
            config["teacher_model_path"],
            config["teacher_vec_path"],
            fit_keys,
            config["teacher_targets_path"],
            device,
        )
    else:
        print(
            f"Stage 2/4: reusing teacher targets at "
            f"{config['teacher_targets_path']}."
        )
    if not (
        teacher_was_reused
        and _teacher_targets_match(
            config["validation_teacher_targets_path"],
            validation_keys,
        )
    ):
        generate_teacher_targets(
            config["teacher_model_path"],
            config["teacher_vec_path"],
            validation_keys,
            config["validation_teacher_targets_path"],
            device,
        )
    else:
        print(
            "Reusing held-out teacher targets at "
            f"{config['validation_teacher_targets_path']}."
        )

    build_distillation_dataset(
        config["teacher_targets_path"],
        config["distill_observations_path"],
        config["distill_actions_path"],
        config["distill_weights_path"],
        vgg_cache,
    )
    build_distillation_dataset(
        config["validation_teacher_targets_path"],
        config["validation_distill_observations_path"],
        config["validation_distill_actions_path"],
        config["validation_distill_weights_path"],
        vgg_cache,
    )

    student_env = build_training_env(
        config["history_dir"] / "stage4_student_ppo",
        fit_keys,
        seed + 20_000,
        reward_profile=V5_REWARD_PROFILE,
        observation_mode=STUDENT_OBSERVATION_MODE,
        action_step=CURRENT_ACTION_STEP,
        max_steps=CURRENT_MAX_EDIT_STEPS,
        num_envs=num_envs,
        policy_action_dim=STUDENT_POLICY_ACTION_DIM,
        use_edit_gate=STUDENT_EDIT_GATE_ENABLED,
    )
    fit_observation_normalizer(
        student_env,
        config["distill_observations_path"],
    )
    student_model = create_ppo_model(
        student_env,
        seed + 20_000,
        device,
        learning_rate=STUDENT_LEARNING_RATE,
        ent_coef=STUDENT_ENT_COEF,
        observation_mode=STUDENT_OBSERVATION_MODE,
        clip_range=0.08,
        n_epochs=5,
        target_kl=0.01,
    )
    bc_validation_metrics = behavior_clone_student(
        student_model,
        student_env,
        config["distill_observations_path"],
        config["distill_actions_path"],
        config["distill_weights_path"],
        bc_epochs,
        seed + 30_000,
        bc_device,
        validation_observations_path=(
            config["validation_distill_observations_path"]
        ),
        validation_actions_path=config["validation_distill_actions_path"],
        validation_history_path=(
            config["history_dir"] / "bc_validation_history.jsonl"
        ),
    )
    _save_model_and_vec(
        student_model,
        student_env,
        config["bc_model_path"],
        config["artifact_dir"] / "student_after_bc_vec_normalize.pkl",
    )

    print("Stage 4/4: fine-tuning the no-reference student with PPO.")
    student_env.training = False
    student_env.norm_reward = False
    student_evaluator = lambda model, normalizer: evaluate_no_reference_student(
        model,
        normalizer,
        validation_sample,
        vgg_cache,
    )

    student_best_model, student_best_vec, best_validation = learn_with_validation(
        student_model,
        student_env,
        student_steps,
        "student",
        student_evaluator,
        config["checkpoint_dir"],
        validation_history_path,
        evaluate_initial=True,
        validation_interval_timesteps=STUDENT_VALIDATION_INTERVAL_TIMESTEPS,
        early_stop_patience=STUDENT_EARLY_STOP_PATIENCE,
    )
    student_env.close()

    final_model = PPO.load(
        str(student_best_model),
        device=resolve_device(device),
    )
    final_model.save(str(config["model_path"]))
    shutil.copy2(student_best_vec, config["vec_normalize_path"])
    save_training_metadata(
        config,
        seed,
        teacher_steps,
        student_steps,
        bc_epochs,
        num_envs,
        device,
        bc_device,
        len(fit_keys),
        len(validation_keys),
        best_validation,
        bc_validation_metrics,
    )
    print(
        f"Saved best validation student to {config['model_path']}.zip, "
        f"{config['vec_normalize_path']}, and {config['metadata_path']}."
    )
    print("The historical v3/v4/v5 artifacts remain unchanged.")


def build_argument_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Train a reference teacher, distill its slider targets into a "
            "no-reference student, then PPO fine-tune the student."
        )
    )
    parser.add_argument("--seed", type=int, default=TRAIN_SEED)
    parser.add_argument(
        "--teacher-steps",
        type=int,
        default=DEFAULT_TEACHER_TIMESTEPS,
        help=f"Reference-teacher PPO timesteps (default: {DEFAULT_TEACHER_TIMESTEPS}).",
    )
    parser.add_argument(
        "--student-steps",
        type=int,
        default=DEFAULT_STUDENT_TIMESTEPS,
        help=f"No-reference student PPO timesteps (default: {DEFAULT_STUDENT_TIMESTEPS}).",
    )
    parser.add_argument(
        "--bc-epochs",
        type=int,
        default=DEFAULT_BC_EPOCHS,
        help=f"Behavior-cloning epochs (default: {DEFAULT_BC_EPOCHS}).",
    )
    parser.add_argument(
        "--validation-count",
        type=int,
        default=DEFAULT_VALIDATION_COUNT,
        help=(
            "Images held out from train_keys for validation and best-checkpoint "
            f"selection (default: {DEFAULT_VALIDATION_COUNT})."
        ),
    )
    parser.add_argument(
        "--num-envs",
        type=int,
        default=DEFAULT_NUM_ENVS,
        help=f"Parallel PPO environments (default: {DEFAULT_NUM_ENVS}).",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "cuda", "auto"),
        default=DEFAULT_DEVICE,
        help=(
            f"PPO device (default: {DEFAULT_DEVICE}; CPU is recommended for "
            "SB3 MLP PPO)."
        ),
    )
    parser.add_argument(
        "--bc-device",
        choices=("cpu", "cuda", "auto"),
        default=DEFAULT_BC_DEVICE,
        help=(
            f"Behavior-cloning device (default: {DEFAULT_BC_DEVICE}; CUDA is "
            "useful for the supervised stage)."
        ),
    )
    parser.add_argument(
        "--reuse-teacher",
        action="store_true",
        help=(
            "Reuse compatible reference-teacher weights; reuse slider targets "
            "only when their version matches, then retrain the student."
        ),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate data, filters, VGG cache, and teacher/student boundary.",
    )
    return parser


if __name__ == "__main__":
    multiprocessing.freeze_support()
    arguments = build_argument_parser().parse_args()
    if arguments.check_only:
        validate_training_inputs(deep_check=True)
    else:
        train(
            seed=arguments.seed,
            teacher_steps=arguments.teacher_steps,
            student_steps=arguments.student_steps,
            bc_epochs=arguments.bc_epochs,
            num_envs=arguments.num_envs,
            device=arguments.device,
            bc_device=arguments.bc_device,
            validation_count=arguments.validation_count,
            reuse_teacher=arguments.reuse_teacher,
        )
