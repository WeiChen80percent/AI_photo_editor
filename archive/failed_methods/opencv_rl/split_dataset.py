import argparse
import random

from paths import DEFAULT_DATA_DIR, resolve_rl_file


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def write_split(path, image_ids):
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with open(temporary_path, "w", encoding="utf-8", newline="\n") as f:
        for image_id in image_ids:
            f.write(f"{image_id}\n")
    temporary_path.replace(path)


def split_dataset(test_count=500, seed=42):
    if test_count < 1:
        raise ValueError("test_count must be at least 1.")

    raw_dir = DEFAULT_DATA_DIR / "raw"
    expert_dir = DEFAULT_DATA_DIR / "c"
    all_files = sorted(
        path.name
        for path in raw_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(all_files) <= test_count:
        raise ValueError(
            f"Need more than {test_count} paired images, but found only {len(all_files)}."
        )

    missing_expert = [
        image_id for image_id in all_files if not (expert_dir / image_id).is_file()
    ]
    if missing_expert:
        raise FileNotFoundError(
            f"Missing {len(missing_expert)} Expert C images, including {missing_expert[:5]}."
        )

    random.Random(seed).shuffle(all_files)
    test_files = all_files[:test_count]
    train_files = all_files[test_count:]

    write_split(resolve_rl_file("train_keys.txt"), train_files)
    write_split(resolve_rl_file("test_keys.txt"), test_files)
    print(
        f"Saved deterministic split: {len(train_files)} train / "
        f"{len(test_files)} test images (seed={seed})."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    split_dataset(test_count=args.test_count, seed=args.seed)


if __name__ == "__main__":
    main()
