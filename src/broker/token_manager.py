"""
Groww token health — validate the access token before market open and alert on
expiry. Safe without credentials/SDK; never raises.
"""
from __future__ import annotations

import logging

from config.settings import settings

log = logging.getLogger(__name__)


class GrowwTokenManager:
    def check_token_health(self) -> dict:
        if not settings.has_groww_credentials:
            return {"healthy": False, "reason": "no_credentials",
                    "message": "Groww credentials not set (paper mode)."}
        try:
            from growwapi import GrowwAPI  # type: ignore

            client = GrowwAPI(settings.groww_access_token)
            # A cheap authenticated call validates the token.
            client.get_positions()
            return {"healthy": True, "reason": "ok", "message": "Groww token valid."}
        except Exception as exc:
            msg = f"Groww token invalid/expired: {exc}"
            log.debug(msg)
            try:
                from src.notifications.telegram_bot import TelegramNotifier

                TelegramNotifier().send_message(f"⚠️ {msg}\nRegenerate it before live trading.")
            except Exception:
                pass
            return {"healthy": False, "reason": "invalid", "message": msg}
