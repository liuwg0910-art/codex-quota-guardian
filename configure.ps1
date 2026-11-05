$ErrorActionPreference = 'Stop'
$guardianBase = & python -c 'import sys; print(sys.base_prefix)'
$guardianPython = Join-Path $guardianBase 'python.exe'
$guardianConfig = @{ mcpServers = @{ 'quota-guardian' = @{ command = $guardianPython; args = @((Join-Path $PSScriptRoot 'scripts\mcp_server.py')) } } }
$guardianConfig.mcpServers.'quota-guardian'.Add('env_vars', @('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_MCP_NODE_PATH'))
$guardianConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot '.mcp.json') -Encoding utf8
$guardianConfig.mcpServers.'quota-guardian'.Add('type', 'stdio')
$guardianConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'mcp.json') -Encoding utf8
Write-Host '已生成当前电脑的插件 MCP 配置。'
$guardianBase = & $guardianPython -c 'import sys; print(sys.base_prefix)'
$guardianPythonw = Join-Path $guardianBase 'pythonw.exe'
$guardianLauncher = Join-Path $PSScriptRoot 'launch.pyw'
$guardianVbs = 'CreateObject("WScript.Shell").Run """' + $guardianPythonw + '"" ""' + $guardianLauncher + '""", 0, False'
$guardianVbs | Set-Content -LiteralPath (Join-Path $PSScriptRoot '启动额度条.vbs') -Encoding Unicode
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'hooks') | Out-Null
$guardianHookCommand = '"' + $guardianPython + '" "' + (Join-Path $PSScriptRoot 'scripts\refresh_bridge.py') + '"'
$guardianHookHandler = @{type='command'; command=$guardianHookCommand; timeout=10}
@{hooks=@{SessionStart=@(@{hooks=@($guardianHookHandler)}); UserPromptSubmit=@(@{hooks=@($guardianHookHandler)})}} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'hooks\hooks.json') -Encoding utf8
