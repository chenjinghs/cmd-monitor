"""会话注册表 — 跟踪每个 session 的运行上下文与最后活动时间

每当 hook handler 上报事件,daemon 在 SessionRegistry 中注册/更新 SessionInfo,
包含定位窗口(WT 或独立 conhost)所需的全部信息。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SessionInfo:
    """单个 CLI 会话的运行上下文(不可变快照)。"""

    session_id: str
    cwd: str = ""
    # Windows Terminal 上下文(可选)
    wt_session: str = ""  # WT_SESSION 环境变量(每个 tab 一个 GUID)
    wt_window_id: int = 0  # wt --window 参数,默认 0
    wt_tab_index: int = -1  # tab 序号,-1 表示未知
    wt_window_hwnd: int = 0  # WT 主窗口 hwnd
    # 通用窗口 hwnd(独立 conhost 等场景)
    window_hwnd: int = 0
    # 元数据
    last_event_name: str = ""
    last_active_at: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)


class SessionRegistry:
    """线程安全的 session_id → SessionInfo 注册表,带 TTL 清理。"""

    def __init__(self, ttl_seconds: float = 1800.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionInfo] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def upsert(self, info: SessionInfo) -> SessionInfo:
        """插入或更新 session。返回最终存储的 SessionInfo。

        如果 session 已存在,合并:保留 created_at,更新其他字段为新值
        (空字符串/0 视为未提供,保留旧值)。
        """
        if not info.session_id:
            raise ValueError("session_id is empty")
        with self._lock:
            existing = self._sessions.get(info.session_id)
            if existing is None:
                self._sessions[info.session_id] = info
                return info
            merged = replace(
                existing,
                cwd=info.cwd or existing.cwd,
                wt_session=info.wt_session or existing.wt_session,
                wt_window_id=info.wt_window_id or existing.wt_window_id,
                wt_tab_index=info.wt_tab_index if info.wt_tab_index >= 0 else existing.wt_tab_index,
                wt_window_hwnd=info.wt_window_hwnd or existing.wt_window_hwnd,
                window_hwnd=info.window_hwnd or existing.window_hwnd,
                last_event_name=info.last_event_name or existing.last_event_name,
                last_active_at=info.last_active_at or time.monotonic(),
            )
            self._sessions[info.session_id] = merged
            return merged

    def touch(self, session_id: str, now: Optional[float] = None) -> None:
        """更新 last_active_at。"""
        ts = now if now is not None else time.monotonic()
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                self._sessions[session_id] = replace(existing, last_active_at=ts)

    def get(self, session_id: str) -> Optional[SessionInfo]:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def all_sessions(self) -> List[SessionInfo]:
        with self._lock:
            return list(self._sessions.values())

    def evict_expired(self, now: Optional[float] = None) -> List[str]:
        """移除超过 TTL 未活动的 session。返回被移除的 session_id 列表。"""
        ts = now if now is not None else time.monotonic()
        evicted: List[str] = []
        with self._lock:
            for sid, info in list(self._sessions.items()):
                if ts - info.last_active_at > self._ttl:
                    self._sessions.pop(sid, None)
                    evicted.append(sid)
        return evicted
