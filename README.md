# KiteCast · Trade Share

Mirror my trades to friends, **one tap**. I place my trades through the
**Share console** on my own Kite (Kite Connect api_key); friends mirror them
with one tap in their **own** Kite via Publisher basket links. Because every
shared trade originates inside the tool, the tool always knows exactly what I
did — no polling, no guessing intent. No fee, no credential custody.

## How it works (origination model)

```
me ──ticket──▶ Share console ──order──▶ my Kite (Kite Connect)
                     │                        │
                     │◀────fill postback──────┘
                     │
                     ├─ writes SQLite ledger (authoritative — I originated it)
                     ├─ pre-builds every friend's EXIT link (NFR-1)
                     └─ builds per-friend + public ENTRY mirror links
                                     │ I copy / WA them to friends' phones
                                     ▼ tap
                              friend's own Kite
                                                    │ confirm (their session)
                     board flips green ◀── Publisher redirect
```

- **The boundary:** only tool-originated trades are shared. Anything placed
  directly in the Kite app has an order_id the ledger has never seen — the
  postback is ignored and the trade stays personal.
- **The split (one api_key, ₹500/mo on my account only):** my side uses full
  Kite Connect (order placement + fill postbacks); the friend side uses
  Publisher basket links from the *same* api_key. Friends register nothing,
  pay nothing, and I never hold their tokens (NFR-3). Each friend confirms in
  their own session — manual confirm keeps it compliant and custody-free
  (NFR-4). Honest limit: I can't force-close their account; non-tappers don't
  act.
- **Blow-off-top exits (NFR-1):** the close mirror is pre-built the moment my
  entry fills, so Close & Share is push-only. My close order is at-any-cost:
  opposite side, full qty, MARKET, `autoslice` past the freeze limit. The
  friends' close basket is MARKET with no limit price, so their
  market-protection setting is what governs — tell friends to keep protection
  off/wide for these contracts.

## Screens

| Route | Who | What |
|---|---|---|
| `/` | me | Ticket with live contract search (Kite instrument master + full quote: LTP, OHLC, bid/ask, OI, circuits, lot/tick/expiry) → **Place & Share** / **Close & Share**, my fill status |
| `/board` | me | Per-friend board: entry/exit link live · confirmed (green/amber), copy/WA buttons per friend |
| `/friends` | me | The private circle (4 named friends), per-friend multipliers, on/off |
| `/screenshot` | me | Upload/paste a broker order screenshot → Claude parses it → validated against the instrument master → review → share-only LIMIT links with copy buttons. Needs `ANTHROPIC_API_KEY`; the whole path works **without the daily Kite login** (dump is disk-cached, LIMIT needs no live anchor) |
| `/m/{token}` | friend | Auto-submitting pre-filled Kite basket — one tap to confirm |
| `/kite/postback` | Kite | My order fills (checksum-verified) |
| `/kite/redirect` | Kite | My daily login **and** friends' basket confirmations |

## Setup

1. **Kite Connect app** (developers.kite.trade, ₹500/mo): set the redirect URL
   to `https://YOUR_HOST/kite/redirect` and the postback URL to
   `https://YOUR_HOST/kite/postback`.
2. **Telegram bot (owner ops only)**: create via @BotFather, send it `/start`
   yourself, and set `OWNER_TELEGRAM_CHAT_ID` to your chat id. It DMs *you*
   the weekday-morning login reminder (`LOGIN_REMINDER_TIME`, IST) and ops
   alerts. Friends never interact with the bot — delivery to friends is
   manual by design: copy their link or use the WA buttons.
3. **VPS** (NFR-2): always-on, **static IP**, **exclude Mullvad** / any VPN
   from this host's egress so postbacks and Publisher redirects always land.
4. **Static IP whitelist (mandatory for orders):** Zerodha validates *order*
   endpoints against **one** whitelisted IP, set at developers.kite.trade →
   Profile → IP Whitelist and changeable only **once per calendar week**.
   Market data, session, and postbacks are not IP-validated.
   - Host with its own static IP (a VPS): whitelist that IP, leave
     `KITE_ORDER_PROXY` empty.
   - Host without one fixed egress IP (Render, Railway, Heroku…): set
     `KITE_ORDER_PROXY=http://user:pass@host:port` to a proxy with a single
     static IP and whitelist the *proxy's* IP. Only the order-placement call
     goes through it; everything else stays direct.
