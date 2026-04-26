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


def test_build_claude_ipc_event_skips_active_stop() -> None:
    raw = json.dumps(
        {
            "session_id": "abc",
            "cwd": "/x",
            "hook_event_name": "Stop",
            "stop_hook_active": True,
        }
    )
    assert build_claude_ipc_event(raw) is None


def test_build_claude_ipc_event_invalid_json() -> None:
    assert build_claude_ipc_event("not json") is None


def test_build_copilot_ipc_event_session_start_is_running_role() -> None:
    raw = json.dumps(
        {"hook_event_name": "sessionStart", "cwd": "/x", "timestamp": 1, "source": "new"}
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "running"
    assert p["session_id"] == "copilot:/x"


def test_build_copilot_ipc_event_post_tool_use_is_waiting_role() -> None:
    raw = json.dumps(
        {
            "hook_event_name": "postToolUse",
            "cwd": "/x",
            "timestamp": 1,
            "toolName": "bash",
            "toolResult": "ok",
        }
    )
    p = build_copilot_ipc_event(raw)
    assert p["notify_role"] == "waiting"
