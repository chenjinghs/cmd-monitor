"""AutoReplier 测试"""

import threading
import time

from cmd_monitor.auto_replier import AutoReplier


# --- 基本属性测试 ---


def test_auto_replier_initial_state() -> None:
    ar = AutoReplier(timeout_seconds=30.0, default_answer="yes")
    assert ar.timeout_seconds == 30.0
    assert ar.default_answer == "yes"
    assert ar.is_armed is False


def test_auto_replier_arm() -> None:
    ar = AutoReplier(timeout_seconds=30.0, default_answer="yes")
    ar.arm()
    assert ar.is_armed is True


def test_auto_replier_wait_without_arm_returns_default() -> None:
    ar = AutoReplier(timeout_seconds=30.0, default_answer="default")
    result = ar.wait()
    assert result == "default"
    assert ar.is_armed is False


# --- 超时测试 ---


def test_auto_replier_timeout_returns_default() -> None:
    ar = AutoReplier(timeout_seconds=0.1, default_answer="preset")
    ar.arm()
    result = ar.wait()
    assert result == "preset"
    assert ar.is_armed is False


def test_auto_replier_timeout_disarms() -> None:
    ar = AutoReplier(timeout_seconds=0.1, default_answer="preset")
    ar.arm()
    assert ar.is_armed is True
    ar.wait()
    assert ar.is_armed is False


# --- 用户回复测试 ---


def test_auto_replier_user_reply_wins() -> None:
    ar = AutoReplier(timeout_seconds=5.0, default_answer="default")
    ar.arm()

    # 在另一个线程中发送回复
    def send_reply() -> None:
        time.sleep(0.05)
        ar.on_message("user reply")

    t = threading.Thread(target=send_reply)
    t.start()
    result = ar.wait()
    t.join()

    assert result == "user reply"
    assert ar.is_armed is False


def test_auto_replier_user_reply_faster_than_timeout() -> None:
    ar = AutoReplier(timeout_seconds=10.0, default_answer="default")
    ar.arm()

    start = time.monotonic()
    threading.Timer(0.05, lambda: ar.on_message("fast reply")).start()
    result = ar.wait()
    elapsed = time.monotonic() - start

    assert result == "fast reply"
    assert elapsed < 1.0  # 应该在 1 秒内返回，不用等 10 秒


# --- on_message 忽略未 armed 状态 ---


def test_on_message_ignored_when_not_armed() -> None:
    ar = AutoReplier(timeout_seconds=0.1, default_answer="default")
    ar.on_message("should be ignored")
    ar.arm()
    result = ar.wait()
    # 消息在 arm() 之前发送，应该超时后返回默认值
    assert result == "default"


# --- 重复使用测试 ---


def test_auto_replier_reusable() -> None:
    ar = AutoReplier(timeout_seconds=0.1, default_answer="default")

    # 第一次：超时
    ar.arm()
    result1 = ar.wait()
    assert result1 == "default"

    # 第二次：用户回复
    ar.arm()
    threading.Timer(0.02, lambda: ar.on_message("second reply")).start()
    result2 = ar.wait()
    assert result2 == "second reply"

    # 第三次：超时
    ar.arm()
    result3 = ar.wait()
    assert result3 == "default"


def test_auto_replier_arm_resets_previous_reply() -> None:
    """重新 arm 时清除上次的回复"""
    ar = AutoReplier(timeout_seconds=5.0, default_answer="default")

    ar.arm()
    ar.on_message("first reply")
    ar.wait()

    # 重新 arm 后，旧回复不应影响新的等待
    ar.arm()
    # 等待超时（不发送新回复）
    ar2 = AutoReplier(timeout_seconds=0.05, default_answer="default")
    ar2.arm()
    result = ar2.wait()
    assert result == "default"


# --- 并发安全测试 ---


def test_auto_replier_concurrent_messages() -> None:
    """多个并发消息，只有第一个生效"""
    ar = AutoReplier(timeout_seconds=5.0, default_answer="default")
    ar.arm()

    def send_after(delay: float, text: str) -> None:
        time.sleep(delay)
        ar.on_message(text)

    threads = [
        threading.Thread(target=send_after, args=(0.02, "first")),
        threading.Thread(target=send_after, args=(0.05, "second")),
        threading.Thread(target=send_after, args=(0.08, "third")),
    ]
    for t in threads:
        t.start()

    result = ar.wait()
    for t in threads:
        t.join()

    # 第一个消息应该胜出
    assert result == "first"
