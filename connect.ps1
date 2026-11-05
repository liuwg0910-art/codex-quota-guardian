$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ([System.Environment]::OSVersion.Platform -eq 'Win32NT') {
    & python -m guardian --connect-desktop
    if ($LASTEXITCODE -ne 0) { throw '桌面连接配置失败。请在 Codex 会话内运行本脚本，以获取程序提供的真实连接信息。' }
    return
}
# Uses the supported CLI; does not alter config.toml or enable remote control.
& codex app-server daemon start
if ($LASTEXITCODE -ne 0) { throw '无法启动本地共享 App Server。' }
& python -m guardian --check
if ($LASTEXITCODE -ne 0) { throw '共享连接诊断失败。额度悬浮条仍可使用只读回退模式。' }
