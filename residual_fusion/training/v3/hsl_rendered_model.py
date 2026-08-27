from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


def _group_count(channels: int) -> int:
    for groups in (8, 4, 2, 1):
        if channels % groups == 0:
            return groups
    return 1


class HSLResidualModel(nn.Module):
    def __init__(
        self,
        *,
        feature_count: int,
        conv_channels: Sequence[int],
        statistics_hidden: int,
        head_hidden: Sequence[int],
        dropout: float,
    ) -> None:
        super().__init__()
        channels = [6, *[int(value) for value in conv_channels]]
        blocks: list[nn.Module] = []
        for input_channels, output_channels in zip(channels[:-1], channels[1:]):
            blocks.extend(
                [
                    nn.Conv2d(
                        input_channels,
                        output_channels,
                        kernel_size=3,
                        stride=2,
                        padding=1,
                        bias=False,
                    ),
                    nn.GroupNorm(_group_count(output_channels), output_channels),
                    nn.SiLU(),
                ]
            )
        self.encoder = nn.Sequential(*blocks)
        self.statistics = nn.Sequential(
            nn.Linear(int(feature_count), int(statistics_hidden)),
            nn.SiLU(),
        )
        hidden_1, hidden_2 = [int(value) for value in head_hidden]
        self.head = nn.Sequential(
            nn.Linear(channels[-1] * 2 + int(statistics_hidden), hidden_1),
            nn.SiLU(),
            nn.Dropout(float(dropout)),
            nn.Linear(hidden_1, hidden_2),
            nn.SiLU(),
            nn.Linear(hidden_2, 18),
        )
        final = self.head[-1]
        if not isinstance(final, nn.Linear):
            raise TypeError("Expected linear HSL output layer")
        nn.init.zeros_(final.weight)
        nn.init.zeros_(final.bias)

    def forward(
        self,
        raw_rgb: torch.Tensor,
        base_rgb: torch.Tensor,
        statistics: torch.Tensor,
    ) -> torch.Tensor:
        encoded = self.encoder(torch.cat([raw_rgb, base_rgb], dim=1))
        image_embedding = torch.cat(
            [
                encoded.mean(dim=(2, 3)),
                encoded.flatten(2).std(dim=2, unbiased=False),
            ],
            dim=1,
        )
        statistics_embedding = self.statistics(statistics)
        return torch.tanh(self.head(torch.cat([image_embedding, statistics_embedding], dim=1)))


def decode_hsl_parameters(
    normalized: torch.Tensor,
    editor: dict[str, Any],
) -> torch.Tensor:
    bounds = torch.as_tensor(
        [float(editor["hue_shift_bound_degrees"])] * 6
        + [float(editor["saturation_log_gain_bound"])] * 6
        + [float(editor["lightness_delta_bound"])] * 6,
        dtype=normalized.dtype,
        device=normalized.device,
    )
    return normalized * bounds.unsqueeze(0)


