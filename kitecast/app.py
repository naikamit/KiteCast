"""FastAPI app: Share console, per-friend board, Kite postback + redirect,
friend mirror pages. The postback is checksum-verified and the
mirror/redirect routes are token-scoped. The console itself is
unauthenticated — keep the URL private."""

import logging
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse,
                               RedirectResponse)
from fastapi.templating import Jinja2Templates

from . import basket
from .config import settings
from .db import Ledger
from .ebook import (Library, blocks_to_text, code_matches, image_count, media_type, new_code,
                    parse_text_with_images, parse_upload, prepare, split_free, word_count)
from .instruments import InstrumentStore, days_to_expiry, moneyness
from .kite import KiteClient, KiteError
from .service import TradeShareService
from .telegram import TelegramClient
from .vision import VisionExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ---- the books-only public domain (BOOKS_HOST) ----
#
# One app, two faces. On the books domain the library lives at the root and
# its admin at /admin, and every trading route is simply not there. On any
# other hostname (the .onrender.com one) nothing changes: the console is at
# / and the library keeps its long /millsandgoons paths.

_BOOKS_PASSTHROUGH = ("/favicon.ico", "/apple-touch-icon.png", "/healthz")


def books_hosts() -> set[str]:
    """Hostnames that serve books only — each configured host plus its www."""
    hosts: set[str] = set()
    for raw in (settings.books_host or "").split(","):
        host = raw.strip().lower().lstrip("*.")
        if not host:
            continue
        hosts.add(host)
        if not host.startswith("www."):
            hosts.add("www." + host)
    return hosts


def books_path(path: str) -> str | None:
    """Map a path on the books domain to the route that serves it, or None
    if that path doesn't exist there (everything Kite)."""
    if path == "/":
        return "/millsandgoons"
    if path == "/admin":
        return "/millsandgoonsadmin"
    if path.startswith("/admin/"):
        return "/millsandgoonsadmin" + path[len("/admin"):]
    if path == "/b" or path.startswith("/b/"):
        return "/millsandgoons" + path
    if path in _BOOKS_PASSTHROUGH or path.startswith("/millsandgoons"):
        return path          # the long URLs keep working if one gets shared
    return None


def _on_books(request: Request) -> bool:
    return bool(getattr(request.state, "on_books", False))


def reader_base(request: Request) -> str:
    return "" if _on_books(request) else "/millsandgoons"


def admin_base(request: Request) -> str:
    return "/admin" if _on_books(request) else "/millsandgoonsadmin"


