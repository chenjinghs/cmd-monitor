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

# Win32 函数 argtypes/restype 声明 — 否则 64-bit 下 hwnd/handle 会被截断为 c_int
user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsWindow.restype = ctypes.wintypes.BOOL
user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
user32.IsIconic.restype = ctypes.wintypes.BOOL
user32.IsHungAppWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsHungAppWindow.restype = ctypes.wintypes.BOOL
user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
user32.SetActiveWindow.argtypes = [ctypes.wintypes.HWND]
user32.SetActiveWindow.restype = ctypes.wintypes.HWND
user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
user32.BringWindowToTop.restype = ctypes.wintypes.BOOL
user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = ctypes.wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
user32.AttachThreadInput.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.BOOL]
user32.AttachThreadInput.restype = ctypes.wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
user32.OpenClipboard.restype = ctypes.wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.wintypes.BOOL

kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

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
        ("dwExtraInfo", ctypes.c_uint64),  # ULONG_PTR as integer (8 bytes on 64-bit)
        ("_pad", ctypes.c_uint64),          # pad KEYBDINPUT to 32 bytes to match MOUSEINPUT
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


assert ctypes.sizeof(INPUT) == 40, f"INPUT struct size mismatch: {ctypes.sizeof(INPUT)} (expected 40)"


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
        if user32.IsHungAppWindow(hwnd):
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
    """强制将窗口带到前台。

    核心技巧: AttachThreadInput 必须附加到【当前前台窗口】的线程,
    而不是目标窗口的线程,这样才能获得"前台权限"来切换焦点。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        logger.error("hwnd is invalid or no longer exists: %s", hwnd)
        return False

    # 还原最小化
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)

    # Already foreground?
    if user32.GetForegroundWindow() == hwnd:
        return True

    # AllowSetForegroundWindow(ASFW_ANY) — 让本进程获得前台切换权限
    ASFW_ANY = 0xFFFFFFFF
    user32.AllowSetForegroundWindow(ASFW_ANY)

    # AttachThreadInput: 附加到【当前前台窗口】的线程,借用其前台权限
    fg_hwnd = user32.GetForegroundWindow()
    pid_buf = ctypes.wintypes.DWORD()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid_buf)) if fg_hwnd else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
    current_thread = kernel32.GetCurrentThreadId()

    attached_fg = False
    attached_target = False
    if fg_thread and fg_thread != current_thread:
        attached_fg = bool(user32.AttachThreadInput(current_thread, fg_thread, True))
    if target_thread and target_thread != current_thread and target_thread != fg_thread:
        attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))

    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        time.sleep(0.08)
        if user32.GetForegroundWindow() == hwnd:
            return True
    finally:
        if attached_fg:
            user32.AttachThreadInput(current_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)

    # Fallback: Alt-key trick
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 0x0002, 0)  # Alt up
    time.sleep(0.1)

    if user32.GetForegroundWindow() == hwnd:
        return True

    logger.warning(
        "force_foreground: SetForegroundWindow failed (hwnd=%s fg=%s), will try PostMessage paste",
        hwnd,
        user32.GetForegroundWindow(),
    )
    return False


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


def inject_text(hwnd: int, text: str, inject_delay: float = 0.5, skip_foreground: bool = False) -> bool:
    """将文本注入目标窗口（剪贴板粘贴方式）

    Args:
        hwnd: 目标窗口句柄
        text: 要注入的文本
        inject_delay: 注入后等待时间（秒）
        skip_foreground: 为 True 时跳过 force_foreground（调用方已确保焦点）

    Returns:
        是否成功
    """
    if not text:
        logger.warning("Empty text, skipping injection")
        return False

    # 1. Bring window to foreground (best-effort; daemon may lack foreground permission)
    if not skip_foreground:
        fg_ok = force_foreground(hwnd)
        if not fg_ok:
            logger.warning("force_foreground failed for hwnd=%s, attempting inject anyway", hwnd)

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
