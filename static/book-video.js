(() => {
  const panel = document.querySelector('[data-book-video]');
  if (!panel) return;
  const player = panel.querySelector('video'), form = panel.querySelector('form');
  const message = panel.querySelector('[role=status]'), button = form ? form.querySelector('button') : null;
  const download = panel.querySelector('.video-download');
  let timer = null, requesting = false, revision = panel.dataset.videoRevision || null;
  function show(data) {
    clearTimeout(timer);
    const busy = data.state === 'queued' || data.state === 'rendering';
    if (button) {
      button.disabled = busy;
      button.textContent = data.state === 'ready' ? 'Пересоздать с выбранным временем' : 'Создать видео';
    }
    if (data.state === 'ready') {
      if (player.getAttribute('src') !== data.file || revision !== data.revision) { player.src = data.file; player.load(); revision = data.revision; }
      player.hidden = false; download.hidden = false; download.href = data.file + '?download=1';
      message.textContent = 'Видео готово и сохранено на сервере.';
    } else {
      player.pause(); player.hidden = true; download.hidden = true;
      if (player.hasAttribute('src')) { player.removeAttribute('src'); player.load(); }
      revision = null;
      message.textContent = data.state === 'queued' ? 'Видео в очереди на создание.' :
        data.state === 'rendering' ? 'Создаём видео: ' + data.progress + ' из ' + (data.total || '…') + ' разворотов.' :
        data.state === 'failed' ? data.error : data.state === 'stale' ?
        (button ? 'Книга изменилась. Создайте обновлённое видео.' : 'Видео обновляется. Попробуйте открыть ссылку позже.') :
        (button ? 'Видео ещё не создано. Выберите время и нажмите «Создать видео».' : 'Видео пока не подготовлено. Попробуйте открыть ссылку позже.');
    }
    timer = setTimeout(refresh, busy ? 3000 : 15000);
  }
  async function refresh() {
    if (document.hidden) { timer = setTimeout(refresh, 5000); return; }
    if (requesting) return;
    requesting = true;
    try {
      const response = await fetch(panel.dataset.statusUrl, {cache:'no-store'});
      if (!response.ok) { player.pause(); player.removeAttribute('src'); player.load(); throw Error('Видео недоступно. Проверьте вход и доступ к книге.'); }
      show(await response.json());
    } catch (error) { message.textContent = error.message; timer = setTimeout(refresh, 10000); }
    finally { requesting = false; }
  }
  if (form) form.addEventListener('submit', async event => {
    event.preventDefault();
    if (requesting || !form.reportValidity()) return;
    requesting = true; button.disabled = true;
    try {
      const response = await fetch(form.action, {method:'POST', body:new FormData(form), cache:'no-store'});
      if (!response.ok) throw Error(await response.text());
      show(await response.json());
    } catch (error) { message.textContent = error.message; button.disabled = false; }
    finally { requesting = false; }
  });
  player.addEventListener('play', () => { if (window.bookScreenAwake) window.bookScreenAwake(true); });
  ['pause', 'ended', 'error'].forEach(name => player.addEventListener(name, () => { if (window.bookScreenAwake) window.bookScreenAwake(false); }));
  window.addEventListener('pagehide', () => { clearTimeout(timer); player.pause(); });
  window.addEventListener('pageshow', () => { clearTimeout(timer); void refresh(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(timer); void refresh(); } });
  void refresh();
})();
