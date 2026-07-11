"""Orchestration: Place & Share, fill fan-out, Close & Share, nudges.

Origination model (PRD §2): every shared trade is born here, fired on my
Kite Connect api_key, so the ledger holds the exact truth. Orders placed
directly in the Kite app never enter the ledger and are never shared.
"""

import logging

from . import basket
from .config import Settings
from .db import Ledger
from .kite import KiteClient
from .telegram import TelegramClient

log = logging.getLogger("kitecast")


class TradeShareService:
    def __init__(self, ledger: Ledger, kite: KiteClient, telegram: TelegramClient, settings: Settings):
        self.ledger = ledger
        self.kite = kite
        self.telegram = telegram
        self.settings = settings

    # ---- entry: Place & Share ----

    def place_and_share(self, *, tradingsymbol: str, exchange: str, side: str,
                        qty: int, product: str, order_type: str,
                        price: float | None) -> int:
        """Fire MY order, open the ledger row. Mirrors fan on my fill (default)
        or immediately, per SHARE_TIMING_ENTRY."""
        order_id = self.kite.place_order(
            tradingsymbol=tradingsymbol, exchange=exchange, transaction_type=side,
            quantity=qty, product=product, order_type=order_type, price=price,
        )
        trade_id = self.ledger.create_trade(
            tradingsymbol=tradingsymbol, exchange=exchange, side=side, qty=qty,
            product=product, order_type=order_type, price=price, entry_order_id=order_id,
        )
        log.info("placed entry trade=%s order=%s %s %s x%s", trade_id, order_id, side, tradingsymbol, qty)
        if self.settings.share_timing_entry == "placement":
            self._build_shares(trade_id)
            self._push_leg(trade_id, "ENTRY")
        return trade_id

    # ---- exit: Close & Share ----

    def close_and_share(self, trade_id: int) -> str:
        """Close MY position at any cost: opposite side, full qty, MARKET,
        autoslice past the freeze limit. Exit mirrors were pre-built at entry
        (NFR-1) and fan on my fill (default) or immediately."""
        trade = self.ledger.trade(trade_id)
        if trade is None or trade["status"] not in ("FILLED",):
            raise ValueError(f"Trade {trade_id} is not open (status={trade['status'] if trade else 'missing'})")
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        close_qty = trade["entry_filled_qty"] or trade["qty"]
        order_id = self.kite.place_order(
            tradingsymbol=trade["tradingsymbol"], exchange=trade["exchange"],
            transaction_type=close_side, quantity=close_qty,
            product=trade["product"], order_type="MARKET",
            autoslice=self.settings.kite_autoslice,
        )
        self.ledger.mark_closing(trade_id, order_id)
        log.info("placed exit trade=%s order=%s %s x%s", trade_id, order_id, close_side, close_qty)
        if self.settings.share_timing_exit == "placement":
            self._push_leg(trade_id, "EXIT")
        return order_id

    # ---- postbacks: how fills flow back to us ----

    def handle_postback(self, payload: dict) -> bool:
        """Kite order-update postback. Returns True if it moved our ledger.
        Unknown order_ids are trades placed directly in the Kite app —
        personal by design, silently ignored (PRD §2 boundary)."""
        if not self.kite.verify_postback(payload):
            log.warning("postback failed checksum, dropped")
            return False
        found = self.ledger.trade_by_order_id(str(payload.get("order_id", "")))
        if found is None:
            return False
        trade, leg = found
        status = payload.get("status", "")
        if status == "COMPLETE":
            if leg == "ENTRY" and trade["status"] == "PLACED":
                self.ledger.mark_entry_filled(
                    trade["id"],
                    float(payload.get("average_price") or 0),
                    int(payload.get("filled_quantity") or trade["qty"]),
                    str(payload.get("exchange_timestamp") or payload.get("order_timestamp") or ""),
                )
                # On my fill: build entry mirrors AND pre-build every exit
                # mirror now, so Close & Share later is push-only (NFR-1).
                self._build_shares(trade["id"])
                if self.settings.share_timing_entry == "fill":
                    self._push_leg(trade["id"], "ENTRY")
            elif leg == "EXIT" and trade["status"] in ("FILLED", "CLOSING"):
                self.ledger.mark_closed(
                    trade["id"],
                    float(payload.get("average_price") or 0),
                    str(payload.get("exchange_timestamp") or payload.get("order_timestamp") or ""),
                )
                if self.settings.share_timing_exit == "fill":
                    self._push_leg(trade["id"], "EXIT")
        elif status in ("REJECTED", "CANCELLED") and leg == "ENTRY" and trade["status"] == "PLACED":
            self.ledger.mark_failed(trade["id"])
        return True

    # ---- friend confirmations ----

    def confirm_share(self, token: str) -> bool:
        """The Publisher redirect landed — flip this friend green."""
        ok = self.ledger.mark_share_confirmed(token)
        if ok:
            share = self.ledger.share_by_token(token)
            log.info("share confirmed trade=%s friend=%s leg=%s", share["trade_id"], share["friend_id"], share["leg"])
        return ok

    def nudge(self, share_id: int) -> bool:
        """Re-ping a straggler. The board escalates to 'call' after re-pings."""
        share = self.ledger.share(share_id)
        if share is None or share["status"] == "CONFIRMED":
            return False
        trade = self.ledger.trade(share["trade_id"])
        friend = self.ledger.friend(share["friend_id"])
        sent = self.telegram.send(*self._message(trade, friend, share, nudge=True))
        self.ledger.record_nudge(share_id)
        if share["status"] == "PREBUILT":
            self.ledger.mark_share_sent(share_id)
        return sent

    # ---- internals ----

    def _build_shares(self, trade_id: int) -> None:
        """Create per-friend ENTRY and EXIT share rows (idempotent)."""
        trade = self.ledger.trade(trade_id)
        if self.ledger.shares_for_trade(trade_id):
            return
        for friend in self.ledger.friends(active_only=True):
            qty = basket.scaled_qty(trade["qty"], friend["multiplier"])
            if qty <= 0:
                continue
            self.ledger.create_share(trade_id, friend["id"], "ENTRY", qty)
            self.ledger.create_share(trade_id, friend["id"], "EXIT", qty)

    def _push_leg(self, trade_id: int, leg: str) -> None:
        """Fan the mirror links for one leg to every friend, concurrently."""
        if not self.ledger.shares_for_trade(trade_id):
            self._build_shares(trade_id)  # placement-timing exits before fill
        trade = self.ledger.trade(trade_id)
        shares = [s for s in self.ledger.shares_for_trade(trade_id, leg) if s["status"] != "CONFIRMED"]
        pushable, messages = [], []
        for share in shares:
            friend = self.ledger.friend(share["friend_id"])
            if friend["telegram_chat_id"]:
                pushable.append(share)
                messages.append(self._message(trade, friend, share))
            # No chat id: manual-share friend — link is copyable on the board.
            self.ledger.mark_share_sent(share["id"])
        for share, sent in zip(pushable, self.telegram.fan_out(messages)):
            if not sent:
                log.warning("telegram push failed share=%s", share["id"])

    def _message(self, trade, friend, share, nudge: bool = False) -> tuple[str, str, str, str]:
        url = basket.mirror_url(self.settings.base_url, share["token"])
        if share["leg"] == "ENTRY":
            action = f"{trade['side']} {share['qty']} × {trade['tradingsymbol']}"
            button = "Mirror this trade"
        else:
            side = "SELL" if trade["side"] == "BUY" else "BUY"
            action = f"CLOSE ({side}) {share['qty']} × {trade['tradingsymbol']}"
            button = "Close now"
        prefix = "⏰ Reminder — still pending:\n" if nudge else ""
        text = f"{prefix}<b>{action}</b>\n{trade['exchange']} · {trade['product']} · one tap to confirm in your Kite."
        return (friend["telegram_chat_id"], text, button, url)
