$ErrorActionPreference = 'Stop'
$testRoot = 'G:\Araqich-ERP-Test-20261105-SQL2025'
$program = [IO.Path]::GetFullPath((Join-Path $testRoot 'harness\AraqichNativeTest.exe'))
if ($program -ne 'G:\Araqich-ERP-Test-20261105-SQL2025\harness\AraqichNativeTest.exe') { throw 'Unexpected program target' }
$name = 'AraqichERPTest20261105SQL2025-Native-Outbound'
$rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($rule) {
    $filter = $rule | Get-NetFirewallApplicationFilter
    if ($filter.Program -ne $program -or $rule.Action -ne 'Block' -or $rule.Direction -ne 'Outbound' -or $rule.Enabled -ne 'True') {
        throw 'An existing rule does not match the expected isolation; no rule changed'
    }
} else {
    $rule = New-NetFirewallRule -Name $name -DisplayName 'Araqich local ERP test SQL2025 - block external connections' `
        -Description 'Only the isolated native test executable. No production program, SQL service or user-wide network setting is changed.' `
        -Program $program -Direction Outbound -Action Block -Enabled True -Profile Any -Protocol Any -RemoteAddress Any
}
$filter = $rule | Get-NetFirewallApplicationFilter
@{ rule = $name; program = $filter.Program; enabled = [string]$rule.Enabled; action = [string]$rule.Action;
   direction = [string]$rule.Direction; verified_utc = [DateTime]::UtcNow.ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testRoot 'evidence\firewall-proof.json') -Encoding UTF8
