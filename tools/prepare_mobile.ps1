# Dependency and generated-file preparation only; no app build or tests.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$frontendPath = Join-Path $repositoryRoot "mobile_app"
$flutterCommand = (Get-Command flutter -ErrorAction Stop).Source
$flutterRoot = Split-Path -Parent (Split-Path -Parent $flutterCommand)
$wrapperRoot = Join-Path $flutterRoot "bin\cache\artifacts\gradle_wrapper"
Push-Location -LiteralPath $frontendPath
try {
    & $flutterCommand pub get
    if ($LASTEXITCODE -ne 0) { throw "Flutter dependency preparation failed." }
    & $flutterCommand gen-l10n
    if ($LASTEXITCODE -ne 0) { throw "Localization generation failed." }
    foreach ($relative in @("gradlew", "gradlew.bat", "gradle\wrapper\gradle-wrapper.jar")) {
        $source = Join-Path $wrapperRoot $relative
        $target = Join-Path (Join-Path $frontendPath "android") $relative
        if (-not (Test-Path -LiteralPath $target)) {
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
                throw "Flutter's cached Gradle wrapper is missing: $source. Prepare Android artifacts with flutter precache --android."
            }
            Copy-Item -LiteralPath $source -Destination $target
        }
    }
} finally {
    Pop-Location
}
