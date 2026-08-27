[CmdletBinding()]
param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
    throw "Flutter is not installed or is not available on PATH."
}
$originalLocation = (Get-Location).Path
try {
    Set-Location -LiteralPath (Join-Path $PSScriptRoot "frontend")
    flutter pub get
    if ($LASTEXITCODE -ne 0) {
        throw "flutter pub get failed."
    }
    if ($CheckOnly) {
        flutter analyze
        if ($LASTEXITCODE -ne 0) {
            throw "flutter analyze failed."
        }
        Write-Host "status=PASS"
        return
    }
    flutter run -d chrome
}
finally {
    Set-Location -LiteralPath $originalLocation
}
