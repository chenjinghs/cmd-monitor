"""CLI 入口模块"""

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import click

from cmd_monitor.config import load_config
from cmd_monitor.logger import setup_logging


@click.group()
@click.option("--config", "-c", default="config/default.toml", help="配置文件路径")
@click.option("--log-level", "-l", default="INFO", help="日志级别")
@click.pass_context
def main(ctx: click.Context, config: str, log_level: str) -> None:
    """cmd-monitor: 终端监控 + IM 双向通信

    监控 Claude Code 等 AI CLI 工具，通过飞书/微信发送通知并接收回复。
    """
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)
    setup_logging(log_level)


def _get_pid_file(config: dict) -> Optional[Path]:
    p = config.get("general", {}).get("pid_file", "")
    return Path(p) if p else None


@main.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """启动守护进程"""
    config = ctx.obj["config"]
    feishu_config = config.get("feishu", {})

    if not feishu_config.get("app_id") or not feishu_config.get("app_secret"):
        click.echo("错误: 飞书 app_id 或 app_secret 未配置，请编辑 config/default.toml", err=True)
        return

    # Prevent double-start via PID file
    pid_file = _get_pid_file(config)
    if pid_file is not None:
        from cmd_monitor.daemon import is_alive, read_pid

        existing = read_pid(pid_file)
        if existing and is_alive(existing):
            click.echo(f"cmd-monitor 已在运行 (pid={existing})", err=True)
            sys.exit(1)

    try:
        from cmd_monitor.daemon import Daemon
    except ImportError as e:
        click.echo(f"daemon 依赖缺失: {e}", err=True)
        sys.exit(1)

    daemon = Daemon(config)
    click.echo("cmd-monitor 已启动，按 Ctrl+C 停止")
    try:
        sys.exit(daemon.run())
    except KeyboardInterrupt:
        daemon.stop()
        click.echo("cmd-monitor 已停止")


