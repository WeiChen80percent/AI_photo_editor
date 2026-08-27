[CmdletBinding()]
param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "backend\start_backend_supervised.ps1"
$originalLocation = (Get-Location).Path
try {
    & $launcher -UseExpertCV38 -CheckOnly:$CheckOnly
}
finally {
    Set-Location -LiteralPath $originalLocation
}
