"""状态管理模块 — 全局状态机 + 防抖 + 通知抑制

管理会话状态转换（running→idle→waiting→running），
通过防抖窗口避免短暂停顿误触发通知，
通过通知冷却避免相同状态重复发送通知。
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """会话状态"""

    RUNNING = "running"
    IDLE = "idle"
    WAITING = "waiting"


@dataclass
class StateInfo:
    """不可变状态快照"""

    state: SessionState = SessionState.RUNNING
    last_state_change: float = 0.0
    last_notification_time: float = 0.0
    last_notification_state: Optional[SessionState] = None
    debounce_start: float = 0.0


class StateManager:
    """状态管理器 — 防抖 + 通知抑制

    Args:
        debounce_seconds: 防抖窗口（秒），空闲持续超过此时间才触发通知
        notification_cooldown: 通知冷却（秒），同一状态转换不重复通知的最小间隔
    """

    def __init__(
        self,
        debounce_seconds: float = 10.0,
        notification_cooldown: float = 60.0,
    ) -> None:
        self._debounce_seconds = debounce_seconds
        self._notification_cooldown = notification_cooldown
        self._state = StateInfo()

    @property
    def state(self) -> SessionState:
        """当前会话状态"""
        return self._state.state

    @property
    def current_state(self) -> StateInfo:
        """当前状态快照（只读）"""
        return self._state

    @property
    def debounce_seconds(self) -> float:
        """防抖窗口（秒，只读）"""
        return self._debounce_seconds

    @property
    def notification_cooldown(self) -> float:
        """通知冷却（秒，只读）"""
        return self._notification_cooldown

    def transition(self, new_state: SessionState, now: Optional[float] = None) -> bool:
        """尝试状态转换，返回是否应发送通知

        Args:
            new_state: 目标状态
            now: 当前单调时间戳（None 则使用 time.monotonic()）

        Returns:
            True 表示应发送通知，False 表示应抑制
        """
        now = now if now is not None else time.monotonic()
        current = self._state

        # 相同状态 — 检查防抖
        if new_state == current.state:
            if new_state == SessionState.IDLE:
                return self._check_debounce(now)
            return False

        # RUNNING → IDLE：启动防抖计时器
        if current.state == SessionState.RUNNING and new_state == SessionState.IDLE:
            self._state = StateInfo(
                state=SessionState.IDLE,
                last_state_change=now,
                last_notification_time=current.last_notification_time,
                last_notification_state=current.last_notification_state,
                debounce_start=now,
            )
            logger.debug("State: RUNNING → IDLE (debounce started)")
            return False

        # IDLE → WAITING：防抖到期，应通知
        if current.state == SessionState.IDLE and new_state == SessionState.WAITING:
            if self._should_send_notification(now):
                self._state = StateInfo(
                    state=SessionState.WAITING,
                    last_state_change=now,
                    last_notification_time=now,
                    last_notification_state=SessionState.WAITING,
                )
                logger.info("State: IDLE → WAITING (notification sent)")
                return True
            return False

        # IDLE → RUNNING：新活动，取消防抖
        if current.state == SessionState.IDLE and new_state == SessionState.RUNNING:
            self._state = StateInfo(
                state=SessionState.RUNNING,
                last_state_change=now,
                last_notification_time=current.last_notification_time,
                last_notification_state=current.last_notification_state,
            )
            logger.debug("State: IDLE → RUNNING (debounce cancelled)")
            return False

        # WAITING → RUNNING：用户回复，重置
        if current.state == SessionState.WAITING and new_state == SessionState.RUNNING:
            self._state = StateInfo(
                state=SessionState.RUNNING,
                last_state_change=now,
                last_notification_time=current.last_notification_time,
                last_notification_state=current.last_notification_state,
            )
            logger.debug("State: WAITING → RUNNING (reset)")
            return False

        # WAITING → IDLE：再次空闲
        if current.state == SessionState.WAITING and new_state == SessionState.IDLE:
            self._state = StateInfo(
                state=SessionState.IDLE,
                last_state_change=now,
                last_notification_time=current.last_notification_time,
                last_notification_state=current.last_notification_state,
                debounce_start=now,
            )
            logger.debug("State: WAITING → IDLE (re-entering idle)")
            return False

        # 其他转换：直接执行
        notify = False
        if new_state == SessionState.WAITING and self._should_send_notification(now):
            notify = True
        self._state = StateInfo(
            state=new_state,
            last_state_change=now,
            last_notification_time=now if notify else current.last_notification_time,
            last_notification_state=SessionState.WAITING if notify else current.last_notification_state,
        )
        return notify

    def should_notify(self, now: Optional[float] = None) -> bool:
        """检查当前状态是否应发送通知（用于 hook 事件）

        Args:
            now: 当前时间戳

        Returns:
            True 表示应发送通知
        """
        now = now if now is not None else time.monotonic()
        if self._state.state != SessionState.WAITING:
            return False
        return self._should_send_notification(now)

    def reset(self) -> None:
        """重置为 RUNNING 状态"""
        self._state = StateInfo()
        logger.debug("State: reset to RUNNING")

    def _check_debounce(self, now: float) -> bool:
        """检查 IDLE 状态下的防抖是否到期

        如果到期，自动转换为 WAITING 并返回 True。
        """
        current = self._state
        if current.debounce_start == 0.0:
            return False

        elapsed = now - current.debounce_start
        if elapsed >= self._debounce_seconds:
            if self._should_send_notification(now):
                self._state = StateInfo(
                    state=SessionState.WAITING,
                    last_state_change=now,
                    last_notification_time=now,
                    last_notification_state=SessionState.WAITING,
                )
                logger.info("Debounce expired: IDLE → WAITING (notification sent)")
                return True
        return False

    def _should_send_notification(self, now: float) -> bool:
        """检查通知冷却是否已过期"""
        if self._state.last_notification_time == 0.0:
            return True
        elapsed = now - self._state.last_notification_time
        return elapsed >= self._notification_cooldown


class PerSessionStateManager:
    """Per-session 状态管理器 — 每个 session_id 持有独立的 StateManager。

    用于 daemon,多个 PowerShell tab/CLI 实例之间状态完全隔离。
    线程安全。
    """

    def __init__(
        self,
        debounce_seconds: float = 10.0,
        notification_cooldown: float = 60.0,
    ) -> None:
        import threading

        self._debounce_seconds = debounce_seconds
        self._notification_cooldown = notification_cooldown
        self._managers: dict[str, StateManager] = {}
        self._lock = threading.RLock()

    @property
    def debounce_seconds(self) -> float:
        return self._debounce_seconds

    @property
    def notification_cooldown(self) -> float:
        return self._notification_cooldown

    def _get_or_create(self, session_id: str) -> StateManager:
        with self._lock:
            mgr = self._managers.get(session_id)
            if mgr is None:
                mgr = StateManager(
                    debounce_seconds=self._debounce_seconds,
                    notification_cooldown=self._notification_cooldown,
                )
                self._managers[session_id] = mgr
            return mgr

    def transition(
        self,
        session_id: str,
        new_state: SessionState,
        now: Optional[float] = None,
    ) -> bool:
        """对指定 session 执行状态转换,返回是否应发送通知。"""
        return self._get_or_create(session_id).transition(new_state, now=now)

    def state(self, session_id: str) -> SessionState:
        with self._lock:
            mgr = self._managers.get(session_id)
        return mgr.state if mgr is not None else SessionState.RUNNING

    def reset(self, session_id: str) -> None:
        with self._lock:
            mgr = self._managers.get(session_id)
        if mgr is not None:
            mgr.reset()

    def remove(self, session_id: str) -> None:
        """移除某个 session 的状态(session 关闭/过期时调用)。"""
        with self._lock:
            self._managers.pop(session_id, None)

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._managers.keys())
