function Resolve-HistoricalExperiment {
    param([Parameter(Mandatory=$true)][string]$RelativePath)
    $AuditRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
    $Current = Join-Path (Join-Path $AuditRoot "experiments") $RelativePath
    if (Test-Path -LiteralPath $Current) { return $Current }
    $Matches = @(Get-ChildItem -LiteralPath (Join-Path $AuditRoot "ARCHIVE") -Directory | ForEach-Object {
        $Candidate = Join-Path (Join-Path $_.FullName "experiments") $RelativePath
        if (Test-Path -LiteralPath $Candidate) { $Candidate }
    })
    if ($Matches.Count -ne 1) { throw "Expected one historical experiment input: $RelativePath" }
    return $Matches[0]
}
