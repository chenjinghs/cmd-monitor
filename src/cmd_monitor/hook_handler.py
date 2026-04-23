"""Hook 事件处理模块

解析 Claude Code 和 copilot-cli hook 的 stdin JSON 输入，格式化为飞书通知。
Claude Code 事件：Notification、Stop、PermissionRequest
copilot-cli 事件：sessionStart、sessionEnd、userPromptSubmitted、preToolUse、postToolUse、errorOccurred
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from cmd_monitor.state_manager import SessionState, StateManager

logger = logging.getLogger(__name__)


@dataclass
class HookEvent:
    """Claude Code hook 事件基类"""

    session_id: str
    cwd: str
    hook_event_name: str


@dataclass
class NotificationEvent(HookEvent):
    """Notification 事件 — Claude 需要输入/权限"""

    message: str = ""


@dataclass
class StopEvent(HookEvent):
    """Stop 事件 — Claude 完成响应"""

    stop_hook_active: bool = False


@dataclass
class PermissionRequestEvent(HookEvent):
    """PermissionRequest 事件 — 权限对话框出现"""

    permission_type: str = ""
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)


# --- copilot-cli Hook Events ---


@dataclass
class CopilotHookEvent:
    """copilot-cli hook 事件基类"""

    cwd: str = ""
    timestamp: int = 0


@dataclass
class SessionStartEvent(CopilotHookEvent):
    """sessionStart 事件 — 会话开始/恢复"""

    source: str = ""  # "startup", "resume", "new"


@dataclass
class SessionEndEvent(CopilotHookEvent):
    """sessionEnd 事件 — 会话结束"""

    reason: str = ""


@dataclass
class UserPromptSubmittedEvent(CopilotHookEvent):
    """userPromptSubmitted 事件 — 用户提交提示"""

    prompt: str = ""


@dataclass
class PreToolUseEvent(CopilotHookEvent):
    """preToolUse 事件 — 工具执行前"""

    tool_name: str = ""
    tool_args: str = ""


@dataclass
class PostToolUseEvent(CopilotHookEvent):
    """postToolUse 事件 — 工具执行后"""

    tool_name: str = ""
    tool_result: str = ""


@dataclass
class ErrorOccurredEvent(CopilotHookEvent):
    """errorOccurred 事件 — 错误发生"""

    error: str = ""
    error_context: str = ""
    recoverable: bool = False


def parse_hook_input(input_json: str) -> Optional[HookEvent]:
    """解析 Claude Code hook 的 stdin JSON 输入

    Args:
        input_json: 从 stdin 读取的 JSON 字符串

    Returns:
        解析后的 HookEvent 子类，解析失败返回 None
    """
    try:
        data = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse hook input JSON")
        return None

    if not isinstance(data, dict):
        logger.error("Hook input is not a JSON object")
        return None

    event_name = data.get("hook_event_name", "")
    session_id = data.get("session_id", "")
    cwd = data.get("cwd", "")

    if event_name == "Notification":
        return NotificationEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            message=data.get("message", ""),
        )
    elif event_name == "Stop":
        return StopEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            stop_hook_active=data.get("stop_hook_active", False),
        )
    elif event_name == "PermissionRequest":
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        return PermissionRequestEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            permission_type=data.get("permission_type", ""),
            tool_name=data.get("tool_name", ""),
            tool_input=tool_input,
        )
    else:
        logger.warning("Unknown hook event: %s", event_name)
        return None


def format_notification(event: HookEvent) -> tuple[str, str]:
    """将 hook 事件格式化为飞书通知内容

    Args:
        event: 解析后的 hook 事件

    Returns:
        (title, content) 元组，用于 send_card()
    """
    if isinstance(event, NotificationEvent):
        title = "Claude Code — 需要输入"
        content = (
            f"**消息**: {event.message}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, StopEvent):
        title = "Claude Code — 已停止"
        content = (
            f"**状态**: 任务完成\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, PermissionRequestEvent):
        title = "Claude Code — 权限请求"
        content = (
            f"**类型**: {event.permission_type}\n"
            f"**工具**: {event.tool_name}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    else:
        title = "Claude Code — 未知事件"
        content = (
            f"**事件**: {event.hook_event_name}\n"
            f"**目录**: {event.cwd}"
        )
    return title, content


def handle_hook_event(
    input_json: str,
    feishu_bot: Any,
    state_manager: Optional[StateManager] = None,
    auto_replier: Optional[Any] = None,
) -> int:
    """处理 Claude Code hook 事件的主入口

    Args:
        input_json: 从 stdin 读取的 JSON 字符串
        feishu_bot: FeishuBot 实例（用于发送通知）
        state_manager: 状态管理器（可选，用于通知抑制）
        auto_replier: 自动回复管理器（可选，超时后注入预设答案）

    Returns:
        exit code: 0=allow, 2=block
    """
    event = parse_hook_input(input_json)
    if event is None:
        return 0  # Parse failure — allow Claude to continue

    # Skip if stop_hook_active (Claude already handling)
    if isinstance(event, StopEvent) and event.stop_hook_active:
        logger.info("Stop hook already active, skipping notification")
        return 0

    # State management: check if notification should be sent
    if state_manager is not None:
        should_notify = state_manager.transition(SessionState.WAITING)
        if not should_notify:
            logger.info("Notification suppressed by state manager")
            return 0

    title, content = format_notification(event)
    if feishu_bot:
        feishu_bot.send_card(title, content)
        logger.info("Hook notification sent: %s", title)
    else:
        logger.warning("FeishuBot not available, notification not sent")

    # Arm auto-replier after notification is sent.
    # Only for events that require user input (Notification, PermissionRequest);
    # StopEvent signals Claude finished, so no pending input is needed.
    if auto_replier is not None and isinstance(event, (NotificationEvent, PermissionRequestEvent)):
        auto_replier.arm()

    return 0  # Allow Claude to continue


# --- copilot-cli Hook Processing ---

COPILOT_HOOK_EVENTS = [
    "sessionStart", "sessionEnd", "userPromptSubmitted",
    "preToolUse", "postToolUse", "errorOccurred",
]


def parse_copilot_hook_input(input_json: str) -> Optional[CopilotHookEvent]:
    """解析 copilot-cli hook 的 stdin JSON 输入

    Args:
        input_json: 从 stdin 读取的 JSON 字符串

    Returns:
        解析后的 CopilotHookEvent 子类，解析失败返回 None
    """
    try:
        data = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse copilot hook input JSON")
        return None

    if not isinstance(data, dict):
        logger.error("Copilot hook input is not a JSON object")
        return None

    event_name = data.get("hook_event_name", "")
    cwd = data.get("cwd", "")
    timestamp = data.get("timestamp", 0)

    if event_name == "sessionStart":
        return SessionStartEvent(
            cwd=cwd, timestamp=timestamp,
            source=data.get("source", ""),
        )
    elif event_name == "sessionEnd":
        return SessionEndEvent(
            cwd=cwd, timestamp=timestamp,
            reason=data.get("reason", ""),
        )
    elif event_name == "userPromptSubmitted":
        return UserPromptSubmittedEvent(
            cwd=cwd, timestamp=timestamp,
            prompt=data.get("prompt", ""),
        )
    elif event_name == "preToolUse":
        return PreToolUseEvent(
            cwd=cwd, timestamp=timestamp,
            tool_name=data.get("toolName", ""),
            tool_args=data.get("toolArgs", ""),
        )
    elif event_name == "postToolUse":
        return PostToolUseEvent(
            cwd=cwd, timestamp=timestamp,
            tool_name=data.get("toolName", ""),
            tool_result=data.get("toolResult", ""),
        )
    elif event_name == "errorOccurred":
        return ErrorOccurredEvent(
            cwd=cwd, timestamp=timestamp,
            error=data.get("error", ""),
            error_context=data.get("errorContext", ""),
            recoverable=data.get("recoverable", False),
        )
    else:
        logger.warning("Unknown copilot hook event: %s", event_name)
        return None


def format_copilot_notification(event: CopilotHookEvent) -> tuple[str, str]:
    """将 copilot-cli hook 事件格式化为飞书通知内容

    Args:
        event: 解析后的 copilot hook 事件

    Returns:
        (title, content) 元组，用于 send_card()
    """
    if isinstance(event, SessionStartEvent):
        title = "Copilot CLI — 会话开始"
        content = f"**来源**: {event.source}\n**目录**: {event.cwd}"
    elif isinstance(event, SessionEndEvent):
        title = "Copilot CLI — 会话结束"
        content = f"**原因**: {event.reason}\n**目录**: {event.cwd}"
    elif isinstance(event, UserPromptSubmittedEvent):
        title = "Copilot CLI — 用户提交"
        content = f"**提示**: {event.prompt[:100]}\n**目录**: {event.cwd}"
    elif isinstance(event, PreToolUseEvent):
        title = "Copilot CLI — 工具调用"
        content = (
            f"**工具**: {event.tool_name}\n"
            f"**参数**: {event.tool_args[:100]}\n"
            f"**目录**: {event.cwd}"
        )
    elif isinstance(event, PostToolUseEvent):
        title = "Copilot CLI — 工具完成"
        content = (
            f"**工具**: {event.tool_name}\n"
            f"**结果**: {event.tool_result[:100]}\n"
            f"**目录**: {event.cwd}"
        )
    elif isinstance(event, ErrorOccurredEvent):
        title = "Copilot CLI — 错误"
        content = (
            f"**错误**: {event.error[:100]}\n"
            f"**上下文**: {event.error_context}\n"
            f"**可恢复**: {event.recoverable}"
        )
    else:
        title = "Copilot CLI — 未知事件"
        content = f"**目录**: {event.cwd}"
    return title, content


def handle_copilot_hook_event(
    input_json: str,
    feishu_bot: Any,
    state_manager: Optional[StateManager] = None,
    auto_replier: Optional[Any] = None,
) -> int:
    """处理 copilot-cli hook 事件的主入口

    Args:
        input_json: 从 stdin 读取的 JSON 字符串
        feishu_bot: FeishuBot 实例（用于发送通知）
        state_manager: 状态管理器（可选，用于通知抑制）
        auto_replier: 自动回复管理器（可选，超时后注入预设答案）

    Returns:
        exit code: 0=allow
    """
    event = parse_copilot_hook_input(input_json)
    if event is None:
        return 0

    # State management: session-start/prompt → RUNNING, others → check WAITING
    if state_manager is not None:
        if isinstance(event, (SessionStartEvent, UserPromptSubmittedEvent)):
            state_manager.transition(SessionState.RUNNING)
        elif isinstance(event, (PostToolUseEvent, ErrorOccurredEvent)):
            should_notify = state_manager.transition(SessionState.WAITING)
            if not should_notify:
                logger.info("Copilot notification suppressed by state manager")
                return 0

    title, content = format_copilot_notification(event)
    if feishu_bot:
        feishu_bot.send_card(title, content)
        logger.info("Copilot hook notification sent: %s", title)
    else:
        logger.warning("FeishuBot not available, notification not sent")

    # Arm auto-replier after notification is sent.
    # For copilot hooks all events may need a reply, so we arm unconditionally.
    if auto_replier is not None:
        auto_replier.arm()

    return 0
