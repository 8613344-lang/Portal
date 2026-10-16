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
    const picker = player.querySelector('.preview-picker-toggle');
    const panel = player.querySelector('.audio-preview-menu');
    if (!audio || !button || !picker || !panel || !status) return;
    const options = [...panel.querySelectorAll('.preview-option')];
    let selected = null;
    let playbackRequest = 0;

    // A fixed panel outside the hero remains visible even when its artwork is clipped.
    document.body.append(panel);
    const placePanel = () => {
      if (panel.hidden) return;
      const bounds = player.getBoundingClientRect();
      const width = Math.min(360, document.documentElement.clientWidth - 24);
      const viewportHeight = window.visualViewport?.height || window.innerHeight;
      const viewportTop = window.visualViewport?.offsetTop || 0;
      panel.style.width = `${width}px`;
      panel.style.maxHeight = `${Math.max(100, viewportHeight - 24)}px`;
      const height = panel.getBoundingClientRect().height;
      let top = bounds.bottom + 10;
      if (top + height > viewportTop + viewportHeight - 12) top = bounds.top - height - 10;
      top = Math.max(viewportTop + 12, Math.min(top, viewportTop + viewportHeight - height - 12));
      panel.style.top = `${top}px`;
      panel.style.left = `${Math.max(12, Math.min(bounds.right - width, document.documentElement.clientWidth - width - 12))}px`;
    };
    const closePanel = (restoreFocus = false) => {
      panel.hidden = true;
      picker.setAttribute('aria-expanded', 'false');
      if (restoreFocus) picker.focus({preventScroll: true});
    };
    const openPanel = (focusOption = false) => {
      panel.hidden = false;
      picker.setAttribute('aria-expanded', 'true');
      placePanel();
      if (focusOption) (selected || options.find(option => !option.disabled) || panel.querySelector('.audio-preview-close')).focus({preventScroll: true});
    };
    const setPlaying = playing => {
      player.classList.toggle('is-playing', playing);
      button.setAttribute('aria-pressed', String(playing));
      button.setAttribute('aria-label', selected ? `${playing ? 'Приостановить' : 'Слушать'}: ${selected.querySelector('strong').textContent.trim()}` : 'Выбрать вариант озвучки');
    };
    const currentSource = () => selected && audio.currentSrc === new URL(selected.dataset.audioUrl, document.baseURI).href;
    const playSelected = async () => {
      if (!selected) { openPanel(true); return; }
      const request = ++playbackRequest;
      const title = selected.querySelector('strong').textContent.trim();
      status.textContent = `Загружаем: ${title}`;
      if (audio.error) audio.load();
      try {
        await audio.play();
        if (request !== playbackRequest || !currentSource()) return;
        setPlaying(!audio.paused);
        status.textContent = title;
      } catch {
        if (request !== playbackRequest) return;
        setPlaying(false);
        status.textContent = 'Не удалось включить. Попробуйте ещё раз.';
      }
    };
    picker.addEventListener('click', () => panel.hidden ? openPanel(true) : closePanel());
    picker.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); openPanel(true); }
    });
    panel.querySelector('.audio-preview-close').addEventListener('click', () => closePanel(true));
    options.forEach(option => option.addEventListener('click', () => {
      if (option.disabled || !option.dataset.audioUrl) return;
      if (selected !== option) {
        ++playbackRequest;
        selected = option;
        audio.pause();
        setPlaying(false);
        options.forEach(item => item.setAttribute('aria-pressed', String(item === selected)));
        audio.src = selected.dataset.audioUrl;
        audio.load();
      }
      closePanel(true);
      if (audio.paused) playSelected();
    }));
    button.addEventListener('click', () => {
      if (!selected) { openPanel(true); return; }
      if (!audio.paused) { ++playbackRequest; audio.pause(); status.textContent = selected.querySelector('strong').textContent.trim(); return; }
      playSelected();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); closePanel(true); }
    });
    document.addEventListener('click', event => {
      if (!panel.hidden && !player.contains(event.target) && !panel.contains(event.target)) closePanel();
    });
    document.addEventListener('focusin', event => {
      if (!panel.hidden && !panel.contains(event.target) && !player.contains(event.target)) closePanel();
    });
    window.addEventListener('resize', placePanel);
    window.addEventListener('scroll', placePanel, {passive: true});
    window.visualViewport?.addEventListener('resize', placePanel);
    audio.addEventListener('pause', () => { if (audio.paused) setPlaying(false); });
    audio.addEventListener('ended', () => {
      if (currentSource() && audio.ended) { setPlaying(false); status.textContent = selected.querySelector('strong').textContent.trim(); }
    });
    audio.addEventListener('error', () => {
      if (currentSource() && audio.error) { setPlaying(false); status.textContent = 'Запись временно недоступна. Попробуйте ещё раз.'; }
    });
    window.addEventListener('pagehide', () => audio.pause());
  });
})();
