"""Tests for the hover dictionary: AI explanation, ECDICT lookup, OCR word picking, and routes."""

import base64
import io
from unittest.mock import patch

import pytest
import requests

from backend.routes import word as word_route
from backend.services import dictionary_service as dictionary
from backend.services import ocr_service, word_service
from backend.services.word_service import define_word, normalize_word, InvalidWordError

CSV = '''word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio
go,gәu,"v. move\\nn. a time for working","vi. 去, 走\\nn. 尝试",,5,1,zk gk,1,1,p:went/d:gone/i:going/3:goes,,
went,went,,go的过去式,,,,,0,0,0:go/1:p,,
run,rʌn,"v. move fast\\nn. a score in baseball","vi. 跑, 运转\\nvt. 经营",,5,1,zk,1,1,p:ran/d:run/i:running/3:runs/s:runs,,
running,'rʌniŋ,n. the state of being in operation,"n. 赛跑\\na. 连续的",,4,1,gk,1,1,0:run/1:i,,
benchmark,'bentʃmɑ:k,n. a standard,[计] 基准程序,,1,,,0,0,s:benchmarks/d:benchmarked,,
Bank,bæŋk,,n. 班克（姓氏）,,,,,0,0,,,
bank,bæŋk,,"n. 银行, 堤",,1,1,ielts,1,1,s:banks,,
nothing,,,,,,,,0,0,,,
'''

AI_REPLY = {'word': 'running', 'in_context': '经营', 'note': '这里是 run a business 的意思', 'sentence_zh': '她经营面包店。'}


@pytest.fixture(autouse=True)
def _reset():
    word_service.clear_cache()
    yield
    word_service.clear_cache()


@pytest.fixture
def dict_db(tmp_path):
    csv_path, db_path = tmp_path / 'ecdict.csv', tmp_path / 'ecdict.db'
    csv_path.write_text(CSV, encoding='utf-8')
    dictionary.build_database(str(csv_path), str(db_path))
    with patch.object(dictionary, 'DB_PATH', str(db_path)), \
         patch.object(dictionary, 'CSV_PATH', str(csv_path)), \
         patch.object(dictionary, '_conn', None):
        yield str(db_path)


# ---------------------------------------------------------------------------
# normalize_word / AI explanation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('running', 'running'), ("'don't'", "don't"), ('  well-known ', 'well-known'), ('A', 'A'),
])
def test_normalize_word_accepts_english(raw, expected):
    assert normalize_word(raw) == expected


@pytest.mark.parametrize('raw', ['', None, '123', '中文', 'two words', 'a' * 41, '<script>'])
def test_normalize_word_rejects_invalid(raw):
    with pytest.raises(InvalidWordError):
        normalize_word(raw)


def test_define_word_converts_to_traditional_and_sends_sentence():
    with patch.object(word_service, '_chat_json', return_value=AI_REPLY) as chat:
        result = define_word('running', 'She is running a bakery.')
    assert result == {'word': 'running', 'in_context': '經營', 'note': '這裡是 run a business 的意思',
                      'sentence_zh': '她經營麵包店。'}
    assert 'running a bakery' in chat.call_args[0][1]
    assert '影片畫面' not in chat.call_args[0][1]


def test_define_word_ocr_mode_uses_corrected_word():
    with patch.object(word_service, '_chat_json', return_value={**AI_REPLY, 'word': 'ratio'}) as chat:
        result = define_word('Executiontimeratio', 'Execution time ratio', near='time|ratio')
    assert result['word'] == 'ratio'
    assert 'time|ratio' in chat.call_args[0][1] and '影片畫面' in chat.call_args[0][1]


def test_define_word_ignores_corrected_word_without_ocr():
    with patch.object(word_service, '_chat_json', return_value={**AI_REPLY, 'word': 'other'}):
        assert define_word('running', 'x')['word'] == 'running'


def test_define_word_no_context_has_no_sentence_translation():
    with patch.object(word_service, '_chat_json', return_value=AI_REPLY) as chat:
        assert define_word('running')['sentence_zh'] == ''
    assert '沒有句子' in chat.call_args[0][1]


def test_define_word_caches_and_is_bounded():
    with patch.object(word_service, 'CACHE_SIZE', 2), \
         patch.object(word_service, '_chat_json', return_value=AI_REPLY) as chat:
        define_word('run', 'ctx')
        define_word('RUN', '  ctx ')
        for w in ['one', 'two', 'three']:
            define_word(w)
    assert chat.call_count == 4
    assert len(word_service._cache) == 2