4. Configure and run:

```bash
cp .env.example .env   # fill in keys, BASE_URL, Telegram token
pip install -r requirements.txt
set -a; source .env; set +a
python run.py          # or: uvicorn run:app --host 0.0.0.0 --port 8000
```

A systemd unit is provided in `deploy/kitecast.service`. Put TLS in front
(caddy/nginx) — Kite requires https redirect/postback URLs.

5. **Every trading day**: open `/` and tap **Log in to Kite** once (Kite
   Connect access tokens expire daily).

## Daily flow

1. Console → ticket → **Place & Share**. My order fires; on my fill the ledger
   is written, every friend's entry + exit mirror links go live, and the
   trade row shows 🔗 entry / 🔗 close public links.
2. I deliver the links myself — **🔗 copy** (clipboard) or **WA** (opens
   WhatsApp with the message pre-written) from the console or the per-friend
   board. Friend taps → pre-filled basket in their own Kite → confirms → the
   redirect flips them **green** on `/board`; stragglers stay **amber** (call
   them). Per-friend links carry their scaled qty and flip their own cell;
   the public `/t/` links work for anyone at base qty.
3. Exit: **Close & Share** on the open trade. My close fires at-any-cost; on
   my fill the matching close links go live — copy/WA them out.

Share timing is configurable (`SHARE_TIMING_ENTRY/EXIT`): `fill` (default)
fans mirrors when *my* order fills; `placement` fans the instant I place.

## Notes & honest limits

- **The console has no login.** Anyone who knows the URL can place orders on
  my account and see the friend ledger. Keep the hostname unguessable and
  never post it anywhere; for extra cover, put an IP allowlist or an access
  layer (e.g. Cloudflare Access) in front. `/kite/postback` stays safe
  regardless (checksum-verified) and mirror links are per-share tokens.

- Friends need MCX-enabled Zerodha accounts with margin, and a live Kite
  session (keep the app installed & logged in — the basket deep-links into it).
- If a friend hand-modifies size on their side, the ledger still closes them
  at the *ledger* qty (NFR-5) — the pending/confirmed board plus qty shown on
  each cell is the reconcile surface; we can't read their book (no creds, by
  design).
- Confirmation is based on the Publisher redirect `status=success`; a
  cancelled basket stays amber.
- **Commodity options:** the exchange rejects bare MARKET orders on MCX
  options, so a MARKET ticket on one is placed as a protected LIMIT at
  LTP ± `MARKET_PROTECTION_PCT_ENTRY` (default 5%) /
  `MARKET_PROTECTION_PCT_EXIT` (default 10%, wider because exits are
  at-any-cost), tick-rounded — the same thing the Kite app's market
  protection does. Friend mirrors re-anchor to live LTP at tap time
  (fallback: my entry fill price if my session has expired).
- **Share only:** the ticket's "Share only" checkbox fans mirror links to
  friends *without* placing my order — for trades I already hold, or ideas I
  want to share but not take myself. The ledger row (status `SHARED`) has no
  order/fill of mine; "Share close" later pushes the matching close mirrors,
  again without touching my account.
- **Public share links:** every trade row on the console has 🔗 entry / 🔗
  close copy buttons (`/t/<token>` URLs). One link works for *any* friend —
  no Telegram, no friend setup needed; I paste it wherever I like. Taps place
  the order at base qty in the tapper's own Kite; confirms bump a counter on
  the console (no per-friend attribution — use the per-friend links on
  `/board` for that).

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

`kitecast/` — `app.py` (routes) · `service.py` (Place/Close & Share
orchestration) · `db.py` (SQLite ledger) · `kite.py` (Kite Connect REST +
postback checksum) · `basket.py` (Publisher baskets, scaling) · `telegram.py`
(owner ops DMs only).
