"""hook_handler 中 IPC payload 构建器测试"""

import json

from cmd_monitor.hook_handler import build_claude_ipc_event, build_copilot_ipc_event


def test_build_claude_ipc_event_for_stop() -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "cwd": "/x",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
    )
    p = build_claude_ipc_event(raw)
    assert p["type"] == "hook_event"
    assert p["session_id"] == "abc"
    assert p["event_name"] == "Stop"
    assert p["notify_role"] == "waiting"
    assert "Claude" in p["title"]


def test_build_claude_ipc_event_for_active_stop() -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "cwd": "/x",
            "hook_event_name": "Stop",
            "stop_hook_active": True,
        }
    )
    p = build_claude_ipc_event(raw)
    assert p["type"] == "hook_event"
    assert p["session_id"] == "abc"
    assert p["event_name"] == "Stop"
    assert p["notify_role"] == "waiting"
    assert "Claude" in p["title"]


def test_build_claude_ipc_event_for_ask_user_question_is_waiting_after_running() -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "cwd": "/x",
            "hook_event_name": "AskUserQuestion",
            "question": "继续吗？",
            "options": [{"label": "继续"}],
        },
        ensure_ascii=False,
    )
    p = build_claude_ipc_event(raw)
    assert p["notify_role"] == "waiting_after_running"
    assert p["event_name"] == "AskUserQuestion"


def test_build_claude_ipc_event_invalid_json() -> None:
    assert build_claude_ipc_event("not json") is None


def test_build_copilot_ipc_event_session_start_is_running_role() -> None:
    raw = json.dumps(
        {"hook_event_name": "sessionStart", "cwd": "/x", "timestamp": 1, "source": "new"}
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "running"
    assert p["session_id"] == "copilot:/x"


def test_build_copilot_ipc_event_pre_tool_use_is_running_role() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "preToolUse",
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "bash",
            "toolArgs": "echo hi",
        }
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "running"


def test_build_copilot_ipc_event_ask_user_question_is_waiting_after_running() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "preToolUse",
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "ask-user",
            "toolArgs": json.dumps(
                {"question": "继续吗？", "options": [{"label": "继续"}]},
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "waiting_after_running"
    assert p["event_name"] == "CopilotAskUserQuestionEvent"


def test_build_copilot_ipc_event_functions_ask_user_with_choices() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "preToolUse",
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "functions.ask_user",
            "toolArgs": json.dumps(
                {"question": "继续吗？", "choices": ["继续", "停止"], "allow_freeform": True},
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "waiting_after_running"
    assert p["event_name"] == "CopilotAskUserQuestionEvent"
    assert "继续吗？" in p["content"]
    assert "继续 / 停止" in p["content"]


def test_build_copilot_ipc_event_post_tool_use_fallback_is_running_role() -> None:
    raw = json.dumps(
        {
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "bash",
            "toolResult": "ok",
        }
    )
    p = build_copilot_ipc_event(raw, fallback_event_name="postToolUse")
    assert p["notify_role"] == "running"
    assert p["session_id"] == "copilot:/x"


def test_build_copilot_ipc_event_post_tool_use_structured_result_includes_final_message() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "postToolUse",
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "bash",
            "toolResult": {
                "resultType": "success",
                "textResultForLlm": "All tests passed (15/15)",
            },
        },
        ensure_ascii=False,
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "running"
    assert "结果详情" in p["content"]
    assert "All tests passed (15/15)" in p["content"]
