# Hybrid Parametric Retouching for Expert C

This is an independent alternative to the existing DeepLPF reproduction. It
does not load DeepLPF source files or pretrained weights. The model predicts
interpretable editing parameters: global monotone curves, a global colour
matrix, and smooth local exposure/contrast/saturation maps.

Read [REFERENCES.md](REFERENCES.md) before writing the thesis. This code is a
research prototype, not evidence by itself that the method is novel or better.

## Safety and data split

- Training: `manifests/train.jsonl` only.
- Model selection: `manifests/public_dev.jsonl` only.
- `hidden` is never used by the defaults.
- Images under `data/` are opened read-only and are never overwritten.

## Checks

Run commands from `hybrid_parametric_retouch/`:

```powershell
$files = Get-ChildItem parametric_retouch -Filter *.py | Select-Object -ExpandProperty FullName
..\.venv\Scripts\python.exe -m py_compile $files
..\.venv\Scripts\python.exe -m parametric_retouch.test_smoke
```

One-pair pipeline test:

```powershell
..\.venv\Scripts\python.exe -m parametric_retouch.train `
  --run-name smoke `
  --image-size 256 `
  --train-limit 1 `
  --val-limit 1 `
  --max-steps 1 `
  --cpu
```

## Recommended GTX 1650 training

First run a 300-step preflight:

```powershell
..\.venv\Scripts\python.exe -m parametric_retouch.train `
  --run-name preflight_300 `
  --image-size 512 `
  --train-limit 300 `
  --val-limit 50 `
  --max-steps 300 `
  --no-amp
```

After inspecting `artifacts\preflight_300\previews`, train
the full model from scratch:

```powershell
..\.venv\Scripts\python.exe -m parametric_retouch.train `
  --run-name hybrid_full `
  --image-size 512 `
  --epochs 30 `
  --no-amp
```

Resume with:

```powershell
..\.venv\Scripts\python.exe -m parametric_retouch.train `
  --run-name hybrid_full `
  --resume artifacts\hybrid_full\checkpoints\latest.pt `
  --image-size 512 `
  --epochs 30 `
  --no-amp
```

## Inference

```powershell
..\.venv\Scripts\python.exe -m parametric_retouch.infer `
  --checkpoint artifacts\hybrid_full\checkpoints\best.pt `
  --input path\to\photo.jpg `
  --output outputs\hybrid_result.jpg
```

## Ablation runs

Use identical seed, split, image size and training budget:

```powershell
# No local maps
..\.venv\Scripts\python.exe -m parametric_retouch.train --run-name ablate_local --disable-local --epochs 30 --no-amp

# No monotone curves
..\.venv\Scripts\python.exe -m parametric_retouch.train --run-name ablate_curves --disable-curves --epochs 30 --no-amp

# No colour affine transform
..\.venv\Scripts\python.exe -m parametric_retouch.train --run-name ablate_affine --disable-affine --epochs 30 --no-amp
```

Compare validation loss and, more importantly, fixed RAW/prediction/Expert-C
preview sheets. Do not use the hidden split until the design is frozen.
