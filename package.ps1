$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'configure.ps1')
$guardianDist = Join-Path $PSScriptRoot 'dist'
$guardianPackage = Join-Path $guardianDist 'plugins\codex-quota-guardian'
New-Item -ItemType Directory -Force -Path $guardianPackage | Out-Null
foreach ($guardianFile in @('plugin.json', '.mcp.json', 'mcp.json', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $guardianFile) -Destination $guardianPackage -Force
}
$guardianOverlay = Join-Path $guardianPackage '.codex-plugin'
New-Item -ItemType Directory -Force -Path (Join-Path $guardianPackage 'hooks') | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'hooks\hooks.json') -Destination (Join-Path $guardianPackage 'hooks\hooks.json') -Force
New-Item -ItemType Directory -Force -Path $guardianOverlay | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.codex-plugin\plugin.json') -Destination $guardianOverlay -Force
$guardianCatalogDir = Join-Path $guardianDist '.agents\plugins'
New-Item -ItemType Directory -Force -Path $guardianCatalogDir | Out-Null
@{
    name = 'quota-guardian-local'
    interface = @{ displayName = '额度守卫本地插件' }
    plugins = @(@{name = 'codex-quota-guardian'; source = @{source = 'local'; path = './plugins/codex-quota-guardian'}; policy = @{installation = 'AVAILABLE'; authentication = 'ON_INSTALL'}; category = 'Productivity'})
} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $guardianCatalogDir 'marketplace.json') -Encoding utf8
Write-Host "插件市场已生成：$guardianDist"
Write-Host '本地 MCP 入口指向此开发工作区；移动项目后请重新执行 package.ps1。'
