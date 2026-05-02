"""PerSessionStateManager / AutoReplyScheduler 单元测试"""

import threading
import time

from cmd_monitor.auto_reply_scheduler import AutoReplyScheduler
from cmd_monitor.state_manager import PerSessionStateManager, SessionState


def test_per_session_state_isolation() -> None:
    mgr = PerSessionStateManager(debounce_seconds=0.01, notification_cooldown=0.0)
    # Drive session A through RUNNING → IDLE → WAITING
    mgr.transition("A", SessionState.IDLE, now=0.0)
    mgr.transition("A", SessionState.WAITING, now=1.0)
    assert mgr.state("A") == SessionState.WAITING
    # Session B remains untouched
    assert mgr.state("B") == SessionState.RUNNING


def test_per_session_remove() -> None:
    mgr = PerSessionStateManager()
    mgr.transition("A", SessionState.IDLE)
    assert "A" in mgr.session_ids()
    mgr.remove("A")
    assert "A" not in mgr.session_ids()


def test_auto_reply_fires_after_timeout() -> None:
    fired = []
    ev = threading.Event()

    def on_timeout(sid: str, default: str) -> None:
        fired.append((sid, default))
        ev.set()

    sched = AutoReplyScheduler(timeout_seconds=0.05, default_answer="继续", on_timeout=on_timeout)
    sched.mark_replied("s1")
    sched.arm("s1")
    assert ev.wait(timeout=1.0)
    assert fired == [("s1", "继续")]


def test_auto_reply_cancel_prevents_fire() -> None:
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.5,
        default_answer="继续",
        on_timeout=lambda s, d: fired.append(s),
    )
    sched.mark_replied("s1")
    sched.arm("s1")
    assert sched.cancel("s1") is True
    time.sleep(0.6)
    assert fired == []


def test_auto_reply_cancel_unknown_returns_false() -> None:
    sched = AutoReplyScheduler(timeout_seconds=1.0, default_answer="继续", on_timeout=lambda s, d: None)
    assert sched.cancel("nonexistent") is False


def test_auto_reply_arm_resets_timer() -> None:
    fired = []
    ev = threading.Event()

    def on_timeout(sid: str, default: str) -> None:
        fired.append(sid)
        ev.set()

    sched = AutoReplyScheduler(timeout_seconds=0.1, default_answer="继续", on_timeout=on_timeout)
    sched.mark_replied("s1")
    sched.arm("s1")
    time.sleep(0.05)
    sched.arm("s1")  # reset
    assert ev.wait(timeout=0.5)
    assert fired == ["s1"]


def test_auto_reply_only_for_replied_sessions() -> None:
    """未 mark_replied 的 session 不会触发自动回复。"""
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.05,
        default_answer="继续",
        on_timeout=lambda s, d: fired.append(s),
    )
    # 不调用 mark_replied，直接 arm
    result = sched.arm("s1")
    assert result is False
    time.sleep(0.1)
    assert fired == []


def test_auto_reply_max_replies() -> None:
    """同一 session 最多触发 max_replies 次自动回复。"""
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.02,
        default_answer="继续",
        on_timeout=lambda s, d: fired.append(s),
        max_replies=2,
    )
    sched.mark_replied("s1")

    # 第一次触发
    sched.arm("s1")
    time.sleep(0.05)
    assert fired == ["s1"]

    # 第二次触发
    sched.arm("s1")
    time.sleep(0.05)
    assert fired == ["s1", "s1"]

    # 第三次 arm 应该被拒绝（已达 max_replies）
    result = sched.arm("s1")
    assert result is False
    time.sleep(0.05)
    # 第三次不会触发
    assert fired == ["s1", "s1"]


def test_auto_reply_remove_clears_state() -> None:
    """remove 应该清理 session 的所有自动回复状态。"""
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.5,
        default_answer="继续",
        on_timeout=lambda s, d: fired.append(s),
    )
    sched.mark_replied("s1")
    sched.arm("s1")
    sched.remove("s1")

    # remove 后定时器应该被取消
    time.sleep(0.6)
    assert fired == []

    # remove 后需要重新 mark_replied 才能 arm
    assert sched.arm("s1") is False

    # 重新标记后可以 arm
    sched.mark_replied("s1")
    assert sched.arm("s1") is True


def test_auto_reply_count_resets_on_mark_replied() -> None:
    """用户手动回复后,连续未回复计数应重置,允许下一轮自动回复。"""
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.02,
        default_answer="继续",
        on_timeout=lambda s, d: fired.append(s),
        max_replies=2,
    )
    sched.mark_replied("s1")

    # 连续触发到上限
    sched.arm("s1")
    time.sleep(0.05)
    sched.arm("s1")
    time.sleep(0.05)
    assert fired == ["s1", "s1"]

    # 上限后 arm 应被拒绝
    assert sched.arm("s1") is False

    # 用户手动回复 → 计数重置
    sched.mark_replied("s1")

    # 重置后可重新 arm 并触发
    assert sched.arm("s1") is True
    time.sleep(0.05)
    assert fired == ["s1", "s1", "s1"]
