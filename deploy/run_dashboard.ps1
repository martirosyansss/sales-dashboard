# Starts Sales Dashboard (called by scheduled task SalesDashboard-Server).
# Keep this file ASCII-only: PowerShell 5.1 misreads UTF-8 without BOM.
param(
    [string]$AppDir = 'C:\Sales Dashboard',
    [string]$PythonExe = 'python'
)

Set-Location $AppDir
New-Item -ItemType Directory -Force (Join-Path $AppDir 'logs') | Out-Null
$log = Join-Path $AppDir ('logs\dashboard_{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))

# cmd handles output redirection for a native exe cleanly under PS 5.1
cmd /c "`"$PythonExe`" app_v2.py >> `"$log`" 2>&1"
