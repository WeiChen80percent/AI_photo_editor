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

from cv2_filters import ACTION_NAMES
from model_paths import (
    CURRENT_METADATA_PATH,
    CURRENT_MODEL_PATH,
    CURRENT_VEC_NORMALIZE_PATH,
)
from paths import DEFAULT_DATA_DIR, RL_DIR, resolve_rl_file
from rl_features import (
    ObservationOnlyEnv,
    load_no_reference_metadata,
    paired_style_score,
    run_no_reference_policy,
)
from train_cv2 import (  # noqa: F401
    GeneralizedSpatialFeatureExtractor,
    MultiModalFeatureExtractor,
    SpatialMultiModalFeatureExtractor,
)


def load_vgg_cache():
    cache_path = resolve_rl_file("vgg_cache_5000.pkl")
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Missing raw-image VGG cache: {cache_path}. Run precompute.py first."
        )

    with open(cache_path, "rb") as f:
        raw_cache = pickle.load(f)

    cache = {}
    for image_id, value in raw_cache.items():
        feature = np.asarray(value, dtype=np.float32)
        cache[image_id] = feature / (np.linalg.norm(feature) + 1e-8)
    return cache


def load_no_reference_agent():
    model_zip = CURRENT_MODEL_PATH.with_suffix(".zip")
    if not model_zip.exists():
        raise FileNotFoundError(f"Missing current model: {model_zip}. Run train_cv2.py first.")
    if not CURRENT_VEC_NORMALIZE_PATH.exists():
        raise FileNotFoundError(
            f"Missing VecNormalize stats: {CURRENT_VEC_NORMALIZE_PATH}. Run train_cv2.py first."
        )

    metadata = load_no_reference_metadata(CURRENT_METADATA_PATH)
    model = PPO.load(str(CURRENT_MODEL_PATH), device="cpu")
    observation_dim = int(metadata["observation_dim"])
    policy_action_dim = int(metadata.get("policy_action_dim", len(ACTION_NAMES)))
    dummy_env = DummyVecEnv(
        [
            lambda: ObservationOnlyEnv(
                observation_dim,
                action_dim=policy_action_dim,
            )
        ]
    )
    vec_env = VecNormalize.load(str(CURRENT_VEC_NORMALIZE_PATH), dummy_env)
    vec_env.training = False
    vec_env.norm_reward = False
    return model, vec_env, metadata


