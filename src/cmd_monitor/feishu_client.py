"""飞书机器人客户端模块

基于 lark-oapi SDK 的 WebSocket 长连接实现双向通信。
无需公网服务器，适合本地守护进程场景。
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FeishuMessage:
    """飞书消息数据类"""

    message_id: str
    sender_id: str
    chat_id: str
    chat_type: str  # "p2p" or "group"
    content: str
    msg_type: str  # "text", "post", etc.


@dataclass
class FeishuBotConfig:
    """飞书机器人配置"""

    app_id: str = ""
    app_secret: str = ""
    receiver_id: str = ""


def _extract_post_text(content_json: dict) -> str:
    """从飞书富文本消息中提取纯文本

    处理多种格式:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}
    """
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]

    if not isinstance(root, dict):
        return ""

    def _parse_block(block: dict) -> str:
        texts: List[str] = []
        if block.get("title"):
            texts.append(block["title"])
        for row in block.get("content", []):
            if isinstance(row, list):
                for el in row:
                    if isinstance(el, dict) and el.get("tag") in ("text", "a"):
                        texts.append(el.get("text", ""))
        return " ".join(texts).strip()

    # Direct format: {"title": "...", "content": [[...]]}
    if "content" in root:
        result = _parse_block(root)
        if result:
            return result

    # Localized format: {"zh_cn": {"title": "...", "content": [...]}}
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            result = _parse_block(root[key])
            if result:
                return result

    # Fallback: try any dict child
    for val in root.values():
        if isinstance(val, dict):
            result = _parse_block(val)
            if result:
                return result

    return ""


class FeishuBot:
    """飞书机器人客户端，基于 lark-oapi WebSocket 长连接"""

    def __init__(self, app_id: str, app_secret: str, receiver_id: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._receiver_id = receiver_id
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._message_callback: Optional[Callable[[FeishuMessage], None]] = None
        self._processed_message_ids: Dict[str, None] = {}

    def set_message_callback(self, callback: Callable[[FeishuMessage], None]) -> None:
        """设置消息回调函数（用于后续 Phase 注入指令）"""
        self._message_callback = callback

    def start(self) -> bool:
        """启动飞书机器人 WebSocket 长连接

        Returns:
            True if started successfully, False otherwise.
        """
        if not self._app_id or not self._app_secret:
            logger.error("飞书 app_id 或 app_secret 未配置")
            return False

        try:
            import lark_oapi as lark

            # 创建 API Client（用于发送消息）
            self._client = (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )

            # 创建事件处理器
            event_handler = self._setup_event_handler()

            # 创建 WebSocket Client
            self._ws_client = lark.ws.Client(
                self._app_id,
                self._app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
            )

            # 在独立线程中启动 WebSocket
            self._running = True
            self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
            self._ws_thread.start()

            logger.info("飞书机器人已启动 (WebSocket 长连接)")
            return True

        except ImportError:
            logger.error("lark-oapi 未安装，请运行: pip install lark-oapi")
            return False
        except Exception as e:
            logger.error("启动飞书机器人失败: %s", e)
            return False

    def stop(self) -> None:
        """停止飞书机器人"""
        self._running = False
        logger.info("飞书机器人已停止")

    def send_text(self, text: str) -> bool:
        """发送文本消息到配置的接收者

        Args:
            text: 消息文本内容

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._client:
            logger.warning("飞书客户端未初始化")
            return False

        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(self._receiver_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error("发送飞书消息失败: code=%s, msg=%s", response.code, response.msg)
                return False

            logger.info("飞书消息已发送: %s", text[:50])
            return True

        except Exception as e:
            logger.error("发送飞书消息异常: %s", e)
            return False

    def send_card(self, title: str, content: str) -> bool:
        """发送卡片消息（interactive card）

        Args:
            title: 卡片标题
            content: 卡片内容（支持 Markdown）

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._client:
            logger.warning("飞书客户端未初始化")
            return False

        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            card = {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            }

            request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(self._receiver_id)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error("发送飞书卡片失败: code=%s, msg=%s", response.code, response.msg)
                return False

            logger.info("飞书卡片已发送: %s", title)
            return True

        except Exception as e:
            logger.error("发送飞书卡片异常: %s", e)
            return False

    def _setup_event_handler(self) -> Any:
        """创建 lark-oapi 事件处理器"""
        import lark_oapi as lark

        def on_message(data: Any) -> None:
            """处理接收到的消息事件"""
            try:
                event = data.event
                message = event.message
                sender = event.sender

                # 跳过 bot 自身消息
                if sender.sender_type == "bot":
                    return

                # 消息去重
                message_id = message.message_id
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = None

                # 限制缓存大小
                while len(self._processed_message_ids) > 1000:
                    self._processed_message_ids.popitem()

                # 解析消息内容
                content = self._parse_message_content(message)
                if not content:
                    return

                msg = FeishuMessage(
                    message_id=message_id,
                    sender_id=sender.sender_id.open_id if sender.sender_id else "unknown",
                    chat_id=message.chat_id,
                    chat_type=message.chat_type,
                    content=content,
                    msg_type=message.message_type,
                )

                logger.info("收到飞书消息: %s", content[:50])

                # 触发回调
                if self._message_callback:
                    self._message_callback(msg)

            except Exception as e:
                logger.error("处理飞书消息失败: %s", e)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        return handler

    @staticmethod
    def _parse_message_content(message: Any) -> Optional[str]:
        """解析飞书消息内容，提取纯文本

        Args:
            message: 飞书消息对象

        Returns:
            解析后的纯文本，解析失败返回 None
        """
        if not message.content:
            return None

        try:
            content_json = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            return None

        msg_type = message.message_type

        if msg_type == "text":
            return content_json.get("text", "").strip()
        elif msg_type == "post":
            return _extract_post_text(content_json)

        return None

    def _run_ws(self) -> None:
        """在独立线程中运行 WebSocket（含自动重连）"""
        try:
            import lark_oapi.ws.client as _lark_ws_client
        except ImportError:
            logger.error("lark-oapi 未安装")
            return

        # 创建独立的 event loop，避免与主线程冲突
        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)
        _lark_ws_client.loop = ws_loop

        try:
            while self._running:
                try:
                    self._ws_client.start()
                except Exception as e:
                    logger.warning("飞书 WebSocket 连接中断: %s", e)
                if self._running:
                    logger.info("5 秒后重连...")
                    time.sleep(5)
        finally:
            ws_loop.close()
