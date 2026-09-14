param(
    [string]$BindAddress = "0.0.0.0",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$PythonPath = "",
    [switch]$DisableLlm,
    [switch]$Reload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment missing. Prepare .venv using backend/requirements-mobile.lock.txt or pass -PythonPath."
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
$env:AI_PHOTO_USE_LLM = if ($DisableLlm) { "0" } else { "1" }
if (-not $env:AI_PHOTO_OLLAMA_MODEL) { $env:AI_PHOTO_OLLAMA_MODEL = "llama3.2:3b" }
if (-not $env:AI_PHOTO_OLLAMA_TIMEOUT) { $env:AI_PHOTO_OLLAMA_TIMEOUT = "30" }
$serverArguments = @("-m", "uvicorn", "app.main:app", "--host", $BindAddress, "--port", "$Port", "--workers", "1")
if ($Reload) { $serverArguments += "--reload" }
Write-Host "AI Photo Editor: $BindAddress`:$Port (single worker, local demonstration)"
Write-Host "Python: $resolvedPython"
Write-Host "Phone: enter this computer's LAN IPv4 address in the app's Server connection menu."
Push-Location -LiteralPath $PSScriptRoot
try {
    & $resolvedPython @serverArguments
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
