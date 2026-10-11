param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$SkipGeneralBenchmark
)

$ErrorActionPreference = "Stop"
$LabRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ProjectRoot = (Resolve-Path (Join-Path $LabRoot "..\..")).Path
$Python = Join-Path $ProjectRoot "pyoccenv\python.exe"
$Runner = Join-Path $LabRoot "common\scripts\run_current_final_tolerance_pipeline.py"
$Output = Join-Path $LabRoot "experiments\$Date\current_final"
$Step = Join-Path $ProjectRoot "models\1AL0W5000H-R03.stp"
$Dxf = Join-Path $LabRoot "experiments\2026-10-08\blade_curve_tolerance_v1\source_1AL0W5000H-R03.dxf"
$PoseReport = Join-Path $LabRoot "experiments\2026-10-08\robust_projection_pose_v5\robust_projection_pose_report.json"
$AllToleranceConfig = Join-Path $PSScriptRoot "blade_all_tolerances_config.json"
$AssemblyToleranceConfig = Join-Path $PSScriptRoot "assembly_all_tolerances_config.json"
$AllToleranceOutput = Join-Path $Output "all_tolerances"

& $Python $Runner --step $Step --dxf $Dxf --output-dir $Output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force -Path $AllToleranceOutput | Out-Null

$SemanticRunner = Join-Path $LabRoot "common\scripts\generate_feature_semantic_review.py"
$RadialRunner = Join-Path $LabRoot "common\scripts\run_radial_material_graph_experiment.py"
$ProjectionRunner = Join-Path $LabRoot "common\scripts\run_multi_projection_instance_index.py"
$AssemblyRunner = Join-Path $LabRoot "common\scripts\assembly_feature_resolver.py"
$RotationalEnvelopeRunner = Join-Path $LabRoot "common\scripts\run_rotational_envelope_phase_experiment.py"
$MergeRunner = Join-Path $LabRoot "common\scripts\merge_blade_all_tolerance_results.py"
$AssemblyMergeRunner = Join-Path $LabRoot "common\scripts\merge_full_assembly_tolerance_results.py"
$BaselineOutput = Join-Path $AllToleranceOutput "generic_baseline"
$RadialReport = Join-Path $AllToleranceOutput "radial_material_report.json"
$ProjectionReport = Join-Path $AllToleranceOutput "projection_instances.json"
$AssemblyOutput = Join-Path $Output "assembly_features"
$AssemblyReport = Join-Path $AssemblyOutput "assembly_feature_report.json"
$RotationalEnvelopeOutput = Join-Path $Output "rotational_envelope_7AB9"
$RotationalEnvelopeReport = Join-Path $RotationalEnvelopeOutput "rotational_envelope_report.json"
$MergedReport = Join-Path $AllToleranceOutput "blade_all_tolerances_report.json"
$MergedImage = Join-Path $AllToleranceOutput "blade_all_tolerances_overview.png"
$ShaftOutput = Join-Path $Output "components\2AL0W5010H-R01"
$MotorOutput = Join-Path $Output "components\2AL0W4010H-R01"
$AssemblyReportOutput = Join-Path $Output "assembly_all_tolerances_report.json"
$AssemblyImageOutput = Join-Path $Output "assembly_all_tolerances_overview.png"

& $Python $SemanticRunner --config $AllToleranceConfig --pose-report $PoseReport --component "AL0W_BLADE_H-R01" --output-dir $BaselineOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $RadialRunner --step $Step --dxf $Dxf --component "AL0W_BLADE_H-R01" --handles 7AD9 7ADA 7ADB 7ADC 7ADD --output $RadialReport
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $ProjectionRunner --step $Step --dxf $Dxf --component "AL0W_BLADE_H-R01" --handles 7AD9 7ADA 7ADB 7ADC 7ADD --pose-report $PoseReport --output $ProjectionReport
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $AssemblyRunner --step $Step --dxf $Dxf --baseline (Join-Path $BaselineOutput "feature_semantic_review_report.json") --section-report (Join-Path $Output "fan_projection\fan_half_section_report.json") --current-final (Join-Path $Output "final_tolerance_report.json") --output-dir $AssemblyOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $RotationalEnvelopeRunner --step $Step --dxf $Dxf --section-report (Join-Path $Output "fan_projection\fan_half_section_report.json") --output-dir $RotationalEnvelopeOutput --handle "7AB9" --component "AL0W_BLADE_H-R01"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $MergeRunner --baseline (Join-Path $BaselineOutput "feature_semantic_review_report.json") --radial $RadialReport --projection $ProjectionReport --current-final (Join-Path $Output "final_tolerance_report.json") --assembly $AssemblyReport --rotational-envelope $RotationalEnvelopeReport --output $MergedReport --image $MergedImage
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $SemanticRunner --config $AssemblyToleranceConfig --pose-report $PoseReport --component "2AL0W5010H-R01" --output-dir $ShaftOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $SemanticRunner --config $AssemblyToleranceConfig --pose-report $PoseReport --component "2AL0W4010H-R01" --output-dir $MotorOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $AssemblyMergeRunner --blade $MergedReport --shaft (Join-Path $ShaftOutput "feature_semantic_review_report.json") --motor (Join-Path $MotorOutput "feature_semantic_review_report.json") --output $AssemblyReportOutput --image $AssemblyImageOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipGeneralBenchmark) {
    & (Join-Path $LabRoot "run_benchmark.ps1") -Date $Date -SkipSourceReplay
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Current final output: $Output"