@main.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """停止守护进程"""
    from cmd_monitor.daemon import is_alive, read_pid, terminate

    config = ctx.obj["config"]
    pid_file = _get_pid_file(config)
    if pid_file is None:
        click.echo("未配置 pid_file，无法定位 daemon", err=True)
        sys.exit(1)
    pid = read_pid(pid_file)
    if pid is None or not is_alive(pid):
        click.echo("daemon 未运行")
        return
    if terminate(pid):
        click.echo(f"已终止 daemon (pid={pid})")
        try:
            pid_file.unlink()
        except OSError:
            pass
    else:
        click.echo(f"终止失败 (pid={pid})", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """查看运行状态"""
    from cmd_monitor.daemon import is_alive, read_pid
    from cmd_monitor.ipc import send_event

    config = ctx.obj["config"]
    pid_file = _get_pid_file(config)
    pid = read_pid(pid_file) if pid_file else None
    if pid is None or not is_alive(pid):
        click.echo("daemon 未运行")
        return
    resp = send_event({"type": "status"})
    if resp and resp.get("ok"):
        sessions = resp.get("sessions", [])
        tokens = {t["session_id"]: t["token"] for t in resp.get("tokens", [])}
        click.echo(f"daemon 运行中 (pid={pid}), 活跃 session: {len(sessions)}")
        for s in sessions:
            sid = s["session_id"]
            click.echo(
                f"  [{tokens.get(sid, '----')}] {sid[:12]}  cwd={s['cwd']}  tab={s['tab']}"
            )
    else:
        click.echo(f"daemon 运行中 (pid={pid}), 但 IPC 不可达")


@main.command("hook-handler")
@click.option(
    "--event",
    required=True,
    help="Hook event name (Notification, Stop, PermissionRequest)",
)
@click.pass_context
def hook_handler(ctx: click.Context, event: str) -> None:
    """处理 Claude Code hook 事件（内部命令）"""
    from cmd_monitor.hook_handler import build_claude_ipc_event

    input_json = sys.stdin.read().strip()
    if not input_json:
        click.echo("No input received", err=True)
        sys.exit(0)

    payload = build_claude_ipc_event(input_json)
    if payload is None:
        sys.exit(0)

    _augment_with_terminal_context(payload)
    _send_to_daemon(payload)
    sys.exit(0)


@main.command("copilot-hook-handler")
@click.option(
    "--event",
    required=True,
    help="Hook event (sessionStart, sessionEnd, userPromptSubmitted, preToolUse, postToolUse, errorOccurred)",
)
@click.pass_context
def copilot_hook_handler(ctx: click.Context, event: str) -> None:
    """处理 copilot-cli hook 事件（内部命令）"""
    from cmd_monitor.hook_handler import build_copilot_ipc_event

    input_json = sys.stdin.read().strip()
    if not input_json:
        click.echo("No input received", err=True)
        sys.exit(0)

    payload = build_copilot_ipc_event(input_json)
    if payload is None:
        sys.exit(0)

    _augment_with_terminal_context(payload)
    _send_to_daemon(payload)
    sys.exit(0)


def _augment_with_terminal_context(payload: dict) -> None:
    """采集 WT 上下文并注入到 payload(失败软退化)。"""
    try:
        from cmd_monitor.windows_term import collect_terminal_context

        ctx = collect_terminal_context()
        payload.setdefault("wt_session", ctx.wt_session)
        payload.setdefault("wt_window_id", ctx.wt_window_id)
        payload.setdefault("wt_tab_index", ctx.wt_tab_index)
        payload.setdefault("wt_window_hwnd", ctx.wt_window_hwnd)
        payload.setdefault("window_hwnd", ctx.window_hwnd)
    except Exception as e:
        # 不影响 hook 主流程
        click.echo(f"[warn] terminal context detection failed: {e}", err=True)


def _send_to_daemon(payload: dict) -> None:
    try:
        from cmd_monitor.ipc import send_event

        resp = send_event(payload, timeout_ms=2000)
        if resp is None:
            click.echo(
                "[warn] daemon 未运行或 IPC 不可达;事件未送达",
                err=True,
            )
    except Exception as e:
        click.echo(f"[warn] IPC error: {e}", err=True)


@main.command("hooks")
@click.argument("action", type=click.Choice(["install"]))
@click.option("--config-path", default=None, help="配置文件路径")
@click.option(
    "--type",
    "hook_type",
    type=click.Choice(["claude", "copilot", "all"]),
    default="all",
    help="安装哪种 hooks",
)
@click.pass_context
def hooks(
    ctx: click.Context,
    action: str,
    config_path: Optional[str],
    hook_type: str,
) -> None:
    """管理 hooks"""
    from cmd_monitor.hook_installer import install_copilot_hooks, install_hooks

    if action == "install":
        config = ctx.obj["config"]

        if hook_type in ("claude", "all"):
            events = config.get("hooks", {}).get("claude", {}).get("events")
            success = install_hooks(config_path=config_path, events=events)
            if success:
                click.echo("Claude Code hooks 已安装")
            else:
                click.echo("Claude Code hooks 安装失败", err=True)

        if hook_type in ("copilot", "all"):
            copilot_config = config.get("hooks", {}).get("copilot", {})
            events = copilot_config.get("events")
            config_dir = copilot_config.get("config_dir")
            success = install_copilot_hooks(config_dir=config_dir, events=events)
            if success:
                click.echo("copilot-cli hooks 已安装")
            else:
                click.echo("copilot-cli hooks 安装失败", err=True)


@main.command()
@click.option("--transcript", "-t", default=None, help="Transcript 文件路径")
@click.pass_context
def monitor(ctx: click.Context, transcript: Optional[str]) -> None:
    """监控 PowerShell transcript 文件"""
    from cmd_monitor.ps_monitor import PsMonitor

    config = ctx.obj["config"]
    ps_config = config.get("powershell", {})
    feishu_config = config.get("feishu", {})

    transcript_path = transcript or ps_config.get("transcript_path", "")
    if not transcript_path:
        click.echo(
            "错误: 未指定 transcript 文件路径，请使用 --transcript 或配置 powershell.transcript_path"
        )
        return

    bot = None
    if feishu_config.get("app_id") and feishu_config.get("app_secret"):
        from cmd_monitor.feishu_client import FeishuBot

        bot = FeishuBot(
            app_id=feishu_config["app_id"],
            app_secret=feishu_config["app_secret"],
            receiver_id=feishu_config.get("receiver_id", ""),
            receive_id_type=feishu_config.get("receive_id_type", "open_id"),
        )
        bot.start()

    from cmd_monitor.state_manager import StateManager

    state_config = config.get("state", {})
    state_manager = StateManager(
        debounce_seconds=float(state_config.get("debounce_seconds", 10.0)),
        notification_cooldown=float(state_config.get("notification_cooldown", 60.0)),
    )

    monitor_instance = PsMonitor(
        transcript_path=transcript_path,
        poll_interval=float(ps_config.get("poll_interval", 5)),
        idle_threshold=float(ps_config.get("idle_threshold", 10)),
        feishu_bot=bot,
        debounce_seconds=state_manager.debounce_seconds,
        notification_cooldown=state_manager.notification_cooldown,
    )

    click.echo(f"正在监控: {transcript_path}")
    try:
        monitor_instance.run()
    except KeyboardInterrupt:
        monitor_instance.stop()
        if bot:
            bot.stop()
        click.echo("监控已停止")


if __name__ == "__main__":
    main()
