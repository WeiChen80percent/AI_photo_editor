import argparse
from pathlib import Path

import cv2
import numpy as np

from cv2_filters import (
    ACTION_DIM,
    ACTION_NAMES,
    POLICY_IMAGE_MAX_SIDE,
    apply_edits,
)
from model_paths import (
    CURRENT_METADATA_PATH,
    CURRENT_MODEL_PATH,
    CURRENT_VEC_NORMALIZE_PATH,
)
from paths import PROJECT_ROOT, RL_DIR
from rl_features import (
    VGG_DIM,
    ObservationOnlyEnv,
    load_no_reference_metadata,
    run_no_reference_policy,
)


ACTION_INDEX = {name: index for index, name in enumerate(ACTION_NAMES)}
EXPERT_STYLE_KEYWORDS = (
    "變好看",
    "更好看",
    "美化",
    "專家",
    "專業調色",
    "專家調色",
    "調色",
    "風格",
    "電影感",
    "enhance",
    "better",
    "expert",
    "style",
    "color grade",
    "grading",
)


def contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def build_prompt_sliders(prompt, strength=1.0):
    """Translate simple Chinese or English requests into direct slider edits."""
    text = prompt.lower().strip()
    strength = float(np.clip(strength, 0.1, 2.0))
    sliders = np.zeros(ACTION_DIM, dtype=np.float32)
    matched = []

    def adjust(action_name, amount, label):
        sliders[ACTION_INDEX[action_name]] += amount * strength
        matched.append(label)

    shadow_request = contains_any(
        text,
        ("陰影", "暗部", "shadow", "shadows"),
    )
    highlight_request = contains_any(
        text,
        ("高光", "亮部", "highlight", "highlights"),
    )

    if shadow_request:
        if contains_any(
            text,
            ("調亮", "變亮", "提亮", "亮一點", "brighter", "lift", "increase"),
        ):
            adjust("shadows", 0.25, "提亮陰影")
        elif contains_any(
            text,
            ("調暗", "變暗", "壓暗", "暗一點", "darker", "decrease"),
        ):
            adjust("shadows", -0.24, "壓暗陰影")

    if highlight_request:
        if contains_any(
            text,
            ("降低", "壓低", "減少", "恢復", "recover", "decrease", "lower"),
        ):
            adjust("highlights", -0.25, "降低高光")
        elif contains_any(
            text,
            ("提高", "增加", "提亮", "brighter", "increase", "raise"),
        ):
            adjust("highlights", 0.22, "提高高光")

    regional_tone_request = shadow_request or highlight_request
    if not regional_tone_request and contains_any(
        text,
        (
            "變亮",
            "調亮",
            "提亮",
            "亮一點",
            "提高亮度",
            "增加亮度",
            "提高曝光",
            "增加曝光",
            "brighter",
            "brighten",
            "lighten",
            "increase exposure",
        ),
    ):
        adjust("exposure", 0.28, "提高曝光")
    elif not regional_tone_request and contains_any(
        text,
        (
            "變暗",
            "調暗",
            "壓暗",
            "暗一點",
            "降低亮度",
            "減少亮度",
            "降低曝光",
            "減少曝光",
            "darker",
            "darken",
            "decrease exposure",
        ),
    ):
        adjust("exposure", -0.28, "降低曝光")

    if contains_any(
        text,
        ("降低對比", "減少對比", "對比低一點", "decrease contrast", "less contrast"),
    ):
        adjust("contrast", -0.25, "降低對比")
    elif contains_any(
        text,
        ("提高對比", "增加對比", "對比高一點", "increase contrast", "more contrast"),
    ):
        adjust("contrast", 0.25, "提高對比")

    color_saturation_matched = False
    color_rules = (
        (
            "orange_saturation",
            ("增加橘色", "提高橘色", "橙色多一點", "more orange"),
            ("減少橘色", "降低橘色", "橙色少一點", "less orange"),
            "橘色",
        ),
        (
            "green_saturation",
            ("增加綠色", "提高綠色", "綠色多一點", "more green"),
            ("減少綠色", "降低綠色", "綠色少一點", "less green"),
            "綠色",
        ),
        (
            "blue_saturation",
            ("增加藍色", "提高藍色", "藍色多一點", "more blue"),
            ("減少藍色", "降低藍色", "藍色少一點", "less blue"),
            "藍色",
        ),
    )
    for action_name, positive, negative, label in color_rules:
        if contains_any(text, negative):
            adjust(action_name, -0.28, f"減少{label}")
            color_saturation_matched = True
        elif contains_any(text, positive):
            adjust(action_name, 0.28, f"增加{label}")
            color_saturation_matched = True

    if not color_saturation_matched:
        if contains_any(
            text,
            (
                "降低飽和",
                "減少飽和",
                "顏色淡一點",
                "decrease saturation",
                "desaturate",
                "less colorful",
            ),
        ):
            adjust("saturation", -0.25, "降低飽和度")
        elif contains_any(
            text,
            (
                "提高飽和",
                "增加飽和",
                "顏色鮮豔",
                "更鮮豔",
                "increase saturation",
                "more colorful",
            ),
        ):
            adjust("saturation", 0.25, "提高飽和度")

    if contains_any(
        text,
        ("柔和", "柔一點", "降低銳利", "減少銳利", "softer", "less sharp"),
    ):
        adjust("sharpness", -0.22, "降低銳利度")
    elif contains_any(
        text,
        ("銳利", "清晰一點", "增加細節", "sharp", "sharpen"),
    ):
        adjust("sharpness", 0.28, "提高銳利度")

    if contains_any(
        text,
        ("冷一點", "偏冷", "冷色調", "降低色溫", "cooler", "cool tone"),
    ):
        adjust("temperature", -0.28, "降低色溫")
    elif contains_any(
        text,
        ("暖一點", "偏暖", "暖色調", "提高色溫", "warmer", "warm tone"),
    ):
        adjust("temperature", 0.28, "提高色溫")

    if contains_any(text, ("偏綠", "增加綠色調", "green tint")):
        adjust("tint", -0.20, "偏綠色調")
    elif contains_any(text, ("偏洋紅", "偏紅紫", "magenta tint")):
        adjust("tint", 0.20, "偏洋紅色調")

    if contains_any(
        text,
        ("降低自然飽和", "減少自然飽和", "less vibrance"),
    ):
        adjust("vibrance", -0.25, "降低自然飽和度")
    elif contains_any(
        text,
        ("提高自然飽和", "增加自然飽和", "more vibrance"),
    ):
        adjust("vibrance", 0.25, "提高自然飽和度")

    if contains_any(
        text,
        ("降低清晰度", "減少局部對比", "less clarity", "less local contrast"),
    ):
        adjust("local_contrast", -0.25, "降低局部對比")
    elif contains_any(
        text,
        ("提高清晰度", "增加局部對比", "more clarity", "local contrast"),
    ):
        adjust("local_contrast", 0.25, "提高局部對比")

    if contains_any(
        text,
        ("加深黑色", "黑色更深", "增加黑點", "deeper blacks", "black point"),
    ):
        adjust("black_point", 0.20, "加深黑色")
    if contains_any(
        text,
        ("提亮白色", "白色更亮", "提高白點", "brighter whites", "white point"),
    ):
        adjust("white_point", 0.20, "提亮白色")

    return np.clip(sliders, -1.0, 1.0), matched


