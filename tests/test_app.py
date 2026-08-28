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
    monkeypatch.setattr(app_settings, "db_path", settings.db_path)
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
    assert "dte" in hits[0] and hits[0]["atm_pct"] is None  # futures: no strike


def test_underlying_search_and_chain(client):
    """Ticker search finds underlyings; the chain feeds the type/expiry/strike
    dropdowns so one front-series expiry can't flood the results."""
    hits = client.get("/api/underlyings?q=crude").json()
    assert hits == [{"name": "CRUDEOIL", "exchange": "MCX",
                     "futures": 2, "options": 2, "equity": 0}]

    chain = client.get("/api/chain?exchange=MCX&name=CRUDEOIL").json()
    assert [f["tradingsymbol"] for f in chain["futures"]] == [
        "CRUDEOIL25JULFUT", "CRUDEOIL25AUGFUT"]      # every expiry present
    assert all("dte" in f for f in chain["futures"])
    opt = chain["options"][0]
    assert (opt["tradingsymbol"], opt["strike"], opt["type"]) == ("CRUDEOIL26JUL5500CE", 5500.0, "CE")
    assert chain["underlying_ltp"] == 6250.0          # anchors the ATM% labels

    assert client.get("/api/chain?exchange=MCX&name=NOPE").status_code == 404

    eq = client.get("/api/underlyings?q=reliance").json()[0]
    assert eq["equity"] == 1
    assert client.get("/api/chain?exchange=NSE&name=RELIANCE%20INDUSTRIES").json()["equities"][0]["tradingsymbol"] == "RELIANCE"


def test_chain_atm_survives_expired_session(client, kite):
    """The prod symptom: token died overnight, quotes 403, and the strike
    dropdown lost its ATM window/labels. The chain must fall back to the
    dump's previous close so ATM context keeps working."""
    client.get("/api/underlyings?q=crude")   # loads the instrument dump
    kite.access_token = None                 # overnight token death

    chain = client.get("/api/chain?exchange=MCX&name=CRUDEOIL").json()
    assert chain["underlying_ltp"] == 6200.0            # dump prev close
    assert chain["underlying_ltp_source"] == "prev close"

    hits = client.get("/api/instruments?q=crudeoil 5500").json()
    ce = next(h for h in hits if h["tradingsymbol"] == "CRUDEOIL26JUL5500CE")
    assert ce["moneyness"] == "11.3% ITM"               # (5500-6200)/6200

    # With a live session, the live quote wins over the dump price.
    kite.access_token = "tok"
    chain = client.get("/api/chain?exchange=MCX&name=CRUDEOIL").json()
    assert chain["underlying_ltp"] == 6250.0
    assert chain["underlying_ltp_source"] == "live"


def test_search_and_contract_show_atm_distance(client):
    """Options carry strike distance from the underlying future's LTP."""
    hits = client.get("/api/instruments?q=crudeoil 5500").json()
    ce = next(h for h in hits if h["tradingsymbol"] == "CRUDEOIL26JUL5500CE")
    assert ce["atm_pct"] == -12.0        # (5500 - 6250) / 6250
    assert ce["moneyness"] == "12.0% ITM"  # CE below spot is in the money

    c = client.get("/api/contract?exchange=MCX&tradingsymbol=CRUDEOIL26JUL5500CE").json()
    assert c["atm_pct"] == -12.0
    assert c["moneyness"] == "12.0% ITM"
    assert "dte" in c


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


