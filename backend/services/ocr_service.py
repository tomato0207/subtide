"""
Find the English word under the mouse in a video frame (hover dictionary for text
burned into the video, e.g. lecture slides).

The extension sends a horizontal strip of the frame around the cursor; RapidOCR finds
text lines, and the word is located by its approximate character position in the line.
The OCR model often drops spaces between English words ("Executiontimeratio"), so the
result carries the letters around the cursor and the LLM picks the actual word.
"""

import io
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger('subtide')
LOG_PREFIX = "[OCR]"

MAX_IMAGE_BYTES = 4 * 1024 * 1024
HIT_PAD_PX = 6

_engine = None
_engine_lock = threading.Lock()
_run_lock = threading.Lock()


class OCRUnavailableError(RuntimeError):
    pass


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as e:
                raise OCRUnavailableError('rapidocr-onnxruntime is not installed') from e
            # 'max' keeps the strip at native size; the default upscales it ~6x and is 30x slower
            _engine = RapidOCR(det_limit_type='max', det_limit_side_len=2000, intra_op_num_threads=4)
            logger.info(f"{LOG_PREFIX} RapidOCR loaded")
        return _engine


def _decode_image(image_bytes: bytes):
    import numpy as np
    from PIL import Image

    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError('Image missing or too large')
    with Image.open(io.BytesIO(image_bytes)) as img:
        return np.array(img.convert('RGB'))


def _box_bounds(box) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def pick_box(boxes: List[Any], x: float, y: float) -> Optional[Tuple[float, float, float, float]]:
    """Bounds of the detected text line under (x, y), or the nearest one on the same row."""
    best, best_dist = None, None
    for box in boxes:
        x0, y0, x1, y1 = _box_bounds(box)
        if not (y0 - HIT_PAD_PX <= y <= y1 + HIT_PAD_PX):
            continue
        dist = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
        if dist <= HIT_PAD_PX and (best_dist is None or dist < best_dist):
            best, best_dist = (x0, y0, x1, y1), dist
    return best


def word_in_line(text: str, bounds: Tuple[float, float, float, float], x: float) -> Optional[Dict[str, str]]:
    """Locate the word at horizontal position x by character proportion within the line box."""
    x0, _, x1, _ = bounds
    if not text or x1 <= x0:
        return None
    idx = int((x - x0) / (x1 - x0) * len(text))
    idx = min(max(idx, 0), len(text) - 1)

    # Snap to the nearest letter (proportional fonts make the estimate approximate)
    def is_letter(i):
        return 0 <= i < len(text) and text[i].isascii() and text[i].isalpha()
    candidates = [j for d in range(3) for j in (idx - d, idx + d) if is_letter(j)]
    if not candidates:
        return None
    idx = candidates[0]

    start, end = idx, idx + 1
    while start > 0 and re.match(r"[A-Za-z'-]", text[start - 1]):
        start -= 1
    while end < len(text) and re.match(r"[A-Za-z'-]", text[end]):
        end += 1
    run = text[start:end]
    return {
        'word': run.strip("'-")[:40],
        'split': split_at(run, idx - start),
        'context': unglue(' '.join(text.split())),
        'near': f"{text[max(0, idx - 6):idx]}|{text[idx:idx + 6]}",
    }


def unglue(text: str) -> str:
    """Re-insert spaces in long glued letter runs so the sentence can be translated."""
    import wordninja
    return re.sub(r"[A-Za-z]{16,}", lambda m: ' '.join(wordninja.split(m.group(0))), text)


def split_at(run: str, offset: int) -> str:
    """OCR often drops spaces ("Executiontimeratio"): split into words and return the one at offset."""
    import wordninja

    pos = 0
    for piece in wordninja.split(run):
        found = run.lower().find(piece.lower(), pos)
        if found < 0:
            continue
        end = found + len(piece)
        if found <= offset < end:
            return run[found:end]
        pos = end
    return run


def find_word(image_bytes: bytes, x: float, y: float) -> Optional[Dict[str, str]]:
    """Return {word, split, context, near} for the English word at (x, y) in the strip, or None.

    Detects all text lines (fast), then recognizes only the line under the cursor:
    recognizing every line costs ~10x more.
    """
    image = _decode_image(image_bytes)
    engine = _get_engine()
    with _run_lock:  # onnxruntime sessions are shared; one strip at a time
        boxes, _ = engine(image, use_det=True, use_cls=False, use_rec=False)
        bounds = pick_box(boxes or [], x, y)
        if not bounds:
            return None
        x0, y0, x1, y1 = (int(v) for v in bounds)
        pad = 2
        crop = image[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
        rec, _ = engine(crop, use_det=False, use_cls=False, use_rec=True)
    text = rec[0][0] if rec else ''
    found = word_in_line(text, bounds, x)
    logger.debug(f"{LOG_PREFIX} ({x:.0f},{y:.0f}) -> {found}")
    return found
