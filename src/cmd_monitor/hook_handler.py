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
    final_message: str = ""  # transcript 里最后一条 assistant 消息（上下文）


@dataclass
class StopEvent(HookEvent):
    """Stop 事件 — Claude 完成响应"""

    stop_hook_active: bool = False
    final_message: str = ""  # assistant_output.response.output_message（可能为空）


@dataclass
class PermissionRequestEvent(HookEvent):
    """PermissionRequest 事件 — 权限对话框出现"""

    permission_type: str = ""
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    final_message: str = ""  # transcript 里最后一条 assistant 消息（上下文）


@dataclass
class AskUserQuestionEvent(HookEvent):
    """AskUserQuestion 事件 — Claude 主动向用户提问"""

    question: str = ""
    options: list[Dict[str, Any]] = field(default_factory=list)
    final_message: str = ""


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
class CopilotAskUserQuestionEvent(CopilotHookEvent):
    """Copilot ask-user 事件 — Copilot 主动向用户提问"""

    question: str = ""
    options: list[Dict[str, Any]] = field(default_factory=list)
    source_event_name: str = ""


@dataclass
class ErrorOccurredEvent(CopilotHookEvent):
    """errorOccurred 事件 — 错误发生"""

    error: str = ""
    error_context: str = ""
    recoverable: bool = False



