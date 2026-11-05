param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$guardianStartup = [Environment]::GetFolderPath('Startup')
if (-not $guardianStartup) { throw '无法定位当前用户启动目录。' }
$guardianShortcutPath = Join-Path $guardianStartup 'Codex 额度守卫.lnk'
$guardianConfigPath = Join-Path $PSScriptRoot 'data\autostart.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $guardianConfigPath) | Out-Null
if ($Remove) {
    $guardianTask = Get-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher' -ErrorAction SilentlyContinue
    if ($guardianTask) { Unregister-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher' -Confirm:$false }
    if (Test-Path -LiteralPath $guardianShortcutPath) { Remove-Item -LiteralPath $guardianShortcutPath }
    @{enabled = $false} | ConvertTo-Json | Set-Content -LiteralPath $guardianConfigPath -Encoding utf8
    Write-Host '已关闭额度守卫登录启动与 ChatGPT 启动监听。'
    return
}
$guardianBase = & python -c 'import sys; print(sys.base_prefix)'
$guardianPythonw = Join-Path $guardianBase 'pythonw.exe'
if (-not (Test-Path -LiteralPath $guardianPythonw)) { throw '找不到无控制台运行时。' }
$guardianWatch = Join-Path $PSScriptRoot 'watch.pyw'
@{enabled = $true} | ConvertTo-Json | Set-Content -LiteralPath $guardianConfigPath -Encoding utf8
$guardianShell = New-Object -ComObject WScript.Shell
$guardianShortcut = $guardianShell.CreateShortcut($guardianShortcutPath)
$guardianShortcut.TargetPath = $guardianPythonw
$guardianShortcut.Arguments = '"' + $guardianWatch + '"'
$guardianShortcut.WorkingDirectory = $PSScriptRoot
$guardianShortcut.Description = '检测 ChatGPT 启动，自动显示额度悬浮条；不显示命令行。'
$guardianShortcut.WindowStyle = 7
$guardianShortcut.Save()
# The task service owns this process, independent of ChatGPT's process job.
$guardianUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$guardianAction = New-ScheduledTaskAction -Execute $guardianPythonw -Argument ('"' + $guardianWatch + '"') -WorkingDirectory $PSScriptRoot
$guardianPrincipal = New-ScheduledTaskPrincipal -UserId $guardianUser -LogonType Interactive -RunLevel Limited
$guardianTrigger = New-ScheduledTaskTrigger -AtLogOn -User $guardianUser
$guardianSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher' -Action $guardianAction -Principal $guardianPrincipal -Trigger $guardianTrigger -Settings $guardianSettings -Description '额度守卫独立启动监听，无控制台' -Force | Out-Null
Start-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher'
Write-Host '已开启当前用户登录启动与 ChatGPT 启动监听。'
