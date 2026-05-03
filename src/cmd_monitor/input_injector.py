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

# FlashWindowEx for taskbar notification when foreground switch fails
class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.wintypes.HWND),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("uCount", ctypes.wintypes.UINT),
        ("dwTimeout", ctypes.wintypes.DWORD),
    ]

user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
user32.FlashWindowEx.restype = ctypes.wintypes.BOOL
user32.MessageBeep.argtypes = [ctypes.wintypes.UINT]
user32.MessageBeep.restype = ctypes.wintypes.BOOL

FLASHW_ALL = 0x00000003
FLASHW_TIMERNOFG = 0x0000000C

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
        if gti.hwndFocus:
            return int(gti.hwndFocus)
        return 0
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


# Win32 constants for force_foreground strategies
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_FLAGS = SWP_SHOWWINDOW | SWP_NOMOVE | SWP_NOSIZE
ASFW_ANY = 0xFFFFFFFF
VK_MENU = 0x12  # Alt
KEYEVENTF_KEYUP_FLAG = 0x0002


def _attach_thread_input_set_foreground(hwnd: int) -> None:
    """策略 1: AttachThreadInput 到当前前台窗口的线程，借其权限切换焦点。"""
    fg_hwnd = user32.GetForegroundWindow()
    pid_buf = ctypes.wintypes.DWORD()
    fg_thread = (
        user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid_buf)) if fg_hwnd else 0
    )
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


def _topmost_toggle(hwnd: int) -> None:
    """策略 3a: HWND_TOPMOST 置顶（不需前台权限），再 SetForegroundWindow。"""
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
    time.sleep(0.05)
    user32.SetForegroundWindow(hwnd)


def _undo_topmost(hwnd: int) -> None:
    """策略 3b: 取消置顶，保持正常 Z-order。"""
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_FLAGS)


def _alt_key_trick(hwnd: int) -> None:
    """策略 4: 按下/释放 Alt 后再切前台，规避 Win10 焦点窃取限制。"""
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP_FLAG, 0)


def _flash_window_taskbar(hwnd: int) -> None:
    """所有策略失败后闪烁任务栏图标并播放提示音，提醒用户手动切窗。"""
    try:
        fi = FLASHWINFO()
        fi.cbSize = ctypes.sizeof(FLASHWINFO)
        fi.hwnd = hwnd
        fi.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG
        fi.uCount = 5
        fi.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(fi))
        # 0x00000030 = MB_ICONEXCLAMATION，在 UIPI 限制下也能发声
        user32.MessageBeep(0x00000030)
        logger.info("Flashed window taskbar + beep to notify user")
    except Exception:
        pass


def force_foreground(hwnd: int, flash_on_failure: bool = True) -> bool:
    """强制将窗口带到前台。

    多轮调度 4 种策略,任一成功即返回 True。

    核心技巧: AttachThreadInput 必须附加到【当前前台窗口】的线程,
    而不是目标窗口的线程,这样才能获得"前台权限"来切换焦点。

    Args:
        hwnd: 目标窗口句柄
        flash_on_failure: 为 False 时，所有策略失败后不闪烁任务栏。
            适用于调用方已有完整注入保障流程（focus-tab + click + inject）的场景。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        logger.error("hwnd is invalid or no longer exists: %s", hwnd)
        return False

    # 还原最小化
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.1)

    if user32.GetForegroundWindow() == hwnd:
        return True

    for attempt in range(2):
        # 每轮请求一次前台权限（窗口期很短）
        user32.AllowSetForegroundWindow(ASFW_ANY)

        _attach_thread_input_set_foreground(hwnd)
        time.sleep(0.1 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.12 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

        _topmost_toggle(hwnd)
        time.sleep(0.1 + attempt * 0.05)
        succeeded = user32.GetForegroundWindow() == hwnd
        _undo_topmost(hwnd)
        if succeeded:
            return True

        _alt_key_trick(hwnd)
        time.sleep(0.12 + attempt * 0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True

    # UIPI fallback: 当 daemon 无 GUI 窗口时 GetForegroundWindow() 可能返回 NULL
    # (ctypes HWND restype 下表现为 Python None),但目标窗口仍可见 — 假定其已在前台。
    fg_final = user32.GetForegroundWindow()
    if not fg_final and user32.IsWindowVisible(hwnd):
        logger.info(
            "force_foreground: GetForegroundWindow() returned NULL, but target window is visible. "
            "Assuming window is already foreground (UIPI restriction)."
        )
        return True

    # UIPI 环境下 GetForegroundWindow 不可靠，降级为 INFO 避免用户误以为注入失败。
    logger.info(
        "force_foreground: all attempts failed (hwnd=%s fg=%s), "
        "inject will proceed with caller-provided fallback",
        hwnd,
        fg_final,
    )
    if flash_on_failure:
        _flash_window_taskbar(hwnd)
    return False


# --- Clipboard + SendInput Paste ---


def _send_key(vk: int, key_down: bool = True) -> int:
    """发送单个按键，返回 SendInput 注入的事件数（0 表示失败）。"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    if not key_down:
        inp.union.ki.dwFlags = KEYEVENTF_KEYUP
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


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
        time.sleep(0.01)
        _send_unicode_char(code, key_down=False)
        time.sleep(0.01)
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
        result = user32.SetClipboardData(CF_UNICODETEXT, h)
        if not result:
            logger.error("SetClipboardData failed")
            return False
        logger.debug("Clipboard set: %d bytes", len(data))
        return True
    finally:
        user32.CloseClipboard()


