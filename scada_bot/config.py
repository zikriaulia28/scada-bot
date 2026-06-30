#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Konfigurasi global, FIELD_MAP, Gemini prompt.
Semua konstanta & constant data ada di sini.
"""

import os
import sys
import pathlib
import logging

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
SCRIPT_DIR = pathlib.Path(__file__).parent.parent.resolve()
_API_KEY_FILE = SCRIPT_DIR / "api-key.txt"


def _load_config() -> dict:
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
OPERATOR_CHAT_ID = _config.get("Chat ID operator", "")
GEMINI_API_KEY = _config.get("Gemini API Key", "")

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

# =============================================================
# SHEET SETTINGS
# =============================================================
SPREADSHEET_ID = "19CVGvZmEYiMQCQek1pHUKtdF9wABab7vyIjdM7dK1o4"
SHEET_NAME = "1"
TIME_BASE_ROW = 4    # Time N → row TIME_BASE_ROW + N
MAX_TIME = 24
SESSION_TIMEOUT_MINUTES = 30

# =============================================================
# GEMINI THROTTLE
# =============================================================
GEMINI_REQUEST_DELAY = 3

# =============================================================
# GEMINI MODEL
# =============================================================
_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + _GEMINI_MODEL
    + ":generateContent?key="
    + GEMINI_API_KEY
)

# =============================================================
# GEMINI PROMPT
# =============================================================
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
    ("pit_100", "PIT 100"),
    ("pit_101", "PIT 101"),
    ("tit_100", "TIT 100"),
    ("pit_1001a", "PIT 1001A"),
    ("tit_1001a", "TIT 1001A"),
    ("fit_1001a", "FIT 1001A"),
    ("pit_1001b", "PIT 1001B"),
    ("tit_1001b", "TIT 1001B"),
    ("fit_1001b", "FIT 1001B"),
    ("pit_106", "PIT 106"),
    ("tit_103", "TIT 103"),
    ("pit_103", "PIT 103"),
    ("pcv_a_auto_loop_mv", "Loop A %"),
    ("pcv_b_auto_loop_mv", "Loop B %"),
    ("pit_104_pv", "PIT 104(PV)"),
    ("gc_a_actual_btu", "GC A"),
    ("gc_b_actual_btu", "GC B"),
    ("h2o_analyzer_a", "H2O A"),
    ("h2o_analyzer_b", "H2O B"),
    ("h2s_analyzer_a", "H2S A"),
    ("h2s_analyzer_b", "H2S B"),
]

TOTAL_PARAMS = len(FIELD_MAP)  # 21
