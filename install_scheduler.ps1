param(
    [string]$TaskName = "StockMarketBot-DailyBrief"
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "run_scheduled.ps1"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher not found: $launcher"
}

# Poll on the local half-hour. The launcher owns the authoritative UTC gate.
# It runs at most once per UTC day regardless of the host timezone or DST.

$triggers = @(
    for ($minutes = 0; $minutes -lt (24 * 60); $minutes += 30) {
        New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.AddMinutes($minutes))
    }
)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $launcher
)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Description (
        "Stock Market Bot at 21:30 UTC; UTC-gated, DST-safe, and idempotent."
    ) -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName
