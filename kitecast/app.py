"""FastAPI app: Share console, per-friend board, Kite postback + redirect,
friend mirror pages. Console routes sit behind HTTP Basic auth; the postback
is checksum-verified and the mirror/redirect routes are token-scoped."""

import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import basket
from .config import settings
from .db import Ledger
from .kite import KiteClient, KiteError
from .service import TradeShareService
from .telegram import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()


def build_app(ledger: Ledger | None = None, kite: KiteClient | None = None,
              telegram: TelegramClient | None = None) -> FastAPI:
    ledger = ledger or Ledger(settings.db_path)
    kite = kite or KiteClient(settings.kite_api_key, settings.kite_api_secret,
                              access_token=ledger.kv_get("access_token"))
    telegram = telegram or TelegramClient(settings.telegram_bot_token)
    service = TradeShareService(ledger, kite, telegram, settings)

    app = FastAPI(title="KiteCast Trade Share")
    app.state.service = service

    def console_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        ok_user = secrets.compare_digest(credentials.username, settings.console_user)
        ok_pass = bool(settings.console_password) and secrets.compare_digest(
            credentials.password, settings.console_password)
        if not (ok_user and ok_pass):
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
        return credentials.username

    # ---- my side: Share console ----

    @app.get("/", response_class=HTMLResponse)
    def console(request: Request, _: str = Depends(console_auth)):
        return templates.TemplateResponse(request, "console.html", {
            "trades": ledger.trades(),
            "logged_in": kite.access_token is not None,
            "flash": request.query_params.get("flash"),
        })

    @app.post("/trade")
    def place_trade(_: str = Depends(console_auth),
                    tradingsymbol: str = Form(...), exchange: str = Form("MCX"),
                    side: str = Form(...), qty: int = Form(...),
                    product: str = Form("NRML"), order_type: str = Form("MARKET"),
                    price: float | None = Form(None)):
        if side not in ("BUY", "SELL") or qty <= 0:
            raise HTTPException(400, "Invalid ticket")
        try:
            trade_id = service.place_and_share(
                tradingsymbol=tradingsymbol.strip().upper(), exchange=exchange,
                side=side, qty=qty, product=product, order_type=order_type,
                price=price if order_type == "LIMIT" else None,
            )
        except KiteError as e:
            return RedirectResponse(f"/?flash=Order failed: {e}", status_code=303)
        return RedirectResponse(f"/?flash=Placed & sharing (trade #{trade_id})", status_code=303)

    @app.post("/trade/{trade_id}/close")
    def close_trade(trade_id: int, _: str = Depends(console_auth)):
        try:
            service.close_and_share(trade_id)
        except (KiteError, ValueError) as e:
            return RedirectResponse(f"/?flash=Close failed: {e}", status_code=303)
        return RedirectResponse(f"/?flash=Closing & sharing (trade #{trade_id})", status_code=303)

    # ---- my side: confirmation dashboard ----

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request, _: str = Depends(console_auth)):
        friends = ledger.friends()
        rows = []
        for trade in ledger.trades():
            shares = {(s["friend_id"], s["leg"]): s for s in ledger.shares_for_trade(trade["id"])}
            rows.append({"trade": trade, "shares": shares})
        return templates.TemplateResponse(request, "board.html", {"rows": rows, "friends": friends})

    @app.post("/share/{share_id}/nudge")
    def nudge(share_id: int, _: str = Depends(console_auth)):
        service.nudge(share_id)
        return RedirectResponse("/board", status_code=303)

    # ---- my side: friends admin ----

    @app.get("/friends", response_class=HTMLResponse)
    def friends_page(request: Request, _: str = Depends(console_auth)):
        return templates.TemplateResponse(request, "friends.html", {"friends": ledger.friends()})

    @app.post("/friends")
    def add_friend(_: str = Depends(console_auth), name: str = Form(...),
                   telegram_chat_id: str = Form(...), multiplier: float = Form(1.0)):
        ledger.add_friend(name.strip(), telegram_chat_id.strip(), multiplier)
        return RedirectResponse("/friends", status_code=303)

    @app.post("/friends/{friend_id}")
    def update_friend(friend_id: int, _: str = Depends(console_auth),
                      multiplier: float = Form(1.0), active: str = Form("off")):
        ledger.update_friend(friend_id, multiplier=multiplier, active=(active == "on"))
        return RedirectResponse("/friends", status_code=303)

    # ---- daily Kite login ----

    @app.get("/auth/login")
    def auth_login(_: str = Depends(console_auth)):
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

    # ---- friend side: one-tap mirror page ----

    @app.get("/m/{token}", response_class=HTMLResponse)
    def mirror(token: str, request: Request):
        share = ledger.share_by_token(token)
        if share is None:
            raise HTTPException(404, "Unknown or expired mirror link")
        trade = ledger.trade(share["trade_id"])
        order = (basket.entry_order(trade, share["qty"]) if share["leg"] == "ENTRY"
                 else basket.exit_order(trade, share["qty"]))
        return templates.TemplateResponse(request, "mirror.html", {
            "share": share, "trade": trade, "order": order,
            "basket_url": basket.BASKET_URL,
            "fields": basket.basket_form_fields(settings.kite_api_key, order, token),
            "already_confirmed": share["status"] == "CONFIRMED",
        })

    return app


def create_app() -> FastAPI:
    return build_app()
