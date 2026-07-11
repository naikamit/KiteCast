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
                     └─ fans per-friend ENTRY mirror links ──▶ Telegram push
                                                                   │ tap
                                              friend's own Kite ◀──┘
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
| `/board` | me | Per-friend board: entry/exit sent · confirmed (green/amber), one-tap nudge → call escalation |
| `/friends` | me | The private circle (4 named friends), per-friend multipliers, on/off |
| `/m/{token}` | friend | Auto-submitting pre-filled Kite basket — one tap to confirm |
| `/kite/postback` | Kite | My order fills (checksum-verified) |
| `/kite/redirect` | Kite | My daily login **and** friends' basket confirmations |

## Setup

1. **Kite Connect app** (developers.kite.trade, ₹500/mo): set the redirect URL
   to `https://YOUR_HOST/kite/redirect` and the postback URL to
   `https://YOUR_HOST/kite/postback`.
2. **Telegram bot**: create via @BotFather; each friend sends `/start` to the
   bot once, then store their chat id on `/friends`.
3. **VPS** (NFR-2): always-on, **static IP** (register it with the api_key if
   required), **exclude Mullvad** / any VPN from this host's egress so
   postbacks and Publisher redirects always land.
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
   is written and each friend gets a Telegram push with a **Mirror this
   trade** button (their exit link is pre-built at the same moment).
2. Friend taps → lands on the pre-filled basket in their own Kite → confirms.
   The redirect flips them **green** on `/board`; stragglers stay **amber**
   with a nudge button (re-ping first, the button then escalates to *call*).
   **Alternative to Telegram push:** every pending cell on `/board` also has a
   **🔗 copy** button (copies the friend's mirror URL —
   `BASE_URL/m/<token>` — paste it anywhere) and a **WA** button that opens
   WhatsApp with the message pre-written. Leave a friend's Telegram chat id
   blank to make them manual-share only. Mirror links are per-friend and
   per-leg — send each friend *their* link, since it carries their scaled qty
   and flips *their* cell green.
3. Exit: **Close & Share** on the open trade. My close fires at-any-cost; on
   my fill every friend gets the matching **Close now** push.

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

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest
```

`kitecast/` — `app.py` (routes) · `service.py` (Place/Close & Share
orchestration) · `db.py` (SQLite ledger) · `kite.py` (Kite Connect REST +
postback checksum) · `basket.py` (Publisher baskets, scaling) · `telegram.py`
(concurrent fan-out).
