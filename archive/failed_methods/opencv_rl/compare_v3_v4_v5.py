import argparse
import pickle
import random

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

from cv2_filters import ACTION_DIM, ACTION_NAMES, POLICY_IMAGE_MAX_SIDE, apply_edits
from model_paths import (
    CURRENT_ACTION_STEP,
    CURRENT_MAX_EDIT_STEPS,
    CURRENT_METADATA_PATH,
    CURRENT_MODEL_PATH,
    CURRENT_VEC_NORMALIZE_PATH,
    V3_ACTION_STEP,
    V3_MAX_EDIT_STEPS,
    V3_MODEL_PATH,
    V3_VEC_NORMALIZE_PATH,
    V4_ACTION_STEP,
    V4_MAX_EDIT_STEPS,
    V4_MODEL_PATH,
    V4_VEC_NORMALIZE_PATH,
    V5_ACTION_STEP,
    V5_MAX_EDIT_STEPS,
    V5_MODEL_PATH,
    V5_VEC_NORMALIZE_PATH,
)
from paths import DEFAULT_DATA_DIR, RL_DIR, resolve_rl_file
from rl_features import (
    LEGACY_REFERENCE_OBSERVATION_MODE,
    NO_REFERENCE_OBSERVATION_MODE,
    ObservationOnlyEnv,
    build_reference_conditioned_observation_from_features,
    calculate_histogram,
    calculate_lab_stats,
    calculate_weighted_lab_mse,
    load_no_reference_metadata,
    paired_style_score,
    run_no_reference_policy,
)
from train_cv2 import (  # noqa: F401
    GeneralizedSpatialFeatureExtractor,
    MultiModalFeatureExtractor,
    SpatialMultiModalFeatureExtractor,
)


VERSION_SPECS = [
    {
        "label": "new no-ref",
        "model_path": CURRENT_MODEL_PATH,
        "vec_path": CURRENT_VEC_NORMALIZE_PATH,
        "metadata_path": CURRENT_METADATA_PATH,
        "action_step": CURRENT_ACTION_STEP,
        "max_steps": CURRENT_MAX_EDIT_STEPS,
        "observation_mode": NO_REFERENCE_OBSERVATION_MODE,
    },
    {
        "label": "v3 legacy",
        "model_path": V3_MODEL_PATH,
        "vec_path": V3_VEC_NORMALIZE_PATH,
        "action_step": V3_ACTION_STEP,
        "max_steps": V3_MAX_EDIT_STEPS,
        "observation_mode": LEGACY_REFERENCE_OBSERVATION_MODE,
    },
    {
        "label": "v4 legacy",
        "model_path": V4_MODEL_PATH,
        "vec_path": V4_VEC_NORMALIZE_PATH,
        "action_step": V4_ACTION_STEP,
        "max_steps": V4_MAX_EDIT_STEPS,
        "observation_mode": LEGACY_REFERENCE_OBSERVATION_MODE,
    },
    {
        "label": "v5 legacy",
        "model_path": V5_MODEL_PATH,
        "vec_path": V5_VEC_NORMALIZE_PATH,
        "action_step": V5_ACTION_STEP,
        "max_steps": V5_MAX_EDIT_STEPS,
        "observation_mode": LEGACY_REFERENCE_OBSERVATION_MODE,
    },
]


def load_vgg_cache():
    cache_path = resolve_rl_file("vgg_cache_5000.pkl")
    if not cache_path.exists():
        raise FileNotFoundError(f"Missing VGG cache: {cache_path}. Run precompute.py first.")

    with open(cache_path, "rb") as f:
        raw_cache = pickle.load(f)

    cache = {}
    for image_id, value in raw_cache.items():
        feature = np.asarray(value, dtype=np.float32)
        cache[image_id] = feature / (np.linalg.norm(feature) + 1e-8)
    return cache


