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
