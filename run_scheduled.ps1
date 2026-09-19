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

function Invoke-ScheduledPython {
    param(
        [string]$Stage,
        [string[]]$PythonArguments
    )

    $stamp = [DateTime]::UtcNow.ToString("yyyy-MM-dd_HHmmss_fffffff")
    $stdoutPath = Join-Path $logDir ("scheduled-{0}-{1}.stdout.log" -f $stamp, $Stage)
    $stderrPath = Join-Path $logDir ("scheduled-{0}-{1}.stderr.log" -f $stamp, $Stage)
    $summaryPath = Join-Path $logDir ("scheduled-{0}.log" -f [DateTime]::UtcNow.ToString("yyyy-MM-dd"))
    Add-Content -LiteralPath $summaryPath -Encoding UTF8 -Value (
        "Starting {0}; stdout={1}; stderr={2}" -f $Stage, $stdoutPath, $stderrPath
    )

    # Windows PowerShell 5.1 turns native stderr into NativeCommandError under
    # ErrorActionPreference=Stop. Capture it outside PowerShell's error stream;
    # warnings are data, while the actual process exit code determines success.
    $process = Start-Process -FilePath $pythonPath -ArgumentList $PythonArguments `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath `
        -Wait -PassThru
    $exitCode = $process.ExitCode
    Add-Content -LiteralPath $summaryPath -Encoding UTF8 -Value (
        "Finished {0}; exit_code={1}" -f $Stage, $exitCode
    )
    foreach ($capturePath in @($stdoutPath, $stderrPath)) {
        Get-Content -LiteralPath $capturePath | Add-Content -LiteralPath $summaryPath -Encoding UTF8
    }
    if ($null -eq $exitCode -or $exitCode -ne 0) {
        throw "$Stage failed (exit code $exitCode); see $summaryPath and $stderrPath"
    }
}

if ($ValidateOnly) {
    Invoke-ScheduledPython -Stage "validate" -PythonArguments @("-m", "ledger", "--help")
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

    Invoke-ScheduledPython -Stage "export" -PythonArguments @("-u", "export_for_gemini.py")
    Invoke-ScheduledPython -Stage "ledger" -PythonArguments @("-u", "-m", "ledger", "update")
    Set-Content -LiteralPath $lastRunPath -Value $utcDate -NoNewline
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
