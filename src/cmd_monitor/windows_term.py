"""Windows Terminal 上下文采集

在 hook handler 进程中调用,收集足以定位当前 tab 的信息:
- WT_SESSION 环境变量(每个 tab 一个 GUID)
- 父进程链中的 Windows Terminal 主窗口 hwnd
- (best-effort) tab 索引 — 通过 uiautomation 扫描 tab 列表

所有失败都是软失败:返回部分填充的字典,daemon 端有降级注入策略。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

WT_PROCESS_NAMES = {"windowsterminal.exe", "wt.exe"}


@dataclass(frozen=True)
class TerminalContext:
    """采集到的终端上下文(可全部为空)。"""

    wt_session: str = ""
    wt_window_id: int = 0
    wt_tab_index: int = -1
    wt_window_hwnd: int = 0
    window_hwnd: int = 0


def _get_wt_session() -> str:
    return os.environ.get("WT_SESSION", "").strip()


def _find_wt_window_hwnd() -> int:
    """沿父进程链向上查 WindowsTerminal.exe 的主窗口 hwnd。"""
    pid = _find_wt_window_pid()
    if pid:
        return _hwnd_from_pid(pid)
    # psutil 不可用时退回 toolhelp32
    return _find_wt_via_toolhelp()


def _find_wt_via_toolhelp() -> int:
    """无 psutil 时用 pywin32 toolhelp32 查父进程。"""
    try:
        import win32process  # type: ignore
        import psutil  # type: ignore  # noqa: F401
    except ImportError:
        return 0

    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return 0
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            mapping: dict[int, tuple[int, str]] = {}
            if kernel32.Process32First(snap, ctypes.byref(entry)):
                while True:
                    name = entry.szExeFile.decode("ascii", "replace").lower()
                    mapping[entry.th32ProcessID] = (entry.th32ParentProcessID, name)
                    if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                        break
            pid = os.getpid()
            for _ in range(10):
                parent_name = mapping.get(pid)
                if parent_name is None:
                    break
                ppid, name = parent_name
                if name in WT_PROCESS_NAMES:
                    return _hwnd_from_pid(pid)
                pid = ppid
        finally:
            kernel32.CloseHandle(snap)
    except Exception as e:
        logger.debug("toolhelp32 walk failed: %s", e)
    return 0


def _hwnd_from_pid(pid: int) -> int:
    """枚举顶层窗口,返回属于 pid 的第一个可见窗口。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return 0

    user32 = ctypes.windll.user32
    found = [0]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        out_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(out_pid))
        if out_pid.value == pid:
            found[0] = hwnd
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0]


def _find_wt_window_pid() -> int:
    """返回父进程链里第一个 WindowsTerminal/wt 的 PID。"""
    try:
        import psutil  # type: ignore
    except ImportError:
        return 0
    try:
        proc = psutil.Process(os.getpid())
        for ancestor in [proc, *proc.parents()]:
            if ancestor.name().lower() in WT_PROCESS_NAMES:
                return ancestor.pid
    except Exception as e:
        logger.debug("psutil parent walk failed: %s", e)
    return 0


def _is_tab_item_selected(item: object) -> bool:
    """判断 TabItemControl 是否被选中。

    WinUI3 ListViewItem 的 SelectionItemPattern 有时无法正确反映选中状态,
    依次尝试三种策略:
    1. SelectionItemPattern.IsSelected
    2. LegacyIAccessible STATE_SYSTEM_SELECTED (0x2)
    3. HasKeyboardFocus (最后手段)
    """
    # Strategy 1: SelectionItemPattern
    try:
        pattern = item.GetSelectionItemPattern()  # type: ignore[attr-defined]
        if pattern and pattern.IsSelected:
            return True
    except Exception:
        pass

    # Strategy 2: LegacyIAccessible STATE_SYSTEM_SELECTED
    try:
        legacy = item.GetLegacyIAccessiblePattern()  # type: ignore[attr-defined]
        if legacy:
            STATE_SYSTEM_SELECTED = 0x2
            if legacy.CurrentState & STATE_SYSTEM_SELECTED:
                return True
    except Exception:
        pass

    # Strategy 3: keyboard focus (best-effort)
    try:
        if item.HasKeyboardFocus:  # type: ignore[attr-defined]
            return True
    except Exception:
        pass

    return False


