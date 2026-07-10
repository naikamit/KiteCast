"""Publisher basket payloads — the friend side of the split (PRD §4).

A mirror link lands the friend on /m/{token}, which auto-POSTs a one-order
basket to Kite. The friend confirms in their OWN session; we never hold
their tokens. redirect_params carries our share token back so the Publisher
redirect can flip the board green.
"""

import json
from urllib.parse import urlencode

BASKET_URL = "https://kite.zerodha.com/connect/basket"


def scaled_qty(qty: int, multiplier: float) -> int:
    """Per-friend sizing: uniform lots when multiplier=1, else scaled and
    rounded to whole units. A result of 0 means the friend is skipped."""
    return max(0, int(round(qty * multiplier)))


def entry_order(trade, qty: int) -> dict:
    return _order(trade["tradingsymbol"], trade["exchange"], trade["side"], qty,
                  trade["product"], trade["order_type"], trade["price"])


def exit_order(trade, qty: int) -> dict:
    """The at-any-cost close (NFR-1): opposite side, full mirrored qty, MARKET —
    no limit price for their market-protection to trip over."""
    side = "SELL" if trade["side"] == "BUY" else "BUY"
    return _order(trade["tradingsymbol"], trade["exchange"], side, qty,
                  trade["product"], "MARKET", None)


def _order(tradingsymbol: str, exchange: str, side: str, qty: int,
           product: str, order_type: str, price: float | None) -> dict:
    order = {
        "variety": "regular",
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": side,
        "order_type": order_type,
        "quantity": qty,
        "product": product,
        "readonly": False,
        "tag": "kitecast",
    }
    if order_type == "LIMIT" and price is not None:
        order["price"] = price
    return order


def basket_form_fields(api_key: str, order: dict, share_token: str) -> dict:
    """Fields for the auto-submitted POST to kite.zerodha.com/connect/basket."""
    return {
        "api_key": api_key,
        "data": json.dumps([order]),
        "redirect_params": urlencode({"share_token": share_token}),
    }


def mirror_url(base_url: str, token: str) -> str:
    return f"{base_url}/m/{token}"
