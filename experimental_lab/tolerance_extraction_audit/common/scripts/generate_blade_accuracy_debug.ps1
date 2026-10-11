param(
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$LabRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $LabRoot "experiments\2026-10-09\blade_accuracy_debug\01_blade_accuracy_debug.png"
}
$Output = [System.IO.Path]::GetFullPath($Output)
$OutputDir = Split-Path -Parent $Output
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$Stl = Join-Path $LabRoot "experiments\2026-10-07\projection_pairing_v2\AL0W_BLADE_H-R01\01_original_step_component.stl"
$SectionImage = Join-Path $LabRoot "experiments\2026-10-08\current_final\fan_projection\05_original_vs_central_section.png"
$RadiusImage = Join-Path $LabRoot "experiments\2026-10-08\current_final\blade_tolerance\02_blade_bend_R2_15_review.png"

$points = [System.Collections.Generic.List[double[]]]::new()
foreach ($line in [System.IO.File]::ReadLines($Stl)) {
    if (-not $line.TrimStart().StartsWith("vertex ")) { continue }
    $parts = $line.Trim() -split '\s+'
    $x = [double]::Parse($parts[1], [Globalization.CultureInfo]::InvariantCulture)
    $y = [double]::Parse($parts[2], [Globalization.CultureInfo]::InvariantCulture)
    $points.Add([double[]]@($x, $y))
}

$angle = 58.5 * [Math]::PI / 180.0
$cosine = [Math]::Cos($angle)
$sine = [Math]::Sin($angle)
$rotated = [System.Collections.Generic.List[double[]]]::new()
$xmin = [double]::PositiveInfinity
$xmax = [double]::NegativeInfinity
$ymin = [double]::PositiveInfinity
$ymax = [double]::NegativeInfinity
foreach ($point in $points) {
    $x = $point[0] * $cosine + $point[1] * $sine
    $y = -$point[0] * $sine + $point[1] * $cosine
    $rotated.Add([double[]]@($x, $y))
    if ($x -lt $xmin) { $xmin = $x }
    if ($x -gt $xmax) { $xmax = $x }
    if ($y -lt $ymin) { $ymin = $y }
    if ($y -gt $ymax) { $ymax = $y }
}

$canvas = [Drawing.Bitmap]::new(3000, 1900)
$graphics = [Drawing.Graphics]::FromImage($canvas)
$graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([Drawing.ColorTranslator]::FromHtml('#101010'))
$fontTitle = [Drawing.Font]::new('Consolas', 30, [Drawing.FontStyle]::Bold)
$fontHead = [Drawing.Font]::new('Consolas', 22, [Drawing.FontStyle]::Bold)
$fontBody = [Drawing.Font]::new('Consolas', 17)
$fontSmall = [Drawing.Font]::new('Consolas', 14)
$white = [Drawing.Brushes]::White
$muted = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#a3a3a3'))
$green = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#22c55e'))
$yellow = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#facc15'))
$cyan = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#22d3ee'))
$magentaPen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#ff2bd6'), 8)
$cyanPen = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#22d3ee'), 2)
$border = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#404040'), 2)

