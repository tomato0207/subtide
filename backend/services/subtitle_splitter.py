"""
Split long Whisper segments into readable subtitle lines.

Whisper often returns 10-30 s segments, and Chinese output frequently has no
punctuation, so a single segment can fill the whole screen. Using per-word
timestamps, each segment is cut into lines of at most MAX_CHARS display units
(CJK character = 1, other characters = 0.5) and MAX_DURATION seconds, preferring
to cut after punctuation, then at the longest pause between words.
"""

import re
from typing import Any, Dict, List

from backend.config import MAX_SUBTITLE_CHARS, MAX_LINE_DURATION

PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:…]\s*$")
CJK_RE = re.compile(r"[　-鿿가-힯＀-￯]")
# Don't cut so early that the first line becomes a fragment
MIN_FILL = 0.35
# Spoken-Chinese connectives that usually start a new clause: good places to start a new line
CONNECTIVES = ('那', '然後', '所以', '但是', '可是', '因為', '如果', '其實', '而且', '或者',
               '接下來', '也就是', '不過', '因此', '另外', '首先', '最後')


def display_width(text: str) -> float:
    return sum(1 if CJK_RE.match(ch) else 0.5 for ch in text)


def _join(words: List[Dict[str, Any]]) -> str:
    return ''.join(w.get('word', '') for w in words).strip()


def _break_quality(words: List[Dict[str, Any]], i: int) -> float:
    """How natural it is to end a line after words[i]."""
    if PUNCT_RE.search(words[i].get('word', '')):
        return 3
    gap = words[i + 1].get('start', 0) - words[i].get('end', 0)
    if gap >= 0.4:
        return 2.5
    following = ''.join(w.get('word', '') for w in words[i + 1:i + 4]).lstrip()
    if following.startswith(CONNECTIVES):
        return 2
    if gap >= 0.2:
        return 1.5
    # Never split an English word in two (tokens without a leading space continue a word)
    nxt = words[i + 1].get('word', '')
    if nxt[:1].isascii() and nxt[:1].isalpha() and words[i].get('word', '')[-1:].isascii():
        return -5
    return 0


def _best_cut(words: List[Dict[str, Any]], max_chars: float, max_index: int) -> int:
    """
    Index after which to cut `words` (which overflow the limits). The first line stays within
    max_chars and words[:max_index + 1]; among those cuts, prefer natural breaks and balanced lines.
    """
    widths, total = [], 0.0
    for w in words:
        total += display_width(w.get('word', ''))
        widths.append(total)
    # Two lines' worth or less: aim for halves; otherwise fill the line
    ideal = total / 2 if total <= 2 * max_chars else max_chars
    # The time limit may end the line earlier than the width does
    ideal = min(ideal, widths[min(max_index, len(widths) - 1)])
    best, best_score = 0, None
    for i in range(0, min(max_index, len(words) - 2) + 1):
        if widths[i] > max_chars:
            break
        if widths[i] < MIN_FILL * min(ideal, max_chars) and i < max_index:
            continue
        score = _break_quality(words, i) - 2 * abs(widths[i] - ideal) / max_chars
        if best_score is None or score > best_score:
            best, best_score = i, score
    return best


def _line(words: List[Dict[str, Any]], segment: Dict[str, Any], start=None, end=None) -> Dict[str, Any]:
    line = {k: v for k, v in segment.items() if k not in ('words', 'text', 'start', 'end')}
    line.update({
        'start': words[0].get('start', start) if start is None else start,
        'end': words[-1].get('end', end) if end is None else end,
        'text': _join(words),
        'words': words,
    })
    return line


def _split_by_words(segment: Dict[str, Any], max_chars: float, max_duration: float) -> List[Dict[str, Any]]:
    words = [w for w in segment['words'] if w.get('word')]
    lines, rest = [], words
    while rest:
        if display_width(_join(rest)) <= max_chars and rest[-1]['end'] - rest[0]['start'] <= max_duration:
            lines.append(rest)
            break
        if len(rest) == 1:
            lines.append(rest)
            break
        # Words that still fit the time limit, counted from the start of this line
        in_time = sum(1 for w in rest if w['end'] - rest[0]['start'] <= max_duration)
        cut = _best_cut(rest, max_chars, max(0, in_time - 1))
        lines.append(rest[:cut + 1])
        rest = rest[cut + 1:]

    out = [_line(ws, segment) for ws in lines]
    _use_segment_text(out, segment)
    # Keep the segment's outer boundaries (already refined upstream)
    out[0]['start'] = segment['start']
    out[-1]['end'] = segment['end']
    return out


def _use_segment_text(lines: List[Dict[str, Any]], segment: Dict[str, Any]):
    """
    Take each line's text from the segment text rather than its joined words: the segment text
    was converted to Traditional Chinese as a whole, with context, while words were converted one
    by one (詞彙 can become 詞匯). Only possible when the two have the same length.
    """
    words_text = ''.join(w.get('word', '') for line in lines for w in line['words'])
    full = segment.get('text', '')
    offset = len(words_text) - len(words_text.lstrip())
    if len(words_text.strip()) != len(full.strip()):
        return
    full = full.strip()
    pos = 0
    for line in lines:
        n = len(''.join(w.get('word', '') for w in line['words']))
        start = max(0, pos - offset)
        line['text'] = full[start:pos - offset + n].strip()
        pos += n


def _split_by_text(segment: Dict[str, Any], max_chars: float) -> List[Dict[str, Any]]:
    """No word timestamps: cut the text evenly and spread the time by length."""
    text = segment.get('text', '').strip()
    pieces = max(1, int(-(-display_width(text) // max_chars)))
    size = -(-len(text) // pieces)
    chunks = [text[i:i + size].strip() for i in range(0, len(text), size)]
    chunks = [c for c in chunks if c]
    duration = segment['end'] - segment['start']
    total = sum(len(c) for c in chunks) or 1
    out, t = [], segment['start']
    for c in chunks:
        end = t + duration * len(c) / total
        line = {k: v for k, v in segment.items() if k not in ('words', 'text', 'start', 'end')}
        line.update({'start': t, 'end': end, 'text': c})
        out.append(line)
        t = end
    return out


def split_long_segments(segments: List[Dict[str, Any]], max_chars: float = None,
                        max_duration: float = None) -> List[Dict[str, Any]]:
    """Return segments with every overlong one split into several lines (times in seconds)."""
    max_chars = max_chars or MAX_SUBTITLE_CHARS
    max_duration = max_duration or MAX_LINE_DURATION
    result = []
    for seg in segments:
        text = seg.get('text', '')
        too_long = display_width(text.strip()) > max_chars or seg['end'] - seg['start'] > max_duration
        if not too_long:
            result.append(seg)
        elif all(isinstance(w, dict) and 'start' in w and 'end' in w for w in seg.get('words') or [None]):
            result.extend(_split_by_words(seg, max_chars, max_duration))
        elif display_width(text) > max_chars:
            result.extend(_split_by_text(seg, max_chars))
        else:
            result.append(seg)  # long but short text and no word timing: nothing sensible to cut
    return result