def infer_edit_mode(prompt, requested_mode, has_manual_adjustment):
    if requested_mode != "auto":
        return requested_mode

    wants_expert = contains_any(prompt.lower(), EXPERT_STYLE_KEYWORDS)
    if wants_expert and has_manual_adjustment:
        return "expert_manual"
    if has_manual_adjustment:
        return "manual"
    return "expert"


def resolve_image_path(path_value):
    path = Path(path_value)
    if path.is_absolute() and path.exists():
        return path

    for candidate in (Path.cwd() / path, RL_DIR / path, PROJECT_ROOT / path):
        if candidate.exists():
            return candidate.resolve()
    return (Path.cwd() / path).resolve()


def resize_max_side(img, max_side):
    if max_side <= 0 or max(img.shape[:2]) <= max_side:
        return img

    height, width = img.shape[:2]
    scale = max_side / max(height, width)
    size = (max(int(width * scale), 1), max(int(height * scale), 1))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def extract_vgg_feature(img, use_vgg=True):
    if not use_vgg:
        print("Warning: zero VGG features do not match the training distribution.")
        return np.zeros(VGG_DIM, dtype=np.float32)

    import torch
    import torchvision.models as models
    import torchvision.transforms as transforms

    try:
        model = models.vgg16(weights=models.VGG16_Weights.IMAGENET1K_V1)
        model.classifier = torch.nn.Sequential(
            *list(model.classifier.children())[:2]
        )
        model.eval()
    except Exception as exc:
        raise RuntimeError(
            "Could not load VGG16 weights. Use --no-vgg only if you accept "
            "distribution-mismatched inference."
        ) from exc

    transform = transforms.Compose(
        [
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )
    image_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    tensor = transform(image_rgb).unsqueeze(0)
    with torch.inference_mode():
        feature = model(tensor).cpu().numpy().flatten().astype(np.float32)
    return feature / (np.linalg.norm(feature) + 1e-8)


def load_no_reference_bundle():
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

    from train_cv2 import (  # noqa: F401
        GeneralizedSpatialFeatureExtractor,
        MultiModalFeatureExtractor,
        SpatialMultiModalFeatureExtractor,
    )

    model_zip = CURRENT_MODEL_PATH.with_suffix(".zip")
    if not model_zip.exists():
        raise FileNotFoundError(
            f"Missing current model: {model_zip}. Run train_cv2.py first."
        )
    if not CURRENT_VEC_NORMALIZE_PATH.exists():
        raise FileNotFoundError(
            "Missing VecNormalize stats: "
            f"{CURRENT_VEC_NORMALIZE_PATH}. Run train_cv2.py first."
        )

    metadata = load_no_reference_metadata(CURRENT_METADATA_PATH)
    model = PPO.load(str(CURRENT_MODEL_PATH), device="cpu")
    observation_dim = int(metadata["observation_dim"])
    policy_action_dim = int(metadata.get("policy_action_dim", ACTION_DIM))
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


def run_model_edit(raw_img, use_vgg=True, vgg_source_img=None, max_steps=None):
    if vgg_source_img is None:
        vgg_source_img = raw_img
    model, vec_env, metadata = load_no_reference_bundle()
    try:
        vgg_feature = extract_vgg_feature(vgg_source_img, use_vgg=use_vgg)
        inference_steps = int(
            metadata["max_edit_steps"] if max_steps is None else max_steps
        )
        if inference_steps < 1:
            raise ValueError("Model inference steps must be at least 1.")
        _, sliders, inference_info = run_no_reference_policy(
            model,
            vec_env,
            raw_img,
            vgg_feature,
            action_step=float(metadata["action_step"]),
            max_steps=inference_steps,
            observation_mode=metadata["observation_mode"],
            safety_stop=bool(metadata.get("inference_safety_stop", False)),
            min_steps=int(metadata.get("inference_min_steps", 3)),
            safety_limits=metadata.get("inference_safety_limits"),
            safety_backoff=bool(
                metadata.get("inference_safety_backoff", False)
            ),
            edit_gate_enabled=bool(metadata.get("edit_gate_enabled", False)),
            edit_gate_stop_threshold=float(
                metadata.get("edit_gate_stop_threshold", 0.12)
            ),
            slider_limits=metadata.get("inference_slider_limits"),
            return_info=True,
        )
    finally:
        vec_env.close()
    return sliders, metadata, inference_info


def save_comparison(raw_img, edited_img, output_path, title):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(1, 2, figsize=(12, 6))
    axes[0].imshow(cv2.cvtColor(raw_img, cv2.COLOR_BGR2RGB))
    axes[0].set_title("Input")
    axes[0].axis("off")
    axes[1].imshow(cv2.cvtColor(edited_img, cv2.COLOR_BGR2RGB))
    axes[1].set_title(title)
    axes[1].axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Edit one image with direct prompt controls, the trained "
            "no-reference model, or both."
        )
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Input image path. If omitted, asks interactively.",
    )
    parser.add_argument("--output", default=None, help="Edited output path.")
    parser.add_argument(
        "--comparison-output",
        default=None,
        help="Before/after comparison image path.",
    )
    parser.add_argument(
        "--prompt",
        default="",
        help="For example: 把照片變好看、調亮一點、專家調色並提高飽和度.",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "manual", "expert", "expert_manual"),
        default="auto",
        help="Auto selects direct controls, trained grading, or both.",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=1.0,
        help="Strength of direct prompt controls.",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Optional model step override; defaults to model metadata.",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=POLICY_IMAGE_MAX_SIDE,
        help="Policy working-image max side; the final edit remains full resolution.",
    )
    vgg_group = parser.add_mutually_exclusive_group()
    vgg_group.add_argument(
        "--use-vgg",
        dest="use_vgg",
        action="store_true",
        help="Use raw-image VGG16 features (default).",
    )
    vgg_group.add_argument(
        "--no-vgg",
        dest="use_vgg",
        action="store_false",
        help="Use zero VGG features for faster but mismatched inference.",
    )
    parser.set_defaults(use_vgg=True)
    args = parser.parse_args()

    if args.input is None:
        args.input = input("Input image path: ").strip()
    if not args.input:
        raise ValueError("Input image path is required.")
    if not args.prompt and args.mode == "auto":
        args.prompt = input(
            "Prompt (Enter for trained Expert C color grading): "
        ).strip()

    input_path = resolve_image_path(args.input)
    raw_full = cv2.imread(str(input_path))
    if raw_full is None:
        raise FileNotFoundError(f"Could not read input image: {input_path}")

    prompt = args.prompt.strip()
    prompt_sliders, matched_adjustments = build_prompt_sliders(
        prompt,
        strength=args.strength,
    )
    has_manual_adjustment = bool(matched_adjustments)
    edit_mode = infer_edit_mode(prompt, args.mode, has_manual_adjustment)
    metadata = None
    inference_info = None

    if edit_mode == "manual":
        if not has_manual_adjustment:
            raise ValueError(
                "Manual mode did not recognize a parameter request. "
                "Try: 調亮一點、降低對比、提高飽和度、暖一點."
            )
        sliders = prompt_sliders
        title = "Prompt Parameter Output"
        suffix = "manual"
        print(
            "Mode: manual parameter adjustment "
            f"({', '.join(matched_adjustments)})"
        )
    else:
        raw_work = resize_max_side(raw_full, args.max_side)
        sliders, metadata, inference_info = run_model_edit(
            raw_work,
            use_vgg=args.use_vgg,
            vgg_source_img=raw_full,
            max_steps=args.steps,
        )
        title = "RL Output (No Reference)"
        suffix = "rl"
        print("Mode: no-reference RL Expert C color grading")

        if edit_mode == "expert_manual":
            limits = np.asarray(
                metadata.get(
                    "inference_slider_limits",
                    np.ones(ACTION_DIM, dtype=np.float32),
                ),
                dtype=np.float32,
            )
            sliders = np.clip(
                sliders + prompt_sliders * 0.55,
                -limits,
                limits,
            )
            title = "RL + Prompt Parameter Output"
            suffix = "rl_manual"
            print(
                f"Applied prompt adjustments: {', '.join(matched_adjustments)}"
            )

    edited_full = apply_edits(raw_full.copy(), sliders)
    output_path = (
        Path(args.output)
        if args.output
        else RL_DIR / f"{input_path.stem}_{suffix}_edited.png"
    )
    comparison_path = (
        Path(args.comparison_output)
        if args.comparison_output
        else RL_DIR / f"{input_path.stem}_{suffix}_comparison.png"
    )
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    if not comparison_path.is_absolute():
        comparison_path = (Path.cwd() / comparison_path).resolve()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    comparison_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), edited_full):
        raise RuntimeError(f"Could not save edited image: {output_path}")
    save_comparison(raw_full, edited_full, comparison_path, title)

    slider_summary = {
        name: round(float(value), 2)
        for name, value in zip(ACTION_NAMES, sliders)
        if abs(float(value)) >= 0.05
    }
    print(f"Input: {input_path}")
    if prompt:
        print(f"Prompt: {prompt}")
    if metadata is not None:
        print(
            f"Policy steps: {inference_info['steps_used']} "
            f"({inference_info['stop_reason']}; no Expert image used)"
        )
        if inference_info.get("edit_gate_enabled"):
            print(
                "Edit confidence: "
                f"{inference_info['mean_gate_strength']:.3f}"
            )
    print(f"Non-trivial sliders: {slider_summary}")
    print(f"Saved edited image: {output_path}")
    print(f"Saved comparison: {comparison_path}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