def rgb_to_hsl(rgb: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    red, green, blue = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    maximum, maximum_index = rgb.max(dim=1)
    minimum = rgb.min(dim=1).values
    delta = maximum - minimum
    lightness = (maximum + minimum) * 0.5
    denominator = torch.clamp(1.0 - torch.abs(2.0 * lightness - 1.0), min=1e-7)
    saturation = torch.where(delta > 1e-7, delta / denominator, torch.zeros_like(delta))
    safe_delta = torch.clamp(delta, min=1e-7)
    red_hue = torch.remainder((green - blue) / safe_delta, 6.0)
    green_hue = (blue - red) / safe_delta + 2.0
    blue_hue = (red - green) / safe_delta + 4.0
    hue_sector = torch.where(
        maximum_index == 0,
        red_hue,
        torch.where(maximum_index == 1, green_hue, blue_hue),
    )
    hue = torch.where(delta > 1e-7, torch.remainder(hue_sector * 60.0, 360.0), torch.zeros_like(delta))
    return hue, lightness, torch.clamp(saturation, 0.0, 1.0)


def hsl_to_rgb(
    hue: torch.Tensor,
    lightness: torch.Tensor,
    saturation: torch.Tensor,
) -> torch.Tensor:
    hue_prime = torch.remainder(hue, 360.0) / 60.0
    chroma = (1.0 - torch.abs(2.0 * lightness - 1.0)) * saturation
    intermediate = chroma * (1.0 - torch.abs(torch.remainder(hue_prime, 2.0) - 1.0))
    zeros = torch.zeros_like(chroma)
    sector = torch.floor(hue_prime).to(torch.int64)
    red = torch.where(
        sector == 0,
        chroma,
        torch.where(
            sector == 1,
            intermediate,
            torch.where(
                (sector == 4) | (sector == 5),
                torch.where(sector == 5, chroma, intermediate),
                zeros,
            ),
        ),
    )
    green = torch.where(
        sector == 0,
        intermediate,
        torch.where(
            (sector == 1) | (sector == 2),
            chroma,
            torch.where(sector == 3, intermediate, zeros),
        ),
    )
    blue = torch.where(
        (sector == 0) | (sector == 1),
        zeros,
        torch.where(
            sector == 2,
            intermediate,
            torch.where(
                (sector == 3) | (sector == 4),
                chroma,
                intermediate,
            ),
        ),
    )
    offset = lightness - chroma * 0.5
    return torch.stack([red + offset, green + offset, blue + offset], dim=1)


def hue_weights(hue: torch.Tensor) -> torch.Tensor:
    centers = torch.arange(6, dtype=hue.dtype, device=hue.device).view(1, 6, 1, 1) * 60.0
    distance = torch.abs(hue.unsqueeze(1) - centers)
    distance = torch.minimum(distance, 360.0 - distance)
    weights = torch.clamp(1.0 - distance / 60.0, min=0.0)
    return weights / torch.clamp(weights.sum(dim=1, keepdim=True), min=1e-7)


def apply_hsl_torch(
    base_rgb: torch.Tensor,
    normalized: torch.Tensor,
    editor: dict[str, Any],
) -> tuple[torch.Tensor, torch.Tensor]:
    parameters = decode_hsl_parameters(normalized, editor)
    hue, lightness, saturation = rgb_to_hsl(base_rgb)
    weights = hue_weights(hue)
    hue_shift = torch.sum(weights * parameters[:, :6].unsqueeze(-1).unsqueeze(-1), dim=1)
    saturation_gain = torch.sum(
        weights * parameters[:, 6:12].unsqueeze(-1).unsqueeze(-1), dim=1
    )
    lightness_delta = torch.sum(
        weights * parameters[:, 12:18].unsqueeze(-1).unsqueeze(-1), dim=1
    )
    adjusted_hue = torch.remainder(hue + hue_shift, 360.0)
    adjusted_saturation = torch.clamp(saturation * torch.exp(saturation_gain), 0.0, 1.0)
    adjusted_lightness = torch.clamp(
        lightness + lightness_delta * (4.0 * lightness * (1.0 - lightness)),
        0.0,
        1.0,
    )
    candidate = hsl_to_rgb(adjusted_hue, adjusted_lightness, adjusted_saturation)
    return torch.clamp(candidate, 0.0, 1.0), parameters


def differentiable_composite(candidate: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    rgb_mae = torch.mean(torch.abs(candidate - target), dim=(1, 2, 3))
    luma_weights = torch.as_tensor(
        [0.2126, 0.7152, 0.0722], dtype=candidate.dtype, device=candidate.device
    ).view(1, 3, 1, 1)
    candidate_luma = torch.sum(candidate * luma_weights, dim=1)
    target_luma = torch.sum(target * luma_weights, dim=1)
    luma_mean = torch.abs(candidate_luma.mean(dim=(1, 2)) - target_luma.mean(dim=(1, 2)))
    luma_std = torch.abs(
        candidate_luma.std(dim=(1, 2), unbiased=False)
        - target_luma.std(dim=(1, 2), unbiased=False)
    )
    candidate_max = candidate.max(dim=1).values
    candidate_min = candidate.min(dim=1).values
    target_max = target.max(dim=1).values
    target_min = target.min(dim=1).values
    candidate_saturation = (candidate_max - candidate_min) / torch.clamp(candidate_max, min=1e-6)
    target_saturation = (target_max - target_min) / torch.clamp(target_max, min=1e-6)
    saturation_mean = torch.abs(
        candidate_saturation.mean(dim=(1, 2)) - target_saturation.mean(dim=(1, 2))
    )
    soft_clip = (
        torch.sigmoid((0.005 - candidate) * 80.0)
        + torch.sigmoid((candidate - 0.995) * 80.0)
    ).mean(dim=(1, 2, 3))
    return rgb_mae + 0.25 * luma_mean + 0.15 * luma_std + 0.10 * saturation_mean + 0.05 * soft_clip


def new_model(freeze: dict[str, Any], device: torch.device) -> HSLResidualModel:
    model = freeze["model"]
    return HSLResidualModel(
        feature_count=int(freeze["data"]["feature_count"]),
        conv_channels=model["conv_channels"],
        statistics_hidden=int(model["statistics_hidden"]),
        head_hidden=model["head_hidden"],
        dropout=float(model["dropout"]),
    ).to(device)


def train_model(
    *,
    raw_tensor: torch.Tensor,
    base_tensor: torch.Tensor,
    target_tensor: torch.Tensor,
    target_parameter_tensor: torch.Tensor | None,
    feature_tensor: torch.Tensor,
    sample_weights: torch.Tensor,
    train_indices: np.ndarray,
    freeze: dict[str, Any],
    device: torch.device,
    seed: int,
    label: str,
) -> tuple[HSLResidualModel, dict[str, Any]]:
    torch.manual_seed(seed)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed)
    model = new_model(freeze, device)
    config = freeze["training"]
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config["learning_rate"]),
        weight_decay=float(config["weight_decay"]),
    )
    epochs = int(config["epochs"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    index_tensor = torch.as_tensor(train_indices, dtype=torch.long, device=device)
    history = []
    model.train()
    for epoch in range(epochs):
        permutation = index_tensor[torch.randperm(len(index_tensor), device=device)]
        epoch_total = 0.0
        epoch_count = 0
        for start in range(0, len(permutation), int(config["batch_size"])):
            indices = permutation[start : start + int(config["batch_size"])]
            raw = raw_tensor[indices]
            base = base_tensor[indices]
            target = target_tensor[indices]
            features = feature_tensor[indices]
            weights = sample_weights[indices]
            if float(torch.rand((), device=device)) < float(config["horizontal_flip_probability"]):
                raw = torch.flip(raw, dims=(3,))
                base = torch.flip(base, dims=(3,))
                target = torch.flip(target, dims=(3,))
            normalized = model(raw, base, features)
            candidate, _ = apply_hsl_torch(base, normalized, freeze["editor"])
            candidate_loss = differentiable_composite(candidate, target)
            with torch.no_grad():
                base_loss = differentiable_composite(base, target)
            weighted_candidate = torch.sum(candidate_loss * weights) / torch.sum(weights)
            weighted_hinge = torch.sum(torch.relu(candidate_loss - base_loss) * weights) / torch.sum(weights)
            grouped = normalized.reshape(-1, 3, 6)
            smoothness = (grouped - torch.roll(grouped, shifts=1, dims=2)).square().mean()
            parameter_l2 = normalized.square().mean()
            parameter_supervision = torch.zeros((), dtype=normalized.dtype, device=device)
            parameter_supervision_weight = float(
                config.get("direct_oracle_parameter_loss_weight", 0.0)
            )
            if parameter_supervision_weight > 0.0:
                if target_parameter_tensor is None:
                    raise RuntimeError("Direct parameter supervision requires projected targets")
                per_sample_parameter_loss = F.smooth_l1_loss(
                    normalized,
                    target_parameter_tensor[indices],
                    reduction="none",
                    beta=float(config.get("direct_oracle_parameter_smooth_l1_beta", 0.1)),
                ).mean(dim=1)
                parameter_supervision = (
                    torch.sum(per_sample_parameter_loss * weights) / torch.sum(weights)
                )
            loss = (
                weighted_candidate
                + float(config["no_worse_hinge_weight"]) * weighted_hinge
                + float(config["parameter_l2_weight"]) * parameter_l2
                + float(config["circular_smoothness_weight"]) * smoothness
                + parameter_supervision_weight * parameter_supervision
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
            epoch_total += float(loss.detach()) * len(indices)
            epoch_count += len(indices)
        scheduler.step()
        epoch_loss = epoch_total / max(epoch_count, 1)
        history.append(round(epoch_loss, 8))
        if epoch == 0 or (epoch + 1) % 10 == 0 or epoch + 1 == epochs:
            print(f"{label} epoch={epoch + 1}/{epochs} loss={epoch_loss:.6f}", flush=True)
    return model.eval(), {
        "initial_loss": history[0],
        "final_loss": history[-1],
        "minimum_loss": min(history),
        "epoch_losses": history,
    }


def predict(
    model: HSLResidualModel,
    *,
    raw_tensor: torch.Tensor,
    base_tensor: torch.Tensor,
    feature_tensor: torch.Tensor,
    indices: np.ndarray,
    batch_size: int,
) -> np.ndarray:
    outputs = []
    index_tensor = torch.as_tensor(indices, dtype=torch.long, device=raw_tensor.device)
    with torch.no_grad():
        for start in range(0, len(index_tensor), batch_size):
            batch = index_tensor[start : start + batch_size]
            outputs.append(model(raw_tensor[batch], base_tensor[batch], feature_tensor[batch]).cpu())
    return torch.cat(outputs, dim=0).numpy().astype(np.float32)
