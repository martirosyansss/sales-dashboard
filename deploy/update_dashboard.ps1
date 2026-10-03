# Auto-update Sales Dashboard from GitHub.
# Runs every 5 minutes via scheduled task SalesDashboard-AutoUpdate.
# Exits silently when there is nothing to update.
# Keep this file ASCII-only: PowerShell 5.1 misreads UTF-8 without BOM.
param(
    [string]$AppDir = 'C:\Sales Dashboard',
    [string]$Branch = 'main',
    [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'
$logFile = Join-Path $AppDir 'logs\autoupdate.log'

function Write-Log([string]$Message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Set-Location $AppDir
New-Item -ItemType Directory -Force (Join-Path $AppDir 'logs') | Out-Null

try {
    git fetch origin $Branch --quiet
    $local  = git rev-parse HEAD
    $remote = git rev-parse "origin/$Branch"
    if ($local -eq $remote) { exit 0 }

    Write-Log ('Updating {0} -> {1}' -f $local.Substring(0, 7), $remote.Substring(0, 7))
    git reset --hard "origin/$Branch" --quiet
    & $PythonExe -m pip install -r requirements.txt --quiet --disable-pip-version-check --no-warn-script-location

    # Optional: Valhalla road tiles, built before the restart so the server has them at once. Takes about a second
    # when the tiles are current; fails fast (logged, update goes on) without pyvalhalla or the map. Capped at 180 s
    # to stay within this task's 10-minute limit. Matrices are not warmed here: that needs an ERP snapshot, and the
    # server warms them itself in a background thread after the restart (serving the old road model meanwhile).
    # '-W ignore::RuntimeWarning:runpy' only hides a harmless warning of 'python -m' for modules of the package.
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        $build = Start-Process -FilePath $PythonExe -WorkingDirectory $AppDir -NoNewWindow -Wait -PassThru `
            -ArgumentList '-W', 'ignore::RuntimeWarning:runpy', '-m', 'route_optimizer.valhalla_engine', 'build', `
                '--max-seconds', '180' `
            -RedirectStandardOutput (Join-Path $AppDir 'logs\valhalla_build.log') `
            -RedirectStandardError (Join-Path $AppDir 'logs\valhalla_build.err.log')
        if ($build.ExitCode -ne 0) {
            Write-Log ('Valhalla tiles not built (exit {0}), see logs\valhalla_build.err.log' -f $build.ExitCode)
        }
    }
    catch {
        Write-Log ('Valhalla tiles skipped: {0}' -f $_)
    }

    # Optional: OSM road distance cache. When an update changes its format (or the map), the first request after the
    # restart would rebuild it for about 2 minutes; do it here, before the restart. 'warm --if-stale' exits at once
    # when the cache is current (no ERP access then); otherwise it reads an ERP snapshot (read-only) and fills the
    # cache. Stopped after 240 s (the first request then finishes the job); failures are only logged.
    try {
        $warm = Start-Process -FilePath $PythonExe -WorkingDirectory $AppDir -NoNewWindow -PassThru `
            -ArgumentList '-W', 'ignore::RuntimeWarning:runpy', '-m', 'route_optimizer.roads', 'warm', '--if-stale' `
            -RedirectStandardOutput (Join-Path $AppDir 'logs\roads_warm.log') `
            -RedirectStandardError (Join-Path $AppDir 'logs\roads_warm.err.log')
        $null = $warm.Handle   # PowerShell 5.1: without it ExitCode is empty after WaitForExit
        if (-not $warm.WaitForExit(240000)) {
            Stop-Process -Id $warm.Id -Force -ErrorAction SilentlyContinue
            Write-Log 'OSM road cache not warmed in 240 s (stopped), the first request will finish it'
        }
        elseif ($warm.ExitCode -ne 0) {
            Write-Log ('OSM road cache not warmed (exit {0}), see logs\roads_warm.err.log' -f $warm.ExitCode)
        }
    }
    catch {
        Write-Log ('OSM road cache warm skipped: {0}' -f $_)
    }

    # Restart the server through the task scheduler
    schtasks /End /TN 'SalesDashboard-Server' | Out-Null
    Start-Sleep -Seconds 3
    # Kill any leftover app process the task engine did not stop
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -match 'app_v2\.py' } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
    schtasks /Run /TN 'SalesDashboard-Server' | Out-Null
    Write-Log ('Server restarted on commit {0}' -f $remote.Substring(0, 7))
}
catch {
    Write-Log ("ERROR: {0}" -f $_)
    exit 1
}
