# ArmSoft local test adapter

This is a Windows-only adapter for the isolated ERP copy on this workstation. It is not a production ERP connector. The source uses the installed ArmSoft 7.16.3 business libraries and does not redistribute them.

The exact target is `(localdb)\AraqichERP20261105`, database `AraqichTest_20261105`, native master `AraqichSM_20261105`, root `G:\Araqich-ERP-Test-20261105-SQL2025`. The native executable rejects another target, checks the actual SQL machine/LocalDB identity, and verifies its per-program outbound-block firewall rule before native calls.

The validated local files already exist under `harness` in that root. `Build-NativeHarness.ps1` compiles the C# files located there using the installed .NET Framework and ArmSoft libraries. `Enable-TestIsolation.ps1` installs only that executable's outbound-block rule and requires Windows elevation. It does not change the production ERP application or SQL service network rules.

`private-config.json` is deliberately absent from source control. It contains only the dedicated local test SQL/native credentials and belongs inside the restricted local root. Do not put it in the repository. The native user/password validation remains enabled; the in-process SQL credential setup is test-specific and is not a qualified production connection contract.

The `dispatch` mode reads one JSON request from stdin and returns JSON to stdout. Actions are `options`, `preview`, `execute`, and `status`. Only `execute` may create business documents. Repeated execution of an existing operation reads its journal and ERP state. Pending/unknown claims are never automatically retried. A `QUARANTINED.json` file blocks new writes while keeping read-only diagnosis available.

The other command modes are labelled local qualification scenarios. They must remain confined to this disposable copy. The old SQL 2017 root `G:\Araqich-ERP-Test-20261105` is quarantined after an intermittent CHECKDB failure and is not the active test target.
