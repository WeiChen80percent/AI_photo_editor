import argparse
import os
import pickle
import random

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import cv2
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from tqdm import tqdm

from paths import DEFAULT_DATA_DIR, resolve_rl_file
from rl_features import VGG_DIM


DEFAULT_BATCH_SIZE = 16
DEFAULT_SEED = 42


def read_split(filename):
    path = resolve_rl_file(filename)
    if not path.exists():
        raise FileNotFoundError(f"Missing split file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        keys = [line.strip() for line in f if line.strip()]
    if not keys:
        raise ValueError(f"No image keys found in {path}.")
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate image keys found in {path}.")
    return keys


def load_image_keys():
    train_keys = read_split("train_keys.txt")
    test_keys = read_split("test_keys.txt")
    overlap = set(train_keys) & set(test_keys)
    if overlap:
        raise ValueError(f"Train/test split overlap contains {len(overlap)} images.")
    return train_keys + test_keys


def resolve_device(requested):
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is False.")
    return torch.device(requested)


def set_deterministic_seed(seed):
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def cache_is_complete(cache_path, image_keys):
    if not cache_path.exists():
        return False
    try:
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError):
        return False
    return all(
        image_id in cache and np.asarray(cache[image_id]).size == VGG_DIM
        for image_id in image_keys
    )


def build_vgg_model(device):
    model = models.vgg16(weights=models.VGG16_Weights.IMAGENET1K_V1).to(device)
    model.classifier = torch.nn.Sequential(*list(model.classifier.children())[:2])
    model.eval()
    return model


def build_transform():
    return transforms.Compose(
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


def extract_batch(model, device, batch_ids, batch_tensors, output):
    if not batch_ids:
        return
    tensor_batch = torch.stack(batch_tensors, dim=0).to(device)
    with torch.inference_mode():
        features = model(tensor_batch).cpu().numpy().astype(np.float32)

    for image_id, feature in zip(batch_ids, features):
        feature = feature.flatten()
        if feature.shape != (VGG_DIM,):
            raise ValueError(
                f"VGG feature for {image_id!r} has shape {feature.shape}; expected {(VGG_DIM,)}."
            )
        output[image_id] = feature / (np.linalg.norm(feature) + 1e-8)


def precompute(batch_size=DEFAULT_BATCH_SIZE, device_name="auto", seed=DEFAULT_SEED, force=False):
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")

    image_keys = load_image_keys()
    cache_path = resolve_rl_file("vgg_cache_5000.pkl")
    if not force and cache_is_complete(cache_path, image_keys):
        print(f"VGG cache is already complete: {cache_path} ({len(image_keys)} images)")
        return

    missing_images = [
        image_id
        for image_id in image_keys
        if not (DEFAULT_DATA_DIR / "raw" / image_id).is_file()
    ]
    if missing_images:
        raise FileNotFoundError(
            f"Missing {len(missing_images)} raw images, including {missing_images[:5]}."
        )

    set_deterministic_seed(seed)
    device = resolve_device(device_name)
    print(
        f"Extracting raw-image VGG16 features for {len(image_keys)} images "
        f"on {device} with batch size {batch_size}."
    )
    model = build_vgg_model(device)
    transform = build_transform()
    output = {}
    batch_ids = []
    batch_tensors = []

    for image_id in tqdm(image_keys, desc="VGG"):
        image = cv2.imread(str(DEFAULT_DATA_DIR / "raw" / image_id))
        if image is None:
            raise FileNotFoundError(
                f"OpenCV could not decode {DEFAULT_DATA_DIR / 'raw' / image_id}."
            )
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        batch_ids.append(image_id)
        batch_tensors.append(transform(image_rgb))

        if len(batch_ids) >= batch_size:
            extract_batch(model, device, batch_ids, batch_tensors, output)
            batch_ids.clear()
            batch_tensors.clear()

    extract_batch(model, device, batch_ids, batch_tensors, output)

    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with open(temporary_path, "wb") as f:
        pickle.dump(output, f, protocol=pickle.HIGHEST_PROTOCOL)
    temporary_path.replace(cache_path)
    print(f"Saved complete raw-image VGG cache: {cache_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true", help="Rebuild even if the cache is complete.")
    args = parser.parse_args()
    try:
        precompute(
            batch_size=args.batch_size,
            device_name=args.device,
            seed=args.seed,
            force=args.force,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None


if __name__ == "__main__":
    main()
