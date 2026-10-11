param(
    [int]$Workers = 4,
    [int]$ModelTimeoutSeconds = 900,
    [int]$StageTimeoutSeconds = 43200,
    [string]$SpecializedReport = "",
    [string]$SpecializedConfig = "",
    [switch]$DryRun,
    [switch]$AllowPartial
)

$ErrorActionPreference = "Stop"
$LabRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path (Join-Path $LabRoot "..\..")).Path
$Python = Join-Path $ProjectRoot "pyoccenv\python.exe"
$Runner = Join-Path $LabRoot "common\scripts\rebuild_and_deploy_tolerance_case_base.py"
$Config = Join-Path $LabRoot "CURRENT_FINAL\database_full_extract_config.json"
if (-not $SpecializedConfig) {
    $SpecializedConfig = Join-Path $LabRoot "CURRENT_FINAL\assembly_all_tolerances_config.json"
}
if (-not $SpecializedReport) {
    $LatestSpecialized = Get-ChildItem -LiteralPath (Join-Path $LabRoot "experiments") `
        -Filter "assembly_all_tolerances_report.json" -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($LatestSpecialized) {
        $SpecializedReport = $LatestSpecialized.FullName
    }
}

$Arguments = @(
    $Runner,
    "--config", $Config,
    "--workers", $Workers,
    "--model-timeout-seconds", $ModelTimeoutSeconds,
    "--stage-timeout-seconds", $StageTimeoutSeconds
)

if ((Test-Path -LiteralPath $SpecializedReport) -and (Test-Path -LiteralPath $SpecializedConfig)) {
    $Arguments += @("--specialized-report", $SpecializedReport, "--specialized-config", $SpecializedConfig)
}
if ($DryRun) {
    $Arguments += "--dry-run"
}
if ($AllowPartial) {
    $Arguments += "--allow-partial"
}

& $Python @Arguments
exit $LASTEXITCODE