# ---------------------------------------------------------------------------
# Dictionary (ECDICT)
# ---------------------------------------------------------------------------

def test_parse_exchange():
    assert dictionary.parse_exchange('0:go/1:p/s:runs') == {'0': 'go', '1': 'p', 's': 'runs'}
    assert dictionary.parse_exchange('') == {}


def test_format_helpers():
    assert dictionary.format_phonetic("'bentʃ,mɑ:k") == '/ˈbentʃˌmɑ:k/'
    assert dictionary.format_phonetic('') == ''
    assert dictionary.parse_senses('n. 银行, 堤\\n[医] 库') == [
        {'pos': 'n.', 'text': '銀行、堤'}, {'pos': '[醫]', 'text': '庫'}]
    assert dictionary.parse_senses('沒有詞性') == [{'pos': '', 'text': '沒有詞性'}]
    assert dictionary.parse_definitions('v. move\\nn. a time\\nbad line') == [
        {'pos': 'v.', 'text': 'move'}, {'pos': 'n.', 'text': 'a time'}]
    assert dictionary.parse_definitions('v move') == [{'pos': 'v.', 'text': 'move'}]
    assert dictionary.format_tags('zk toefl xx', 3, 1) == ['★★★', '牛津3000', '國中', '托福']


def test_lookup_plain_word(dict_db):
    entry = dictionary.lookup('benchmark')
    assert entry['lemma'] == 'benchmark' and entry['form_of'] == ''
    assert entry['senses'] == [{'pos': '[計]', 'text': '基準程式'}]
    assert entry['forms'] == [{'label': '過去分詞', 'word': 'benchmarked'}, {'label': '複數', 'word': 'benchmarks'}]


def test_lookup_irregular_form_shows_lemma_senses(dict_db):
    entry = dictionary.lookup('went')
    assert entry['lemma'] == 'go'
    assert entry['form_of'] == 'go 的過去式'
    assert entry['phonetic'] == '/went/'
    assert entry['senses'][0] == {'pos': 'vi.', 'text': '去、走'}
    assert entry['lemma_senses'] == []
    assert {'label': '現在分詞／動名詞', 'word': 'going'} in entry['forms']


def test_lookup_form_with_own_meanings_shows_both(dict_db):
    entry = dictionary.lookup('running')
    assert entry['form_of'] == 'run 的現在分詞／動名詞'
    assert entry['senses'] == [{'pos': 'n.', 'text': '賽跑'}, {'pos': 'a.', 'text': '連續的'}]
    assert entry['lemma_senses'][0] == {'pos': 'vi.', 'text': '跑、運轉'}


def test_lookup_form_without_own_row_uses_forms_table(dict_db):
    entry = dictionary.lookup('benchmarks')
    assert entry['lemma'] == 'benchmark' and entry['form_of'] == 'benchmark 的複數'
    assert entry['phonetic'] == '/ˈbentʃmɑ:k/'


def test_lookup_prefers_exact_case(dict_db):
    assert dictionary.lookup('bank')['senses'][0]['text'] == '銀行、堤'
    assert dictionary.lookup('Bank')['senses'][0]['text'] == '班克（姓氏）'


def test_lookup_unknown_and_empty_rows(dict_db):
    assert dictionary.lookup('qwertyuiop') is None
    assert dictionary.lookup('nothing') is None  # row without translation is skipped


def test_lookup_builds_db_from_csv_on_first_use(tmp_path):
    csv_path = tmp_path / 'ecdict.csv'
    csv_path.write_text(CSV, encoding='utf-8')
    with patch.object(dictionary, 'DB_PATH', str(tmp_path / 'new.db')), \
         patch.object(dictionary, 'CSV_PATH', str(csv_path)), patch.object(dictionary, '_conn', None):
        assert dictionary.lookup('go')['lemma'] == 'go'
    assert (tmp_path / 'new.db').exists()


def test_lookup_without_dictionary_raises(tmp_path):
    with patch.object(dictionary, 'DB_PATH', str(tmp_path / 'x.db')), \
         patch.object(dictionary, 'CSV_PATH', str(tmp_path / 'x.csv')), patch.object(dictionary, '_conn', None):
        with pytest.raises(dictionary.DictionaryUnavailableError):
            dictionary.lookup('go')


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


FREE_DICT = [{'meanings': [
    {'partOfSpeech': 'verb', 'definitions': [
        {'definition': 'To run.'},
        {'definition': 'To move swiftly on foot.', 'example': 'run brandy'},
        {'definition': 'To manage a business.', 'example': 'She is running a small shop.'},
        {'definition': 'Second verb sense here.', 'example': 'He runs every single morning.'},
    ]},
    {'partOfSpeech': 'noun', 'definitions': [{'definition': 'An act of running fast.'}]},
]}]


