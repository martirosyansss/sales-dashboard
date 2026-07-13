# One-time setup of the Sales Dashboard work server.
# Turns AppDir into a git clone of the repo (WITHOUT deleting local files
# such as .env and runtime *.json), installs dependencies and registers
# two scheduled tasks:
#   SalesDashboard-Server      - runs the app, starts at system boot
#   SalesDashboard-AutoUpdate  - pulls updates from GitHub every 5 minutes
#
# Run from an elevated (Administrator) PowerShell:
#   powershell -ExecutionPolicy Bypass -File install_server.ps1 -Token "github_pat_..."
#
# Keep this file ASCII-only: PowerShell 5.1 misreads UTF-8 without BOM.
param(
    [string]$AppDir  = 'C:\Sales Dashboard',
    [string]$Branch  = 'main',
    [string]$RepoUrl = 'https://github.com/martirosyansss/sales-dashboard.git',
    [string]$Token   = ''   # fine-grained PAT with read-only Contents access to the repo
)

$ErrorActionPreference = 'Stop'

# --- preflight checks ---
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated (Administrator) PowerShell.'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is not installed. Install it first: winget install --id Git.Git -e'
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'python is not installed or not in PATH.' }
$pythonExe = $python.Source

# --- repo: init in place to preserve local untracked files ---
$authUrl = $RepoUrl
if ($Token) { $authUrl = $RepoUrl -replace '^https://', "https://x-access-token:$Token@" }

New-Item -ItemType Directory -Force $AppDir | Out-Null
Set-Location $AppDir

if (-not (Test-Path (Join-Path $AppDir '.git'))) {
    git init | Out-Null
    git remote add origin $authUrl
} else {
    git remote set-url origin $authUrl
}
git fetch origin $Branch
git checkout -f -B $Branch "origin/$Branch"

# --- .env ---
$envFile = Join-Path $AppDir '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $AppDir '.env.example') $envFile
    Write-Warning '.env created from .env.example - EDIT IT: set DB_PASSWORD (and ANTHROPIC_API_KEY if AI features are needed).'
}

# --- dependencies ---
& $pythonExe -m pip install -r requirements.txt --quiet --disable-pip-version-check --no-warn-script-location

# --- scheduled tasks ---
$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$deployDir = Join-Path $AppDir 'deploy'
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest

$serverArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}\run_dashboard.ps1" -AppDir "{1}" -PythonExe "{2}"' -f $deployDir, $AppDir, $pythonExe
$serverAction   = New-ScheduledTaskAction -Execute $psExe -Argument $serverArgs
$serverTrigger  = New-ScheduledTaskTrigger -AtStartup
$serverSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'SalesDashboard-Server' -Action $serverAction -Trigger $serverTrigger `
    -Settings $serverSettings -Principal $principal -Force | Out-Null

$updArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}\update_dashboard.ps1" -AppDir "{1}" -Branch {2} -PythonExe "{3}"' -f $deployDir, $AppDir, $Branch, $pythonExe
$updAction   = New-ScheduledTaskAction -Execute $psExe -Argument $updArgs
$updTrigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$updSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'SalesDashboard-AutoUpdate' -Action $updAction -Trigger $updTrigger `
    -Settings $updSettings -Principal $principal -Force | Out-Null

Start-ScheduledTask -TaskName 'SalesDashboard-Server'

Write-Output ''
Write-Output 'Done. Dashboard: http://localhost:5000'
Write-Output 'Tasks registered: SalesDashboard-Server (at boot), SalesDashboard-AutoUpdate (every 5 min).'
if (-not (Select-String -Path $envFile -Pattern '^DB_PASSWORD=.+' -Quiet)) {
    Write-Warning 'DB_PASSWORD in .env is empty - the app cannot connect to the database until you set it.'
}
