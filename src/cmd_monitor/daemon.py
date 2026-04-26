"""Daemon 主进程 — 唯一的 FeishuBot WS 连接 + IPC server + 路由

cmd-monitor start 入口。维护:
- SessionRegistry: 所有活跃 session 的窗口/tab 信息
- TokenRouter: session_id ↔ 短 token,解析飞书回复
- PerSessionStateManager: 每 session 防抖+冷却
- AutoReplyScheduler: per-session 超时
- FeishuBot: 唯一 WS 连接

Hook handler 进程通过 named pipe 上报事件。
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from cmd_monitor.auto_reply_scheduler import AutoReplyScheduler
from cmd_monitor.feishu_client import FeishuBot, FeishuMessage
from cmd_monitor.injector import inject_to_session
from cmd_monitor.ipc import PipeServer
from cmd_monitor.session_registry import SessionInfo, SessionRegistry
from cmd_monitor.state_manager import PerSessionStateManager, SessionState
from cmd_monitor.token_router import RouteResult, TokenRouter

logger = logging.getLogger(__name__)

EVICT_INTERVAL_SECONDS = 300.0


class Daemon:
    """cmd-monitor 守护进程。"""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        feishu_cfg = config.get("feishu", {}) or {}
        state_cfg = config.get("state", {}) or {}
        auto_cfg = config.get("auto_reply", {}) or {}
        general_cfg = config.get("general", {}) or {}
        inject_cfg = config.get("inject", {}) or {}

        self._inject_delay = float(inject_cfg.get("inject_delay", 0.5))
        self._fallback_title = inject_cfg.get("target_window") or "PowerShell"

        self._registry = SessionRegistry(ttl_seconds=float(state_cfg.get("session_ttl", 1800.0)))
        self._token_router = TokenRouter(
            token_length=int(state_cfg.get("token_length", 4)),
            fallback_to_last_active=bool(state_cfg.get("fallback_to_last_active", True)),
        )
        self._state = PerSessionStateManager(
            debounce_seconds=float(state_cfg.get("debounce_seconds", 10.0)),
            notification_cooldown=float(state_cfg.get("notification_cooldown", 60.0)),
        )

        self._auto_reply: Optional[AutoReplyScheduler] = None
        if auto_cfg.get("enabled", False):
            self._auto_reply = AutoReplyScheduler(
                timeout_seconds=float(auto_cfg.get("timeout_seconds", 60.0)),
                default_answer=str(auto_cfg.get("default_answer", "y")),
                on_timeout=self._handle_auto_reply_timeout,
            )

        self._bot: Optional[FeishuBot] = None
        if feishu_cfg.get("app_id") and feishu_cfg.get("app_secret"):
            self._bot = FeishuBot(
                app_id=feishu_cfg["app_id"],
                app_secret=feishu_cfg["app_secret"],
                receiver_id=feishu_cfg.get("receiver_id", ""),
                receive_id_type=feishu_cfg.get("receive_id_type", "open_id"),
            )

        self._pipe_server = PipeServer(handler=self._handle_pipe_event)
        self._pid_file = Path(general_cfg.get("pid_file", "")) if general_cfg.get("pid_file") else None
        self._stop_event = threading.Event()
        self._evict_thread: Optional[threading.Thread] = None

    # --- lifecycle ---

    def run(self) -> int:
        """阻塞运行,直到 SIGINT/SIGTERM 或 stop()。返回退出码。"""
        if self._pid_file is not None:
            self._write_pid_file()

        if self._bot is not None:
            self._bot.set_message_callback(self._handle_feishu_reply)
            if not self._bot.start():
                logger.error("FeishuBot failed to start; daemon will continue without IM")

        self._pipe_server.start()
        self._evict_thread = threading.Thread(
            target=self._evict_loop, name="EvictLoop", daemon=True
        )
        self._evict_thread.start()

        signal.signal(signal.SIGINT, lambda *_: self.stop())
        try:
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
        except (ValueError, AttributeError):
            pass

        logger.info("cmd-monitor daemon running (pid=%d)", os.getpid())
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1.0)
        finally:
            self._cleanup()
        return 0

    def stop(self) -> None:
        self._stop_event.set()

    def _cleanup(self) -> None:
        if self._auto_reply is not None:
            self._auto_reply.shutdown()
        self._pipe_server.stop()
        if self._bot is not None:
            self._bot.stop()
        if self._pid_file is not None and self._pid_file.exists():
            try:
                self._pid_file.unlink()
            except OSError:
                pass
        logger.info("cmd-monitor daemon stopped")

    def _write_pid_file(self) -> None:
        assert self._pid_file is not None
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot write pid file %s: %s", self._pid_file, e)

    def _evict_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(EVICT_INTERVAL_SECONDS)
            if self._stop_event.is_set():
                return
            for sid in self._registry.evict_expired():
                self._token_router.remove(sid)
                self._state.remove(sid)
                logger.info("Session evicted (TTL): %s", sid[:8])

    # --- pipe event handling ---

    def _handle_pipe_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        etype = event.get("type", "")
        if etype == "hook_event":
            return self._handle_hook_event(event)
        if etype == "ping":
            return {"ok": True, "pid": os.getpid(), "sessions": len(self._registry.all_sessions())}
        if etype == "status":
            return {
                "ok": True,
                "pid": os.getpid(),
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "cwd": s.cwd,
                        "tab": s.wt_tab_index,
                        "wt_session": s.wt_session,
                        "hwnd": s.wt_window_hwnd or s.window_hwnd,
                    }
                    for s in self._registry.all_sessions()
                ],
                "tokens": [
                    {"session_id": sid, "token": tok}
                    for sid, tok in self._token_router.items()
                ],
            }
        logger.warning("Unknown pipe event type: %s", etype)
        return {"ok": False, "error": f"unknown type {etype}"}

    def _handle_hook_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        session_id = event.get("session_id", "")
        if not session_id:
            return {"ok": False, "error": "missing session_id"}

        info = SessionInfo(
            session_id=session_id,
            cwd=event.get("cwd", ""),
            wt_session=event.get("wt_session", ""),
            wt_window_id=int(event.get("wt_window_id", 0) or 0),
            wt_tab_index=int(event.get("wt_tab_index", -1)),
            wt_window_hwnd=int(event.get("wt_window_hwnd", 0) or 0),
            window_hwnd=int(event.get("window_hwnd", 0) or 0),
            last_event_name=event.get("event_name", ""),
        )
        self._registry.upsert(info)
        self._token_router.mark_active(session_id)
        token = self._token_router.get_or_create_token(session_id)

        # Decide whether to notify based on per-session state
        event_name = event.get("event_name", "")
        notify_role = event.get("notify_role", "waiting")  # 'waiting'|'running'|'skip'
        if notify_role == "running":
            self._state.transition(session_id, SessionState.RUNNING)
            return {"ok": True, "notified": False}
        if notify_role == "waiting_after_running":
            self._state.transition(session_id, SessionState.RUNNING)
            should_notify = self._state.transition(session_id, SessionState.WAITING)
            if not should_notify:
                return {"ok": True, "notified": False, "reason": "suppressed"}
            title = event.get("title", f"cmd-monitor — {event_name or 'event'}")
            content = event.get("content", "")
            full_title = f"[{token}] {title}"
            if self._bot is not None:
                self._bot.send_card(full_title, content)
            if self._auto_reply is not None:
                self._auto_reply.arm(session_id)
            return {"ok": True, "notified": True, "token": token}
        if notify_role == "skip":
            return {"ok": True, "notified": False}

        should_notify = self._state.transition(session_id, SessionState.WAITING)
        if not should_notify:
            return {"ok": True, "notified": False, "reason": "suppressed"}

        title = event.get("title", f"cmd-monitor — {event_name or 'event'}")
        content = event.get("content", "")
        full_title = f"[{token}] {title}"
        if self._bot is not None:
            self._bot.send_card(full_title, content)
        if self._auto_reply is not None:
            self._auto_reply.arm(session_id)

        return {"ok": True, "notified": True, "token": token}

    # --- feishu reply routing ---

    def _handle_feishu_reply(self, msg: FeishuMessage) -> None:
        result: RouteResult = self._token_router.route(msg.content)
        if result.session_id is None:
            logger.warning("Feishu reply has no routable session: %s", msg.content[:50])
            return

        info = self._registry.get(result.session_id)
        if info is None:
            logger.warning("Routed session not in registry: %s", result.session_id[:8])
            return

        if self._auto_reply is not None:
            self._auto_reply.cancel(result.session_id)
        self._state.transition(result.session_id, SessionState.RUNNING)
        self._registry.touch(result.session_id)
        self._token_router.mark_active(result.session_id)

        success = inject_to_session(
            info,
            result.content,
            inject_delay=self._inject_delay,
            fallback_title=self._fallback_title,
        )
        if not success:
            logger.error("Injection failed for session %s", result.session_id[:8])

    def _handle_auto_reply_timeout(self, session_id: str, default_answer: str) -> None:
        info = self._registry.get(session_id)
        if info is None:
            logger.warning("Auto-reply timeout: session gone %s", session_id[:8])
            return
        inject_to_session(
            info,
            default_answer,
            inject_delay=self._inject_delay,
            fallback_title=self._fallback_title,
        )
        self._state.transition(session_id, SessionState.RUNNING)


# --- daemon control utilities (used by `cmd-monitor stop`/`status`) ---


def read_pid(pid_file: Path) -> Optional[int]:
    try:
        if not pid_file.exists():
            return None
        text = pid_file.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            STILL_ACTIVE = 259
            return bool(ok and exit_code.value == STILL_ACTIVE)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def terminate(pid: int) -> bool:
    try:
        import ctypes

        PROCESS_TERMINATE = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.TerminateProcess(handle, 0))
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False
