param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$SkipSourceReplay,
    [switch]$FailOnGate
)

$ErrorActionPreference = "Stop"
if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw "Date must use yyyy-MM-dd."
}

$ExperimentRoot = (Resolve-Path $PSScriptRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $ExperimentRoot "..\..")).Path
$Python = Join-Path $ProjectRoot "pyoccenv\python.exe"
$Runner = Join-Path $ExperimentRoot "common\scripts\run_tolerance_benchmark.py"
$Summarizer = Join-Path $ExperimentRoot "common\scripts\summarize_benchmark.py"
$Dataset = Join-Path $ExperimentRoot "benchmarks\datasets\silver_verified_cases_snapshot.json"
$CaseDb = Join-Path $ExperimentRoot "benchmarks\snapshots\feature_case_base_snapshot.json"
$OutputDir = Join-Path $ExperimentRoot "experiments\$Date\benchmark"
$Report = Join-Path $OutputDir "tolerance_benchmark.json"
$Baseline = Join-Path $ExperimentRoot "benchmarks\baselines\baseline_2026-10-05.json"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$StartedAt = (Get-Date).ToString('o')

$Arguments = @($Runner, '--mode', 'run', '--case-db', $CaseDb, '--dataset', $Dataset, '--report', $Report)
if ($SkipSourceReplay) { $Arguments += '--skip-source-replay' }
if ($FailOnGate) { $Arguments += '--fail-on-gate' }

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $Python $Summarizer --report $Report --baseline $Baseline --output-dir $OutputDir
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$RepositoryCommit = (& git -C $ProjectRoot rev-parse HEAD 2>$null)
$RepositoryDirty = [bool](& git -C $ProjectRoot status --porcelain 2>$null)
$Manifest = [ordered]@{
    schema_version = "1.0"
    experiment_date = $Date
    started_at = $StartedAt
    completed_at = (Get-Date).ToString('o')
    python = $Python
    repository_commit = [string]$RepositoryCommit
    repository_dirty = $RepositoryDirty
    source_replay = -not [bool]$SkipSourceReplay
    fail_on_gate = [bool]$FailOnGate
    inputs = [ordered]@{
        dataset = [ordered]@{ path = $Dataset; sha256 = (Get-FileHash -LiteralPath $Dataset -Algorithm SHA256).Hash.ToLowerInvariant() }
        case_database = [ordered]@{ path = $CaseDb; sha256 = (Get-FileHash -LiteralPath $CaseDb -Algorithm SHA256).Hash.ToLowerInvariant() }
        baseline = [ordered]@{ path = $Baseline; sha256 = (Get-FileHash -LiteralPath $Baseline -Algorithm SHA256).Hash.ToLowerInvariant() }
        runner = [ordered]@{ path = $Runner; sha256 = (Get-FileHash -LiteralPath $Runner -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    outputs = [ordered]@{
        report = $Report
        summary_json = (Join-Path $OutputDir "benchmark_summary.json")
        summary_markdown = (Join-Path $OutputDir "benchmark_summary.md")
    }
    status = "COMPLETED"
}
$Manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $OutputDir "benchmark_run_manifest.json") -Encoding UTF8
Write-Host "Benchmark report: $Report"
