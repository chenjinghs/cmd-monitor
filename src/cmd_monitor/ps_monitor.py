"""PowerShell transcript 监控模块

通过轮询 transcript 文件检测空闲状态（无新输出 + 提示符模式匹配），
截取最近输出作为通知摘要，通过飞书发送通知。
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional

from cmd_monitor.state_manager import SessionState, StateManager

logger = logging.getLogger(__name__)

# PowerShell 提示符正则: PS C:\Users\path>
PS_PROMPT_RE = re.compile(r"^PS\s+([A-Z]:\\[^>]*)>(?:\s.*)?$")
WAITING_PS_PROMPT_RE = re.compile(r"^PS\s+([A-Z]:\\[^>]*)>\s*$")
TRANSCRIPT_HEADER = "Windows PowerShell transcript start"
TRANSCRIPT_FOOTER = "Windows PowerShell transcript end"


# --- Transcript Reader ---


def follow_transcript(filepath: str, poll_interval: float = 0.1) -> Iterator[str]:
    """持续读取 transcript 文件新增行（tail -f 模式）

    Args:
        filepath: transcript 文件路径
        poll_interval: 轮询间隔（秒）

    Yields:
        新增的非空行（已去除行尾换行符）
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                stripped = line.rstrip("\n\r")
                if stripped:
                    yield stripped
            else:
                time.sleep(poll_interval)


def is_prompt_line(line: str) -> bool:
    """检测是否为 PowerShell 提示符行

    Args:
        line: transcript 中的一行

    Returns:
        是否匹配 PS C:\\path> 格式
    """
    return bool(PS_PROMPT_RE.match(line))


def extract_prompt_cwd(line: str) -> str:
    """从 PowerShell 提示符行中提取 cwd。"""
    match = PS_PROMPT_RE.match(line)
    if match is None:
        return ""
    return match.group(1)


def get_waiting_cwd(state: "TranscriptState") -> str:
    """返回“空闲等待输入”状态下的 cwd；否则返回空字符串。"""
    if not state.recent_lines:
        return ""
    match = WAITING_PS_PROMPT_RE.match(state.recent_lines[-1])
    if match is None:
        return ""
    return match.group(1)


def is_waiting_for_input(state: "TranscriptState") -> bool:
    """最近一行是否为纯提示符，表示终端已回到输入态。"""
    return bool(get_waiting_cwd(state))


def extract_last_output_block(state: "TranscriptState") -> str:
    """提取最近一次提示符前的输出块。"""
    if not state.recent_lines:
        return ""

    idx = len(state.recent_lines) - 1
    while idx >= 0:
        line = state.recent_lines[idx]
        if is_transcript_header(line) or WAITING_PS_PROMPT_RE.match(line):
            idx -= 1
            continue
        break

    if idx < 0:
        return ""

    block: list[str] = []
    while idx >= 0:
        line = state.recent_lines[idx]
        if is_transcript_header(line) or is_prompt_line(line):
            break
        block.append(line)
        idx -= 1

    return "\n".join(reversed(block)).strip()


def is_transcript_header(line: str) -> bool:
    """检测是否为 transcript 头/尾标记或启动信息

    Args:
        line: transcript 中的一行

    Returns:
        是否为头/尾标记行
    """
    if line.startswith("*" * 10):
        return True
    if TRANSCRIPT_HEADER in line:
        return True
    if TRANSCRIPT_FOOTER in line:
        return True
    if line.startswith("Transcript started, output file is"):
        return True
    return False


# --- Idle Detector ---


@dataclass
class TranscriptState:
    """Transcript 监控状态（不可变更新）"""

    last_activity_time: float = 0.0
    recent_lines: List[str] = field(default_factory=list)
    is_idle: bool = False
    max_recent_lines: int = 10


def update_state(state: TranscriptState, line: str, now: float) -> TranscriptState:
    """根据新行更新状态，返回新 TranscriptState

    Args:
        state: 当前状态
        line: 新读取的行
        now: 当前时间戳

    Returns:
        新的 TranscriptState（不修改原对象）
    """
    if is_transcript_header(line):
        return state

    new_lines = [*state.recent_lines, line]
    if len(new_lines) > state.max_recent_lines:
        new_lines = new_lines[-state.max_recent_lines :]

    return TranscriptState(
        last_activity_time=now,
        recent_lines=new_lines,
        is_idle=False,
        max_recent_lines=state.max_recent_lines,
    )


def check_idle(state: TranscriptState, idle_threshold: float, now: float) -> bool:
    """检查是否已空闲超过阈值

    Args:
        state: 当前状态
        idle_threshold: 空闲阈值（秒）
        now: 当前时间戳

    Returns:
        是否已空闲
    """
    if state.last_activity_time == 0.0:
        return False
    elapsed = now - state.last_activity_time
    return elapsed >= idle_threshold


# --- Notification Formatter ---


