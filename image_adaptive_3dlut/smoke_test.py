"""Fast CUDA/CPU smoke test for the exact model, loss and optimizer path."""

from __future__ import annotations

import argparse

import torch

from .losses import PairedLUTLoss
from .model import ImageAdaptive3DLUT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    torch.manual_seed(42)
    model = ImageAdaptive3DLUT().to(device)
    criterion = PairedLUTLoss().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)
    source = torch.rand(1, 3, 96, 128, device=device)
    target = torch.rand(1, 3, 96, 128, device=device)
    prediction, weights = model(source)
    losses = criterion(prediction, target, model.luts, weights)
    optimizer.zero_grad(set_to_none=True)
    losses.total.backward()
    optimizer.step()
    if not torch.isfinite(losses.total):
        raise RuntimeError("Non-finite smoke-test loss")
    print(
        f"PASS device={device} params={model.parameter_count} "
        f"loss={float(losses.total):.6f} mse={float(losses.mse):.6f}"
    )


if __name__ == "__main__":
    main()

