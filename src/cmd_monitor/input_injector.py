"""指令注入模块

通过 Win32 API 将文本注入 PowerShell 终端窗口。
主方案: ctypes SendInput + 剪贴板粘贴（零依赖、支持 Unicode）
"""

import ctypes
import ctypes.wintypes
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040
SW_RESTORE = 9
VK_CONTROL = 0x11
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


# --- Window Finding ---


@dataclass
class WindowInfo:
    """窗口信息"""

    hwnd: int
    title: str
    pid: int = 0


def find_windows(title_substr: str) -> List[WindowInfo]:
    """按标题关键词查找窗口

    Args:
        title_substr: 窗口标题子串（不区分大小写）

    Returns:
        匹配的窗口列表
    """
    results: List[WindowInfo] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _callback(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_substr.lower() in buf.value.lower():
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append(WindowInfo(hwnd=hwnd, title=buf.value, pid=pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return results


def find_first_window(title_substr: str) -> Optional[WindowInfo]:
    """查找第一个匹配的窗口"""
    windows = find_windows(title_substr)
    return windows[0] if windows else None


# --- Window Focus ---


def force_foreground(hwnd: int) -> bool:
    """强制将窗口带到前台

    Args:
        hwnd: 窗口句柄

    Returns:
        是否成功
    """
    # Show if minimized
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.05)

    # Check if already foreground
    if user32.GetForegroundWindow() == hwnd:
        return True

    # Alt-key trick to bypass SetForegroundWindow restriction
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 0x0002, 0)  # Alt up
    time.sleep(0.1)

    return user32.GetForegroundWindow() == hwnd


# --- Clipboard + SendInput Paste ---


def _send_key(vk: int, key_down: bool = True) -> None:
    """发送单个按键"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    if not key_down:
        inp.union.ki.dwFlags = KEYEVENTF_KEYUP
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _set_clipboard_text(text: str) -> bool:
    """将文本写入系统剪贴板

    Args:
        text: 要写入的文本

    Returns:
        是否成功
    """
    if not user32.OpenClipboard(0):
        logger.error("Failed to open clipboard")
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        if not h:
            logger.error("GlobalAlloc failed")
            return False
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        user32.CloseClipboard()


def inject_text(hwnd: int, text: str, inject_delay: float = 0.5) -> bool:
    """将文本注入目标窗口（剪贴板粘贴方式）

    Args:
        hwnd: 目标窗口句柄
        text: 要注入的文本
        inject_delay: 注入后等待时间（秒）

    Returns:
        是否成功
    """
    if not text:
        logger.warning("Empty text, skipping injection")
        return False

    # 1. Bring window to foreground
    if not force_foreground(hwnd):
        logger.error("Failed to bring window to foreground: hwnd=%s", hwnd)
        return False

    # 2. Set clipboard
    if not _set_clipboard_text(text):
        logger.error("Failed to set clipboard")
        return False

    time.sleep(0.05)

    # 3. Ctrl+V via SendInput
    _send_key(VK_CONTROL, key_down=True)
    _send_key(VK_V, key_down=True)
    _send_key(VK_V, key_down=False)
    _send_key(VK_CONTROL, key_down=False)

    time.sleep(0.05)

    # 4. Send Enter to execute
    _send_key(0x0D, key_down=True)  # VK_RETURN
    _send_key(0x0D, key_down=False)

    time.sleep(inject_delay)
    logger.info("Text injected to hwnd=%s: %s", hwnd, text[:50])
    return True


# --- Convenience Function ---


def inject_to_window(
    title_substr: str,
    text: str,
    inject_delay: float = 0.5,
) -> bool:
    """按标题查找窗口并注入文本

    Args:
        title_substr: 窗口标题子串
        text: 要注入的文本
        inject_delay: 注入后等待时间

    Returns:
        是否成功
    """
    window = find_first_window(title_substr)
    if not window:
        logger.error("Window not found: %s", title_substr)
        return False

    return inject_text(window.hwnd, text, inject_delay)