def _is_paste_ready(hwnd: int, fg_hwnd: int) -> bool:
    """前台窗口是否适合 paste — 命中 hwnd,或 UIPI 限制下 fg=NULL 但目标窗口可见。

    注: ctypes HWND restype 在 NULL 时返回 Python None,所以用 `not fg_hwnd` 同时覆盖
    None 与 0 两种取值。
    """
    if fg_hwnd == hwnd:
        return True
    return not fg_hwnd and bool(user32.IsWindowVisible(hwnd))


def _ensure_paste_ready(hwnd: int, user_wait_seconds: float = 2.0, skip_foreground: bool = False) -> int:
    """验证前台已就绪,必要时重试 force_foreground 并等用户手动切窗。

    Args:
        hwnd: 目标窗口句柄
        user_wait_seconds: 等待用户手动切换窗口的时间
        skip_foreground: 为 True 时跳过 force_foreground 重试。
            前置条件: 调用方已确保目标窗口可见且获得焦点。
            UIPI 环境下 GetForegroundWindow 可能返回 NULL，此时只要窗口
            可见就直接继续，避免无意义的 force_foreground 重试。

    Returns:
        最后一次 GetForegroundWindow() 返回值,供日志记录
    """
    fg = user32.GetForegroundWindow()
    if _is_paste_ready(hwnd, fg):
        time.sleep(0.05)
        return fg

    if skip_foreground:
        is_visible = bool(user32.IsWindowVisible(hwnd))
        if not is_visible:
            logger.warning(
                "skip_foreground=True, bypassing foreground check for hwnd=%s (fg=%s, visible=%s)",
                hwnd,
                fg,
                is_visible,
            )
            time.sleep(0.05)
            return fg
        # UIPI 环境下 GetForegroundWindow 返回 NULL，窗口可见但前台状态未知。
        # 给更多时间让用户手动切换窗口（如果 taskbar flash 提醒触发了手动切窗）。
        if not fg:
            logger.info(
                "UIPI: fg unknown for hwnd=%s, waiting 1.5s for user to switch...",
                hwnd,
            )
            time.sleep(1.5)
        else:
            time.sleep(0.05)
        return fg

    logger.warning(
        "Foreground mismatch before paste (fg=%s != target=%s), retrying force_foreground",
        fg,
        hwnd,
    )
    force_foreground(hwnd)
    time.sleep(0.15)
    fg = user32.GetForegroundWindow()
    if _is_paste_ready(hwnd, fg):
        time.sleep(0.05)
        return fg

    logger.error("Failed to set foreground before paste (fg=%s != target=%s)", fg, hwnd)
    logger.info("Waiting %.1fs for user to manually switch window...", user_wait_seconds)
    polls = max(1, int(user_wait_seconds / 0.5))
    for _ in range(polls):
        time.sleep(0.5)
        fg = user32.GetForegroundWindow()
        if _is_paste_ready(hwnd, fg):
            logger.info("User switched to target window, proceeding with inject")
            return fg
    logger.warning("User did not switch window, attempting inject anyway")
    return fg


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

    if not skip_foreground:
        if not force_foreground(hwnd):
            logger.warning("force_foreground failed for hwnd=%s, attempting inject anyway", hwnd)

    fg_before = _ensure_paste_ready(hwnd, skip_foreground=skip_foreground)

    if not _set_clipboard_text(text):
        logger.error("Failed to set clipboard")
        return False

    time.sleep(0.1)

    # paste 前记录前台/焦点状态,排查窗口被抢焦点等问题
    focus_hwnd = get_focus_window()
    focus_cls, focus_title = get_window_info(focus_hwnd)
    target_cls, target_title = get_window_info(hwnd)
    logger.info(
        "Before paste: fg=%s focus=%s(%s/%s) target=%s(%s/%s)",
        fg_before, focus_hwnd, focus_cls, focus_title, hwnd, target_cls, target_title,
    )

    if focus_hwnd != 0 and focus_hwnd != hwnd:
        logger.warning(
            "Focus mismatch (focus=%s != target=%s), input may go to wrong window",
            focus_hwnd, hwnd,
        )

    # KEYEVENTF_UNICODE 逐字符输入（不经过剪贴板）。
    # 实测在 WinUI3/WT 中 KEYEVENTF_UNICODE 能工作，而 Ctrl+V 粘贴不生效。
    logger.info("Typing text via KEYEVENTF_UNICODE (%d chars)", len(text))
    inject_text_unicode(text)

    time.sleep(0.1)

    # Enter 执行
    rc_enter_dn = _send_key(0x0D, key_down=True)
    rc_enter_up = _send_key(0x0D, key_down=False)
    logger.debug("SendInput Enter: down=%s up=%s", rc_enter_dn, rc_enter_up)

    time.sleep(inject_delay)
    logger.info(
        "Text injected to hwnd=%s (fg_before=%s focus=%s): %s",
        hwnd, fg_before, focus_hwnd, text[:50],
    )
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
