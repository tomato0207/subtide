"""Tests for splitting long Whisper segments into readable subtitle lines."""

import json

from backend.services import subtitle_splitter as sp
from backend.services.subtitle_splitter import display_width, split_long_segments


def _words(text, start=0.0, step=0.2, gaps=None):
    """One word per character (like Chinese Whisper output); gaps maps index -> extra pause after it."""
    words, t = [], start
    for i, ch in enumerate(text):
        words.append({'word': ch, 'start': round(t, 3), 'end': round(t + step, 3)})
        t += step + (gaps or {}).get(i, 0)
    return words


def _seg(text, **kw):
    words = _words(text, **kw)
    return {'start': words[0]['start'], 'end': words[-1]['end'], 'text': text, 'words': words, 'speaker': None}


def test_display_width():
    assert display_width('中文') == 2
    assert display_width('ab c') == 2
    assert display_width('中a') == 1.5


def test_short_segment_is_untouched():
    seg = _seg('短短一句')
    assert split_long_segments([seg], 32, 8) == [seg]


def test_long_segment_respects_limits_and_keeps_text():
    text = '目前為止我們好像只有關一個系統有沒有解有多少解我們都好像只是做了一些專有名詞的定義' * 3
    seg = _seg(text, step=0.1)
    lines = split_long_segments([seg], 20, 8)
    assert len(lines) > 1
    assert all(display_width(l['text']) <= 20 for l in lines)
    assert ''.join(l['text'] for l in lines) == text
    assert lines[0]['start'] == seg['start'] and lines[-1]['end'] == seg['end']
    assert all(a['end'] <= b['start'] + 1e-9 for a, b in zip(lines, lines[1:]))
    assert 'words' in lines[0] and 'speaker' in lines[0]


def test_time_limit_splits_slow_speech():
    lines = split_long_segments([_seg('一二三四五六七八九十', step=1.0)], 32, 4)
    assert len(lines) >= 3
    assert all(l['end'] - l['start'] <= 4 + 1e-9 for l in lines)


def test_prefers_punctuation():
    text = '第一個子句說完了，' + '然後第二個子句接著講下去還有更多內容'
    lines = split_long_segments([_seg(text)], 20, 30)
    assert lines[0]['text'] == '第一個子句說完了，'


def test_prefers_long_pause():
    text = '這是前面的一段話沒有標點' + '這是後面的另一段話也沒有標點'
    lines = split_long_segments([_seg(text, gaps={11: 0.6})], 20, 30)
    assert lines[0]['text'] == '這是前面的一段話沒有標點'


def test_prefers_connective():
    text = '我們先把方程式列出來看一看' + '然後再用高斯消去法來解它'
    lines = split_long_segments([_seg(text)], 20, 30)
    assert lines[1]['text'].startswith('然後')


def test_never_splits_inside_english_word():
    words = [{'word': w, 'start': i * 0.3, 'end': i * 0.3 + 0.25} for i, w in enumerate(
        ['這', '是', ' Lin', 'ear', ' Equ', 'ations', '的', '一', '個', '例', '子', '我', '們', '來', '看'])]
    seg = {'start': 0, 'end': words[-1]['end'], 'text': ''.join(w['word'] for w in words), 'words': words}
    lines = split_long_segments([seg], 7, 30)
    joined = [l['text'] for l in lines]
    assert not any(t.endswith('Lin') or t.endswith('Equ') for t in joined), joined


def test_uses_segment_text_not_word_conversion():
    # Words converted one by one can differ from the whole-text conversion (詞彙 vs 詞匯)
    seg = _seg('據說方程這個詞匯其實就來自於九章算術然後我們繼續往下看下一個例子')
    seg['text'] = '據說方程這個詞彙其實就來自於九章算術然後我們繼續往下看下一個例子'
    lines = split_long_segments([seg], 20, 30)
    assert '詞彙' in ''.join(l['text'] for l in lines)


def test_length_mismatch_falls_back_to_words():
    seg = _seg('一二三四五六七八九十一二三四五六七八九十')
    seg['text'] = '不同長度' * 3
    lines = split_long_segments([seg], 8, 30)
    assert ''.join(l['text'] for l in lines) == '一二三四五六七八九十一二三四五六七八九十'


