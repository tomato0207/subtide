import base64
import binascii
import logging

import requests
from flask import Blueprint, request, jsonify

from backend.config import WORD_LOOKUP_ENABLED
from backend.services import dictionary_service, ocr_service
from backend.services.word_service import define_word, normalize_word, InvalidWordError

word_bp = Blueprint('word', __name__)
logger = logging.getLogger('subtide')


@word_bp.route('/api/define', methods=['POST'])
def define():
    """
    Hover dictionary for an English word.

    Body: {"word": "running", "context": "sentence it appears in", "mode": "dict" | "extras" | "ai"}
      dict   (default) local ECDICT entry, instant: lemma, phonetic, senses, forms, tags
      extras online extras: English definitions, examples (+ zh), context sentence translation
      ai     local Ollama explanation of the meaning in this sentence
    """
    if not WORD_LOOKUP_ENABLED:
        return jsonify({'error': 'Word lookup disabled'}), 404

    data = request.get_json(silent=True) or {}
    mode = data.get('mode') or 'dict'
    context = ' '.join(str(data.get('context') or '').split())[:300]
    try:
        word = normalize_word(data.get('word'))
        if mode == 'dict':
            entry = dictionary_service.lookup(word)
            return (jsonify(entry), 200) if entry else (jsonify({'error': 'Not found', 'word': word}), 404)
        if mode == 'extras':
            lemma = normalize_word(data.get('lemma') or word)
            return jsonify(dictionary_service.extras(word, lemma, context))
        if mode == 'ai':
            return jsonify(define_word(word, context, near=str(data.get('near') or '')[:20]))
        return jsonify({'error': 'Invalid mode'}), 400
    except InvalidWordError:
        return jsonify({'error': 'Invalid word'}), 400
    except dictionary_service.DictionaryUnavailableError:
        return jsonify({'error': 'Dictionary not installed'}), 503
    except requests.ConnectionError:
        return jsonify({'error': 'Cannot connect to Ollama'}), 503
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning(f"[WORD] Lookup failed: {e}")
        return jsonify({'error': 'Lookup failed'}), 502


def choose_ocr_word(found):
    """
    Pick the word to look up from an OCR hit: the whole letter run if it is a real word,
    then the run with OCR's usual l/i confusion fixed ("Executlon"), else the piece
    split out of glued words at the cursor ("Executiontimeratio" -> "ratio").
    """
    run = found['word']
    candidates = [run, run.replace('l', 'i')] if len(run) >= 2 else []
    try:
        for candidate in candidates:
            if dictionary_service.lookup(candidate):
                return candidate
    except dictionary_service.DictionaryUnavailableError:
        pass
    return found['split']


@word_bp.route('/api/ocr-word', methods=['POST'])
def ocr_word():
    """
    Find the English word under the mouse in a video frame strip.

    Body: {"image": "<base64 JPEG/PNG, data: URL allowed>", "x": 120, "y": 40}  (pixels in the image)
    Returns: {"found": true, "word", "context", "near"} or {"found": false}
    """
    if not WORD_LOOKUP_ENABLED:
        return jsonify({'error': 'Word lookup disabled'}), 404

    data = request.get_json(silent=True) or {}
    try:
        image_b64 = str(data.get('image') or '')
        image = base64.b64decode(image_b64.split(',', 1)[-1], validate=True)
        x, y = float(data['x']), float(data['y'])
    except (KeyError, TypeError, ValueError, binascii.Error):
        return jsonify({'error': 'Invalid request'}), 400

    try:
        found = ocr_service.find_word(image, x, y)
    except ocr_service.OCRUnavailableError:
        return jsonify({'error': 'OCR not installed'}), 503
    except (ValueError, OSError) as e:
        logger.warning(f"[OCR] Failed: {e}")
        return jsonify({'error': 'Invalid image'}), 400
    if not found:
        return jsonify({'found': False})
    word = choose_ocr_word(found)
    if len(word) < 2:
        return jsonify({'found': False})
    return jsonify({'found': True, 'word': word, 'context': found['context'], 'near': found['near']})
