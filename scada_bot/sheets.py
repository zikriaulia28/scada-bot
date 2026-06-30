#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Google Sheets integration (Task 6).
Normalization, sheet row building, writing to Sheets API.
"""

import json
import time

import jwt
import requests

from scada_bot.config import (
    SCRIPT_DIR, SPREADSHEET_ID, SHEET_NAME, TIME_BASE_ROW, logger,
)

# =============================================================
# Normalisasi angka — koma → titik (pemisah desimal)
# =============================================================
def normalize_number(val: str) -> str:
    """Normalize numeric strings from Gemini to a format suitable for Google Sheets."""
    if not val or val == "":
        return val
    s = str(val).strip()
    if not any(c.isdigit() for c in s):
        return s
    s = s.replace(",", "")
    parts = s.split('.')
    if len(parts) == 1:
        return s
    if len(parts) == 2:
        int_part, frac = parts
        if len(frac) <= 2:
            return s
        else:
            return int_part + frac
    if len(parts) == 3 and len(parts[0]) == 1 and len(parts[-1]) == 3:
        integer = parts[0] + parts[1]
        return f"{integer}.{parts[2]}"
    if len(parts[-1]) <= 2:
        integer = ''.join(parts[:-1])
        return f"{integer}.{parts[-1]}"
    return ''.join(parts)


# =============================================================
# Build Sheet Row (kolom B–V, 21 nilai)
# =============================================================
def build_sheet_row(ocr: dict) -> list:
    """
    Urutan kolom B-V (21 kolom, 1-to-1 dengan Gemini prompt).
    """
    return [
        normalize_number(ocr.get("pit_100", "")),            # B
        normalize_number(ocr.get("pit_101", "")),            # C
        normalize_number(ocr.get("tit_100", "")),            # D
        normalize_number(ocr.get("pit_1001a", "")),          # E
        normalize_number(ocr.get("tit_1001a", "")),          # F
        normalize_number(ocr.get("fit_1001a", "")),          # G
        normalize_number(ocr.get("pit_1001b", "")),          # H
        normalize_number(ocr.get("tit_1001b", "")),          # I
        normalize_number(ocr.get("fit_1001b", "")),          # J
        normalize_number(ocr.get("pit_106", "")),            # K
        normalize_number(ocr.get("tit_103", "")),            # L
        normalize_number(ocr.get("pit_103", "")),            # M
        normalize_number(ocr.get("pcv_a_auto_loop_mv", "")), # N
        normalize_number(ocr.get("pcv_b_auto_loop_mv", "")), # O
        normalize_number(ocr.get("pit_104_pv", "")),         # P
        ocr.get("gc_a_actual_btu", ""),    # Q
        ocr.get("gc_b_actual_btu", ""),    # R
        ocr.get("h2o_analyzer_a", ""),     # S
        ocr.get("h2o_analyzer_b", ""),     # T
        ocr.get("h2s_analyzer_a", ""),     # U
        ocr.get("h2s_analyzer_b", ""),     # V
    ]


# =============================================================
# Google Sheets Access Token (JWT)
# =============================================================
def get_google_access_token() -> str:
    """
    Buat access token untuk Google Sheets.
    - Railway: baca dari env var GOOGLE_SERVICE_ACCOUNT_JSON
    - Local: baca dari file credentials.json
    """
    # Coba baca dari env var dulu (Railway)
    svc_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if svc_json:
        sa = json.loads(svc_json)
    else:
        # Fallback ke file (Local)
        sa_file = SCRIPT_DIR / "credentials.json"
        if not sa_file.exists():
            raise FileNotFoundError(
                f"credentials.json tidak ditemukan!\n"
                f"  → Railway: Set env var GOOGLE_SERVICE_ACCOUNT_JSON\n"
                f"  → Local:   Letakkan file credentials.json di {sa_file}"
            )
        sa = json.loads(sa_file.read_text(encoding="utf-8"))
    iat = int(time.time())
    payload = {
        "iss":   sa["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud":   "https://oauth2.googleapis.com/token",
        "iat":   iat,
        "exp":   iat + 3600,
    }
    private_key = sa["private_key"]
    if "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")
    signed_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion": signed_jwt},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# =============================================================
# Tulis ke Google Sheets (Task 6)
# =============================================================
def write_to_sheet(session: dict):
    """Tulis session.ocr ke Google Sheets (panggil saat /selesai)."""
    sheet_row = build_sheet_row(session["ocr"])
    row_number = TIME_BASE_ROW + session["time"]
    access_token = get_google_access_token()
    range_str = f"{SHEET_NAME}!B{row_number}:V{row_number}"
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/"
        f"{SPREADSHEET_ID}/values/{range_str}"
        "?valueInputOption=USER_ENTERED"
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    body = {"values": [sheet_row]}
    resp = requests.put(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    logger.info(f"Data ditulis ke sheet baris {row_number} (Time {session['time']})")
    return resp.json()


# =============================================================
# OCR Merge (Task 3)
# =============================================================
def merge_ocr(session: dict, new_data: dict) -> list[str]:
    """
    Merge new_data ke session['ocr'].
    Hanya overwrite jika new_data[key] NOT NULL.
    Return list perubahan (Task 8: duplicate detection).
    """
    changes: list[str] = []
    ocr = session["ocr"]
    for key, value in new_data.items():
        if value is None or value == "" or value == "null":
            continue
        old_val = ocr.get(key)
        ocr[key] = value
        if old_val not in (None, "", "null") and old_val != value:
            changes.append(f"{key}: {old_val} → {value}")
            logger.info(f"Nilai berubah ({key}): {old_val} → {value}")
    return changes
