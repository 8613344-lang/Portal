// The native Android activity also keeps the screen on, including old WebViews.
window.bookScreenAwake = (() => {
  let wanted = false, lock = null, requesting = false;
  async function refresh() {
    if (!wanted || document.visibilityState !== 'visible') {
      const previous = lock; lock = null;
      if (previous) { try { await previous.release(); } catch (_) {} }
      return;
    }
    if (lock || requesting || !navigator.wakeLock) return;
    requesting = true;
    try {
      const acquired = await navigator.wakeLock.request('screen');
      if (!wanted || document.visibilityState !== 'visible') await acquired.release();
      else { lock = acquired; acquired.addEventListener('release', () => { if (lock === acquired) lock = null; }); }
    } catch (_) { /* Unsupported contexts and battery policy must not break reading. */ }
    finally { requesting = false; }
  }
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('pagehide', () => { wanted = false; void refresh(); });
  return enabled => { wanted = enabled; void refresh(); };
})();