def test_share_only_flow_over_http(client, ledger, kite, telegram, friends):
    r = client.post("/trade", data={
        "tradingsymbol": "CRUDEOIL25JULFUT", "exchange": "MCX", "side": "BUY",
        "qty": "100", "product": "NRML", "order_type": "MARKET", "mode": "share_url",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert kite.orders == []
    trade = ledger.trades()[0]
    assert trade["status"] == "SHARED"

    # The redirect lands on a links-ready panel with both public URLs.
    assert r.headers["location"] == f"/?shared={trade['id']}"
    page = client.get(r.headers["location"]).text
    assert "no order was placed" in page
    assert f"https://vps.test/t/{trade['public_entry_token']}" in page
    assert f"https://vps.test/t/{trade['public_exit_token']}" in page

    # Console offers "Share close" for it; the route pushes exit mirrors.
    assert "share-close" in client.get("/").text
    r = client.post(f"/trade/{trade['id']}/share-close", follow_redirects=False)
    assert r.status_code == 303
    assert ledger.trade(trade["id"])["status"] == "CLOSED"
    assert kite.orders == []


def test_public_link_works_with_no_friends_configured(client, ledger, kite, telegram):
    """The core share-only ask: no order on my account, no Telegram needed —
    just a URL I copy and send myself; any friend's tap places THEIR order."""
    client.post("/trade", data={
        "tradingsymbol": "CRUDEOIL25JULFUT", "exchange": "MCX", "side": "BUY",
        "qty": "100", "product": "NRML", "order_type": "MARKET", "mode": "share_url",
    }, follow_redirects=False)
    assert kite.orders == [] and telegram.sent == []
    trade = ledger.trades()[0]

    # Entry link renders the pre-filled basket at base qty, with the live
    # price and estimated order value visible before confirming.
    r = client.get(f"/t/{trade['public_entry_token']}")
    assert r.status_code == 200
    assert "kite.zerodha.com/connect/basket" in r.text
    assert "CRUDEOIL25JULFUT" in r.text and "BUY" in r.text
    assert "₹6,250.00" in r.text                    # LTP
    assert "₹62,500,000.00" in r.text               # 100 lots × 100 units × 6250

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


def test_atm_from_oi_mode(client, kite):
    """ATM = the strike carrying the most OI across CE+PE for the expiry."""
    kite.quote_overrides["MCX:CRUDEOIL26JUL6000PE"] = {"oi": 99999}
    r = client.get("/api/atm?exchange=MCX&name=CRUDEOIL&expiry=2026-07-15")
    assert r.status_code == 200
    body = r.json()
    assert body["atm_strike"] == 6000.0     # PE's 99999 beats CE's 6789
    assert body["oi_at_atm"] == 99999

    assert client.get("/api/atm?exchange=MCX&name=CRUDEOIL&expiry=2099-01-01").status_code == 404
    kite.access_token = None
    assert client.get("/api/atm?exchange=MCX&name=CRUDEOIL&expiry=2026-07-15").status_code == 409


def test_strike_quotes_api(client, kite):
    """Batch premium + OI per strike for the dropdown decoration."""
    r = client.get("/api/strike_quotes?i=MCX:CRUDEOIL26JUL5500CE,MCX:GOLD25AUGFUT")
    assert r.status_code == 200
    q = r.json()["MCX:CRUDEOIL26JUL5500CE"]
    assert q["ltp"] == 6250.0
    assert q["oi"] == 6789

    assert client.get("/api/strike_quotes?i=").status_code == 400
    kite.access_token = None
    assert client.get("/api/strike_quotes?i=MCX:X").status_code == 409


def test_order_cost_api(client):
    r = client.get("/api/cost?exchange=MCX&tradingsymbol=CRUDEOIL25JULFUT&side=BUY&qty=3")
    assert r.status_code == 200
    c = r.json()
    assert c["lots"] == 3                    # MCX qty is lots
    assert c["units"] == 300                 # 3 lots × lot_size 100
    assert c["ref_price"] == 6250.0          # MARKET anchors to LTP
    assert c["order_value"] == 1875000.0     # 6250 × 300
    assert c["total"] == 150000.0            # Kite margins API total
    assert c["per_lot"] == 50000.0           # total / lots
    assert c["span"] == 90000.0 and c["exposure"] == 60000.0

    # LIMIT price overrides the anchor.
    c = client.get("/api/cost?exchange=MCX&tradingsymbol=CRUDEOIL25JULFUT"
                   "&side=BUY&qty=1&order_type=LIMIT&price=6000").json()
    assert c["ref_price"] == 6000.0
    assert c["order_value"] == 600000.0

    assert client.get("/api/cost?exchange=MCX&tradingsymbol=NOPE&side=BUY&qty=1").status_code == 404


def test_placed_trades_also_get_public_links(client, ledger, kite, friends):
    client.post("/trade", data={
        "tradingsymbol": "GOLD25AUGFUT", "exchange": "MCX", "side": "SELL",
        "qty": "10", "product": "NRML", "order_type": "MARKET",
    }, follow_redirects=False)
    trade = ledger.trades()[0]
    assert trade["public_entry_token"] and trade["public_exit_token"]
    assert client.get(f"/t/{trade['public_entry_token']}").status_code == 200


def test_unpriceable_option_mirror_blocks_and_alerts(client, ledger, kite, telegram, monkeypatch):
    """The screenshot bug: a share-only MCX option with no session and no fill
    price must NOT render a bare MARKET basket (exchange rejects it) — the
    friend gets a try-again page and the owner gets a Telegram alert."""
    monkeypatch.setattr(app_settings, "owner_telegram_chat_id", "999")
    client.post("/trade", data={
        "tradingsymbol": "CRUDEOIL26JUL5500CE", "exchange": "MCX", "side": "BUY",
        "qty": "1", "product": "NRML", "order_type": "MARKET", "mode": "share_url",
    }, follow_redirects=False)
    trade = ledger.trades()[0]

    kite.access_token = None  # daily login lapsed before the friend taps
    r = client.get(f"/t/{trade['public_entry_token']}")
    assert r.status_code == 200
    assert "connect/basket" not in r.text          # no doomed MARKET basket
    assert "Can't build this order right now" in r.text
    assert any(m[0] == "999" and "Kite login" in m[1] for m in telegram.sent)

    # Session restored: same link now renders the protected-LIMIT basket.
    kite.access_token = "tok"
    r = client.get(f"/t/{trade['public_entry_token']}")
    assert "connect/basket" in r.text and "LIMIT" in r.text


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
        "name": "Ravi", "multiplier": "0.5",
    }, follow_redirects=False)
    assert r.status_code == 303
    f = ledger.friends()[0]
    assert (f["name"], f["multiplier"]) == ("Ravi", 0.5)
