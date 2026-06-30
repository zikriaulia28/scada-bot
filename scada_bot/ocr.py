#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — OCR engines (Gemini + PaddleOCR).
Gemini is the primary engine; PaddleOCR is optional for offline use.
"""

import base64
import json
import re
import time
from io import BytesIO
from typing import Optional

import requests

from scada_bot.config import (
    OCR_ENGINE, GEMINI_PROMPT, _GEMINI_ENDPOINT, _GEMINI_MODEL,
    GEMINI_REQUEST_DELAY, logger,
)

_gemini_last_request_time: float = 0.0


# =============================================================
# Gemini OCR (with retry + throttle)
# =============================================================
def gemini_ocr(image_bytes: bytes, max_retries: int = 3) -> dict:
    """OCR via Gemini with compression, retry on 429/timeout, and rate throttle."""
    global _gemini_last_request_time

    # Compress image first
    image_bytes = _compress_image(image_bytes)

    body = {
        "contents": [{"parts": [
            {"text": GEMINI_PROMPT},
            {"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }},
        ]}]
    }

    # Throttle: ensure minimum delay between Gemini requests
    now = time.time()
    elapsed = now - _gemini_last_request_time
    if elapsed < GEMINI_REQUEST_DELAY:
        sleep_time = GEMINI_REQUEST_DELAY - elapsed
        logger.debug(f"Throttle Gemini: waiting {sleep_time:.1f}s...")
        time.sleep(sleep_time)

    for attempt in range(max_retries):
        try:
            logger.info(f"Sending photo to Gemini ({_GEMINI_MODEL})... attempt {attempt + 1}/{max_retries}")
            resp = requests.post(_GEMINI_ENDPOINT, json=body, timeout=120)
            _gemini_last_request_time = time.time()

            resp_json = resp.json()
            logger.debug(f"Gemini response (status {resp.status_code}): {json.dumps(resp_json, indent=2)[:1000]}")

            # Handle 429 — Too Many Requests
            if resp.status_code == 429:
                wait_time = (2 ** attempt) * 5
                logger.warning(f"429 Too Many Requests. Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue

            # Handle quota error in JSON body
            if "error" in resp_json:
                error_msg = resp_json["error"].get("message", str(resp_json["error"]))
                retry_match = re.search(r'retry\s+in\s+([\d.]+)s', error_msg, re.IGNORECASE)
                if retry_match and attempt < max_retries - 1:
                    wait_time = float(retry_match.group(1)) + 1
                    logger.warning(f"Quota exceeded — retry in {wait_time:.0f}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"Gemini API error: {error_msg}")

            resp.raise_for_status()

            # Check promptFeedback (blocked content)
            if "promptFeedback" in resp_json:
                block_reason = resp_json["promptFeedback"].get("blockReason", "unknown")
                raise RuntimeError(f"Gemini blocked response: {block_reason}")

            # Check candidates
            if "candidates" not in resp_json or not resp_json["candidates"]:
                finish = resp_json.get("candidates", [{}])[0].get("finishReason", "unknown") if resp_json.get("candidates") else "no candidates"
                raise RuntimeError(f"Gemini returned no candidates. finishReason: {finish}. Response: {json.dumps(resp_json)[:500]}")

            txt = resp_json["candidates"][0]["content"]["parts"][0]["text"]
            txt = txt.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(txt)
                logger.info(f"OCR success — {len(data)} parameters extracted.")
                return data
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse Gemini JSON: {e}\nRAW: {txt[:300]}")

        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ReadTimeout,
        ) as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5
                logger.warning(f"Connection error: {e}. Retry {attempt + 2}/{max_retries} in {wait_time}s...")
                time.sleep(wait_time)
                continue
            raise

    raise RuntimeError("Failed after all retries — Gemini did not respond with valid data.")


# =============================================================
# PaddleOCR (lazy init — only loaded when OCR_ENGINE == "paddle")
# =============================================================
_paddle_ocr_instance: Optional[object] = None


def _get_paddle_ocr():
    """Lazy init PaddleOCR — only load when first used."""
    global _paddle_ocr_instance
    if _paddle_ocr_instance is None:
        logger.info("Loading PaddleOCR...")
        import os as _os
        _os.environ["FLAGS_use_mkldnn"] = "0"
        _os.environ["FLAGS_enable_pir_api"] = "0"
        _os.environ["FLAGS_pir_apply_inplace_pass"] = "0"
        _os.environ["FLAGS_pir_apply_general_fuse_pass"] = "0"
        from paddleocr import PaddleOCR  # type: ignore[import-untyped]
        _paddle_ocr_instance = PaddleOCR(use_textline_orientation=False, lang='en')
        logger.info("PaddleOCR ready.")
    return _paddle_ocr_instance


# Label patterns for PaddleOCR (case-insensitive) → (key, regex_pattern)
_LABEL_PATTERNS = [
    ("pit_100",         r"PIT[\s._-]*100(?![a-zA-Z0-9])"),
    ("pit_101",         r"PIT[\s._-]*101(?![a-zA-Z0-9])"),
    ("tit_100",         r"TIT[\s._-]*100(?![a-zA-Z0-9])"),
    ("pit_1001a",       r"PIT[\s._-]*1001[\s._-]*A"),
    ("tit_1001a",       r"TIT[\s._-]*1001[\s._-]*A"),
    ("fit_1001a",       r"FIT[\s._-]*1001[\s._-]*A"),
    ("pit_1001b",       r"PIT[\s._-]*1001[\s._-]*B"),
    ("tit_1001b",       r"TIT[\s._-]*1001[\s._-]*B"),
    ("fit_1001b",       r"FIT[\s._-]*1001[\s._-]*B"),
    ("pit_106",         r"PIT[\s._-]*106(?![a-zA-Z0-9])"),
    ("tit_103",         r"TIT[\s._-]*103(?![a-zA-Z0-9])"),
    ("pit_103",         r"PIT[\s._-]*103(?![a-zA-Z0-9])"),
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
    try:
        float(s.replace(",", "."))
        return True
    except (ValueError, AttributeError):
        return False


def paddle_ocr(image_bytes: bytes) -> dict:
    """
    Local OCR using PaddleOCR.
    Detects all text + position, matches labels to values, returns dict.
    """
    import numpy as np
    from PIL import Image

    ocr_engine = _get_paddle_ocr()

    # Convert bytes → numpy array
    img = Image.open(BytesIO(image_bytes))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img_array = np.array(img)

    result = ocr_engine.predict(img_array)

    # Collect all text + position
    detections = []
    for page in result:
        if page is None:
            continue
        for line in page:
            bbox = line[0]
            txt, conf = line[1]
            cx = (bbox[0][0] + bbox[2][0]) / 2
            cy = (bbox[0][1] + bbox[2][1]) / 2
            x1, y1 = bbox[0]
            x2, y2 = bbox[2]
            detections.append({
                "text": txt.strip(),
                "conf": conf,
                "cx": cx, "cy": cy,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            })

    logger.info(f"PaddleOCR: {len(detections)} texts detected.")

    # Debug log
    for d in detections:
        logger.debug(f"  OCR: '{d['text']}' conf={d['conf']:.2f} pos=({d['cx']:.0f},{d['cy']:.0f})")

    # Step 1: Separate labels from numbers
    label_detections = []
    number_detections = []

    for d in detections:
        txt = d["text"]
        if _is_number(txt):
            number_detections.append(d)
        else:
            label_detections.append(d)

    logger.info(f"PaddleOCR: {len(label_detections)} labels, {len(number_detections)} numbers.")

    # Step 2: Match labels to nearest values
    result_dict = {}
    used_numbers = set()

    for key, pattern in _LABEL_PATTERNS:
        best_label = None
        best_dist = float("inf")

        for lbl in label_detections:
            if re.search(pattern, lbl["text"], re.IGNORECASE):
                dist = lbl["cy"]
                if dist < best_dist:
                    best_dist = dist
                    best_label = lbl

        if best_label is None:
            continue

        best_num = None
        best_score = float("inf")

        for j, num in enumerate(number_detections):
            if j in used_numbers:
                continue
            dx = num["cx"] - best_label["cx"]
            dy = abs(num["cy"] - best_label["cy"])

            if dx > 0 and dy < 60:
                score = dx + dy * 5
            elif dy < 40 and abs(dx) < 100:
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

    logger.info(f"PaddleOCR: {len(result_dict)}/{len(_LABEL_PATTERNS)} parameters found.")
    return result_dict


# =============================================================
# Image compression helper (max ~500KB to avoid timeout)
# =============================================================
def _compress_image(image_bytes: bytes, max_size_kb: int = 500, min_quality: int = 60) -> bytes:
    """
    Compress JPEG image. If >max_size_kb, lower quality until small enough.
    """
    from PIL import Image

    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        quality = 85
        while quality >= min_quality:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            size_kb = buf.tell() // 1024
            if size_kb <= max_size_kb:
                logger.debug(f"Photo compressed: {size_kb}KB (quality={quality})")
                return buf.getvalue()
            quality -= 10

        logger.warning(f"Photo still large after compression")
        return image_bytes
    except Exception as e:
        logger.warning(f"Image compression failed: {e}, using original")
        return image_bytes


# =============================================================
# Dispatcher — picks the configured OCR engine
# =============================================================
def run_ocr(image_bytes: bytes) -> dict:
    """Run OCR using the configured engine (gemini or paddle)."""
    if OCR_ENGINE == "paddle":
        return paddle_ocr(image_bytes)
    else:
        return gemini_ocr(image_bytes)