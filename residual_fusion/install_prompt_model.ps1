[CmdletBinding()]
param(
    [string]$GgufPath = "",
    [string]$RepoId = "",
    [string]$Revision = "v1.0.0",
    [string]$Filename = "ai-photo-prompt-control-exp007-bf16.gguf",
    [string]$ModelName = "ai-photo-prompt-control:exp007-v1",
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedSha256 = "7E40DEB0CF81A7D4F3CFFE166BE9664F08CB3CC376150038A1749F065F13B9E0"
$modelsRoot = Join-Path $PSScriptRoot "models"
$defaultGguf = Join-Path $modelsRoot $Filename

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is not installed or is not available on PATH."
}

if ($CheckOnly) {
    $models = & ollama list 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama service is unavailable. Start Ollama and retry."
    }
    if (($models | Out-String) -notmatch [regex]::Escape($ModelName)) {
        throw "Ollama model is missing: $ModelName"
    }
    Write-Host "model=$ModelName"
    Write-Host "status=PASS"
    exit 0
}

if ($GgufPath -and $RepoId) {
    throw "Use either -GgufPath or -RepoId, not both."
}

if ($RepoId) {
    $hfCommand = Get-Command hf -ErrorAction SilentlyContinue
    if (-not $hfCommand) {
        $venvHf = Join-Path $PSScriptRoot ".venv\Scripts\hf.exe"
        if (Test-Path -LiteralPath $venvHf -PathType Leaf) {
            $hfCommand = Get-Item -LiteralPath $venvHf
        }
    }
    if (-not $hfCommand) {
        throw "Hugging Face CLI was not found. Install requirements.txt or run: python -m pip install huggingface-hub"
    }
    & $hfCommand.Source download $RepoId $Filename --revision $Revision --local-dir $modelsRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download $Filename from $RepoId"
    }
    Write-Host "repo=$RepoId"
    Write-Host "revision=$Revision"
    $GgufPath = $defaultGguf
}
elseif (-not $GgufPath) {
    $GgufPath = $defaultGguf
}

if (-not (Test-Path -LiteralPath $GgufPath -PathType Leaf)) {
    throw "GGUF not found: $GgufPath. Pass -GgufPath or -RepoId."
}

$resolvedGguf = (Resolve-Path -LiteralPath $GgufPath).Path
$actualSha256 = (Get-FileHash -LiteralPath $resolvedGguf -Algorithm SHA256).Hash.ToUpperInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "GGUF SHA-256 mismatch. Expected $expectedSha256, got $actualSha256"
}

$ollamaPath = $resolvedGguf.Replace("\", "/")
$runtimeModelfile = Join-Path $modelsRoot "Modelfile.runtime"
$modelfileContent = @"
FROM $ollamaPath
TEMPLATE {{ .Prompt }}
PARAMETER temperature 0
PARAMETER num_predict 96
PARAMETER num_ctx 1024
"@
Set-Content -LiteralPath $runtimeModelfile -Value $modelfileContent -Encoding UTF8

& ollama create $ModelName -f $runtimeModelfile
if ($LASTEXITCODE -ne 0) {
    throw "Ollama failed to create $ModelName"
}

Write-Host "model=$ModelName"
Write-Host "gguf=$resolvedGguf"
Write-Host "sha256=$actualSha256"
Write-Host "status=PASS"
