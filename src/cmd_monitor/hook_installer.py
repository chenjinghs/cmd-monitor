"""Hook 安装模块

生成/更新 Claude Code (.claude/settings.json) 和 copilot-cli (.github/hooks/hooks.json) 的 hooks 配置。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CLAUDE_HOOK_EVENTS = ["Notification", "Stop", "PermissionRequest"]


def generate_hook_command(
    event_name: str,
    monitor_bin: str = "cmd-monitor",
) -> str:
    """生成单个 hook 事件的 PowerShell 命令

    Args:
        event_name: 事件名称 (Notification, Stop, PermissionRequest)
        monitor_bin: cmd-monitor CLI 命令名

    Returns:
        PowerShell 命令字符串
    """
    return f'powershell.exe -Command "& {monitor_bin} hook-handler --event {event_name}"'


def generate_hooks_config(
    events: List[str],
    monitor_bin: str = "cmd-monitor",
) -> Dict[str, Any]:
    """生成完整的 hooks 配置字典

    Args:
        events: 要监听的事件列表
        monitor_bin: cmd-monitor CLI 命令名

    Returns:
        hooks 配置字典（包含 "hooks" key）
    """
    hooks: Dict[str, List[Dict[str, str]]] = {}
    for event in events:
        hooks[event] = [
            {
                "type": "command",
                "command": generate_hook_command(event, monitor_bin),
            }
        ]
    return {"hooks": hooks}


def install_hooks(
    config_path: Optional[str] = None,
    monitor_bin: str = "cmd-monitor",
    events: Optional[List[str]] = None,
) -> bool:
    """安装 Claude Code hooks 到 .claude/settings.json

    Args:
        config_path: settings.json 路径，默认 .claude/settings.json
        monitor_bin: cmd-monitor CLI 命令名
        events: 要监听的事件列表，默认全部

    Returns:
        True if installed successfully
    """
    target = Path(config_path) if config_path else Path(".claude/settings.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    hook_events = events or CLAUDE_HOOK_EVENTS

    # Load existing config or create new
    existing: Dict[str, Any] = {}
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to parse existing %s, will overwrite", target)

    # Generate new hooks config
    new_hooks = generate_hooks_config(hook_events, monitor_bin)

    # Merge — keep existing non-hook keys, overwrite hooks
    existing.update(new_hooks)

    # Write back
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        logger.info("Hooks installed to %s", target)
        return True
    except OSError as e:
        logger.error("Failed to write hooks config: %s", e)
        return False


# --- copilot-cli Hook Installation ---

COPILOT_HOOK_EVENTS = [
    "sessionStart", "sessionEnd", "userPromptSubmitted",
    "preToolUse", "postToolUse", "errorOccurred",
]


def generate_copilot_hook_command(
    event_name: str,
    monitor_bin: str = "cmd-monitor",
) -> str:
    """生成单个 copilot-cli hook 事件的 PowerShell 命令

    Args:
        event_name: 事件名称
        monitor_bin: cmd-monitor CLI 命令名

    Returns:
        PowerShell 命令字符串
    """
    return f"{monitor_bin} copilot-hook-handler --event {event_name}"


def generate_copilot_hooks_config(
    events: List[str],
    monitor_bin: str = "cmd-monitor",
) -> Dict[str, Any]:
    """生成完整的 copilot-cli hooks 配置字典

    Args:
        events: 要监听的事件列表
        monitor_bin: cmd-monitor CLI 命令名

    Returns:
        hooks 配置字典（包含 "version" 和 "hooks" key）
    """
    hooks: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        hooks[event] = [
            {
                "type": "command",
                "powershell": generate_copilot_hook_command(event, monitor_bin),
                "timeoutSec": 30,
            }
        ]
    return {"version": 1, "hooks": hooks}


def install_copilot_hooks(
    config_dir: Optional[str] = None,
    monitor_bin: str = "cmd-monitor",
    events: Optional[List[str]] = None,
) -> bool:
    """安装 copilot-cli hooks 到 .github/hooks/hooks.json

    Args:
        config_dir: hooks 目录路径，默认 .github/hooks/
        monitor_bin: cmd-monitor CLI 命令名
        events: 要监听的事件列表，默认全部

    Returns:
        True if installed successfully
    """
    target_dir = Path(config_dir) if config_dir else Path(".github/hooks")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "hooks.json"

    hook_events = events or COPILOT_HOOK_EVENTS

    # Load existing config or create new
    existing: Dict[str, Any] = {}
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to parse existing %s, will overwrite", target)

    # Generate new hooks config
    new_config = generate_copilot_hooks_config(hook_events, monitor_bin)

    # Merge — keep existing non-hook keys, overwrite hooks and version
    existing.update(new_config)

    # Write back
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        logger.info("Copilot hooks installed to %s", target)
        return True
    except OSError as e:
        logger.error("Failed to write copilot hooks config: %s", e)
        return False
