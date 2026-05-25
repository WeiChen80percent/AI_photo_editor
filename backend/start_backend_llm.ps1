Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$env:AI_PHOTO_USE_LLM = "1"
$env:AI_PHOTO_OLLAMA_MODEL = "llama3.2:3b"
$env:AI_PHOTO_OLLAMA_TIMEOUT = "30"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment python was not found: $python"
}

Write-Host "Starting AI Photo Editor backend with LLM enabled..."
Write-Host "AI_PHOTO_USE_LLM=$env:AI_PHOTO_USE_LLM"
Write-Host "AI_PHOTO_OLLAMA_MODEL=$env:AI_PHOTO_OLLAMA_MODEL"
Write-Host "AI_PHOTO_OLLAMA_TIMEOUT=$env:AI_PHOTO_OLLAMA_TIMEOUT"
Write-Host "Keep Ollama running in another terminal, for example: ollama run llama3.2:3b"

& $python -m uvicorn app.main:app --reload
