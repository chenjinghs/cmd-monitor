"""Hook 事件处理模块

解析 Claude Code hook 的 stdin JSON 输入，格式化为飞书通知。
Claude Code 标准事件：Notification、Stop、PreToolUse
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
    final_message: str = ""  # transcript 里最后一条 assistant 消息（上下文）


@dataclass
class StopEvent(HookEvent):
    """Stop 事件 — Claude 完成响应"""

    stop_hook_active: bool = False
    final_message: str = ""  # assistant_output.response.output_message（可能为空）


@dataclass
class AskUserQuestionEvent(HookEvent):
    """AskUserQuestion 事件 — Claude 主动向用户提问"""

    question: str = ""
    options: list[Dict[str, Any]] = field(default_factory=list)
    final_message: str = ""


@dataclass
class SessionStartEvent(HookEvent):
    """SessionStart 事件 — 新会话开始"""

    user_message: str = ""


@dataclass
class UserPromptSubmitEvent(HookEvent):
    """UserPromptSubmit 事件 — 用户提交输入，Claude 开始执行"""

    user_message: str = ""



def _format_message_snippet(
    text: str,
    label: str,
    limit: int = 200,
    exclude: str = "",
) -> str:
    """格式化卡片里的附加消息片段。"""
    normalized_text = text.strip()
    if not normalized_text:
        return ""
    if exclude and normalized_text == exclude.strip():
        return ""
    snippet = normalized_text[:limit]
    if len(normalized_text) > limit:
        snippet += "…"
    return f"\n**{label}**: {snippet}"


def _read_last_assistant_message(transcript_path: str) -> str:
    """从 transcript 文件读取最后一条 assistant 消息文本。"""
    if not transcript_path:
        return ""
    try:
        import pathlib
        p = pathlib.Path(transcript_path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
    except Exception:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        role = obj.get("role") or obj.get("author") or obj.get("type") or ""
        if str(role).lower() != "assistant":
            continue
        content = obj.get("content") or obj.get("message") or obj.get("text") or ""
        if isinstance(content, list):
            parts = [
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(parts)
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


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
        final_message = data.get("last_assistant_message", "") or ""
        if not final_message:
            final_message = _read_last_assistant_message(data.get("transcript_path", ""))
        return NotificationEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            message=data.get("message", ""),
            final_message=final_message,
        )
    elif event_name == "Stop":
        final_message = data.get("last_assistant_message", "") or ""
        if not final_message:
            final_message = _read_last_assistant_message(data.get("transcript_path", ""))
        return StopEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            stop_hook_active=data.get("stop_hook_active", False),
            final_message=final_message,
        )
    elif event_name == "PreToolUse":
        if data.get("tool_name") != "AskUserQuestion":
            return None
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        raw_questions = tool_input.get("questions")
        first_question = raw_questions[0] if isinstance(raw_questions, list) and raw_questions else {}
        if not isinstance(first_question, dict):
            first_question = {}
        raw_options = first_question.get("options")
        options = raw_options if isinstance(raw_options, list) else []
        final_message = data.get("last_assistant_message", "") or ""
        if not final_message:
            final_message = _read_last_assistant_message(data.get("transcript_path", ""))
        question = (
            first_question.get("question", "")
            or data.get("question", "")
            or data.get("prompt", "")
            or data.get("message", "")
        )
        return AskUserQuestionEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name="AskUserQuestion",
            question=question,
            options=options,
            final_message=final_message,
        )
    elif event_name == "SessionStart":
        return SessionStartEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            user_message=data.get("user_message", ""),
        )
    elif event_name == "UserPromptSubmit":
        return UserPromptSubmitEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            user_message=data.get("user_message", ""),
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
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:400]
            if len(event.final_message) > 400:
                snippet += "…"
            msg_snippet = f"\n**上下文**: {snippet}"
        content = (
            f"**消息**: {event.message}{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, StopEvent):
        title = "Claude Code — 已停止"
        # 截取最后一条消息前 600 字符，避免卡片过长
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:600]
            if len(event.final_message) > 600:
                snippet += "…"
            msg_snippet = f"\n**结尾消息**: {snippet}"
        content = (
            f"**状态**: 任务完成{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, AskUserQuestionEvent):
        title = "Claude Code — 需要回答"
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:400]
            if len(event.final_message) > 400:
                snippet += "…"
            msg_snippet = f"\n**结尾消息**: {snippet}"
        option_labels = [
            option.get("label", "")
            for option in event.options
            if isinstance(option, dict) and option.get("label")
        ]
        options_line = f"\n**选项**: {' / '.join(option_labels)}" if option_labels else ""
        reply_hint = "\n\n**回复**: 直接在此消息下方回复您的选择即可"
        content = (
            f"**问题**: {event.question}{options_line}{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
            f"{reply_hint}"
        )
    elif isinstance(event, SessionStartEvent):
        title = "Claude Code — 会话开始"
        msg_snippet = ""
        if event.user_message:
            snippet = event.user_message[:400]
            if len(event.user_message) > 400:
                snippet += "…"
            msg_snippet = f"\n**消息**: {snippet}"
        content = (
            f"**状态**: 新会话启动{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, UserPromptSubmitEvent):
        title = "Claude Code — 正在执行"
        msg_snippet = ""
        if event.user_message:
            snippet = event.user_message[:400]
            if len(event.user_message) > 400:
                snippet += "…"
            msg_snippet = f"\n**输入**: {snippet}"
        content = (
            f"**状态**: 用户输入已提交，开始执行{msg_snippet}\n"
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
    # Only for events that require user input (Notification, AskUserQuestion);
    # StopEvent signals Claude finished, so no pending input is needed.
    if auto_replier is not None and isinstance(event, (NotificationEvent, AskUserQuestionEvent)):
        auto_replier.arm()

    return 0  # Allow Claude to continue




# --- IPC payload builders for daemon mode ---


def build_claude_ipc_event(input_json: str) -> Optional[Dict[str, Any]]:
    """把 Claude hook stdin JSON 解析后,转成 daemon 期待的 IPC payload。

    返回 None 表示解析失败 / 应跳过。
    """
    event = parse_hook_input(input_json)
    if event is None:
        return None
    title, content = format_notification(event)
    notify_role = "waiting"
    if isinstance(event, AskUserQuestionEvent):
        notify_role = "waiting_after_running"
    elif isinstance(event, (SessionStartEvent, UserPromptSubmitEvent)):
        notify_role = "running"
    return {
        "type": "hook_event",
        "session_id": event.session_id,
        "cwd": event.cwd,
        "event_name": event.hook_event_name,
        "title": title,
        "content": content,
        "notify_role": notify_role,
    }


