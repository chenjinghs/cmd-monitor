# cmd-monitor 使用文档

终端监控 + 飞书双向通信工具。监控 Windows PowerShell 中运行的 AI CLI 工具（Claude Code、GitHub Copilot CLI），在它们停止等待输入时通过飞书发送通知，回复指令后自动注入终端。

**v0.2 新增:** 多 Windows Terminal tab 支持 — 通过 daemon + 短 token 路由,飞书回复能精确送达原始 tab。

## 多 tab 工作方式

1. `cmd-monitor start` 启动唯一的 daemon 进程,持有飞书 WS 长连接;
2. 每次 hook 触发,短命的 hook handler 进程采集 (session_id, WT_SESSION, tab index, hwnd) → 通过 named pipe (`\\.\pipe\cmd-monitor`) 上报给 daemon;
3. daemon 为每个 session 分配 4 位 hex token,卡片标题前缀 `[ab12]`;
4. 用户在飞书回复 `ab12 命令内容` → daemon 解析 token → 查注册表 → `wt.exe focus-tab --target N` 切到对应 tab → 注入。

## 飞书回复格式

- `<token> <内容>` — 路由到指定 session(推荐)
- `<内容>` — 路由到最近活跃的 session(可在 `state.fallback_to_last_active = false` 关闭)

Token 不区分大小写,token 与内容之间允许空格、Tab、半角/全角冒号(`:` / `：`)。


---

## 目录

