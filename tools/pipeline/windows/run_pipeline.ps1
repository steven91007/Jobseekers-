<#
  Scheduled action of "AI Job Pipeline": keeps Windows awake (system only, the display may turn off) while the
  WSL daily pipeline runs, then lets the PC sleep again.

    powershell -ExecutionPolicy Bypass -File run_pipeline.ps1 [-Distro Ubuntu-24.04] [-Repo /mnt/e/PythonProject/LinkedinJobSearcher]
#>
param(
  [string]$Distro = "Ubuntu-24.04",
  [string]$Repo = "/mnt/e/PythonProject/LinkedinJobSearcher"
)

Add-Type -Namespace PipelinePower -Name Native -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
$ES_CONTINUOUS = [uint32]"0x80000000"
$ES_SYSTEM_REQUIRED = [uint32]"0x00000001"

$code = 1
try {
  # ES_SYSTEM_REQUIRED without ES_DISPLAY_REQUIRED: no idle sleep, the screen may stay off / locked
  [void][PipelinePower.Native]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
  & wsl.exe -d $Distro --cd $Repo -- bash -l tools/pipeline/run_daily.sh
  $code = $LASTEXITCODE
}
finally {
  [void][PipelinePower.Native]::SetThreadExecutionState($ES_CONTINUOUS)
}
exit $code
