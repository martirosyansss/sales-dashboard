#Requires -RunAsAdministrator
<#
Installs the ERP bridge service. Idempotent: re-running keeps the existing secrets unless -RotateSecrets is given.
It never creates WRITES_ENABLED.json: writing to the ERP stays off until an administrator enables it by hand.
Not executed during development.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BuildDir,
    [Parameter(Mandatory = $true)][string]$ErpUser,
    [int]$Port = 8765,
    [string]$SiteIp = '192.168.1.24',
    [switch]$RotateSecrets
)
$ErrorActionPreference = 'Stop'
$serviceName = 'AraqichErpBridge'
$account = "NT SERVICE\$serviceName"
$root = 'C:\ProgramData\AraqichErpBridge'
$binaries = Join-Path $env:ProgramFiles 'AraqichErpBridge'
$firewallRule = 'Araqich ERP bridge from route site'
$prefix = "http://+:$Port/"

# 1. Verify the build against its manifest before anything is installed.
$manifest = Get-Content -LiteralPath (Join-Path $BuildDir 'manifest.sha256.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $BuildDir $entry.file) -Algorithm SHA256).Hash
    if ($hash -ne $entry.sha256) { throw "Manifest mismatch: $($entry.file)" }
}

# The port must be free or already ours. Checked before anything on this machine is changed.
foreach ($block in ((& netsh http show urlacl) -join "`n") -split "\n\s*\n") {
    if ($block -match (':' + $Port + '/') -and $block -notmatch [regex]::Escape($account)) {
        throw "Port $Port is already reserved by another account. Nothing was changed. Free it or choose another -Port."
    }
}

# 2. A running service stops only after its write children finish (OnStop waits and never kills them).
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing -and $existing.Status -eq 'Running') {
    Stop-Service -Name $serviceName
    $existing.WaitForStatus('Stopped', [TimeSpan]::FromHours(1))
}

# 3. Binaries are read-only for the service account.
New-Item -ItemType Directory -Force -Path $binaries | Out-Null
foreach ($entry in $manifest.files) { Copy-Item -LiteralPath (Join-Path $BuildDir $entry.file) -Destination (Join-Path $binaries $entry.file) -Force }
$bridgeExe = Join-Path $binaries 'AraqichErpBridge.exe'
$nativeExe = Join-Path $binaries 'AraqichErpNative.exe'

# 4. Service, manual start, virtual account.
if (-not $existing) {
    New-Service -Name $serviceName -BinaryPathName "`"$bridgeExe`"" -StartupType Manual -DisplayName 'Araqich ERP bridge' | Out-Null
}
& sc.exe config $serviceName start= demand obj= $account | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'sc.exe config failed' }

# 5. Data root: SYSTEM and Administrators only. The service reads the root (so it cannot change its own
#    WRITES_ENABLED.json, QUARANTINED.json or configs) and may write only to logs and dispatch.
New-Item -ItemType Directory -Force -Path $root | Out-Null
& icacls $root /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' "${account}:(OI)(CI)RX" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls failed on the root' }
foreach ($writable in 'logs', 'dispatch') {
    $folder = Join-Path $root $writable
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    & icacls $folder /grant:r "${account}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $writable" }
}

function Protect-Secret([string]$Text) {
    Add-Type -AssemblyName System.Security
    $plain = [Text.Encoding]::UTF8.GetBytes($Text)
    try { [Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect($plain, $null, 'LocalMachine')) }
    finally { [Array]::Clear($plain, 0, $plain.Length) }
}

# 6. ERP credentials for the dedicated non-administrator user. No SQL login is stored.
#    Non-secret fields are rewritten on every run; the protected password is kept unless rotated or the user changed.
$privateConfig = Join-Path $root 'private-config.json'
$previous = if (Test-Path -LiteralPath $privateConfig) { Get-Content -LiteralPath $privateConfig -Raw | ConvertFrom-Json } else { $null }
$passwordProtected = if ($previous -and -not $RotateSecrets -and $previous.erp_user -ceq $ErpUser) { $previous.erp_password_protected } else { $null }
if (-not $passwordProtected) {
    $secure = Read-Host -Prompt "ERP password for $ErpUser" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $passwordProtected = Protect-Secret $password
    Remove-Variable password
}
@{ erp_user = $ErpUser; erp_password_protected = $passwordProtected } | ConvertTo-Json -Compress |
    Set-Content -LiteralPath $privateConfig -Encoding UTF8

# 7. Bridge config and HMAC secret. The plain secret is written once for transfer to the site host, then must be deleted.
$bridgeConfig = Join-Path $root 'bridge-config.json'
$siteSecretFile = Join-Path $root 'site-hmac-secret.txt'
$previous = if (Test-Path -LiteralPath $bridgeConfig) { Get-Content -LiteralPath $bridgeConfig -Raw | ConvertFrom-Json } else { $null }
$secretProtected = if ($previous -and -not $RotateSecrets) { $previous.secret_protected } else { $null }
if (-not $secretProtected) {
    $random = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($random)
    $secret = (($random | ForEach-Object { $_.ToString('x2') }) -join '')
    $secretProtected = Protect-Secret $secret
    Set-Content -LiteralPath $siteSecretFile -Value $secret -Encoding Ascii -NoNewline
    Remove-Variable secret
    Write-Host "Copy $siteSecretFile to the site host (ROUTES_ERP_CHAIN_BRIDGE_SECRET_FILE), then delete it here."
}
[ordered]@{ listen_prefix = $prefix; allowed_ips = @($SiteIp); secret_protected = $secretProtected; native_exe = $nativeExe } |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $bridgeConfig -Encoding UTF8

# 8. URL reservation and inbound rule, limited to the site host and the bridge port.
& netsh http delete urlacl url=$prefix | Out-Null
& netsh http add urlacl url=$prefix "user=$account" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'netsh urlacl failed' }
Get-NetFirewallRule -DisplayName $firewallRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $firewallRule -Direction Inbound -Action Allow -Enabled True -Profile Any `
    -Protocol TCP -LocalPort $Port -RemoteAddress $SiteIp -Program System | Out-Null

Write-Host "Installed. Service '$serviceName' is manual and stopped. ERP writes stay disabled: no WRITES_ENABLED.json was created."
Write-Host 'Next steps and the staged go-live are in docs/erp-chain-bridge.md.'
