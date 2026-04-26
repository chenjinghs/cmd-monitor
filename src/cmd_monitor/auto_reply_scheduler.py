"""自动回复调度器 — daemon 内的 per-session 超时调度

每个 hook 事件触发 arm(session_id),启动一个超时定时器。
若飞书回复在超时前到达 → cancel(session_id),由路由直接注入。
若超时未到 → 回调 default_handler(session_id),注入预设答案。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

DefaultHandler = Callable[[str, str], None]  # (session_id, default_answer) → 注入


class AutoReplyScheduler:
    """线程安全的 per-session 超时调度。"""

    def __init__(
        self,
        timeout_seconds: float,
        default_answer: str,
        on_timeout: DefaultHandler,
    ) -> None:
        self._timeout = timeout_seconds
        self._default = default_answer
        self._on_timeout = on_timeout
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.RLock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    @property
    def default_answer(self) -> str:
        return self._default

    def arm(self, session_id: str) -> None:
        """为 session 启动/重置超时定时器。"""
        with self._lock:
            existing = self._timers.pop(session_id, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(
                self._timeout,
                self._fire,
                args=(session_id,),
            )
            timer.daemon = True
            self._timers[session_id] = timer
            timer.start()
        logger.debug("AutoReply armed for %s (timeout=%.1fs)", session_id[:8], self._timeout)

    def cancel(self, session_id: str) -> bool:
        """取消 session 的超时定时器,返回是否成功取消(True 表示用户在超时前回复)。"""
        with self._lock:
            timer = self._timers.pop(session_id, None)
        if timer is None:
            return False
        timer.cancel()
        logger.debug("AutoReply cancelled for %s", session_id[:8])
        return True

    def shutdown(self) -> None:
        """停止全部定时器(daemon 退出时调用)。"""
        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for t in timers:
            t.cancel()

    def _fire(self, session_id: str) -> None:
        with self._lock:
            self._timers.pop(session_id, None)
        try:
            logger.info("AutoReply timeout fired for %s, injecting default", session_id[:8])
            self._on_timeout(session_id, self._default)
        except Exception as e:
            logger.error("AutoReply timeout handler failed for %s: %s", session_id[:8], e)
