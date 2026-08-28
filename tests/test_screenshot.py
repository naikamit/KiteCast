"""The /screenshot login-free path: image → parsed order → share links."""

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings

PARSED_CRUDE_CE = {
    "underlying": "CRUDEOIL", "expiry_day": 15, "expiry_month": 7, "expiry_year": None,
    "strike": 5500, "instrument_type": "CE", "side": "BUY", "qty_lots": 2,
    "limit_price": 488.4, "ltp": 480.0,
}


class FakeVision:
    def __init__(self, result):
        self.configured = True
        self.result = result
        self.calls = 0

    def extract(self, image_bytes, media_type):
        self.calls += 1
        return dict(self.result)


@pytest.fixture
def shot(ledger, kite, telegram, settings, monkeypatch):
    from tests.test_instruments import _pin_today

    _pin_today(monkeypatch)
    monkeypatch.setattr(app_settings, "kite_api_key", settings.kite_api_key)
    monkeypatch.setattr(app_settings, "kite_api_secret", settings.kite_api_secret)
    monkeypatch.setattr(app_settings, "base_url", settings.base_url)
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
    monkeypatch.setattr(app_settings, "share_timing_entry", "fill")
    monkeypatch.setattr(app_settings, "share_timing_exit", "fill")
    fake = FakeVision(PARSED_CRUDE_CE)
    client = TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram, vision=fake))
    return client, fake


def _upload(client):
    return client.post("/api/screenshot_parse",
                       files={"file": ("shot.png", b"\x89PNG fake", "image/png")})


def test_parse_resolves_exact_contract(shot):
    client, fake = shot
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["tradingsymbol"] == "CRUDEOIL26JUL5500CE"
    assert body["exchange"] == "MCX"
    assert body["side"] == "BUY"
    assert body["qty"] == 2                # MCX: lots stay lots
    assert body["price"] == 488.4          # limit price wins over LTP
    assert fake.calls == 1


def test_full_login_free_flow(shot, ledger, kite):
    """The whole promise: parse, mint, and serve the friend's basket with a
    dead Kite session — screenshot price is the anchor, LIMIT needs no quote."""
    client, fake = shot
    client.get("/api/underlyings?q=x")      # warm the instrument dump (writes disk cache)
    kite.access_token = None                # daily login never happened

    r = _upload(client)
    assert r.status_code == 200

    r = client.post("/api/screenshot_share", data={
        "exchange": "MCX", "tradingsymbol": "CRUDEOIL26JUL5500CE",
        "side": "BUY", "qty": "2", "price": "488.4",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["entry_url"].startswith("https://vps.test/t/")

    trade = ledger.trade(body["trade_id"])
    assert trade["status"] == "SHARED"
    assert trade["order_type"] == "LIMIT" and trade["price"] == 488.4
    assert kite.orders == []                # nothing on my account, ever

    # Friend's tap renders the pre-filled LIMIT basket — no session needed.
    token = body["entry_url"].rsplit("/", 1)[-1]
    page = client.get(f"/t/{token}")
    assert page.status_code == 200
    assert "connect/basket" in page.text and "LIMIT" in page.text


def test_unmatched_underlying_is_rejected(shot):
    client, fake = shot
    fake.result = {**PARSED_CRUDE_CE, "underlying": "PLUTONIUM"}
    r = _upload(client)
    assert r.status_code == 422
    assert "PLUTONIUM" in r.json()["detail"]


def test_unconfigured_vision_gives_clear_error(shot):
    client, fake = shot
    fake.configured = False
    assert _upload(client).status_code == 503
    assert "ANTHROPIC_API_KEY" in client.get("/screenshot").text


def test_screenshot_page_renders(shot):
    client, _ = shot
    r = client.get("/screenshot")
    assert r.status_code == 200
    assert "Create share link" in r.text
