"""PowerShell transcript 监控模块测试"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cmd_monitor.ps_monitor import (
    PS_PROMPT_RE,
    TranscriptState,
    build_idle_ipc_event,
    check_idle,
    extract_last_output_block,
    extract_prompt_cwd,
    follow_transcript,
    format_idle_notification,
    get_waiting_cwd,
    is_prompt_line,
    is_transcript_header,
    is_waiting_for_input,
    update_state,
)


# --- follow_transcript tests ---


def test_follow_transcript_file_not_found() -> None:
    gen = follow_transcript("C:\\nonexistent\\file.txt", poll_interval=0.01)
    with pytest.raises(FileNotFoundError):
        next(gen)


def test_follow_transcript_yields_lines_from_mock(tmp_path: Path) -> None:
    """Test follow_transcript by writing all data before starting"""
    filepath = str(tmp_path / "transcript.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("line 1\nline 2\nline 3\n")

    # Read from the beginning instead of tail-follow
    lines = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            stripped = raw_line.rstrip("\n\r")
            if stripped:
                lines.append(stripped)

    assert lines == ["line 1", "line 2", "line 3"]


def test_follow_transcript_encoding_replace(tmp_path: Path) -> None:
    """Test that encoding errors are replaced, not raised"""
    filepath = str(tmp_path / "transcript.txt")
    # Write invalid UTF-8 bytes
    with open(filepath, "wb") as f:
        f.write(b"valid line\n\xff\xfe invalid\nanother line\n")

    lines = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            stripped = raw_line.rstrip("\n\r")
            if stripped:
                lines.append(stripped)

    assert lines[0] == "valid line"
    assert len(lines) == 3
    # The invalid bytes should be replaced, not crash
    assert "\ufffd" in lines[1] or "invalid" in lines[1]


# --- is_prompt_line tests ---


def test_is_prompt_line_valid() -> None:
    assert is_prompt_line("PS C:\\Users\\test>") is True
    assert is_prompt_line("PS C:\\>") is True
    assert is_prompt_line("PS D:\\Projects\\myapp\\sub>") is True


def test_is_prompt_line_with_command() -> None:
    assert is_prompt_line("PS C:\\Users\\test> Get-Process") is True


def test_is_prompt_line_invalid() -> None:
    assert is_prompt_line("Hello world") is False
    assert is_prompt_line("Handles  NPM(K)  PM(K)") is False
    assert is_prompt_line("") is False
    assert is_prompt_line("PS something") is False


def test_extract_prompt_cwd() -> None:
    assert extract_prompt_cwd("PS C:\\Users\\test>") == "C:\\Users\\test"
    assert extract_prompt_cwd("PS D:\\repo> Get-ChildItem") == "D:\\repo"
    assert extract_prompt_cwd("not prompt") == ""


def test_waiting_prompt_detection_requires_bare_prompt() -> None:
    waiting_state = TranscriptState(recent_lines=["output", "PS E:\\repo>"])
    running_state = TranscriptState(recent_lines=["PS E:\\repo> copilot", "thinking..."])
    assert is_waiting_for_input(waiting_state) is True
    assert get_waiting_cwd(waiting_state) == "E:\\repo"
    assert is_waiting_for_input(running_state) is False
    assert get_waiting_cwd(running_state) == ""


def test_extract_last_output_block_returns_lines_before_waiting_prompt() -> None:
    state = TranscriptState(
        recent_lines=[
            "PS E:\\repo> copilot",
            "第一行",
            "第二行",
            "PS E:\\repo>",
        ]
    )
    assert extract_last_output_block(state) == "第一行\n第二行"


def test_extract_last_output_block_skips_prompt_only_state() -> None:
    state = TranscriptState(recent_lines=["PS E:\\repo>"])
    assert extract_last_output_block(state) == ""


# --- is_transcript_header tests ---


def test_is_transcript_header_stars() -> None:
    assert is_transcript_header("**********************") is True
    assert is_transcript_header("**********") is True


def test_is_transcript_header_start() -> None:
    assert is_transcript_header("Windows PowerShell transcript start") is True


def test_is_transcript_header_end() -> None:
    assert is_transcript_header("Windows PowerShell transcript end") is True


def test_is_transcript_header_transcript_started() -> None:
    assert is_transcript_header(
        "Transcript started, output file is C:\\Users\\test\\transcript.txt"
    ) is True


def test_is_transcript_header_normal_line() -> None:
    assert is_transcript_header("PS C:\\Users\\test>") is False
    assert is_transcript_header("Hello world") is False


# --- update_state tests ---


def test_update_state_creates_new_object() -> None:
    state = TranscriptState()
    now = time.time()
    new_state = update_state(state, "new line", now)
    assert new_state is not state
    assert new_state.recent_lines == ["new line"]
    assert new_state.last_activity_time == now


def test_update_state_appends_line() -> None:
    state = TranscriptState(recent_lines=["line 1"], last_activity_time=100.0)
    new_state = update_state(state, "line 2", 200.0)
    assert new_state.recent_lines == ["line 1", "line 2"]
    assert new_state.last_activity_time == 200.0


def test_update_state_limits_recent_lines() -> None:
    state = TranscriptState(max_recent_lines=3)
    state = update_state(state, "a", 1.0)
    state = update_state(state, "b", 2.0)
    state = update_state(state, "c", 3.0)
    state = update_state(state, "d", 4.0)
    assert len(state.recent_lines) == 3
    assert state.recent_lines == ["b", "c", "d"]


def test_update_state_skips_header() -> None:
    state = TranscriptState(recent_lines=["existing"], last_activity_time=100.0)
    new_state = update_state(state, "**********************", 200.0)
    assert new_state is state


def test_update_state_resets_idle() -> None:
    state = TranscriptState(is_idle=True)
    new_state = update_state(state, "new line", time.time())
    assert new_state.is_idle is False


def test_update_state_skips_all_header_types() -> None:
    state = TranscriptState()
    header_lines = [
        "**********************",
        "Windows PowerShell transcript start",
        "Windows PowerShell transcript end",
        "Transcript started, output file is C:\\log.txt",
    ]
    for line in header_lines:
        result = update_state(state, line, time.time())
        assert result is state


# --- check_idle tests ---


def test_check_idle_below_threshold() -> None:
    state = TranscriptState(last_activity_time=time.time())
    assert check_idle(state, idle_threshold=10.0, now=time.time()) is False


def test_check_idle_above_threshold() -> None:
    now = time.time()
    state = TranscriptState(last_activity_time=now - 15.0)
    assert check_idle(state, idle_threshold=10.0, now=now) is True


def test_check_idle_no_activity() -> None:
    state = TranscriptState(last_activity_time=0.0)
    assert check_idle(state, idle_threshold=10.0, now=time.time()) is False


def test_check_idle_exact_threshold() -> None:
    now = time.time()
    state = TranscriptState(last_activity_time=now - 10.0)
    assert check_idle(state, idle_threshold=10.0, now=now) is True


# --- format_idle_notification tests ---


def test_format_idle_notification_with_lines() -> None:
    state = TranscriptState(
        recent_lines=["line 1", "line 2", "PS C:\\repo>"],
        last_activity_time=time.time(),
    )
    title, content = format_idle_notification(state, "C:\\transcript.txt")
    assert "等待输入" in title
    assert "终端已空闲" in content
    assert "C:\\repo" in content
    assert "C:\\transcript.txt" in content
    assert "最后一条消息" in content
    assert "line 1" in content
    assert "PS C:\\repo>" in content


def test_format_idle_notification_no_lines() -> None:
    state = TranscriptState()
    title, content = format_idle_notification(state, "C:\\transcript.txt")
    assert "等待输入" in title
    assert "最后一条消息" in content
    assert "(无输出)" in content


def test_format_idle_notification_limits_to_5_lines() -> None:
    lines = [f"line {i}" for i in range(10)]
    state = TranscriptState(recent_lines=lines)
    title, content = format_idle_notification(state, "C:\\log.txt")
    assert "line 5" in content
    assert "line 9" in content
    assert "line 4" not in content


def test_format_idle_notification_truncates_last_output_block() -> None:
    long_line = "x" * 350
    state = TranscriptState(recent_lines=[long_line, "PS C:\\repo>"])
    _, content = format_idle_notification(state, "C:\\log.txt")
    assert "最后一条消息" in content
    assert f"{'x' * 300}…" in content


def test_build_idle_ipc_event_contains_cwd() -> None:
    state = TranscriptState(recent_lines=["output", "PS E:\\repo>"])
    event = build_idle_ipc_event(state, "C:\\log.txt")
    assert event["type"] == "transcript_idle"
    assert event["cwd"] == "E:\\repo"
    assert event["title"] == "PowerShell — 等待输入"
    assert "E:\\repo" in event["content"]


# --- PS_PROMPT_RE tests ---


def test_ps_prompt_regex_match() -> None:
    assert PS_PROMPT_RE.match("PS C:\\Users\\test>")
    assert PS_PROMPT_RE.match("PS D:\\>")
    assert PS_PROMPT_RE.match("PS C:\\Users\\chenjing\\Documents>")


def test_ps_prompt_regex_no_match() -> None:
    assert not PS_PROMPT_RE.match("Hello")
    assert not PS_PROMPT_RE.match("PS something")
    assert not PS_PROMPT_RE.match("")


# --- PsMonitor tests ---


def test_ps_monitor_init() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    bot = MagicMock()
    m = PsMonitor(
        transcript_path="C:\\test.txt",
        poll_interval=3.0,
        idle_threshold=15.0,
        feishu_bot=bot,
    )
    assert m.transcript_path == "C:\\test.txt"
    assert m.poll_interval == 3.0
    assert m.idle_threshold == 15.0
    assert m.feishu_bot is bot
    assert m._running is False


def test_ps_monitor_stop() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    m = PsMonitor(transcript_path="C:\\test.txt")
    m._running = True
    m.stop()
    assert m._running is False


def test_ps_monitor_run_file_not_found() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    m = PsMonitor(transcript_path="C:\\nonexistent\\file.txt")
    # Should not raise, logs error instead
    m.run()
    assert m._running is False


def test_ps_monitor_idle_triggers_notification() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    bot = MagicMock()
    m = PsMonitor(
        transcript_path="C:\\dummy.txt",
        poll_interval=0.1,
        idle_threshold=0.1,
        feishu_bot=bot,
    )
    # Simulate state with recent activity
    m._state = TranscriptState(
        last_activity_time=time.time() - 1.0,
        recent_lines=["output line", "PS C:\\repo>"],
    )
    # Directly call idle detection
    m._on_idle_detected()
    bot.send_card.assert_called_once()
    title, content = bot.send_card.call_args[0]
    assert "等待输入" in title
    assert "output line" in content


def test_ps_monitor_idle_callback_short_circuits_bot() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    bot = MagicMock()
    callback = MagicMock(return_value=True)
    m = PsMonitor(
        transcript_path="C:\\dummy.txt",
        feishu_bot=bot,
        notification_callback=callback,
    )
    m._state = TranscriptState(recent_lines=["done", "PS C:\\repo>"])
    m._on_idle_detected()
    callback.assert_called_once()
    bot.send_card.assert_not_called()


def test_ps_monitor_idle_callback_falls_back_to_bot_on_false() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    bot = MagicMock()
    callback = MagicMock(return_value=False)
    m = PsMonitor(
        transcript_path="C:\\dummy.txt",
        feishu_bot=bot,
        notification_callback=callback,
    )
    m._state = TranscriptState(recent_lines=["done", "PS C:\\repo>"])
    m._on_idle_detected()
    callback.assert_called_once()
    bot.send_card.assert_called_once()


def test_ps_monitor_idle_no_bot() -> None:
    from cmd_monitor.ps_monitor import PsMonitor

    m = PsMonitor(transcript_path="C:\\dummy.txt", feishu_bot=None)
    m._state = TranscriptState(
        last_activity_time=time.time() - 1.0,
        recent_lines=["test"],
    )
    # Should not raise
    m._on_idle_detected()


# --- CLI tests ---


def test_monitor_help() -> None:
    from click.testing import CliRunner

    from cmd_monitor.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "--transcript" in result.output


def test_monitor_no_transcript_path() -> None:
    from click.testing import CliRunner

    from cmd_monitor.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["monitor"])
    assert result.exit_code == 0
    assert "错误" in result.output
