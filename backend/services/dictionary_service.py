"""
English-Chinese dictionary for the hover lookup.

- Entries (phonetic, Chinese senses by part of speech, English definitions, inflections,
  exam tags) come from ECDICT (MIT, https://github.com/skywind3000/ECDICT), stored
  locally in SQLite, so lookups are instant and offline.
- Example sentences come from the Free Dictionary API (dictionaryapi.dev).
- Example sentences and the sentence the word appears in are translated with Google
  Translate. Both online parts are optional: failures just leave them out.
"""

import csv
import logging
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger('subtide')
LOG_PREFIX = "[DICT]"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
CSV_PATH = os.path.join(DATA_DIR, 'ecdict.csv')
DB_PATH = os.path.join(DATA_DIR, 'ecdict.db')
CSV_URL = 'https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv'

FREE_DICT_URL = 'https://api.dictionaryapi.dev/api/v2/entries/en/{}'
GOOGLE_URL = 'https://translate.googleapis.com/translate_a/single'
HTTP_TIMEOUT = 4
MAX_EXAMPLES = 2
MAX_DEFINITIONS = 3
BATCH_SIZE = 20000

# ECDICT exchange codes
FORM_LABELS = {
    'p': '過去式', 'd': '過去分詞', 'i': '現在分詞／動名詞', '3': '第三人稱單數',
    's': '複數', 'r': '比較級', 't': '最高級',
}
FORM_ORDER = ['p', 'd', 'i', '3', 's', 'r', 't']
POS_ABBR = {
    'noun': 'n.', 'verb': 'v.', 'adjective': 'adj.', 'adverb': 'adv.', 'pronoun': 'pron.',
    'preposition': 'prep.', 'conjunction': 'conj.', 'interjection': 'interj.', 'article': 'art.',
}
# Sense lines that only say "X's past tense" etc.
FORM_NOTE_RE = re.compile(r'的(過去式|過去分詞|現在分詞|複數|比較級|最高級|第三人稱)|的过去式|的过去分词|的复数')
TAG_LABELS = {
    'zk': '國中', 'gk': '高中', 'cet4': 'CET4', 'cet6': 'CET6', 'ky': '考研',
    'toefl': '托福', 'ielts': '雅思', 'gre': 'GRE',
}

_db_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_pool = ThreadPoolExecutor(max_workers=4)


class DictionaryUnavailableError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def parse_exchange(exchange: str) -> Dict[str, str]:
    """'0:go/1:p/s:runs' -> {'0': 'go', '1': 'p', 's': 'runs'}"""
    result = {}
    for part in (exchange or '').split('/'):
        key, _, value = part.partition(':')
        if key and value:
            result[key] = value
    return result


def build_database(csv_path: Optional[str] = None, db_path: Optional[str] = None) -> int:
    """Convert ecdict.csv to an indexed SQLite file. Returns the number of entries."""
    csv_path, db_path = csv_path or CSV_PATH, db_path or DB_PATH
    tmp_path = db_path + '.tmp'
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute('CREATE TABLE entries (word TEXT, key TEXT, phonetic TEXT, definition TEXT, '
                     'translation TEXT, collins INTEGER, oxford INTEGER, tag TEXT, exchange TEXT)')
        conn.execute('CREATE TABLE forms (form TEXT, lemma TEXT, kind TEXT)')
        count = 0
        with open(csv_path, 'r', encoding='utf-8', newline='') as f:
            rows, forms = [], []
            for row in csv.DictReader(f):
                if not row.get('translation'):
                    continue
                word = row['word']
                rows.append((word, word.lower(), row['phonetic'], row['definition'], row['translation'],
                             int(row['collins'] or 0), int(row['oxford'] or 0), row['tag'], row['exchange']))
                for kind, form in parse_exchange(row['exchange']).items():
                    if kind in FORM_LABELS and form.lower() != word.lower():
                        forms.append((form.lower(), word, kind))
                count += 1
                if len(rows) >= BATCH_SIZE:
                    conn.executemany('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?)', rows)
                    conn.executemany('INSERT INTO forms VALUES (?,?,?)', forms)
                    rows, forms = [], []
            conn.executemany('INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?)', rows)
            conn.executemany('INSERT INTO forms VALUES (?,?,?)', forms)
        conn.execute('CREATE INDEX idx_entries_key ON entries(key)')
        conn.execute('CREATE INDEX idx_forms_form ON forms(form)')
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp_path, db_path)
    logger.info(f"{LOG_PREFIX} Built {db_path} ({count} entries)")
    return count


