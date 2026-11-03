<#
  Register the daily job pipeline in Windows Task Scheduler (run once, normal PowerShell, no admin needed):

    powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\...\register_task.ps1
    # or from the repo on E:
    powershell -ExecutionPolicy Bypass -File E:\PythonProject\LinkedinJobSearcher\tools\pipeline\windows\register_task.ps1

  Options: -Distro Ubuntu-24.04  -Repo /mnt/e/PythonProject/LinkedinJobSearcher  -RunNow  -Unregister
#>
param(
  [string]$Distro = "Ubuntu-24.04",
  [string]$Repo = "/mnt/e/PythonProject/LinkedinJobSearcher",
  [string]$TaskName = "AI Job Pipeline",
  [switch]$RunNow,
  [switch]$Unregister
)
$ErrorActionPreference = "Stop"

if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "已移除排程:$TaskName"
  exit 0
}

$distros = (wsl.exe -l -q) -replace "`0", "" | Where-Object { $_.Trim() }
if (-not ($distros | Where-Object { $_.Trim() -eq $Distro })) {
  throw "找不到 WSL distro '$Distro'。現有:$($distros -join ', ')。用 -Distro 指定。"
}

$xmlPath = Join-Path $PSScriptRoot "daily_pipeline_task.xml"
$runner = Join-Path $PSScriptRoot "run_pipeline.ps1"
if ($runner -like "\\wsl*") {
  Write-Warning "run_pipeline.ps1 在 \\wsl 路徑上;建議從 E:\PythonProject\LinkedinJobSearcher\tools\pipeline\windows 執行本腳本,讓排程用 Windows 路徑。"
}
$xml = (Get-Content -Raw -Encoding UTF8 $xmlPath).Replace("__SCRIPT__", $runner).Replace("__DISTRO__", $Distro).Replace("__REPO__", $Repo)
$user = "$env:USERDOMAIN\$env:USERNAME"
Register-ScheduledTask -TaskName $TaskName -Xml $xml -User $user -Force | Out-Null
Write-Host "已註冊排程「$TaskName」:每天 04:00(台北時間),會喚醒睡眠中的電腦、執行期間保持不睡,錯過時開機後盡快補跑;使用者 $user(需保持登入,鎖定畫面即可)。"
Write-Host "檢查喚醒計時器:powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE(要是 Enable / 1),設定方式見 tools/pipeline/README.md。"

# quick check that the WSL side can run the pipeline (prints the help, no calls)
wsl.exe -d $Distro -- bash -lc "cd '$Repo' && python3 .claude/skills/daily-job-tracker/scripts/daily_pipeline.py --help | head -1 && command -v claude codex"

if ($RunNow) {
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "已啟動一次。紀錄:$Repo/data/pipeline/logs/"
}
