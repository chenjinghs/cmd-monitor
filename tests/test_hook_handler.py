"""Hook handler 测试"""

import json
from unittest.mock import MagicMock

from cmd_monitor.hook_handler import (
    AskUserQuestionEvent,
    CopilotAskUserQuestionEvent,
    ErrorOccurredEvent,
    HookEvent,
    NotificationEvent,
    PermissionRequestEvent,
    PostToolUseEvent,
    PreToolUseEvent,
    SessionEndEvent,
    SessionStartEvent,
    StopEvent,
    UserPromptSubmittedEvent,
    format_copilot_notification,
    format_notification,
    handle_copilot_hook_event,
    handle_hook_event,
    parse_copilot_hook_input,
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


def test_parse_permission_request_event() -> None:
    data = {
        "session_id": "sess_abc",
        "cwd": "/workspace",
        "hook_event_name": "PermissionRequest",
        "permission_type": "tool",
        "tool_name": "bash",
        "tool_input": {"command": "ls -la"},
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, PermissionRequestEvent)
    assert event.permission_type == "tool"
    assert event.tool_name == "bash"
    assert event.tool_input == {"command": "ls -la"}


def test_parse_permission_request_missing_tool_input() -> None:
    data = {
        "session_id": "sess_def",
        "cwd": "/project",
        "hook_event_name": "PermissionRequest",
        "permission_type": "tool",
        "tool_name": "write",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, PermissionRequestEvent)
    assert event.tool_input == {}


def test_parse_ask_user_question_event() -> None:
    data = {
        "session_id": "sess_ask",
        "cwd": "/workspace",
        "hook_event_name": "AskUserQuestion",
        "question": "下一步做什么？",
        "options": [
            {"label": "查日志"},
            {"label": "继续测试"},
        ],
        "last_assistant_message": "请先确认当前方案。",
    }
    event = parse_hook_input(json.dumps(data))
    assert isinstance(event, AskUserQuestionEvent)
    assert event.question == "下一步做什么？"
    assert event.options == [
        {"label": "查日志"},
        {"label": "继续测试"},
    ]
    assert event.final_message == "请先确认当前方案。"


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


def test_parse_copilot_post_tool_use_uses_cli_event_name_fallback() -> None:
    data = {
        "cwd": "/project",
        "timestamp": 1,
        "toolName": "bash",
        "toolResult": "ok",
    }
    event = parse_copilot_hook_input(json.dumps(data), fallback_event_name="postToolUse")
    assert isinstance(event, PostToolUseEvent)
    assert event.tool_name == "bash"
    assert event.tool_result == "ok"


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


def test_format_permission_request_event() -> None:
    event = PermissionRequestEvent(
        session_id="sess_99999",
        cwd="/code",
        hook_event_name="PermissionRequest",
        permission_type="tool",
        tool_name="bash",
        tool_input={"command": "rm -rf /tmp/test"},
    )
    title, content = format_notification(event)
    assert "权限请求" in title
    assert "bash" in content
    assert "tool" in content
    assert "/code" in content


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


def test_copilot_format_post_tool_use_has_more_context() -> None:
    event = PostToolUseEvent(cwd="/code", tool_name="edit", tool_result="x" * 150)
    title, content = format_copilot_notification(event)
    assert "工具完成" in title
    assert "/code" in content
    assert "edit" in content
    assert "x" * 120 in content


def test_format_stop_event_uses_400_char_snippet() -> None:
    final_message = "a" * 500
    event = StopEvent(
        session_id="sess_abcdefgh",
        cwd="/workspace",
        hook_event_name="Stop",
        stop_hook_active=False,
        final_message=final_message,
    )
    title, content = format_notification(event)
    assert "已停止" in title
    assert "a" * 400 in content
    assert "a" * 401 not in content
    assert "…" in content




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


def test_handle_permission_request_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_abc",
        "cwd": "/workspace",
        "hook_event_name": "PermissionRequest",
        "permission_type": "tool",
        "tool_name": "bash",
        "tool_input": {"command": "echo hello"},
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "权限请求" in title
    assert "bash" in content


def test_handle_ask_user_question_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_ask",
        "cwd": "/workspace",
        "hook_event_name": "AskUserQuestion",
        "question": "下一步做什么？",
        "options": [{"label": "查日志"}, {"label": "继续测试"}],
    })
    exit_code = handle_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "需要回答" in title
    assert "下一步做什么？" in content


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