def load_agent_bundle(spec):
    model_zip = spec["model_path"].with_suffix(".zip")
    if not model_zip.exists():
        raise FileNotFoundError(f"Missing model: {model_zip}")
    if not spec["vec_path"].exists():
        raise FileNotFoundError(f"Missing VecNormalize stats: {spec['vec_path']}")

    metadata = None
    if spec["observation_mode"] == NO_REFERENCE_OBSERVATION_MODE:
        metadata = load_no_reference_metadata(spec["metadata_path"])

    model = PPO.load(str(spec["model_path"]), device="cpu")
    observation_dim = (
        int(metadata["observation_dim"])
        if metadata is not None
        else ObservationOnlyEnv().observation_space.shape[0]
    )
    policy_action_dim = (
        int(metadata.get("policy_action_dim", ACTION_DIM))
        if metadata is not None
        else ACTION_DIM
    )
    dummy_env = DummyVecEnv(
        [
            lambda: ObservationOnlyEnv(
                observation_dim,
                action_dim=policy_action_dim,
            )
        ]
    )
    vec_env = VecNormalize.load(str(spec["vec_path"]), dummy_env)
    vec_env.training = False
    vec_env.norm_reward = False
    return model, vec_env, metadata


def run_legacy_reference_policy(
    model,
    vec_env,
    raw_img,
    expert_img,
    vgg_feature,
    action_step,
    max_steps,
):
    """Reproduce the old v3/v4/v5 input contract for historical comparison."""
    expert_id = np.array([1.0, 0.0], dtype=np.float32)
    expert_hist = calculate_histogram(expert_img)
    expert_stats = calculate_lab_stats(expert_img)
    current_img = raw_img.copy()
    current_sliders = np.zeros(ACTION_DIM, dtype=np.float32)

    for _ in range(max_steps):
        current_hist = calculate_histogram(current_img)
        current_stats = calculate_lab_stats(current_img)
        observation = build_reference_conditioned_observation_from_features(
            expert_id,
            current_sliders,
            current_hist,
            current_stats,
            expert_hist,
            expert_stats,
            vgg_feature,
        )
        normalized_observation = vec_env.normalize_obs(observation.reshape(1, -1))[0]
        action, _ = model.predict(normalized_observation, deterministic=True)
        action = np.asarray(action, dtype=np.float32).flatten()
        current_sliders = np.clip(
            current_sliders + action * action_step,
            -1.0,
            1.0,
        )
        current_img = apply_edits(raw_img.copy(), current_sliders)

    return current_img, current_sliders


def resize_pair(raw_img, expert_img, max_side=POLICY_IMAGE_MAX_SIDE):
    h, w = raw_img.shape[:2]
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        size = (max(int(w * scale), 1), max(int(h * scale), 1))
        raw_img = cv2.resize(raw_img, size, interpolation=cv2.INTER_AREA)
    else:
        size = (w, h)
    expert_img = cv2.resize(expert_img, size, interpolation=cv2.INTER_AREA)
    return raw_img, expert_img


def evaluate_output(edited_img, expert_img, sliders, steps_used):
    score, details = paired_style_score(edited_img, expert_img)
    slider_summary = {
        name: round(float(value), 2)
        for name, value in zip(ACTION_NAMES, sliders)
        if abs(float(value)) >= 0.05
    }
    return {
        "image": edited_img,
        "score": float(score),
        "mse": float(details["lab_mse"]),
        "sliders": slider_summary,
        "steps_used": int(steps_used),
    }


