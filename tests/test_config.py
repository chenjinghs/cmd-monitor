"""配置加载测试"""

import pytest

from cmd_monitor.config import load_config


def test_load_default_config() -> None:
    config = load_config()
    assert "general" in config
    assert "feishu" in config
    assert "powershell" in config
    assert "hooks" in config
    assert "inject" in config


def test_load_config_file_not_found() -> None:
    """文件不存在时返回空字典（允许 status/stop 在任意目录运行）"""
    result = load_config("/nonexistent/path.toml")
    assert result == {}


def test_load_config_structure() -> None:
    config = load_config()
    assert config["general"]["log_level"] == "INFO"
    assert isinstance(config["feishu"]["app_id"], str)
    assert config["powershell"]["poll_interval"] == 5
    assert config["hooks"]["enabled"] is True
    assert config["inject"]["inject_delay"] == 0.5
