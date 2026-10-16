(() => {
  'use strict';
  document.documentElement.classList.add('site-js');
  const header = document.querySelector('.site-header');
  const menu = header?.querySelector('.menu-toggle');
  const setMenu = (open) => {
    header.classList.toggle('nav-open', open);
    menu.setAttribute('aria-expanded', String(open));
    menu.querySelector('.sr-only').textContent = open ? 'Закрыть меню' : 'Открыть меню';
  };
  if (menu) {
    menu.addEventListener('click', () => setMenu(menu.getAttribute('aria-expanded') !== 'true'));
    header.querySelectorAll('nav a').forEach(link => link.addEventListener('click', () => setMenu(false)));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') {
        setMenu(false);
        menu.focus();
      }
    });
    document.addEventListener('click', event => {
      if (!header.contains(event.target)) setMenu(false);
    });
  }

  document.querySelectorAll('[data-audio-preview]').forEach(player => {
    const audio = player.querySelector('audio');
    const button = player.querySelector('.preview-play');
    const status = player.querySelector('[data-audio-status]');
    if (!audio || !button) return;
    const title = status.textContent.trim();
    const setPlaying = playing => {
      player.classList.toggle('is-playing', playing);
      button.setAttribute('aria-pressed', String(playing));
      button.setAttribute('aria-label', playing ? 'Приостановить аудиопример' : 'Слушать аудиопример');
    };
    button.addEventListener('click', async () => {
      if (!audio.paused) { audio.pause(); return; }
      try { await audio.play(); }
      catch { setPlaying(false); status.textContent = 'Не удалось включить. Попробуйте ещё раз.'; }
    });
    audio.addEventListener('play', () => { setPlaying(true); status.textContent = title; });
    audio.addEventListener('pause', () => setPlaying(false));
    audio.addEventListener('ended', () => { setPlaying(false); status.textContent = title; });
    audio.addEventListener('error', () => { setPlaying(false); status.textContent = 'Запись временно недоступна'; });
    window.addEventListener('pagehide', () => audio.pause());
  });
})();
