"""
AI explanation of what an English word means in its sentence (hover dictionary's
"AI 解析" button), answered by the local Ollama model.

Uses the same model and request options as chapter summaries so Ollama does not
reload the model between the two features.
"""

import logging
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from backend.services.summary_service import _chat_json

logger = logging.getLogger('subtide')
LOG_PREFIX = "[WORD]"

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]{0,39}$")
MAX_CONTEXT_CHARS = 300
CACHE_SIZE = 1000

WORD_PROMPT = (
    "你是英文老師。使用者給你一個英文單字和它出現的句子，用繁體中文（台灣用語）說明這個字在這個句子裡的意思。"
    "只輸出 JSON：{\"word\": \"單字\", \"in_context\": \"在這個句子裡的意思，不超過 25 個字\", "
    "\"note\": \"用法或語感補充，例如搭配詞、片語、為什麼是這個意思，不超過 50 個字\", "
    "\"sentence_zh\": \"整句的通順中文翻譯\"}。"
    "如果單字是變化形（過去式、動名詞等），順帶說明。"
)
OCR_NOTE = (
    "\n（文字來自影片畫面辨識，單字可能和相鄰的字黏在一起或有錯字。"
    "請根據句子判斷游標指的是哪一個英文單字，word 欄位寫出正確拼法。）"
)

_cache: "OrderedDict[Tuple[str, str, str], Dict[str, Any]]" = OrderedDict()
_cache_lock = threading.Lock()


class InvalidWordError(ValueError):
    pass


def normalize_word(word: Any) -> str:
    """Strip surrounding punctuation; raise InvalidWordError if it is not an English word."""
    word = str(word or '').strip().strip("'-")
    if not WORD_RE.match(word):
        raise InvalidWordError('Invalid word')
    return word


def _clean_context(context: Any) -> str:
    return ' '.join(str(context or '').split())[:MAX_CONTEXT_CHARS]


def _as_text(value: Any) -> str:
    return str(value or '').strip()


def define_word(word: Any, context: Optional[str] = None, near: str = '') -> Dict[str, Any]:
    """Explain the word's meaning in this sentence. `near` (letters around the cursor) marks OCR input."""
    from backend.services.whisper_service import convert_chinese_text

    word = normalize_word(word)
    context = _clean_context(context)
    key = (word.lower(), context.lower(), near)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return dict(_cache[key])

    user = f"單字：{word}\n句子：{context or '（沒有句子）'}"
    if near:
        user += f"\n游標位置附近的字母（| 是游標）：{near}" + OCR_NOTE
    reply = _chat_json(WORD_PROMPT, user)

    reply_word = _as_text(reply.get('word'))
    result = {
        'word': reply_word if (near and WORD_RE.match(reply_word)) else word,
        'in_context': convert_chinese_text(_as_text(reply.get('in_context'))),
        'note': convert_chinese_text(_as_text(reply.get('note'))),
        'sentence_zh': convert_chinese_text(_as_text(reply.get('sentence_zh'))) if context else '',
    }

    with _cache_lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return dict(result)


def clear_cache():
    with _cache_lock:
        _cache.clear()
