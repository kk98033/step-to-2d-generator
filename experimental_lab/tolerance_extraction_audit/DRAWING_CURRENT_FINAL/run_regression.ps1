param(
    [switch]$IncludeSmoke,
    [string]$StepPath,
    [string]$SmokeOutput = "scratch/drawing_current_final_smoke"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Python = Join-Path $ProjectRoot "pyoccenv\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Push-Location $ProjectRoot
try {
    & $Python ".\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\validate_manifest.py"
    if ($LASTEXITCODE -ne 0) { throw "Manifest validation failed." }

    & $Python -m unittest discover `
        -s ".\experimental_lab\tolerance_extraction_audit\tests" `
        -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { throw "Drawing experiment regression tests failed." }

    if ($IncludeSmoke) {
        if ([string]::IsNullOrWhiteSpace($StepPath)) {
            throw "-StepPath is required with -IncludeSmoke."
        }
        & $Python ".\experimental_lab\tolerance_extraction_audit\DRAWING_CURRENT_FINAL\experimental_main.py" `
            --step $StepPath `
            --output $SmokeOutput
        if ($LASTEXITCODE -ne 0) { throw "Unknown-model smoke test failed." }
    }
}
finally {
    Pop-Location
}
