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

    # Fan-out to the 3 active friends only, per-friend scaled.
    entry_shares = ledger.shares_for_trade(trade_id, "ENTRY")
    assert [s["qty"] for s in entry_shares] == [100, 50, 200]
    assert all(s["status"] == "SENT" for s in entry_shares)
    assert len(telegram.sent) == 3
    assert all("/m/" in msg[3] for msg in telegram.sent)

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


def test_duplicate_fill_postback_does_not_refan(service, kite, telegram, friends, ledger):
    trade_id = place_and_fill(service, kite)
    trade = ledger.trade(trade_id)
    assert len(telegram.sent) == 3
    service.handle_postback(postback(kite, trade["entry_order_id"], filled_qty=100))
    assert len(telegram.sent) == 3  # no double fan-out
