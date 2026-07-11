"""Telegram delivery — hard phone push, never email (NFR-1).

Fan-out uses a thread pool so all friends' pushes go out concurrently;
one slow send must not delay the others during a blow-off top.
"""

from concurrent.futures import ThreadPoolExecutor

import httpx

_pool = ThreadPoolExecutor(max_workers=8)


class TelegramClient:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    def send(self, chat_id: str, text: str, button_text: str, url: str) -> bool:
        """Send one push with a single tap-to-mirror button. Friends without a
        chat id are manual-share (copy link / WhatsApp from the board)."""
        if not self.bot_token or not chat_id:
            return False
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": [[{"text": button_text, "url": url}]]},
                },
                timeout=5.0,
            )
            return resp.status_code == 200 and resp.json().get("ok", False)
        except httpx.HTTPError:
            return False

    def fan_out(self, messages: list[tuple[str, str, str, str]]) -> list[bool]:
        """Send (chat_id, text, button_text, url) pushes concurrently."""
        return list(_pool.map(lambda m: self.send(*m), messages))
