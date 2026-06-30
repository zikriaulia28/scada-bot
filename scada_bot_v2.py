#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Multi Image Session OCR

Alur baru (Session-based):
  /mulai                    → Buat session, tanya Time
  Kirim foto (banyak)       → OCR + merge, update progress
  /status                   → Tampilkan progress
  /selesai                  → Validasi → tulis ke Google Sheets
  /batal                    → Hapus session

Implementasi 12 Task:
  [1]  Session state (_pending_photos → _sessions)
  [2]  Command handler (/mulai, /status, /selesai, /batal)
  [3]  OCR Merge (gabung foto tanpa overwrite)
  [4]  Progress calculator
  [5]  Summary builder dinamis (FIELD_MAP + loop)
  [6]  Google Sheets hanya saat /selesai
  [7]  Validasi sebelum /selesai
  [8]  Duplicate detection + log perubahan
  [9]  Session timeout 30 menit
  [10] Logging (print → logger)
  [11] Configuration (.env/api-key.txt)
  [12] Unit test (coverage 80%)
"""

# =============================================================
# IMPORTS
# =============================================================
import os
import sys
import time
import json
import base64
import pathlib
import logging
import traceback
from datetime import datetime, timezone, timedelta
from typing import Optional
from io import BytesIO

import requests
import jwt

# =============================================================
# LOGGING (Task 10)
# =============================================================
_log_level = os.environ.get("SCADA_LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format=_log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scada_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scada_bot")

# =============================================================
# KONFIGURASI — Baca dari api-key.txt (Task 11)
# =============================================================
SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
_API_KEY_FILE = SCRIPT_DIR / "api-key.txt"

def _load_config():
    """Baca token/key dari file api-key.txt."""
    cfg = {}
    if not _API_KEY_FILE.exists():
        logger.error(f"File konfigurasi tidak ditemukan: {_API_KEY_FILE}")
        sys.exit(1)
    for line in _API_KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            cfg[key.strip()] = val.strip()
    return cfg

_config = _load_config()

TELEGRAM_BOT_TOKEN = _config.get("Telegram Bot Token", "")
OPERATOR_CHAT_ID   = _config.get("Chat ID operator", "")
GEMINI_API_KEY     = _config.get("Gemini API Key", "")

# OCR Engine: "gemini" atau "paddle" (lokal, tanpa limit)
OCR_ENGINE = "gemini"

if not TELEGRAM_BOT_TOKEN or "***" in TELEGRAM_BOT_TOKEN or len(TELEGRAM_BOT_TOKEN) < 20:
    logger.error("TELEGRAM_BOT_TOKEN belum diisi dengan lengkap di api-key.txt!")
    logger.error("Buka api-key.txt → isi 'Telegram Bot Token=TOKEN_LENGKAP'")
    sys.exit(1)

if not OPERATOR_CHAT_ID:
    logger.error("Chat ID belum diisi di api-key.txt!")
    sys.exit(1)

if not GEMINI_API_KEY and OCR_ENGINE != "paddle":
    logger.error("Gemini API Key belum diisi di api-key.txt!")
    sys.exit(1)

# Sheet
SPREADSHEET_ID = "19CVGvZmEYiMQCQek1pHUKtdF9wABab7vyIjdM7dK1o4"
SHEET_NAME     = "1"

# Row mapping: Time N → sheet row (N=1 → baris 5, dst.)
TIME_BASE_ROW = 4   # Time N → row TIME_BASE_ROW + N
MAX_TIME      = 24
SESSION_TIMEOUT_MINUTES = 30

# Delay antar request ke Gemini (dalam detik) untuk menghindari 429
GEMINI_REQUEST_DELAY = 3
_gemini_last_request_time: float = 0.0


# Gemini
_GEMINI_MODEL    = "gemini-3.1-flash-lite"
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + _GEMINI_MODEL
    + ":generateContent?key="
    + GEMINI_API_KEY
)

# Gemini prompt
GEMINI_PROMPT = """You are a SCADA OCR system. Analyze this HMI SCADA photo carefully.
Extract ALL visible values. Return ONLY a pure JSON object with these exact keys, no markdown, no code block:

{
  "pit_100": number_or_null,
  "pit_101": number_or_null,
  "tit_100": number_or_null,
  "pit_1001a": number_or_null,
  "tit_1001a": number_or_null,
  "fit_1001a": number_or_null,
  "pit_1001b": number_or_null,
  "tit_1001b": number_or_null,
  "fit_1001b": number_or_null,
  "pit_106": number_or_null,
  "tit_103": number_or_null,
  "pit_103": number_or_null,
  "pcv_a_auto_loop_mv": number_or_null,
  "pcv_b_auto_loop_mv": number_or_null,
  "pit_104_pv": number_or_null,
  "gc_a_actual_btu": number_or_null,
  "gc_b_actual_btu": number_or_null,
  "h2o_analyzer_a": number_or_null,
  "h2o_analyzer_b": number_or_null,
  "h2s_analyzer_a": number_or_null,
  "h2s_analyzer_b": number_or_null
}

Important:
- Use dot (.) as decimal separator, NOT comma.
- Use null for values that are not visible, unclear, or missing.
- Do NOT include any text before or after the JSON.
- Do NOT use markdown code blocks."""

# =============================================================
# FIELD MAP — untuk iterasi dinamis (Task 5)
# =============================================================
FIELD_MAP = [
    ("pit_100",          "PIT 100"),
    ("pit_101",          "PIT 101"),
    ("tit_100",          "TIT 100"),
    ("pit_1001a",        "PIT 1001A"),
    ("tit_1001a",        "TIT 1001A"),
    ("fit_1001a",        "FIT 1001A"),
    ("pit_1001b",        "PIT 1001B"),
    ("tit_1001b",        "TIT 1001B"),
    ("fit_1001b",        "FIT 1001B"),
    ("pit_106",          "PIT 106"),
    ("tit_103",          "TIT 103"),
    ("pit_103",          "PIT 103"),
    ("pcv_a_auto_loop_mv",    "Loop A %"),
    ("pcv_b_auto_loop_mv",    "Loop B %"),
    ("pit_104_pv",       "PIT 104(PV)"),
    ("gc_a_actual_btu",  "GC A"),
    ("gc_b_actual_btu",  "GC B"),
    ("h2o_analyzer_a",   "H2O A"),
    ("h2o_analyzer_b",   "H2O B"),
    ("h2s_analyzer_a",   "H2S A"),
    ("h2s_analyzer_b",   "H2S B"),
]

TOTAL_PARAMS = len(FIELD_MAP)   # 21

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

# =============================================================
# FUNGSI: Google Sheets Access Token (JWT)
# =============================================================
def get_google_access_token() -> str:
    sa_file = SCRIPT_DIR / "credentials.json"
    if not sa_file.exists():
        raise FileNotFoundError(f"credentials.json tidak ditemukan di {sa_file}")
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
# FUNGSI: Normalisasi angka — koma → titik (pemisah desimal)
# =============================================================
def normalize_number(val: str) -> str:
    """Normalize numeric strings from Gemini to a format suitable for Google Sheets.
    Rules:
    1. Remove commas (assumed thousand separators).
    2. Handle dots:
       - Single dot:
           * If fractional part length <=2 → treat as decimal (keep dot).
           * Else (e.g., "1.234") → treat as thousand separator, remove dot.
       - Multiple dots:
           * If the last part length <=2 → decimal, join previous parts as integer.
           * If the pattern matches "1.xxx.yyy" (first part length==1, exactly three parts, last part length==3)
             → treat last part as decimal, first two parts form integer (e.g., "1.069.307" → "1069.307").
           * Otherwise → no decimal, treat all dots as thousand separators (remove them).
    3. Preserve non‑numeric strings unchanged.
    """
    if not val or val == "":
        return val
    s = str(val).strip()
    # Non‑numeric, just return
    if not any(c.isdigit() for c in s):
        return s
    # Remove commas (US thousand separator)
    s = s.replace(",", "")
    parts = s.split('.')
    if len(parts) == 1:
        return s
    # Single dot case
    if len(parts) == 2:
        int_part, frac = parts
        if len(frac) <= 2:
            # Decimal (e.g., "1234.56")
            return s
        else:
            # Thousand separator (e.g., "1.234")
            return int_part + frac
    # Multiple dots
    # Check for special pattern 1.xxx.yyy where last part length == 3
    if len(parts) == 3 and len(parts[0]) == 1 and len(parts[-1]) == 3:
        # Treat last part as decimal, first two parts as integer
        integer = parts[0] + parts[1]
        return f"{integer}.{parts[2]}"
    # General case: if last part length <=2, treat as decimal
    if len(parts[-1]) <= 2:
        integer = ''.join(parts[:-1])
        return f"{integer}.{parts[-1]}"
    # Otherwise, treat all dots as thousand separators → remove them
    return ''.join(parts)
# =============================================================
# FUNGSI: Build Sheet Row (kolom B–V, 21 nilai)
# =============================================================
def build_sheet_row(ocr: dict) -> list:
    """
    Urutan kolom B-V (21 kolom, 1-to-1 dengan Gemini prompt):
      B: PIT 100        K: PIT 106        R: GC A
      C: PIT 101        L: TIT 103        S: GC B
      D: TIT 100        M: PIT 103        T: H2O A
      E: PIT 1001A      N: Loop A %       U: H2O B
      F: TIT 1001A      O: Loop B %       V: H2S A
      G: FIT 1001A      P: PIT 104(PV)    
      H: PIT 1001B      Q: GC A BTU
      I: TIT 1001B
      J: FIT 1001B
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
# FUNGSI: Tulis ke Google Sheets (Task 6)
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
# FUNGSI: Kompres gambar agar tidak timeout (max ~500KB)
# =============================================================
def compress_image(image_bytes: bytes, max_size_kb: int = 500, min_quality: int = 60) -> bytes:
    """
    Kompres gambar JPEG. Jika >max_size_kb, turunkan quality sampai cukup kecil.
    """
    from PIL import Image
    
    try:
        img = Image.open(BytesIO(image_bytes))
        # Convert ke RGB jika RGBA/PNG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        quality = 85
        while quality >= min_quality:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            size_kb = buf.tell() // 1024
            if size_kb <= max_size_kb:
                logger.debug(f"Foto dikompres: {size_kb}KB (quality={quality})")
                return buf.getvalue()
            quality -= 10
        
        # Sudah di min_quality tapi masih besar — return apa adanya
        logger.warning(f"Foto tetap besar: {size_kb}KB setelah kompresi")
        return image_bytes
    except Exception as e:
        logger.warning(f"Gagal kompres gambar: {e}, gunakan aslinya")
        return image_bytes

