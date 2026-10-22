# Schedule scripts/quanta_daily.ps1 with Windows Task Scheduler, Monday to Saturday.
#
#   scripts/schedule_daily.ps1                install, 17:30 local time (any time works: the job
#                                             scans each completed New York session once)
#   scripts/schedule_daily.ps1 -Time 18:15    install at another local time
#   scripts/schedule_daily.ps1 -Remove        uninstall
#   scripts/schedule_daily.ps1 -Status        show whether it is scheduled and the last log
#   scripts/schedule_daily.ps1 -RunNow        start the scheduled job immediately
#
# The task runs as you, only while you are signed in, so it needs no stored password.
# A run missed while the PC was off or asleep starts as soon as it is available again.
param(
    [string]$Time = "17:30",
    [switch]$Remove,
    [switch]$Status,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"
$TaskName = "QUANTA Daily Track Record"
$Root = Split-Path -Parent $PSScriptRoot
$Job = Join-Path $PSScriptRoot "quanta_daily.ps1"

function Show-LastLog {
    $last = Get-ChildItem (Join-Path $Root "logs\daily\*.log") -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($last) {
        Write-Host "Last log: $($last.FullName)"
        Get-Content $last.FullName -Tail 5
    }
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$TaskName'."
    exit 0
}

if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "'$TaskName' is scheduled ($($task.State)). Next run: $($info.NextRunTime). Last run: $($info.LastRunTime) (result $($info.LastTaskResult))."
    } else {
        Write-Host "'$TaskName' is not scheduled."
    }
    Show-LastLog
    exit 0
}

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started '$TaskName'. Follow it in $Root\logs\daily\."
    exit 0
}

if ($Time -notmatch '^([01]?[0-9]|2[0-3]):([0-5][0-9])$') {
    throw "Time must be HH:MM, got '$Time'."
}
$at = [datetime]::ParseExact(("{0:D2}:{1}" -f [int]$Matches[1], $Matches[2]), "HH:mm", $null)

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"{0}`"" -f $Job) `
    -WorkingDirectory $Root
# Saturday too: zones ahead of New York finish Friday's session then.
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday, Saturday -At $at
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "QUANTA: resolve due calls, scan the completed session, report and publish the track record." `
    -Force | Out-Null

Write-Host ("Scheduled '{0}' Monday-Saturday at {1:HH:mm} local time." -f $TaskName, $at)
Write-Host "Logs: $Root\logs\daily\"
Write-Host "Run now: scripts\schedule_daily.ps1 -RunNow"
