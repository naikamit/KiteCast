"""End-to-end flow tests against the PRD acceptance criteria (§11)."""

from tests.conftest import postback


def place_and_fill(service, kite):
    trade_id = service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    trade = service.ledger.trade(trade_id)
    assert service.handle_postback(postback(kite, trade["entry_order_id"], filled_qty=100))
    return trade_id


def test_entry_fill_writes_ledger_and_fans_mirrors(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    trade = ledger.trade(trade_id)

    # Acceptance: my order fired, fill recorded.
    assert kite.orders[0]["transaction_type"] == "BUY"
    assert trade["status"] == "FILLED"
    assert trade["entry_fill_price"] == 6250.0

    # Fan-out to the 3 active friends only, per-friend scaled, with the
    # live price in the push text.
    entry_shares = ledger.shares_for_trade(trade_id, "ENTRY")
    assert [s["qty"] for s in entry_shares] == [100, 50, 200]
    assert all(s["status"] == "SENT" for s in entry_shares)
    assert len(telegram.sent) == 3
    assert all("/m/" in msg[3] for msg in telegram.sent)
    assert all("LTP ₹6,250.00" in msg[1] for msg in telegram.sent)

    # Acceptance: exit links auto-built at entry, held for Close & Share (NFR-1).
    exit_shares = ledger.shares_for_trade(trade_id, "EXIT")
    assert [s["qty"] for s in exit_shares] == [100, 50, 200]
    assert all(s["status"] == "PREBUILT" for s in exit_shares)


def test_no_push_before_my_fill_by_default(service, kite, telegram, friends):
    service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    assert telegram.sent == []  # share timing defaults to on-my-fill


def test_placement_timing_fans_immediately(service, kite, telegram, friends, settings):
    settings.share_timing_entry = "placement"
    service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    assert len(telegram.sent) == 3


def test_friend_confirmation_flips_green(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    share = ledger.shares_for_trade(trade_id, "ENTRY")[0]

    assert service.confirm_share(share["token"]) is True
    assert ledger.share(share["id"])["status"] == "CONFIRMED"
    # Idempotent: a replayed redirect doesn't double-confirm.
    assert service.confirm_share(share["token"]) is False
    assert service.confirm_share("bogus-token") is False


def test_close_and_share_pushes_matching_close(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    telegram.sent.clear()

    exit_order_id = service.close_and_share(trade_id)
    trade = ledger.trade(trade_id)
    assert trade["status"] == "CLOSING"

    # My close: opposite side, full qty, MARKET, autoslice (at-any-cost).
    close = kite.orders[-1]
    assert close["transaction_type"] == "SELL"
    assert close["quantity"] == 100
    assert close["order_type"] == "MARKET"
    assert close["autoslice"] is True

    # Default timing: exit mirrors fan on MY fill.
    assert telegram.sent == []
    assert service.handle_postback(postback(kite, exit_order_id, avg_price=6900.0))
    trade = ledger.trade(trade_id)
    assert trade["status"] == "CLOSED"
    assert trade["exit_fill_price"] == 6900.0
    assert len(telegram.sent) == 3
    assert all("Close now" in msg[2] for msg in telegram.sent)
    exit_shares = ledger.shares_for_trade(trade_id, "EXIT")
    assert all(s["status"] == "SENT" for s in exit_shares)


def test_close_rejects_unfilled_trade(service, kite, friends):
    trade_id = service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    try:
        service.close_and_share(trade_id)
        assert False, "should refuse to close an unfilled trade"
    except ValueError:
        pass


def test_kite_app_trades_stay_personal(service, kite, telegram, friends, ledger):
    """Acceptance: trades placed directly in the Kite app are NOT shared."""
    assert service.handle_postback(postback(kite, "999999")) is False
    assert telegram.sent == []
    assert ledger.trades() == []


def test_forged_postback_is_dropped(service, kite, telegram, friends):
    trade_id = place_and_fill(service, kite)
    forged = postback(kite, "555")
    forged["checksum"] = "not-the-checksum"
    assert service.handle_postback(forged) is False


def test_rejected_entry_marks_failed_and_shares_nothing(service, kite, telegram, friends, ledger):
    trade_id = service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    trade = ledger.trade(trade_id)
    service.handle_postback(postback(kite, trade["entry_order_id"], status="REJECTED"))
    assert ledger.trade(trade_id)["status"] == "FAILED"
    assert telegram.sent == []
    assert ledger.shares_for_trade(trade_id) == []


def test_nudge_repings_straggler(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    share = ledger.shares_for_trade(trade_id, "ENTRY")[0]
    telegram.sent.clear()

    assert service.nudge(share["id"]) is True
    assert len(telegram.sent) == 1
    assert "Reminder" in telegram.sent[0][1]
    assert ledger.share(share["id"])["nudge_count"] == 1

    # Confirmed friends can't be nudged.
    service.confirm_share(share["token"])
    assert service.nudge(share["id"]) is False


def test_manual_share_friend_gets_link_not_push(service, kite, telegram, ledger):
    """A friend without a Telegram chat id is manual-share: their mirror link
    is created and copyable from the board, but no push goes out."""
    ledger.add_friend("Ravi", "1001")
    ledger.add_friend("NoTelegram", "")
    trade_id = place_and_fill(service, kite)

    shares = ledger.shares_for_trade(trade_id, "ENTRY")
    assert len(shares) == 2
    assert all(s["status"] == "SENT" for s in shares)
    assert len(telegram.sent) == 1          # only Ravi got a push
    assert telegram.sent[0][0] == "1001"


def test_commodity_option_market_order_becomes_protected_limit(service, kite, telegram, friends, ledger):
    """MCX options reject bare MARKET; my order goes out as LIMIT at LTP+5%."""
    trade_id = service.place_and_share(
        tradingsymbol="CRUDEOIL26JUL5500CE", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    placed = kite.orders[0]
    assert placed["order_type"] == "LIMIT"
    assert placed["price"] == 6562.5     # LTP 6250 * 1.05, tick 0.1
    # Ledger keeps the MARKET intent for tap-time re-anchoring.
    assert ledger.trade(trade_id)["order_type"] == "MARKET"

    # Close goes out as a protected LIMIT on the opposite side.
    trade = ledger.trade(trade_id)
    service.handle_postback(postback(kite, trade["entry_order_id"], filled_qty=100))
    service.close_and_share(trade_id)
    close = kite.orders[-1]
    assert close["transaction_type"] == "SELL"
    assert close["order_type"] == "LIMIT"
    assert close["price"] == 5625.0      # LTP 6250 * 0.90 (exit pct defaults wider)

    # Friend mirrors are protected LIMITs too, anchored at tap time.
    share = ledger.shares_for_trade(trade_id, "ENTRY")[0]
    order = service.build_mirror_order(share, ledger.trade(trade_id))
    assert order["order_type"] == "LIMIT" and order["price"] == 6562.5
    exit_share = ledger.shares_for_trade(trade_id, "EXIT")[0]
    order = service.build_mirror_order(exit_share, ledger.trade(trade_id))
    assert order["transaction_type"] == "SELL"
    assert order["order_type"] == "LIMIT" and order["price"] == 5625.0


def test_futures_market_orders_stay_market(service, kite, friends):
    service.place_and_share(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    assert kite.orders[0]["order_type"] == "MARKET"
    assert kite.orders[0].get("price") is None


def test_protected_limit_falls_back_to_fill_price_without_session(service, kite, telegram, friends, ledger):
    """A friend tapping after my token expired still gets a valid LIMIT,
    anchored to my recorded entry fill."""
    trade_id = service.place_and_share(
        tradingsymbol="CRUDEOIL26JUL5500CE", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    trade = ledger.trade(trade_id)
    service.handle_postback(postback(kite, trade["entry_order_id"], filled_qty=100, avg_price=6000.0))
    kite.access_token = None  # session expired -> quote() raises
    share = ledger.shares_for_trade(trade_id, "ENTRY")[0]
    order = service.build_mirror_order(share, ledger.trade(trade_id))
    assert order["order_type"] == "LIMIT"
    assert order["price"] == 6300.0      # entry fill 6000 * 1.05


def test_option_entry_without_anchor_price_fails_loudly(service, kite, friends):
    """If the quote is unavailable (403, expired session), a MARKET option
    entry must error clearly instead of firing a doomed bare MARKET order."""
    from kitecast.kite import KiteError

    kite.access_token = None  # quote() raises
    try:
        service.place_and_share(
            tradingsymbol="CRUDEOIL26JUL5500CE", exchange="MCX", side="BUY",
            qty=100, product="NRML", order_type="MARKET", price=None,
        )
        assert False, "expected KiteError"
    except KiteError as e:
        assert "market protection" in str(e)
    assert kite.orders == []  # no bare MARKET went out


def test_share_only_fans_without_placing_my_order(service, kite, telegram, friends, ledger):
    trade_id = service.share_only(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    assert kite.orders == []                       # nothing on MY account
    trade = ledger.trade(trade_id)
    assert trade["status"] == "SHARED"
    assert trade["entry_order_id"] is None

    # Mirrors pushed immediately (no fill to wait for), exits pre-built.
    entry_shares = ledger.shares_for_trade(trade_id, "ENTRY")
    assert [s["qty"] for s in entry_shares] == [100, 50, 200]
    assert all(s["status"] == "SENT" for s in entry_shares)
    assert len(telegram.sent) == 3
    assert all(s["status"] == "PREBUILT" for s in ledger.shares_for_trade(trade_id, "EXIT"))

    # Share close: pushes exit mirrors, still no order on my account.
    telegram.sent.clear()
    service.share_only_close(trade_id)
    assert kite.orders == []
    assert ledger.trade(trade_id)["status"] == "CLOSED"
    assert len(telegram.sent) == 3
    assert all("Close now" in msg[2] for msg in telegram.sent)

    # Can't share-close twice, and Close & Share rejects share-only trades.
    try:
        service.share_only_close(trade_id)
        assert False
    except ValueError:
        pass


def test_close_and_share_rejects_share_only_trade(service, kite, friends):
    trade_id = service.share_only(
        tradingsymbol="CRUDEOIL25JULFUT", exchange="MCX", side="BUY",
        qty=100, product="NRML", order_type="MARKET", price=None,
    )
    try:
        service.close_and_share(trade_id)
        assert False, "close_and_share must not fire an order for a share-only trade"
    except ValueError:
        pass


def test_duplicate_fill_postback_does_not_refan(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    trade = ledger.trade(trade_id)
    assert len(telegram.sent) == 3
    service.handle_postback(postback(kite, trade["entry_order_id"], filled_qty=100))
    assert len(telegram.sent) == 3  # no double fan-out
