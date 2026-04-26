"""windows_term 模块测试"""

from unittest.mock import patch

from cmd_monitor.windows_term import collect_terminal_context


def test_collect_terminal_context_prefers_process_tree_tab_index() -> None:
    with (
        patch("cmd_monitor.windows_term._get_wt_session", return_value="wt-session"),
        patch("cmd_monitor.windows_term._find_wt_window_pid", return_value=123),
        patch("cmd_monitor.windows_term._hwnd_from_pid", return_value=456),
        patch("cmd_monitor.windows_term._find_my_tab_index", return_value=2),
        patch("cmd_monitor.windows_term._find_selected_tab_index") as selected_tab,
    ):
        ctx = collect_terminal_context()

    assert ctx.wt_session == "wt-session"
    assert ctx.wt_window_hwnd == 456
    assert ctx.wt_tab_index == 2
    selected_tab.assert_not_called()


def test_collect_terminal_context_falls_back_to_selected_tab_index() -> None:
    with (
        patch("cmd_monitor.windows_term._get_wt_session", return_value="wt-session"),
        patch("cmd_monitor.windows_term._find_wt_window_pid", return_value=123),
        patch("cmd_monitor.windows_term._hwnd_from_pid", return_value=456),
        patch("cmd_monitor.windows_term._find_my_tab_index", return_value=-1),
        patch("cmd_monitor.windows_term._find_selected_tab_index", return_value=4),
    ):
        ctx = collect_terminal_context()

    assert ctx.wt_tab_index == 4


def test_collect_terminal_context_keeps_unknown_tab_when_both_strategies_fail() -> None:
    with (
        patch("cmd_monitor.windows_term._get_wt_session", return_value="wt-session"),
        patch("cmd_monitor.windows_term._find_wt_window_pid", return_value=123),
        patch("cmd_monitor.windows_term._hwnd_from_pid", return_value=456),
        patch("cmd_monitor.windows_term._find_my_tab_index", return_value=-1),
        patch("cmd_monitor.windows_term._find_selected_tab_index", return_value=-1),
    ):
        ctx = collect_terminal_context()

    assert ctx.wt_tab_index == -1
