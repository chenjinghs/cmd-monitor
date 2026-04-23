"""自动回复模块 — 超时后使用预设答案

等待用户通过飞书回复指令时，如果在超时时间内未收到回复，
自动注入预设答案，使 AI CLI 工具可以继续运行。
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class AutoReplier:
    """自动回复管理器 — 超时后使用预设答案

    用法：
    1. 向飞书发送通知后调用 arm()
    2. 将 on_message() 注册为飞书消息回调
    3. 调用 wait() 阻塞等待回复或超时
    4. wait() 返回应注入终端的答案

    Args:
        timeout_seconds: 等待用户回复的最大时间（秒）
        default_answer: 超时后自动使用的预设答案
    """

    def __init__(self, timeout_seconds: float, default_answer: str) -> None:
        self._timeout = timeout_seconds
        self._default_answer = default_answer
        self._event = threading.Event()
        self._reply: Optional[str] = None
        self._armed = False

    @property
    def default_answer(self) -> str:
        """预设答案"""
        return self._default_answer

    @property
    def timeout_seconds(self) -> float:
        """超时时间（秒）"""
        return self._timeout

    @property
    def is_armed(self) -> bool:
        """是否正在等待回复"""
        return self._armed

    def arm(self) -> None:
        """开始等待用户回复（发送通知后调用）"""
        self._event.clear()
        self._reply = None
        self._armed = True
        logger.debug(
            "AutoReplier armed (timeout=%.1fs, default=%r)",
            self._timeout,
            self._default_answer,
        )

    def on_message(self, text: str) -> None:
        """收到用户飞书消息时调用（注册为飞书消息回调）

        Args:
            text: 用户发送的消息文本
        """
        if self._armed:
            self._reply = text
            self._event.set()
            logger.info("AutoReplier: received reply, cancelling timeout")

    def wait(self) -> str:
        """阻塞等待用户回复或超时，返回应注入终端的答案

        Returns:
            用户回复内容（如在超时前收到），或超时后的预设答案
        """
        if not self._armed:
            return self._default_answer

        received = self._event.wait(timeout=self._timeout)
        self._armed = False

        if received and self._reply is not None:
            logger.info("AutoReplier: using user reply: %r", self._reply[:50])
            return self._reply

        logger.info(
            "AutoReplier: timeout (%.1fs elapsed), using default answer: %r",
            self._timeout,
            self._default_answer,
        )
        return self._default_answer