def _extract_copilot_question_from_text(text: str) -> tuple[str, list[Dict[str, Any]]]:
    """从 copilot tool 文本负载中提取 ask-user 问题与选项。"""
    if not text:
        return "", []

    candidates: list[Any] = [text]
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if parsed is not None:
        candidates.append(parsed)

    def _from_obj(obj: Any) -> tuple[str, list[Dict[str, Any]]]:
        if isinstance(obj, dict):
            tool_name = str(obj.get("toolName") or obj.get("tool_name") or "")
            if tool_name.lower() in {"askuserquestion", "ask-user", "ask_user_question", "ask_user"}:
                question = str(
                    obj.get("question")
                    or obj.get("prompt")
                    or obj.get("message")
                    or ""
                )
                raw_options = obj.get("options")
                options = raw_options if isinstance(raw_options, list) else []
                if question:
                    return question, options

            raw_questions = obj.get("questions")
            if isinstance(raw_questions, list) and raw_questions:
                first_question = raw_questions[0]
                if isinstance(first_question, dict):
                    question = str(first_question.get("question") or "")
                    raw_options = first_question.get("options")
                    options = raw_options if isinstance(raw_options, list) else []
                    if question:
                        return question, options

            question = str(obj.get("question") or obj.get("prompt") or obj.get("message") or "")
            raw_options = obj.get("options")
            options = raw_options if isinstance(raw_options, list) else []
            if question and (options or any(k in obj for k in ("questions", "toolName", "tool_name"))):
                return question, options

            for key in ("toolArgs", "tool_args", "toolResult", "tool_result", "input", "result", "payload"):
                nested = obj.get(key)
                if isinstance(nested, str):
                    nested_question, nested_options = _extract_copilot_question_from_text(nested)
                    if nested_question:
                        return nested_question, nested_options
                elif isinstance(nested, dict):
                    nested_question, nested_options = _from_obj(nested)
                    if nested_question:
                        return nested_question, nested_options
        return "", []

    for candidate in candidates:
        question, options = _from_obj(candidate)
        if question:
            return question, options
    return "", []


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
    elif event_name == "PermissionRequest":
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        final_message = _read_last_assistant_message(data.get("transcript_path", ""))
        return PermissionRequestEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            permission_type=data.get("permission_type", ""),
            tool_name=data.get("tool_name", ""),
            tool_input=tool_input,
            final_message=final_message,
        )
    elif event_name == "PreToolUse" and data.get("tool_name") == "AskUserQuestion":
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
    elif event_name == "AskUserQuestion":
        raw_options = data.get("options")
        options = raw_options if isinstance(raw_options, list) else []
        final_message = data.get("last_assistant_message", "") or ""
        if not final_message:
            final_message = _read_last_assistant_message(data.get("transcript_path", ""))
        question = data.get("question", "") or data.get("prompt", "") or data.get("message", "")
        return AskUserQuestionEvent(
            session_id=session_id,
            cwd=cwd,
            hook_event_name=event_name,
            question=question,
            options=options,
            final_message=final_message,
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
            snippet = event.final_message[:200]
            if len(event.final_message) > 200:
                snippet += "…"
            msg_snippet = f"\n**上下文**: {snippet}"
        content = (
            f"**消息**: {event.message}{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, StopEvent):
        title = "Claude Code — 已停止"
        # 截取最后一条消息前 400 字符，避免卡片过长
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:400]
            if len(event.final_message) > 400:
                snippet += "…"
            msg_snippet = f"\n**结尾消息**: {snippet}"
        content = (
            f"**状态**: 任务完成{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, PermissionRequestEvent):
        title = "Claude Code — 权限请求"
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:200]
            if len(event.final_message) > 200:
                snippet += "…"
            msg_snippet = f"\n**上下文**: {snippet}"
        content = (
            f"**类型**: {event.permission_type}\n"
            f"**工具**: {event.tool_name}{msg_snippet}\n"
            f"**目录**: {event.cwd}\n"
            f"**会话**: {event.session_id[:8]}"
        )
    elif isinstance(event, AskUserQuestionEvent):
        title = "Claude Code — 需要回答"
        msg_snippet = ""
        if event.final_message:
            snippet = event.final_message[:200]
            if len(event.final_message) > 200:
                snippet += "…"
            msg_snippet = f"\n**结尾消息**: {snippet}"
        option_labels = [
            option.get("label", "")
            for option in event.options
            if isinstance(option, dict) and option.get("label")
        ]
        options_line = f"\n**选项**: {' / '.join(option_labels)}" if option_labels else ""
        content = (
            f"**问题**: {event.question}{options_line}{msg_snippet}\n"
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
    # Only for events that require user input (Notification, PermissionRequest, AskUserQuestion);
    # StopEvent signals Claude finished, so no pending input is needed.
    if auto_replier is not None and isinstance(event, (NotificationEvent, PermissionRequestEvent, AskUserQuestionEvent)):
        auto_replier.arm()

    return 0  # Allow Claude to continue


# --- copilot-cli Hook Processing ---

COPILOT_HOOK_EVENTS = [
    "sessionStart", "sessionEnd", "userPromptSubmitted",
    "preToolUse", "postToolUse", "errorOccurred",
]


def _parse_copilot_ask_user_event(
    event_name: str,
    cwd: str,
    timestamp: int,
    tool_name: str,
    payload_text: str,
) -> Optional[CopilotAskUserQuestionEvent]:
    question, options = _extract_copilot_question_from_text(payload_text)
    tool_name_normalized = tool_name.strip().lower()
    if not question and tool_name_normalized not in {"askuserquestion", "ask-user", "ask_user_question", "ask_user"}:
        return None
    return CopilotAskUserQuestionEvent(
        cwd=cwd,
        timestamp=timestamp,
        question=question,
        options=options,
        source_event_name=event_name,
    )


def parse_copilot_hook_input(
    input_json: str,
    fallback_event_name: str = "",
) -> Optional[CopilotHookEvent]:
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

    event_name = data.get("hook_event_name") or fallback_event_name
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
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", "")
        ask_user_event = _parse_copilot_ask_user_event(
            event_name=event_name,
            cwd=cwd,
            timestamp=timestamp,
            tool_name=tool_name,
            payload_text=tool_args,
        )
        if ask_user_event is not None:
            return ask_user_event
        return PreToolUseEvent(
            cwd=cwd, timestamp=timestamp,
            tool_name=tool_name,
            tool_args=tool_args,
        )
    elif event_name == "postToolUse":
        tool_name = data.get("toolName", "")
        tool_result = data.get("toolResult", "")
        ask_user_event = _parse_copilot_ask_user_event(
            event_name=event_name,
            cwd=cwd,
            timestamp=timestamp,
            tool_name=tool_name,
            payload_text=tool_result,
        )
        if ask_user_event is not None:
            return ask_user_event
        return PostToolUseEvent(
            cwd=cwd, timestamp=timestamp,
            tool_name=tool_name,
            tool_result=tool_result,
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
            f"**参数**: {event.tool_args[:200]}\n"
            f"**目录**: {event.cwd}"
        )
    elif isinstance(event, PostToolUseEvent):
        title = "Copilot CLI — 工具完成"
        content = (
            f"**工具**: {event.tool_name}\n"
            f"**结果**: {event.tool_result[:200]}\n"
            f"**目录**: {event.cwd}"
        )
    elif isinstance(event, CopilotAskUserQuestionEvent):
        title = "Copilot CLI — 需要回答"
        option_labels = [
            option.get("label", "")
            for option in event.options
            if isinstance(option, dict) and option.get("label")
        ]
        options_line = f"\n**选项**: {' / '.join(option_labels)}" if option_labels else ""
        content = f"**问题**: {event.question}{options_line}\n**目录**: {event.cwd}"
    elif isinstance(event, ErrorOccurredEvent):
        title = "Copilot CLI — 错误"
        content = (
            f"**错误**: {event.error[:200]}\n"
            f"**上下文**: {event.error_context}\n"
            f"**可恢复**: {event.recoverable}\n"
            f"**目录**: {event.cwd}"
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
    fallback_event_name: str = "",
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
    event = parse_copilot_hook_input(input_json, fallback_event_name=fallback_event_name)
    if event is None:
        return 0

    # State management: session-start/prompt → RUNNING, ask-user/question → WAITING, others → check WAITING
    if state_manager is not None:
        if isinstance(event, (SessionStartEvent, UserPromptSubmittedEvent, PreToolUseEvent)):
            state_manager.transition(SessionState.RUNNING)
        elif isinstance(event, CopilotAskUserQuestionEvent):
            should_notify = state_manager.transition(SessionState.WAITING)
            if not should_notify:
                logger.info("Copilot ask-user notification suppressed by state manager")
                return 0
        elif isinstance(event, (SessionEndEvent, PostToolUseEvent, ErrorOccurredEvent)):
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

    if auto_replier is not None and isinstance(event, (SessionEndEvent, PostToolUseEvent, ErrorOccurredEvent, CopilotAskUserQuestionEvent)):
        auto_replier.arm()

    return 0


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
    return {
        "type": "hook_event",
        "session_id": event.session_id,
        "cwd": event.cwd,
        "event_name": event.hook_event_name,
        "title": title,
        "content": content,
        "notify_role": notify_role,
    }


def build_copilot_ipc_event(
    input_json: str,
    fallback_event_name: str = "",
) -> Optional[Dict[str, Any]]:
    """copilot-cli hook stdin JSON → IPC payload。"""
    event = parse_copilot_hook_input(input_json, fallback_event_name=fallback_event_name)
    if event is None:
        return None

    # Map event class to notify_role for daemon-side state machine
    if isinstance(event, (SessionStartEvent, UserPromptSubmittedEvent, PreToolUseEvent)):
        role = "running"
    elif isinstance(event, CopilotAskUserQuestionEvent):
        role = "waiting_after_running"
    elif isinstance(event, (SessionEndEvent, PostToolUseEvent, ErrorOccurredEvent)):
        role = "waiting"
    else:
        role = "skip"

    title, content = format_copilot_notification(event)
    # copilot 事件没有 session_id;用 cwd+hook_event_name 派生一个稳定 id,
    # 让 daemon 能为同一终端的多次事件复用 token。
    derived_id = (
        f"copilot:{event.cwd}"
        if event.cwd
        else f"copilot:{getattr(event, 'timestamp', '0')}"
    )
    return {
        "type": "hook_event",
        "session_id": derived_id,
        "cwd": event.cwd,
        "event_name": getattr(event, "__class__").__name__,
        "title": title,
        "content": content,
        "notify_role": role,
    }
