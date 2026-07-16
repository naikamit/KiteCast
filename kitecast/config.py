"""Environment-driven settings. Everything secret stays server-side (NFR-3)."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    kite_api_key: str = field(default_factory=lambda: _env("KITE_API_KEY"))
    kite_api_secret: str = field(default_factory=lambda: _env("KITE_API_SECRET"))
    base_url: str = field(default_factory=lambda: _env("BASE_URL", "http://localhost:8000").rstrip("/"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    # My own chat with the bot — gets ops alerts (e.g. a friend's mirror
    # couldn't be priced because the daily Kite session lapsed).
    owner_telegram_chat_id: str = field(default_factory=lambda: _env("OWNER_TELEGRAM_CHAT_ID"))
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "kitecast.db"))
    # Open decision 10 — share timing: "fill" (default) or "placement".
    share_timing_entry: str = field(default_factory=lambda: _env("SHARE_TIMING_ENTRY", "fill"))
    share_timing_exit: str = field(default_factory=lambda: _env("SHARE_TIMING_EXIT", "fill"))
    kite_autoslice: bool = field(default_factory=lambda: _env("KITE_AUTOSLICE", "true").lower() == "true")
    # Static-IP proxy for order placement only (Zerodha whitelists one IP for
    # order endpoints). e.g. http://user:pass@proxy-host:port — leave empty
    # when the host itself has the whitelisted static IP.
    kite_order_proxy: str = field(default_factory=lambda: _env("KITE_ORDER_PROXY"))
    # MCX options reject bare MARKET orders; we emulate the Kite app's market
    # protection with a LIMIT at LTP ± this percent. Entries cap slippage
    # (tight); exits are at-any-cost per the PRD (wide). MARKET_PROTECTION_PCT
    # (no suffix) overrides the default for both.
    market_protection_pct_entry: float = field(default_factory=lambda: float(
        _env("MARKET_PROTECTION_PCT_ENTRY", _env("MARKET_PROTECTION_PCT", "5"))))
    market_protection_pct_exit: float = field(default_factory=lambda: float(
        _env("MARKET_PROTECTION_PCT_EXIT", _env("MARKET_PROTECTION_PCT", "10"))))

    @property
    def postback_url(self) -> str:
        return f"{self.base_url}/kite/postback"

    @property
    def redirect_url(self) -> str:
        return f"{self.base_url}/kite/redirect"


settings = Settings()
