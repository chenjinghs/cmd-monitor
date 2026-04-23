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
    mock_user32.GetForegroundWindow.side_effect = [0, 12345]
    result = force_foreground(12345)
    assert result is True
    mock_user32.ShowWindow.assert_called_once()
    mock_user32.SetForegroundWindow.assert_called_once_with(12345)


# --- inject_text Tests ---


def test_inject_text_empty_text() -> None:
    """空文本返回 False"""
    result = inject_text(12345, "")
    assert result is False


@patch("cmd_monitor.input_injector.force_foreground")
def test_inject_text_no_window(mock_fg: MagicMock) -> None:
    """窗口不可用时返回 False"""
    mock_fg.return_value = False
    result = inject_text(12345, "test")
    assert result is False


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
    """成功注入文本"""
    mock_fg.return_value = True
    mock_clipboard.return_value = True
    result = inject_text(12345, "hello world", inject_delay=0.01)
    assert result is True
    # Should call Ctrl+V then Enter
    assert mock_send_key.call_count == 6  # Ctrl down, V down, V up, Ctrl up, Enter down, Enter up


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
