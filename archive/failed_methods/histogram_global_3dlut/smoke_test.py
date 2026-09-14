"""One CUDA/CPU optimization step for the V2 global histogram model."""

from __future__ import annotations

import torch

from .losses import GlobalHistogramLoss
from .model import HistogramGlobal3DLUT


def main() -> None:
    torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = HistogramGlobal3DLUT()
    model.initialize_from_baseline("image_adaptive_3dlut/trained_model/best.pt")
    model.set_training_stage(head_only=True)
    model = model.to(device).train()
    model.classifier.eval()
    source, target = torch.rand(1, 3, 96, 128, device=device), torch.rand(1, 3, 96, 128, device=device)
    prediction, auxiliary = model(source)
    criterion = GlobalHistogramLoss().to(device)
    values = criterion(prediction, target, model.luts, auxiliary)
    optimizer = torch.optim.Adam(model.head_parameters(), lr=1e-4)
    optimizer.zero_grad(set_to_none=True)
    values.total.backward()
    optimizer.step()
    if not torch.isfinite(values.total):
        raise RuntimeError("Non-finite smoke-test loss")
    print(
        f"PASS device={device} params={model.parameter_count} loss={float(values.total):.6f} "
        f"mse={float(values.mse):.6f} strength={float(auxiliary['strength'].mean()):.6f}"
    )


if __name__ == "__main__":
    main()
