"""HTTP-level tests: console, contract data API, postback, mirror, redirect."""

import pytest
from fastapi.testclient import TestClient

from kitecast.app import build_app
from kitecast.config import settings as app_settings
from tests.conftest import postback


@pytest.fixture
def client(ledger, kite, telegram, settings, monkeypatch):
    monkeypatch.setattr(app_settings, "kite_api_key", settings.kite_api_key)
    monkeypatch.setattr(app_settings, "kite_api_secret", settings.kite_api_secret)
    monkeypatch.setattr(app_settings, "base_url", settings.base_url)
    monkeypatch.setattr(app_settings, "share_timing_entry", "fill")
    monkeypatch.setattr(app_settings, "share_timing_exit", "fill")
    return TestClient(build_app(ledger=ledger, kite=kite, telegram=telegram))


def test_console_pages_open(client):
    assert client.get("/").status_code == 200
    assert client.get("/board").status_code == 200
    assert client.get("/friends").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}


def test_favicon_served(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(("image/png", "image/svg"))
    assert client.get("/apple-touch-icon.png").status_code == 200


def test_instrument_search_api(client):
    hits = client.get("/api/instruments?q=crudeoil fut").json()
    assert hits[0]["tradingsymbol"] == "CRUDEOIL25JULFUT"
    assert hits[0]["exchange"] == "MCX"


def test_instrument_search_needs_login(client, kite):
    kite.access_token = None
    r = client.get("/api/instruments?q=crudeoil")
    assert r.status_code == 409
    assert "login" in r.json()["detail"].lower()


def test_contract_info_api(client):
    r = client.get("/api/contract?exchange=MCX&tradingsymbol=CRUDEOIL25JULFUT")
    assert r.status_code == 200
    c = r.json()
    assert c["lot_size"] == 100
    assert c["expiry"] == "2026-07-17"
    assert c["quote"]["last_price"] == 6250.0
    assert c["quote"]["oi"] == 6789
    assert c["quote"]["best_bid"]["price"] == 6249.9
    assert c["quote"]["upper_circuit_limit"] == 6800.0

    assert client.get("/api/contract?exchange=MCX&tradingsymbol=NOPE").status_code == 404


def test_full_flow_over_http(client, ledger, kite, telegram, friends):
    # Place & Share from the console.
    r = client.post("/trade", data={
        "tradingsymbol": "crudeoil25julfut", "exchange": "MCX", "side": "BUY",
        "qty": "100", "product": "NRML", "order_type": "MARKET",
    }, follow_redirects=False)
    assert r.status_code == 303
    trade = ledger.trades()[0]
    assert trade["tradingsymbol"] == "CRUDEOIL25JULFUT"

    # My fill arrives via the checksum-verified postback.
    r = client.post("/kite/postback", json=postback(kite, trade["entry_order_id"], filled_qty=100))
    assert r.status_code == 200
    assert ledger.trade(trade["id"])["status"] == "FILLED"
    assert len(telegram.sent) == 3

    # Friend opens the mirror link: pre-filled basket form.
    share = ledger.shares_for_trade(trade["id"], "ENTRY")[0]
    r = client.get(f"/m/{share['token']}")
    assert r.status_code == 200
    assert "kite.zerodha.com/connect/basket" in r.text
    assert "CRUDEOIL25JULFUT" in r.text

    # Publisher redirect flips them green.
    r = client.get(f"/kite/redirect?status=success&share_token={share['token']}")
    assert r.status_code == 200
    assert ledger.share(share["id"])["status"] == "CONFIRMED"

    # Board shows the confirmation, and pending cells carry a copyable
    # mirror URL plus a WhatsApp share link.
    board = client.get("/board").text
    assert "✓" in board
    pending = ledger.shares_for_trade(trade["id"], "ENTRY")[1]
    assert f"https://vps.test/m/{pending['token']}" in board
    assert "wa.me/?text=" in board

    # Close & Share, then exit fill fans the close mirrors.
    telegram.sent.clear()
    r = client.post(f"/trade/{trade['id']}/close", follow_redirects=False)
    assert r.status_code == 303
    exit_order_id = ledger.trade(trade["id"])["exit_order_id"]
    client.post("/kite/postback", json=postback(kite, exit_order_id, avg_price=7000.0))
    assert ledger.trade(trade["id"])["status"] == "CLOSED"
    assert len(telegram.sent) == 3


def test_share_only_flow_over_http(client, ledger, kite, telegram, friends):
    r = client.post("/trade", data={
        "tradingsymbol": "CRUDEOIL25JULFUT", "exchange": "MCX", "side": "BUY",
        "qty": "100", "product": "NRML", "order_type": "MARKET", "share_only": "on",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert kite.orders == []
    trade = ledger.trades()[0]
    assert trade["status"] == "SHARED"
    assert len(telegram.sent) == 3

    # The console shows copyable public entry/close links for the trade.
    page = client.get("/").text
    assert f"https://vps.test/t/{trade['public_entry_token']}" in page
    assert f"https://vps.test/t/{trade['public_exit_token']}" in page

    # Console offers "Share close" for it; the route pushes exit mirrors.
    assert "share-close" in client.get("/").text
    telegram.sent.clear()
    r = client.post(f"/trade/{trade['id']}/share-close", follow_redirects=False)
    assert r.status_code == 303
    assert ledger.trade(trade["id"])["status"] == "CLOSED"
    assert len(telegram.sent) == 3
    assert kite.orders == []


def test_public_link_works_with_no_friends_configured(client, ledger, kite, telegram):
    """The core share-only ask: no order on my account, no Telegram needed —
    just a URL I copy and send myself; any friend's tap places THEIR order."""
    client.post("/trade", data={
        "tradingsymbol": "CRUDEOIL25JULFUT", "exchange": "MCX", "side": "BUY",
        "qty": "100", "product": "NRML", "order_type": "MARKET", "share_only": "on",
    }, follow_redirects=False)
    assert kite.orders == [] and telegram.sent == []
    trade = ledger.trades()[0]

    # Entry link renders the pre-filled basket at base qty.
    r = client.get(f"/t/{trade['public_entry_token']}")
    assert r.status_code == 200
    assert "kite.zerodha.com/connect/basket" in r.text
    assert "CRUDEOIL25JULFUT" in r.text and "BUY" in r.text

    # Close link renders the opposite side.
    r = client.get(f"/t/{trade['public_exit_token']}")
    assert r.status_code == 200 and "SELL" in r.text

    # A completed basket bumps the public confirm counter (repeatable —
    # several friends can use the same link).
    client.get(f"/kite/redirect?status=success&public_token={trade['public_entry_token']}")
    client.get(f"/kite/redirect?status=success&public_token={trade['public_entry_token']}")
    client.get(f"/kite/redirect?status=cancelled&public_token={trade['public_exit_token']}")
    trade = ledger.trade(trade["id"])
    assert trade["public_entry_confirms"] == 2
    assert trade["public_exit_confirms"] == 0
    assert "(2✓)" in client.get("/").text


def test_placed_trades_also_get_public_links(client, ledger, kite, friends):
    client.post("/trade", data={
        "tradingsymbol": "GOLD25AUGFUT", "exchange": "MCX", "side": "SELL",
        "qty": "10", "product": "NRML", "order_type": "MARKET",
    }, follow_redirects=False)
    trade = ledger.trades()[0]
    assert trade["public_entry_token"] and trade["public_exit_token"]
    assert client.get(f"/t/{trade['public_entry_token']}").status_code == 200


def test_mirror_unknown_token_404(client):
    assert client.get("/m/nope").status_code == 404
    assert client.get("/t/nope").status_code == 404


def test_failed_basket_redirect_does_not_confirm(client, ledger, kite, telegram, friends):
    client.post("/trade", data={
        "tradingsymbol": "GOLD25AUGFUT", "exchange": "MCX", "side": "SELL",
        "qty": "10", "product": "NRML", "order_type": "MARKET",
    }, follow_redirects=False)
    trade = ledger.trades()[0]
    client.post("/kite/postback", json=postback(kite, trade["entry_order_id"], filled_qty=10))
    share = ledger.shares_for_trade(trade["id"], "ENTRY")[0]

    r = client.get(f"/kite/redirect?status=cancelled&share_token={share['token']}")
    assert r.status_code == 200
    assert ledger.share(share["id"])["status"] == "SENT"  # still amber


def test_friends_admin(client, ledger):
    r = client.post("/friends", data={
        "name": "Ravi", "telegram_chat_id": "42", "multiplier": "0.5",
    }, follow_redirects=False)
    assert r.status_code == 303
    f = ledger.friends()[0]
    assert (f["name"], f["telegram_chat_id"], f["multiplier"]) == ("Ravi", "42", 0.5)
