param(
    [int]$Port = 5500,
    [switch]$NoInstall,
    # Force a full dependency reinstall even if the cache marker looks current.
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ProjectRoot

Set-Location $ProjectRoot

function Resolve-ExistingPath {
    param([string[]]$Paths)

    foreach ($path in $Paths) {
        if (Test-Path $path) {
            return (Resolve-Path $path).Path
        }
    }

    return $null
}

$venvCandidates = @(
    (Join-Path $ProjectRoot ".venv/bin/Activate.ps1"),
    (Join-Path $ProjectRoot ".venv/Scripts/Activate.ps1"),
    (Join-Path $WorkspaceRoot ".venv/bin/Activate.ps1"),
    (Join-Path $WorkspaceRoot ".venv/Scripts/Activate.ps1")
)

$activated = $false
$activeVenvRoot = $null
foreach ($venv in $venvCandidates) {
    if (Test-Path $venv) {
        Write-Host "Activating Python environment: $venv" -ForegroundColor Cyan
        . $venv
        $activated = $true
        # .../.venv/Scripts/Activate.ps1  ->  .../.venv
        $activeVenvRoot = Split-Path -Parent (Split-Path -Parent $venv)
        break
    }
}

if (-not $activated) {
    Write-Host "No virtual environment found. Using the active Python on PATH." -ForegroundColor Yellow
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        Set-Alias -Name python -Value python3 -Scope Script
    }
    else {
        throw "Python was not found on PATH. Activate a Python environment and retry."
    }
}

$WebAppCandidates = @(
    (Join-Path $ProjectRoot "webapp")
)

$WebAppRoot = Resolve-ExistingPath $WebAppCandidates
if (-not $WebAppRoot) {
    throw "Could not find the QUANTA web app directory. Looked for: $($WebAppCandidates -join ', ')"
}

$RequirementsPath = Join-Path $WebAppRoot "requirements.txt"
# Exact tested versions; installs use it as a pip constraints file.
$ConstraintsPath = Join-Path $WebAppRoot "constraints.txt"
$AppPath = Join-Path $WebAppRoot "app.py"
if (-not (Test-Path $RequirementsPath)) {
    throw "Could not find requirements file at $RequirementsPath"
}
if (-not (Test-Path $AppPath)) {
    throw "Could not find app file at $AppPath"
}

# The engine's own dependency declaration. Installed editable so the deep-dive
# and scanner code paths under core/ resolve every third-party import, not just
# the ones the web layer touches directly.
$PyProjectPath = Join-Path $ProjectRoot "pyproject.toml"

Write-Host "Using web app directory: $WebAppRoot" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Dependency install
# ---------------------------------------------------------------------------
# The web app imports the analysis engines (TradingAnalysis_ELITE, SP_ELITE_PRO)
# in-process, and those import numpy / pandas / yfinance / vaderSentiment / ta /
# scipy / rich / dateutil at module load with NO fallback. A Flask-only install
# boots the server but every /api/analyze and /api/stock-picks request then dies
# with "No module named ...". So the install step must cover the whole runtime,
# and the "skip" fast-path must actually verify it — not just look for Flask.
if (-not $NoInstall) {
    # The modules that MUST import for the app to serve a single request. Kept
    # in sync with webapp/requirements.txt; probing the real import names is the
    # only reliable readiness check (a partial venv can have Flask but not ta).
    $CriticalModules = @(
        "flask", "numpy", "pandas", "requests", "rich",
        "ta", "yfinance", "vaderSentiment", "dateutil", "scipy"
    )

    # Cache marker lives next to the environment that's actually active, so a
    # stale marker beside an unused .venv can never suppress a real install.
    $markerDir = if ($activeVenvRoot) { $activeVenvRoot } else { Join-Path $ProjectRoot ".venv" }
    $markerPath = Join-Path $markerDir ".quanta_reqs_hash"

    # Hash requirements.txt AND pyproject.toml together: either changing must
    # invalidate the cache, and a drift between the two (they declare the same
    # set) forces a clean resolve.
    $hashInputs = @($RequirementsPath)
    if (Test-Path $ConstraintsPath) { $hashInputs += $ConstraintsPath }
    if (Test-Path $PyProjectPath) { $hashInputs += $PyProjectPath }
    # .NET directly, not Get-FileHash: Windows PowerShell 5.1 started from a
    # PowerShell 7 terminal inherits 7's PSModulePath, cannot load Get-FileHash,
    # and (with ErrorActionPreference=Stop) the script died here before the
    # server ever started.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $reqHash = ($hashInputs | ForEach-Object {
            [System.BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes((Resolve-Path $_).Path))).Replace("-", "")
        }) -join "-"
    }
    finally { $sha.Dispose() }
    $cachedHash = if (Test-Path $markerPath) { (Get-Content $markerPath -Raw).Trim() } else { "" }

    # Probe every critical import with importlib.util.find_spec (no side effects,
    # no heavy import). Exit code = count of missing modules; their names go to
    # stderr one per line. The probe string carries NO quote characters on
    # purpose — Windows PowerShell 5.1's native-argument parser mangles embedded
    # quotes when forwarding to python.exe, so chr(10) stands in for a newline.
    $probe = 'import importlib.util as u,sys; miss=[m for m in sys.argv[1:] if u.find_spec(m) is None]; sys.stderr.write(chr(10).join(miss)); sys.exit(len(miss))'
    $probeErr = (& python -c $probe @CriticalModules 2>&1) -join "`n"
    $modulesPresent = ($LASTEXITCODE -eq 0)

    $satisfied = (-not $Reinstall) -and ($cachedHash -eq $reqHash) -and $modulesPresent

    if ($satisfied) {
        Write-Host "Dependencies satisfied (marker current, all modules import)." -ForegroundColor DarkGray
    }
    else {
        if (-not $modulesPresent -and $probeErr.Trim()) {
            Write-Host "Missing Python modules: $(($probeErr -split '\r?\n' | Where-Object { $_ }) -join ', ')" -ForegroundColor Yellow
        }
        Write-Host "Installing QUANTA dependencies (this can take a minute the first time)..." -ForegroundColor Cyan

        python -m pip install --upgrade pip -q
        $constraintArgs = if (Test-Path $ConstraintsPath) { @("-c", $ConstraintsPath) } else { @() }
        python -m pip install -r $RequirementsPath @constraintArgs
        if ($LASTEXITCODE -ne 0) { throw "pip install -r $RequirementsPath failed." }

        if (Test-Path $PyProjectPath) {
            # Editable install with the optional-but-lightweight provider extras
            # (tabulate, textblob, pandas-datareader, polygon-api-client). Every
            # one is import-guarded in the engines, but installing them means the
            # secondary code paths (SP_ShortTerm's table output, TextBlob
            # sentiment fallback, alt data providers) work instead of silently
            # degrading. Falls back to a bare editable install if the extra
            # can't resolve.
            Write-Host "Installing the engine package (editable, with provider extras) ..." -ForegroundColor Cyan
            # $() subexpression so PowerShell doesn't read "$ProjectRoot[providers]"
            # as an index expression on the path string.
            $editableTarget = "$($ProjectRoot)[providers]"
            python -m pip install -e $editableTarget @constraintArgs
            if ($LASTEXITCODE -ne 0) {
                python -m pip install -e $ProjectRoot @constraintArgs
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "Editable engine install failed; continuing on the web requirements only." -ForegroundColor Yellow
                }
            }
        }

        # Re-probe: only write the cache marker once every critical module truly
        # imports, so a partial failure doesn't get remembered as success.
        & python -c $probe @CriticalModules 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            if (-not (Test-Path $markerDir)) { New-Item -ItemType Directory -Path $markerDir -Force | Out-Null }
            Set-Content -Path $markerPath -Value $reqHash -NoNewline
            Write-Host "All dependencies verified." -ForegroundColor Green
        }
        else {
            Write-Host "WARNING: some modules still fail to import after install. The app may error on analysis requests." -ForegroundColor Red
        }
    }
}

