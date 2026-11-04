"""Tests for chapter summary sectioning and job flow."""

import json
from unittest.mock import patch

from backend.services import summary_service
from backend.services.summary_service import split_sections, MAX_SECTION_MS


def _subs(count, duration_ms=3000, gap_ms=1000):
    subs, t = [], 0
    for i in range(count):
        subs.append({'start': t, 'end': t + duration_ms, 'text': f'句子{i}', 'translatedText': f'句子{i}'})
        t += duration_ms + gap_ms
    return subs


def test_split_sections_empty():
    assert split_sections([]) == []


def test_split_sections_covers_all_subtitles_in_order():
    subs = _subs(900)  # ~60 minutes
    sections = split_sections(subs)
    assert [s for section in sections for s in section] == subs
    assert 5 <= len(sections) <= 15


def test_split_sections_respects_max_length():
    subs = _subs(900, gap_ms=100)  # no pauses long enough to cut at
    for section in split_sections(subs):
        assert section[-1]['end'] - section[0]['start'] <= MAX_SECTION_MS * 1.5 + 4000


def test_split_sections_short_video_single_section():
    subs = _subs(20)  # ~80 seconds
    assert len(split_sections(subs)) == 1


def test_get_summary_status_no_subtitles(tmp_path):
    with patch('backend.utils.file_utils.get_cache_path', side_effect=lambda v, s: str(tmp_path / f'{v}_{s}.json')), \
         patch.object(summary_service, 'get_cache_path', side_effect=lambda v, s: str(tmp_path / f'{v}_{s}.json')):
        assert summary_service.get_summary_status('abcdefghijk', 'zh-TW')['status'] == 'no_subtitles'


def test_get_summary_status_runs_job_and_caches(tmp_path):
    path = lambda v, s: str(tmp_path / f'{v}_{s}.json')
    (tmp_path / 'abcdefghijk_translated_zh-TW.json').write_text(
        json.dumps({'subtitles': _subs(100)}), encoding='utf-8')

    sections = iter(range(100))

    def fake_chat(system, user):
        if system == summary_service.OVERVIEW_PROMPT:
            return {'overview': '总结'}
        n = next(sections) + 1
        return {'title': f'第{n}段', 'summary': f'内容{n}'}

    with patch.object(summary_service, 'get_cache_path', side_effect=path), \
         patch.object(summary_service, '_chat_json', side_effect=fake_chat), \
         patch('backend.services.whisper_service.convert_chinese_text', side_effect=lambda t: t), \
         patch('threading.Thread') as thread:
        first = summary_service.get_summary_status('abcdefghijk', 'zh-TW')
        assert first['status'] == 'queued'
        # Run the job synchronously instead of in a thread
        target, args = thread.call_args.kwargs['target'], thread.call_args.kwargs['args']
        target(*args)
        done = summary_service.get_summary_status('abcdefghijk', 'zh-TW')

    assert done['status'] == 'done'
    assert len(done['chapters']) > 1
    assert done['chapters'][0]['title'] == '第1段'
    assert done['chapters'][0]['start'] == 0
    assert done['overview'] == '总结'