def test_handle_ask_user_question_arms_auto_reply() -> None:
    bot = MagicMock()
    auto_replier = MagicMock()
    input_json = json.dumps({
        "session_id": "sess_ask",
        "cwd": "/workspace",
        "hook_event_name": "AskUserQuestion",
        "question": "下一步做什么？",
        "options": [{"label": "查日志"}, {"label": "继续测试"}],
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


# --- parse_copilot_hook_input tests ---


def test_copilot_parse_session_start() -> None:
    data = {
        "hook_event_name": "sessionStart",
        "cwd": "/project",
        "timestamp": 1704614400000,
        "source": "startup",
    }
    event = parse_copilot_hook_input(json.dumps(data))
    assert isinstance(event, SessionStartEvent)
    assert event.cwd == "/project"
    assert event.source == "startup"
    assert event.timestamp == 1704614400000


def test_copilot_parse_session_end() -> None:
    data = {
        "hook_event_name": "sessionEnd",
        "cwd": "/workspace",
        "reason": "user_exit",
    }
    event = parse_copilot_hook_input(json.dumps(data))
    assert isinstance(event, SessionEndEvent)
    assert event.reason == "user_exit"


def test_copilot_parse_user_prompt_submitted() -> None:
    data = {
        "hook_event_name": "userPromptSubmitted",
        "cwd": "/code",
        "prompt": "fix the bug in main.py",
    }
    event = parse_copilot_hook_input(json.dumps(data))
    assert isinstance(event, UserPromptSubmittedEvent)
    assert event.prompt == "fix the bug in main.py"


def test_copilot_parse_pre_tool_use() -> None:
    data = {
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "bash",
        "toolArgs": '{"command": "ls -la"}',
    }
    event = parse_copilot_hook_input(json.dumps(data))
    assert isinstance(event, PreToolUseEvent)
    assert event.tool_name == "bash"
    assert event.tool_args == '{"command": "ls -la"}'




def test_copilot_parse_pre_tool_use_ask_user_question() -> None:
    data = {
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "ask-user",
        "toolArgs": json.dumps(
            {
                "question": "下一步做什么？",
                "options": [{"label": "继续"}, {"label": "停止"}],
            },
            ensure_ascii=False,
        ),
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert event.__class__.__name__ == "CopilotAskUserQuestionEvent"
    assert event.question == "下一步做什么？"
    assert event.options == [{"label": "继续"}, {"label": "停止"}]


def test_copilot_parse_pre_tool_use_functions_ask_user_with_choices() -> None:
    data = {
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "functions.ask_user",
        "toolArgs": json.dumps(
            {
                "question": "下一步做什么？",
                "choices": ["继续修复", "先看日志"],
                "allow_freeform": True,
            },
            ensure_ascii=False,
        ),
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert event.__class__.__name__ == "CopilotAskUserQuestionEvent"
    assert event.question == "下一步做什么？"
    assert event.options == [{"label": "继续修复"}, {"label": "先看日志"}]


def test_copilot_parse_post_tool_use_ask_user_question() -> None:
    data = {
        "hook_event_name": "postToolUse",
        "cwd": "/project",
        "toolName": "ask-user",
        "toolResult": json.dumps(
            {
                "question": "确认继续吗？",
                "options": [{"label": "是"}, {"label": "否"}],
            },
            ensure_ascii=False,
        ),
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert event.__class__.__name__ == "CopilotAskUserQuestionEvent"
    assert event.question == "确认继续吗？"


def test_copilot_parse_post_tool_use_structured_result() -> None:
    data = {
        "hook_event_name": "postToolUse",
        "cwd": "/project",
        "toolName": "bash",
        "toolResult": {
            "resultType": "success",
            "textResultForLlm": "All tests passed (15/15)",
        },
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert isinstance(event, PostToolUseEvent)
    assert event.tool_result == "success"
    assert event.final_message == "All tests passed (15/15)"


def test_copilot_parse_post_tool_use_ask_user_with_choices_and_final_message() -> None:
    data = {
        "hook_event_name": "postToolUse",
        "cwd": "/project",
        "toolName": "functions.ask_user",
        "toolResult": {
            "question": "下一步做什么？",
            "choices": ["继续", "停止"],
            "textResultForLlm": "请先在继续和停止之间选择。",
        },
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert event.__class__.__name__ == "CopilotAskUserQuestionEvent"
    assert event.question == "下一步做什么？"
    assert event.options == [{"label": "继续"}, {"label": "停止"}]
    assert event.final_message == "请先在继续和停止之间选择。"


def test_copilot_parse_error_occurred() -> None:
    data = {
        "hook_event_name": "errorOccurred",
        "cwd": "/project",
        "error": "Connection timeout",
        "errorContext": "api_call",
        "recoverable": True,
    }
    event = parse_copilot_hook_input(json.dumps(data))
    assert isinstance(event, ErrorOccurredEvent)
    assert event.error == "Connection timeout"
    assert event.recoverable is True


def test_copilot_parse_error_occurred_with_object_payload() -> None:
    data = {
        "hook_event_name": "errorOccurred",
        "cwd": "/project",
        "error": {"name": "TimeoutError", "message": "Connection timeout"},
        "recoverable": False,
    }
    event = parse_copilot_hook_input(json.dumps(data, ensure_ascii=False))
    assert isinstance(event, ErrorOccurredEvent)
    assert event.error == "Connection timeout"
    assert event.final_message == "Connection timeout"


def test_copilot_parse_invalid_json() -> None:
    result = parse_copilot_hook_input("not json")
    assert result is None


def test_copilot_parse_unknown_event() -> None:
    data = {"hook_event_name": "unknownEvent", "cwd": "/x"}
    result = parse_copilot_hook_input(json.dumps(data))
    assert result is None


# --- format_copilot_notification tests ---


def test_copilot_format_session_start() -> None:
    event = SessionStartEvent(cwd="/project", source="startup")
    title, content = format_copilot_notification(event)
    assert "会话开始" in title
    assert "startup" in content
    assert "/project" in content


def test_copilot_format_pre_tool_use() -> None:
    event = PreToolUseEvent(cwd="/code", tool_name="bash", tool_args="ls -la")
    title, content = format_copilot_notification(event)
    assert "工具调用" in title
    assert "bash" in content
    assert "ls -la" in content


def test_copilot_format_ask_user_question() -> None:
    class_name_event = parse_copilot_hook_input(json.dumps({
        "hook_event_name": "preToolUse",
        "cwd": "/code",
        "toolName": "ask-user",
        "toolArgs": json.dumps({"question": "下一步做什么？", "options": [{"label": "继续"}]}, ensure_ascii=False),
    }, ensure_ascii=False))
    title, content = format_copilot_notification(class_name_event)
    assert "需要回答" in title
    assert "下一步做什么？" in content
    assert "继续" in content


def test_copilot_format_ask_user_question_from_choices() -> None:
    class_name_event = parse_copilot_hook_input(json.dumps({
        "hook_event_name": "preToolUse",
        "cwd": "/code",
        "toolName": "functions.ask_user",
        "toolArgs": json.dumps(
            {
                "question": "下一步做什么？",
                "choices": ["继续", "停止"],
                "allow_freeform": True,
            },
            ensure_ascii=False,
        ),
    }, ensure_ascii=False))
    title, content = format_copilot_notification(class_name_event)
    assert "需要回答" in title
    assert "下一步做什么？" in content
    assert "继续 / 停止" in content


def test_copilot_format_ask_user_question_includes_final_message() -> None:
    event = CopilotAskUserQuestionEvent(
        cwd="/code",
        question="下一步做什么？",
        options=[{"label": "继续"}, {"label": "停止"}],
        final_message="请先在继续和停止之间选择。",
    )
    title, content = format_copilot_notification(event)
    assert "需要回答" in title
    assert "下一步做什么？" in content
    assert "继续 / 停止" in content
    assert "补充说明" in content
    assert "请先在继续和停止之间选择。" in content


def test_copilot_format_post_tool_use_includes_final_message() -> None:
    event = PostToolUseEvent(
        cwd="/code",
        tool_name="bash",
        tool_result="success",
        final_message="All tests passed (15/15)",
    )
    title, content = format_copilot_notification(event)
    assert "工具完成" in title
    assert "success" in content
    assert "结果详情" in content
    assert "All tests passed (15/15)" in content


def test_copilot_format_error_occurred() -> None:
    event = ErrorOccurredEvent(cwd="/project", error="timeout", error_context="api", recoverable=True)
    title, content = format_copilot_notification(event)
    assert "错误" in title
    assert "timeout" in content
    assert "True" in content


# --- handle_copilot_hook_event tests ---


def test_copilot_handle_session_start_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "sessionStart",
        "cwd": "/project",
        "source": "startup",
    })
    exit_code = handle_copilot_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "会话开始" in title


def test_copilot_handle_pre_tool_use_ask_user_question_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "ask-user",
        "toolArgs": json.dumps(
            {
                "question": "下一步做什么？",
                "options": [{"label": "继续"}, {"label": "停止"}],
            },
            ensure_ascii=False,
        ),
    }, ensure_ascii=False)
    exit_code = handle_copilot_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "需要回答" in title
    assert "下一步做什么？" in content


def test_copilot_handle_pre_tool_use_functions_ask_user_sends_card() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "functions.ask_user",
        "toolArgs": json.dumps(
            {
                "question": "下一步做什么？",
                "choices": ["继续修复", "先看日志"],
                "allow_freeform": True,
            },
            ensure_ascii=False,
        ),
    }, ensure_ascii=False)
    exit_code = handle_copilot_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "需要回答" in title
    assert "下一步做什么？" in content
    assert "继续修复 / 先看日志" in content


def test_copilot_handle_post_tool_use_does_not_send_card_while_thinking() -> None:
    bot = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "postToolUse",
        "cwd": "/project",
        "toolName": "bash",
        "toolResult": {
            "resultType": "success",
            "textResultForLlm": "All tests passed (15/15)",
        },
    }, ensure_ascii=False)
    exit_code = handle_copilot_hook_event(input_json, bot)
    assert exit_code == 0
    bot.send_card.assert_not_called()


def test_copilot_handle_pre_tool_use_does_not_arm_auto_reply() -> None:
    bot = MagicMock()
    auto_replier = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "preToolUse",
        "cwd": "/project",
        "toolName": "bash",
        "toolArgs": "echo hello",
    })
    exit_code = handle_copilot_hook_event(input_json, bot, auto_replier=auto_replier)
    assert exit_code == 0
    auto_replier.arm.assert_not_called()


def test_copilot_handle_post_tool_use_does_not_arm_auto_reply() -> None:
    bot = MagicMock()
    auto_replier = MagicMock()
    input_json = json.dumps({
        "hook_event_name": "postToolUse",
        "cwd": "/project",
        "toolName": "bash",
        "toolResult": "done",
    })
    exit_code = handle_copilot_hook_event(input_json, bot, auto_replier=auto_replier)
    assert exit_code == 0
    auto_replier.arm.assert_not_called()
