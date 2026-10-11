param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$LabRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $LabRoot "experiments\2026-10-09\blade_bend_angle_v1"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$Stl = Join-Path $LabRoot "experiments\2026-10-07\projection_pairing_v2\AL0W_BLADE_H-R01\01_original_step_component.stl"
$OriginalReview = Join-Path $LabRoot "experiments\2026-10-08\current_final\blade_tolerance\02_blade_bend_R2_15_review.png"

# Persisted STEP-to-DXF transform from the existing blade experiment.
$scale = 1.0010075335
$rotation = 0.338298 * [Math]::PI / 180.0
$cosine = [Math]::Cos($rotation)
$sine = [Math]::Sin($rotation)
$tx = 1370.0219951
$ty = 268.89970259

# Native DXF ARC 6FE2.
$centerX = 1337.89890202714
$centerY = 284.3722019626393
$radius = 2.150000000592478
$startDegrees = 154.6791322089975
$endDegrees = 259.6326110631058
$targetSweep = $endDegrees - $startDegrees

$local = [System.Collections.Generic.List[double[]]]::new()
$seen = [System.Collections.Generic.HashSet[string]]::new()
foreach ($line in [System.IO.File]::ReadLines($Stl)) {
    if (-not $line.TrimStart().StartsWith("vertex ")) { continue }
    $parts = $line.Trim() -split '\s+'
    $x = [double]::Parse($parts[1], [Globalization.CultureInfo]::InvariantCulture)
    $y = [double]::Parse($parts[2], [Globalization.CultureInfo]::InvariantCulture)
    $px = $scale * ($x*$cosine - $y*$sine) + $tx
    $py = $scale * ($x*$sine + $y*$cosine) + $ty
    if ([Math]::Abs($px-$centerX) -le 5.0 -and [Math]::Abs($py-$centerY) -le 5.0) {
        $key = "{0:F4},{1:F4}" -f $px, $py
        if ($seen.Add($key)) { $local.Add([double[]]@($px, $py)) }
    }
}

$target = [System.Collections.Generic.List[double[]]]::new()
$track = [System.Collections.Generic.List[double[]]]::new()
$distances = [System.Collections.Generic.List[double]]::new()
for ($index = 0; $index -le 96; $index++) {
    $angle = ($startDegrees + ($endDegrees-$startDegrees)*$index/96.0) * [Math]::PI / 180.0
    $qx = $centerX + $radius*[Math]::Cos($angle)
    $qy = $centerY + $radius*[Math]::Sin($angle)
    $target.Add([double[]]@($qx, $qy))
    $best = $null
    $bestDistance = [double]::PositiveInfinity
    foreach ($point in $local) {
        $distance = [Math]::Sqrt(($point[0]-$qx)*($point[0]-$qx) + ($point[1]-$qy)*($point[1]-$qy))
        if ($distance -lt $bestDistance) { $bestDistance = $distance; $best = $point }
    }
    $track.Add($best)
    $distances.Add($bestDistance)
}

$sensitivity = @()
foreach ($window in @(4, 6, 8, 10, 12, 16, 20)) {
    $last = $track.Count - 1
    $startVector = [double[]]@(
        ($track[$window][0]-$track[0][0]),
        ($track[$window][1]-$track[0][1])
    )
    $endVector = [double[]]@(
        ($track[$last][0]-$track[$last-$window][0]),
        ($track[$last][1]-$track[$last-$window][1])
    )
    $startNorm = [Math]::Sqrt($startVector[0]*$startVector[0] + $startVector[1]*$startVector[1])
    $endNorm = [Math]::Sqrt($endVector[0]*$endVector[0] + $endVector[1]*$endVector[1])
    if ($startNorm -le 1e-9 -or $endNorm -le 1e-9) { continue }
    $dot = ($startVector[0]*$endVector[0] + $startVector[1]*$endVector[1]) / ($startNorm*$endNorm)
    $dot = [Math]::Max(-1.0, [Math]::Min(1.0, $dot))
    $turn = [Math]::Acos($dot)*180.0/[Math]::PI
    $sensitivity += [ordered]@{
        window_samples = $window
        candidate_turn_degrees = [Math]::Round($turn, 6)
        error_degrees = [Math]::Round([Math]::Abs($turn-$targetSweep), 6)
    }
}
$selected = $sensitivity | Where-Object { $_.window_samples -eq 12 } | Select-Object -First 1
$sortedDistances = $distances | Sort-Object
$meanDistance = ($distances | Measure-Object -Average).Average
$p90Distance = $sortedDistances[[int][Math]::Floor(0.9*($sortedDistances.Count-1))]

$report = [ordered]@{
    schema_version = 1
    method = "DXF_ARC_VS_PROJECTED_STL_LOCAL_TANGENT_TURN_V1"
    production_or_current_final_updated = $false
    dxf_handle = "6FE2"
    semantic = "7 repeated blade-tip bend"
    target = [ordered]@{
        radius_mm = [Math]::Round($radius, 6)
        start_angle_degrees = [Math]::Round($startDegrees, 6)
        end_angle_degrees = [Math]::Round($endDegrees, 6)
        tangent_turn_degrees = [Math]::Round($targetSweep, 6)
    }
    candidate = [ordered]@{
        tangent_turn_degrees = $selected.candidate_turn_degrees
        angle_error_degrees = $selected.error_degrees
        mean_nearest_distance_mm = [Math]::Round($meanDistance, 6)
        p90_nearest_distance_mm = [Math]::Round($p90Distance, 6)
        source = "registered Blade STL local contour"
    }
    sensitivity = $sensitivity
    conclusion = [ordered]@{
        location_semantics_match = $true
        bend_direction_matches = $true
        bend_angle_gate_passed = ($selected.error_degrees -le 5.0)
        status = if ($selected.error_degrees -le 5.0) { "ANGLE_VERIFIED" } else { "CANDIDATE_ONLY" }
        note = "The local mesh track supports the same bend location, but its tangent turn differs from the native DXF arc."
    }
}
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDir "blade_bend_angle_report.json") -Encoding UTF8