def test_single_overlong_word():
    seg = {'start': 0, 'end': 10, 'text': 'x' * 100,
           'words': [{'word': 'x' * 100, 'start': 0, 'end': 9}, {'word': 'y', 'start': 9, 'end': 10}]}
    lines = split_long_segments([seg], 10, 30)
    assert [l['text'] for l in lines] == ['x' * 100, 'y']


def test_no_word_timestamps_splits_text_evenly():
    seg = {'start': 10.0, 'end': 20.0, 'text': '一' * 50}
    lines = split_long_segments([seg], 20, 30)
    assert len(lines) == 3
    assert ''.join(l['text'] for l in lines) == '一' * 50
    assert lines[0]['start'] == 10.0 and abs(lines[-1]['end'] - 20.0) < 1e-9


def test_long_duration_without_words_and_short_text_is_kept():
    seg = {'start': 0, 'end': 30, 'text': '短'}
    assert split_long_segments([seg], 20, 8) == [seg]


def test_defaults_come_from_config(monkeypatch):
    monkeypatch.setattr(sp, 'MAX_SUBTITLE_CHARS', 5)
    monkeypatch.setattr(sp, 'MAX_LINE_DURATION', 100)
    assert len(split_long_segments([_seg('一二三四五六七八九十')])) == 2


def test_resplit_cache_script(tmp_path):
    from backend.scripts import resplit_cache

    seg = _seg('一二三四五六七八九十' * 5, step=0.1)
    (tmp_path / 'vid_whisper.json').write_text(json.dumps({'segments': [seg], 'text': '', 'language': 'zh'}), encoding='utf-8')
    subs = [{'start': 0, 'end': 5000, 'text': seg['text'], 'translatedText': seg['text']}]
    (tmp_path / 'vid_translated_zh-TW.json').write_text(json.dumps({'subtitles': subs, 'source': 'whisper'}), encoding='utf-8')
    (tmp_path / 'vid_translated_en.json').write_text(json.dumps({'subtitles': [{**subs[0], 'translatedText': 'English'}]}), encoding='utf-8')
    (tmp_path / 'ok_whisper.json').write_text(json.dumps({'segments': [_seg('短')]}), encoding='utf-8')

    report = resplit_cache.resplit(str(tmp_path))

    whisper = json.loads((tmp_path / 'vid_whisper.json').read_text(encoding='utf-8'))
    translated = json.loads((tmp_path / 'vid_translated_zh-TW.json').read_text(encoding='utf-8'))
    assert len(whisper['segments']) > 1
    assert len(translated['subtitles']) == len(whisper['segments'])
    assert translated['source'] == 'whisper'
    assert translated['subtitles'][0]['translatedText'] == translated['subtitles'][0]['text']
    assert isinstance(translated['subtitles'][0]['start'], int)
    assert json.loads((tmp_path / 'vid_translated_en.json').read_text(encoding='utf-8'))['subtitles'][0]['translatedText'] == 'English'
    assert (tmp_path / '_before_split' / 'vid_whisper.json').exists()
    assert any('has translations' in r for r in report) and any('1 ->' in r for r in report)
    assert resplit_cache.resplit(str(tmp_path)) == []  # idempotent


def test_medium_pause_beats_no_pause():
    text = '這是前面的一段話沒有標點' + '這是後面的另一段話也沒有標點'
    lines = split_long_segments([_seg(text, gaps={11: 0.25})], 20, 30)
    assert lines[0]['text'] == '這是前面的一段話沒有標點'


def test_overlong_last_word_ends_its_own_line():
    seg = {'start': 0, 'end': 10, 'text': 'y' + 'x' * 100,
           'words': [{'word': 'y', 'start': 0, 'end': 1}, {'word': 'x' * 100, 'start': 1, 'end': 10}]}
    assert [l['text'] for l in split_long_segments([seg], 10, 30)] == ['y', 'x' * 100]


def test_resplit_cache_list_format(tmp_path):
    from backend.scripts import resplit_cache

    seg = _seg('一二三四五六七八九十' * 5, step=0.1)
    (tmp_path / 'v_whisper.json').write_text(json.dumps({'segments': [seg]}), encoding='utf-8')
    (tmp_path / 'v_translated_zh-TW.json').write_text(json.dumps([{'text': 'a', 'translatedText': 'a'}]), encoding='utf-8')
    resplit_cache.resplit(str(tmp_path))
    assert len(json.loads((tmp_path / 'v_translated_zh-TW.json').read_text(encoding='utf-8'))) > 1
