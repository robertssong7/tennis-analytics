/* TennisIQ warmup helper — Session 16.2

   Two concerns, one file:

   1. Auto-retry on 503 for our own API. App Runner serves a 30-90s cold-
      start window during which every engine-dependent endpoint returns
      a 503 with "ML engine still warming up — retry in ~60s". This file
      wraps window.fetch so requests to our API automatically retry at
      2s / 5s / 10s before bubbling the failure. Non-API fetches pass
      through untouched.

   2. Top-of-page banner that flips visible while /ready is 503 and hides
      itself when /ready turns 200. Lets the user see "warming up" copy
      instead of a broken-looking page during a cold start. Skeleton
      loaders on the underlying pages stay in their loading state because
      their fetch() calls are looping silently behind this banner.

   Loaded after config.js on every page, so window.API_URL is defined by
   the time this runs.
*/

(function () {
    if (!window.API_URL) {
        console.warn('[warmup.js] API_URL not defined; loader order is wrong');
        return;
    }

    var API_HOST = (function () {
        try { return new URL(window.API_URL).host; }
        catch (e) { return null; }
    })();

    function isOurApi(input) {
        var u = typeof input === 'string' ? input : (input && input.url) || '';
        if (!u) return false;
        if (u.indexOf(window.API_URL) === 0) return true;
        if (API_HOST && u.indexOf(API_HOST) !== -1) return true;
        return false;
    }

    function sleep(ms) {
        return new Promise(function (r) { setTimeout(r, ms); });
    }

    var _retryDelaysMs = [2000, 5000, 10000];

    var _originalFetch = window.fetch.bind(window);

    window.fetch = async function (input, init) {
        if (!isOurApi(input)) {
            return _originalFetch(input, init);
        }
        // Skip retry on /warm and /ready themselves so they return their
        // intended status (the banner poller below uses /ready directly
        // and depends on seeing 503 to decide whether to keep polling).
        var url = typeof input === 'string' ? input : (input && input.url) || '';
        if (url.indexOf('/warm') !== -1 || url.indexOf('/ready') !== -1) {
            return _originalFetch(input, init);
        }
        var lastResp = null;
        for (var attempt = 0; attempt <= _retryDelaysMs.length; attempt++) {
            try {
                lastResp = await _originalFetch(input, init);
            } catch (e) {
                if (attempt === _retryDelaysMs.length) throw e;
                await sleep(_retryDelaysMs[attempt]);
                continue;
            }
            if (lastResp.status !== 503) return lastResp;
            if (attempt === _retryDelaysMs.length) return lastResp;
            await sleep(_retryDelaysMs[attempt]);
        }
        return lastResp;
    };

    // ── Warming-up banner ────────────────────────────────────────────

    var BANNER_ID = 'tiq-warm-banner';
    var BANNER_CSS = (
        '#' + BANNER_ID + '{position:fixed;top:0;left:0;right:0;z-index:9999;' +
        'background:#FFFFFF;border-bottom:1px solid #EAE3DA;color:#2C2C2C;' +
        "font-family:'DM Sans',sans-serif;font-size:13px;padding:10px 18px;" +
        'box-shadow:0 1px 4px rgba(0,0,0,0.04);display:none;align-items:center;gap:10px;}' +
        '#' + BANNER_ID + ' .dot{width:8px;height:8px;border-radius:50%;background:#0ABAB5;' +
        'animation:tiqPulse 1.2s ease-in-out infinite;}' +
        '@keyframes tiqPulse{0%,100%{opacity:0.4;}50%{opacity:1;}}' +
        '@media (prefers-reduced-motion: reduce){#' + BANNER_ID + ' .dot{animation:none;}}'
    );

    function ensureBannerInjected() {
        if (document.getElementById(BANNER_ID)) return;
        var style = document.createElement('style');
        style.textContent = BANNER_CSS;
        document.head.appendChild(style);
        var div = document.createElement('div');
        div.id = BANNER_ID;
        div.setAttribute('role', 'status');
        div.setAttribute('aria-live', 'polite');
        div.innerHTML = '<span class="dot"></span><span>TennisIQ is warming up. Data will load in 30-60 seconds.</span>';
        // Insert as the first child of body so it sits above everything.
        if (document.body.firstChild) {
            document.body.insertBefore(div, document.body.firstChild);
        } else {
            document.body.appendChild(div);
        }
    }

    function showBanner() {
        ensureBannerInjected();
        var el = document.getElementById(BANNER_ID);
        if (el) el.style.display = 'flex';
    }

    function hideBanner() {
        var el = document.getElementById(BANNER_ID);
        if (el) el.style.display = 'none';
    }

    async function checkReadyOnce() {
        try {
            var r = await _originalFetch(window.API_URL + '/ready', { cache: 'no-store' });
            return r.status === 200;
        } catch (e) {
            return false;
        }
    }

    async function pollReady() {
        // First check on page load. If already ready, banner never shows.
        if (await checkReadyOnce()) return;
        showBanner();
        // Poll every 5s for up to 5 minutes. Most cold starts finish under 3.
        for (var i = 0; i < 60; i++) {
            await sleep(5000);
            if (await checkReadyOnce()) {
                hideBanner();
                // Tell any page that wants to re-trigger data loads.
                window.dispatchEvent(new CustomEvent('tiq:ready'));
                return;
            }
        }
        // Gave up after 5 min. Leave banner visible so the user sees the
        // problem isn't transient and can decide to refresh later.
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', pollReady);
    } else {
        pollReady();
    }

    // Expose for debugging.
    window.TIQWarmup = { showBanner: showBanner, hideBanner: hideBanner, pollReady: pollReady };
})();
