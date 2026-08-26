[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$Images,

    [string[]]$Labels,

    [string]$Output = "",

    [ValidateSet("auto", "horizontal", "vertical", "grid")]
    [string]$Layout = "auto",

    [ValidateSet("contain", "crop")]
    [string]$Fit = "contain",

    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$hostTrainingRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
    (Join-Path $PSScriptRoot "training\.venv\Scripts\python.exe"),
    (Join-Path $hostTrainingRoot ".venv\Scripts\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
)
$python = $null
foreach ($candidate in $candidates) {
    if ((Test-Path -LiteralPath $candidate -PathType Leaf)) {
        & $candidate -c "from PIL import Image" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    }
}
if (-not $python) {
    throw "No Python installation with Pillow was found."
}

$arguments = @((Join-Path $PSScriptRoot "make_comparison.py")) + $Images
if ($Labels) {
    $arguments += "--labels"
    $arguments += $Labels
}
if (-not [string]::IsNullOrWhiteSpace($Output)) {
    $arguments += @("--output", $Output)
}
$arguments += @("--layout", $Layout, "--fit", $Fit)
if ($Overwrite) {
    $arguments += "--overwrite"
}
& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Comparison generation failed with exit code $LASTEXITCODE"
}
