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

    # Optional steps share this task's 10-minute limit, counted from the start of this PowerShell process. Each runs
    # under an outside time limit (its process tree is killed after it), not only under its own budget. Before the
    # restart a step leaves 4 min of the limit for the restart and the step after it; with less than 30 s for it, it
    # is skipped. Failures are only logged: the update goes on. '-W ignore::RuntimeWarning:runpy' only hides a
    # harmless warning of 'python -m' for modules of the package.

    # Optional: Valhalla road tiles, built before the restart so the server has them at once. Takes about a second
    # when the tiles are current; fails fast (logged, update goes on) without pyvalhalla or the map. At most 180 s;
    # Python stops the build itself 15 s before that. Matrices are not warmed here: that needs an ERP snapshot, and
    # the server warms them itself in a background thread after the restart (serving the old road model meanwhile).
    try {
        $env:PYTHONIOENCODING = 'utf-8'
        $left = [int](((Get-Process -Id $PID).StartTime.AddMinutes(10) - (Get-Date)).TotalSeconds)
        $limit = [Math]::Min(180, $left - 240)
        if ($limit -lt 30) { throw ('{0} s left in the task' -f $left) }
        $build = Start-Process -FilePath $PythonExe -WorkingDirectory $AppDir -NoNewWindow -PassThru `
            -ArgumentList '-W', 'ignore::RuntimeWarning:runpy', '-m', 'route_optimizer.valhalla_engine', 'build', `
                '--max-seconds', ($limit - 15) `
            -RedirectStandardOutput (Join-Path $AppDir 'logs\valhalla_build.log') `
            -RedirectStandardError (Join-Path $AppDir 'logs\valhalla_build.err.log')
        $null = $build.Handle   # PowerShell 5.1: without it ExitCode is empty after WaitForExit
        if (-not $build.WaitForExit($limit * 1000)) {
            taskkill /PID $build.Id /T /F | Out-Null
            Write-Log ('Valhalla tiles not built in {0} s (stopped)' -f $limit)
        }
        elseif ($build.ExitCode -ne 0) {
            Write-Log ('Valhalla tiles not built (exit {0}), see logs\valhalla_build.err.log' -f $build.ExitCode)
        }
    }
    catch {
        Write-Log ('Valhalla tiles skipped: {0}' -f $_)
    }

    # Optional: OSM road distance cache and its small-center bypass cache (dispatch). When an update changes their
    # format, the map or the center boundary, the first request after the restart would rebuild them for about 2 minutes
    # each; do it here, before the restart, and once more after it if this run could not finish. 'warm --if-stale' exits
    # at once when both caches are current (no ERP access then; the center boundary comes from a temporary copy of the
    # routes DB); otherwise it reads an ERP snapshot (read-only) and the route settings from a temporary copy of the
    # routes DB: the DB itself is never changed here (the old server keeps using it until the restart). At most 240 s.
    # With terrain on (No. 85, 'roads dem' was run once) it also checks the climb caches and rebuilds the elevation cache
    # from the downloaded tiles after a map change; climbs are written in chunks, the server finishes the rest itself.
    $warmRoads = {
        param([string]$Log, [int]$KeepS)
        try {
            $env:PYTHONIOENCODING = 'utf-8'
            $left = [int](((Get-Process -Id $PID).StartTime.AddMinutes(10) - (Get-Date)).TotalSeconds)
            $limit = [Math]::Min(240, $left - $KeepS)
            if ($limit -lt 30) { throw ('{0} s left in the task' -f $left) }
            $warm = Start-Process -FilePath $PythonExe -WorkingDirectory $AppDir -NoNewWindow -PassThru `
                -ArgumentList '-W', 'ignore::RuntimeWarning:runpy', '-m', 'route_optimizer.roads', 'warm', `
                    '--if-stale' `
                -RedirectStandardOutput (Join-Path $AppDir "logs\$Log.log") `
                -RedirectStandardError (Join-Path $AppDir "logs\$Log.err.log")
            $null = $warm.Handle   # PowerShell 5.1: without it ExitCode is empty after WaitForExit
            if (-not $warm.WaitForExit($limit * 1000)) {
                taskkill /PID $warm.Id /T /F | Out-Null
                Write-Log ('OSM road cache not warmed in {0} s (stopped), see logs\{1}.log' -f $limit, $Log)
            }
            elseif ($warm.ExitCode -ne 0) {
                Write-Log ('OSM road cache not warmed (exit {0}), see logs\{1}.err.log' -f $warm.ExitCode, $Log)
            }
        }
        catch {
            Write-Log ('OSM road cache warm skipped ({0}): {1}' -f $Log, $_)
        }
    }
    & $warmRoads 'roads_warm' 240

    # Restart the server through the task scheduler
    schtasks /End /TN 'SalesDashboard-Server' | Out-Null
    Start-Sleep -Seconds 3
    # Kill any leftover app process the task engine did not stop
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -match 'app_v2\.py' } |
        ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
    schtasks /Run /TN 'SalesDashboard-Server' | Out-Null
    Write-Log ('Server restarted on commit {0}' -f $remote.Substring(0, 7))

    # Optional: the OSM road cache once more, next to the new server, if the run before the restart could not finish
    # (too little time, stopped, failed): 'warm --if-stale' exits at once when the cache is current.
    & $warmRoads 'roads_warm_after' 30
}
catch {
    Write-Log ("ERROR: {0}" -f $_)
    exit 1
}