def main(count=6, seed=42):
    keys_path = resolve_rl_file("test_keys.txt")
    with open(keys_path, "r", encoding="utf-8") as f:
        test_images = [line.strip() for line in f if line.strip()]
    if not test_images:
        raise RuntimeError("No test images found.")

    images = random.Random(seed).sample(test_images, min(count, len(test_images)))
    print(f"Selected test images: {images}")
    print("new no-ref uses raw-image features only; legacy models receive Expert C features.")
    print("All displayed outputs use the fixed final step. Expert metrics do not select a step.")

    agents = {}
    for spec in VERSION_SPECS:
        agents[spec["label"]] = load_agent_bundle(spec)
    vgg_cache = load_vgg_cache()

    fig, axes = plt.subplots(
        len(images),
        len(VERSION_SPECS) + 2,
        figsize=(5 * (len(VERSION_SPECS) + 2), 5 * len(images)),
    )
    axes = np.atleast_2d(axes)

    try:
        for row_idx, image_id in enumerate(images):
            raw_img = cv2.imread(str(DEFAULT_DATA_DIR / "raw" / image_id))
            expert_img = cv2.imread(str(DEFAULT_DATA_DIR / "c" / image_id))
            if raw_img is None or expert_img is None:
                raise FileNotFoundError(f"Missing image pair: {image_id}")
            if image_id not in vgg_cache:
                raise KeyError(f"Missing VGG feature: {image_id}")
            raw_img, expert_img = resize_pair(raw_img, expert_img)
            initial_mse = calculate_weighted_lab_mse(raw_img, expert_img)

            results = {}
            for spec in VERSION_SPECS:
                model, vec_env, metadata = agents[spec["label"]]
                if spec["observation_mode"] == NO_REFERENCE_OBSERVATION_MODE:
                    edited_img, sliders, inference_info = run_no_reference_policy(
                        model,
                        vec_env,
                        raw_img,
                        vgg_cache[image_id],
                        action_step=float(metadata["action_step"]),
                        max_steps=int(metadata["max_edit_steps"]),
                        observation_mode=metadata["observation_mode"],
                        safety_stop=bool(
                            metadata.get("inference_safety_stop", False)
                        ),
                        min_steps=int(metadata.get("inference_min_steps", 3)),
                        safety_limits=metadata.get("inference_safety_limits"),
                        safety_backoff=bool(
                            metadata.get("inference_safety_backoff", False)
                        ),
                        edit_gate_enabled=bool(
                            metadata.get("edit_gate_enabled", False)
                        ),
                        edit_gate_stop_threshold=float(
                            metadata.get("edit_gate_stop_threshold", 0.12)
                        ),
                        slider_limits=metadata.get(
                            "inference_slider_limits"
                        ),
                        return_info=True,
                    )
                    steps_used = inference_info["steps_used"]
                else:
                    edited_img, sliders = run_legacy_reference_policy(
                        model,
                        vec_env,
                        raw_img,
                        expert_img,
                        vgg_cache[image_id],
                        spec["action_step"],
                        spec["max_steps"],
                    )
                    steps_used = spec["max_steps"]
                results[spec["label"]] = evaluate_output(
                    edited_img,
                    expert_img,
                    sliders,
                    steps_used,
                )

            print(f"[{image_id}] LAB MSE init: {initial_mse:.4f}")
            for spec in VERSION_SPECS:
                result = results[spec["label"]]
                print(
                    f"  {spec['label']}: final LAB MSE {result['mse']:.4f} "
                    f"| evaluation score {result['score']:.5f}"
                )

            axes[row_idx, 0].imshow(cv2.cvtColor(raw_img, cv2.COLOR_BGR2RGB))
            axes[row_idx, 0].set_title("RAW")
            axes[row_idx, 0].set_ylabel(f"Init LAB MSE: {initial_mse:.4f}")
            axes[row_idx, 0].set_xticks([])
            axes[row_idx, 0].set_yticks([])

            for col_idx, spec in enumerate(VERSION_SPECS, start=1):
                result = results[spec["label"]]
                axes[row_idx, col_idx].imshow(
                    cv2.cvtColor(result["image"], cv2.COLOR_BGR2RGB)
                )
                axes[row_idx, col_idx].set_title(spec["label"])
                axes[row_idx, col_idx].set_xlabel(
                    f"Step {result['steps_used']} | LAB MSE: {result['mse']:.4f}"
                )
                axes[row_idx, col_idx].set_xticks([])
                axes[row_idx, col_idx].set_yticks([])

            expert_col = len(VERSION_SPECS) + 1
            axes[row_idx, expert_col].imshow(cv2.cvtColor(expert_img, cv2.COLOR_BGR2RGB))
            axes[row_idx, expert_col].set_title("Expert Target")
            axes[row_idx, expert_col].axis("off")
    finally:
        for _, vec_env, _ in agents.values():
            vec_env.close()

    plt.tight_layout()
    save_path = (RL_DIR / "multi_comparison_no_ref_vs_legacy.jpg").resolve()
    plt.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"Saved comparison: {save_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=6, help="Number of test images.")
    parser.add_argument("--seed", type=int, default=42, help="Image sampling seed.")
    args = parser.parse_args()
    try:
        main(count=args.count, seed=args.seed)
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
