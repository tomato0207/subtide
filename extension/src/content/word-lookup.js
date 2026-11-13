/**
 * Hover dictionary: rest the mouse on an English word for a moment to see a
 * Traditional Chinese explanation in a small box beside it.
 * Works on every page, including Subtide/YouTube subtitles (which ignore pointer events).
 * Definitions come from the backend (/api/define) via the service worker.
 */
(function () {
    'use strict';

    const DWELL_MS = 600;
    const HIDE_DELAY_MS = 250;
    const MAX_CONTEXT = 300;
    const STORAGE_KEY = 'wordLookupEnabled';
    const WORD_CHAR = /[A-Za-z'’-]/;
    const WORD_RE = /^[A-Za-z][A-Za-z'-]*[A-Za-z]$/;
    // Subtitle layers have pointer-events: none, so caret hit-testing skips them
    const SUBTITLE_SELECTOR = '.vt-text, .vt-subtitle-text, .ytp-caption-segment';

    // ---------- Pure helpers (unit tested) ----------

    /** Find the English word in `text` covering character `offset`. Returns {word, start, end} or null. */
    function extractWord(text, offset) {
        if (!text) return null;
        let start = Math.min(Math.max(offset, 0), text.length);
        let end = start;
        while (start > 0 && WORD_CHAR.test(text[start - 1])) start--;
        while (end < text.length && WORD_CHAR.test(text[end])) end++;
        // Trim quotes/hyphens at the edges
        while (start < end && /['’-]/.test(text[start])) start++;
        while (end > start && /['’-]/.test(text[end - 1])) end--;
        const word = text.slice(start, end).replace(/’/g, "'");
        if (word.length < 2 || word.length > 40 || !WORD_RE.test(word)) return null;
        return { word, start, end };
    }

    /** The sentence around `index` in `text`, collapsed and capped to MAX_CONTEXT chars. */
    function sentenceAround(text, index) {
        if (!text) return '';
        const before = text.slice(0, index);
        const after = text.slice(index);
        const startMatch = before.match(/[\s\S]*[.!?\n]\s/);
        const endMatch = after.match(/[.!?\n](\s|$)/);
        const start = startMatch ? startMatch[0].length : 0;
        const end = endMatch ? index + endMatch.index + 1 : text.length;
        let sentence = text.slice(start, end).replace(/\s+/g, ' ').trim();
        if (sentence.length > MAX_CONTEXT) {
            const center = index - start;
            const from = Math.max(0, Math.min(center - MAX_CONTEXT / 2, sentence.length - MAX_CONTEXT));
            sentence = sentence.slice(from, from + MAX_CONTEXT);
        }
        return sentence;
    }

    /**
     * Map a viewport point to video pixels, accounting for letterboxing (object-fit: contain).
     * Returns {vx, vy} or null when the point is outside the picture.
     */
    function mapToVideo(rect, videoWidth, videoHeight, x, y) {
        if (!rect.width || !rect.height || !videoWidth || !videoHeight) return null;
        const scale = Math.min(rect.width / videoWidth, rect.height / videoHeight);
        const left = rect.left + (rect.width - videoWidth * scale) / 2;
        const top = rect.top + (rect.height - videoHeight * scale) / 2;
        const vx = (x - left) / scale;
        const vy = (y - top) / scale;
        if (vx < 0 || vy < 0 || vx >= videoWidth || vy >= videoHeight) return null;
        return { vx, vy };
    }

    /**
     * Horizontal strip of the frame around row vy to OCR: full width (so whole lines are read),
     * ~12% of the height, downscaled so the strip is at most 1920px wide.
     */
    function stripBounds(videoWidth, videoHeight, vy) {
        const height = Math.min(videoHeight, Math.max(48, Math.min(220, Math.round(videoHeight * 0.12))));
        const top = Math.min(Math.max(0, Math.round(vy - height / 2)), videoHeight - height);
        return { top, height, scale: Math.min(1, 1920 / videoWidth) };
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { extractWord, sentenceAround, mapToVideo, stripBounds };
    }
    if (typeof document === 'undefined' || typeof chrome === 'undefined' || !chrome.runtime?.id) return;
    if (window.__subtideWordLookup) return;
    window.__subtideWordLookup = true;

    // ---------- Hit testing ----------

    function rectContains(rect, x, y, pad = 0) {
        return x >= rect.left - pad && x <= rect.right + pad && y >= rect.top - pad && y <= rect.bottom + pad;
    }

    function rangeContains(range, x, y) {
        for (const rect of range.getClientRects()) {
            if (rectContains(rect, x, y, 1)) return rect;
        }
        return null;
    }

    function blockText(node) {
        let el = node.parentElement;
        // Climb to a block-ish ancestor so the context is a full sentence, not one <span>
        while (el && el.parentElement && (el.textContent || '').length < 80) {
            const display = getComputedStyle(el).display;
            if (display !== 'inline' && display !== 'contents') break;
            el = el.parentElement;
        }
        return el || node.parentElement;
    }

    function hitFromTextNode(node, offset, x, y) {
        const found = extractWord(node.data, offset);
        if (!found) return null;
        const range = document.createRange();
        range.setStart(node, found.start);
        range.setEnd(node, found.end);
        const rect = rangeContains(range, x, y);
        if (!rect) return null;

        let context = node.data;
        let index = found.start;
        const block = blockText(node);
        if (block) {
            const pre = document.createRange();
            pre.selectNodeContents(block);
            pre.setEnd(node, found.start);
            context = block.textContent || node.data;
            index = pre.toString().length;
        }
        return { word: found.word, rect, context: sentenceAround(context, index) };
    }

    function hitInSubtitles(x, y) {
        for (const el of document.querySelectorAll(SUBTITLE_SELECTOR)) {
            if (!rectContains(el.getBoundingClientRect(), x, y)) continue;
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                const re = /[A-Za-z][A-Za-z'’-]*/g;
                for (let m = re.exec(node.data); m; m = re.exec(node.data)) {
                    const hit = hitFromTextNode(node, m.index, x, y);
                    if (hit) {
                        hit.context = sentenceAround(el.textContent, el.textContent.indexOf(m[0]));
                        return hit;
                    }
                }
            }
        }
        return null;
    }

    function isEditable(el) {
        return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    }

    function wordAt(x, y) {
        const fromSubs = hitInSubtitles(x, y);
        if (fromSubs) return fromSubs;
        let node, offset;
        if (document.caretPositionFromPoint) {
            const pos = document.caretPositionFromPoint(x, y);
            if (pos) { node = pos.offsetNode; offset = pos.offset; }
        } else if (document.caretRangeFromPoint) {
            const range = document.caretRangeFromPoint(x, y);
            if (range) { node = range.startContainer; offset = range.startOffset; }
        }
        if (!node || node.nodeType !== Node.TEXT_NODE || isEditable(node.parentElement)) return null;
        if (host && host.contains(node)) return null;
        return hitFromTextNode(node, offset, x, y);
    }

    // ---------- Video frames (text burned into the video, e.g. slides) ----------

    /** Ask the browser to decode the current frame again (seek in place), e.g. after the tab was in the background. */
    function restoreFrame(video) {
        return new Promise((resolve) => {
            const done = () => { clearTimeout(timer); video.removeEventListener('seeked', done); resolve(); };
            const timer = setTimeout(done, 1500);
            video.addEventListener('seeked', done);
            video.currentTime = video.currentTime;
        });
    }

    function captureStrip(video, strip) {
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(video.videoWidth * strip.scale);
        canvas.height = Math.round(strip.height * strip.scale);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, strip.top, video.videoWidth, strip.height, 0, 0, canvas.width, canvas.height);
        // Nothing drawn (transparent) means the browser has no decoded frame to give
        const hasFrame = ctx.getImageData(canvas.width >> 1, canvas.height >> 1, 1, 1).data[3] !== 0;
        return hasFrame ? canvas : null;
    }

    async function videoHit(x, y) {
        for (const video of document.querySelectorAll('video')) {
            if (!video.videoWidth) continue;
            const point = mapToVideo(video.getBoundingClientRect(), video.videoWidth, video.videoHeight, x, y);
            if (!point) continue;
            const strip = stripBounds(video.videoWidth, video.videoHeight, point.vy);
            try {
                if (video.readyState < 2) await restoreFrame(video);
                let canvas = captureStrip(video, strip);
                if (!canvas) {
                    await restoreFrame(video);
                    canvas = captureStrip(video, strip);
                }
                if (!canvas) return null;
                return {
                    image: canvas.toDataURL('image/jpeg', 0.92),
                    x: point.vx * strip.scale,
                    y: (point.vy - strip.top) * strip.scale,
                };
            } catch (_) {
                return null; // cross-origin video: frame pixels are not readable
            }
        }
        return null;
    }

    // ---------- Box ----------

    let host = null;
    let box = null;
    let current = null; // { word, context, rect, near, key, state }
    const cache = new Map();

    const STYLE = `
        :host { all: initial; }
        .box {
            position: fixed; z-index: 2147483647; box-sizing: border-box;
            width: max-content; max-width: 360px; min-width: 200px; max-height: 70vh; overflow-y: auto;
            padding: 10px 12px;
            background: #1f2328; color: #e6edf3; border: 1px solid #3d444d; border-radius: 8px;
            box-shadow: 0 6px 24px rgba(0,0,0,.35);
            font: 14px/1.5 "Microsoft JhengHei", "PingFang TC", system-ui, sans-serif;
            pointer-events: auto; text-align: left;
        }
        .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
        .word { font-size: 17px; font-weight: 700; color: #fff; }
        .formof { color: #9da7b3; font-size: 12.5px; }
        .phon { color: #7ee2d0; font-size: 13px; }
        .speak { margin-left: auto; cursor: pointer; background: none; border: 0; color: #9da7b3; font-size: 15px; padding: 0 2px; }
        .speak:hover { color: #fff; }
        .tags { margin-top: 2px; display: flex; gap: 4px; flex-wrap: wrap; }
        .tag { font-size: 11px; color: #f0b72f; border: 1px solid #5a4a1a; border-radius: 3px; padding: 0 4px; }
        .sense { margin-top: 3px; font-size: 14.5px; }
        .pos { color: #f0b72f; font-size: 12.5px; font-style: italic; margin-right: 6px; }
        .sub { margin-top: 8px; color: #9da7b3; font-size: 12px; }
        .forms { margin-top: 6px; font-size: 12.5px; color: #c4ccd5; }
        .forms b { color: #9da7b3; font-weight: 400; }
        .sec { margin-top: 8px; padding-top: 6px; border-top: 1px solid #30363d; }
        .def { font-size: 12.5px; color: #c4ccd5; margin-top: 2px; }
        .ex { margin-top: 5px; font-size: 13px; }
        .zh { color: #9da7b3; font-size: 12.5px; }
        .ctx { margin-top: 6px; padding: 5px 8px; background: #2a3038; border-radius: 4px; font-size: 13px; }
        .ctx b { color: #7ee2d0; font-weight: 600; }
        .ai { margin-top: 8px; cursor: pointer; background: #2a3038; color: #e6edf3; border: 1px solid #3d444d;
              border-radius: 4px; padding: 3px 10px; font: inherit; font-size: 12.5px; }
        .ai:hover { background: #343b45; }
        .ai:disabled { cursor: default; opacity: .6; }
        .muted { color: #9da7b3; font-size: 13px; margin-top: 4px; }
    `;

    function ensureBox() {
        const container = document.fullscreenElement && document.fullscreenElement.tagName !== 'VIDEO'
            ? document.fullscreenElement : document.documentElement;
        if (!host) {
            host = document.createElement('subtide-word-lookup');
            const shadow = host.attachShadow({ mode: 'closed' });
            const style = document.createElement('style');
            style.textContent = STYLE;
            box = document.createElement('div');
            box.className = 'box';
            box.addEventListener('mouseenter', cancelHide);
            box.addEventListener('mouseleave', scheduleHide);
            shadow.append(style, box);
        }
        if (host.parentNode !== container) container.appendChild(host);
        return box;
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    function speak(text) {
        try {
            speechSynthesis.cancel();
            const u = new SpeechSynthesisUtterance(text);
            u.lang = 'en-US';
            speechSynthesis.speak(u);
        } catch (_) { /* speech not available */ }
    }

    function senseLine(sense) {
        const line = el('div', 'sense');
        if (sense.pos) line.append(el('span', 'pos', sense.pos));
        line.append(document.createTextNode(sense.text));
        return line;
    }

    function renderHead(word, dict) {
        const head = el('div', 'head');
        head.append(el('span', 'word', word));
        if (dict && dict.form_of) head.append(el('span', 'formof', `← ${dict.form_of}`));
        if (dict && dict.phonetic) head.append(el('span', 'phon', dict.phonetic));
        const btn = el('button', 'speak', '🔊');
        btn.title = '發音';
        btn.addEventListener('click', () => speak(word));
        head.append(btn);
        return head;
    }

    function renderDict(dict) {
        const frag = document.createDocumentFragment();
        if (dict.tags && dict.tags.length) {
            const tags = el('div', 'tags');
            dict.tags.forEach(t => tags.append(el('span', 'tag', t)));
            frag.append(tags);
        }
        (dict.senses || []).forEach(s => frag.append(senseLine(s)));
        if (dict.lemma_senses && dict.lemma_senses.length) {
            frag.append(el('div', 'sub', `原形 ${dict.lemma}`));
            dict.lemma_senses.forEach(s => frag.append(senseLine(s)));
        }
        if (dict.forms && dict.forms.length) {
            const forms = el('div', 'forms');
            dict.forms.forEach((f, i) => {
                if (i) forms.append(document.createTextNode(' · '));
                forms.append(el('b', '', f.label + ' '), document.createTextNode(f.word));
            });
            frag.append(forms);
        }
        return frag;
    }

    function renderExtras(extras) {
        const sec = el('div', 'sec');
        (extras.definitions || []).forEach(d => {
            const line = el('div', 'def');
            line.append(el('span', 'pos', d.pos), document.createTextNode(d.text));
            sec.append(line);
        });
        (extras.examples || []).forEach(ex => {
            const block = el('div', 'ex');
            block.append(el('div', '', ex.en));
            if (ex.zh) block.append(el('div', 'zh', ex.zh));
            sec.append(block);
        });
        if (extras.context_zh) {
            const ctx = el('div', 'ctx');
            ctx.append(el('b', '', '整句：'), document.createTextNode(extras.context_zh));
            sec.append(ctx);
        }
        return sec.childNodes.length ? sec : null;
    }

    function renderAI(ai) {
        const sec = el('div', 'sec');
        if (ai.error) {
            sec.append(el('div', 'muted', ai.error));
            return sec;
        }
        const ctx = el('div', 'ctx');
        ctx.append(el('b', '', '句中意思：'), document.createTextNode(ai.in_context || '—'));
        sec.append(ctx);
        if (ai.note) sec.append(el('div', 'muted', ai.note));
        return sec;
    }

    /** Draw the box from whatever parts have arrived: state = {dict, extras, ai, aiLoading} */
    function render(state) {
        box.replaceChildren(renderHead(current.word, state.dict));
        if (!state.dict) {
            box.append(el('div', 'muted', '查詢中…'));
        } else if (state.dict.error) {
            box.append(el('div', 'muted', state.dict.error));
        } else {
            box.append(renderDict(state.dict));
        }
        if (!state.dict || state.dict.fatal) return;

        if (state.extras) {
            const sec = renderExtras(state.extras);
            if (sec) box.append(sec);
        } else {
            box.append(el('div', 'muted', '載入例句…'));
        }
        if (state.ai) {
            box.append(renderAI(state.ai));
        } else if (current.context) {
            const btn = el('button', 'ai', state.aiLoading ? '🤖 AI 分析中…（約 5 秒）' : '🤖 AI 解析句中意思');
            btn.disabled = !!state.aiLoading;
            btn.addEventListener('click', () => askAI());
            box.append(btn);
        }
    }

    function position(rect) {
        const gap = 6;
        const w = box.offsetWidth;
        const h = box.offsetHeight;
        const left = Math.min(Math.max(8, rect.left), window.innerWidth - w - 8);
        let top = rect.bottom + gap;
        if (top + h > window.innerHeight - 8) top = Math.max(8, rect.top - h - gap);
        box.style.left = `${left}px`;
        box.style.top = `${top}px`;
    }

    /** Merge new parts into the box's state, unless the user has moved on to another word. */
    function update(key, parts) {
        if (!current || current.key !== key) return false;
        current.state = { ...current.state, ...parts };
        render(current.state);
        position(current.rect);
        return true;
    }

    function friendlyError(response) {
        const msg = (response && response.error) || '';
        if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) return { error: '連不到 Subtide 後端，請先執行 start.bat', fatal: true };
        if (/Not found/i.test(msg)) return { error: '字典裡查不到這個字' };
        if (/Dictionary not installed/i.test(msg)) return { error: '字典尚未安裝，請重新執行 setup.bat', fatal: true };
        if (/Ollama/i.test(msg)) return { error: '連不到 Ollama，請確認它在背景執行' };
        if (/disabled/i.test(msg)) return { error: '後端未開啟單字查詢（WORD_LOOKUP_ENABLED）', fatal: true };
        return { error: '查詢失敗，請稍後再試' };
    }

    async function request(hit, mode, extra = {}) {
        const cacheKey = `${mode}|${hit.word.toLowerCase()}|${mode === 'dict' ? '' : hit.context}`;
        if (cache.has(cacheKey)) return cache.get(cacheKey);
        let data;
        try {
            const response = await send({
                action: 'defineWord', mode, word: hit.word, context: hit.context, ...extra,
            }, mode === 'ai' ? 120000 : 20000);
            data = response && !response.error ? response : friendlyError(response);
            if (!data.error || /查不到/.test(data.error)) cache.set(cacheKey, data);
        } catch (e) {
            data = e.message === 'timeout'
                ? { error: '後端沒有回應，請確認 start.bat 還開著' }
                : { error: '擴充已更新，請重新整理這個頁面', fatal: true };
        }
        return data;
    }

    let showCount = 0;
    async function show(hit) {
        const key = ++showCount;
        current = { ...hit, key, state: {} };
        ensureBox();
        box.style.display = 'block';
        update(key, {});

        const dict = await request(hit, 'dict');
        if (!update(key, { dict }) || dict.fatal) return;
        const extras = await request(hit, 'extras', { lemma: dict.lemma || hit.word });
        update(key, { extras: extras.error ? {} : extras });
    }

    async function askAI() {
        const hit = current;
        update(hit.key, { aiLoading: true });
        const ai = await request(hit, 'ai', { near: hit.near || '' });
        update(hit.key, { aiLoading: false, ai });
    }

    /** sendMessage that gives up after `ms`, so one lost reply can't block later lookups. */
    function send(message, ms) {
        let timer;
        const timeout = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), ms); });
        return Promise.race([chrome.runtime.sendMessage(message), timeout]).finally(() => clearTimeout(timer));
    }

    let hideTimer = null;
    function cancelHide() { clearTimeout(hideTimer); hideTimer = null; }
    function scheduleHide() {
        if (hideTimer) return;
        hideTimer = setTimeout(hide, HIDE_DELAY_MS);
    }
    function hide() {
        cancelHide();
        current = null;
        if (box) box.style.display = 'none';
    }

    // ---------- Events ----------

    let enabled = true;
    let dwellTimer = null;
    let lastX = 0;
    let lastY = 0;
    let ocrBusy = false;

    function overBox(x, y) {
        return !!(box && box.style.display !== 'none' && rectContains(box.getBoundingClientRect(), x, y));
    }

    async function lookupVideo(x, y) {
        if (ocrBusy) return;
        ocrBusy = true;
        let response = null;
        try {
            const frame = await videoHit(x, y);
            if (frame) response = await send({ action: 'ocrWord', image: frame.image, x: frame.x, y: frame.y }, 15000);
        } catch (_) {
            // extension reloaded, or no answer in time: try again on the next hover
        } finally {
            ocrBusy = false;
        }
        // Only show if the mouse is still resting where it was
        if (!response || !response.found || Math.abs(lastX - x) > 4 || Math.abs(lastY - y) > 4) return;
        const rect = { left: x - 14, right: x + 14, top: y - 16, bottom: y + 16 };
        show({ word: response.word, context: response.context || '', near: response.near || '', rect });
    }

    /**
     * The extension was reloaded or updated after this page loaded: this copy can no longer
     * reach the extension (the new version is injected separately), so remove it quietly.
     */
    function retireIfOrphaned() {
        if (chrome.runtime?.id) return false;
        clearTimeout(dwellTimer);
        document.removeEventListener('mousemove', onMouseMove);
        if (host) host.remove();
        if (toastHost) toastHost.remove();
        return true;
    }

    function onMouseMove(e) {
        if (retireIfOrphaned()) return;
        lastX = e.clientX;
        lastY = e.clientY;
        clearTimeout(dwellTimer);
        if (!enabled || e.buttons) return; // selecting text or dragging

        if (current) {
            if (overBox(lastX, lastY) || rectContains(current.rect, lastX, lastY, 3)) { cancelHide(); return; }
            scheduleHide();
        }
        dwellTimer = setTimeout(() => {
            if (overBox(lastX, lastY)) return;
            const selection = window.getSelection();
            if (selection && !selection.isCollapsed) return;
            const hit = wordAt(lastX, lastY);
            if (!hit) {
                lookupVideo(lastX, lastY);
                return;
            }
            if (current && current.word === hit.word && rectContains(current.rect, lastX, lastY, 3)) return;
            cancelHide();
            show(hit);
        }, DWELL_MS);
    }

    document.addEventListener('mousemove', onMouseMove, { passive: true });
    document.addEventListener('mousedown', (e) => {
        if (host && e.composedPath().includes(host)) return;
        clearTimeout(dwellTimer);
        hide();
    }, true);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
    // Scrolling the page hides the box; scrolling inside the box does not
    window.addEventListener('scroll', (e) => { if (e.target !== host) hide(); }, { passive: true, capture: true });
    window.addEventListener('blur', hide);
    document.addEventListener('mouseleave', () => { clearTimeout(dwellTimer); scheduleHide(); });

    chrome.storage.local.get(STORAGE_KEY, (r) => { enabled = r[STORAGE_KEY] !== false; });
    chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'local' && changes[STORAGE_KEY]) {
            enabled = changes[STORAGE_KEY].newValue !== false;
            if (!enabled) hide();
            if (window === window.top && document.visibilityState === 'visible') {
                toast(enabled ? '滑鼠查單字：已開啟' : '滑鼠查單字：已關閉（Alt+W 開啟）');
            }
        }
    });

    let toastHost = null;
    let toastTimer = null;
    function toast(text) {
        if (!toastHost) {
            toastHost = document.createElement('subtide-word-toast');
            const shadow = toastHost.attachShadow({ mode: 'closed' });
            const style = document.createElement('style');
            style.textContent = `
                .t { position: fixed; z-index: 2147483647; left: 50%; top: 24px; transform: translateX(-50%);
                     padding: 8px 16px; border-radius: 8px; background: #1f2328; color: #e6edf3;
                     border: 1px solid #3d444d; box-shadow: 0 6px 24px rgba(0,0,0,.35);
                     font: 14px/1.4 "Microsoft JhengHei", system-ui, sans-serif; pointer-events: none;
                     transition: opacity .3s; }
                .t.hidden { opacity: 0; }`;
            const t = document.createElement('div');
            t.className = 't hidden';
            shadow.append(style, t);
            toastHost._t = t;
        }
        const container = document.fullscreenElement && document.fullscreenElement.tagName !== 'VIDEO'
            ? document.fullscreenElement : document.documentElement;
        if (toastHost.parentNode !== container) container.appendChild(toastHost);
        toastHost._t.textContent = text;
        toastHost._t.classList.remove('hidden');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastHost._t.classList.add('hidden'), 1500);
    }
})();
