# cmd-monitor

Terminal Monitor + IM Bridge: 监控终端状态，通过飞书/微信双向通信。

**v0.2:** Daemon 化 + 多 Windows Terminal tab 支持。每个 session 分配独立 token,飞书回复 `<token> 内容` 精确路由到对应 tab。

## 安装

```bash
pip install -e .
```

## 使用

```bash
cmd-monitor --help
cmd-monitor start          # 启动守护进程(Ctrl+C 停止)
cmd-monitor stop           # 通过 PID 文件停止后台 daemon
cmd-monitor status         # 列出活跃 session 与 token
cmd-monitor hooks install  # 安装 Claude Code / Copilot CLI hooks
```

## 多 tab 工作流程

1. `cmd-monitor start` 起 daemon(只保留唯一飞书 WebSocket 连接)
2. 在多个 WT tab 里跑 Claude Code / Copilot CLI
3. hook 触发 → 短命 hook-handler 通过 named pipe 把事件发给 daemon
4. daemon 发卡片,标题带 `[xxxx]` token 前缀
5. 飞书回复 `xxxx 命令` → daemon 切到对应 tab + 注入

## 开发

```bash
pip install -e ".[dev]"
pytest
```

