(() => {
  'use strict';
  const panel = document.querySelector('[data-book-video]');
  if (!panel) return;
  const player = panel.querySelector('video'), note = panel.querySelector('[data-video-resume]');
  const account = panel.dataset.account, url = panel.dataset.positionUrl;
  const csrf = panel.querySelector('.video-position-auth input')?.value;
  const key = `qicko-video-position-v1:${account}:${panel.dataset.bookId}`;
  const client = Date.now().toString(36) + '_' + Math.random().toString(36).slice(2);
  let revision = null, loaded = false, interacted = false, sequence = 0, lastSaved = 0;
  let candidate = null, generation = 0, canSync = true, waiting = false;
  const read = () => { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } };
  const store = data => { try { localStorage.setItem(key, JSON.stringify(data)); } catch {} };
  const valid = data => data && data.revision === revision && Number.isFinite(data.position) && data.position > 0;
  function resume() {
    if (waiting || loaded || interacted || !valid(candidate) || !Number.isFinite(player.duration) || player.duration <= 0) return;
    loaded = true;
    const seconds = Math.min(candidate.position, player.duration);
    if (seconds >= player.duration) return;
    player.currentTime = seconds;
    note.hidden = false;
    const minutes = Math.floor(seconds/60), rest = Math.floor(seconds%60).toString().padStart(2, '0');
    note.querySelector('span').textContent = `Продолжено с ${minutes}:${rest}.`;
  }
  function save(force = false, reset = false) {
    if (!revision || (!loaded && !interacted) || !Number.isFinite(player.duration) || player.duration <= 0) return;
    if (!force && performance.now()-lastSaved < 5000) return;
    lastSaved = performance.now();
    const seconds = reset || player.ended ? 0 : player.currentTime;
    const data = {revision, position: Math.max(0, seconds), updated_at: new Date().toISOString()};
    store(data);
    if (url && csrf && canSync) {
      void fetch(url, {method:'POST', credentials:'same-origin', keepalive:true, cache:'no-store',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf},
        body:JSON.stringify({...data, duration:player.duration, account, client_id:client, sequence:++sequence})
      }).then(response => { if ([401,403].includes(response.status)) canSync = false; }).catch(() => {});
    }
  }
  panel.addEventListener('video-revision', async event => {
    if (revision === event.detail) return;
    revision = event.detail; generation++; loaded = false; interacted = false; candidate = read();
    note.hidden = true;
    const version = generation;
    waiting = false;
    if (!revision) return;
    if (url && canSync) {
      waiting = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2500);
      try {
        const response = await fetch(url, {cache:'no-store', credentials:'same-origin', signal:controller.signal});
        if ([401,403].includes(response.status)) canSync = false;
        if (response.ok) {
          const remote = await response.json();
          if (version !== generation) return;
          if (remote.account !== account) { canSync = false; return; }
          if (!valid(candidate) || Date.parse(remote.updated_at) > Date.parse(candidate.updated_at || '')) candidate = remote;
        }
      } catch {} finally { clearTimeout(timeout); }
    }
    if (version === generation) { waiting = false; resume(); }
  });
  player.addEventListener('loadedmetadata', resume);
  player.addEventListener('play', () => { interacted = true; });
  player.addEventListener('seeking', () => { interacted = true; });
  player.addEventListener('timeupdate', () => save());
  player.addEventListener('pause', () => save(true));
  player.addEventListener('seeked', () => { if (!player.seeking) save(true); });
  player.addEventListener('ended', () => { save(true, true); note.hidden = true; });
  panel.querySelector('[data-video-restart]').addEventListener('click', () => {
    loaded = true; interacted = true; player.currentTime = 0; note.hidden = true; save(true, true);
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) save(true); });
  window.addEventListener('pagehide', () => save(true));
})();
