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
