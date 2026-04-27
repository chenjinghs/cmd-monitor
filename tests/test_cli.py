"""CLI 测试"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cmd_monitor.cli import main


DEFAULT_CONFIG = {
    "general": {"pid_file": "cmd-monitor.pid"},
    "hooks": {
        "claude": {"config_path": ".claude/settings.json"},
        "copilot": {"config_dir": ".github/hooks"},
    },
}


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "cmd-monitor" in result.output


def test_start_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["start", "--help"])
    assert result.exit_code == 0
    assert "启动守护进程" in result.output


def test_stop_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--help"])
    assert result.exit_code == 0
    assert "停止守护进程" in result.output


def test_status_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--help"])
    assert result.exit_code == 0
    assert "查看运行状态" in result.output


def test_doctor_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "自检" in result.output


def test_hook_handler_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["hook-handler", "--help"])
    assert result.exit_code == 0
    assert "--event" in result.output
    assert "AskUserQuestion" in result.output


def test_hooks_install_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["hooks", "--help"])
    assert result.exit_code == 0
    assert "install" in result.output
    assert "--type" in result.output


def test_copilot_hook_handler_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["copilot-hook-handler", "--help"])
    assert result.exit_code == 0
    assert "--event" in result.output
    assert "sessionStart" in result.output


def test_doctor_reports_all_checks_happy_path() -> None:
    runner = CliRunner()
    with patch("cmd_monitor.cli.load_config", return_value=DEFAULT_CONFIG), patch(
        "cmd_monitor.daemon.read_pid", return_value=43324
    ), patch("cmd_monitor.daemon.is_alive", return_value=True), patch(
        "cmd_monitor.ipc.send_event",
        return_value={"ok": True},
    ), patch(
        "cmd_monitor.hook_installer.claude_hooks_are_configured",
        return_value=True,
    ), patch(
        "cmd_monitor.hook_installer.copilot_hooks_are_configured",
        return_value=True,
    ):
        result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "[ok] daemon alive (pid=43324)" in result.output
    assert "[ok] IPC reachable" in result.output
    assert "[ok] Claude hooks configured" in result.output
    assert "[ok] Copilot hooks configured" in result.output




def test_status_shows_wt_session_when_tab_unknown() -> None:
    runner = CliRunner()
    with patch("cmd_monitor.cli.load_config", return_value=DEFAULT_CONFIG), patch(
        "cmd_monitor.daemon.read_pid", return_value=43324
    ), patch("cmd_monitor.daemon.is_alive", return_value=True), patch(
        "cmd_monitor.ipc.send_event",
        return_value={
            "ok": True,
            "sessions": [
                {
                    "session_id": "sess-1234567890",
                    "cwd": "E:/repo",
                    "tab": -1,
                    "wt_session": "wt-guid-1234",
                    "hwnd": 999,
                }
            ],
            "tokens": [{"session_id": "sess-1234567890", "token": "abcd"}],
        },
    ):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "tab=?" in result.output
    assert "wt=wt-guid-" in result.output
    assert "hwnd=999" in result.output


def test_monitor_passes_daemon_notification_callback() -> None:
    runner = CliRunner()
    config = {
        "powershell": {"transcript_path": "C:\\trace.txt"},
        "feishu": {},
        "state": {},
    }
    monitor_instance = MagicMock()
    monitor_cls = MagicMock(return_value=monitor_instance)
    with patch("cmd_monitor.cli.load_config", return_value=config), patch(
        "cmd_monitor.ps_monitor.PsMonitor",
        monitor_cls,
    ), patch(
        "cmd_monitor.ipc.send_event",
        return_value={"ok": True, "notified": True},
    ):
        result = runner.invoke(main, ["monitor"])

    assert result.exit_code == 0
    callback = monitor_cls.call_args.kwargs["notification_callback"]
    assert callback({"type": "transcript_idle"}) is True


def test_monitor_daemon_callback_treats_suppressed_as_handled() -> None:
    runner = CliRunner()
    config = {
        "powershell": {"transcript_path": "C:\\trace.txt"},
        "feishu": {},
        "state": {},
    }
    monitor_instance = MagicMock()
    monitor_cls = MagicMock(return_value=monitor_instance)
    with patch("cmd_monitor.cli.load_config", return_value=config), patch(
        "cmd_monitor.ps_monitor.PsMonitor",
        monitor_cls,
    ), patch(
        "cmd_monitor.ipc.send_event",
        return_value={"ok": True, "notified": False, "reason": "suppressed"},
    ):
        result = runner.invoke(main, ["monitor"])

    assert result.exit_code == 0
    callback = monitor_cls.call_args.kwargs["notification_callback"]
    assert callback({"type": "transcript_idle"}) is True


def test_monitor_daemon_callback_falls_back_when_no_matching_session() -> None:
    runner = CliRunner()
    config = {
        "powershell": {"transcript_path": "C:\\trace.txt"},
        "feishu": {},
        "state": {},
    }
    monitor_instance = MagicMock()
    monitor_cls = MagicMock(return_value=monitor_instance)
    with patch("cmd_monitor.cli.load_config", return_value=config), patch(
        "cmd_monitor.ps_monitor.PsMonitor",
        monitor_cls,
    ), patch(
        "cmd_monitor.ipc.send_event",
        return_value={"ok": True, "notified": False, "reason": "no_session"},
    ):
        result = runner.invoke(main, ["monitor"])

    assert result.exit_code == 0
    callback = monitor_cls.call_args.kwargs["notification_callback"]
    assert callback({"type": "transcript_idle"}) is False

