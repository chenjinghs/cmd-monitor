"""CLI 测试"""

from click.testing import CliRunner

from cmd_monitor.cli import main


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


def test_hook_handler_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["hook-handler", "--help"])
    assert result.exit_code == 0
    assert "--event" in result.output


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
