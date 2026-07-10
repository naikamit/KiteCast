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
