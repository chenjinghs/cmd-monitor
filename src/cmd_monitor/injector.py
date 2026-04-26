"""增强注入器 — 支持 Windows Terminal 多 tab 切换

注入流程:
  1. 若有 WT_SESSION + tab_index → 调用 wt.exe focus-tab 切到目标 tab
  2. 若有 WT 主窗口 hwnd → force_foreground
  3. 否则退回独立窗口模式(沿用 input_injector.find_first_window)
  4. 剪贴板 + Ctrl+V + Enter

完全复用 input_injector 中的底层 SendInput / clipboard / window enumeration。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Optional

from cmd_monitor.input_injector import (
    find_first_window,
    force_foreground,
    inject_text,
)
from cmd_monitor.session_registry import SessionInfo

logger = logging.getLogger(__name__)

WT_FOCUS_GRACE_SECONDS = 0.15


def _focus_wt_tab(window_id: int, tab_index: int) -> bool:
    """调用 wt.exe --window <id> focus-tab --target <idx>。"""
    if tab_index < 0:
        return False
    wt_exe = shutil.which("wt.exe") or shutil.which("wt")
    if not wt_exe:
        logger.debug("wt.exe not found in PATH, skip tab focus")
        return False
    try:
        subprocess.run(
            [
                wt_exe,
                "--window",
                str(window_id),
                "focus-tab",
                "--target",
                str(tab_index),
            ],
            check=False,
            timeout=3.0,
        )
        time.sleep(WT_FOCUS_GRACE_SECONDS)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("wt focus-tab failed: %s", e)
        return False


def inject_to_session(
    info: SessionInfo,
    text: str,
    inject_delay: float = 0.5,
    fallback_title: Optional[str] = "PowerShell",
) -> bool:
    """根据 SessionInfo 选择最合适的注入方式。

    Args:
        info: 由 daemon 注册表中查询到的 session 上下文
        text: 要注入的文本
        inject_delay: 注入完成后的等待时间
        fallback_title: 当 SessionInfo 没有 hwnd 时,按窗口标题查找

    Returns:
        是否注入成功
    """
    if not text:
        return False

    # 优先 WT 多 tab 路径
    if info.wt_session and info.wt_window_hwnd:
        if info.wt_tab_index >= 0:
            _focus_wt_tab(info.wt_window_id, info.wt_tab_index)
        force_foreground(info.wt_window_hwnd)
        return inject_text(info.wt_window_hwnd, text, inject_delay=inject_delay)

    # 独立 conhost 窗口
    if info.window_hwnd:
        return inject_text(info.window_hwnd, text, inject_delay=inject_delay)

    # 退化:按标题模糊查
    if fallback_title:
        win = find_first_window(fallback_title)
        if win:
            return inject_text(win.hwnd, text, inject_delay=inject_delay)
    logger.error("No injection target found for session %s", info.session_id)
    return False