# =============================================================
# FUNGSI: OCR via Gemini Vision (dengan retry + throttle)
# =============================================================
def gemini_ocr(image_bytes: bytes, max_retries: int = 3) -> dict:
    """OCR via Gemini dengan kompresi + retry 429 + retry timeout."""
    global _gemini_last_request_time
    
    # Kompres gambar agar tidak timeout
    image_bytes = compress_image(image_bytes)
    body = {
        "contents": [{"parts": [
            {"text": GEMINI_PROMPT},
            {"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }},
        ]}]
    }

    # Throttle: pastikan jeda minimum antar request ke Gemini
    now = time.time()
    elapsed = now - _gemini_last_request_time
    if elapsed < GEMINI_REQUEST_DELAY:
        sleep_time = GEMINI_REQUEST_DELAY - elapsed
        logger.debug(f"Throttle Gemini: menunggu {sleep_time:.1f}s...")
        time.sleep(sleep_time)
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Mengirim foto ke Gemini ({_GEMINI_MODEL})... attempt {attempt + 1}/{max_retries}")
            resp = requests.post(_GEMINI_ENDPOINT, json=body, timeout=120)
            _gemini_last_request_time = time.time()  # update timestamp

            resp_json = resp.json()
            logger.debug(f"Gemini response (status {resp.status_code}): {json.dumps(resp_json, indent=2)[:1000]}")

            # Handle 429 - Too Many Requests (status code)
            if resp.status_code == 429:
                wait_time = (2 ** attempt) * 5
                logger.warning(f"429 Too Many Requests. Menunggu {wait_time} detik sebelum retry...")
                time.sleep(wait_time)
                continue

            # Handle quota error di body JSON (Gemini kadang return 200 + error)
            if "error" in resp_json:
                error_msg = resp_json["error"].get("message", str(resp_json["error"]))
                # Coba ekstrak "retry in Xs" dari pesan error
                import re
                retry_match = re.search(r'retry\s+in\s+([\d.]+)s', error_msg, re.IGNORECASE)
                if retry_match and attempt < max_retries - 1:
                    wait_time = float(retry_match.group(1)) + 1  # +1 safety
                    logger.warning(f"Quota exceeded — retry dalam {wait_time:.0f}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"Gemini API error: {error_msg}")

            resp.raise_for_status()

            # Cek promptFeedback (blockReason)
            if "promptFeedback" in resp_json:
                block_reason = resp_json["promptFeedback"].get("blockReason", "unknown")
                raise RuntimeError(f"Gemini memblokir respons: {block_reason}")

            # Cek candidates
            if "candidates" not in resp_json or not resp_json["candidates"]:
                finish = resp_json.get("candidates", [{}])[0].get("finishReason", "unknown") if resp_json.get("candidates") else "no candidates"
                raise RuntimeError(f"Gemini tidak mengembalikan candidates. finishReason: {finish}. Response: {json.dumps(resp_json)[:500]}")

            txt = resp_json["candidates"][0]["content"]["parts"][0]["text"]
            txt = txt.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(txt)
                logger.info(f"OCR berhasil — {len(data)} parameter diekstrak.")
                return data
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Gagal parse JSON Gemini: {e}\nRAW: {txt[:300]}")

        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ReadTimeout,
        ) as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5
                logger.warning(f"Error koneksi Gemini: {e}. Retry {attempt + 2}/{max_retries} dalam {wait_time}s...")
                time.sleep(wait_time)
                continue
            raise

    # Jika loop selesai tanpa return
    raise RuntimeError("Gagal setelah semua retry — Gemini tidak merespons dengan data valid.")

# =============================================================
# FUNGSI: OCR via PaddleOCR Lokal
# =============================================================
# PaddleOCR instance (lazy init)
_paddle_ocr_instance = None

def _get_paddle_ocr():
    """Lazy init PaddleOCR — hanya load saat pertama dipakai."""
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        logger.info("Memuat PaddleOCR...")
        # Disable OneDNN & PIR — tidak kompatibel dengan Paddle 3.x di CPU Windows
        os.environ["FLAGS_use_mkldnn"] = "0"
        os.environ["FLAGS_enable_pir_api"] = "0"
        os.environ["FLAGS_pir_apply_inplace_pass"] = "0"
        os.environ["FLAGS_pir_apply_general_fuse_pass"] = "0"
        from paddleocr import PaddleOCR  # type: ignore[reportMissingImports]
        _paddle_ocr_instance = PaddleOCR(use_textline_orientation=False, lang='en')
        logger.info("PaddleOCR siap.")
    return _paddle_ocr_instance

# Pola label untuk matching (case-insensitive) → (key, regex_pattern)
# Setiap label bisa muncul dengan variasi spasi/separator di HMI
_LABEL_PATTERNS = [
    ("pit_100",         r"PIT[\s._-]*100(?![\da-zA-Z])"),
    ("pit_101",         r"PIT[\s._-]*101(?![\da-zA-Z])"),
    ("tit_100",         r"TIT[\s._-]*100(?![\da-zA-Z])"),
    ("pit_1001a",       r"PIT[\s._-]*1001[\s._-]*A"),
    ("tit_1001a",       r"TIT[\s._-]*1001[\s._-]*A"),
    ("fit_1001a",       r"FIT[\s._-]*1001[\s._-]*A"),
    ("pit_1001b",       r"PIT[\s._-]*1001[\s._-]*B"),
    ("tit_1001b",       r"TIT[\s._-]*1001[\s._-]*B"),
    ("fit_1001b",       r"FIT[\s._-]*1001[\s._-]*B"),
    ("pit_106",         r"PIT[\s._-]*106(?![\da-zA-Z])"),
    ("tit_103",         r"TIT[\s._-]*103(?![\da-zA-Z])"),
    ("pit_103",         r"PIT[\s._-]*103(?![\da-zA-Z])"),
    ("loop_mv_pcv_a",   r"(?:LOOP|PCV)[\s._-]*A"),
    ("loop_mv_pcv_b",   r"(?:LOOP|PCV)[\s._-]*B"),
    ("pit_104_pv",      r"PIT[\s._-]*104"),
    ("gc_a_actual_btu", r"GC[\s._-]*A"),
    ("gc_b_actual_btu", r"GC[\s._-]*B"),
    ("h2o_analyzer_a",  r"H2O[\s._-]*A"),
    ("h2o_analyzer_b",  r"H2O[\s._-]*B"),
    ("h2s_analyzer_a",  r"H2S[\s._-]*A"),
    ("h2s_analyzer_b",  r"H2S[\s._-]*B"),
]

def _is_number(s: str) -> bool:
    """Cek apakah string adalah angka (positif/negatif, desimal)."""
    try:
        float(s.replace(",", "."))
        return True
    except (ValueError, AttributeError):
        return False

def paddle_ocr(image_bytes: bytes) -> dict:
    """
    OCR lokal pakai PaddleOCR.
    Strategi:
      1. Deteksi semua teks + posisi dari gambar
      2. Cari label yang cocok (PIT 100, TIT 100, dll)
      3. Untuk setiap label, cari angka terdekat (di sebelah kanan/bawah)
      4. Return dict {key: value} seperti output Gemini
    """
    import re
    import numpy as np
    from io import BytesIO
    from PIL import Image

    ocr_engine = _get_paddle_ocr()

    # Convert bytes → numpy array
    img = Image.open(BytesIO(image_bytes))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img_array = np.array(img)

    # Jalankan OCR (API terbaru: pakai predict())
    result = ocr_engine.predict(img_array)

    # Kumpulkan semua teks + posisi
    detections = []
    for page in result:
        if page is None:
            continue
        for line in page:
            bbox = line[0]
            txt, conf = line[1]
            # Posisi tengah
            cx = (bbox[0][0] + bbox[2][0]) / 2
            cy = (bbox[0][1] + bbox[2][1]) / 2
            # Bounding box
            x1, y1 = bbox[0]
            x2, y2 = bbox[2]
            detections.append({
                "text": txt.strip(),
                "conf": conf,
                "cx": cx, "cy": cy,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            })

    logger.info(f"PaddleOCR: {len(detections)} teks terdeteksi.")

    # Debug: log semua teks yang terdeteksi
    for d in detections:
        logger.debug(f"  OCR: '{d['text']}' conf={d['conf']:.2f} pos=({d['cx']:.0f},{d['cy']:.0f})")

    # Step 1: Pisahkan label dari angka
    label_detections = []   # teks yang mengandung label (PIT/TIT/FIT/GC/H2O/H2S)
    number_detections = []  # teks yang merupakan angka

    for d in detections:
        txt = d["text"]
        if _is_number(txt):
            number_detections.append(d)
        else:
            label_detections.append(d)

    logger.info(f"PaddleOCR: {len(label_detections)} label, {len(number_detections)} angka.")

    # Step 2: Untuk setiap label pattern, cari label terdekat
    result_dict = {}
    used_numbers = set()  # indeks angka yang sudah dipakai

    for key, pattern in _LABEL_PATTERNS:
        best_label = None
        best_dist = float("inf")

        for i, lbl in enumerate(label_detections):
            if re.search(pattern, lbl["text"], re.IGNORECASE):
                # Hitung jarak vertikal (prioritaskan label yang satu baris)
                # dan horizontal (label harus di kiri angka)
                dist = lbl["cy"]  # preferensi posisi
                if dist < best_dist:
                    best_dist = dist
                    best_label = lbl

        if best_label is None:
            continue

        # Cari angka terdekat: di sebelah KANAN label, dengan Y yang mirip
        best_num = None
        best_score = float("inf")

        for j, num in enumerate(number_detections):
            if j in used_numbers:
                continue
            # Angka harus di sebelah kanan label (x > label.x2)
            # atau di bawah label (y > label.y2) dengan X yang mirip
            dx = num["cx"] - best_label["cx"]
            dy = abs(num["cy"] - best_label["cy"])

            # Skor: lebih kecil = lebih dekat
            # Prioritaskan: di kanan & sejajar
            if dx > 0 and dy < 60:
                score = dx + dy * 5  # beri penalty pada offset vertikal
            elif dy < 40 and abs(dx) < 100:
                # Di bawah label (vertikal)
                score = dy * 3 + abs(dx) * 2
            else:
                score = 99999

            if score < best_score:
                best_score = score
                best_num = (j, num)

        if best_num is not None:
            j, num = best_num
            used_numbers.add(j)
            value = num["text"].replace(",", ".")
            result_dict[key] = value
            logger.debug(f"  Match: {key} = {value} (label: '{best_label['text']}', num: '{num['text']}')")

    logger.info(f"PaddleOCR: {len(result_dict)}/{len(_LABEL_PATTERNS)} parameter ditemukan.")
    return result_dict

# =============================================================
# FUNGSI: OCR Merge (Task 3)
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

# =============================================================
# FUNGSI: Kirim pesan ke Telegram
# =============================================================
def tg_send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if not r.ok:
            logger.warning(f"Gagal kirim Telegram: {r.text}")
    except Exception as e:
        logger.warning(f"Exception kirim Telegram: {e}")

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

        tg_send_message(chat_id,"⏰ Silakan pilih *Time* (1–24) untuk session ini.")
        # Simpan state: sedang menunggu input time
        _pending_time[chat_id] = True
        logger.info(f"Menunggu input time dari chat={chat_id}")
        return

    # /status
    if command == "/status":
        if not session:
            tg_send_message(chat_id,"📭 Belum ada session aktif.\nGunakan /mulai untuk memulai.")
            return
        tg_send_message(chat_id,build_status_text(session))
        return

    # /batal
    if command == "/batal":
        if not session:
            tg_send_message(chat_id,"📭 Tidak ada session aktif.")
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

    # /selesai
    if command == "/selesai":
        if not session:
            tg_send_message(chat_id,"📭 Belum ada session aktif.\nGunakan /mulai untuk memulai.")
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

    # Konfirmasi Ya/Tidak untuk /selesai
    if chat_id in _waiting_confirm:
        if text.lower() in ("ya", "y", "yes", "iya", "yoi"):
            _waiting_confirm.pop(chat_id, None)
            session = get_session(chat_id)
            if session:
                _do_write(chat_id, session)
        elif text.lower() in ("tidak", "no", "n", "nope", "engga"):
            _waiting_confirm.pop(chat_id, None)
            tg_send_message(chat_id,"❌ Penyimpanan dibatalkan.\nGunakan /mulai untuk session baru.")
        else:
            tg_send_message(chat_id,"Ketik *Ya* atau *Tidak*.")
        return

    # Input waktu (setelah /mulai)
    if chat_id in _pending_time:
        try:
            time_num = int(text)
        except ValueError:
            tg_send_message(chat_id,"❌ Input tidak valid. Masukkan angka 1–24.")
            return
        if time_num < 1 or time_num > MAX_TIME:
            tg_send_message(chat_id,f"❌ Angka harus antara 1–{MAX_TIME}.")
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
# INTERNAL: Eksekusi penulisan sheet
# =============================================================
def _do_write(chat_id: str, session: dict):
    """Panggil setelah /selesai + konfirmasi."""
    try:
        write_to_sheet(session)
        changes = session.get("changes_log", [])
        tg_send_message(chat_id,build_summary_text(session, changes))
        logger.info(f"Session selesai: chat={chat_id}, time={session['time']}")
    except Exception as e:
        logger.error(f"Gagal menulis sheet: {e}")
        tg_send_message(chat_id,f"⚠️ *Error saat menyimpan:*\n`{str(e)[:200]}`")
    finally:
        delete_session(chat_id)
        _waiting_confirm.pop(chat_id, None)


# State tambahan
_pending_time: dict[str, bool] = {}   # chat_id → True (menunggu input time)

# =============================================================
# MAIN: Handle pesan masuk
# =============================================================
def handle_message(msg: dict):
    chat_id = str(msg["chat"]["id"])
    user = msg.get("from", {}).get("first_name", "User")
    text = msg.get("text", "").strip()
    logger.info(f"Pesan dari {user} ({chat_id}): '{text[:50]}'")

    # Cleanup timeout sessions dulu (Task 9)
    cleanup_timeout_sessions()

    # Command
    if text.startswith("/"):
        handle_command(chat_id, text.split()[0], text[len(text.split()[0]):].strip())
        return

    # Konfirmasi Ya/Tidak (Task 7)
    if chat_id in _waiting_confirm:
        handle_command(chat_id, "/selesai", text)
        return

    # Input time setelah /mulai
    if chat_id in _pending_time:
        handle_command(chat_id, "", text)
        return

    # Foto
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

            # OCR — pilih engine berdasarkan konfigurasi
            if OCR_ENGINE == "paddle":
                ocr_data = paddle_ocr(img_bytes)
            else:
                ocr_data = gemini_ocr(img_bytes)

            # Merge (Task 3)
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
            tg_send_message(chat_id,status_preview)
            logger.info(f"Progress: {filled}/{total}, foto: {session['photo_count']}")

        except Exception as e:
            logger.error(f"Error proses foto: {e}\n{traceback.format_exc()}")
            tg_send_message(chat_id,f"⚠️ *Error memproses foto:*\n`{str(e)[:200]}`")
        return

    # Teks biasa tanpa session
    if text and not text.startswith("/"):
        tg_send_message(chat_id,
            "📸 Ketik /mulai untuk membuat session.\n"
            "📊 Ketik /status untuk melihat progress.\n"
            "✅ Ketik /selesai untuk menyimpan ke Sheet."
        )
        return


# =============================================================
# MAIN LOOP
# =============================================================
def main():
    logger.info("=" * 55)
    logger.info("  SCADA Bot v2 — Multi Image Session OCR")
    logger.info(f"  Model: {_GEMINI_MODEL}  |  Sheet: {SHEET_NAME}")
    logger.info(f"  Timeout: {SESSION_TIMEOUT_MINUTES} menit")
    logger.info("=" * 55)
    logger.info("Perintah: /mulai /status /selesai /batal")

    # Startup notification ke operator (jika ada Chat ID)
    if OPERATOR_CHAT_ID:
        import time as _time
        _time.sleep(2)  # Tunggu polling dimulai
        tg_send_message(OPERATOR_CHAT_ID, "🤖 *SCADA Bot v2 online!*")

    last_update_id = 0

    while True:
        try:
            cleanup_timeout_sessions()   # cek timeout setiap loop
            upd_resp = requests.get(
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
            logger.error(f"Error: {exc}\n{traceback.format_exc()}")
            if OPERATOR_CHAT_ID:
                tg_send_message(OPERATOR_CHAT_ID, f"⚠️ *Error bot:*\n`{str(exc)[:200]}`")
            time.sleep(5)
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()