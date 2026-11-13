const { test } = require('node:test');
const assert = require('node:assert/strict');
const { extractWord, sentenceAround } = require('../src/content/word-lookup.js');

test('extractWord finds the word under the offset', () => {
  const text = 'She has been running a bakery.';
  assert.deepEqual(extractWord(text, 15), { word: 'running', start: 13, end: 20 });
  assert.equal(extractWord(text, 13).word, 'running'); // first letter
  assert.equal(extractWord(text, 20).word, 'running'); // caret right after the word
});

test('extractWord keeps apostrophes and hyphens inside words', () => {
  assert.equal(extractWord("I don't know", 4).word, "don't");
  assert.equal(extractWord('I don’t know', 4).word, "don't");
  assert.equal(extractWord('a well-known fact', 5).word, 'well-known');
  assert.equal(extractWord("'quoted'", 3).word, 'quoted');
});

test('extractWord rejects non-words', () => {
  assert.equal(extractWord('', 0), null);
  assert.equal(extractWord('   ', 1), null);
  assert.equal(extractWord('I am', 0), null); // single letter
  assert.equal(extractWord('中文字幕', 2), null);
  assert.equal(extractWord('version 123', 9), null);
  assert.equal(extractWord('a'.repeat(50), 3), null);
});

test('extractWord stops at CJK and digits', () => {
  assert.equal(extractWord('這是API文件', 3).word, 'API');
  assert.equal(extractWord('mp3player', 5).word, 'player');
});

test('sentenceAround returns only the sentence containing the word', () => {
  const text = 'First sentence here. The river bank was quiet! Last one?';
  assert.equal(sentenceAround(text, text.indexOf('bank')), 'The river bank was quiet!');
  assert.equal(sentenceAround(text, 2), 'First sentence here.');
  assert.equal(sentenceAround(text, text.indexOf('Last')), 'Last one?');
});

test('sentenceAround keeps decimals and collapses whitespace', () => {
  assert.equal(sentenceAround('Version 2.5 is\n\n  out', 1), 'Version 2.5 is');
  assert.equal(sentenceAround('a   b\tc', 0), 'a b c');
});

test('sentenceAround caps long text around the word', () => {
  const text = 'word '.repeat(200) + 'target ' + 'word '.repeat(200);
  const result = sentenceAround(text, text.indexOf('target'));
  assert.ok(result.length <= 300);
  assert.ok(result.includes('target'));
});

const { mapToVideo, stripBounds } = require('../src/content/word-lookup.js');

test('mapToVideo maps viewport points to video pixels', () => {
  const rect = { left: 100, top: 50, width: 960, height: 540 }; // 1920x1080 shown at half size
  assert.deepEqual(mapToVideo(rect, 1920, 1080, 100, 50), { vx: 0, vy: 0 });
  assert.deepEqual(mapToVideo(rect, 1920, 1080, 580, 320), { vx: 960, vy: 540 });
  assert.equal(mapToVideo(rect, 1920, 1080, 99, 60), null);
  assert.equal(mapToVideo(rect, 0, 0, 200, 200), null);
});

test('mapToVideo accounts for letterboxing', () => {
  // 4:3 video in a 16:9 element: bars of 120px on each side
  const rect = { left: 0, top: 0, width: 960, height: 540 };
  assert.equal(mapToVideo(rect, 720, 540, 100, 270), null); // in the left bar
  assert.deepEqual(mapToVideo(rect, 720, 540, 120, 0), { vx: 0, vy: 0 });
});

test('stripBounds stays inside the frame and caps width', () => {
  assert.deepEqual(stripBounds(1920, 1080, 540), { top: 475, height: 130, scale: 1 });
  assert.equal(stripBounds(1920, 1080, 5).top, 0);
  assert.equal(stripBounds(1920, 1080, 1079).top, 1080 - 130);
  assert.equal(stripBounds(3840, 2160, 100).height, 220);
  assert.equal(stripBounds(3840, 2160, 100).scale, 0.5);
  assert.equal(stripBounds(320, 40, 20).height, 40); // tiny video: whole frame
});
