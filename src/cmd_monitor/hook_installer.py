"""Hook 安装模块

生成/更新 Claude Code (.claude/settings.json) 的 hooks 配置。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CLAUDE_HOOK_EVENTS = ["Notification", "Stop", "PreToolUse"]


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
    hooks: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        hooks[event] = [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": generate_hook_command(event, monitor_bin),
                    }
                ],
            }
        ]
    return {"hooks": hooks}




def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def claude_hooks_are_configured(
    config_path: Optional[str] = None,
    monitor_bin: str = "cmd-monitor",
    events: Optional[List[str]] = None,
) -> bool:
    target = Path(config_path) if config_path else Path(".claude/settings.json")
    hook_events = events or CLAUDE_HOOK_EVENTS
    data = _load_json_file(target)
    if data is None:
        return False

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False

    for event in hook_events:
        entries = hooks.get(event)
        if not isinstance(entries, list) or not entries:
            return False
        entry = entries[0]
        if not isinstance(entry, dict):
            return False
        if entry.get("matcher") != "*":
            return False
        nested_hooks = entry.get("hooks")
        if not isinstance(nested_hooks, list) or not nested_hooks:
            return False
        hook = nested_hooks[0]
        if not isinstance(hook, dict):
            return False
        if hook.get("type") != "command":
            return False
        if hook.get("command") != generate_hook_command(event, monitor_bin):
            return False
    return True


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