def main(count=6, seed=None, output_name="multi_comparison.jpg", steps=None):
    if count < 1:
        raise ValueError("count must be at least 1.")

    model, vec_env, metadata = load_no_reference_agent()
    vgg_cache = load_vgg_cache()

    keys_path = resolve_rl_file("test_keys.txt")
    with open(keys_path, "r", encoding="utf-8") as f:
        test_images = [line.strip() for line in f if line.strip()]
    if not test_images:
        raise RuntimeError("No test images found.")

    images = random.Random(seed).sample(test_images, min(count, len(test_images)))
    print(f"Selected test images: {images}")
    inference_steps = int(
        metadata["max_edit_steps"] if steps is None else steps
    )
    if inference_steps < 1:
        raise ValueError("steps must be at least 1.")
    safety_stop = bool(metadata.get("inference_safety_stop", False))
    print(
        "Inference mode: no-reference, up to "
        f"{inference_steps} steps (safety stop={safety_stop}); "
        "expert targets are metrics-only."
    )

    fig, axes = plt.subplots(len(images), 3, figsize=(15, 5 * len(images)))
    axes = np.atleast_2d(axes)
    initial_mses = []
    final_mses = []
    initial_style_scores = []
    final_style_scores = []

    try:
        for row_idx, image_id in enumerate(images):
            raw_path = DEFAULT_DATA_DIR / "raw" / image_id
            raw_img = cv2.imread(str(raw_path))
            if raw_img is None:
                raise FileNotFoundError(f"Missing raw image: {raw_path}")
            if image_id not in vgg_cache:
                raise KeyError(f"Missing VGG feature for test image: {image_id}")

            # The policy runs before the paired expert image is loaded.
            raw_work = raw_img
            h, w = raw_work.shape[:2]
            policy_max_side = int(metadata.get("policy_image_max_side", 256))
            if max(h, w) > policy_max_side:
                scale = policy_max_side / max(h, w)
                size = (max(int(w * scale), 1), max(int(h * scale), 1))
                raw_work = cv2.resize(raw_work, size, interpolation=cv2.INTER_AREA)

            edited_img, final_sliders, inference_info = run_no_reference_policy(
                model,
                vec_env,
                raw_work,
                vgg_cache[image_id],
                action_step=float(metadata["action_step"]),
                max_steps=inference_steps,
                observation_mode=metadata["observation_mode"],
                safety_stop=safety_stop,
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
                slider_limits=metadata.get("inference_slider_limits"),
                return_info=True,
            )

            # Expert C is read only after inference and is used only for reporting.
            expert_path = DEFAULT_DATA_DIR / "c" / image_id
            expert_img = cv2.imread(str(expert_path))
            if expert_img is None:
                raise FileNotFoundError(f"Missing expert image: {expert_path}")
            expert_img = cv2.resize(
                expert_img,
                (raw_work.shape[1], raw_work.shape[0]),
                interpolation=cv2.INTER_AREA,
            )

            initial_score, initial_details = paired_style_score(raw_work, expert_img)
            initial_mse = float(initial_details["lab_mse"])
            final_score, final_details = paired_style_score(edited_img, expert_img)
            final_mse = float(final_details["lab_mse"])
            initial_mses.append(initial_mse)
            final_mses.append(final_mse)
            initial_style_scores.append(initial_score)
            final_style_scores.append(final_score)
            slider_summary = {
                name: round(float(value), 2)
                for name, value in zip(ACTION_NAMES, final_sliders)
                if abs(float(value)) >= 0.05
            }
            print(
                f"[{image_id}] LAB MSE: {initial_mse:.4f} -> {final_mse:.4f} "
                f"| style score: {initial_score:.5f} -> {final_score:.5f} "
                f"| steps: {inference_info['steps_used']} "
                f"({inference_info['stop_reason']}; "
                f"backoffs={inference_info['safety_backoffs']}; "
                f"gate={inference_info['mean_gate_strength']:.2f})"
            )
            print(f"[{image_id}] Final sliders: {slider_summary}")

            axes[row_idx, 0].imshow(cv2.cvtColor(raw_work, cv2.COLOR_BGR2RGB))
            axes[row_idx, 0].set_title("RAW")
            axes[row_idx, 0].set_ylabel(f"Init LAB MSE: {initial_mse:.4f}")
            axes[row_idx, 0].set_xticks([])
            axes[row_idx, 0].set_yticks([])

            axes[row_idx, 1].imshow(cv2.cvtColor(edited_img, cv2.COLOR_BGR2RGB))
            axes[row_idx, 1].set_title("RL Output (No Reference)")
            axes[row_idx, 1].set_xlabel(
                f"Step {inference_info['steps_used']} | LAB MSE: {final_mse:.4f}"
            )
            axes[row_idx, 1].set_xticks([])
            axes[row_idx, 1].set_yticks([])

            axes[row_idx, 2].imshow(cv2.cvtColor(expert_img, cv2.COLOR_BGR2RGB))
            axes[row_idx, 2].set_title("Expert Target (Evaluation Only)")
            axes[row_idx, 2].axis("off")
    finally:
        vec_env.close()

    plt.tight_layout()
    save_path = (RL_DIR / output_name).resolve()
    plt.savefig(save_path, dpi=150)
    plt.close(fig)
    improved = sum(final < initial for initial, final in zip(initial_mses, final_mses))
    style_improved = sum(
        final < initial
        for initial, final in zip(initial_style_scores, final_style_scores)
    )
    print(
        f"Mean LAB MSE: {np.mean(initial_mses):.4f} -> {np.mean(final_mses):.4f} "
        f"| improved {improved}/{len(final_mses)} images"
    )
    print(
        f"Mean style score: {np.mean(initial_style_scores):.5f} -> "
        f"{np.mean(final_style_scores):.5f} "
        f"| improved {style_improved}/{len(final_style_scores)} images"
    )
    print(f"Saved comparison: {save_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=6, help="Number of test images.")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional image sampling seed. Omit it for a different random sample each run.",
    )
    parser.add_argument("--output", default="multi_comparison.jpg", help="Output filename.")
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Optional inference-step override; defaults to model metadata.",
    )
    args = parser.parse_args()
    try:
        main(
            count=args.count,
            seed=args.seed,
            output_name=args.output,
            steps=args.steps,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