def test_fetch_free_dict_filters_and_prefers_hovered_form():
    with patch.object(dictionary.requests, 'get', return_value=_Resp(200, FREE_DICT)):
        result = dictionary.fetch_free_dict('run', 'running')
    assert result['definitions'] == [{'pos': 'v.', 'text': 'To move swiftly on foot.'},
                                     {'pos': 'n.', 'text': 'An act of running fast.'}]
    assert result['examples'] == ['She is running a small shop.', 'He runs every single morning.']


@pytest.mark.parametrize('response', [_Resp(404, None), requests.ConnectionError('down')])
def test_fetch_free_dict_failures_are_empty(response):
    kwargs = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
    with patch.object(dictionary.requests, 'get', **kwargs):
        assert dictionary.fetch_free_dict('run', 'run') == {'definitions': [], 'examples': []}


def test_translate_lines():
    google = [[['她經營商店。\n', 'x'], ['他每天早上跑步。', 'y']]]
    with patch.object(dictionary.requests, 'get', return_value=_Resp(200, google)) as get:
        assert dictionary.translate_lines(['She runs a shop.', 'He runs  daily.']) == ['她經營商店。', '他每天早上跑步。']
    assert ('q', 'She runs a shop.\nHe runs daily.') in get.call_args.kwargs['params']
    assert dictionary.translate_lines(['']) == ['']


@pytest.mark.parametrize('kwargs', [
    {'side_effect': requests.Timeout('slow')},
    {'return_value': _Resp(200, [[['只有一行', 'x']]])},  # line count mismatch
])
def test_translate_lines_failures_are_blank(kwargs):
    with patch.object(dictionary.requests, 'get', **kwargs):
        assert dictionary.translate_lines(['a b', 'c d']) == ['', '']


def test_extras_combines_online_parts():
    free = {'definitions': [{'pos': 'v.', 'text': 'To manage.'}], 'examples': ['She runs a shop.']}

    def fake_translate(lines):
        return [f'譯:{l}' for l in lines]

    with patch.object(dictionary, 'fetch_free_dict', return_value=free), \
         patch.object(dictionary, 'translate_lines', side_effect=fake_translate):
        result = dictionary.extras('running', 'run', 'She is running a bakery.')
    assert result == {'definitions': free['definitions'],
                      'examples': [{'en': 'She runs a shop.', 'zh': '譯:She runs a shop.'}],
                      'context_zh': '譯:She is running a bakery.'}
    with patch.object(dictionary, 'fetch_free_dict', return_value={'definitions': [], 'examples': []}):
        assert dictionary.extras('run', 'run') == {'definitions': [], 'examples': [], 'context_zh': ''}


# ---------------------------------------------------------------------------
# OCR word picking
# ---------------------------------------------------------------------------

BOX = [[0, 0], [100, 0], [100, 20], [0, 20]]


def test_pick_box():
    other = [[0, 40], [100, 40], [100, 60], [0, 60]]
    assert ocr_service.pick_box([other, BOX], 50, 10) == (0, 0, 100, 20)
    assert ocr_service.pick_box([BOX], 104, 10) == (0, 0, 100, 20)  # just past the edge
    assert ocr_service.pick_box([BOX], 150, 10) is None
    assert ocr_service.pick_box([BOX], 50, 35) is None


def test_word_in_line_spaced_text():
    found = ocr_service.word_in_line('the system vendors', (0, 0, 180, 20), 70)
    assert found['word'] == 'system' and found['split'] == 'system'
    assert found['context'] == 'the system vendors'


def test_word_in_line_glued_text_is_split_at_cursor():
    found = ocr_service.word_in_line('Executiontimeratio', (0, 0, 180, 20), 165)
    assert found['word'] == 'Executiontimeratio'
    assert found['split'] == 'ratio'
    assert found['context'] == 'Execution time ratio'  # glued runs of 16+ letters are re-spaced
    assert ocr_service.word_in_line('timeratio', (0, 0, 90, 20), 80)['context'] == 'timeratio'
    long_line = ocr_service.word_in_line('asetofprogramsusedtoassess', (0, 0, 260, 20), 100)
    assert long_line['context'] == 'a set of programs used to assess'


