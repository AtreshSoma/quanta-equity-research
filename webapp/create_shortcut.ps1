# Creates (or repoints) the desktop shortcut for QUANTA Web
# Run once: powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
#
# The shortcut must start THIS checkout. The launcher lives at the repository
# root, one level above this webapp; the script stops rather than write a
# shortcut to a file that does not exist.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # <repo>/webapp
$candidates = @(
    (Join-Path $scriptDir "..\launch_quanta.bat")   # repository root
)
$batPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $batPath) {
    throw "Could not find launch_quanta.bat. Looked in: $($candidates -join ', ')"
}
$batPath    = (Resolve-Path $batPath).Path
$projectDir = Split-Path -Parent $batPath
$desktop    = [Environment]::GetFolderPath("Desktop")
$lnkPath    = Join-Path $desktop "QUANTA Elite.lnk"

$shell    = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$previous = $shortcut.TargetPath
$shortcut.TargetPath       = $batPath
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description      = "QUANTA Elite - Quantitative Trading Intelligence"
$shortcut.WindowStyle      = 1  # Normal window
$shortcut.Save()

Write-Host ""
Write-Host "  Desktop shortcut: $lnkPath" -ForegroundColor Green
Write-Host "  Starts: $batPath"
if ($previous -and ($previous -ne $batPath)) {
    Write-Host "  (It previously started $previous)" -ForegroundColor Yellow
}
Write-Host ""
