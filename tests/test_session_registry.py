"""SessionRegistry 单元测试"""

import time

from cmd_monitor.session_registry import SessionInfo, SessionRegistry


def test_upsert_new_session() -> None:
    reg = SessionRegistry()
    info = SessionInfo(session_id="s1", cwd="/x")
    reg.upsert(info)
    assert reg.get("s1").cwd == "/x"


def test_upsert_merges_preserves_old_fields() -> None:
    reg = SessionRegistry()
    reg.upsert(SessionInfo(session_id="s1", cwd="/x", wt_window_hwnd=999))
    reg.upsert(SessionInfo(session_id="s1", cwd=""))  # empty cwd should keep old
    assert reg.get("s1").cwd == "/x"
    assert reg.get("s1").wt_window_hwnd == 999


def test_upsert_updates_tab_index_zero_aware() -> None:
    reg = SessionRegistry()
    reg.upsert(SessionInfo(session_id="s1", wt_tab_index=-1))
    reg.upsert(SessionInfo(session_id="s1", wt_tab_index=0))  # 0 is valid
    assert reg.get("s1").wt_tab_index == 0


def test_remove() -> None:
    reg = SessionRegistry()
    reg.upsert(SessionInfo(session_id="s1"))
    reg.remove("s1")
    assert reg.get("s1") is None


def test_evict_expired() -> None:
    reg = SessionRegistry(ttl_seconds=0.01)
    reg.upsert(SessionInfo(session_id="s1"))
    time.sleep(0.05)
    evicted = reg.evict_expired()
    assert "s1" in evicted
    assert reg.get("s1") is None


def test_touch_updates_last_active() -> None:
    reg = SessionRegistry(ttl_seconds=0.05)
    reg.upsert(SessionInfo(session_id="s1"))
    time.sleep(0.03)
    reg.touch("s1")
    time.sleep(0.03)  # total > ttl from creation, but touch reset
    evicted = reg.evict_expired()
    assert "s1" not in evicted


def test_upsert_rejects_empty_session_id() -> None:
    reg = SessionRegistry()
    try:
        reg.upsert(SessionInfo(session_id=""))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
