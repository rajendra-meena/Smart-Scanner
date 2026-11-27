
from types import SimpleNamespace

from fastapi import Response

import app.api.routes.auth as auth_module


def make_logout_request(cookie_name, session_key="test-session"):
    return SimpleNamespace(cookies={cookie_name: session_key})


def setup_logout_mocks(monkeypatch, strategy_error=None):
    calls = []

    current_session = SimpleNamespace(access_token="test-access-token")

    class FakeStrategy:
        def stop(self):
            calls.append("strategy.stop")
            if strategy_error:
                raise strategy_error

    class FakeMarketData:
        def stop(self, access_token):
            calls.append(("market_data.stop", access_token))

    class FakeKiteClient:
        def invalidate_access_token(self):
            calls.append("kite.invalidate")

    monkeypatch.setattr(
        auth_module.session_store,
        "delete",
        lambda key: current_session,
    )
    monkeypatch.setattr(
        auth_module,
        "opening_breakout_service",
        FakeStrategy(),
        raising=False,
    )
    monkeypatch.setattr(
        auth_module,
        "market_data_engine",
        FakeMarketData(),
    )
    monkeypatch.setattr(
        auth_module,
        "kite_client",
        lambda access_token: FakeKiteClient(),
    )

    return calls


def test_logout_stops_strategy_before_market_feed_and_kite(monkeypatch):
    cookie_name = auth_module.settings.session_cookie_name
    calls = setup_logout_mocks(monkeypatch)

    response = Response()
    result = auth_module.logout(
        make_logout_request(cookie_name),
        response,
    )

    assert result == {"authenticated": False}
    assert calls == [
        "strategy.stop",
        ("market_data.stop", "test-access-token"),
        "kite.invalidate",
    ]
    assert "set-cookie" in response.headers


def test_logout_continues_cleanup_if_strategy_stop_fails(monkeypatch):
    cookie_name = auth_module.settings.session_cookie_name
    calls = setup_logout_mocks(
        monkeypatch,
        strategy_error=RuntimeError("Strategy cleanup failed"),
    )

    response = Response()
    result = auth_module.logout(
        make_logout_request(cookie_name),
        response,
    )

    assert result == {"authenticated": False}
    assert calls == [
        "strategy.stop",
        ("market_data.stop", "test-access-token"),
        "kite.invalidate",
    ]
