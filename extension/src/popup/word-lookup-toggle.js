// Hover dictionary on/off switch; saved immediately, read by content/word-lookup.js
(function () {
    const KEY = 'wordLookupEnabled';
    const box = document.getElementById(KEY);
    if (!box) return;
    chrome.storage.local.get(KEY, (r) => { box.checked = r[KEY] !== false; });
    box.addEventListener('change', () => chrome.storage.local.set({ [KEY]: box.checked }));
})();
