from flask import Blueprint, request, jsonify
import logging

from backend.config import SUMMARY_ENABLED
from backend.services.summary_service import get_summary_status
from backend.services.youtube_service import validate_video_id
from backend.utils.input_validation import validate_lang_code

summary_bp = Blueprint('summary', __name__)
logger = logging.getLogger('subtide')


@summary_bp.route('/api/summary', methods=['GET'])
def get_summary():
    """
    Chapter summary for a video whose subtitles are already cached.

    First call starts a background job; poll until status is 'done'.
    Statuses: queued | running (progress/total) | done (chapters, overview) | error | no_subtitles
    """
    if not SUMMARY_ENABLED:
        return jsonify({'status': 'disabled'}), 404

    video_id = request.args.get('video_id')
    lang = str(request.args.get('lang', 'zh-TW')).replace('_', '-')

    if not video_id or not validate_video_id(video_id):
        return jsonify({'error': 'Invalid video_id format'}), 400
    if not validate_lang_code(lang):
        return jsonify({'error': 'Invalid language'}), 400

    result = get_summary_status(video_id, lang)
    status_code = 404 if result['status'] == 'no_subtitles' else 200
    return jsonify(result), status_code