def _find_my_tab_index(wt_pid: int) -> int:
    """通过进程树定位当前进程所属的 tab 索引(0-based)。

    策略: WT 为每个 tab 直接创建一个 shell 子进程(pwsh/cmd/bash 等),
    将这些直接子 shell 按创建时间排序后的下标即为 tab 索引。
    找到当前进程祖先链中属于 WT 直接子进程的 shell,返回其下标。

    相比 uiautomation "找当前选中 tab" 的方案,本方法返回的是
    当前进程**实际所在**的 tab,不受焦点影响,多 tab 场景下准确可靠。

    返回 -1 表示无法识别。
    """
    if wt_pid <= 0:
        return -1
    try:
        import psutil  # type: ignore
    except ImportError:
        return -1

    try:
        wt_proc = psutil.Process(wt_pid)

        # WT 直接子 shell 进程(每个 tab 一个)
        SHELL_NAMES = {"pwsh.exe", "powershell.exe", "cmd.exe", "bash.exe", "wsl.exe", "nu.exe"}
        direct_shells = []
        for child in wt_proc.children():
            try:
                if child.name().lower() in SHELL_NAMES:
                    direct_shells.append(child)
            except Exception:
                continue
        if not direct_shells:
            logger.debug("No direct shell children found under WT pid=%s", wt_pid)
            return -1

        # 按创建时间排序 => tab 创建顺序即下标
        direct_shells.sort(key=lambda p: p.create_time())
        shell_pids = {p.pid for p in direct_shells}

        # 从当前进程向上找第一个属于 WT 直接子的祖先
        proc = psutil.Process(os.getpid())
        for ancestor in [proc, *proc.parents()]:
            if ancestor.pid in shell_pids:
                idx = next(i for i, p in enumerate(direct_shells) if p.pid == ancestor.pid)
                logger.debug(
                    "tab index resolved: pid=%s -> ancestor_shell=%s -> idx=%s",
                    os.getpid(), ancestor.pid, idx,
                )
                return idx
        logger.debug("No ancestor shell found in direct WT children")
    except Exception as e:
        logger.debug("process-tree tab index detection failed: %s", e)
    return -1


def _find_selected_tab_index(wt_pid: int) -> int:
    """在指定 PID 的 WT 窗口里,返回当前选中 tab 的索引(0-based)。

    已被 _find_my_tab_index 取代(后者通过进程树精确定位当前 tab,
    不依赖 UI 焦点)。保留此函数作为 fallback。

    返回 -1 表示无法识别。
    """
    if wt_pid <= 0:
        return -1
    try:
        import uiautomation as auto  # type: ignore
    except ImportError:
        logger.debug("uiautomation not installed, skip tab index detection")
        return -1

    try:
        wt_window = None
        for w in auto.GetRootControl().GetChildren():
            try:
                if (
                    w.ProcessId == wt_pid
                    and w.ControlTypeName == "WindowControl"
                    and w.ClassName == "CASCADIA_HOSTING_WINDOW_CLASS"
                ):
                    wt_window = w
                    break
            except Exception:
                continue
        if wt_window is None:
            logger.debug("WT window control not found for pid=%s", wt_pid)
            return -1

        tabs = wt_window.TabControl(searchDepth=5)
        if not tabs.Exists(maxSearchSeconds=0.3):
            logger.debug("TabControl not found in WT window")
            return -1

        tab_items: list = []
        stack = list(tabs.GetChildren())
        while stack:
            node = stack.pop(0)
            try:
                if node.ControlTypeName == "TabItemControl":
                    tab_items.append(node)
                else:
                    stack.extend(node.GetChildren())
            except Exception:
                continue

        for idx, item in enumerate(tab_items):
            if _is_tab_item_selected(item):
                return idx
        logger.debug(
            "No selected tab found among %d tab items",
            len(tab_items),
        )
        return -1
    except Exception as e:
        logger.debug("uiautomation tab scan failed: %s", e)
    return -1


def collect_terminal_context() -> TerminalContext:
    """采集当前进程的终端上下文。"""
    wt_session = _get_wt_session()
    wt_pid = _find_wt_window_pid()
    wt_hwnd = _hwnd_from_pid(wt_pid) if wt_pid else (_find_wt_via_toolhelp() if wt_session else 0)
    tab_idx = _find_my_tab_index(wt_pid) if wt_pid else -1
    if tab_idx < 0 and wt_pid:
        tab_idx = _find_selected_tab_index(wt_pid)
    # 没有 WT 时退回到当前控制台窗口
    fallback_hwnd = 0
    if not wt_hwnd:
        try:
            import ctypes

            fallback_hwnd = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
        except Exception:
            fallback_hwnd = 0

    return TerminalContext(
        wt_session=wt_session,
        wt_window_id=0,  # WT --window 多窗口暂未自动发现,默认 0
        wt_tab_index=tab_idx,
        wt_window_hwnd=wt_hwnd,
        window_hwnd=fallback_hwnd,
    )