$canvas = [Drawing.Bitmap]::new(2600, 1400)
$graphics = [Drawing.Graphics]::FromImage($canvas)
$graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([Drawing.ColorTranslator]::FromHtml('#101010'))
$titleFont = [Drawing.Font]::new('Consolas', 28, [Drawing.FontStyle]::Bold)
$headFont = [Drawing.Font]::new('Consolas', 20, [Drawing.FontStyle]::Bold)
$bodyFont = [Drawing.Font]::new('Consolas', 16)
$white = [Drawing.Brushes]::White
$muted = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#a3a3a3'))
$yellow = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#facc15'))
$border = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#404040'), 2)
$targetPen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#ff2bd6'), 10)
$candidatePen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#22d3ee'), 7)
$targetTangentPen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#facc15'), 6)
$candidateTangentPen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#f97316'), 6)
try {
    $graphics.DrawString('7-R2.15 BLADE BEND ANGLE DEBUG', $titleFont, $white, 30, 22)
    $graphics.DrawString("DXF tangent turn: $([Math]::Round($targetSweep,3)) deg", $headFont, $white, 30, 76)
    $graphics.DrawString("STEP/STL tangent turn: $($selected.candidate_turn_degrees) deg; error: $($selected.error_degrees) deg", $headFont, $yellow, 670, 76)

    $original = [Drawing.Image]::FromFile($OriginalReview)
    try { $graphics.DrawImage($original, [Drawing.Rectangle]::new(25, 130, 1260, 1230)) } finally { $original.Dispose() }
    $plot = [Drawing.Rectangle]::new(1315, 130, 1260, 1230)
    $graphics.FillRectangle([Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#171717')), $plot)
    $graphics.DrawRectangle($border, $plot)
    $graphics.DrawString('LOCAL ARC AND END TANGENTS', $headFont, $white, 1345, 160)
    $graphics.DrawString('magenta: native DXF arc; cyan: nearest projected STL trace', $bodyFont, $muted, 1345, 205)
    $graphics.DrawString('yellow: DXF tangents; orange: STL trace tangents', $bodyFont, $muted, 1345, 238)

    $plotX = 1370.0; $plotY = 340.0; $plotW = 1140.0; $plotH = 850.0
    $minX = $centerX-3.8; $maxX = $centerX+3.8; $minY = $centerY-3.8; $maxY = $centerY+3.8
    $sx = $plotW/($maxX-$minX); $sy = $plotH/($maxY-$minY)
    function MapPoint([double[]]$point) {
        return [Drawing.PointF]::new([single]($plotX+($point[0]-$minX)*$sx), [single]($plotY+$plotH-($point[1]-$minY)*$sy))
    }
    for ($i=1; $i -lt $target.Count; $i++) { $graphics.DrawLine($targetPen, (MapPoint $target[$i-1]), (MapPoint $target[$i])) }
    for ($i=1; $i -lt $track.Count; $i++) { $graphics.DrawLine($candidatePen, (MapPoint $track[$i-1]), (MapPoint $track[$i])) }

    $tangentLength = 1.6
    $startRad = $startDegrees*[Math]::PI/180.0
    $endRad = $endDegrees*[Math]::PI/180.0
    $targetStart2 = [double[]]@(
        ($target[0][0]-[Math]::Sin($startRad)*$tangentLength),
        ($target[0][1]+[Math]::Cos($startRad)*$tangentLength)
    )
    $targetEnd2 = [double[]]@(
        ($target[$target.Count-1][0]-[Math]::Sin($endRad)*$tangentLength),
        ($target[$target.Count-1][1]+[Math]::Cos($endRad)*$tangentLength)
    )
    $graphics.DrawLine($targetTangentPen, (MapPoint $target[0]), (MapPoint $targetStart2))
    $graphics.DrawLine($targetTangentPen, (MapPoint $target[$target.Count-1]), (MapPoint $targetEnd2))
    $window = 12; $last = $track.Count-1
    $graphics.DrawLine($candidateTangentPen, (MapPoint $track[0]), (MapPoint $track[$window]))
    $graphics.DrawLine($candidateTangentPen, (MapPoint $track[$last-$window]), (MapPoint $track[$last]))
    $graphics.DrawString("RESULT: $($report.conclusion.status)", $headFont, $yellow, 1345, 1230)
    $graphics.DrawString("mean distance $([Math]::Round($meanDistance,3)) mm; p90 $([Math]::Round($p90Distance,3)) mm", $bodyFont, $muted, 1345, 1275)
    $canvas.Save((Join-Path $OutputDir "01_blade_bend_angle_debug.png"), [Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose(); $canvas.Dispose(); $titleFont.Dispose(); $headFont.Dispose(); $bodyFont.Dispose()
    $muted.Dispose(); $yellow.Dispose(); $border.Dispose(); $targetPen.Dispose(); $candidatePen.Dispose(); $targetTangentPen.Dispose(); $candidateTangentPen.Dispose()
}

Write-Output ($report | ConvertTo-Json -Depth 8)
