/* Theme before first paint: ?theme=light|dark (for sharing and screenshots), else the saved choice, else dark.
   A file rather than an inline script, so the Content-Security-Policy needs no 'unsafe-inline' for scripts. */
(function (d) {
  var t = 'dark', ok = function (v) { return v === 'light' || v === 'dark' }
  try {
    var q = new URLSearchParams(location.search).get('theme'), s = localStorage.getItem('realist-theme')
    t = ok(q) ? q : ok(s) ? s : 'dark'
  } catch (e) {}
  d.dataset.theme = t
  if (t === 'light') document.querySelector('meta[name="theme-color"]').setAttribute('content', '#eef2ef')
})(document.documentElement)
