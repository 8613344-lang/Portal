document.querySelectorAll('.print-button').forEach(button => button.addEventListener('click', () => window.print()));

const reader = document.querySelector('[data-book-reader]');
if (reader && document.querySelector('#auto-toggle')) {
  const toggle = document.querySelector('#auto-toggle');
  const delay = document.querySelector('#page-delay');
  const status = document.querySelector('#auto-status');
  let running = false;
  let changing = false;
  let interval = null;
  let controller = null;
  let generation = 0;
  const audio = () => reader.querySelector('audio');
  const clearTimer = () => { clearInterval(interval); interval = null; };

  function stop(message = 'Автоматическое чтение приостановлено.') {
    running = false;
    generation += 1;
    clearTimer();
    if (controller) controller.abort();
    controller = null;
    changing = false;
    toggle.textContent = '▶ Продолжить чтение';
    toggle.setAttribute('aria-pressed', 'false');
    if (audio()) audio().pause();
    status.textContent = message;
  }

  async function advance() {
    const number = Number(reader.dataset.number);
    if (number >= Number(reader.dataset.total)) {
      stop('Книга закончилась. Можно вернуться к первой странице и прочитать её снова.');
      toggle.textContent = '▶ Читать сначала';
      return;
    }
    await turnPage(number + 1);
  }

  async function turnPage(number) {
    if (changing) return;
    changing = true;
    clearTimer();
    const version = ++generation;
    if (audio()) audio().pause();
    status.textContent = 'Открываем следующую страницу…';
    controller = new AbortController();
    const url = new URL(window.location.href);
    url.searchParams.set('page', String(number));
    url.hash = '';
    try {
      const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('page-unavailable');
      const html = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = html.querySelector('[data-book-reader]');
      if (!next || !next.querySelector('.reading-page')) throw new Error('page-unavailable');
      if (version !== generation) return;
      for (const selector of ['.reading-page', '.audio-panel', '.page-navigation', '.contents']) {
        reader.querySelector(selector).replaceWith(next.querySelector(selector));
      }
      reader.dataset.number = next.dataset.number;
      reader.dataset.total = next.dataset.total;
      history.replaceState(null, '', url);
      changing = false;
      controller = null;
      bindAudio();
      reader.querySelector('.reading-page').scrollIntoView({behavior: 'smooth', block: 'start'});
      if (running) await playPage();
    } catch (error) {
      if (version !== generation) return;
      stop('Не удалось открыть страницу. Проверьте соединение и доступ к книге, затем повторите.');
    }
  }

  async function playPage() {
    clearTimer();
    if (!running) return;
    const recording = audio();
    if (recording) {
      const version = generation;
      if (recording.ended) recording.currentTime = 0;
      status.textContent = 'Слушаем страницу. Следующая откроется после окончания записи.';
      try { await recording.play(); }
      catch (error) {
        if (version === generation) stop('Запись не запустилась. Нажмите «Продолжить чтение» или проверьте аудиофайл.');
      }
    } else {
      const seconds = Math.max(3, Math.min(300, Number(delay.value) || 15));
      delay.value = String(seconds);
      const until = performance.now() + seconds * 1000;
      const tick = () => {
        const left = Math.max(0, Math.ceil((until - performance.now()) / 1000));
        status.textContent = `Страница без аудио. Следующая через ${left} сек.`;
        if (left === 0) { clearTimer(); void advance(); }
      };
      interval = setInterval(tick, 250);
      tick();
    }
  }

  function bindAudio() {
    const recording = audio();
    if (!recording) return;
    recording.addEventListener('ended', () => { if (running && !changing) void advance(); });
    recording.addEventListener('pause', () => {
      if (running && !changing && !recording.ended) stop();
    });
    recording.addEventListener('error', () => {
      if (running && !changing) stop('Не удалось воспроизвести запись. Проверьте файл или продолжите вручную.');
    });
  }
  bindAudio();
  toggle.addEventListener('click', async () => {
    if (running) { stop(); return; }
    running = true;
    generation += 1;
    toggle.textContent = 'Ⅱ Пауза';
    toggle.setAttribute('aria-pressed', 'true');
    if (Number(reader.dataset.number) >= Number(reader.dataset.total) &&
        status.textContent.startsWith('Книга закончилась')) await turnPage(1);
    else await playPage();
  });
  delay.addEventListener('change', () => { if (running && !audio() && !changing) void playPage(); });
  reader.addEventListener('click', event => {
    const link = event.target.closest('.contents a, .page-navigation a');
    if (!link || !running) return;
    const url = new URL(link.href);
    const number = Number(url.searchParams.get('page'));
    if (url.pathname === location.pathname && number >= 1 && number <= Number(reader.dataset.total)) {
      event.preventDefault();
      void turnPage(number);
    }
  });
  window.addEventListener('pagehide', () => stop());
}
