"""Daemon 端 IPC 事件处理 + 飞书回复路由集成测试"""

from typing import Any
from unittest.mock import MagicMock, patch

from cmd_monitor.daemon import Daemon
from cmd_monitor.feishu_client import FeishuMessage


def make_daemon(auto_reply: bool = False) -> Daemon:
    cfg: dict[str, Any] = {
        "feishu": {},  # 不启 bot
        "general": {},
        "state": {
            "debounce_seconds": 0.0,
            "notification_cooldown": 0.0,
            "token_length": 4,
        },
        "inject": {"inject_delay": 0.0, "target_window": "PowerShell"},
    }
    if auto_reply:
        cfg["auto_reply"] = {"enabled": True, "timeout_seconds": 0.5, "default_answer": "y"}
    return Daemon(cfg)


def test_handle_hook_event_registers_session_and_returns_token() -> None:
    d = make_daemon()
    resp = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-A",
            "event_name": "Stop",
            "title": "Claude — stopped",
            "content": "x",
            "notify_role": "waiting",
            "wt_session": "{guid-A}",
            "wt_tab_index": 2,
            "wt_window_hwnd": 12345,
        }
    )
    assert resp["ok"] is True
    assert resp["notified"] is True
    token = resp["token"]
    assert d._token_router.lookup(token) == "sess-A"
    info = d._registry.get("sess-A")
    assert info.wt_tab_index == 2
    assert info.wt_window_hwnd == 12345


def test_status_includes_wt_session_and_hwnd_for_unknown_tab() -> None:
    d = make_daemon()
    d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-A",
            "event_name": "Stop",
            "title": "Claude — stopped",
            "content": "x",
            "notify_role": "waiting",
            "wt_session": "{guid-A}",
            "wt_tab_index": -1,
            "wt_window_hwnd": 12345,
        }
    )
    resp = d._handle_pipe_event({"type": "status"})
    assert resp["ok"] is True
    assert resp["sessions"][0]["tab"] == -1
    assert resp["sessions"][0]["wt_session"] == "{guid-A}"
    assert resp["sessions"][0]["hwnd"] == 12345


def test_feishu_reply_routes_to_correct_session() -> None:
    d = make_daemon()
    d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "A",
            "title": "t",
            "content": "c",
            "wt_window_hwnd": 100,
        }
    )
    d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "B",
            "title": "t",
            "content": "c",
            "wt_window_hwnd": 200,
        }
    )
    token_b = d._token_router.get_or_create_token("B")

    with patch("cmd_monitor.daemon.inject_to_session", return_value=True) as mock_inj:
        d._handle_feishu_reply(
            FeishuMessage(
                message_id="m1",
                sender_id="u",
                chat_id="c",
                chat_type="p2p",
                content=f"{token_b} hello",
                msg_type="text",
            )
        )
        # injected for session B
        called_info = mock_inj.call_args[0][0]
        assert called_info.session_id == "B"
        assert mock_inj.call_args[0][1] == "hello"


def test_feishu_reply_unknown_token_falls_back_to_last_active() -> None:
    d = make_daemon()
    d._handle_pipe_event({"type": "hook_event", "session_id": "A", "title": "t", "content": "c"})
    d._handle_pipe_event({"type": "hook_event", "session_id": "B", "title": "t", "content": "c"})
    # B is more recent
    with patch("cmd_monitor.daemon.inject_to_session", return_value=True) as mock_inj:
        d._handle_feishu_reply(
            FeishuMessage(
                message_id="m1",
                sender_id="u",
                chat_id="c",
                chat_type="p2p",
                content="just text",
                msg_type="text",
            )
        )
        assert mock_inj.call_args[0][0].session_id == "B"


def test_auto_reply_cancelled_on_user_reply() -> None:
    d = make_daemon(auto_reply=True)
    d._handle_pipe_event({"type": "hook_event", "session_id": "A", "title": "t", "content": "c"})
    token = d._token_router.get_or_create_token("A")
    fired = []
    d._auto_reply._on_timeout = lambda sid, default: fired.append(sid)
    with patch("cmd_monitor.daemon.inject_to_session", return_value=True):
        d._handle_feishu_reply(
            FeishuMessage(
                message_id="m1", sender_id="u", chat_id="c", chat_type="p2p",
                content=f"{token} ok", msg_type="text",
            )
        )
    import time as _t
    _t.sleep(0.7)
    assert fired == []  # cancel won


def test_handle_hook_event_copilot_ask_user_question_not_suppressed_while_waiting() -> None:
    d = make_daemon()
    first = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "copilot:/project",
            "event_name": "SessionStartEvent",
            "title": "Copilot CLI — 会话开始",
            "content": "开始",
            "notify_role": "running",
        }
    )
    second = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "copilot:/project",
            "event_name": "CopilotAskUserQuestionEvent",
            "title": "Copilot CLI — 需要回答",
            "content": "问题: 继续吗?",
            "notify_role": "waiting_after_running",
        }
    )
    assert first["notified"] is False
    assert second["ok"] is True
    assert second["notified"] is True


def test_handle_hook_event_copilot_post_tool_use_stays_running_without_notification() -> None:
    d = make_daemon()
    first = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "copilot:/project",
            "cwd": "E:\\repo",
            "event_name": "UserPromptSubmittedEvent",
            "title": "Copilot CLI — 用户提交",
            "content": "提示",
            "notify_role": "running",
        }
    )
    second = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "copilot:/project",
            "cwd": "E:\\repo",
            "event_name": "PostToolUseEvent",
            "title": "Copilot CLI — 工具完成",
            "content": "工具: report_intent",
            "notify_role": "running",
        }
    )
    assert first["notified"] is False
    assert second["ok"] is True
    assert second["notified"] is False


def test_transcript_idle_event_routes_to_matching_copilot_session() -> None:
    d = make_daemon()
    first = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "copilot:E:\\repo",
            "cwd": "E:\\repo",
            "event_name": "UserPromptSubmittedEvent",
            "title": "Copilot CLI — 用户提交",
            "content": "提示",
            "notify_role": "running",
        }
    )
    second = d._handle_pipe_event(
        {
            "type": "transcript_idle",
            "cwd": "E:\\repo",
            "title": "PowerShell — 等待输入",
            "content": "最近输出",
        }
    )
    assert first["notified"] is False
    assert second["ok"] is True
    assert second["notified"] is True
    assert d._token_router.lookup(second["token"]) == "copilot:E:\\repo"


def test_transcript_idle_event_without_matching_session_returns_no_session() -> None:
    d = make_daemon()
    resp = d._handle_pipe_event(
        {
            "type": "transcript_idle",
            "cwd": "E:\\repo",
            "title": "PowerShell — 等待输入",
            "content": "最近输出",
        }
    )
    assert resp == {"ok": True, "notified": False, "reason": "no_session"}





