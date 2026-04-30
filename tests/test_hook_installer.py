"""Hook installer 测试"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cmd_monitor.hook_installer import (
    CLAUDE_HOOK_EVENTS,
    claude_hooks_are_configured,
    generate_hook_command,
    generate_hooks_config,
    install_hooks,
)


# --- generate_hook_command tests ---


def test_generate_hook_command_notification() -> None:
    cmd = generate_hook_command("Notification")
    assert "powershell.exe" in cmd
    assert "hook-handler" in cmd
    assert "--event Notification" in cmd


def test_generate_hook_command_stop() -> None:
    cmd = generate_hook_command("Stop")
    assert "--event Stop" in cmd


def test_generate_hook_command_pre_tool_use() -> None:
    cmd = generate_hook_command("PreToolUse")
    assert "--event PreToolUse" in cmd


def test_generate_hook_command_custom_bin() -> None:
    cmd = generate_hook_command("Notification", monitor_bin="my-monitor")
    assert "my-monitor" in cmd


# --- generate_hooks_config tests ---


def test_generate_hooks_config_all_events() -> None:
    config = generate_hooks_config(CLAUDE_HOOK_EVENTS)
    assert "hooks" in config
    hooks = config["hooks"]
    assert "Notification" in hooks
    assert "Stop" in hooks
    assert "PreToolUse" in hooks


def test_generate_hooks_config_single_event() -> None:
    config = generate_hooks_config(["Notification"])
    hooks = config["hooks"]
    assert "Notification" in hooks
    assert "Stop" not in hooks


def test_generate_hooks_config_structure() -> None:
    config = generate_hooks_config(["Notification"])
    hook_entry = config["hooks"]["Notification"][0]
    assert hook_entry["matcher"] == "*"
    assert hook_entry["hooks"][0]["type"] == "command"
    assert "powershell.exe" in hook_entry["hooks"][0]["command"]


def test_generate_hooks_config_custom_bin() -> None:
    config = generate_hooks_config(["Notification"], monitor_bin="custom-bin")
    cmd = config["hooks"]["Notification"][0]["hooks"][0]["command"]
    assert "custom-bin" in cmd


# --- install_hooks tests ---


def test_install_hooks_creates_file(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "settings.json"
    result = install_hooks(config_path=str(target))
    assert result is True
    assert target.exists()

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "hooks" in data
    assert "Notification" in data["hooks"]


def test_install_hooks_preserves_existing(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    existing = {
        "permissions": {"allow": ["Bash(ls)"]},
        "theme": "dark",
    }
    with open(target, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    result = install_hooks(config_path=str(target))
    assert result is True

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "permissions" in data
    assert data["permissions"]["allow"] == ["Bash(ls)"]
    assert data["theme"] == "dark"
    assert "hooks" in data


def test_install_hooks_overwrites_old_hooks(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    existing = {
        "hooks": {
            "Notification": [{"type": "command", "command": "old-command"}],
        },
    }
    with open(target, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    result = install_hooks(config_path=str(target))
    assert result is True

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "powershell.exe" in data["hooks"]["Notification"][0]["hooks"][0]["command"]


def test_install_hooks_custom_events(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    result = install_hooks(config_path=str(target), events=["Notification"])
    assert result is True

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "Notification" in data["hooks"]
    assert "Stop" not in data["hooks"]


def test_install_hooks_invalid_existing(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    with open(target, "w", encoding="utf-8") as f:
        f.write("not valid json{")

    result = install_hooks(config_path=str(target))
    assert result is True

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "hooks" in data


def test_install_hooks_write_failure(tmp_path: Path) -> None:
    target = tmp_path / "nonexistent_dir" / "settings.json"
    # Don't create parent — but install_hooks creates it with mkdir
    # Let's mock to force OSError
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        result = install_hooks(config_path=str(target))
        assert result is False


def test_install_hooks_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / ".claude" / "settings.json"
    result = install_hooks(config_path=str(target))
    assert result is True
    assert target.exists()


# --- claude hook verification tests ---


def test_claude_hooks_are_configured_returns_true_for_complete_settings(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(generate_hooks_config(CLAUDE_HOOK_EVENTS)), encoding="utf-8")

    assert claude_hooks_are_configured(str(target)) is True


def test_claude_hooks_are_configured_returns_false_when_event_missing(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(generate_hooks_config(["Notification", "Stop"])), encoding="utf-8")

    assert claude_hooks_are_configured(str(target)) is False


def test_claude_hooks_are_configured_returns_false_when_command_mismatch(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True)
    config = generate_hooks_config(CLAUDE_HOOK_EVENTS)
    config["hooks"]["Notification"][0]["hooks"][0]["command"] = "wrong-command"
    target.write_text(json.dumps(config), encoding="utf-8")

    assert claude_hooks_are_configured(str(target)) is False
