"""指令注入模块测试"""

import ctypes
from unittest.mock import MagicMock, patch

from cmd_monitor.input_injector import (
    CF_UNICODETEXT,
    GMEM_MOVEABLE,
    GMEM_ZEROINIT,
    INPUT,
    INPUT_KEYBOARD,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    WindowInfo,
    _ensure_paste_ready,
    _is_paste_ready,
    find_first_window,
    find_windows,
    force_foreground,
    inject_text,
    inject_to_window,
)


# --- Win32 Constants Tests ---


def test_input_constants() -> None:
    """Win32 常量值正确"""
    assert INPUT_KEYBOARD == 1
    assert KEYEVENTF_KEYUP == 0x0002
    assert CF_UNICODETEXT == 13
    assert GMEM_MOVEABLE == 0x0002
    assert GMEM_ZEROINIT == 0x0040


def test_input_structures() -> None:
    """ctypes Structure 大小正确"""
    assert ctypes.sizeof(KEYBDINPUT) > 0
    assert ctypes.sizeof(INPUT) > 0


# --- WindowInfo Tests ---


def test_window_info_dataclass() -> None:
    """数据类字段"""
    w = WindowInfo(hwnd=123, title="PowerShell", pid=456)
    assert w.hwnd == 123
    assert w.title == "PowerShell"
    assert w.pid == 456


def test_window_info_default_pid() -> None:
    """pid 默认为 0"""
    w = WindowInfo(hwnd=1, title="test")
    assert w.pid == 0


# --- find_windows Tests ---


@patch("cmd_monitor.input_injector.user32")
def test_find_windows_returns_list(mock_user32: MagicMock) -> None:
    """mock EnumWindows 返回窗口列表"""
    # Simulate EnumWindows calling callback with one window
    def simulate_enum_wnd(proc, lparam):
        # Simulate a visible window with matching title
        mock_user32.IsWindowVisible.return_value = True
        mock_user32.GetWindowTextLengthW.return_value = 11
        # Create a buffer that will be filled
        buf = MagicMock()
        buf.value = "PowerShell"
        mock_user32.GetWindowTextW.side_effect = lambda hwnd, buf_arg, n: None

        # We can't easily test the callback directly since EnumWindows
        # calls it internally. Instead, test that find_windows returns a list.
        return 0

    mock_user32.EnumWindows.side_effect = simulate_enum_wnd
    result = find_windows("PowerShell")
    assert isinstance(result, list)


@patch("cmd_monitor.input_injector.user32")
def test_find_first_window_returns_none(mock_user32: MagicMock) -> None:
    """无匹配时返回 None"""
    # EnumWindows calls callback but no windows match
    def simulate_enum_wnd(proc, lparam):
        # Simulate no visible windows
        mock_user32.IsWindowVisible.return_value = False
        return 0

    mock_user32.EnumWindows.side_effect = simulate_enum_wnd
    result = find_first_window("NonExistent")
    assert result is None


# --- force_foreground Tests ---


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.time")
def test_force_foreground_already_focused(
    mock_time: MagicMock, mock_user32: MagicMock
) -> None:
    """已在前台时直接返回 True"""
    mock_user32.GetForegroundWindow.return_value = 12345
    result = force_foreground(12345)
    assert result is True
    # Should not call SetForegroundWindow since already focused
    mock_user32.SetForegroundWindow.assert_not_called()


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.time")
def test_force_foreground_needs_switch(
    mock_time: MagicMock, mock_user32: MagicMock
) -> None:
    """需要切换窗口时使用 Alt-key trick"""
    mock_user32.GetForegroundWindow.side_effect = [0, 12345, 12345]
    result = force_foreground(12345)
    assert result is True
    mock_user32.ShowWindow.assert_called_once()
    mock_user32.SetForegroundWindow.assert_called_once_with(12345)


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.time")
def test_force_foreground_uipi_fallback_when_fg_is_none(
    mock_time: MagicMock, mock_user32: MagicMock
) -> None:
    """ctypes HWND restype 在 NULL 时返回 Python None — UIPI fallback 应该命中,
    而不是与整数 0 比较失败 (回归: 之前 fg_final == 0 永远为 False)。
    """
    mock_user32.IsWindow.return_value = True
    mock_user32.IsIconic.return_value = False
    mock_user32.IsWindowVisible.return_value = True
    # GetForegroundWindow 始终返回 None,模拟 UIPI 限制
    mock_user32.GetForegroundWindow.return_value = None

    result = force_foreground(12345)

    assert result is True


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.time")
def test_force_foreground_uipi_fallback_skipped_if_target_invisible(
    mock_time: MagicMock, mock_user32: MagicMock
) -> None:
    """UIPI fallback 仅在目标窗口可见时触发,不可见时仍然返回 False。"""
    mock_user32.IsWindow.return_value = True
    mock_user32.IsIconic.return_value = False
    mock_user32.IsWindowVisible.return_value = False
    mock_user32.GetForegroundWindow.return_value = None

    result = force_foreground(12345)

    assert result is False


# --- _is_paste_ready Tests ---


@patch("cmd_monitor.input_injector.user32")
def test_is_paste_ready_target_is_foreground(mock_user32: MagicMock) -> None:
    """前台命中目标 hwnd 时直接 ready"""
    assert _is_paste_ready(12345, 12345) is True


