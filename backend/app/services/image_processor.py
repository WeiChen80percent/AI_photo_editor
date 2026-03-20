from pathlib import Path

from PIL import Image, ImageEnhance, ImageStat


def create_mock_result(
    original_path: Path,
    reference_path: Path,
    result_path: Path,
) -> None:
    original = Image.open(original_path).convert("RGB")
    reference = Image.open(reference_path).convert("RGB")

    reference_small = reference.resize((50, 50))
    avg_r, avg_g, avg_b = ImageStat.Stat(reference_small).mean

    overlay = Image.new(
        "RGB",
        original.size,
        (int(avg_r), int(avg_g), int(avg_b)),
    )

    blended = Image.blend(original, overlay, alpha=0.12)

    blended = ImageEnhance.Contrast(blended).enhance(1.15)
    blended = ImageEnhance.Color(blended).enhance(1.20)
    blended = ImageEnhance.Brightness(blended).enhance(1.05)

    result_path.parent.mkdir(parents=True, exist_ok=True)
    blended.save(result_path)