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
user32.GetGUIThreadInfo.argtypes = [ctypes.wintypes.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = ctypes.wintypes.BOOL
user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
user32.SwitchToThisWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.BOOL]
user32.SwitchToThisWindow.restype = None
user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.UINT,
]
user32.SetWindowPos.restype = ctypes.wintypes.BOOL
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


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", ctypes.wintypes.RECT),
    ]


def get_focus_window() -> int:
    """获取当前前台线程中具有键盘焦点的窗口句柄。"""
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    fg = user32.GetForegroundWindow()
    if not fg:
        return 0
    pid = ctypes.wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        return int(gti.hwndFocus)
    return 0


def get_window_info(hwnd: int) -> tuple[str, str]:
    """获取窗口的类名和标题。"""
    if not hwnd or not user32.IsWindow(hwnd):
        return ("", "")
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = title_buf.value
        else:
            title = ""
        return (cls, title)
    except Exception:
        return ("", "")


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
    增加多轮重试和多种策略,提高在复杂桌面环境下的成功率。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        logger.error("hwnd is invalid or no longer exists: %s", hwnd)
        return False

    # 还原最小化
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.1)

    # Already foreground?
    if user32.GetForegroundWindow() == hwnd:
        return True

    # 多轮重试,每轮使用不同策略
    HWND_TOP = 0
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_SHOWWINDOW = 0x0040
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    ASFW_ANY = 0xFFFFFFFF

    for attempt in range(3):
        # 每轮开始时都请求前台权限（权限窗口期很短）
        user32.AllowSetForegroundWindow(ASFW_ANY)

        # 策略 1: AttachThreadInput + SetForegroundWindow
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
        finally:
            if attached_fg:
                user32.AttachThreadInput(current_thread, fg_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)

        time.sleep(0.1 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # 策略 2: SwitchToThisWindow (Windows 内置的切换逻辑)
        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.12 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # 策略 3: SetWindowPos HWND_TOPMOST 置顶（不需要前台权限）
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_SHOWWINDOW | SWP_NOMOVE | SWP_NOSIZE,
        )
        time.sleep(0.05)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            # 成功后再取消置顶，保持正常层级
            user32.SetWindowPos(
                hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                SWP_SHOWWINDOW | SWP_NOMOVE | SWP_NOSIZE,
            )
            return True
        # 取消置顶，避免窗口一直置顶
        user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_SHOWWINDOW | SWP_NOMOVE | SWP_NOSIZE,
        )

        # 策略 4: Alt-key trick
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 0x0002, 0)  # Alt up
        time.sleep(0.15 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

    logger.warning(
        "force_foreground: all attempts failed (hwnd=%s fg=%s)",
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


def _send_unicode_char(code: int, key_down: bool = True) -> None:
    """发送单个 Unicode 字符（绕过剪贴板）"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = 0
    inp.union.ki.wScan = code
    flags = KEYEVENTF_UNICODE
    if not key_down:
        flags |= KEYEVENTF_KEYUP
    inp.union.ki.dwFlags = flags
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def inject_text_unicode(text: str) -> bool:
    """使用 KEYEVENTF_UNICODE 逐字符输入文本（不经过剪贴板）"""
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        _send_unicode_char(code, key_down=True)
        _send_unicode_char(code, key_down=False)
    return True


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

    # 2. Verify foreground window before paste (especially when skip_foreground=True)
    fg_before = user32.GetForegroundWindow()
    if fg_before != hwnd:
        logger.warning(
            "Foreground mismatch before paste (fg=%s != target=%s), retrying force_foreground",
            fg_before,
            hwnd,
        )
        force_foreground(hwnd)
        time.sleep(0.15)
        fg_before = user32.GetForegroundWindow()
        if fg_before != hwnd:
            logger.error("Failed to set foreground before paste (fg=%s != target=%s)", fg_before, hwnd)
            # Attempt paste anyway as last resort
    else:
        time.sleep(0.05)

    # 3. Set clipboard
    if not _set_clipboard_text(text):
        logger.error("Failed to set clipboard")
        return False

    time.sleep(0.1)

    # 4. Log focus window right before paste
    focus_hwnd = get_focus_window()
    focus_cls, focus_title = get_window_info(focus_hwnd)
    target_cls, target_title = get_window_info(hwnd)
    logger.info(
        "Before paste: fg=%s focus=%s(%s/%s) target=%s(%s/%s)",
        fg_before, focus_hwnd, focus_cls, focus_title, hwnd, target_cls, target_title,
    )

    # 5. Use KEYEVENTF_UNICODE to type text directly (bypass clipboard issues)
    logger.info("Typing text via KEYEVENTF_UNICODE (%d chars)", len(text))
    inject_text_unicode(text)

    time.sleep(0.1)

    # 6. Send Enter to execute
    _send_key(0x0D, key_down=True)  # VK_RETURN
    _send_key(0x0D, key_down=False)

    time.sleep(inject_delay)
    logger.info("Text injected to hwnd=%s (fg_before=%s focus=%s): %s", hwnd, fg_before, focus_hwnd, text[:50])
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
