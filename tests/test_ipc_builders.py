"""hook_handler 中 IPC payload 构建器测试"""

import json

from cmd_monitor.hook_handler import build_claude_ipc_event


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


def test_build_claude_ipc_event_for_pre_tool_use_ask_user_question_is_waiting_after_running() -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "cwd": "/x",
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {
                "questions": [
                    {
                        "question": "继续吗？",
                        "options": [{"label": "继续"}],
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    p = build_claude_ipc_event(raw)
    assert p["notify_role"] == "waiting_after_running"
    assert p["event_name"] == "AskUserQuestion"


def test_build_claude_ipc_event_invalid_json() -> None:
    assert build_claude_ipc_event("not json") is None
