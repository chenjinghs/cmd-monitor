"""状态管理模块测试"""

from cmd_monitor.state_manager import (
    SessionState,
    StateInfo,
    StateManager,
)


# --- SessionState Tests ---


def test_session_state_values() -> None:
    """枚举值正确"""
    assert SessionState.RUNNING.value == "running"
    assert SessionState.IDLE.value == "idle"
    assert SessionState.WAITING.value == "waiting"


def test_session_state_members() -> None:
    """枚举成员数量"""
    assert len(SessionState) == 3


# --- StateInfo Tests ---


def test_state_info_defaults() -> None:
    """默认值正确"""
    info = StateInfo()
    assert info.state == SessionState.RUNNING
    assert info.last_state_change == 0.0
    assert info.last_notification_time == 0.0
    assert info.last_notification_state is None
    assert info.debounce_start == 0.0


def test_state_info_custom_values() -> None:
    """自定义值"""
    info = StateInfo(
        state=SessionState.IDLE,
        last_state_change=100.0,
        debounce_start=95.0,
    )
    assert info.state == SessionState.IDLE
    assert info.last_state_change == 100.0
    assert info.debounce_start == 95.0


# --- StateManager Init Tests ---


def test_state_manager_defaults() -> None:
    """默认参数"""
    sm = StateManager()
    assert sm.state == SessionState.RUNNING
    assert sm._debounce_seconds == 10.0
    assert sm._notification_cooldown == 60.0


def test_state_manager_custom_params() -> None:
    """自定义参数"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=30.0)
    assert sm._debounce_seconds == 5.0
    assert sm._notification_cooldown == 30.0


# --- StateManager.transition() Tests ---


def test_transition_running_to_idle_starts_debounce() -> None:
    """RUNNING→IDLE：启动防抖，不通知"""
    sm = StateManager(debounce_seconds=10.0)
    result = sm.transition(SessionState.IDLE, now=100.0)
    assert result is False
    assert sm.state == SessionState.IDLE
    assert sm.current_state.debounce_start == 100.0


def test_transition_idle_debounce_not_expired() -> None:
    """IDLE 状态下防抖未到期：不通知"""
    sm = StateManager(debounce_seconds=10.0)
    sm.transition(SessionState.IDLE, now=100.0)
    # 3 秒后再次 IDLE — 防抖未到期
    result = sm.transition(SessionState.IDLE, now=103.0)
    assert result is False
    assert sm.state == SessionState.IDLE


def test_transition_idle_debounce_expired() -> None:
    """IDLE 状态下防抖到期：转为 WAITING，应通知"""
    sm = StateManager(debounce_seconds=10.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    # 15 秒后再次 IDLE — 防抖到期
    result = sm.transition(SessionState.IDLE, now=115.0)
    assert result is True
    assert sm.state == SessionState.WAITING


def test_transition_same_state_suppressed() -> None:
    """RUNNING→RUNNING：相同状态抑制"""
    sm = StateManager()
    result = sm.transition(SessionState.RUNNING, now=100.0)
    assert result is False
    assert sm.state == SessionState.RUNNING


def test_transition_waiting_to_waiting_cooldown_active_suppresses() -> None:
    """WAITING→WAITING：冷却期内抑制"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING, notified at 110
    assert sm.state == SessionState.WAITING
    # 冷却期内再次 WAITING
    result = sm.transition(SessionState.WAITING, now=120.0)
    assert result is False
    assert sm.state == SessionState.WAITING


def test_transition_waiting_to_waiting_cooldown_expired_notifies() -> None:
    """WAITING→WAITING：冷却期过后允许再次通知"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING, notified at 110
    assert sm.state == SessionState.WAITING
    # 冷却期过后再次 WAITING
    result = sm.transition(SessionState.WAITING, now=180.0)
    assert result is True
    assert sm.state == SessionState.WAITING
    assert sm.current_state.last_notification_time == 180.0


def test_transition_waiting_to_waiting_updates_state() -> None:
    """WAITING→WAITING 成功时更新状态时间戳"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=10.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING, notified at 110
    assert sm.current_state.last_state_change == 110.0
    # 冷却期过后再次 WAITING
    result = sm.transition(SessionState.WAITING, now=130.0)
    assert result is True
    assert sm.current_state.last_state_change == 130.0
    assert sm.current_state.last_notification_time == 130.0


