# Send the "Business Pulse" digest (Telegram / Email).
# Schedule this (e.g. daily 09:00) via Task Scheduler; see DIGEST_SETUP.md.
# Keep this file ASCII-only: PowerShell 5.1 misreads UTF-8 without BOM.
param(
    [string]$AppDir = 'C:\Sales Dashboard',
    [string]$PythonExe = 'python',
    [string]$Period = 'this-month'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$logFile = Join-Path $AppDir 'logs\digest.log'
New-Item -ItemType Directory -Force (Join-Path $AppDir 'logs') | Out-Null

function Write-Log([string]$Message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Set-Location $AppDir
try {
    $out = & $PythonExe (Join-Path $AppDir 'deploy\send_digest.py') '--period' $Period
    Write-Log ('OK: ' + ($out -join ' | '))
}
catch {
    Write-Log ('ERROR: ' + $_)
    exit 1
}
