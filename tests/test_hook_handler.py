"""Hook handler 测试"""

import json

from cmd_monitor.hook_handler import (
    AskUserQuestionEvent,
    NotificationEvent,
    SessionStartEvent,
    StopEvent,
    UserPromptSubmitEvent,
    format_notification,
    parse_hook_input,
)


# --- parse_hook_input tests ---


def test_parse_notification_event() -> None:
    data = {
        "session_id": "sess_123",
        "cwd": "/home/user/project",
        "hook_event_name": "Notification",
        "message": "Task completed successfully",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, NotificationEvent)
    assert event.session_id == "sess_123"
    assert event.cwd == "/home/user/project"
    assert event.message == "Task completed successfully"


def test_parse_stop_event() -> None:
    data = {
        "session_id": "sess_456",
        "cwd": "C:\\Users\\dev",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, StopEvent)
    assert event.session_id == "sess_456"
    assert event.stop_hook_active is False


def test_parse_stop_event_active() -> None:
    data = {
        "session_id": "sess_789",
        "cwd": "/project",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, StopEvent)
    assert event.stop_hook_active is True


def test_parse_pre_tool_use_ask_user_question_event() -> None:
    data = {
        "session_id": "sess_pre_ask",
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {
                    "question": "下一步做什么？",
                    "options": [{"label": "查日志"}, {"label": "继续测试"}],
                }
            ]
        },
        "last_assistant_message": "建议先看最新日志。",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, AskUserQuestionEvent)
    assert event.question == "下一步做什么？"
    assert event.options == [
        {"label": "查日志"},
        {"label": "继续测试"},
    ]
    assert event.final_message == "建议先看最新日志。"


def test_parse_pre_tool_use_non_ask_user_question_returns_none() -> None:
    """PreToolUse 但 tool_name 不是 AskUserQuestion — 不通知"""
    data = {
        "session_id": "sess_pre_other",
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
    }
    event = parse_hook_input(json.dumps(data))
    assert event is None


def test_parse_invalid_json() -> None:
    result = parse_hook_input("not json{{{")
    assert result is None


def test_parse_empty_string() -> None:
    result = parse_hook_input("")
    assert result is None


def test_parse_non_object_json() -> None:
    result = parse_hook_input('"just a string"')
    assert result is None


def test_parse_session_start_event() -> None:
    data = {
        "session_id": "sess_start",
        "cwd": "/workspace",
        "hook_event_name": "SessionStart",
        "user_message": "Hello Claude",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, SessionStartEvent)
    assert event.session_id == "sess_start"
    assert event.cwd == "/workspace"
    assert event.user_message == "Hello Claude"


def test_parse_session_start_without_message() -> None:
    data = {
        "session_id": "sess_start2",
        "cwd": "/project",
        "hook_event_name": "SessionStart",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, SessionStartEvent)
    assert event.user_message == ""


def test_parse_user_prompt_submit_event() -> None:
    data = {
        "session_id": "sess_prompt",
        "cwd": "/code",
        "hook_event_name": "UserPromptSubmit",
        "user_message": "请帮我写个排序算法",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, UserPromptSubmitEvent)
    assert event.session_id == "sess_prompt"
    assert event.cwd == "/code"
    assert event.user_message == "请帮我写个排序算法"


def test_parse_unknown_event() -> None:
    data = {
        "session_id": "sess_xxx",
        "cwd": "/project",
        "hook_event_name": "UnknownEvent",
    }
    result = parse_hook_input(json.dumps(data))
    assert result is None


def test_parse_missing_event_name() -> None:
    data = {
        "session_id": "sess_yyy",
        "cwd": "/project",
    }
    result = parse_hook_input(json.dumps(data))
    assert result is None


# --- format_notification tests ---


def test_format_notification_event() -> None:
    event = NotificationEvent(
        session_id="sess_12345678",
        cwd="/home/user",
        hook_event_name="Notification",
        message="请确认权限",
    )
    title, content = format_notification(event)
    assert "需要输入" in title
    assert "请确认权限" in content
    assert "/home/user" in content
    assert event.session_id[:8] in content


