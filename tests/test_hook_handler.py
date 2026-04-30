"""Hook handler 测试"""

import json
from unittest.mock import MagicMock

from cmd_monitor.hook_handler import (
    AskUserQuestionEvent,
    HookEvent,
    NotificationEvent,
    StopEvent,
    format_notification,
    handle_hook_event,
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


def test_format_notification_uses_400_char_context() -> None:
    final_message = "c" * 500
    event = NotificationEvent(
        session_id="sess_12345678",
        cwd="/home/user",
        hook_event_name="Notification",
        message="请确认权限",
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "需要输入" in title
    assert "c" * 400 in content
    assert "c" * 401 not in content
    assert "…" in content
    assert "**上下文**" in content


def test_format_stop_event_uses_600_char_snippet() -> None:
    final_message = "a" * 700
    event = StopEvent(
        session_id="sess_abcdefgh",
        cwd="/workspace",
        hook_event_name="Stop",
        stop_hook_active=False,
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "已停止" in title
    assert "a" * 600 in content
    assert "a" * 601 not in content
    assert "…" in content


def test_format_ask_user_question_uses_400_char_snippet() -> None:
    final_message = "b" * 500
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
    assert "b" * 400 in content
    assert "b" * 401 not in content
    assert "…" in content


# --- handle_hook_event tests ---


def test_handle_notification_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_123",
        "cwd": "/project",
        "hook_event_name": "Notification",
        "message": "需要确认",
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "需要输入" in title


def test_handle_stop_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_456",
        "cwd": "/project",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()


def test_handle_stop_active_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_789",
        "cwd": "/project",
        "hook_event_name": "Stop",
        "stop_hook_active": True,
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()


def test_handle_pre_tool_use_ask_user_question_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
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
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "需要回答" in title
    assert "下一步做什么？" in content


def test_handle_pre_tool_use_ask_user_question_arms_auto_reply() -> None:
    bot = MagicMock()
    auto_replier = MagicMock()
    input_json = json.dumps({
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
    })
    exit_code = handle_hook_event(input_json, bot, auto_replier=auto_replier)
    assert exit_code == 0
    auto_replier.arm.assert_called_once()


def test_handle_pre_tool_use_non_ask_user_question_skips() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_pre_other",
        "cwd": "/workspace",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_not_called()


def test_handle_no_bot_returns_zero() -> None:
    input_json = json.dumps({
        "session_id": "sess_xxx",
        "cwd": "/project",
        "hook_event_name": "Notification",
        "message": "test",
    })
    exit_code = handle_hook_event(input_json, None)
    assert exit_code == 0


def test_handle_empty_input_returns_zero() -> None:
    bot = MagicMock()
    exit_code = handle_hook_event("", bot)
    assert exit_code == 0
    bot.send_card.assert_not_called()