def download_csv(csv_path: Optional[str] = None):
    csv_path = csv_path or CSV_PATH
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with requests.get(CSV_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(csv_path + '.part', 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(csv_path + '.part', csv_path)


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _db_lock:
        if _conn is None:
            if not os.path.exists(DB_PATH):
                if not os.path.exists(CSV_PATH):
                    raise DictionaryUnavailableError('Dictionary not installed')
                build_database()
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
        return _conn


def _find_entry(conn: sqlite3.Connection, word: str) -> Optional[sqlite3.Row]:
    rows = conn.execute('SELECT * FROM entries WHERE key = ?', (word.lower(),)).fetchall()
    if not rows:
        return None
    # Prefer exact case ("Bank" the name vs "bank"), then the more common entry
    rows.sort(key=lambda r: (r['word'] != word, r['word'] != word.lower(), -(r['collins'] or 0)))
    return rows[0]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _zh(text: str) -> str:
    from backend.services.whisper_service import convert_chinese_text
    return convert_chinese_text(text) if text else ''


def format_phonetic(phonetic: str) -> str:
    if not phonetic:
        return ''
    return '/' + phonetic.replace("'", 'ˈ').replace(',', 'ˌ').strip('/') + '/'


def parse_senses(translation: str) -> List[Dict[str, str]]:
    """'n. 银行, 堤\\n[医] 库' -> [{'pos': 'n.', 'text': '銀行、堤'}, {'pos': '[醫]', 'text': '庫'}]"""
    senses = []
    for line in (translation or '').replace('\\n', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^((?:[a-z]+\.\s*)+|\[[^\]]+\]\s*)(.*)$', line)
        pos, text = (m.group(1).strip(), m.group(2).strip()) if m else ('', line)
        text = re.sub(r'\s*,\s*', '、', text)
        senses.append({'pos': _zh(pos), 'text': _zh(text)})
    return senses


def parse_definitions(definition: str) -> List[Dict[str, str]]:
    defs = []
    for line in (definition or '').replace('\\n', '\n').split('\n'):
        m = re.match(r'^(n|v|vt|vi|a|adj|s|r|adv|prep|conj|pron|int)\.?\s+(.*)$', line.strip())
        if m:
            defs.append({'pos': m.group(1) if m.group(1).endswith('.') else m.group(1) + '.', 'text': m.group(2)})
        if len(defs) >= MAX_DEFINITIONS:
            break
    return defs


def format_forms(exchange: Dict[str, str]) -> List[Dict[str, str]]:
    return [{'label': FORM_LABELS[k], 'word': exchange[k]} for k in FORM_ORDER if exchange.get(k)]


def format_tags(tag: str, collins: int, oxford: int) -> List[str]:
    tags = [TAG_LABELS[t] for t in (tag or '').split() if t in TAG_LABELS]
    if oxford:
        tags.insert(0, '牛津3000')
    if collins:
        tags.insert(0, '★' * int(collins))
    return tags


# ---------------------------------------------------------------------------
# Online extras (examples, English definitions, sentence translation)
# ---------------------------------------------------------------------------

def fetch_free_dict(lemma: str, word: str) -> Dict[str, Any]:
    """English definitions (most common first) and example sentences from the Free Dictionary API."""
    definitions: List[Dict[str, str]] = []
    examples: List[str] = []
    try:
        r = requests.get(FREE_DICT_URL.format(requests.utils.quote(lemma)), timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            for entry in r.json():
                for meaning in entry.get('meanings', []):
                    pos = POS_ABBR.get(meaning.get('partOfSpeech', ''), meaning.get('partOfSpeech', ''))
                    for d in meaning.get("definitions", []):
                        text = (d.get('definition') or '').strip()
                        # First real definition per part of speech ("To run." just points elsewhere)
                        if text and len(text.split()) >= 3 and not any(x['pos'] == pos for x in definitions):
                            definitions.append({'pos': pos, 'text': text})
                        example = (d.get('example') or '').strip()
                        if len(example.split()) >= 4:  # skip collocation fragments like "run brandy"
                            examples.append(example)
    except (requests.RequestException, ValueError, AttributeError) as e:
        logger.debug(f"{LOG_PREFIX} free dictionary for {lemma} failed: {e}")
    forms = {word.lower(), lemma.lower()}
    examples.sort(key=lambda s: (not any(re.search(rf"\b{re.escape(f)}\b", s, re.I) for f in forms), len(s)))
    return {'definitions': definitions[:MAX_DEFINITIONS], 'examples': examples[:MAX_EXAMPLES]}


def translate_lines(lines: List[str]) -> List[str]:
    """Translate English lines to Traditional Chinese in one Google request ('' on failure)."""
    lines = [' '.join(l.split()) for l in lines]
    if not any(lines):
        return ['' for _ in lines]
    try:
        r = requests.get(GOOGLE_URL, params=[('client', 'gtx'), ('sl', 'en'), ('tl', 'zh-TW'), ('dt', 't'),
                                             ('q', '\n'.join(lines))], timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        text = ''.join(seg[0] for seg in r.json()[0] if seg and seg[0])
    except (requests.RequestException, ValueError, IndexError, TypeError) as e:
        logger.debug(f"{LOG_PREFIX} translate failed: {e}")
        return ['' for _ in lines]
    out = [_zh(t.strip()) for t in text.split('\n')]
    return out if len(out) == len(lines) else ['' for _ in lines]


def extras(word: str, lemma: str, context: str = '') -> Dict[str, Any]:
    """Online part of a lookup; every field may come back empty."""
    context_future = _pool.submit(translate_lines, [context]) if context else None
    free = fetch_free_dict(lemma or word, word)
    examples = free['examples']
    example_zh = translate_lines(examples) if examples else []
    return {
        'definitions': free['definitions'],
        'examples': [{'en': en, 'zh': zh} for en, zh in zip(examples, example_zh)],
        'context_zh': context_future.result()[0] if context_future else '',
    }


# ---------------------------------------------------------------------------
# Lookup (local)
# ---------------------------------------------------------------------------

def _is_form_note(sense: Dict[str, str]) -> bool:
    return bool(FORM_NOTE_RE.search(sense['text']))


def lookup(word: str) -> Optional[Dict[str, Any]]:
    """Local dictionary entry for `word`, with its lemma's senses for inflected forms; None if unknown."""
    conn = _get_conn()
    entry = _find_entry(conn, word)
    lemma_entry, form_kinds = entry, []

    if entry:
        exchange = parse_exchange(entry['exchange'])
        if exchange.get('0') and exchange['0'].lower() != word.lower():
            lemma_entry = _find_entry(conn, exchange['0']) or entry
            form_kinds = [k for k in exchange.get('1', '') if k in FORM_LABELS]
    else:
        row = conn.execute('SELECT lemma, kind FROM forms WHERE form = ? LIMIT 1', (word.lower(),)).fetchone()
        lemma_entry = _find_entry(conn, row['lemma']) if row else None
        if not lemma_entry:
            return None
        form_kinds = [row['kind']]

    own = [s for s in parse_senses(entry['translation']) if not _is_form_note(s)] if entry else []
    lemma_senses = [s for s in parse_senses(lemma_entry['translation']) if not _is_form_note(s)]
    is_form = lemma_entry is not entry
    return {
        'word': word,
        'lemma': lemma_entry['word'],
        'form_of': (f"{lemma_entry['word']} 的{'、'.join(FORM_LABELS[k] for k in form_kinds) or '變化形'}"
                    if is_form else ''),
        # Pronunciation of the hovered form when known ("went"), else of the lemma
        'phonetic': format_phonetic((entry and entry['phonetic']) or lemma_entry['phonetic']),
        # The hovered form's own meanings ("running": 賽跑、連續的), when it has any
        'senses': own if (own and is_form) else lemma_senses,
        'lemma_senses': lemma_senses if (own and is_form) else [],
        'definitions': parse_definitions(lemma_entry['definition']),
        'forms': format_forms(parse_exchange(lemma_entry['exchange'])),
        'tags': format_tags(lemma_entry['tag'], lemma_entry['collins'], lemma_entry['oxford']),
    }
