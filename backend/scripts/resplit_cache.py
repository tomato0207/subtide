"""
Re-split cached Whisper transcripts into readable subtitle lines (one-time fix for videos
transcribed before long segments were split). Originals are backed up to cache/_before_split/.

Only transcription-only subtitle caches (translatedText == text) are rebuilt; caches with real
translations are left alone and reported, since rebuilding them would drop the translation.
"""

import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.config import CACHE_DIR  # noqa: E402
from backend.services.subtitle_splitter import split_long_segments  # noqa: E402


def _load(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save(path, data, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(path))
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resplit(cache_dir=CACHE_DIR):
    backup_dir = os.path.join(cache_dir, '_before_split')
    report = []
    for whisper_path in sorted(glob.glob(os.path.join(cache_dir, '*_whisper.json'))):
        video_id = os.path.basename(whisper_path)[:-len('_whisper.json')]
        whisper = _load(whisper_path)
        segments = whisper.get('segments', [])
        new_segments = split_long_segments(segments)
        if len(new_segments) == len(segments):
            continue
        whisper['segments'] = new_segments
        _save(whisper_path, whisper, backup_dir)

        subtitles = [{
            'start': round(s['start'] * 1000),
            'end': round(s['end'] * 1000),
            'text': s['text'].strip(),
            'speaker': s.get('speaker'),
            'translatedText': s['text'].strip(),
        } for s in new_segments]
        for translated_path in glob.glob(os.path.join(cache_dir, f'{video_id}_translated_*.json')):
            data = _load(translated_path)
            subs = data.get('subtitles') if isinstance(data, dict) else data
            if any((s.get('translatedText') or '') != (s.get('text') or '') for s in subs or []):
                report.append(f'{video_id}: kept {os.path.basename(translated_path)} (has translations)')
                continue
            if isinstance(data, dict):
                data['subtitles'] = subtitles
            else:
                data = subtitles
            _save(translated_path, data, backup_dir)
        report.append(f'{video_id}: {len(segments)} -> {len(new_segments)} lines')
    return report


if __name__ == '__main__':  # pragma: no cover
    lines = resplit()
    print('\n'.join(lines) if lines else 'Nothing to re-split.')
