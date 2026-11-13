# Builds the production-flavour native target and the bridge, plus a SHA256 manifest. Installs nothing.
# Run this on a machine with the ArmSoft 7.16.3 client installed. Do not add /define:ISOLATED_TEST here.
$ErrorActionPreference = 'Stop'
$bridgeSource = Split-Path -Parent $PSCommandPath
$nativeSource = Join-Path (Split-Path -Parent $bridgeSource) 'armsoft_local_test'
$output = 'G:\Araqich-ERP-Bridge-Build-20261109'
$vendor = 'C:\Program Files (x86)\Armenian Software\AS-Sales Management 7'
$framework = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319'
$csc = Join-Path $framework 'csc.exe'

New-Item -ItemType Directory -Force -Path $output | Out-Null

$refs = @('System.dll','System.Core.dll','System.Data.dll','System.Web.Extensions.dll','Microsoft.CSharp.dll',
          'System.Windows.Forms.dll','System.Drawing.dll','System.Security.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$refs += @('WindowsBase.dll','PresentationCore.dll','PresentationFramework.dll') |
    ForEach-Object { '/reference:' + (Join-Path $framework ('WPF\' + $_)) }
$refs += @('ArmSoft.Core.dll','ArmSoft.Core.GE.dll','SalesManagement.BusinessObjects.dll') |
    ForEach-Object { '/reference:' + (Join-Path $vendor $_) }
$refs += '/reference:' + (Join-Path $vendor 'Bin\DevExpress.Data.v21.1.dll')

$native = Join-Path $output 'AraqichErpNative.exe'
& $csc /nologo /platform:x86 /target:exe /debug- /optimize+ ('/out:' + $native) @refs @((Get-ChildItem -LiteralPath $nativeSource -Filter '*.cs').FullName)
if ($LASTEXITCODE -ne 0) { throw 'Production native build failed' }
Copy-Item -LiteralPath (Join-Path $nativeSource 'AraqichNativeTest.exe.config') -Destination ($native + '.config') -Force

$bridge = Join-Path $output 'AraqichErpBridge.exe'
$bridgeRefs = @('System.dll','System.Core.dll','System.ServiceProcess.dll','System.Security.dll','System.Web.Extensions.dll') |
    ForEach-Object { '/reference:' + (Join-Path $framework $_) }
& $csc /nologo /platform:anycpu /target:exe /debug- /optimize+ ('/out:' + $bridge) @bridgeRefs (Join-Path $bridgeSource 'AraqichErpBridge.cs')
if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed' }

# The production binary must not carry any test-flavour marker (metadata and user strings).
$bytes = [IO.File]::ReadAllBytes($native)
$ascii = [Text.Encoding]::GetEncoding(28591).GetString($bytes)
$utf16 = [Text.Encoding]::Unicode.GetString($bytes)
$utf16Odd = [Text.Encoding]::Unicode.GetString($bytes, 1, $bytes.Length - 1)
foreach ($forbidden in 'sqlServerLogin', 'test-stop', 'AraqichTest', 'localdb') {
    if ($ascii.Contains($forbidden) -or $utf16.Contains($forbidden) -or $utf16Odd.Contains($forbidden)) { throw "Production binary contains '$forbidden'" }
}

$files = @($native, ($native + '.config'), $bridge) | ForEach-Object {
    [ordered]@{ file = (Split-Path -Leaf $_); sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }
}
[ordered]@{ built_utc = [DateTime]::UtcNow.ToString('o'); files = $files } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'manifest.sha256.json') -Encoding UTF8
Write-Host "Built to $output"
