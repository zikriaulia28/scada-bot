#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Entry point.
"""

import time

from scada_bot.config import (
    TELEGRAM_BOT_TOKEN, OPERATOR_CHAT_ID,
    _GEMINI_MODEL, SHEET_NAME, SESSION_TIMEOUT_MINUTES,
    logger,
)
from scada_bot.handlers import handle_message, tg_send_message


def main():
    logger.info("=" * 55)
    logger.info("  SCADA Bot PLN DURI MS — Multi Image Session OCR")
    logger.info(f"  Model: {_GEMINI_MODEL}  |  Sheet: {SHEET_NAME}")
    logger.info(f"  Timeout: {SESSION_TIMEOUT_MINUTES} menit")
    logger.info("=" * 55)
    logger.info("Perintah: /mulai /status /selesai /batal")

    # Startup notification to operator (if Chat ID is set)
    if OPERATOR_CHAT_ID:
        import time as _time
        _time.sleep(2)  # Wait for polling to start
        tg_send_message(OPERATOR_CHAT_ID, "🤖 *SCADA Bot PLN DURI MS online!*")

    last_update_id = 0

    while True:
        try:
            upd_resp = __import__("requests").get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 30},
                timeout=35,
            )
            upd_resp.raise_for_status()
            updates = upd_resp.json().get("result", [])

            if not updates:
                time.sleep(1)
                continue

            for upd in updates:
                last_update_id = max(last_update_id, upd["update_id"])
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                handle_message(msg)

        except Exception as exc:
            logger.error(f"Error: {exc}\n{__import__('traceback').format_exc()}")
            if OPERATOR_CHAT_ID:
                tg_send_message(OPERATOR_CHAT_ID, f"⚠️ *Error bot:*\n`{str(exc)[:200]}`")
            time.sleep(5)
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()