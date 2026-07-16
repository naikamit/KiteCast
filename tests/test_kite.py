import hashlib

from kitecast.kite import KiteClient


def test_postback_checksum_accepts_genuine():
    kite = KiteClient("key", "secret")
    payload = {"order_id": "123", "order_timestamp": "2026-07-10 11:00:00"}
    payload["checksum"] = hashlib.sha256(b"1232026-07-10 11:00:00secret").hexdigest()
    assert kite.verify_postback(payload) is True


def test_postback_checksum_rejects_forged():
    kite = KiteClient("key", "secret")
    payload = {
        "order_id": "123",
        "order_timestamp": "2026-07-10 11:00:00",
        "checksum": "deadbeef",
    }
    assert kite.verify_postback(payload) is False
    assert kite.verify_postback({}) is False


def test_token_exception_clears_access_token(monkeypatch):
    """A dead daily token must flip the client back to logged-out, so the
    console shows the login banner instead of silent dashes."""
    import pytest

    from kitecast.kite import KiteError

    class R:
        status_code = 403

        def json(self):
            return {"status": "error", "message": "Incorrect `api_key` or `access_token`.",
                    "error_type": "TokenException"}

    monkeypatch.setattr("kitecast.kite.httpx.get", lambda *a, **k: R())
    kite = KiteClient("key", "secret", access_token="stale-token")
    with pytest.raises(KiteError, match="TokenException"):
        kite.quote("MCX:X")
    assert kite.access_token is None


def test_best_price_fallback_chain():
    from kitecast.kite import best_price

    q = {"last_price": 100.0,
         "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
         "ohlc": {"close": 95.0}}
    assert best_price(q, "BUY") == 100.0            # LTP wins when present

    q["last_price"] = 0                              # illiquid: no trades yet
    assert best_price(q, "BUY") == 101.0             # buy at the ask
    assert best_price(q, "SELL") == 99.0             # sell at the bid

    q["depth"] = {"buy": [], "sell": []}
    assert best_price(q, "BUY") == 95.0              # previous close

    assert best_price({}, "BUY") is None


def test_login_url():
    kite = KiteClient("key", "secret")
    assert kite.login_url() == "https://kite.zerodha.com/connect/login?v=3&api_key=key"


def test_orders_go_through_static_ip_proxy(monkeypatch):
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"], calls["proxy"] = url, kwargs.get("proxy")

        class R:
            status_code = 200

            def json(self):
                return {"status": "success", "data": {"order_id": "1"}}

        return R()

    monkeypatch.setattr("kitecast.kite.httpx.post", fake_post)
    kite = KiteClient("key", "secret", access_token="tok",
                      order_proxy="http://u:p@static-ip-proxy:8080")
    kite.place_order(tradingsymbol="X", exchange="MCX", transaction_type="BUY",
                     quantity=1, product="NRML", order_type="MARKET")
    assert calls["url"].endswith("/orders/regular")
    assert calls["proxy"] == "http://u:p@static-ip-proxy:8080"

    # Empty env value means no proxy, not proxy-of-empty-string.
    assert KiteClient("key", "secret", order_proxy="").order_proxy is None


def test_quotes_go_through_static_ip_proxy_too(monkeypatch):
    """Zerodha 403s /quote from non-whitelisted IPs, so quotes share the proxy."""
    calls = {}

    def fake_get(url, **kwargs):
        calls["url"], calls["proxy"] = url, kwargs.get("proxy")

        class R:
            status_code = 200

            def json(self):
                return {"status": "success", "data": {"MCX:X": {"last_price": 1.0}}}

        return R()

    monkeypatch.setattr("kitecast.kite.httpx.get", fake_get)
    kite = KiteClient("key", "secret", access_token="tok",
                      order_proxy="http://u:p@static-ip-proxy:8080")
    assert kite.quote("MCX:X")["MCX:X"]["last_price"] == 1.0
    assert calls["url"].endswith("/quote")
    assert calls["proxy"] == "http://u:p@static-ip-proxy:8080"
