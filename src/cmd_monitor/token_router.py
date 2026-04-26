"""Token 路由模块

为每个 session_id 生成短前缀 token(默认 4 位 hex),用作飞书卡片标题前缀。
用户回复格式: `<token> <内容>` → 路由到对应 session。

设计:
- token 由 session_id 哈希前 N 位生成,冲突时递增 salt 重哈希。
- 解析回复用宽松正则: 允许 token 大小写不敏感、token 与内容之间多种空白分隔符。
- 无 token 时按 fallback_to_last_active 配置决定行为。
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

DEFAULT_TOKEN_LENGTH = 4
_TOKEN_RE = re.compile(r"^\s*([0-9a-fA-F]{2,8})[\s:：]+(.+)$", re.DOTALL)


@dataclass(frozen=True)
class RouteResult:
    """路由结果"""

    session_id: Optional[str]
    content: str
    matched_token: bool


class TokenRouter:
    """将 session_id ↔ 短 token 双向映射,并解析飞书回复路由到 session。

    线程安全:所有公共方法持锁。
    """

    def __init__(
        self,
        token_length: int = DEFAULT_TOKEN_LENGTH,
        fallback_to_last_active: bool = True,
    ) -> None:
        if token_length < 2 or token_length > 8:
            raise ValueError("token_length must be 2..8")
        self._token_length = token_length
        self._fallback = fallback_to_last_active
        self._lock = threading.RLock()
        self._token_to_session: Dict[str, str] = {}
        self._session_to_token: Dict[str, str] = {}
        self._last_active: Optional[str] = None

    @property
    def token_length(self) -> int:
        return self._token_length

    def get_or_create_token(self, session_id: str) -> str:
        """为 session_id 分配 token(已存在则复用)。"""
        if not session_id:
            raise ValueError("session_id is empty")
        with self._lock:
            existing = self._session_to_token.get(session_id)
            if existing is not None:
                return existing
            token = self._generate_unique_token(session_id)
            self._token_to_session[token] = session_id
            self._session_to_token[session_id] = token
            return token

    def remove(self, session_id: str) -> None:
        """移除 session 的 token 映射(session 关闭/过期时调用)。"""
        with self._lock:
            token = self._session_to_token.pop(session_id, None)
            if token is not None:
                self._token_to_session.pop(token, None)
            if self._last_active == session_id:
                self._last_active = None

    def mark_active(self, session_id: str) -> None:
        """标记 session 为最近活跃(用于 fallback 路由)。"""
        with self._lock:
            self._last_active = session_id

    def lookup(self, token: str) -> Optional[str]:
        """根据 token 查 session_id。"""
        with self._lock:
            return self._token_to_session.get(token.lower())

    def route(self, reply_text: str) -> RouteResult:
        """解析飞书回复文本,返回 (session_id, 实际内容, 是否命中 token)。

        - 命中: 提取 token 前缀,剩余文本作为内容。
        - 未命中且 fallback 开启: session_id = last_active,完整文本作为内容。
        - 未命中且 fallback 关闭: session_id = None。
        """
        match = _TOKEN_RE.match(reply_text)
        if match:
            token_candidate = match.group(1).lower()
            with self._lock:
                session_id = self._token_to_session.get(token_candidate)
            if session_id is not None:
                return RouteResult(
                    session_id=session_id,
                    content=match.group(2).strip(),
                    matched_token=True,
                )

        # No token match
        if self._fallback:
            with self._lock:
                fallback = self._last_active
            return RouteResult(
                session_id=fallback,
                content=reply_text.strip(),
                matched_token=False,
            )
        return RouteResult(session_id=None, content=reply_text.strip(), matched_token=False)

    def items(self) -> Tuple[Tuple[str, str], ...]:
        """快照所有 (session_id, token) 对(测试 / 状态查询用)。"""
        with self._lock:
            return tuple(self._session_to_token.items())

    # --- internal ---

    def _generate_unique_token(self, session_id: str) -> str:
        for salt in range(0, 1024):
            payload = f"{session_id}|{salt}".encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()
            token = digest[: self._token_length].lower()
            if token not in self._token_to_session:
                return token
        # 极端冲突 — 用 session_id 末尾兜底
        return (session_id + "0" * self._token_length)[-self._token_length :].lower()
