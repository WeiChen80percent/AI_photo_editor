[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputDir = "",

    [switch]$Recursive,

    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$packageRoot = $PSScriptRoot
$packageParent = Split-Path -Parent $packageRoot
$predictor = Join-Path $packageRoot "predict_residual_fusion.py"

if (-not (Test-Path -LiteralPath $InputPath)) {
    throw "Input does not exist: $InputPath"
}

$candidates = @(
    (Join-Path $packageRoot ".venv\Scripts\python.exe"),
    (Join-Path $packageRoot "training\.venv\Scripts\python.exe"),
    (Join-Path $packageParent ".venv\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
)
$python = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
    }
    & $candidate -c "import cv2, joblib, numpy, sklearn, torch, transformers; assert torch.cuda.is_available()" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No CUDA Python environment with the ResidualFusion dependencies was found."
}

$env:AI_PHOTO_SEGMENTATION_DEVICE = "cuda"
$env:AI_PHOTO_SEGMENTATION_HALF = "1"
$env:AI_PHOTO_SEGMENTATION_LOCAL_ONLY = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:AI_PHOTO_V3_1_MAX_SIDE = "2560"
$env:AI_PHOTO_V3_8_STRICT_NOOP = "0"

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $inputItem = Get-Item -LiteralPath $resolvedInput
    $outputName = if ($inputItem.PSIsContainer) {
        $inputItem.Name
    }
    else {
        [System.IO.Path]::GetFileNameWithoutExtension($inputItem.Name)
    }
    $resolvedOutput = Join-Path $packageRoot "outputs\$outputName"
}
elseif ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
}
else {
    $resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $packageRoot $OutputDir))
}

if (-not $Overwrite) {
    $baseOutput = $resolvedOutput
    $suffix = 1
    while (Test-Path -LiteralPath $resolvedOutput) {
        $resolvedOutput = "$baseOutput($suffix)"
        $suffix += 1
    }
}

$arguments = @(
    $predictor,
    "--input", $resolvedInput,
    "--output-dir", $resolvedOutput
)
if ($Recursive) {
    $arguments += "--recursive"
}
if ($Overwrite) {
    $arguments += "--overwrite"
}

Write-Host "Model: ResidualFusion image pipeline"
Write-Host "Pipeline: image analysis -> semantic residual candidate -> risk-aware region fusion"
Write-Host "Python: $python"
Write-Host "Input: $resolvedInput"
Write-Host "Output: $resolvedOutput"

& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "ResidualFusion inference failed with exit code $LASTEXITCODE"
}
