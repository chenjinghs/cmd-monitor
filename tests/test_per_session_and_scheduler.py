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

    sched = AutoReplyScheduler(timeout_seconds=0.05, default_answer="y", on_timeout=on_timeout)
    sched.arm("s1")
    assert ev.wait(timeout=1.0)
    assert fired == [("s1", "y")]


def test_auto_reply_cancel_prevents_fire() -> None:
    fired = []
    sched = AutoReplyScheduler(
        timeout_seconds=0.5,
        default_answer="y",
        on_timeout=lambda s, d: fired.append(s),
    )
    sched.arm("s1")
    assert sched.cancel("s1") is True
    time.sleep(0.6)
    assert fired == []


def test_auto_reply_cancel_unknown_returns_false() -> None:
    sched = AutoReplyScheduler(timeout_seconds=1.0, default_answer="y", on_timeout=lambda s, d: None)
    assert sched.cancel("nonexistent") is False


def test_auto_reply_arm_resets_timer() -> None:
    fired = []
    ev = threading.Event()

    def on_timeout(sid: str, default: str) -> None:
        fired.append(sid)
        ev.set()

    sched = AutoReplyScheduler(timeout_seconds=0.1, default_answer="y", on_timeout=on_timeout)
    sched.arm("s1")
    time.sleep(0.05)
    sched.arm("s1")  # reset
    assert ev.wait(timeout=0.5)
    assert fired == ["s1"]
