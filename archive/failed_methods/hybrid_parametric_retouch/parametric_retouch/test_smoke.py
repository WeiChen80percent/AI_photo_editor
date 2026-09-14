"""Fast architecture and gradient checks; no dataset files are changed."""

import torch

from .losses import RetouchLoss
from .model import HybridParametricRetouchNet


def main() -> None:
    torch.manual_seed(7)
    model = HybridParametricRetouchNet()
    image = torch.rand(1, 3, 128, 160)
    target = torch.rand_like(image)
    with torch.no_grad():
        identity_output = model(image)
    identity_error = float((identity_output - image).abs().max())
    if identity_error > 2e-4:
        raise AssertionError(f"Identity initialization failed: {identity_error}")
    prediction, aux = model.forward_with_aux(image)
    loss, _ = RetouchLoss()(prediction, target, aux)
    loss.backward()
    gradients = [parameter.grad for parameter in model.parameters() if parameter.grad is not None]
    if not gradients or not all(torch.isfinite(gradient).all() for gradient in gradients):
        raise AssertionError("Missing or non-finite gradients")
    parameter_count = sum(parameter.numel() for parameter in model.parameters())
    print({"identity_max_error": identity_error, "loss": float(loss), "parameters": parameter_count})


if __name__ == "__main__":
    main()
