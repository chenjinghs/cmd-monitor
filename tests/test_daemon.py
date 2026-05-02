"""Daemon 端 IPC 事件处理 + 飞书回复路由集成测试"""

from typing import Any
from unittest.mock import MagicMock, patch

from cmd_monitor.daemon import Daemon
from cmd_monitor.feishu_client import FeishuMessage
from cmd_monitor.state_manager import SessionState


def make_daemon(auto_reply: bool = False, notification_cooldown: float = 0.0) -> Daemon:
    cfg: dict[str, Any] = {
        "feishu": {},  # 不启 bot
        "general": {},
        "state": {
            "debounce_seconds": 0.0,
            "notification_cooldown": notification_cooldown,
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


def test_feishu_reply_unknown_token_no_fallback() -> None:
    d = make_daemon()
    d._handle_pipe_event({"type": "hook_event", "session_id": "A", "title": "t", "content": "c"})
    d._handle_pipe_event({"type": "hook_event", "session_id": "B", "title": "t", "content": "c"})
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
        mock_inj.assert_not_called()


def test_auto_reply_cancelled_on_user_reply() -> None:
    d = make_daemon(auto_reply=True)
    # 模拟该 session 此前已手动回复过(满足 arm 的前置条件)
    d._auto_reply.mark_replied("A")
    d._handle_pipe_event({"type": "hook_event", "session_id": "A", "title": "t", "content": "c"})
    # 确认已 arm 起来,后续 cancel 才有意义
    assert "A" in d._auto_reply._timers
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


def test_stop_event_resets_state_to_running() -> None:
    """Stop 事件发完卡片后状态应重置为 RUNNING，确保下一轮对话能正常发卡片"""
    d = make_daemon()
    # 先发送一个 Notification 事件让状态变为 WAITING
    d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-A",
            "event_name": "Notification",
            "title": "需要输入",
            "content": "问题",
            "notify_role": "waiting",
        }
    )
    assert d._state.state("sess-A") == SessionState.WAITING

    # 发送 Stop 事件
    resp = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-A",
            "event_name": "Stop",
            "title": "已停止",
            "content": "完成",
            "notify_role": "waiting",
        }
    )
    assert resp["notified"] is True
    # Stop 后状态应重置为 RUNNING
    assert d._state.state("sess-A") == SessionState.RUNNING


def test_waiting_to_waiting_after_cooldown_allows_notification() -> None:
    """WAITING 状态下冷却期过后再次 WAITING 应允许发卡片"""
    d = make_daemon(notification_cooldown=0.0)
    # 第一次 Notification
    r1 = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-B",
            "event_name": "Notification",
            "title": "问题1",
            "content": "内容1",
            "notify_role": "waiting",
        }
    )
    assert r1["notified"] is True
    assert d._state.state("sess-B") == SessionState.WAITING

    # 再次 Notification（同一状态）— 无冷却应允许
    r2 = d._handle_pipe_event(
        {
            "type": "hook_event",
            "session_id": "sess-B",
            "event_name": "Notification",
            "title": "问题2",
            "content": "内容2",
            "notify_role": "waiting",
        }
    )
    assert r2["notified"] is True
