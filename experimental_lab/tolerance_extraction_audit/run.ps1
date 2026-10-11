param(
    [string]$Config = (Join-Path $PSScriptRoot "common\config\local.json"),
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$WithBenchmark,
    [switch]$SkipSourceReplay
)

$ErrorActionPreference = "Stop"
if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Date must use yyyy-MM-dd, for example 2026-10-07."
}

$ExperimentRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $ExperimentRoot "..\..")).Path
$Python = Join-Path $ProjectRoot "pyoccenv\python.exe"
$Scripts = Join-Path $ExperimentRoot "common\scripts"
$ProjectionEntryPoint = Join-Path $Scripts "extract_projection_only.py"
$EvidenceEntryPoint = Join-Path $Scripts "generate_raw_tolerance_evidence.py"
$DayRoot = Join-Path $ExperimentRoot "experiments\$Date"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Cannot find pyoccenv Python: $Python"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Cannot find config: $Config"
}

$SourceConfig = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$Model = [string]$SourceConfig.model
if ([string]::IsNullOrWhiteSpace($Model)) {
    throw "Config field 'model' is required."
}

$ResultRoot = Join-Path $DayRoot "results\$Model\latest"
$ResolvedConfigPath = Join-Path $DayRoot "resolved_config.json"
$DailyTemplate = Join-Path $ExperimentRoot "common\templates\DAILY_EXPERIMENT_TEMPLATE.md"
New-Item -ItemType Directory -Path $DayRoot,$ResultRoot -Force | Out-Null

$SourceConfig | Add-Member -NotePropertyName output_dir -NotePropertyValue $ResultRoot -Force
$SourceConfig | Add-Member -NotePropertyName case_database -NotePropertyValue (Join-Path $ExperimentRoot "benchmarks\snapshots\feature_case_base_snapshot.json") -Force
$SourceConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ResolvedConfigPath -Encoding UTF8

$DailyReadme = Join-Path $DayRoot "README.md"
if (-not (Test-Path -LiteralPath $DailyReadme) -and (Test-Path -LiteralPath $DailyTemplate)) {
    (Get-Content -LiteralPath $DailyTemplate -Raw -Encoding UTF8).Replace('{{DATE}}', $Date).Replace('{{MODEL}}', $Model) |
        Set-Content -LiteralPath $DailyReadme -Encoding UTF8
}

$StartedAt = (Get-Date).ToString('o')
Write-Host "Date:            $Date"
Write-Host "Model:           $Model"
Write-Host "Experiment root: $DayRoot"
Write-Host "Python:          $Python"

& $Python $ProjectionEntryPoint --config $ResolvedConfigPath
if ($LASTEXITCODE -ne 0) { throw "Projection experiment failed with exit code $LASTEXITCODE" }

& $Python $EvidenceEntryPoint --config $ResolvedConfigPath
if ($LASTEXITCODE -ne 0) { throw "Evidence audit failed with exit code $LASTEXITCODE" }

if ($WithBenchmark) {
    $BenchmarkScript = Join-Path $ExperimentRoot "run_benchmark.ps1"
    & $BenchmarkScript -Date $Date -SkipSourceReplay:$SkipSourceReplay
    if ($LASTEXITCODE -ne 0) { throw "Benchmark failed with exit code $LASTEXITCODE" }
}

$Summary = [ordered]@{
    date = $Date
    model = $Model
    started_at = $StartedAt
    completed_at = (Get-Date).ToString('o')
    config = $ResolvedConfigPath
    result_root = $ResultRoot
    benchmark_requested = [bool]$WithBenchmark
    status = "COMPLETED"
}
$Summary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $DayRoot "run_summary.json") -Encoding UTF8
Write-Host "Completed. Daily experiment: $DayRoot"
