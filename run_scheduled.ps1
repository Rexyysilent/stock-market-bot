param(
    [switch]$ValidateOnly,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "logs"
$stateDir = Join-Path $projectRoot "state"
$lockPath = Join-Path $stateDir "scheduled-run.lock"
$lastRunPath = Join-Path $stateDir "last_scheduled_run_utc.txt"
New-Item -ItemType Directory -Force -Path $logDir, $stateDir | Out-Null

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python runtime not found: $pythonPath"
}

Set-Location -LiteralPath $projectRoot

if ($ValidateOnly) {
    & $pythonPath -m ledger --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Signal Ledger CLI validation failed" }
    Write-Output "Scheduler launcher validated: $pythonPath"
    exit 0
}

$utcNow = [DateTime]::UtcNow
$cutoff = [TimeSpan]::FromHours(21.5)
if (-not $Force -and $utcNow.TimeOfDay -lt $cutoff) {
    exit 0
}

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch {
    exit 0
}

try {
    $utcDate = $utcNow.ToString("yyyy-MM-dd")
    if (-not $Force -and (Test-Path -LiteralPath $lastRunPath)) {
        $lastRun = (Get-Content -Raw -LiteralPath $lastRunPath).Trim()
        if ($lastRun -eq $utcDate) { exit 0 }
    }

    $logPath = Join-Path $logDir ("scheduled-{0}.log" -f $utcDate)
    & $pythonPath export_for_gemini.py *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw "Daily export failed; see $logPath" }
    & $pythonPath -m ledger update *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw "Signal Ledger update failed; see $logPath" }
    Set-Content -LiteralPath $lastRunPath -Value $utcDate -NoNewline
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