def format_idle_notification(state: TranscriptState, transcript_path: str) -> tuple[str, str]:
    """格式化 PowerShell 空闲通知

    Args:
        state: 当前状态
        transcript_path: transcript 文件路径

    Returns:
        (title, content) 元组，用于 send_card()
    """
    title = "PowerShell — 等待输入"
    cwd = get_waiting_cwd(state)
    last_output = extract_last_output_block(state) or "(无输出)"
    last_output_lines = last_output.splitlines()
    if len(last_output_lines) > 5:
        last_output = "\n".join(last_output_lines[-5:])
    if len(last_output) > 300:
        last_output = f"{last_output[:300]}…"
    recent_text = "\n".join(state.recent_lines[-5:]) if state.recent_lines else "(无输出)"
    cwd_line = f"**目录**: {cwd}\n" if cwd else ""
    content = (
        f"**状态**: 终端已空闲，等待输入\n"
        f"{cwd_line}"
        f"**文件**: {transcript_path}\n"
        f"**最后一条消息**:\n```\n{last_output}\n```\n"
        f"**最近输出**:\n```\n{recent_text}\n```"
    )
    return title, content


def build_idle_ipc_event(state: TranscriptState, transcript_path: str) -> dict[str, Any]:
    """把 transcript 空闲通知包装成 daemon 可接收的 IPC 事件。"""
    title, content = format_idle_notification(state, transcript_path)
    return {
        "type": "transcript_idle",
        "cwd": get_waiting_cwd(state),
        "transcript_path": transcript_path,
        "title": title,
        "content": content,
    }


# --- PsMonitor Main Class ---


class PsMonitor:
    """PowerShell transcript 监控器"""

    def __init__(
        self,
        transcript_path: str,
        poll_interval: float = 5.0,
        idle_threshold: float = 10.0,
        feishu_bot: Any = None,
        debounce_seconds: Optional[float] = None,
        notification_cooldown: float = 60.0,
        notification_callback: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> None:
        """初始化监控器

        Args:
            transcript_path: transcript 文件路径
            poll_interval: 空闲检查间隔（秒）
            idle_threshold: 空闲判定阈值（秒）
            feishu_bot: FeishuBot 实例（用于发送通知）
            debounce_seconds: 防抖窗口（秒），默认等于 idle_threshold
            notification_cooldown: 通知冷却（秒）
        """
        self.transcript_path = transcript_path
        self.poll_interval = poll_interval
        self.idle_threshold = idle_threshold
        self.feishu_bot = feishu_bot
        self.notification_callback = notification_callback
        self._state = TranscriptState()
        self._running = False
        self._state_manager = StateManager(
            debounce_seconds=debounce_seconds if debounce_seconds is not None else idle_threshold,
            notification_cooldown=notification_cooldown,
        )

    def run(self) -> None:
        """主监控循环（阻塞）"""
        self._running = True
        self._state = TranscriptState()
        self._state_manager.reset()
        logger.info("PS Monitor started: %s", self.transcript_path)

        # Start idle checker in background thread
        checker = threading.Thread(target=self._idle_check_loop, daemon=True)
        checker.start()

        try:
            for line in follow_transcript(self.transcript_path, poll_interval=0.1):
                if not self._running:
                    break
                now = time.time()
                self._state = update_state(self._state, line, now)
                # New activity — transition to RUNNING to cancel debounce
                self._state_manager.transition(SessionState.RUNNING, now=now)
        except FileNotFoundError:
            logger.error("Transcript file not found: %s", self.transcript_path)
        except OSError as e:
            logger.error("Error reading transcript: %s", e)
        finally:
            self._running = False
            logger.info("PS Monitor stopped")

    def stop(self) -> None:
        """停止监控"""
        self._running = False

    def _idle_check_loop(self) -> None:
        """后台线程：定时检查空闲状态（通过 StateManager 防抖）"""
        while self._running:
            time.sleep(self.poll_interval)
            if not self._running:
                break
            now = time.time()
            if check_idle(self._state, self.idle_threshold, now) and is_waiting_for_input(self._state):
                # Transition to IDLE (starts debounce), then check again
                # On repeated IDLE signals, StateManager handles debounce→WAITING
                should_notify = self._state_manager.transition(SessionState.IDLE, now=now)
                if should_notify:
                    self._on_idle_detected()

    def _on_idle_detected(self) -> None:
        """空闲检测回调"""
        event = build_idle_ipc_event(self._state, self.transcript_path)
        if self.notification_callback is not None:
            try:
                if self.notification_callback(event):
                    logger.info("Idle notification delivered via callback")
                    return
            except Exception as e:
                logger.warning("Idle notification callback failed: %s", e)

        title = str(event["title"])
        content = str(event["content"])
        if self.feishu_bot:
            self.feishu_bot.send_card(title, content)
            logger.info("Idle notification sent")
        else:
            logger.warning("FeishuBot not available, idle notification not sent")
