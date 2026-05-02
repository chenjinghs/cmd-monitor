"""自动回复调度器 — daemon 内的 per-session 超时调度

每个 hook 事件触发 arm(session_id),启动一个超时定时器。
若飞书回复在超时前到达 → cancel(session_id),由路由直接注入。
若超时未到 → 回调 default_handler(session_id),注入预设答案。

新增功能:
- 只针对用户手动回复过的 session 启用自动回复
- 同一 session 最多自动回复 max_replies 次
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)

DefaultHandler = Callable[[str, str], None]  # (session_id, default_answer) → 注入


class AutoReplyScheduler:
    """线程安全的 per-session 超时调度。"""

    def __init__(
        self,
        timeout_seconds: float,
        default_answer: str,
        on_timeout: DefaultHandler,
        max_replies: int = 3,
    ) -> None:
        self._timeout = timeout_seconds
        self._default = default_answer
        self._on_timeout = on_timeout
        self._max_replies = max(max_replies, 1)
        self._timers: Dict[str, threading.Timer] = {}
        self._reply_counts: Dict[str, int] = {}
        self._replied_sessions: Set[str] = set()
        self._lock = threading.RLock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    @property
    def default_answer(self) -> str:
        return self._default

    @property
    def max_replies(self) -> int:
        return self._max_replies

    def mark_replied(self, session_id: str) -> None:
        """标记 session 为用户已手动回复过。

        只有被标记过的 session 才会触发自动回复。
        """
        with self._lock:
            self._replied_sessions.add(session_id)
            logger.info("AutoReply: session %s marked as manually replied", session_id[:8])

    def arm(self, session_id: str) -> bool:
        """为 session 启动/重置超时定时器。

        只有用户手动回复过的 session 才会真正启用。
        返回是否成功启用。
        """
        with self._lock:
            # 只针对用户手动回复过的 session
            if session_id not in self._replied_sessions:
                logger.debug("AutoReply not armed for %s: never manually replied", session_id[:8])
                return False

            # 检查是否已达最大回复次数
            count = self._reply_counts.get(session_id, 0)
            if count >= self._max_replies:
                logger.info(
                    "AutoReply not armed for %s: max_replies (%d) reached",
                    session_id[:8],
                    self._max_replies,
                )
                return False

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
        logger.debug("AutoReply armed for %s (timeout=%.1fs, count=%d/%d)", session_id[:8], self._timeout, count, self._max_replies)
        return True

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
            # 检查是否仍满足条件（可能被并发取消）
            if session_id not in self._replied_sessions:
                logger.info("AutoReply fire skipped for %s: not manually replied", session_id[:8])
                return
            count = self._reply_counts.get(session_id, 0)
            if count >= self._max_replies:
                logger.info("AutoReply fire skipped for %s: max_replies reached", session_id[:8])
                return
            self._reply_counts[session_id] = count + 1
        try:
            logger.info(
                "AutoReply timeout fired for %s (auto-reply %d/%d), injecting default",
                session_id[:8],
                count + 1,
                self._max_replies,
            )
            self._on_timeout(session_id, self._default)
        except Exception as e:
            logger.error("AutoReply timeout handler failed for %s: %s", session_id[:8], e)
