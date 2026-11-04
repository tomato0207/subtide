/**
 * @fileoverview Chapter summary panel shown beside the YouTube player.
 * Polls the backend /api/summary (via the service worker) after subtitles are
 * ready, then lists chapters, highlights the one being played, and seeks on click.
 */

const SUMMARY_POLL_MS = 4000;
const SUMMARY_PANEL_ID = 'vt-summary-panel';

let summaryState = {
    videoId: null,
    pollTimer: null,
    chapters: [],
    activeIndex: -1,
    video: null,
    onTimeUpdate: null,
};

function summaryMsg(key, fallback, subs) {
    return chrome.i18n.getMessage(key, subs) || fallback;
}

function formatSummaryTime(ms) {
    const total = Math.floor(ms / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = n => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function injectSummaryStyles() {
    if (document.getElementById('vt-summary-styles')) return;
    const style = document.createElement('style');
    style.id = 'vt-summary-styles';
    style.textContent = `
        #${SUMMARY_PANEL_ID} {
            margin: 0 0 16px;
            border: 1px solid var(--yt-spec-10-percent-layer, rgba(128,128,128,.25));
            border-radius: 12px;
            color: var(--yt-spec-text-primary, #0f0f0f);
            font-family: Roboto, Arial, sans-serif;
            overflow: hidden;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 12px 16px; cursor: pointer; user-select: none;
            font-size: 16px; font-weight: 600;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-toggle { font-size: 12px; color: var(--yt-spec-text-secondary, #606060); }
        #${SUMMARY_PANEL_ID}.vt-collapsed .vt-sum-body { display: none; }
        #${SUMMARY_PANEL_ID} .vt-sum-status {
            padding: 0 16px 12px; font-size: 13px; color: var(--yt-spec-text-secondary, #606060);
        }
        #${SUMMARY_PANEL_ID} .vt-sum-overview {
            margin: 0 16px 8px; padding: 10px 12px; border-radius: 8px;
            background: var(--yt-spec-badge-chip-background, rgba(128,128,128,.12));
            font-size: 13px; line-height: 1.6;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-list { max-height: 60vh; overflow-y: auto; padding: 4px 8px 8px; }
        #${SUMMARY_PANEL_ID} .vt-sum-item {
            display: grid; grid-template-columns: auto 1fr; column-gap: 10px;
            padding: 8px; border-radius: 8px; cursor: pointer;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-item:hover { background: var(--yt-spec-badge-chip-background, rgba(128,128,128,.12)); }
        #${SUMMARY_PANEL_ID} .vt-sum-item.vt-active {
            background: var(--yt-spec-badge-chip-background, rgba(128,128,128,.12));
            box-shadow: inset 3px 0 0 #3ea6ff;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-time {
            grid-row: span 2; font-size: 12px; font-variant-numeric: tabular-nums;
            color: #3ea6ff; padding-top: 2px;
        }
        #${SUMMARY_PANEL_ID} .vt-sum-title { font-size: 14px; font-weight: 500; line-height: 1.4; }
        #${SUMMARY_PANEL_ID} .vt-sum-text {
            font-size: 13px; line-height: 1.5; color: var(--yt-spec-text-secondary, #606060);
        }
    `;
    document.head.appendChild(style);
}

function createSummaryPanel() {
    const container = document.querySelector('#secondary-inner') || document.querySelector('#secondary')
        || document.querySelector('#below');
    if (!container) return null;

    injectSummaryStyles();
    const panel = document.createElement('div');
    panel.id = SUMMARY_PANEL_ID;
    panel.innerHTML = `
        <div class="vt-sum-header">
            <span>📑 ${summaryMsg('summaryTitle', 'Chapter Summary')}</span>
            <span class="vt-sum-toggle">▾</span>
        </div>
        <div class="vt-sum-body">
            <div class="vt-sum-status"></div>
            <div class="vt-sum-overview" hidden></div>
            <div class="vt-sum-list"></div>
        </div>
    `;
    panel.querySelector('.vt-sum-header').addEventListener('click', () => {
        panel.classList.toggle('vt-collapsed');
        panel.querySelector('.vt-sum-toggle').textContent = panel.classList.contains('vt-collapsed') ? '▸' : '▾';
    });
    container.prepend(panel);
    return panel;
}

function setSummaryStatus(text) {
    const statusEl = document.querySelector(`#${SUMMARY_PANEL_ID} .vt-sum-status`);
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.hidden = !text;
}

function renderSummary(data) {
    const panel = document.getElementById(SUMMARY_PANEL_ID);
    if (!panel) return;

    const overviewEl = panel.querySelector('.vt-sum-overview');
    overviewEl.textContent = data.overview || '';
    overviewEl.hidden = !data.overview;

    const list = panel.querySelector('.vt-sum-list');
    list.textContent = '';
    summaryState.chapters = data.chapters || [];
    summaryState.chapters.forEach((chapter, index) => {
        const item = document.createElement('div');
        item.className = 'vt-sum-item';
        item.dataset.index = index;

        const time = document.createElement('span');
        time.className = 'vt-sum-time';
        time.textContent = formatSummaryTime(chapter.start);
        const title = document.createElement('span');
        title.className = 'vt-sum-title';
        title.textContent = chapter.title;
        const text = document.createElement('span');
        text.className = 'vt-sum-text';
        text.textContent = chapter.summary;

        item.append(time, title, text);
        item.addEventListener('click', () => {
            const video = summaryState.video || document.querySelector('video');
            if (video) video.currentTime = chapter.start / 1000;
        });
        list.appendChild(item);
    });

    setSummaryStatus('');
    attachSummaryTracking();
}

function attachSummaryTracking() {
    const video = document.querySelector('video.html5-main-video') || document.querySelector('video');
    if (!video) return;
    detachSummaryTracking();

    summaryState.video = video;
    summaryState.activeIndex = -1;
    summaryState.onTimeUpdate = () => {
        const time = video.currentTime * 1000;
        const chapters = summaryState.chapters;
        let index = -1;
        for (let i = 0; i < chapters.length; i++) {
            if (chapters[i].start <= time) index = i;
            else break;
        }
        if (index === summaryState.activeIndex) return;

        const items = document.querySelectorAll(`#${SUMMARY_PANEL_ID} .vt-sum-item`);
        items[summaryState.activeIndex]?.classList.remove('vt-active');
        const activeItem = items[index];
        if (activeItem) {
            activeItem.classList.add('vt-active');
            activeItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        summaryState.activeIndex = index;
    };
    video.addEventListener('timeupdate', summaryState.onTimeUpdate);
    summaryState.onTimeUpdate();
}

function detachSummaryTracking() {
    if (summaryState.video && summaryState.onTimeUpdate) {
        summaryState.video.removeEventListener('timeupdate', summaryState.onTimeUpdate);
    }
    summaryState.video = null;
    summaryState.onTimeUpdate = null;
}

/**
 * Start (or resume) the chapter summary for a video once its subtitles are cached.
 * Only runs on the main watch page, not in embeds or Shorts.
 */
function startSummary(videoId, targetLanguage) {
    if (window.top !== window || location.pathname !== '/watch') return;
    if (summaryState.videoId === videoId && document.getElementById(SUMMARY_PANEL_ID)) return;

    removeSummaryPanel();
    summaryState.videoId = videoId;

    const poll = async () => {
        if (summaryState.videoId !== videoId) return;
        let data;
        try {
            data = await sendMessage({ action: 'getSummary', videoId, targetLanguage });
        } catch (error) {
            data = { status: 'error', error: error.message };
        }
        if (summaryState.videoId !== videoId) return;

        // Feature disabled on the backend, or no subtitles: show nothing
        if (!data || data.status === 'disabled' || data.status === 'no_subtitles') {
            removeSummaryPanel();
            return;
        }

        if (!document.getElementById(SUMMARY_PANEL_ID) && !createSummaryPanel()) {
            // Page layout not ready yet
            summaryState.pollTimer = setTimeout(poll, SUMMARY_POLL_MS);
            return;
        }

        if (data.status === 'done') {
            renderSummary(data);
            return;
        }
        if (data.status === 'error') {
            setSummaryStatus(summaryMsg('summaryError', `Summary failed: ${data.error || ''}`, [data.error || '']));
            return;
        }

        const progress = data.total ? ` ${data.progress || 0}/${data.total}` : '';
        setSummaryStatus(summaryMsg('summaryGenerating', 'Generating chapter summary...') + progress);
        summaryState.pollTimer = setTimeout(poll, SUMMARY_POLL_MS);
    };
    poll();
}

/**
 * Remove the panel and stop polling (called on navigation to another video).
 */
function removeSummaryPanel() {
    if (summaryState.pollTimer) {
        clearTimeout(summaryState.pollTimer);
        summaryState.pollTimer = null;
    }
    detachSummaryTracking();
    summaryState.videoId = null;
    summaryState.chapters = [];
    summaryState.activeIndex = -1;
    document.getElementById(SUMMARY_PANEL_ID)?.remove();
}