def test_transition_waiting_to_running_resets() -> None:
    """WAITING→RUNNING：重置，不通知"""
    sm = StateManager(debounce_seconds=10.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=115.0)  # → WAITING
    assert sm.state == SessionState.WAITING
    result = sm.transition(SessionState.RUNNING, now=120.0)
    assert result is False
    assert sm.state == SessionState.RUNNING


def test_transition_notification_cooldown_suppresses() -> None:
    """通知冷却期内重复通知被抑制"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    result1 = sm.transition(SessionState.IDLE, now=110.0)  # debounce expired → WAITING
    assert result1 is True
    assert sm.state == SessionState.WAITING
    # 仍在 WAITING 状态下再次 WAITING — 冷却期内应被抑制
    result2 = sm.transition(SessionState.WAITING, now=120.0)
    assert result2 is False  # 冷却期内，抑制


def test_transition_notification_after_cooldown() -> None:
    """冷却期过后可以再次通知"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING, notified at 110
    # 冷却期过后再次 WAITING（不经过 RUNNING，避免重置冷却）
    result = sm.transition(SessionState.WAITING, now=180.0)  # 70s passed, cooldown expired
    assert result is True


def test_transition_idle_to_running_cancels_debounce() -> None:
    """IDLE→RUNNING：取消防抖"""
    sm = StateManager(debounce_seconds=10.0)
    sm.transition(SessionState.IDLE, now=100.0)
    assert sm.state == SessionState.IDLE
    sm.transition(SessionState.RUNNING, now=103.0)
    assert sm.state == SessionState.RUNNING
    assert sm.current_state.debounce_start == 0.0


def test_transition_waiting_to_idle_reenters() -> None:
    """WAITING→IDLE：重新进入空闲"""
    sm = StateManager(debounce_seconds=10.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=115.0)  # → WAITING
    assert sm.state == SessionState.WAITING
    sm.transition(SessionState.IDLE, now=200.0)
    assert sm.state == SessionState.IDLE
    assert sm.current_state.debounce_start == 200.0


def test_transition_from_scratch_running() -> None:
    """初始状态为 RUNNING"""
    sm = StateManager()
    assert sm.state == SessionState.RUNNING


# --- StateManager.should_notify() Tests ---


def test_should_notify_running_state() -> None:
    """RUNNING 状态不应通知"""
    sm = StateManager()
    assert sm.should_notify(now=100.0) is False


def test_should_notify_waiting_state_no_cooldown() -> None:
    """WAITING 状态，无冷却，应通知"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING
    assert sm.should_notify(now=120.0) is True


def test_should_notify_waiting_state_with_cooldown() -> None:
    """WAITING 状态，冷却期内，不应通知"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING, notified at 110
    assert sm.should_notify(now=120.0) is False  # 冷却期内


def test_should_notify_cooldown_expired() -> None:
    """冷却期过后应通知"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=60.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # notified at 110
    assert sm.should_notify(now=180.0) is True  # 60s passed


# --- StateManager.reset() Tests ---


def test_reset_to_running() -> None:
    """reset 恢复为 RUNNING"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # → WAITING
    assert sm.state == SessionState.WAITING
    sm.reset()
    assert sm.state == SessionState.RUNNING


def test_reset_clears_debounce() -> None:
    """reset 清除防抖状态"""
    sm = StateManager()
    sm.transition(SessionState.IDLE, now=100.0)
    assert sm.current_state.debounce_start == 100.0
    sm.reset()
    assert sm.current_state.debounce_start == 0.0


def test_reset_preserves_notification_time() -> None:
    """reset 不保留通知时间（完全重置）"""
    sm = StateManager(debounce_seconds=5.0, notification_cooldown=0.0)
    sm.transition(SessionState.IDLE, now=100.0)
    sm.transition(SessionState.IDLE, now=110.0)  # notified
    sm.reset()
    assert sm.current_state.last_notification_time == 0.0
