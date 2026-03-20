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
      '.search-item{padding:10px 18px;font-size:13px;cursor:pointer;transition:background 0.15s;display:flex;justify-content:space-between;}' +
      '.search-item:hover,.search-item.sel{background:var(--bg-alt);}' +
      '.search-item .si-m{font-family:"DM Mono",monospace;font-size:10px;color:var(--ink-soft);}';
    document.head.appendChild(s);
  }

  var apiBase =
    typeof API_URL !== 'undefined'
      ? API_URL
      : window.location.hostname === 'localhost'
        ? 'http://localhost:8000'
        : 'https://tennisiq.up.railway.app';

  var timer = null,
    results = [],
    idx = -1;

  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(function () {
      fetch(apiBase + '/api/v2/search?q=' + encodeURIComponent(q))
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (data) {
          results = Array.isArray(data) ? data : [];
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

  function render() {
    if (!results.length) { close(); return; }
    dd.innerHTML = results.slice(0, 10).map(function (p, i) {
      return '<div class="search-item' + (i === idx ? ' sel' : '') + '" data-name="' + p.name + '">' +
        p.name + '<span class="si-m">' + (p.matches || '') + ' matches</span></div>';
    }).join('');
    dd.classList.add('open');
    dd.querySelectorAll('.search-item').forEach(function (el) {
      el.addEventListener('click', function () { go(el.dataset.name); });
    });
  }

  function close() { dd.classList.remove('open'); dd.innerHTML = ''; results = []; idx = -1; }
})();