def test_format_stop_event() -> None:
    event = StopEvent(
        session_id="sess_abcdefgh",
        cwd="/workspace",
        hook_event_name="Stop",
        stop_hook_active=False,
    )
    title, content = format_notification(event)
    assert "已停止" in title
    assert "任务完成" in content
    assert "/workspace" in content


def test_format_ask_user_question_event() -> None:
    event = AskUserQuestionEvent(
        session_id="sess_ask1234",
        cwd="/code",
        hook_event_name="AskUserQuestion",
        question="下一步做什么？",
        options=[{"label": "查日志"}, {"label": "继续测试"}],
        final_message="建议先确认最近一次输出。",
    )
    title, content = format_notification(event)
    assert "需要回答" in title
    assert "下一步做什么？" in content
    assert "查日志" in content
    assert "继续测试" in content
    assert "**结尾消息**" in content
    assert "建议先确认最近一次输出。" in content
    assert "**上下文**" not in content
    assert "/code" in content


def test_format_notification_uses_700_char_context() -> None:
    final_message = "c" * 800
    event = NotificationEvent(
        session_id="sess_12345678",
        cwd="/home/user",
        hook_event_name="Notification",
        message="请确认权限",
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "需要输入" in title
    assert "c" * 700 in content
    assert "c" * 701 not in content
    assert "…" in content
    assert "**上下文**" in content


def test_format_stop_event_uses_900_char_snippet() -> None:
    final_message = "a" * 1000
    event = StopEvent(
        session_id="sess_abcdefgh",
        cwd="/workspace",
        hook_event_name="Stop",
        stop_hook_active=False,
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "已停止" in title
    assert "a" * 900 in content
    assert "a" * 901 not in content
    assert "…" in content


def test_format_ask_user_question_uses_700_char_snippet() -> None:
    final_message = "b" * 800
    event = AskUserQuestionEvent(
        session_id="sess_ask1234",
        cwd="/code",
        hook_event_name="AskUserQuestion",
        question="下一步做什么？",
        options=[{"label": "继续"}],
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "需要回答" in title
    assert "b" * 700 in content
    assert "b" * 701 not in content
    assert "…" in content


def test_format_session_start_event() -> None:
    event = SessionStartEvent(
        session_id="sess_start1234",
        cwd="/workspace",
        hook_event_name="SessionStart",
        user_message="Hello Claude",
    )
    title, content = format_notification(event)
    assert "会话开始" in title
    assert "新会话启动" in content
    assert "Hello Claude" in content
    assert "/workspace" in content


def test_format_session_start_without_message() -> None:
    event = SessionStartEvent(
        session_id="sess_start5678",
        cwd="/project",
        hook_event_name="SessionStart",
    )
    title, content = format_notification(event)
    assert "会话开始" in title
    assert "新会话启动" in content
    assert "**消息**" not in content
    assert "/project" in content


def test_format_user_prompt_submit_event() -> None:
    event = UserPromptSubmitEvent(
        session_id="sess_prompt1234",
        cwd="/code",
        hook_event_name="UserPromptSubmit",
        user_message="请帮我写个排序算法",
    )
    title, content = format_notification(event)
    assert "正在执行" in title
    assert "用户输入已提交" in content
    assert "请帮我写个排序算法" in content
    assert "/code" in content


def test_format_user_prompt_submit_uses_1600_char_snippet() -> None:
    user_message = "x" * 1700
    event = UserPromptSubmitEvent(
        session_id="sess_prompt1234",
        cwd="/code",
        hook_event_name="UserPromptSubmit",
        user_message=user_message,
    )
    title, content = format_notification(event)
    assert "正在执行" in title
    assert "x" * 1600 in content
    assert "x" * 1601 not in content
    assert "…" in content