def test_word_in_line_snaps_to_letters_and_rejects_non_words():
    assert ocr_service.word_in_line('ab 12', (0, 0, 50, 20), 25)['word'] == 'ab'
    assert ocr_service.word_in_line('123456', (0, 0, 60, 20), 30) is None
    assert ocr_service.word_in_line('', (0, 0, 60, 20), 30) is None
    assert ocr_service.word_in_line('abc', (10, 0, 10, 20), 10) is None


def _jpeg():
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (200, 40), 'white').save(buf, 'JPEG')
    return buf.getvalue()


class _FakeEngine:
    def __init__(self, boxes, text):
        self.boxes, self.text, self.calls = boxes, text, []

    def __call__(self, image, use_det=True, use_cls=True, use_rec=True):
        self.calls.append((use_det, use_rec, image.shape))
        if use_det:
            return self.boxes, None
        return [[self.text, 0.99]], None


def test_find_word_recognizes_only_the_line_under_cursor():
    engine = _FakeEngine([BOX, [[0, 25], [100, 25], [100, 39], [0, 39]]], 'hello world')
    with patch.object(ocr_service, '_get_engine', return_value=engine):
        found = ocr_service.find_word(_jpeg(), 80, 10)
    assert found['word'] == 'world'
    assert [c[:2] for c in engine.calls] == [(True, False), (False, True)]
    assert engine.calls[1][2][:2] == (22, 102)  # cropped to the box plus padding


def test_find_word_no_box_under_cursor():
    with patch.object(ocr_service, '_get_engine', return_value=_FakeEngine([], '')):
        assert ocr_service.find_word(_jpeg(), 80, 10) is None


def test_decode_image_rejects_bad_input():
    with pytest.raises(ValueError):
        ocr_service._decode_image(b'')
    with pytest.raises(OSError):
        ocr_service._decode_image(b'not an image')


def test_get_engine_reports_missing_package():
    with patch.object(ocr_service, '_engine', None), \
         patch.dict('sys.modules', {'rapidocr_onnxruntime': None}):
        with pytest.raises(ocr_service.OCRUnavailableError):
            ocr_service._get_engine()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest.fixture
def enabled():
    with patch.object(word_route, 'WORD_LOOKUP_ENABLED', True):
        yield


def test_define_route_dict_mode(client, enabled, dict_db):
    res = client.post('/api/define', json={'word': 'went'})
    assert res.status_code == 200 and res.get_json()['lemma'] == 'go'
    res = client.post('/api/define', json={'word': 'qwertyuiop'})
    assert res.status_code == 404 and res.get_json()['error'] == 'Not found'


def test_define_route_extras_and_ai_modes(client, enabled):
    with patch.object(dictionary, 'extras', return_value={'examples': []}) as extras:
        res = client.post('/api/define', json={'word': 'running', 'lemma': 'run', 'context': ' a  b ', 'mode': 'extras'})
    assert res.status_code == 200
    extras.assert_called_once_with('running', 'run', 'a b')
    with patch.object(word_service, '_chat_json', return_value=AI_REPLY):
        res = client.post('/api/define', json={'word': 'running', 'context': 'x', 'mode': 'ai'})
    assert res.get_json()['in_context'] == '經營'


@pytest.mark.parametrize('body, status', [
    ({'word': '你好'}, 400),
    ({'word': 'run', 'mode': 'bogus'}, 400),
])
def test_define_route_bad_requests(client, enabled, body, status):
    assert client.post('/api/define', json=body).status_code == status


def test_define_route_disabled(client):
    with patch.object(word_route, 'WORD_LOOKUP_ENABLED', False):
        assert client.post('/api/define', json={'word': 'run'}).status_code == 404
        assert client.post('/api/ocr-word', json={}).status_code == 404


@pytest.mark.parametrize('error, status', [
    (dictionary.DictionaryUnavailableError('missing'), 503),
    (requests.ConnectionError('down'), 503),
    (requests.Timeout('slow'), 502),
    (ValueError('bad json'), 502),
])
def test_define_route_errors(client, enabled, error, status):
    with patch.object(dictionary, 'lookup', side_effect=error):
        assert client.post('/api/define', json={'word': 'run'}).status_code == status


def _ocr_body():
    return {'image': 'data:image/jpeg;base64,' + base64.b64encode(b'img').decode(), 'x': 5, 'y': 6}


