import json
from urllib.parse import parse_qs

from kitecast import basket


def make_trade(side="BUY", qty=100, order_type="MARKET", price=None):
    return {
        "tradingsymbol": "CRUDEOIL25JULFUT",
        "exchange": "MCX",
        "side": side,
        "qty": qty,
        "product": "NRML",
        "order_type": order_type,
        "price": price,
    }


def test_scaled_qty_multipliers():
    assert basket.scaled_qty(100, 1.0) == 100     # uniform lots
    assert basket.scaled_qty(100, 0.5) == 50
    assert basket.scaled_qty(3, 0.5) == 2         # rounds, doesn't truncate
    assert basket.scaled_qty(1, 0.2) == 0         # too small -> skip friend
    assert basket.scaled_qty(10, 2.0) == 20


def test_entry_order_mirrors_my_ticket():
    order = basket.entry_order(make_trade(order_type="LIMIT", price=6250.0), qty=50)
    assert order["transaction_type"] == "BUY"
    assert order["quantity"] == 50
    assert order["order_type"] == "LIMIT"
    assert order["price"] == 6250.0
    assert order["exchange"] == "MCX"
    assert order["readonly"] is False


def test_exit_order_is_opposite_full_qty_market():
    """NFR-1: the close is at-any-cost — opposite side, MARKET, no price."""
    order = basket.exit_order(make_trade(side="BUY", order_type="LIMIT", price=6250.0), qty=50)
    assert order["transaction_type"] == "SELL"
    assert order["order_type"] == "MARKET"
    assert "price" not in order
    assert order["quantity"] == 50

    order = basket.exit_order(make_trade(side="SELL"), qty=10)
    assert order["transaction_type"] == "BUY"


def test_basket_form_fields_carry_share_token():
    order = basket.entry_order(make_trade(), qty=100)
    fields = basket.basket_form_fields("my_api_key", order, "tok123")
    assert fields["api_key"] == "my_api_key"
    data = json.loads(fields["data"])
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["tradingsymbol"] == "CRUDEOIL25JULFUT"
    assert parse_qs(fields["redirect_params"]) == {"share_token": ["tok123"]}


def test_mirror_url():
    assert basket.mirror_url("https://vps.test", "abc") == "https://vps.test/m/abc"
