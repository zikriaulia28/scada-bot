#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Telegram command & message handlers.
"""

import traceback

import requests

from scada_bot.config import TELEGRAM_BOT_TOKEN, MAX_TIME, logger
from scada_bot.session import (
    get_session, create_session, delete_session, cleanup_timeout_sessions,
    calculate_progress, build_status_text,
    _waiting_confirm, _pending_time,
)
from scada_bot.sheets import merge_ocr, write_to_sheet
from scada_bot.session import build_summary_text
from scada_bot.ocr import run_ocr


# =============================================================
# Telegram helper
# =============================================================
def tg_send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if not r.ok:
            logger.warning(f"Telegram send failed: {r.text}")
    except Exception as e:
        logger.warning(f"Telegram exception: {e}")


# =============================================================
# COMMAND HANDLER (Task 2)
# =============================================================
def handle_command(chat_id: str, command: str, text: str):
    """Route command /mulai, /status, /selesai, /batal."""
    session = get_session(chat_id)

    # /mulai
    if command == "/mulai":
        if session:
            tg_send_message(chat_id,
                f"⚠️ Session Time {session['time']} masih aktif.\n"
                "Gunakan /batal dulu untuk membuat session baru."
            )
            return

        tg_send_message(chat_id, "⏰ Silakan pilih *Time* (1–24) untuk session ini.")
        _pending_time[chat_id] = True
        logger.info(f"Menunggu input time dari chat={chat_id}")
        return

    # /status
    if command == "/status":
        if not session:
            tg_send_message(chat_id, "📭 Belum ada session aktif.\nGunakan /mulai untuk memulai.")
            return
        tg_send_message(chat_id, build_status_text(session))
        return

    # /batal
    if command == "/batal":
        if not session:
            tg_send_message(chat_id, "📭 Tidak ada session aktif.")
            return
        time_num = session["time"]
        photo_count = session["photo_count"]
        delete_session(chat_id)
        _pending_time.pop(chat_id, None)
        _waiting_confirm.pop(chat_id, None)
        tg_send_message(chat_id,
            f"🗑️ Session Time {time_num} dibatalkan.\n"
            f"({photo_count} foto diproses, dibuang.)"
        )
        logger.info(f"Session dibatalkan: chat={chat_id}")
        return

    # Konfirmasi Ya/Tidak HARUS sebelum /selesai
    if chat_id in _waiting_confirm:
        if text.lower() in ("ya", "y", "yes", "iya", "yoi"):
            _waiting_confirm.pop(chat_id, None)
            session = get_session(chat_id)
            if session:
                _do_write(chat_id, session)
        elif text.lower() in ("tidak", "no", "n", "nope", "engga"):
            _waiting_confirm.pop(chat_id, None)
            tg_send_message(chat_id, "❌ Penyimpanan dibatalkan.\nGunakan /mulai untuk session baru.")
        else:
            tg_send_message(chat_id, "Ketik *Ya* atau *Tidak*.")
        return

    # /selesai
    if command == "/selesai":
        if not session:
            tg_send_message(chat_id, "📭 Belum ada session aktif.\nGunakan /mulai untuk memulai.")
            return

        filled, total, missing = calculate_progress(session)
        if filled < total:
            _waiting_confirm[chat_id] = True
            missing_text = ", ".join(missing[:5])
            if len(missing) > 5:
                missing_text += f" ... (+{len(missing)-5} lagi)"
            tg_send_message(chat_id,
                f"⚠️ Ada *{total - filled}* parameter kosong:\n"
                f"`{missing_text}`\n\n"
                f"Tetap simpan? Ketik *Ya* untuk menyimpan, *Tidak* untuk membatalkan."
            )
            logger.info(f"Menunggu konfirmasi simpan: chat={chat_id}, kosong={total-filled}")
        else:
            _do_write(chat_id, session)
        return

    # Input waktu (setelah /mulai)
    if chat_id in _pending_time:
        try:
            time_num = int(text)
        except ValueError:
            tg_send_message(chat_id, "❌ Input tidak valid. Masukkan angka 1–24.")
            return
        if time_num < 1 or time_num > MAX_TIME:
            tg_send_message(chat_id, f"❌ Angka harus antara 1–{MAX_TIME}.")
            return
        _pending_time.pop(chat_id, None)
        session = create_session(chat_id, time_num)
        tg_send_message(chat_id,
            f"✅ Session Time {time_num} dibuat.\n"
            f"Silakan kirim foto sebanyak yang diperlukan.\n"
            f"Ketik /status untuk melihat progress.\n"
            f"Ketik /selesai jika sudah selesai.\n"
            f"Ketik /batal untuk membatalkan."
        )
        return

    # Fallback
    tg_send_message(chat_id,
        "📸 Kirim *foto HMI SCADA* untuk memulai.\n"
        "Gunakan /mulai untuk membuat session."
    )


# =============================================================
# Internal: Execute sheet write
# =============================================================
def _do_write(chat_id: str, session: dict):
    """Panggil setelah /selesai + konfirmasi."""
    try:
        write_to_sheet(session)
        changes = session.get("changes_log", [])
        tg_send_message(chat_id, build_summary_text(session, changes))
        logger.info(f"Session selesai: chat={chat_id}, time={session['time']}")
    except Exception as e:
        logger.error(f"Gagal menulis sheet: {e}")
        tg_send_message(chat_id, f"⚠️ *Error saat menyimpan:*\n`{str(e)[:200]}`")
    finally:
        delete_session(chat_id)
        _waiting_confirm.pop(chat_id, None)


# =============================================================
# Main: Handle incoming Telegram message
# =============================================================
def handle_message(msg: dict):
    chat_id = str(msg["chat"]["id"])
    user = msg.get("from", {}).get("first_name", "User")
    text = msg.get("text", "").strip()
    logger.info(f"Pesan dari {user} ({chat_id}): '{text[:50]}'")

    # Cleanup timeout sessions first
    cleanup_timeout_sessions()

    # Command
    if text.startswith("/"):
        handle_command(chat_id, text.split()[0], text[len(text.split()[0]):].strip())
        return

    # Konfirmasi Ya/Tidak
    if chat_id in _waiting_confirm:
        handle_command(chat_id, "/selesai", text)
        return

    # Input time after /mulai
    if chat_id in _pending_time:
        handle_command(chat_id, "", text)
        return

    # Photo
    if "photo" in msg:
        session = get_session(chat_id)
        if not session:
            tg_send_message(chat_id,
                "📭 Belum ada session aktif.\n"
                "Ketik /mulai untuk membuat session baru."
            )
            return

        biggest = msg["photo"][-1]
        file_id = biggest["file_id"]
        logger.info(f"Menerima foto ({file_id[:20]}...)")

        try:
            # Download
            file_resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
                params={"file_id": file_id}, timeout=15,
            )
            file_resp.raise_for_status()
            file_path = file_resp.json()["result"]["file_path"]
            dl = requests.get(
                f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}",
                timeout=30,
            )
            dl.raise_for_status()
            img_bytes = dl.content
            logger.info(f"Diunduh {len(img_bytes):,} bytes")

            # OCR
            ocr_data = run_ocr(img_bytes)

            # Merge
            changes = merge_ocr(session, ocr_data)
            session["photo_count"] += 1

            # Progress update
            filled, total, missing = calculate_progress(session)
            pct = int(filled / total * 100)
            status_preview = f"📸 Foto #{session['photo_count']} diproses.\n📈 Progress: *{filled}/{total}* ({pct}%)"
            if missing:
                status_preview += f"\n📌 Sisa: {', '.join(missing[:3])}"
                if len(missing) > 3:
                    status_preview += f" (+{len(missing)-3})"
            tg_send_message(chat_id, status_preview)
            logger.info(f"Progress: {filled}/{total}, foto: {session['photo_count']}")

        except Exception as e:
            logger.error(f"Error proses foto: {e}\n{traceback.format_exc()}")
            tg_send_message(chat_id, f"⚠️ *Error memproses foto:*\n`{str(e)[:200]}`")
        return

    # Plain text without session
    if text and not text.startswith("/"):
        tg_send_message(chat_id,
            "📸 Ketik /mulai untuk membuat session.\n"
            "📊 Ketik /status untuk melihat progress.\n"
            "✅ Ketik /selesai untuk menyimpan ke Sheet."
        )
        return