@patch("cmd_monitor.input_injector.user32")
def test_is_paste_ready_uipi_fg_none_target_visible(mock_user32: MagicMock) -> None:
    """UIPI 限制下 fg=None,目标窗口可见 — 视为 ready (回归: 之前 None == 0 为 False)"""
    mock_user32.IsWindowVisible.return_value = True
    assert _is_paste_ready(12345, None) is True


@patch("cmd_monitor.input_injector.user32")
def test_is_paste_ready_uipi_fg_zero_target_visible(mock_user32: MagicMock) -> None:
    """整数 0 也应被当作 NULL HWND 处理"""
    mock_user32.IsWindowVisible.return_value = True
    assert _is_paste_ready(12345, 0) is True


@patch("cmd_monitor.input_injector.user32")
def test_is_paste_ready_other_fg_not_ready(mock_user32: MagicMock) -> None:
    """前台是其他窗口时 not ready"""
    mock_user32.IsWindowVisible.return_value = True
    assert _is_paste_ready(12345, 99999) is False


# --- inject_text Tests ---


def test_inject_text_empty_text() -> None:
    """空文本返回 False"""
    result = inject_text(12345, "")
    assert result is False


@patch("cmd_monitor.input_injector.force_foreground")
def test_inject_text_fallback_when_not_foreground(mock_fg: MagicMock) -> None:
    """前台切换失败时仍尝试注入（fallback 逻辑）"""
    mock_fg.return_value = False
    result = inject_text(12345, "test")
    assert result is True


@patch("cmd_monitor.input_injector._set_clipboard_text")
@patch("cmd_monitor.input_injector.force_foreground")
@patch("cmd_monitor.input_injector._send_key")
@patch("cmd_monitor.input_injector.time")
def test_inject_text_success(
    mock_time: MagicMock,
    mock_send_key: MagicMock,
    mock_fg: MagicMock,
    mock_clipboard: MagicMock,
) -> None:
    """成功注入文本（剪贴板 + Ctrl+V）"""
    mock_fg.return_value = True
    mock_clipboard.return_value = True
    result = inject_text(12345, "hello world", inject_delay=0.01)
    assert result is True
    mock_clipboard.assert_called_once_with("hello world")
    # Ctrl+V: Ctrl down, V down, V up, Ctrl up + Enter down, Enter up = 6 calls
    assert mock_send_key.call_count == 6


# --- _ensure_paste_ready Tests ---


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.force_foreground")
@patch("cmd_monitor.input_injector.time")
def test_ensure_paste_ready_skip_foreground_bypasses_retry(
    mock_time: MagicMock, mock_fg: MagicMock, mock_user32: MagicMock
) -> None:
    """skip_foreground=True 时，即使 fg 不匹配也不重试 force_foreground"""
    mock_user32.GetForegroundWindow.return_value = 99999
    mock_user32.IsWindowVisible.return_value = True

    result = _ensure_paste_ready(12345, skip_foreground=True)

    mock_fg.assert_not_called()
    assert result == 99999


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.force_foreground")
@patch("cmd_monitor.input_injector.time")
def test_ensure_paste_ready_skip_foreground_uipi_null_fg(
    mock_time: MagicMock, mock_fg: MagicMock, mock_user32: MagicMock
) -> None:
    """skip_foreground=True 且 UIPI 导致 fg=None 时，不重试直接返回"""
    mock_user32.GetForegroundWindow.return_value = None
    mock_user32.IsWindowVisible.return_value = True

    result = _ensure_paste_ready(12345, skip_foreground=True)

    mock_fg.assert_not_called()
    assert result is None


@patch("cmd_monitor.input_injector.user32")
@patch("cmd_monitor.input_injector.force_foreground")
@patch("cmd_monitor.input_injector.time")
def test_ensure_paste_ready_without_skip_calls_force_foreground(
    mock_time: MagicMock, mock_fg: MagicMock, mock_user32: MagicMock
) -> None:
    """skip_foreground=False（默认）时，fg 不匹配会调用 force_foreground"""
    mock_user32.GetForegroundWindow.side_effect = [99999, 12345]
    mock_user32.IsWindowVisible.return_value = True
    mock_fg.return_value = True

    result = _ensure_paste_ready(12345)

    mock_fg.assert_called_once_with(12345)
    assert result == 12345


# --- inject_to_window Tests ---


@patch("cmd_monitor.input_injector.find_first_window")
def test_inject_to_window_not_found(mock_find: MagicMock) -> None:
    """找不到窗口返回 False"""
    mock_find.return_value = None
    result = inject_to_window("PowerShell", "test")
    assert result is False


@patch("cmd_monitor.input_injector.inject_text")
@patch("cmd_monitor.input_injector.find_first_window")
def test_inject_to_window_success(
    mock_find: MagicMock, mock_inject: MagicMock
) -> None:
    """成功注入"""
    mock_find.return_value = WindowInfo(hwnd=12345, title="PowerShell")
    mock_inject.return_value = True
    result = inject_to_window("PowerShell", "test", inject_delay=0.01)
    assert result is True
    mock_inject.assert_called_once_with(12345, "test", 0.01)