def build_app(ledger: Ledger | None = None, kite: KiteClient | None = None,
              telegram: TelegramClient | None = None,
              vision: VisionExtractor | None = None) -> FastAPI:
    ledger = ledger or Ledger(settings.db_path)
    kite = kite or KiteClient(settings.kite_api_key, settings.kite_api_secret,
                              access_token=ledger.kv_get("access_token"),
                              order_proxy=settings.kite_order_proxy)
    telegram = telegram or TelegramClient(settings.telegram_bot_token)
    vision = vision or VisionExtractor(settings.anthropic_api_key, settings.vision_model,
                                       workspace_id=settings.anthropic_workspace_id)
    dump_cache = None
    if settings.db_path and settings.db_path != ":memory:":
        dump_cache = str(Path(settings.db_path).resolve().with_name("instruments_cache.csv"))
    store = InstrumentStore(kite, cache_path=dump_cache)
    service = TradeShareService(ledger, kite, telegram, settings, store=store)

    app = FastAPI(title="KiteCast Trade Share")
    app.state.service = service

    @app.middleware("http")
    async def books_domain(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        path = request.url.path
        on_books = host in books_hosts()
        request.state.on_books = on_books
        if on_books:
            mapped = books_path(path)
            if mapped is None:          # the trading side doesn't exist here
                return PlainTextResponse("Not found", status_code=404)
            request.scope["path"] = mapped
            request.scope["raw_path"] = mapped.encode()
        elif path in ("/admin", "/b") or path.startswith(("/admin/", "/b/")):
            # the short paths belong to the books domain only
            return PlainTextResponse("Not found", status_code=404)
        return await call_next(request)

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

    # ---- my side: friends admin ----

    @app.get("/friends", response_class=HTMLResponse)
    def friends_page(request: Request):
        return templates.TemplateResponse(request, "friends.html", {"friends": ledger.friends()})

    @app.post("/friends")
    def add_friend(name: str = Form(...), multiplier: float = Form(1.0)):
        ledger.add_friend(name.strip(), "", multiplier)
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
        """Add dte and, for options, strike distance from ATM plus moneyness.
        Falls back to the daily dump's previous close when live quotes fail,
        so ATM context survives an expired session."""
        out = {**inst, "dte": days_to_expiry(inst["expiry"]), "atm_pct": None, "moneyness": None}
        if inst["strike"] and inst["instrument_type"] in ("CE", "PE"):
            fut = store.underlying_future(inst["exchange"], inst["name"])
            u_ltp = fut and (underlying_ltps.get(f"{fut['exchange']}:{fut['tradingsymbol']}")
                             or fut.get("last_price"))
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
        u_ltp = next(iter(ltps.values()), None)
        source = "live" if u_ltp else None
        if not u_ltp and fut and fut.get("last_price"):
            u_ltp, source = fut["last_price"], "prev close"
        data["underlying_ltp"] = u_ltp
        data["underlying_ltp_source"] = source
        return data

    @app.get("/api/atm")
    def atm_by_oi(exchange: str, name: str, expiry: str):
        """ATM as the mode of the OI distribution: quote every strike of the
        expiry (CE+PE, chunked batch calls) and return the strike carrying
        the most open interest."""
        try:
            rows = [o for o in store.chain(exchange, name)["options"] if o["expiry"] == expiry]
        except KiteError as e:
            raise HTTPException(409, str(e))
        if not rows:
            raise HTTPException(404, "No options for that expiry")
        oi_by_strike: dict[float, int] = {}
        try:
            for start in range(0, len(rows), 400):
                chunk = rows[start:start + 400]
                data = kite.quote(*(f"{exchange}:{o['tradingsymbol']}" for o in chunk))
                for o in chunk:
                    oi = (data.get(f"{exchange}:{o['tradingsymbol']}") or {}).get("oi") or 0
                    oi_by_strike[o["strike"]] = oi_by_strike.get(o["strike"], 0) + oi
        except KiteError as e:
            raise HTTPException(409, str(e))
        if not any(oi_by_strike.values()):
            raise HTTPException(404, "No open interest data")
        atm = max(oi_by_strike, key=oi_by_strike.get)
        return {"atm_strike": atm, "oi_at_atm": oi_by_strike[atm]}

    @app.get("/api/strike_quotes")
    def strike_quotes_api(i: str):
        """Batch premium + OI for the strike dropdown — one full-quote call
        prices the whole visible window (Kite allows ~500 keys per call)."""
        keys = [k.strip() for k in i.split(",") if k.strip()][:60]
        if not keys:
            raise HTTPException(400, "No instruments")
        try:
            data = kite.quote(*keys)
        except KiteError as e:
            raise HTTPException(409, str(e))
        return {k: {"ltp": (v or {}).get("last_price"), "oi": (v or {}).get("oi")}
                for k, v in data.items()}

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

    # ---- screenshot -> share link (the login-free path) ----

    @app.get("/screenshot", response_class=HTMLResponse)
    def screenshot_page(request: Request):
        return templates.TemplateResponse(request, "screenshot.html", {
            "vision_ready": vision.configured,
            "key_set": bool(vision.api_key),
            "ws_set": bool(vision.workspace_id),
        })

    @app.post("/api/screenshot_parse")
    async def screenshot_parse(file: UploadFile = File(...)):
        if not vision.configured:
            raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
        if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise HTTPException(400, "Upload a PNG or JPEG screenshot")
        data = await file.read()
        if len(data) > 4_500_000:
            raise HTTPException(400, "Image too large (max ~4.5 MB)")
        try:
            parsed = vision.extract(data, file.content_type)
        except Exception as e:
            raise HTTPException(502, f"Couldn't read the screenshot: {e}")
        try:
            inst = store.resolve_screenshot(parsed)
        except KiteError as e:
            raise HTTPException(409, f"Instrument master unavailable: {e}")
        if inst is None:
            raise HTTPException(
                422, f"Read '{parsed.get('underlying')}' but couldn't match it to a "
                     "tradable contract — check the screenshot shows the order window")
        lots = int(parsed.get("qty_lots") or 1)
        # MCX quantity is lots; other segments take units in lot multiples.
        qty = lots if inst["exchange"] == "MCX" else lots * (inst["lot_size"] or 1)
        side = (parsed.get("side") or "BUY").upper()
        # Screenshot prices go stale between capture and the friend's tap —
        # prefill an aggressive limit: screen price ±bump% in the
        # fill-guaranteeing direction (BUY up, SELL down), tick-rounded.
        screen_price = parsed.get("limit_price") or parsed.get("ltp")
        price = None
        if screen_price:
            price = basket.protected_limit(side, float(screen_price),
                                           settings.screenshot_price_bump_pct,
                                           inst["tick_size"] or 0.05)
        return {
            "exchange": inst["exchange"], "tradingsymbol": inst["tradingsymbol"],
            "lot_size": inst["lot_size"], "expiry": inst["expiry"],
            "dte": days_to_expiry(inst["expiry"]),
            "instrument_type": inst["instrument_type"], "strike": inst["strike"],
            "side": side,
            "qty": qty, "lots": lots,
            "price": price, "screen_price": screen_price,
            "bump_pct": settings.screenshot_price_bump_pct,
            "ltp": parsed.get("ltp"),
        }

    @app.post("/api/screenshot_share")
    def screenshot_share(exchange: str = Form(...), tradingsymbol: str = Form(...),
                         side: str = Form(...), qty: int = Form(...),
                         price: float = Form(...)):
        """Mint the share-only links for a reviewed screenshot order. Always a
        LIMIT (so friend taps never need my Kite session for price anchoring)."""
        tradingsymbol = tradingsymbol.strip().upper()
        if side not in ("BUY", "SELL") or qty <= 0 or price <= 0:
            raise HTTPException(400, "Invalid order")
        try:
            if store.get(exchange, tradingsymbol) is None:
                raise HTTPException(404, "Unknown contract")
        except KiteError:
            pass  # master temporarily unavailable; the parse step already validated
        trade_id = service.share_only(
            tradingsymbol=tradingsymbol, exchange=exchange, side=side, qty=qty,
            product="NRML", order_type="LIMIT", price=price,
        )
        trade = ledger.trade(trade_id)
        return {
            "trade_id": trade_id,
            "entry_url": f"{settings.base_url}/t/{trade['public_entry_token']}",
            "exit_url": f"{settings.base_url}/t/{trade['public_exit_token']}",
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
        try:
            order = service.build_mirror_order(share, trade)
        except KiteError as e:
            service.alert_owner(f"Friend mirror for {trade['tradingsymbol']} couldn't be priced "
                                f"— check the daily Kite login. ({e})")
            return templates.TemplateResponse(request, "mirror_unavailable.html", {})
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
        try:
            order = service.build_public_order(trade, leg)
        except KiteError as e:
            service.alert_owner(f"Public mirror for {trade['tradingsymbol']} couldn't be priced "
                                f"— check the daily Kite login. ({e})")
            return templates.TemplateResponse(request, "mirror_unavailable.html", {})
        return templates.TemplateResponse(request, "mirror.html", {
            "leg": leg, "order": order,
            "price": service.price_context(trade, order),
            "basket_url": basket.BASKET_URL,
            "fields": basket.basket_form_fields(settings.kite_api_key, order, token,
                                                token_param="public_token"),
            "already_confirmed": False,
        })

    # ---- ebook: Kindle-style reader over a small library + upload admin ----

    library_root = None
    if settings.db_path and settings.db_path != ":memory:":
        library_root = str(Path(settings.db_path).resolve().with_name("ebooks"))
    library = Library(library_root)
    if library_root:
        library.migrate_single_book(
            str(Path(settings.db_path).resolve().with_name("ebook_millsandgoons.json")))

    def _unlocked(request: Request, book: dict) -> bool:
        """A paying reader carries the book's code in a cookie."""
        return code_matches(request.cookies.get(f"mg_unlock_{book.get('slug', '')}", ""),
                            book.get("code", ""))

    @app.get("/millsandgoons", response_class=HTMLResponse)
    def ebook_library(request: Request):
        books = library.books()
        for b in books:
            b["locked"] = b["gated"] and not _unlocked(request, b)
        return templates.TemplateResponse(request, "ebook_library.html", {
            "books": books, "rb": reader_base(request), "ab": admin_base(request)})

    @app.get("/millsandgoons/b/{slug}", response_class=HTMLResponse)
    def ebook_reader(request: Request, slug: str):
        book = library.get(slug)
        if not book:
            raise HTTPException(404, "No such book")
        # The locked chapters never reach the browser — hiding them in the
        # DOM would put the whole book one View Source away.
        unlocked = _unlocked(request, book)
        free, locked_titles = split_free(book.get("blocks", []),
                                         0 if unlocked else book.get("free_chapters", 0))
        blocks, chapters = prepare(free)
        return templates.TemplateResponse(request, "ebook_reader.html", {
            "slug": slug, "title": book.get("title", ""),
            "author": book.get("author", ""),
            "blocks": blocks, "chapters": chapters,
            "locked_titles": locked_titles, "rb": reader_base(request),
            "terms": library.unlock_terms(book) if locked_titles else "",
            "telegram": library.settings()["telegram"],
            "bad_code": request.query_params.get("bad") == "1",
        })

    @app.post("/millsandgoons/b/{slug}/unlock")
    def ebook_unlock(request: Request, slug: str, code: str = Form("")):
        book = library.get(slug)
        if not book:
            raise HTTPException(404, "No such book")
        here = f"{reader_base(request)}/b/{slug}"
        if not code_matches(code, book.get("code", "")):
            return RedirectResponse(f"{here}?bad=1", status_code=303)
        resp = RedirectResponse(here, status_code=303)
        resp.set_cookie(f"mg_unlock_{slug}", book["code"], max_age=3 * 365 * 24 * 3600,
                        httponly=True, samesite="lax")
        return resp

    @app.get("/millsandgoons/b/{slug}/media/{name}")
    def ebook_media(slug: str, name: str):
        path = library.media_path(slug, name)
        if path is None:
            raise HTTPException(404, "No such image")
        return FileResponse(path, media_type=media_type(name) or "application/octet-stream",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # -- admin

    async def _content_from_form(upload: UploadFile | None, text: str,
                                 book: dict | None = None):
        """(blocks, media) from an upload, else from the textarea. Returns
        (None, None) when neither was supplied — a metadata-only edit."""
        if upload is not None and upload.filename:
            data = await upload.read()
            if not data:
                raise HTTPException(400, "Uploaded file is empty")
            try:
                return parse_upload(upload.filename, data)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if text.strip():
            known = {b["x"] for b in (book or {}).get("blocks", []) if b.get("t") == "img"}
            return parse_text_with_images(text, known), None
        return None, None

    @app.get("/millsandgoonsadmin", response_class=HTMLResponse)
    def ebook_admin(request: Request):
        return templates.TemplateResponse(request, "ebook_admin.html", {
            "books": library.books(), "flash": request.query_params.get("flash"),
            "settings": library.settings(),
            "rb": reader_base(request), "ab": admin_base(request),
        })

    @app.post("/millsandgoonsadmin/settings")
    def ebook_admin_settings(request: Request, telegram: str = Form(""),
                             terms: str = Form("")):
        library.save_settings(telegram, terms)
        return RedirectResponse(f"{admin_base(request)}?flash=Payment+settings+saved.",
                                status_code=303)

    @app.post("/millsandgoonsadmin")
    async def ebook_admin_add(request: Request, title: str = Form(""), author: str = Form(""),
                              text: str = Form(""), free_chapters: int = Form(0),
                              terms: str = Form(""),
                              bookfile: UploadFile | None = File(None)):
        blocks, media = await _content_from_form(bookfile, text)
        if not blocks:
            raise HTTPException(400, "Give the book some text, or upload a .docx/.txt")
        if not title.strip():
            heading = next((b["x"] for b in blocks if b["t"] == "h"), "")
            title = heading or (bookfile.filename.rsplit(".", 1)[0]
                                if bookfile and bookfile.filename else "Untitled")
        slug = library.unique_slug(title)
        library.save(slug, title, author, blocks, media or {},
                     free_chapters=free_chapters, terms=terms)
        return RedirectResponse(
            f"{admin_base(request)}?flash=Added+%E2%80%9C{quote(title)}%E2%80%9D",
            status_code=303)

    @app.get("/millsandgoonsadmin/b/{slug}", response_class=HTMLResponse)
    def ebook_admin_edit(request: Request, slug: str):
        book = library.get(slug)
        if not book:
            raise HTTPException(404, "No such book")
        blocks = book.get("blocks", [])
        return templates.TemplateResponse(request, "ebook_admin_edit.html", {
            "book": book, "text": blocks_to_text(blocks),
            "words": word_count(blocks), "images": image_count(blocks),
            "chapters": [b["x"] for b in blocks if b.get("t") == "h"],
            "flash": request.query_params.get("flash"),
            "settings": library.settings(),
            "locked_titles": split_free(blocks, book.get("free_chapters", 0))[1],
            "rb": reader_base(request), "ab": admin_base(request),
        })

    @app.post("/millsandgoonsadmin/b/{slug}")
    async def ebook_admin_save(request: Request, slug: str, title: str = Form(""),
                               author: str = Form(""),
                               text: str = Form(""), free_chapters: int = Form(0),
                               terms: str = Form(""), regenerate: str = Form(""),
                               bookfile: UploadFile | None = File(None)):
        book = library.get(slug)
        if not book:
            raise HTTPException(404, "No such book")
        blocks, media = await _content_from_form(bookfile, text, book)
        library.save(slug, title or book["title"], author,
                     blocks if blocks is not None else book.get("blocks", []),
                     media, added=book.get("added"),
                     free_chapters=free_chapters, terms=terms,
                     code=new_code() if regenerate else None)
        return RedirectResponse(f"{admin_base(request)}/b/{slug}?flash=Saved.", status_code=303)

    @app.post("/millsandgoonsadmin/b/{slug}/delete")
    def ebook_admin_delete(request: Request, slug: str):
        library.delete(slug)
        return RedirectResponse(f"{admin_base(request)}?flash=Deleted.", status_code=303)

    return app


def _start_login_reminder(service) -> None:
    """Weekday-morning ops thread: at LOGIN_REMINDER_TIME (IST), probe the
    Kite session and ping my phone if the daily login is still pending."""
    if not (settings.telegram_bot_token and settings.owner_telegram_chat_id
            and settings.login_reminder_time):
        return

    from datetime import datetime

    from .instruments import IST

    def loop():
        reminded_on = None
        while True:
            now = datetime.now(IST)
            due = (now.strftime("%H:%M") >= settings.login_reminder_time
                   and now.weekday() < 5 and reminded_on != now.date())
            if due:
                reminded_on = now.date()
                try:
                    service.check_login_and_remind()
                except Exception:
                    logging.getLogger("kitecast").exception("login reminder failed")
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="login-reminder").start()


def create_app() -> FastAPI:
    app = build_app()
    _start_login_reminder(app.state.service)
    return app