$env:QUANTA_PORT = [string]$Port
$env:QUANTA_HOST = "127.0.0.1"
# The engines print emoji / box-drawing glyphs. On a cp1252 console a scan
# job used to die with UnicodeEncodeError; UTF-8 mode makes the streams safe.
$env:PYTHONUTF8 = "1"
Remove-Item Env:\QUANTA_NO_BROWSER -ErrorAction SilentlyContinue

# SEC contact for the corporate-event check. A terminal opened before the user
# variable was set (VS Code inherits its environment at launch) does not have it,
# so read the user scope and then an optional, git-ignored .env.
# Only the state is printed, never the value.
$secSource = $null
if ($env:QUANTA_SEC_CONTACT -or $env:QUANTA_SEC_USER_AGENT) {
    $secSource = "process"
}
else {
    $secContact = [Environment]::GetEnvironmentVariable('QUANTA_SEC_CONTACT', 'User')
    if ($secContact) {
        $env:QUANTA_SEC_CONTACT = $secContact
        $secSource = "Windows user environment"
    }
    Remove-Variable secContact -ErrorAction SilentlyContinue
}

# Every other KEY=VALUE in the git-ignored .env (provider keys, QUANTA_* settings;
# see .env.example) reaches the engines too. Blank values and variables already
# set in the environment are left alone, matching start.sh.
$dotEnv = Join-Path $ProjectRoot ".env"
if (Test-Path $dotEnv) {
    foreach ($line in Get-Content $dotEnv) {
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim('"', "'")
            if (-not $value -or [Environment]::GetEnvironmentVariable($key, 'Process')) { continue }
            [Environment]::SetEnvironmentVariable($key, $value, 'Process')
            if ($key -eq 'QUANTA_SEC_CONTACT' -and -not $secSource) { $secSource = ".env" }
        }
    }
}
if ($secSource) {
    Write-Host "SEC contact: configured (from $secSource)" -ForegroundColor DarkGray
}
else {
    Write-Host "WARNING: QUANTA_SEC_CONTACT is not set (process, user environment or .env)." -ForegroundColor Yellow
    Write-Host "         The corporate-event check is unavailable and no Picks name can be funded." -ForegroundColor Yellow
    Write-Host "         Fix: [Environment]::SetEnvironmentVariable('QUANTA_SEC_CONTACT', '<your email>', 'User'), then rerun start.ps1." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host " QUANTA Elite Daily Dashboard" -ForegroundColor White
Write-Host " http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host " Browser opens automatically. Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor DarkGray
Write-Host ""

# Make sure `core/` (where TradingAnalysis_ELITE.py lives) is on PYTHONPATH
# so the webapp can import the engines however it is started.
$TopLevelCore = Join-Path $ProjectRoot "core"
if (Test-Path $TopLevelCore) {
    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = "$TopLevelCore$([System.IO.Path]::PathSeparator)$env:PYTHONPATH"
    }
    else {
        $env:PYTHONPATH = $TopLevelCore
    }
    Write-Host "Added $TopLevelCore to PYTHONPATH" -ForegroundColor DarkGray
}

python $AppPath
