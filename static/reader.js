document.querySelectorAll('.print-button').forEach(button => button.addEventListener('click', () => window.print()));

const reader = document.querySelector('[data-book-reader]');
if (reader && document.querySelector('#page-only')) {
  const pageOnly = document.querySelector('#page-only');
  const toggle = document.querySelector('#focus-play');
  const previous = document.querySelector('#focus-previous');
  const next = document.querySelector('#focus-next');
  const delay = document.querySelector('#page-delay');
  const status = document.querySelector('#auto-status');
  const playback = document.querySelector('#reader-playback');
  let focusMode = false, running = false, changing = false;
  let interval = null, controller = null, controlsTimer = null;
  let generation = 0, track = 0, playingSource = null, playbackPrimed = false, previousScroll = 0;
  const observer = window.ResizeObserver ? new ResizeObserver(() => fitPage()) : {observe(){}, disconnect(){}};
  const clearTimer = () => { clearInterval(interval); interval = null; };
  const recordings = () => {
    const spread = reader.querySelector('.focus-spread');
    return focusMode && spread ? [...spread.querySelectorAll('audio')] : [...reader.querySelectorAll('.audio-panel audio')];
  };
  const activeAudio = () => recordings()[track];

  function fitPage() {
    if (!focusMode) return;
    const height = window.visualViewport && window.visualViewport.scale <= 1.01 ? window.visualViewport.height : innerHeight;
    document.body.style.setProperty('--reader-height', `${height}px`);
    const page = reader.querySelector('.focus-spread') || reader.querySelector('.reading-page');
    if (!page) return;
    const controls = document.querySelector('.focus-controls');
    const availableHeight = Math.max(100, Math.min(height-24, controls.offsetTop-12));
    if (page.classList.contains('focus-spread')) {
      const stacked = innerHeight > innerWidth;
      page.classList.toggle('stacked', stacked);
      page.style.width = stacked ? '600px' : '1200px';
      page.style.height = stacked ? '1708px' : '840px';
    } else if (page.classList.contains('pdf-leaf')) {
      page.style.width = '720px';
      page.style.maxWidth = 'none';
    } else {
      page.style.width = Math.min(900, Math.max(320, innerWidth-24)) + 'px';
    }
    const textLeaf = page.querySelector('.spread-text');
    if (textLeaf) {
      const text = textLeaf.querySelector('.story-text');
      text.style.fontSize = '18px';
      for (let size = 17; textLeaf.scrollHeight > textLeaf.clientHeight + 1 && size >= 8; size--) text.style.fontSize = `${size}px`;
    }
    const scale = Math.min(1, (innerWidth-24)/page.offsetWidth, availableHeight/page.offsetHeight);
    page.style.top = `${availableHeight/2 + 8}px`;
    page.style.setProperty('--page-scale', String(scale));
  }
  function updateControls() {
    toggle.disabled = changing;
    previous.disabled = changing || !Number(reader.dataset.focusPrevious);
    next.disabled = changing || !Number(reader.dataset.focusNext);
    document.querySelector('#focus-number').textContent = `${reader.dataset.focusLabel} / ${reader.dataset.total}`;
    toggle.classList.toggle('is-playing', running);
    toggle.setAttribute('aria-label', running ? 'Приостановить авточтение' : 'Включить авточтение');
    toggle.title = toggle.getAttribute('aria-label');
    toggle.setAttribute('aria-pressed', String(running));
  }
  function watchPage() {
    observer.disconnect();
    const original = reader.querySelector('.reading-page');
    const page = reader.querySelector('.focus-spread') || original;
    document.body.style.setProperty('--book-background', getComputedStyle(original).backgroundImage);
    observer.observe(page);
    observer.observe(document.querySelector('.focus-controls'));
    page.querySelectorAll('img').forEach(img => img.addEventListener('load', fitPage, {once:true}));
    updateControls();
    requestAnimationFrame(fitPage);
  }
  function showControls() {
    if (!focusMode) return;
    document.body.classList.add('focus-controls-visible');
    clearTimeout(controlsTimer);
    controlsTimer = setTimeout(() => document.body.classList.remove('focus-controls-visible'), 3000);
  }
  function stop(message = 'Авточтение приостановлено. Можно листать вручную.') {
    running = false;
    generation++;
    clearTimer();
    if (controller) controller.abort();
    controller = null;
    changing = false;
    recordings().forEach(audio => audio.pause());
    playback.pause();
    status.textContent = message;
    status.classList.toggle('sr-only', message === 'Авточтение приостановлено. Можно листать вручную.');
    updateControls();
    showControls();
  }
  async function start() {
    if (changing) return;
    running = true;
    generation++;
    status.classList.add('sr-only');
    if (!activeAudio()) primePlayback();
    updateControls();
    if (!Number(reader.dataset.focusNext) && status.textContent.startsWith('Книга закончилась')) {
      await turnPage(1);
    } else await playPage();
  }
  function setFocus(enabled) {
    if (enabled) previousScroll = window.scrollY;
    if (!enabled) stop();
    else reader.querySelectorAll('audio').forEach(audio => audio.pause());
    focusMode = enabled;
    if (window.bookScreenAwake) window.bookScreenAwake(enabled);
    document.body.classList.toggle('book-focus', enabled);
    document.documentElement.classList.toggle('reader-focus', enabled);
    pageOnly.setAttribute('aria-pressed', String(enabled));
    track = 0;
    playingSource = null;
    if (enabled) {
      window.scrollTo(0, 0);
      watchPage();
      showControls();
      document.activeElement.blur();
      void start();
    } else {
      observer.disconnect();
      document.body.style.removeProperty('--book-background');
      clearTimeout(controlsTimer);
      document.body.classList.remove('focus-controls-visible');
      pageOnly.focus();
      window.scrollTo(0, previousScroll);
    }
  }
  async function advance() {
    const number = Number(reader.dataset.focusNext);
    if (!number) { stop('Книга закончилась. Нажмите «Авточтение», чтобы начать сначала.'); return; }
    await turnPage(number);
  }
  async function turnPage(number) {
    if (changing || number < 1 || number > Number(reader.dataset.total)) return;
    changing = true;
    status.classList.add('sr-only');
    clearTimer();
    const version = ++generation;
    playingSource = null;
    playback.pause();
    recordings().forEach(audio => audio.pause());
    updateControls();
    status.textContent = 'Открываем страницу…';
    controller = new AbortController();
    const url = new URL(location.href);
    url.searchParams.set('page', String(number));
    url.hash = '';
    try {
      const response = await fetch(url, {cache:'no-store', signal:controller.signal, headers:{'X-Requested-With':'book-reader'}});
      if (!response.ok) throw new Error('page-unavailable');
      const html = new DOMParser().parseFromString(await response.text(), 'text/html');
      const incoming = html.querySelector('[data-book-reader]');
      if (!incoming || !incoming.querySelector('.reading-sheet')) throw new Error('page-unavailable');
      if (version !== generation) return;
      for (const selector of ['.reading-sheet', '.audio-panel', '.page-navigation', '.contents']) {
        reader.querySelector(selector).replaceWith(incoming.querySelector(selector));
      }
      for (const key of ['number','total','focusPrevious','focusNext','focusLabel']) reader.dataset[key] = incoming.dataset[key];
      history.replaceState(null, '', url);
      changing = false;
      controller = null;
      track = 0;
      if (focusMode) watchPage();
      updateControls();
      showControls();
      if (running) await playPage();
      else status.textContent = 'Ручной просмотр. Нажмите «Авточтение» для воспроизведения.';
    } catch (error) {
      if (version === generation) stop('Не удалось открыть страницу. Проверьте соединение и повторите.');
    }
  }
  async function playPage() {
    clearTimer();
    if (!running) return;
    const recording = activeAudio();
    if (recording) {
      const version = generation;
      if (playingSource !== recording) {
        playingSource = recording;
        playback.src = recording.src;
      } else if (playback.ended) playback.currentTime = 0;
      if (playback.error) playback.load();
      status.textContent = 'Слушаем страницу. Разворот сменится после окончания записи.';
      try { await playback.play(); playbackPrimed = true; }
      catch (error) { if (version === generation) stop('Нажмите «Авточтение», чтобы запустить запись.'); }
    } else {
      const seconds = Math.max(3, Math.min(300, Number(delay.value) || 15));
      delay.value = String(seconds);
      const until = performance.now() + seconds*1000;
      const tick = () => {
        const left = Math.max(0, Math.ceil((until-performance.now())/1000));
        status.textContent = `Без аудио. Следующий разворот через ${left} сек.`;
        if (!left) { clearTimer(); void advance(); }
      };
      interval = setInterval(tick, 250);
      tick();
    }
  }
  function primePlayback() {
    if (playbackPrimed || playingSource) return;
    // Authorize this same player during a user gesture even on a silent cover.
    playback.src = playback.dataset.silence;
    void playback.play().then(() => {
      playbackPrimed = true;
      if (!playingSource) { playback.pause(); playback.removeAttribute('src'); playback.load(); }
    }).catch(() => {});
  }
  playback.addEventListener('ended', () => {
    if (!running || changing || !playingSource || !playback.ended) return;
    track++;
    if (track < recordings().length) void playPage();
    else void advance();
  });
  playback.addEventListener('pause', () => {
    if (running && !changing && playingSource && playback.paused && !playback.ended) stop();
  });
  playback.addEventListener('error', () => {
    if (running && !changing && playingSource && playback.error && playback.currentSrc === playingSource.src) {
      stop('Не удалось воспроизвести запись. Можно продолжить вручную.');
    }
  });
  function manual(number) {
    if (changing || !number) return;
    if (running) primePlayback();
    void turnPage(number);
  }
  pageOnly.addEventListener('click', () => setFocus(!focusMode));
  const exitReader = () => {
    if (reader.dataset.shelfReturn) location.assign(reader.dataset.shelfReturn);
    else if (reader.dataset.app === 'true') location.assign('/app/?device=' + encodeURIComponent(reader.dataset.device));
    else setFocus(false);
  };
  document.querySelector('#focus-exit').addEventListener('click', exitReader);
  toggle.addEventListener('click', () => { if (running) stop(); else void start(); showControls(); });
  previous.addEventListener('click', () => manual(Number(reader.dataset.focusPrevious)));
  next.addEventListener('click', () => manual(Number(reader.dataset.focusNext)));
  document.addEventListener('pointermove', showControls);
  reader.addEventListener('pointerdown', showControls);
  window.addEventListener('resize', fitPage);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', fitPage);
  if (window.visualViewport) window.visualViewport.addEventListener('scroll', fitPage);
  // Horizontal gestures turn one page; vertical scroll, zoom and controls stay native.
  let gesture = null;
  const canSwipe = target => target instanceof Element && target.closest('.reading-sheet') &&
    !target.closest('a,button,input,textarea,select,audio,video,[contenteditable]') &&
    (!window.visualViewport || window.visualViewport.scale <= 1.01) &&
    !changing && window.getSelection()?.isCollapsed !== false;
  const beginSwipe = (target, x, y, id) => {
    gesture = canSwipe(target) && x >= 20 && x <= innerWidth-20 ?
      {x, y, id, time:performance.now()} : null;
  };
  const finishSwipe = (target, x, y, id) => {
    const start = gesture;
    gesture = null;
    if (!start || start.id !== id || !canSwipe(target) || performance.now()-start.time > 1500) return;
    const dx = x-start.x, dy = y-start.y;
    if (Math.abs(dx) < Math.max(44, Math.min(90, innerWidth*.14)) || Math.abs(dx) < Math.abs(dy)*1.8) return;
    const number = focusMode ? Number(reader.dataset[dx < 0 ? 'focusNext' : 'focusPrevious']) :
      Number(reader.dataset.number) + (dx < 0 ? 1 : -1);
    if (number >= 1 && number <= Number(reader.dataset.total)) manual(number);
  };
  if (window.PointerEvent) {
    reader.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch' && event.pointerType !== 'pen') return;
      if (!event.isPrimary) { gesture = null; return; }
      beginSwipe(event.target, event.clientX, event.clientY, event.pointerId);
    });
    document.addEventListener('pointerdown', event => { if (event.pointerType === 'touch' && !event.isPrimary) gesture = null; }, true);
    reader.addEventListener('pointerup', event => {
      if (event.pointerType === 'touch' || event.pointerType === 'pen') {
        finishSwipe(event.target, event.clientX, event.clientY, event.pointerId);
      }
    });
    reader.addEventListener('pointercancel', () => { gesture = null; });
  } else {
    reader.addEventListener('touchstart', event => {
      if (event.touches.length !== 1) { gesture = null; return; }
      const touch = event.touches[0]; beginSwipe(event.target, touch.clientX, touch.clientY, touch.identifier);
    }, {passive:true});
    reader.addEventListener('touchend', event => {
      if (event.touches.length || event.changedTouches.length !== 1) { gesture = null; return; }
      const touch = event.changedTouches[0]; finishSwipe(event.target, touch.clientX, touch.clientY, touch.identifier);
    }, {passive:true});
    reader.addEventListener('touchmove', event => { if (event.touches.length > 1) gesture = null; }, {passive:true});
    reader.addEventListener('touchcancel', () => { gesture = null; });
  }
  document.addEventListener('keydown', event => {
    if (!focusMode || event.target.closest('input,textarea,select')) return;
    if (event.key === 'Escape') { event.preventDefault(); exitReader(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); manual(Number(reader.dataset.focusPrevious)); }
    if (event.key === 'ArrowRight') { event.preventDefault(); manual(Number(reader.dataset.focusNext)); }
    if (event.code === 'Space' && !event.target.closest('button,a')) { event.preventDefault(); toggle.click(); }
  });
  delay.addEventListener('change', () => { if (running && !activeAudio() && !changing) void playPage(); });
  reader.addEventListener('click', event => {
    const link = event.target.closest('.contents a, .page-navigation a');
    if (!link || !focusMode) return;
    const url = new URL(link.href);
    const number = Number(url.searchParams.get('page'));
    if (url.pathname === location.pathname && number >= 1 && number <= Number(reader.dataset.total)) {
      event.preventDefault(); manual(number);
    }
  });
  window.addEventListener('pagehide', () => stop());
  window.addEventListener('pageshow', () => { if (focusMode && window.bookScreenAwake) window.bookScreenAwake(true); });
  if (reader.dataset.autostart === 'true') setFocus(true);
}
