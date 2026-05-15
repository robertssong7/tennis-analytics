/**
 * TennisIQ — Shared search autocomplete
 * Attaches to #sinp / #sdd on any page.
 * Self-contained: injects its own CSS, detects API_URL.
 */
(function () {
  var input = document.getElementById('sinp');
  var dd = document.getElementById('sdd');
  if (!input || !dd || input._acInit) return;
  input._acInit = true;

  // Inject dropdown item styles once
  if (!document.querySelector('style[data-ac]')) {
    var s = document.createElement('style');
    s.setAttribute('data-ac', '1');
    s.textContent =
      '.search-item{padding:12px 18px;min-height:44px;font-size:13px;cursor:pointer;transition:background 0.15s;display:flex;align-items:center;gap:10px;}' +
      '.search-item:hover,.search-item.sel{background:rgba(10,186,181,0.10);}' +
      '.search-item .si-flag{font-size:15px;line-height:1;flex:0 0 auto;}' +
      '.search-item .si-name{flex:1 1 auto;color:var(--ink,#2C2C2C);font-family:"DM Sans",system-ui,sans-serif;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
      '.search-item .si-tier{font-family:"DM Mono",monospace;font-size:10px;letter-spacing:0.04em;text-transform:uppercase;padding:2px 6px;border-radius:3px;flex:0 0 auto;}' +
      '.search-item .si-tier.t-legendary{color:#0ABAB5;border:1px solid #0ABAB5;}' +
      '.search-item .si-tier.t-gold{color:#8a6b0f;background:rgba(218,165,32,0.18);}' +
      '.search-item .si-tier.t-silver{color:#5a5b5e;background:rgba(168,169,173,0.22);}' +
      '.search-item .si-tier.t-bronze{color:#7a4a1d;background:rgba(205,127,50,0.18);}' +
      '.search-item .si-m{font-family:"DM Mono",monospace;font-size:10px;color:var(--ink-soft,#666);flex:0 0 auto;}';
    document.head.appendChild(s);
  }

  var apiBase =
    typeof API_URL !== 'undefined'
      ? API_URL
      : window.location.hostname === 'localhost'
        ? 'http://localhost:8000'
        : 'https://su7vqmgkbd.us-east-1.awsapprunner.com';

  var timer = null,
    results = [],
    idx = -1;

  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(function () {
      fetch(apiBase + '/players/search?q=' + encodeURIComponent(q))
        .then(function (r) { return r.ok ? r.json() : {results:[]}; })
        .then(function (data) {
          results = (data && Array.isArray(data.results)) ? data.results : [];
          idx = results.length > 0 ? 0 : -1;
          render();
        })
        .catch(function () { results = []; render(); });
    }, 200);
  });

  input.addEventListener('keydown', function (e) {
    if (!dd.classList.contains('open')) {
      if (e.key === 'Enter' && input.value.trim())
        location.href = 'player.html?name=' + encodeURIComponent(input.value.trim());
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(idx + 1, results.length - 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(idx - 1, 0); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (idx >= 0 && results[idx]) go(results[idx].name); }
    else if (e.key === 'Escape') close();
  });

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.search-wrap')) close();
  });

  function go(name) { location.href = 'player.html?name=' + encodeURIComponent(name); }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function render() {
    if (!results.length) { close(); return; }
    dd.innerHTML = results.slice(0, 8).map(function (p, i) {
      var tier = (p.card_tier || '').toLowerCase();
      var tierLabel = tier ? tier.charAt(0).toUpperCase() + tier.slice(1) : '';
      var tierHtml = tier
        ? '<span class="si-tier t-' + escapeHtml(tier) + '">' + escapeHtml(tierLabel) + '</span>'
        : '';
      var flagHtml = p.flag ? '<span class="si-flag">' + escapeHtml(p.flag) + '</span>' : '';
      var count = (p.elo_match_count != null) ? p.elo_match_count : 0;
      return '<div class="search-item' + (i === idx ? ' sel' : '') + '" data-name="' + escapeHtml(p.name) + '">' +
        flagHtml +
        '<span class="si-name">' + escapeHtml(p.name) + '</span>' +
        tierHtml +
        '<span class="si-m">' + count + '</span>' +
        '</div>';
    }).join('');
    dd.classList.add('open');
    dd.querySelectorAll('.search-item').forEach(function (el) {
      el.addEventListener('click', function () { go(el.dataset.name); });
    });
  }

  function close() { dd.classList.remove('open'); dd.innerHTML = ''; results = []; idx = -1; }
})();
