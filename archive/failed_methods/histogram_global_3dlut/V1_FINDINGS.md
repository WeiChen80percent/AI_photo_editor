# V1 tone-region experiment findings

The deleted `histogram_tone_3dlut` experiment completed 100 epochs on the same
3,935 training and 488 public-development pairs as the baseline.

| Metric | Baseline (epoch 182) | Tone V1 best (epoch 36) |
|---|---:|---:|
| PSNR | 22.33177698 | 22.36999064 |
| SSIM | 0.83549187 | 0.84004846 |
| Delta E | 9.66193279 | 9.57351912 |
| Parameters | 593,516 | 1,295,504 |

Although all three numerical metrics improved slightly, visual inspection
preferred the baseline. The improvement was too small for the 2.18x parameter
increase. The tone gates converged to approximately 0.88 for shadows, 1.09 for
midtones and 0.84 for highlights, which reduced global tonal consistency.

At the best epoch, the weighted SSIM and Lab terms contributed about 0.0309 to
the objective while MSE contributed only 0.0080. The auxiliary losses therefore
dominated optimization. Validation PSNR fell to 22.3190 by epoch 100, indicating
overfitting after epoch 36.

V2 consequently removes regional LUTs, keeps one global three-LUT transform,
reduces auxiliary-loss weights and constrains fine-tuning around the proven
baseline.