- [环境要求](#环境要求)
- [安装](#安装)
- [配置飞书应用](#配置飞书应用)
- [配置文件说明](#配置文件说明)
- [快速开始](#快速开始)
- [CLI 命令参考](#cli-命令参考)
- [三种监控模式](#三种监控模式)
- [状态管理与防抖](#状态管理与防抖)
- [指令注入](#指令注入)
- [常见问题](#常见问题)

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.9+ | 推荐 3.11 |
| Windows | 10/11 | 依赖 Win32 API |
| PowerShell | 5.1+ 或 7.x | 终端环境 |
| 飞书应用 | 自建 | 需开启机器人能力 |

---

## 安装

```bash
# 克隆项目
git clone <repo-url> CmdMonitor
cd CmdMonitor

# 安装（开发模式）
pip install -e .

# 验证安装
cmd-monitor --help
```

---

## 配置飞书应用

### 1. 创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，创建企业自建应用
2. 在「应用能力」中开启「机器人」
3. 在「权限管理」中申请以下权限：
   - `im:message:send_as_bot` — 以机器人身份发送消息
   - `im:message` — 读取消息
4. 在「事件订阅」中：
   - 订阅方式选择 **WebSocket 模式**（无需公网服务器）
   - 订阅事件 `im.message.receive_v1` — 接收用户回复
5. 发布应用并审批通过

### 2. 获取凭证

| 字段 | 来源 |
|------|------|
| `app_id` | 应用详情页 → 基础信息 → App ID |
| `app_secret` | 应用详情页 → 基础信息 → App Secret |
| `receiver_id` | 目标用户的 `open_id`（可通过飞书 API 获取） |

### 3. 填入配置

编辑 `config/default.toml`：

```toml
[feishu]
app_id = "cli_xxxxxxxxxxxxxxxx"
app_secret = "xxxxxxxxxxxxxxxxxxxxxxxx"
receiver_id = "ou_xxxxxxxxxxxxxxxxxxxxxxxx"
```

---

## 配置文件说明

配置文件路径：`config/default.toml`

### `[general]` — 通用配置

```toml
[general]
pid_file = "/tmp/cmd-monitor.pid"   # 守护进程 PID 文件
log_file = ""                        # 日志文件路径（空则只输出到控制台）
log_level = "INFO"                   # 日志级别：DEBUG / INFO / WARNING / ERROR
```

### `[feishu]` — 飞书机器人

```toml
[feishu]
app_id = ""          # 飞书应用 App ID
app_secret = ""      # 飞书应用 App Secret
receiver_id = ""     # 接收通知的用户 open_id
```

### `[powershell]` — PowerShell 监控

```toml
[powershell]
enabled = true                    # 是否启用 transcript 监控
transcript_path = ""              # Transcript 文件路径（空则需手动指定）
poll_interval = 5                 # 空闲检测间隔（秒）
idle_threshold = 10               # 空闲判定阈值（秒）
prompt_pattern = "PS [A-Z]:\\\\>" # PowerShell 提示符正则
```

### `[inject]` — 指令注入

```toml
[inject]
method = "sendkeys"       # 注入方式：sendkeys（剪贴板+模拟按键）或 namedpipe
target_window = ""        # 目标窗口标题关键词（空则自动匹配 PowerShell）
inject_delay = 0.5        # 注入后等待时间（秒）
```

### `[hooks]` — Hook 监控

```toml
[hooks]
enabled = true

[hooks.claude]
enabled = true
config_path = ".claude/settings.json"           # Claude Code hook 配置路径
events = ["Notification", "Stop", "PermissionRequest", "AskUserQuestion"]

[hooks.copilot]
enabled = true
config_dir = ".github/hooks"                    # copilot-cli hook 配置目录
events = ["sessionStart", "sessionEnd", "userPromptSubmitted", "preToolUse", "postToolUse", "errorOccurred"]
```

### `[state]` — 状态管理

```toml
[state]
debounce_seconds = 10.0        # 防抖窗口（秒）— 空闲持续超过此时间才触发通知
notification_cooldown = 60.0   # 通知冷却（秒）— 相同状态不重复通知的最小间隔
```

---

## 快速开始

### 步骤 1：安装并配置

```bash
pip install -e .
# 编辑 config/default.toml，填入飞书凭证
```

### 步骤 2：安装 Hook（一次性）

```bash
# 安装所有 hooks（Claude Code + copilot-cli）
cmd-monitor hooks install

# 仅安装 Claude Code hooks
cmd-monitor hooks install --type claude

# 仅安装 copilot-cli hooks
cmd-monitor hooks install --type copilot
```

此命令会：
- 将 PowerShell hook 脚本写入 `.claude/settings.json`（Claude Code）
- 将 hook 配置写入 `.github/hooks/hooks.json`（copilot-cli）
- 每个 hook 事件触发时调用 `cmd-monitor hook-handler` 或 `cmd-monitor copilot-hook-handler`
- Claude 默认安装事件：`Notification`、`Stop`、`PermissionRequest`、`AskUserQuestion`
- Copilot 默认安装事件：`sessionStart`、`sessionEnd`、`userPromptSubmitted`、`preToolUse`、`postToolUse`、`errorOccurred`

### 步骤 3：启动守护进程

```bash
cmd-monitor start
```

守护进程启动后：
- 通过 WebSocket 连接飞书（无需公网 IP）
- 监听飞书消息回复
- 收到回复后自动注入到 PowerShell 终端

### 步骤 4：正常使用

1. 在 PowerShell 中启动 Claude Code 或 copilot-cli
2. 离开电脑
3. AI 停止等待时 → 飞书收到通知卡片
4. 在飞书中回复指令 → 自动注入 PowerShell 终端
5. AI 继续执行 → 循环

---

## CLI 命令参考

### `cmd-monitor` — 主命令

```bash
cmd-monitor [OPTIONS] COMMAND [ARGS]...
```

| 选项 | 缩写 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | `-c` | `config/default.toml` | 配置文件路径 |
| `--log-level` | `-l` | `INFO` | 日志级别 |

### `cmd-monitor start` — 启动守护进程

```bash
cmd-monitor start
```

连接飞书 WebSocket，监听消息并注入终端。按 `Ctrl+C` 停止。

### `cmd-monitor hooks install` — 安装 Hook 配置

```bash
cmd-monitor hooks install [OPTIONS]
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--type` | `all` | 安装哪种 hooks：`claude`、`copilot`、`all` |
| `--config-path` | 自动 | 覆盖默认配置路径 |

### `cmd-monitor hook-handler` — Claude Code Hook 处理（内部）

```bash
echo '{"hook_event_name":"Stop","session_id":"xxx","cwd":"/path"}' | cmd-monitor hook-handler --event Stop
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--event` | 是 | 事件名：`Notification`、`Stop`、`PermissionRequest` |

此命令由 Claude Code hook 自动调用，通常无需手动执行。

### `cmd-monitor copilot-hook-handler` — copilot-cli Hook 处理（内部）

```bash
echo '{"hook_event_name":"postToolUse","toolName":"bash"}' | cmd-monitor copilot-hook-handler --event postToolUse
```

| 选项 | 必填 | 说明 |
|------|------|------|
| `--event` | 是 | 事件名：`sessionStart`、`sessionEnd`、`userPromptSubmitted`、`preToolUse`、`postToolUse`、`errorOccurred` |

### `cmd-monitor monitor` — Transcript 监控

```bash
cmd-monitor monitor --transcript "C:\Users\you\Documents\PowerShell_transcript.log"
```

| 选项 | 缩写 | 说明 |
|------|------|------|
| `--transcript` | `-t` | Transcript 文件路径 |

---

## 三种监控模式

### 模式 1：Claude Code Hook（推荐）

利用 Claude Code 原生 hook 系统，在关键事件时触发通知。

**支持的事件：**
| 事件 | 触发时机 | 通知标题 |
|------|----------|----------|
| `Notification` | Claude 需要输入/提示 | Claude Code — 需要输入 |
| `Stop` | Claude 完成响应 | Claude Code — 已停止 |
| `PermissionRequest` | 权限对话框出现 | Claude Code — 权限请求 |

**工作原理：**
```
Claude Code 触发事件
  → 调用 .claude/settings.json 中配置的 PowerShell 脚本
  → 脚本执行: cmd-monitor hook-handler --event <EventName>
  → 读取 stdin JSON，解析事件
  → 通过飞书发送通知卡片
```

### 模式 2：copilot-cli Hook

利用 GitHub Copilot CLI 原生 hook 系统。

**支持的事件：**
| 事件 | 触发时机 | 通知标题 |
|------|----------|----------|
| `sessionStart` | 会话开始/恢复 | Copilot CLI — 会话开始 |
| `sessionEnd` | 会话结束 | Copilot CLI — 会话结束 |
| `userPromptSubmitted` | 用户提交 prompt | Copilot CLI — 用户提交 |
| `preToolUse` | 工具执行前 | Copilot CLI — 工具调用 |
| `postToolUse` | 工具执行后 | Copilot CLI — 工具完成 |
| `errorOccurred` | 错误发生 | Copilot CLI — 错误 |

### 模式 3：PowerShell Transcript 监控（通用）

通过 `Start-Transcript` 记录终端输出，检测空闲状态。适用于任意 CLI 工具。

```bash
# 在 PowerShell 中启动 transcript
Start-Transcript -Path "C:\transcripts\session.log"

# 在另一个终端启动监控
cmd-monitor monitor -t "C:\transcripts\session.log"
```

**工作原理：**
```
PowerShell 输出写入 transcript 文件
  → cmd-monitor monitor 轮询文件变化
  → 检测到空闲（无新输出 + 提示符模式）超过阈值
  → 通过飞书发送通知
```

---

## 状态管理与防抖

系统维护一个三状态机，避免误报和重复通知：

```
RUNNING ──[空闲超时]──▶ IDLE ──[防抖到期]──▶ WAITING ──[用户回复]──▶ RUNNING
                          │                      │
                    [新活动取消防抖]         [冷却期内抑制重复通知]
```

| 状态 | 含义 | 行为 |
|------|------|------|
| `RUNNING` | 终端活跃 | 不发送通知 |
| `IDLE` | 终端短暂空闲 | 启动防抖计时器，不立即通知 |
| `WAITING` | 确认等待用户输入 | 发送飞书通知 |

**防抖机制：**
- 空闲持续超过 `debounce_seconds`（默认 10 秒）才进入 WAITING
- 短暂停顿（如 AI 思考中）不会触发通知

**通知冷却：**
- 同一状态转换在 `notification_cooldown`（默认 60 秒）内不重复通知
- 避免 AI 反复短停时频繁打扰

---

## 指令注入

收到飞书回复后，系统将文本注入 PowerShell 终端。

### 注入方式

| 方式 | 配置值 | 原理 | 适用场景 |
|------|--------|------|----------|
| SendKeys | `sendkeys`（默认） | 剪贴板写入 + Ctrl+V 粘贴 + Enter | 通用，需窗口获得焦点 |
| Named Pipe | `namedpipe` | 写入进程 stdin | 备选方案 |

### 注入流程

```
飞书回复消息
  → 找到目标 PowerShell 窗口（按标题关键词匹配）
  → 强制前台显示（SetForegroundWindow）
  → 将文本写入剪贴板
  → 模拟 Ctrl+V 粘贴
  → 模拟 Enter 执行
```

### 配置

```toml
[inject]
method = "sendkeys"           # 注入方式
target_window = "PowerShell"  # 目标窗口标题关键词（空则匹配所有 PowerShell）
inject_delay = 0.5            # 注入后等待时间（秒）
```

### 注意事项

- SendKeys 模式需要目标窗口获得焦点，注入时会短暂切换窗口
- 多行文本会自动拆分，逐行注入
- 特殊字符（`{}`、`+`、`^`、`%`、`~` 等 SendKeys 特殊键）会自动转义

---

## 常见问题

### Q: 飞书收不到通知？

1. 检查 `config/default.toml` 中 `app_id`、`app_secret`、`receiver_id` 是否正确
2. 确认飞书应用已发布并审批通过
3. 确认已开启 WebSocket 模式的事件订阅
4. 运行 `cmd-monitor --log-level DEBUG start` 查看详细日志

### Q: Hook 没有生效？

1. 运行 `cmd-monitor hooks install` 重新安装
2. 检查 `.claude/settings.json` 中是否有 hook 配置
3. 确认 `cmd-monitor` 在 PATH 中可用（`pip install -e .` 后应该可以）
4. 手动测试：`echo '{"hook_event_name":"Stop","session_id":"test","cwd":"/tmp"}' | cmd-monitor hook-handler --event Stop`

### Q: 指令注入失败？

1. 确认 PowerShell 窗口标题包含配置的 `target_window` 关键词
2. 如果 `target_window` 为空，系统会尝试匹配所有包含 "PowerShell" 的窗口
3. 检查是否有多个 PowerShell 窗口导致匹配错误
4. 尝试将 `inject_delay` 调大（如 1.0 秒）

### Q: 如何同时监控多个终端？

目前 MVP 版本主要支持单终端。如需多终端：
- 为每个终端启动独立的 transcript 文件
- 分别运行 `cmd-monitor monitor -t <path>`
- 或等待后续版本支持多窗口管理

### Q: 通知太多/太少？

调整 `[state]` 配置：
```toml
[state]
debounce_seconds = 15.0       # 增大 → 更少通知（需要空闲更久才通知）
notification_cooldown = 120.0 # 增大 → 更少重复通知
```

### Q: 如何查看日志？

```bash
# 控制台输出（默认）
cmd-monitor start

# 指定日志级别
cmd-monitor --log-level DEBUG start

# 输出到文件
# 在 config/default.toml 中设置:
# log_file = "cmd-monitor.log"
```

### Q: 支持 macOS / Linux 吗？

MVP 版本仅支持 Windows PowerShell。指令注入依赖 Win32 API（`user32.dll`），不兼容其他平台。

---

## 架构概览

```
┌─────────────────────────────────────────────────┐
│                  Windows Host                    │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ Claude   │  │copilot-  │  │   cmd-monitor │  │
│  │ Code     │  │cli       │  │   daemon      │  │
│  │(PowerShl)│  │(PowerShl)│  │               │  │
│  └────┬─────┘  └────┬─────┘  │ ┌───────────┐ │  │
│       │              │        │ │Hook Monitor│ │  │
│       └──────────────┼───────▶│ │(Claude +   │ │  │
│       hooks/json     │hooks/  │ │copilot)    │ │  │
│                      │json    │ └─────┬─────┘ │  │
│  ┌───────────────────┘        │       │       │  │
│  │                          │ ┌───────▼─────┐ │  │
│  │  PowerShell Transcript   │ │PS Monitor  │ │  │
│  └─────────────────────────▶│ └──────┬──────┘ │  │
│                             │        │        │  │
│                             │ ┌──────▼──────┐ │  │
│                             │ │State Manager│ │  │
│                             │ └──────┬──────┘ │  │
│                             │        │        │  │
│                             │ ┌──────▼──────┐ │  │
│                             │ │Feishu Client│ │  │
│                             │ │(WebSocket)  │ │  │
│                             │ └──────┬──────┘ │  │
│                             └────────┼────────┘  │
│                                      │           │
└──────────────────────────────────────┼───────────┘
                                       │
                            ┌──────────▼──────────┐
                            │   Feishu Cloud      │
                            │   (WebSocket)       │
                            └──────────┬──────────┘
                                       │
                            ┌──────────▼──────────┐
                            │   User's Phone      │
                            │   (Feishu App)      │
                            └─────────────────────┘
```

---

*文档版本: v0.1.0 | 项目状态: MVP 完成（Phase 1-7 全部完成）*
