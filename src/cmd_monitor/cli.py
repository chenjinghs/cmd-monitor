"""CLI 入口模块"""

import sys
import time
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


@main.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """启动守护进程"""
    from cmd_monitor.feishu_client import FeishuBot

    config = ctx.obj["config"]
    feishu_config = config.get("feishu", {})

    if not feishu_config.get("app_id") or not feishu_config.get("app_secret"):
        click.echo("错误: 飞书 app_id 或 app_secret 未配置，请编辑 config/default.toml")
        return

    bot = FeishuBot(
        app_id=feishu_config["app_id"],
        app_secret=feishu_config["app_secret"],
        receiver_id=feishu_config.get("receiver_id", ""),
    )

    # 创建状态管理器
    from cmd_monitor.state_manager import StateManager

    state_config = config.get("state", {})
    state_manager = StateManager(
        debounce_seconds=float(state_config.get("debounce_seconds", 10.0)),
        notification_cooldown=float(state_config.get("notification_cooldown", 60.0)),
    )

    # 设置消息回调 — 收到飞书回复后注入终端
    def on_message(msg: Any) -> None:
        from cmd_monitor.input_injector import inject_to_window
        from cmd_monitor.state_manager import SessionState

        click.echo(f"[飞书] {msg.sender_id}: {msg.content}")
        # 用户回复 → 重置状态为 RUNNING
        state_manager.transition(SessionState.RUNNING)
        inject_config = config.get("inject", {})
        method = inject_config.get("method", "sendkeys")
        target = inject_config.get("target_window", "PowerShell")
        delay = float(inject_config.get("inject_delay", 0.5))

        if method == "sendkeys":
            success = inject_to_window(target, msg.content, inject_delay=delay)
            if success:
                click.echo(f"[注入] 已发送到 {target}")
            else:
                click.echo(f"[注入] 发送失败: 未找到窗口 {target}", err=True)

    bot.set_message_callback(on_message)

    if bot.start():
        click.echo("cmd-monitor 已启动，按 Ctrl+C 停止")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            bot.stop()
            click.echo("cmd-monitor 已停止")
    else:
        click.echo("启动失败，请检查配置和网络连接")


@main.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """停止守护进程"""
    click.echo("Stopping cmd-monitor daemon...")
    # Phase 2+ 实现


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """查看运行状态"""
    click.echo("cmd-monitor status: not implemented")
    # Phase 2+ 实现


@main.command("hook-handler")
@click.option("--event", required=True, help="Hook event name (Notification, Stop, PermissionRequest)")
@click.pass_context
def hook_handler(ctx: click.Context, event: str) -> None:
    """处理 Claude Code hook 事件（内部命令）"""
    from cmd_monitor.feishu_client import FeishuBot
    from cmd_monitor.hook_handler import handle_hook_event

    config = ctx.obj["config"]
    feishu_config = config.get("feishu", {})

    # Read stdin (Claude Code sends JSON via stdin)
    input_json = sys.stdin.read().strip()
    if not input_json:
        click.echo("No input received", err=True)
        sys.exit(0)

    # Create FeishuBot for sending notifications
    bot = None
    if feishu_config.get("app_id") and feishu_config.get("app_secret"):
        bot = FeishuBot(
            app_id=feishu_config["app_id"],
            app_secret=feishu_config["app_secret"],
            receiver_id=feishu_config.get("receiver_id", ""),
        )
        bot.start()

    # Create StateManager for notification dedup
    from cmd_monitor.state_manager import StateManager

    state_config = config.get("state", {})
    state_manager = StateManager(
        debounce_seconds=float(state_config.get("debounce_seconds", 10.0)),
        notification_cooldown=float(state_config.get("notification_cooldown", 60.0)),
    )

    exit_code = handle_hook_event(input_json, bot, state_manager=state_manager)

    if bot:
        bot.stop()

    sys.exit(exit_code)


@main.command("copilot-hook-handler")
@click.option("--event", required=True,
    help="Hook event (sessionStart, sessionEnd, userPromptSubmitted, preToolUse, postToolUse, errorOccurred)")
@click.pass_context
def copilot_hook_handler(ctx: click.Context, event: str) -> None:
    """处理 copilot-cli hook 事件（内部命令）"""
    from cmd_monitor.feishu_client import FeishuBot
    from cmd_monitor.hook_handler import handle_copilot_hook_event

    config = ctx.obj["config"]
    feishu_config = config.get("feishu", {})

    input_json = sys.stdin.read().strip()
    if not input_json:
        click.echo("No input received", err=True)
        sys.exit(0)

    bot = None
    if feishu_config.get("app_id") and feishu_config.get("app_secret"):
        bot = FeishuBot(
            app_id=feishu_config["app_id"],
            app_secret=feishu_config["app_secret"],
            receiver_id=feishu_config.get("receiver_id", ""),
        )
        bot.start()

    # Create StateManager for notification dedup
    from cmd_monitor.state_manager import StateManager

    state_config = config.get("state", {})
    state_manager = StateManager(
        debounce_seconds=float(state_config.get("debounce_seconds", 10.0)),
        notification_cooldown=float(state_config.get("notification_cooldown", 60.0)),
    )

    exit_code = handle_copilot_hook_event(input_json, bot, state_manager=state_manager)

    if bot:
        bot.stop()

    sys.exit(exit_code)


@main.command("hooks")
@click.argument("action", type=click.Choice(["install"]))
@click.option("--config-path", default=None, help="配置文件路径")
@click.option("--type", "hook_type", type=click.Choice(["claude", "copilot", "all"]),
    default="all", help="安装哪种 hooks")
@click.pass_context
def hooks(ctx: click.Context, action: str, config_path: Optional[str], hook_type: str) -> None:
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

    # Create FeishuBot
    bot = None
    if feishu_config.get("app_id") and feishu_config.get("app_secret"):
        from cmd_monitor.feishu_client import FeishuBot

        bot = FeishuBot(
            app_id=feishu_config["app_id"],
            app_secret=feishu_config["app_secret"],
            receiver_id=feishu_config.get("receiver_id", ""),
        )
        bot.start()

    # Create StateManager
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
        debounce_seconds=state_manager._debounce_seconds,
        notification_cooldown=state_manager._notification_cooldown,
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
