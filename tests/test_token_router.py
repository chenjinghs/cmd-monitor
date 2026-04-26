"""TokenRouter 单元测试"""

from cmd_monitor.token_router import TokenRouter


def test_get_or_create_token_returns_stable_token() -> None:
    r = TokenRouter()
    t1 = r.get_or_create_token("session-A")
    t2 = r.get_or_create_token("session-A")
    assert t1 == t2
    assert len(t1) == 4
    assert all(c in "0123456789abcdef" for c in t1)


def test_get_or_create_token_unique_for_different_sessions() -> None:
    r = TokenRouter()
    t1 = r.get_or_create_token("session-A")
    t2 = r.get_or_create_token("session-B")
    assert t1 != t2


def test_route_with_token_prefix() -> None:
    r = TokenRouter()
    token = r.get_or_create_token("sess-1")
    result = r.route(f"{token} hello world")
    assert result.session_id == "sess-1"
    assert result.content == "hello world"
    assert result.matched_token is True


def test_route_token_case_insensitive() -> None:
    r = TokenRouter()
    token = r.get_or_create_token("sess-1")
    result = r.route(f"{token.upper()}  hi")
    assert result.session_id == "sess-1"
    assert result.matched_token is True


def test_route_token_with_chinese_colon() -> None:
    r = TokenRouter()
    token = r.get_or_create_token("sess-1")
    result = r.route(f"{token}：你好")
    assert result.session_id == "sess-1"
    assert result.content == "你好"


def test_route_no_token_falls_back_to_last_active() -> None:
    r = TokenRouter(fallback_to_last_active=True)
    r.get_or_create_token("sess-1")
    r.mark_active("sess-1")
    result = r.route("just text without token prefix at all")
    assert result.session_id == "sess-1"
    assert result.matched_token is False


def test_route_no_token_no_fallback() -> None:
    r = TokenRouter(fallback_to_last_active=False)
    r.get_or_create_token("sess-1")
    result = r.route("plain text")
    assert result.session_id is None


def test_remove_clears_mappings() -> None:
    r = TokenRouter()
    token = r.get_or_create_token("sess-1")
    r.remove("sess-1")
    assert r.lookup(token) is None


def test_lookup_unknown_token() -> None:
    r = TokenRouter()
    assert r.lookup("ffff") is None
