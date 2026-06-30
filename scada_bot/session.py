#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Session state management (Task 1, 4, 5, 9).
"""

import time
from typing import Optional

from scada_bot.config import (
    FIELD_MAP, TOTAL_PARAMS, SESSION_TIMEOUT_MINUTES, MAX_TIME, logger,
)

# =============================================================
# SESSION STATE (Task 1)
# =============================================================
# Format: { chat_id: { time, ocr, photo_count, created_at, changes_log } }
_sessions: dict[str, dict] = {}


def get_session(chat_id: str) -> Optional[dict]:
    return _sessions.get(chat_id)


def create_session(chat_id: str, time_num: int) -> dict:
    """Buat session baru. Jika sudah ada, hapus dulu."""
    _sessions.pop(chat_id, None)
    session = {
        "time":        time_num,
        "ocr":         {},      # merged OCR data
        "photo_count": 0,
        "created_at":  time.time(),
        "changes_log": [],      # Task 8: log perubahan nilai
    }
    _sessions[chat_id] = session
    logger.info(f"Session dibuat: chat={chat_id}, time={time_num}")
    return session


def delete_session(chat_id: str) -> bool:
    """Hapus session. Return True jika session ditemukan."""
    if chat_id in _sessions:
        del _sessions[chat_id]
        logger.info(f"Session dihapus: chat={chat_id}")
        return True
    return False


def cleanup_timeout_sessions():
    """Hapus session yang sudah lebih dari SESSION_TIMEOUT_MINUTES (Task 9)."""
    now = time.time()
    cutoff = now - (SESSION_TIMEOUT_MINUTES * 60)
    expired = [
        cid for cid, s in _sessions.items()
        if s.get("created_at", 0) < cutoff
    ]
    for cid in expired:
        _sessions.pop(cid, None)
        logger.warning(f"Session timeout: chat={cid}")


# =============================================================
# PROGRESS CALCULATOR (Task 4)
# =============================================================
def calculate_progress(session: dict) -> tuple[int, int, list[str]]:
    """
    Return (filled, total, missing_keys).
    Tidak hardcode angka — dihitung dari FIELD_MAP.
    """
    ocr = session.get("ocr", {})
    filled_keys = [key for key, _ in FIELD_MAP if ocr.get(key) not in (None, "", "null")]
    missing_keys = [label for key, label in FIELD_MAP if ocr.get(key) in (None, "", "null")]
    return len(filled_keys), TOTAL_PARAMS, missing_keys


# =============================================================
# STATUS BUILDER (Task 5)
# =============================================================
def build_status_text(session: dict) -> str:
    """Bangkitkan teks status dari session (Task 5 — FIELD_MAP loop)."""
    filled, total, missing = calculate_progress(session)
    pct = int(filled / total * 100) if total > 0 else 0

    lines = [
        f"📊 *Status Session*\n",
        f"⏰ Time  : `{session['time']}`",
        f"📸 Foto  : {session['photo_count']} diterima",
        f"📈 Progress: *{filled}/{total}* ({pct}%)",
        f"```",
    ]

    for key, label in FIELD_MAP:
        val = session["ocr"].get(key)
        if val not in (None, "", "null"):
            lines.append(f"  {label}: {val} ✔")
        else:
            lines.append(f"  {label}: --")

    lines.append("```")
    return "\n".join(lines)


# =============================================================
# SUMMARY BUILDER (Task 5)
# =============================================================
def build_summary_text(session: dict, changes: list[str]) -> str:
    """Bangkitkan teks ringkasan setelah /selesai."""
    from scada_bot.config import TIME_BASE_ROW

    filled, total, _ = calculate_progress(session)
    pct = int(filled / total * 100) if total > 0 else 0
    lines = [
        f"✅ *Data Time {session['time']} disimpan!*\n",
        f"📍 Baris: `{TIME_BASE_ROW + session['time']}`",
        f"📸 Foto diproses: `{session['photo_count']}`",
        f"📈 Terisi: *{filled}/{total}* ({pct}%)\n",
    ]
    if changes:
        lines.append("*📝 Nilai berubah:*")
        for c in changes[:10]:   # max 10 perubahan
            lines.append(f"  {c}")
        lines.append("")
    return "\n".join(lines)


# =============================================================
# WAITING STATE (untuk /selesai → konfirmasi)
# =============================================================
_waiting_confirm: dict[str, bool] = {}   # chat_id → True (menunggu Ya)
_pending_time: dict[str, bool] = {}     # chat_id → True (menunggu input time)
