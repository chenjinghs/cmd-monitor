"""IPC 模块 — Windows Named Pipe 上的 JSON-line 协议

服务端 (PipeServer) 在 daemon 中运行,接收 hook handler 进程发来的事件。
客户端 (send_event) 短连一次性发送,不阻塞 hook 进程。

协议:
  - 每条消息一行 UTF-8 编码的 JSON,以 \\n 结束
  - 客户端发送一行后立即关闭连接
  - 服务端可选回复一行 JSON(用于 ping/status)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PIPE_NAME = r"\\.\pipe\cmd-monitor"
PIPE_BUFFER_SIZE = 65536
PIPE_TIMEOUT_MS = 5000

EventHandler = Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]


def _import_pywin32():
    """延迟导入 pywin32。返回 (win32pipe, win32file, pywintypes, winerror)。"""
    import pywintypes  # type: ignore
    import win32file  # type: ignore
    import win32pipe  # type: ignore
    import winerror  # type: ignore

    return win32pipe, win32file, pywintypes, winerror


def send_event(
    event: Dict[str, Any],
    pipe_name: str = DEFAULT_PIPE_NAME,
    timeout_ms: int = PIPE_TIMEOUT_MS,
) -> Optional[Dict[str, Any]]:
    """向 daemon 的 named pipe 发送一个事件。

    Args:
        event: 事件字典(必须 JSON 可序列化)
        pipe_name: pipe 名,默认 \\\\.\\pipe\\cmd-monitor
        timeout_ms: 等待 pipe 可用的最大毫秒数

    Returns:
        服务端返回的 JSON 对象;若服务端无回复或出错返回 None。
    """
    win32pipe, win32file, pywintypes, _ = _import_pywin32()

    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    try:
        win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
        handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    except pywintypes.error as e:
        logger.debug("Pipe connect failed (%s): %s", pipe_name, e)
        return None

    try:
        win32file.WriteFile(handle, payload)
        # Best-effort read (服务端可能不回复)
        try:
            _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
            if data:
                line = data.decode("utf-8").splitlines()[0]
                return json.loads(line)
        except pywintypes.error:
            pass
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("Failed to decode pipe response: %s", e)
    finally:
        try:
            win32file.CloseHandle(handle)
        except Exception:
            pass

    return None


class PipeServer:
    """Daemon 内运行的 named pipe 服务端。

    每接受一个客户端连接,解析一行 JSON,调用 handler,可选回写一行 JSON 应答。
    使用线程池处理连接,避免单个慢请求阻塞其他 tab 的 hook。
    """

    def __init__(
        self,
        handler: EventHandler,
        pipe_name: str = DEFAULT_PIPE_NAME,
        max_instances: int = 16,
    ) -> None:
        self._handler = handler
        self._pipe_name = pipe_name
        self._max_instances = max_instances
        self._running = False
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """启动 pipe 服务端循环(在独立线程中)。"""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._accept_loop, name="PipeServer", daemon=True)
        t.start()
        self._threads.append(t)
        logger.info("Pipe server started: %s", self._pipe_name)

    def stop(self) -> None:
        """请求停止;实际关闭通过下次连接尝试触发。"""
        self._running = False
        # 触发一个虚拟连接让 ConnectNamedPipe 返回
        try:
            send_event({"type": "shutdown"}, self._pipe_name, timeout_ms=200)
        except Exception:
            pass
        logger.info("Pipe server stopping")

    def _accept_loop(self) -> None:
        win32pipe, win32file, pywintypes, _ = _import_pywin32()

        while self._running:
            try:
                handle = win32pipe.CreateNamedPipe(
                    self._pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    self._max_instances,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    PIPE_TIMEOUT_MS,
                    None,
                )
            except pywintypes.error as e:
                logger.error("CreateNamedPipe failed: %s", e)
                return

            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as e:
                logger.warning("ConnectNamedPipe failed: %s", e)
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                continue

            t = threading.Thread(
                target=self._handle_connection,
                args=(handle,),
                name="PipeConn",
                daemon=True,
            )
            t.start()

    def _handle_connection(self, handle: Any) -> None:
        win32pipe, win32file, pywintypes, _ = _import_pywin32()
        try:
            _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
            line = data.decode("utf-8").splitlines()[0] if data else ""
            if not line:
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON on pipe: %s", e)
                return

            if event.get("type") == "shutdown" and not self._running:
                return

            try:
                response = self._handler(event)
            except Exception as e:
                logger.error("Pipe event handler error: %s", e)
                response = {"ok": False, "error": str(e)}

            if response is not None:
                payload = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    win32file.WriteFile(handle, payload)
                except pywintypes.error:
                    pass
        except pywintypes.error as e:
            logger.debug("Pipe read error: %s", e)
        except Exception as e:
            logger.error("Unexpected pipe handler error: %s", e)
        finally:
            try:
                win32pipe.DisconnectNamedPipe(handle)
            except Exception:
                pass
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass
