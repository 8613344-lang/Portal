document.querySelectorAll('.print-button').forEach(button => button.addEventListener('click', () => window.print()));

const reader = document.querySelector('[data-book-reader]');
if (reader && document.querySelector('#page-only')) {
  const pageOnly = document.querySelector('#page-only');
  const toggle = document.querySelector('#focus-play');
  const previous = document.querySelector('#focus-previous');
  const next = document.querySelector('#focus-next');
  const delay = document.querySelector('#page-delay');
  const status = document.querySelector('#auto-status');
  let focusMode = false, running = false, changing = false;
  let interval = null, controller = null, controlsTimer = null;
  let generation = 0, track = 0;
  const observer = new ResizeObserver(() => fitPage());
  const clearTimer = () => { clearInterval(interval); interval = null; };
  const recordings = () => {
    const spread = reader.querySelector('.focus-spread');
    return focusMode && spread ? [...spread.querySelectorAll('audio')] : [...reader.querySelectorAll('.audio-panel audio')];
  };
  const activeAudio = () => recordings()[track];

  function fitPage() {
    if (!focusMode) return;
    const page = reader.querySelector('.focus-spread') || reader.querySelector('.reading-page');
    if (!page) return;
    const textLeaf = page.querySelector('.spread-text');
    if (textLeaf) {
      const text = textLeaf.querySelector('.story-text');
      text.style.fontSize = '18px';
      for (let size = 17; textLeaf.scrollHeight > textLeaf.clientHeight + 1 && size >= 8; size--) text.style.fontSize = `${size}px`;
    }
    const scale = Math.min(1, (innerWidth-24)/page.offsetWidth, (innerHeight-24)/page.offsetHeight);
    page.style.setProperty('--page-scale', String(scale));
  }
  function updateControls() {
    toggle.disabled = changing;
    previous.disabled = changing || !Number(reader.dataset.focusPrevious);
    next.disabled = changing || !Number(reader.dataset.focusNext);
    document.querySelector('#focus-number').textContent = `${reader.dataset.focusLabel} / ${reader.dataset.total}`;
    toggle.textContent = running ? 'Ⅱ Пауза' : '▶ Авточтение';
    toggle.setAttribute('aria-pressed', String(running));
  }
  function watchPage() {
    observer.disconnect();
    const original = reader.querySelector('.reading-page');
    const page = reader.querySelector('.focus-spread') || original;
    document.body.style.setProperty('--book-background', getComputedStyle(original).backgroundImage);
    observer.observe(page);
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
    status.textContent = message;
    updateControls();
    showControls();
  }
  async function start() {
    if (changing) return;
    running = true;
    generation++;
    updateControls();
    if (!Number(reader.dataset.focusNext) && status.textContent.startsWith('Книга закончилась')) {
      await turnPage(1);
    } else await playPage();
  }
  function setFocus(enabled) {
    if (!enabled) stop();
    else reader.querySelectorAll('audio').forEach(audio => audio.pause());
    focusMode = enabled;
    document.body.classList.toggle('book-focus', enabled);
    pageOnly.setAttribute('aria-pressed', String(enabled));
    track = 0;
    bindAudio();
    if (enabled) {
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
    clearTimer();
    const version = ++generation;
    recordings().forEach(audio => audio.pause());
    updateControls();
    status.textContent = 'Открываем страницу…';
    controller = new AbortController();
    const url = new URL(location.href);
    url.searchParams.set('page', String(number));
    url.hash = '';
    try {
      const response = await fetch(url, {cache:'no-store', signal:controller.signal});
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
      bindAudio();
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
      if (recording.ended) recording.currentTime = 0;
      status.textContent = 'Слушаем страницу. Разворот сменится после окончания записи.';
      try { await recording.play(); }
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
  function bindAudio() {
    recordings().forEach(recording => {
      if (recording.dataset.readerBound) return;
      recording.dataset.readerBound = '1';
      recording.addEventListener('ended', () => {
        if (!running || changing || recording !== activeAudio()) return;
        track++;
        if (track < recordings().length) void playPage();
        else void advance();
      });
      recording.addEventListener('pause', () => {
        if (running && !changing && !recording.ended && recording === activeAudio()) stop();
      });
      recording.addEventListener('error', () => {
        if (running && !changing && recording === activeAudio()) stop('Не удалось воспроизвести запись. Можно продолжить вручную.');
      });
    });
  }
  function manual(number) {
    if (changing || !number) return;
    stop();
    void turnPage(number);
  }
  pageOnly.addEventListener('click', () => setFocus(!focusMode));
  document.querySelector('#focus-exit').addEventListener('click', () => setFocus(false));
  toggle.addEventListener('click', () => { if (running) stop(); else void start(); showControls(); });
  previous.addEventListener('click', () => manual(Number(reader.dataset.focusPrevious)));
  next.addEventListener('click', () => manual(Number(reader.dataset.focusNext)));
  document.addEventListener('pointermove', showControls);
  reader.addEventListener('pointerdown', showControls);
  window.addEventListener('resize', fitPage);
  document.addEventListener('keydown', event => {
    if (!focusMode || event.target.closest('input,textarea,select')) return;
    if (event.key === 'Escape') { event.preventDefault(); setFocus(false); }
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
}