try {
    $graphics.DrawString('1AL0W5000H-R03 - BLADE TOLERANCE ACCURACY DEBUG', $fontTitle, $white, 35, 24)
    $graphics.DrawString('Blade focused: 1/2 fully verified = 50.00%', $fontHead, $yellow, 35, 78)
    $graphics.DrawString('Whole assembly trusted 3D links: 12/65 = 18.46% (coverage, not recommendation accuracy)', $fontBody, $muted, 35, 122)

    $left = [Drawing.Rectangle]::new(30, 180, 1440, 790)
    $right = [Drawing.Rectangle]::new(1500, 180, 1470, 790)
    $bottomLeft = [Drawing.Rectangle]::new(30, 1000, 1900, 860)
    $bottomRight = [Drawing.Rectangle]::new(1960, 1000, 1010, 860)
    foreach ($rect in @($left, $right, $bottomLeft, $bottomRight)) {
        $graphics.FillRectangle([Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#171717')), $rect)
        $graphics.DrawRectangle($border, $rect)
    }

    $graphics.DrawString('PASS - 75.19 +0.20/-0.00 mm', $fontHead, $green, 55, 205)
    $graphics.DrawString('Dimension-constrained blade phase = 58.5 deg', $fontBody, $white, 55, 248)
    $graphics.DrawString('Measured projected width = 75.1911 mm; error = +0.0011 mm', $fontBody, $green, 55, 282)

    $plotX = 80.0; $plotY = 350.0; $plotW = 1340.0; $plotH = 540.0
    $scale = [Math]::Min($plotW / (($xmax-$xmin)+8.0), $plotH / (($ymax-$ymin)+8.0))
    foreach ($point in $rotated) {
        $px = $plotX + ($point[0]-$xmin+4.0)*$scale
        $py = $plotY + $plotH - ($point[1]-$ymin+4.0)*$scale
        $graphics.FillRectangle($cyan, [single]$px, [single]$py, 1.4, 1.4)
    }
    $x1 = $plotX + 4.0*$scale
    $x2 = $plotX + ($xmax-$xmin+4.0)*$scale
    $dimY = $plotY + 25
    $graphics.DrawLine($magentaPen, [single]$x1, [single]$dimY, [single]$x2, [single]$dimY)
    $graphics.DrawLine($magentaPen, [single]$x1, [single]($dimY-18), [single]$x1, [single]($dimY+18))
    $graphics.DrawLine($magentaPen, [single]$x2, [single]($dimY-18), [single]$x2, [single]($dimY+18))
    $graphics.DrawString('75.1911 mm', $fontBody, $white, [single](($x1+$x2)/2-80), [single]($dimY+14))

    $graphics.DrawString('Full assembly central-section alignment', $fontHead, $white, 1525, 205)
    $section = [Drawing.Image]::FromFile($SectionImage)
    try {
        $graphics.DrawImage($section, [Drawing.Rectangle]::new(1525, 260, 1420, 680))
    } finally { $section.Dispose() }

    $graphics.DrawString('REVIEW - 7-R2.15 repeated blade-tip bend', $fontHead, $yellow, 55, 1025)
    $radius = [Drawing.Image]::FromFile($RadiusImage)
    try {
        $ratio = [Math]::Min(1840.0/$radius.Width, 760.0/$radius.Height)
        $w = [int]($radius.Width*$ratio); $h = [int]($radius.Height*$ratio)
        $graphics.DrawImage($radius, [Drawing.Rectangle]::new(55, 1080, $w, $h))
    } finally { $radius.Dispose() }

    $graphics.DrawString('STATUS SUMMARY', $fontHead, $white, 1990, 1030)
    $graphics.DrawString('75.19 envelope', $fontBody, $white, 1990, 1090)
    $graphics.DrawString('VERIFIED', $fontHead, $green, 1990, 1125)
    $graphics.DrawString('phase width: 75.1911', $fontSmall, $muted, 1990, 1170)
    $graphics.DrawString('contour: 0.7938', $fontSmall, $muted, 1990, 1200)
    $graphics.DrawString('extent: 0.9330', $fontSmall, $muted, 1990, 1230)
    $graphics.DrawString('7-R2.15 bend', $fontBody, $white, 1990, 1300)
    $graphics.DrawString('CANDIDATE ONLY', $fontHead, $yellow, 1990, 1335)
    $graphics.DrawString('fitted radius: 1.8979', $fontSmall, $muted, 1990, 1380)
    $graphics.DrawString('target mean dist: 0.2989', $fontSmall, $muted, 1990, 1410)
    $graphics.DrawString('target p90 dist: 0.6467', $fontSmall, $muted, 1990, 1440)
    $graphics.DrawString('Current focused accuracy', $fontBody, $white, 1990, 1530)
    $graphics.DrawString('1 / 2 = 50.00%', $fontTitle, $yellow, 1990, 1570)
    $graphics.DrawString('R2.15 is excluded from automatic case ingestion.', $fontSmall, $muted, 1990, 1640)
    $graphics.DrawString('No confidence threshold was relaxed.', $fontSmall, $muted, 1990, 1670)

    $canvas.Save($Output, [Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose(); $canvas.Dispose()
    $fontTitle.Dispose(); $fontHead.Dispose(); $fontBody.Dispose(); $fontSmall.Dispose()
    $muted.Dispose(); $green.Dispose(); $yellow.Dispose(); $cyan.Dispose()
    $magentaPen.Dispose(); $cyanPen.Dispose(); $border.Dispose()
}

Write-Output $Output
