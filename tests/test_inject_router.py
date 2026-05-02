"""inject_router 测试"""

from unittest.mock import MagicMock, patch

from cmd_monitor.inject_router import _focus_wt_tab, inject_to_session
from cmd_monitor.session_registry import SessionInfo


# --- _focus_wt_tab tests ---


@patch("cmd_monitor.inject_router._find_wt_exe")
@patch("cmd_monitor.inject_router.subprocess.run")
def test_focus_wt_tab_omits_window_arg_when_window_id_zero(
    mock_run: MagicMock, mock_find_wt: MagicMock
) -> None:
    """window_id=0 时省略 --window 参数"""
    mock_find_wt.return_value = "wt.exe"
    mock_run.return_value = MagicMock(returncode=0, stderr=b"")

    _focus_wt_tab(window_id=0, tab_index=2)

    args = mock_run.call_args[0][0]
    assert args == ["wt.exe", "focus-tab", "--target", "2"]
    assert "--window" not in args


@patch("cmd_monitor.inject_router._find_wt_exe")
@patch("cmd_monitor.inject_router.subprocess.run")
def test_focus_wt_tab_omits_window_arg_when_window_id_negative(
    mock_run: MagicMock, mock_find_wt: MagicMock
) -> None:
    """window_id=-1 时省略 --window 参数"""
    mock_find_wt.return_value = "wt.exe"
    mock_run.return_value = MagicMock(returncode=0, stderr=b"")

    _focus_wt_tab(window_id=-1, tab_index=1)

    args = mock_run.call_args[0][0]
    assert "--window" not in args


@patch("cmd_monitor.inject_router._find_wt_exe")
@patch("cmd_monitor.inject_router.subprocess.run")
def test_focus_wt_tab_includes_window_arg_when_window_id_positive(
    mock_run: MagicMock, mock_find_wt: MagicMock
) -> None:
    """window_id>0 时附加 --window 参数"""
    mock_find_wt.return_value = "wt.exe"
    mock_run.return_value = MagicMock(returncode=0, stderr=b"")

    _focus_wt_tab(window_id=5, tab_index=3)

    args = mock_run.call_args[0][0]
    assert args == ["wt.exe", "--window", "5", "focus-tab", "--target", "3"]


@patch("cmd_monitor.inject_router._find_wt_exe")
def test_focus_wt_tab_negative_tab_index_returns_false(mock_find_wt: MagicMock) -> None:
    """tab_index<0 时直接返回 False，不调用 wt.exe"""
    mock_find_wt.return_value = "wt.exe"

    result = _focus_wt_tab(window_id=1, tab_index=-1)

    assert result is False
    mock_find_wt.assert_not_called()


@patch("cmd_monitor.inject_router._find_wt_exe")
def test_focus_wt_tab_no_wt_exe_returns_false(mock_find_wt: MagicMock) -> None:
    """wt.exe 找不到时返回 False"""
    mock_find_wt.return_value = None

    result = _focus_wt_tab(window_id=1, tab_index=0)

    assert result is False


@patch("cmd_monitor.inject_router._find_wt_exe")
@patch("cmd_monitor.inject_router.subprocess.run")
def test_focus_wt_tab_subprocess_error_returns_false(
    mock_run: MagicMock, mock_find_wt: MagicMock
) -> None:
    """subprocess 异常时返回 False"""
    mock_find_wt.return_value = "wt.exe"
    mock_run.side_effect = OSError("permission denied")

    result = _focus_wt_tab(window_id=0, tab_index=0)

    assert result is False


# --- inject_to_session tests ---


@patch("cmd_monitor.inject_router._focus_wt_tab")
@patch("cmd_monitor.inject_router.force_foreground")
@patch("cmd_monitor.inject_router._click_window_center")
@patch("cmd_monitor.inject_router.inject_text")
def test_inject_to_session_wt_path_uses_skip_foreground(
    mock_inject: MagicMock,
    mock_click: MagicMock,
    mock_fg: MagicMock,
    mock_focus: MagicMock,
) -> None:
    """WT 路径调用 inject_text 时 skip_foreground=True"""
    mock_focus.return_value = True
    mock_inject.return_value = True

    info = SessionInfo(
        session_id="sess_12345678",
        wt_session="wt-sess-guid",
        wt_window_id=1,
        wt_tab_index=0,
        wt_window_hwnd=12345,
    )

    result = inject_to_session(info, "hello")

    assert result is True
    mock_inject.assert_called_once_with(
        12345, "hello", inject_delay=0.5, skip_foreground=True
    )


@patch("cmd_monitor.inject_router.find_first_window")
@patch("cmd_monitor.inject_router.inject_text")
def test_inject_to_session_fallback_title(mock_inject: MagicMock, mock_find: MagicMock) -> None:
    """无 hwnd 时按 fallback_title 查找窗口注入"""
    mock_find.return_value = MagicMock(hwnd=99999)
    mock_inject.return_value = True

    info = SessionInfo(session_id="sess_12345678")
    result = inject_to_session(info, "test", fallback_title="PowerShell")

    assert result is True
    mock_inject.assert_called_once_with(99999, "test", inject_delay=0.5)


def test_inject_to_session_empty_text() -> None:
    """空文本直接返回 False"""
    info = SessionInfo(session_id="sess_12345678")
    result = inject_to_session(info, "")
    assert result is False
