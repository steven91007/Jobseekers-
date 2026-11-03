<#
  Scheduled action of "Evening Awake": the task wakes the PC at 18:00 and this script keeps Windows from
  idle-sleeping (system only, the display may turn off) until -Until (default 22:00), so the CV dashboard
  and the apply browser stay reachable over Tailscale in the evening. Afterwards the normal power plan applies.

    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File evening_awake.ps1 [-Until 22:00]
#>
param(
  [string]$Until = "22:00"
)

$end = [datetime]::Today.Add([timespan]::Parse($Until))
if ((Get-Date) -ge $end) { exit 0 }  # started late (StartWhenAvailable after a missed 18:00): nothing to do

Add-Type -Namespace EveningPower -Name Native -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
$ES_CONTINUOUS = [uint32]"0x80000000"
$ES_SYSTEM_REQUIRED = [uint32]"0x00000001"

try {
  # ES_SYSTEM_REQUIRED without ES_DISPLAY_REQUIRED: no idle sleep, the screen may turn off / stay locked
  [void][EveningPower.Native]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
  while ((Get-Date) -lt $end) {
    Start-Sleep -Seconds ([math]::Min(300, [math]::Max(1, ($end - (Get-Date)).TotalSeconds)))
  }
}
finally {
  [void][EveningPower.Native]::SetThreadExecutionState($ES_CONTINUOUS)
}
exit 0
