# 安装与排障

## 未恢复

检查悬浮条自动恢复开关、data/health.json 的连接状态和任务错误。再看 data/lifecycle-health.json 是否发布成功。不要将这些文件原样公开上传。

连接失效时，在 Codex 中执行 ./connect.ps1；确认插件钩子已被信任。旧地址重试不等于获得新地址。

最新轮次必须明确是额度失败。完成、用户停止、网络错误和等待审批均不会自动继续。

## 悬浮条未显示

运行 ./start.ps1；检查 data/autostart.log 和 data/desktop.log。使用 ./install-autostart.ps1 更新固定路径的登录启动项。移动目录后需重新配置。

## 发布包

先执行 ./configure.ps1 生成 .mcp.json、mcp.json、hooks/hooks.json 和启动快捷入口。这些含本机路径，不应提交到 GitHub。