@pytest.mark.parametrize('found, known, expected', [
    ({'word': 'system', 'split': 'system'}, {'system'}, 'system'),
    ({'word': 'Executlon', 'split': 'cut'}, {'Execution'}, 'Execution'),
    ({'word': 'Executiontimeratio', 'split': 'ratio'}, set(), 'ratio'),
])
def test_ocr_route_picks_word(client, enabled, found, known, expected):
    found = {**found, 'context': 'ctx', 'near': 'a|b'}
    with patch.object(ocr_service, 'find_word', return_value=found) as find, \
         patch.object(dictionary, 'lookup', side_effect=lambda w: {'lemma': w} if w in known else None):
        res = client.post('/api/ocr-word', json=_ocr_body())
    assert res.get_json() == {'found': True, 'word': expected, 'context': 'ctx', 'near': 'a|b'}
    assert find.call_args[0] == (b'img', 5.0, 6.0)


def test_ocr_route_without_dictionary_uses_split(client, enabled):
    found = {'word': 'Executiontimeratio', 'split': 'ratio', 'context': 'c', 'near': ''}
    with patch.object(ocr_service, 'find_word', return_value=found), \
         patch.object(dictionary, 'lookup', side_effect=dictionary.DictionaryUnavailableError('x')):
        assert client.post('/api/ocr-word', json=_ocr_body()).get_json()['word'] == 'ratio'


@pytest.mark.parametrize('found', [None, {'word': 'x', 'split': 'x', 'context': '', 'near': ''}])
def test_ocr_route_nothing_found(client, enabled, found):
    with patch.object(ocr_service, 'find_word', return_value=found), \
         patch.object(dictionary, 'lookup', return_value=None):
        assert client.post('/api/ocr-word', json=_ocr_body()).get_json() == {'found': False}


@pytest.mark.parametrize('body', [{}, {'image': '!!!', 'x': 1, 'y': 1}, {'image': 'aW1n', 'x': 'a', 'y': 1}])
def test_ocr_route_bad_requests(client, enabled, body):
    assert client.post('/api/ocr-word', json=body).status_code == 400


@pytest.mark.parametrize('error, status', [
    (ocr_service.OCRUnavailableError('missing'), 503),
    (ValueError('too large'), 400),
])
def test_ocr_route_errors(client, enabled, error, status):
    with patch.object(ocr_service, 'find_word', side_effect=error):
        assert client.post('/api/ocr-word', json=_ocr_body()).status_code == status


# ---------------------------------------------------------------------------
# Remaining edge cases
# ---------------------------------------------------------------------------

def test_build_database_batches_and_replaces_stale_tmp(tmp_path):
    csv_path, db_path = tmp_path / 'e.csv', tmp_path / 'e.db'
    csv_path.write_text(CSV, encoding='utf-8')
    (tmp_path / 'e.db.tmp').write_text('stale')
    with patch.object(dictionary, 'BATCH_SIZE', 2):
        assert dictionary.build_database(str(csv_path), str(db_path)) == 7
    assert not (tmp_path / 'e.db.tmp').exists()
    with patch.object(dictionary, 'DB_PATH', str(db_path)), patch.object(dictionary, '_conn', None):
        assert dictionary.lookup('benchmarks')['lemma'] == 'benchmark'


def test_download_csv(tmp_path):
    class Stream(_Resp):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, size):
            return [b'word,', b'phonetic\n']

    target = tmp_path / 'sub' / 'ecdict.csv'
    with patch.object(dictionary.requests, 'get', return_value=Stream()):
        dictionary.download_csv(str(target))
    assert target.read_bytes() == b'word,phonetic\n'


def test_senses_and_definitions_skip_blank_lines_and_cap():
    assert dictionary.parse_senses('n. 银行\n\n') == [{'pos': 'n.', 'text': '銀行'}]
    many = '\n'.join(f'n. sense {i}' for i in range(10))
    assert len(dictionary.parse_definitions(many)) == dictionary.MAX_DEFINITIONS


def test_get_engine_creates_once():
    class FakeRapid:
        created = 0

        def __init__(self, **kwargs):
            FakeRapid.created += 1
            self.kwargs = kwargs

    import types
    module = types.SimpleNamespace(RapidOCR=FakeRapid)
    with patch.object(ocr_service, '_engine', None), patch.dict('sys.modules', {'rapidocr_onnxruntime': module}):
        first = ocr_service._get_engine()
        assert ocr_service._get_engine() is first
    assert FakeRapid.created == 1
    assert first.kwargs['det_limit_type'] == 'max'


def test_split_at_edge_cases():
    with patch('wordninja.split', return_value=['zz', 'time', 'ratio']):
        assert ocr_service.split_at('timeratio', 6) == 'ratio'  # unmatched piece skipped
    with patch('wordninja.split', return_value=['time']):
        assert ocr_service.split_at('timeratio', 6) == 'timeratio'  # offset past all pieces
