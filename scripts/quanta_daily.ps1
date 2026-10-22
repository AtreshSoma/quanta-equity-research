# QUANTA daily paper-trading job for Windows. The same steps as scripts/quanta_daily.sh:
#
#   1. resolve   score every call whose horizon has elapsed (excess return vs SPY / Nifty)
#   2. scan      run the short-term scanner; its calls enter the ledger, immutably
#   3. alerts    report stops / targets touched by open calls (Windows notification)
#   4. report    write the benchmarked track record to the log
#   5. publish   export the tamper-evident record to track_record/ (optionally commit it)
#
# Run it by hand, or schedule it with scripts/schedule_daily.ps1.
# Settings (environment or .env): QUANTA_DAILY_CAPITAL (2500), QUANTA_DAILY_SCAN
# (standard), QUANTA_DAILY_POSITIONS (5), QUANTA_PYTHON (interpreter to use).
# Logs: logs/daily/<date>.log
param(
    # Skip the scan step (resolve, alerts, report and publish still run).
    [switch]$NoScan
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# --- .env: KEY=VALUE lines, never executed. Variables already set win; blanks are skipped.
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
        $key = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value -match '^"(.*)"$' -or $value -match "^'(.*)'$") { $value = $Matches[1] }
        if (-not $value -or [Environment]::GetEnvironmentVariable($key)) { continue }
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

# --- Python: QUANTA_PYTHON, then a project .venv, then the first interpreter that has pandas.
function Find-Python {
    $candidates = @()
    if ($env:QUANTA_PYTHON) { $candidates += $env:QUANTA_PYTHON }
    $candidates += (Join-Path $Root ".venv\Scripts\python.exe")
    $candidates += (Join-Path (Split-Path -Parent $Root) ".venv\Scripts\python.exe")
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $resolved = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($resolved) { $candidates += $resolved }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($c in $candidates) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        & $c -c "import pandas, yfinance" 2>$null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return $null
}

$logDir = Join-Path $Root "logs\daily"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$text) { Add-Content -Path $log -Value $text -Encoding UTF8 }
function Step([string]$name) { Write-Log ("`n=== {0}  {1} ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $name) }

$Python = Find-Python
if (-not $Python) {
    Step "no Python with pandas and yfinance found: set QUANTA_PYTHON in .env"
    exit 1
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$coreDir = Join-Path $Root "core"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreDir;$env:PYTHONPATH" } else { $coreDir }

# Run one Python step, appending all of its output to the log. Returns its exit code.
function Invoke-Py([string[]]$arguments) {
    $output = & $Python @arguments 2>&1
    $code = $LASTEXITCODE
    if ($output) { $output | ForEach-Object { Write-Log "$_" } }
    return $code
}

$status = 0
Step "python $Python"

Step "resolve"
if ((Invoke-Py @("-m", "tracking", "resolve")) -ne 0) { $status = 1 }

# Scan once per completed New York session, whatever the local time zone.
# Weekends and holidays add no new session, so the scan is skipped then.
$marker = Join-Path $logDir ".last_scanned_session"
$session = (& $Python -c "from engine.market_calendar import last_completed_session as l; print(l())" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $session) {
    Step "scan skipped: could not read the market calendar"
    $session = ""
    $status = 1
}
$session = "$session".Trim()
$lastScanned = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }

if ($NoScan) {
    Step "scan skipped: -NoScan"
} elseif ($session -and $session -ne $lastScanned) {
    Step "scan (session $session)"
    $scanArgs = @(
        (Join-Path $coreDir "SP_ShortTerm.py"), "-y", "--no-export",
        "--capital", $(if ($env:QUANTA_DAILY_CAPITAL) { $env:QUANTA_DAILY_CAPITAL } else { "2500" }),
        "--scan", $(if ($env:QUANTA_DAILY_SCAN) { $env:QUANTA_DAILY_SCAN } else { "standard" }),
        "--max-positions", $(if ($env:QUANTA_DAILY_POSITIONS) { $env:QUANTA_DAILY_POSITIONS } else { "5" })
    )
    if ((Invoke-Py $scanArgs) -eq 0) {
        Set-Content -Path $marker -Value $session -NoNewline -Encoding ASCII
    } else {
        $status = 1
    }
} elseif ($session) {
    Step "scan skipped: session $session was already scanned"
}

Step "alerts"
if ((Invoke-Py @("-m", "tracking", "alerts", "--notify")) -ne 0) { $status = 1 }

Step "report"
if ((Invoke-Py @("-m", "tracking", "report")) -ne 0) { $status = 1 }

# Export the hash-chained public record. Set QUANTA_PUBLISH_COMMIT=1 to also commit it,
# so git timestamps each day's link (it is never pushed for you).
Step "publish"
if ((Invoke-Py @("-m", "tracking", "publish")) -ne 0) { $status = 1 }
if ($env:QUANTA_PUBLISH_COMMIT -eq "1") {
    git add track_record 2>&1 | ForEach-Object { Write-Log "$_" }
    git diff --cached --quiet -- track_record
    if ($LASTEXITCODE -ne 0) {
        git commit -q -m ("track record: {0}" -f (Get-Date -Format "yyyy-MM-dd")) -- track_record 2>&1 |
            ForEach-Object { Write-Log "$_" }
        if ($LASTEXITCODE -ne 0) { $status = 1 }
    }
}

Step "done (status $status)"
exit $status
