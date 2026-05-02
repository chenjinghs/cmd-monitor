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

import ctypes
import ctypes.wintypes

from cmd_monitor.input_injector import (
    find_first_window,
    force_foreground,
    inject_text,
)
from cmd_monitor.session_registry import SessionInfo

_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
_user32.GetWindowRect.restype = ctypes.wintypes.BOOL
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = ctypes.wintypes.BOOL

# SendInput structs for mouse
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT_UNION2(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class MOUSE_INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("union", _INPUT_UNION2)]


def _click_window_center(hwnd: int) -> None:
    """在窗口中心模拟鼠标左键点击，使 WT 的 terminal pane 获得键盘焦点。

    注意：WT 窗口顶部有标题栏(~30px) + 标签栏(~35px)，几何中心往往落在
    标签栏上，点击后 terminal pane 拿不到键盘焦点。因此点击位置向下偏移，
    落在 terminal pane 区域。
    """
    rect = ctypes.wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    # 标题栏(~30) + 标签栏(~35) + 可能的上边距，避开这些区域
    # 直接点击窗口底部 1/4 处，确保落在 terminal pane 上
    WT_CHROME_ESTIMATE = 80
    cx = rect.left + width // 2
    cy = rect.bottom - max(height // 4, 50)  # 底部偏上一点，避免点到状态栏
    # 把坐标转换为 SendInput 需要的 0-65535 绝对坐标
    sm_cx = _user32.GetSystemMetrics(0)  # screen width
    sm_cy = _user32.GetSystemMetrics(1)  # screen height
    abs_x = (cx * 65535) // (sm_cx - 1) if sm_cx > 1 else cx
    abs_y = (cy * 65535) // (sm_cy - 1) if sm_cy > 1 else cy

    inputs = (MOUSE_INPUT * 2)()
    # move to center
    inputs[0].type = 0  # INPUT_MOUSE
    inputs[0].union.mi.dx = abs_x
    inputs[0].union.mi.dy = abs_y
    inputs[0].union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    # left click
    inputs[1].type = 0
    inputs[1].union.mi.dx = abs_x
    inputs[1].union.mi.dy = abs_y
    inputs[1].union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE

    _user32.SendInput(2, inputs, ctypes.sizeof(MOUSE_INPUT))
    time.sleep(0.05)

    up = (MOUSE_INPUT * 1)()
    up[0].type = 0
    up[0].union.mi.dx = abs_x
    up[0].union.mi.dy = abs_y
    up[0].union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    _user32.SendInput(1, up, ctypes.sizeof(MOUSE_INPUT))
    # 点击后给 terminal pane 足够时间获得焦点
    time.sleep(0.3)

logger = logging.getLogger(__name__)

WT_FOCUS_GRACE_SECONDS = 0.4  # wt focus-tab 是异步的，需要等待 WT 内部切换完成


def _find_wt_exe() -> Optional[str]:
    """查找 wt.exe，先查 PATH，再查 WindowsApps 目录。"""
    found = shutil.which("wt.exe") or shutil.which("wt")
    if found:
        return found
    import os
    candidate = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WindowsApps", "wt.exe",
    )
    if os.path.exists(candidate):
        return candidate
    return None


def _focus_wt_tab(window_id: int, tab_index: int) -> bool:
    """调用 wt.exe focus-tab --target <idx>。

    当 window_id > 0 时附加 --window <id>，否则省略（操作当前窗口）。
    """
    if tab_index < 0:
        return False
    wt_exe = _find_wt_exe()
    if not wt_exe:
        logger.debug("wt.exe not found, skip tab focus")
        return False
    try:
        args = [wt_exe, "focus-tab", "--target", str(tab_index)]
        if window_id > 0:
            args = [wt_exe, "--window", str(window_id), "focus-tab", "--target", str(tab_index)]
        result = subprocess.run(
            args,
            check=False,
            timeout=3.0,
            capture_output=True,
        )
        logger.info("wt focus-tab window=%s tab=%s rc=%s", window_id, tab_index, result.returncode)
        if result.stderr:
            logger.debug("wt stderr: %s", result.stderr.decode(errors="replace"))
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

    logger.info(
        "inject_to_session: session=%s wt_session=%r wt_hwnd=%s tab_idx=%s window_hwnd=%s",
        info.session_id[:8],
        bool(info.wt_session),
        info.wt_window_hwnd,
        info.wt_tab_index,
        info.window_hwnd,
    )

    # 优先 WT 多 tab 路径
    if info.wt_session and info.wt_window_hwnd:
        tab_switched = False
        if info.wt_tab_index >= 0:
            tab_switched = _focus_wt_tab(info.wt_window_id, info.wt_tab_index)
        else:
            logger.warning(
                "wt_tab_index=-1 for session %s — cannot switch tab, will inject to current foreground tab",
                info.session_id[:8],
            )
        force_foreground(info.wt_window_hwnd, flash_on_failure=False)
        # WT 切 tab 后用鼠标点击确保 terminal pane 获得键盘焦点
        time.sleep(0.3)
        _click_window_center(info.wt_window_hwnd)
        time.sleep(0.2)
        logger.info(
            "Injecting to wt_window_hwnd=%s (tab_switched=%s)",
            info.wt_window_hwnd,
            tab_switched,
        )
        return inject_text(info.wt_window_hwnd, text, inject_delay=inject_delay, skip_foreground=True)

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
