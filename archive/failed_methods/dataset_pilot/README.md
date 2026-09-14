# Dataset Pilot

Small pilot pipeline for reverse-degradation dataset generation.

Source data is expected to be MIT-Adobe FiveK Expert TIFF files.

The generator keeps the original TIFF files outside this repository, then writes
downsampled GT/degraded image pairs plus metadata into this folder.

Default run:

```powershell
python .\dataset_pilot\degradation_generator.py
```

Outputs:

- `selected_sources.jsonl`: the 200 selected Expert C TIFF source paths.
- `gt/`: downsampled GT images converted to sRGB.
- `degraded/`: three degraded variants per GT image.
- `metadata.jsonl`: one record per degraded pair, including degradation ops and target action labels.
- `contact_sheets/`: visual QA sheets for quick inspection.

This first pilot intentionally uses sRGB-level degradation. It is meant to
validate the data pipeline and action-label design before moving to DNG/RAW
exposure simulation.
