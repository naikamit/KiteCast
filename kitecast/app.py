"""FastAPI app: Share console, per-friend board, Kite postback + redirect,
friend mirror pages. The postback is checksum-verified and the
mirror/redirect routes are token-scoped. The console itself is
unauthenticated — keep the URL private."""

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import basket
from .config import settings
from .db import Ledger
from .instruments import InstrumentStore, days_to_expiry, moneyness
from .kite import KiteClient, KiteError
from .service import TradeShareService
from .telegram import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def build_app(ledger: Ledger | None = None, kite: KiteClient | None = None,
              telegram: TelegramClient | None = None) -> FastAPI:
    ledger = ledger or Ledger(settings.db_path)
    kite = kite or KiteClient(settings.kite_api_key, settings.kite_api_secret,
                              access_token=ledger.kv_get("access_token"),
                              order_proxy=settings.kite_order_proxy)
    telegram = telegram or TelegramClient(settings.telegram_bot_token)
    store = InstrumentStore(kite)
    service = TradeShareService(ledger, kite, telegram, settings, store=store)

    app = FastAPI(title="KiteCast Trade Share")
    app.state.service = service

    # ---- my side: Share console ----

    @app.get("/", response_class=HTMLResponse)
    def console(request: Request):
        shared_trade = None
        shared_id = request.query_params.get("shared")
        if shared_id and shared_id.isdigit():
            shared_trade = ledger.trade(int(shared_id))
        return templates.TemplateResponse(request, "console.html", {
            "trades": ledger.trades(),
            "logged_in": kite.access_token is not None,
            "flash": request.query_params.get("flash"),
            "base_url": settings.base_url,
            "shared_trade": shared_trade,
        })

    @app.post("/trade")
    def place_trade(tradingsymbol: str = Form(...), exchange: str = Form("MCX"),
                    side: str = Form(...), qty: int = Form(...),
                    product: str = Form("NRML"), order_type: str = Form("MARKET"),
                    price: float | None = Form(None), mode: str = Form("place")):
        if side not in ("BUY", "SELL") or qty <= 0:
            raise HTTPException(400, "Invalid ticket")
        ticket = dict(
            tradingsymbol=tradingsymbol.strip().upper(), exchange=exchange,
            side=side, qty=qty, product=product, order_type=order_type,
            price=price if order_type == "LIMIT" else None,
        )
        try:
            if mode == "share_url":
                trade_id = service.share_only(**ticket)
                return RedirectResponse(f"/?shared={trade_id}", status_code=303)
            trade_id = service.place_and_share(**ticket)
        except KiteError as e:
            return RedirectResponse(f"/?flash=Order failed: {e}", status_code=303)
        return RedirectResponse(f"/?flash=Placed & sharing (trade #{trade_id})", status_code=303)

    @app.post("/trade/{trade_id}/share-close")
    def share_close(trade_id: int):
        try:
            service.share_only_close(trade_id)
        except ValueError as e:
            return RedirectResponse(f"/?flash=Share close failed: {e}", status_code=303)
        return RedirectResponse(f"/?flash=Close shared to friends (trade #{trade_id})", status_code=303)

    @app.post("/trade/{trade_id}/close")
    def close_trade(trade_id: int):
        try:
            service.close_and_share(trade_id)
        except (KiteError, ValueError) as e:
            return RedirectResponse(f"/?flash=Close failed: {e}", status_code=303)
        return RedirectResponse(f"/?flash=Closing & sharing (trade #{trade_id})", status_code=303)

    # ---- my side: confirmation dashboard ----

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request):
        friends = ledger.friends()
        rows = []
        for trade in ledger.trades():
            shares = {(s["friend_id"], s["leg"]): s for s in ledger.shares_for_trade(trade["id"])}
            rows.append({"trade": trade, "shares": shares})
        return templates.TemplateResponse(request, "board.html", {
            "rows": rows, "friends": friends, "base_url": settings.base_url,
        })

    @app.post("/share/{share_id}/nudge")
    def nudge(share_id: int):
        service.nudge(share_id)
        return RedirectResponse("/board", status_code=303)

    # ---- my side: friends admin ----

    @app.get("/friends", response_class=HTMLResponse)
    def friends_page(request: Request):
        return templates.TemplateResponse(request, "friends.html", {"friends": ledger.friends()})

    @app.post("/friends")
    def add_friend(name: str = Form(...),
                   telegram_chat_id: str = Form(""), multiplier: float = Form(1.0)):
        ledger.add_friend(name.strip(), telegram_chat_id.strip(), multiplier)
        return RedirectResponse("/friends", status_code=303)

    @app.post("/friends/{friend_id}")
    def update_friend(friend_id: int,
                      multiplier: float = Form(1.0), active: str = Form("off")):
        ledger.update_friend(friend_id, multiplier=multiplier, active=(active == "on"))
        return RedirectResponse("/friends", status_code=303)

    # ---- contract data for the ticket (live Kite instrument master + quotes) ----

    ltp_cache: dict[str, tuple[float, float]] = {}  # key -> (fetched_at, ltp)

    def cached_ltps(keys: set[str]) -> dict[str, float]:
        now = time.time()
        missing = [k for k in keys if k not in ltp_cache or now - ltp_cache[k][0] > 20]
        if missing:
            try:
                data = kite.quote(*missing)
                for k in missing:
                    lp = (data.get(k) or {}).get("last_price")
                    if lp:
                        ltp_cache[k] = (now, lp)
            except KiteError:
                pass
        return {k: v[1] for k, v in ltp_cache.items() if k in keys}

    def enrich(inst: dict, underlying_ltps: dict[str, float]) -> dict:
        """Add dte and, for options, strike distance from ATM plus moneyness."""
        out = {**inst, "dte": days_to_expiry(inst["expiry"]), "atm_pct": None, "moneyness": None}
        if inst["strike"] and inst["instrument_type"] in ("CE", "PE"):
            fut = store.underlying_future(inst["exchange"], inst["name"])
            u_ltp = fut and underlying_ltps.get(f"{fut['exchange']}:{fut['tradingsymbol']}")
            if u_ltp:
                out["atm_pct"] = round((inst["strike"] - u_ltp) / u_ltp * 100, 1)
                out["moneyness"] = moneyness(out["atm_pct"], inst["instrument_type"])
        return out

    def underlying_keys(instruments: list[dict]) -> set[str]:
        keys = set()
        for inst in instruments:
            if inst["strike"] and inst["instrument_type"] in ("CE", "PE"):
                fut = store.underlying_future(inst["exchange"], inst["name"])
                if fut:
                    keys.add(f"{fut['exchange']}:{fut['tradingsymbol']}")
        return keys

    @app.get("/api/underlyings")
    def search_underlyings_api(q: str = ""):
        try:
            return store.search_underlyings(q)
        except KiteError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/chain")
    def chain_api(exchange: str, name: str):
        try:
            data = store.chain(exchange, name)
            fut = store.underlying_future(exchange, name)
            ltps = cached_ltps({f"{exchange}:{fut['tradingsymbol']}"} if fut else set())
        except KiteError as e:
            raise HTTPException(409, str(e))
        if not (data["futures"] or data["options"] or data["equities"]):
            raise HTTPException(404, "Unknown underlying")
        data["underlying_ltp"] = next(iter(ltps.values()), None)
        return data

    @app.get("/api/instruments")
    def search_instruments(q: str = ""):
        try:
            hits = store.search(q)
            ltps = cached_ltps(underlying_keys(hits))
        except KiteError as e:
            raise HTTPException(409, str(e))
        return [enrich(h, ltps) for h in hits]

    @app.get("/api/contract")
    def contract_info(exchange: str, tradingsymbol: str):
        try:
            inst = store.get(exchange, tradingsymbol)
        except KiteError as e:
            raise HTTPException(409, str(e))
        if inst is None:
            raise HTTPException(404, "Unknown contract")
        key = f"{inst['exchange']}:{inst['tradingsymbol']}"
        try:
            q = kite.quote(key).get(key, {})
        except KiteError:
            q = {}  # instrument details still useful without a live quote
        depth = q.get("depth") or {}
        best_bid = (depth.get("buy") or [{}])[0]
        best_ask = (depth.get("sell") or [{}])[0]
        return {
            **enrich(inst, cached_ltps(underlying_keys([inst]))),
            "quote": {
                "last_price": q.get("last_price"),
                "net_change": q.get("net_change"),
                "ohlc": q.get("ohlc") or {},
                "volume": q.get("volume"),
                "oi": q.get("oi"),
                "upper_circuit_limit": q.get("upper_circuit_limit"),
                "lower_circuit_limit": q.get("lower_circuit_limit"),
                "best_bid": best_bid,
                "best_ask": best_ask,
            },
        }

    @app.get("/api/cost")
    def order_cost(exchange: str, tradingsymbol: str, side: str, qty: int,
                   product: str = "NRML", order_type: str = "MARKET",
                   price: float | None = None):
        """Everything the ticket needs to show what this order costs:
        anchor price, order value, and Kite's real margin/premium number,
        per lot and multiplied out."""
        tradingsymbol = tradingsymbol.upper()
        try:
            inst = store.get(exchange, tradingsymbol)
            key = f"{exchange}:{tradingsymbol}"
            quote = kite.quote(key).get(key, {})
        except KiteError as e:
            raise HTTPException(409, str(e))
        if inst is None:
            raise HTTPException(404, "Unknown contract")
        from .kite import best_price as _best

        ltp = quote.get("last_price") or 0
        ref_price = price if (order_type == "LIMIT" and price) else (_best(quote, side) or 0)
        lot_size = inst["lot_size"] or 1
        # MCX quantity is lots; other segments take units in lot multiples.
        units = qty * lot_size if exchange == "MCX" else qty
        lots = qty if exchange == "MCX" else (qty // lot_size if lot_size > 1 else qty)
        margin = {}
        try:
            margin = kite.order_margins({
                "exchange": exchange, "tradingsymbol": tradingsymbol,
                "transaction_type": side, "variety": "regular",
                "product": product, "order_type": order_type,
                "quantity": qty, "price": ref_price or 0,
            })
        except KiteError as e:
            margin = {"error": str(e)}
        total = margin.get("total")
        return {
            "ltp": ltp, "ref_price": ref_price, "lot_size": lot_size,
            "units": units, "lots": lots,
            "order_value": round(ref_price * units, 2) if ref_price else None,
            "total": total,
            "per_lot": round(total / lots, 2) if total and lots else None,
            "span": margin.get("span"), "exposure": margin.get("exposure"),
            "option_premium": margin.get("option_premium"),
            "margin_error": margin.get("error"),
        }

    # ---- daily Kite login ----

    @app.get("/auth/login")
    def auth_login():
        return RedirectResponse(kite.login_url())

    # ---- Kite webhooks: postback (fills) + redirect (login & basket confirms) ----

    @app.post("/kite/postback")
    async def postback(request: Request):
        payload = await request.json()
        service.handle_postback(payload)
        return {"ok": True}

    @app.get("/kite/redirect", response_class=HTMLResponse)
    def kite_redirect(request: Request):
        params = request.query_params
        share_token = params.get("share_token")
        if share_token:
            # A friend finished the Publisher basket flow in their own session.
            confirmed = params.get("status") == "success" and service.confirm_share(share_token)
            return templates.TemplateResponse(request, "done.html", {"confirmed": confirmed})
        public_token = params.get("public_token")
        if public_token:
            # Someone confirmed via an anyone-with-the-link mirror.
            found = ledger.trade_by_public_token(public_token)
            confirmed = bool(found) and params.get("status") == "success"
            if confirmed:
                ledger.record_public_confirm(found[0]["id"], found[1])
            return templates.TemplateResponse(request, "done.html", {"confirmed": confirmed})
        request_token = params.get("request_token")
        if request_token and params.get("status") == "success":
            # My daily Kite Connect login completing.
            try:
                token = kite.generate_session(request_token)
            except KiteError as e:
                return RedirectResponse(f"/?flash=Kite login failed: {e}", status_code=303)
            ledger.kv_set("access_token", token)
            return RedirectResponse("/?flash=Kite session ready", status_code=303)
        raise HTTPException(400, "Unrecognised redirect")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---- favicon: real artwork (static/icon.png) if present, else the
    # ---- bundled SVG placeholder in the same palette ----

    static_dir = Path(__file__).parent / "static"

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def favicon():
        icon_png = static_dir / "icon.png"
        if icon_png.exists():
            return FileResponse(icon_png, media_type="image/png")
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")

    # ---- friend side: one-tap mirror page ----

    @app.get("/m/{token}", response_class=HTMLResponse)
    def mirror(token: str, request: Request):
        share = ledger.share_by_token(token)
        if share is None:
            raise HTTPException(404, "Unknown or expired mirror link")
        trade = ledger.trade(share["trade_id"])
        order = service.build_mirror_order(share, trade)
        return templates.TemplateResponse(request, "mirror.html", {
            "leg": share["leg"], "order": order,
            "price": service.price_context(trade, order),
            "basket_url": basket.BASKET_URL,
            "fields": basket.basket_form_fields(settings.kite_api_key, order, token),
            "already_confirmed": share["status"] == "CONFIRMED",
        })

    @app.get("/t/{token}", response_class=HTMLResponse)
    def public_mirror(token: str, request: Request):
        """Anyone-with-the-link mirror: I copy this URL from the console and
        share it myself (group chat, WhatsApp, anywhere). Base qty, no
        per-friend attribution — confirms bump a counter."""
        found = ledger.trade_by_public_token(token)
        if found is None:
            raise HTTPException(404, "Unknown or expired mirror link")
        trade, leg = found
        order = service.build_public_order(trade, leg)
        return templates.TemplateResponse(request, "mirror.html", {
            "leg": leg, "order": order,
            "price": service.price_context(trade, order),
            "basket_url": basket.BASKET_URL,
            "fields": basket.basket_form_fields(settings.kite_api_key, order, token,
                                                token_param="public_token"),
            "already_confirmed": False,
        })

    return app


def create_app() -> FastAPI:
    return build_app()
