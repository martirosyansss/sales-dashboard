$ErrorActionPreference = 'Stop'
$vendor = 'C:\Program Files (x86)\Armenian Software\AS-Sales Management 7'
$framework = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319'
$root = 'G:\Araqich-ERP-Test-20261105-SQL2025\harness'
$refs = @('System.dll','System.Core.dll','System.Data.dll','System.Web.Extensions.dll','Microsoft.CSharp.dll',
          'System.Windows.Forms.dll','System.Drawing.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$refs += @('WindowsBase.dll','PresentationCore.dll','PresentationFramework.dll') |
    ForEach-Object { '/reference:' + (Join-Path $framework ('WPF\' + $_)) }
$refs += @('ArmSoft.Core.dll','ArmSoft.Core.GE.dll','SalesManagement.BusinessObjects.dll') |
    ForEach-Object { '/reference:' + (Join-Path $vendor $_) }
$refs += '/reference:' + (Join-Path $vendor 'Bin\DevExpress.Data.v21.1.dll')
& (Join-Path $framework 'csc.exe') /nologo /platform:x86 /target:exe /debug:pdbonly /define:ISOLATED_TEST `
    ('/out:' + (Join-Path $root 'AraqichNativeTest.exe')) @refs @((Get-ChildItem -LiteralPath $root -Filter '*.cs').FullName)
if ($LASTEXITCODE -ne 0) { throw 'Native test harness build failed' }
