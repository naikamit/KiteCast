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
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "kitecast.db"))
    # Open decision 10 — share timing: "fill" (default) or "placement".
    share_timing_entry: str = field(default_factory=lambda: _env("SHARE_TIMING_ENTRY", "fill"))
    share_timing_exit: str = field(default_factory=lambda: _env("SHARE_TIMING_EXIT", "fill"))
    kite_autoslice: bool = field(default_factory=lambda: _env("KITE_AUTOSLICE", "true").lower() == "true")

    @property
    def postback_url(self) -> str:
        return f"{self.base_url}/kite/postback"

    @property
    def redirect_url(self) -> str:
        return f"{self.base_url}/kite/redirect"


settings = Settings()
