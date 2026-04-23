"""飞书客户端测试"""

from unittest.mock import MagicMock, patch

import pytest

from cmd_monitor.feishu_client import FeishuBot, FeishuBotConfig, FeishuMessage, _extract_post_text


def test_feishu_message_dataclass() -> None:
    msg = FeishuMessage(
        message_id="msg_123",
        sender_id="ou_abc",
        chat_id="oc_xyz",
        chat_type="p2p",
        content="hello",
        msg_type="text",
    )
    assert msg.content == "hello"
    assert msg.sender_id == "ou_abc"
    assert msg.message_id == "msg_123"


def test_feishu_bot_config_defaults() -> None:
    config = FeishuBotConfig()
    assert config.app_id == ""
    assert config.app_secret == ""
    assert config.receiver_id == ""


def test_feishu_bot_config_with_values() -> None:
    config = FeishuBotConfig(app_id="id", app_secret="secret", receiver_id="ou_abc")
    assert config.app_id == "id"
    assert config.app_secret == "secret"


def test_feishu_bot_init() -> None:
    bot = FeishuBot(app_id="test_id", app_secret="test_secret", receiver_id="ou_abc")
    assert bot._app_id == "test_id"
    assert bot._app_secret == "test_secret"
    assert bot._receiver_id == "ou_abc"
    assert bot._running is False
    assert bot._client is None
    assert bot._ws_client is None


def test_feishu_bot_start_no_credentials() -> None:
    bot = FeishuBot(app_id="", app_secret="", receiver_id="")
    assert bot.start() is False


def test_feishu_bot_send_text_no_client() -> None:
    bot = FeishuBot(app_id="test", app_secret="test", receiver_id="ou_abc")
    assert bot.send_text("hello") is False


def test_feishu_bot_send_card_no_client() -> None:
    bot = FeishuBot(app_id="test", app_secret="test", receiver_id="ou_abc")
    assert bot.send_card("title", "content") is False


def test_feishu_bot_stop() -> None:
    bot = FeishuBot(app_id="test", app_secret="test", receiver_id="ou_abc")
    bot._running = True
    bot.stop()
    assert bot._running is False


def test_feishu_bot_set_message_callback() -> None:
    bot = FeishuBot(app_id="test", app_secret="test", receiver_id="ou_abc")
    callback = MagicMock()
    bot.set_message_callback(callback)
    assert bot._message_callback is callback


def test_parse_text_message() -> None:
    message = MagicMock()
    message.content = '{"text": "hello world"}'
    message.message_type = "text"
    result = FeishuBot._parse_message_content(message)
    assert result == "hello world"


def test_parse_text_message_with_whitespace() -> None:
    message = MagicMock()
    message.content = '{"text": "  hello  "}'
    message.message_type = "text"
    result = FeishuBot._parse_message_content(message)
    assert result == "hello"


def test_parse_empty_content() -> None:
    message = MagicMock()
    message.content = None
    message.message_type = "text"
    result = FeishuBot._parse_message_content(message)
    assert result is None


def test_parse_invalid_json() -> None:
    message = MagicMock()
    message.content = "not json"
    message.message_type = "text"
    result = FeishuBot._parse_message_content(message)
    assert result is None


def test_parse_unsupported_type() -> None:
    message = MagicMock()
    message.content = '{"image_key": "img_123"}'
    message.message_type = "image"
    result = FeishuBot._parse_message_content(message)
    assert result is None


def test_extract_post_text_direct_format() -> None:
    content = {
        "title": "Test Title",
        "content": [[{"tag": "text", "text": "Hello "}, {"tag": "text", "text": "World"}]],
    }
    result = _extract_post_text(content)
    assert "Test Title" in result
    assert "Hello" in result
    assert "World" in result


def test_extract_post_text_localized_format() -> None:
    content = {
        "zh_cn": {
            "title": "中文标题",
            "content": [[{"tag": "text", "text": "内容"}]],
        }
    }
    result = _extract_post_text(content)
    assert "中文标题" in result
    assert "内容" in result


def test_extract_post_text_wrapped_format() -> None:
    content = {
        "post": {
            "zh_cn": {
                "title": "Wrapped",
                "content": [[{"tag": "text", "text": "Content"}]],
            }
        }
    }
    result = _extract_post_text(content)
    assert "Wrapped" in result
    assert "Content" in result


def test_extract_post_text_empty() -> None:
    result = _extract_post_text({})
    assert result == ""


def test_extract_post_text_with_link() -> None:
    content = {
        "zh_cn": {
            "content": [[{"tag": "a", "text": "Click here", "href": "https://example.com"}]],
        }
    }
    result = _extract_post_text(content)
    assert "Click here" in result
