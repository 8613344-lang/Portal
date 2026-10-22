(() => {
  'use strict';
  const offer = document.querySelector('[data-install-offer]');
  const page = document.querySelector('[data-install-page]');
  const action = document.querySelector('[data-install-action]');
  const status = document.querySelector('[data-install-status]');
  const standalone = window.matchMedia('(display-mode: standalone)');
  const mobile = window.matchMedia('(max-width: 1100px), (max-width: 1366px) and (pointer: coarse)');
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const nativeApp = /PortalBooks\/|; wv\)/.test(navigator.userAgent);
  const key = 'qicko-install-dismiss-until';
  let prompt = null, installed = false, busy = false, hiddenUntil = 0;
  const isInstalled = () => standalone.matches || navigator.standalone === true || installed;
  const dismissed = () => {
    if (hiddenUntil > Date.now()) return true;
    try { return Number(localStorage.getItem(key)) > Date.now(); } catch { return false; }
  };
  const dismissFor = days => {
    hiddenUntil = Date.now() + days * 86400000;
    try { localStorage.setItem(key, String(hiddenUntil)); } catch {}
  };
  function update() {
    if (offer) offer.hidden = !mobile.matches || nativeApp || isInstalled() || dismissed();
    document.querySelectorAll('[data-install-trigger]').forEach(link => {
      link.firstChild.textContent = prompt ? 'Установить ' : 'Как добавить ';
    });
    if (action) { action.hidden = !prompt || isInstalled() || nativeApp; action.disabled = busy; }
    if (status && (standalone.matches || navigator.standalone === true)) {
      status.textContent = 'Сайт уже открыт как приложение. Книжная полка всегда рядом.';
    }
  }
  async function install(event) {
    if (busy) { event.preventDefault(); return; }
    if (!prompt) return;
    event.preventDefault();
    const pending = prompt;
    prompt = null; busy = true;
    update();
    try {
      await pending.prompt();
      const choice = await pending.userChoice;
      if (choice.outcome === 'accepted') {
        installed = true; dismissFor(90);
        if (status) status.textContent = 'Добавление подтверждено. Найдите значок «Книги рядом» на главном экране.';
      } else if (status) {
        status.textContent = 'Установка отменена. Вы можете добавить ярлык через меню браузера по инструкции ниже.';
      }
    } catch {
      if (status) status.textContent = 'Откройте меню браузера и добавьте ярлык по инструкции ниже.';
      else window.location.assign(document.querySelector('[data-install-trigger]').href);
    } finally { busy = false; update(); }
  }
  window.addEventListener('beforeinstallprompt', event => {
    if (nativeApp || isInstalled()) return;
    event.preventDefault(); prompt = event; update();
  });
  window.addEventListener('appinstalled', () => {
    installed = true; prompt = null; dismissFor(90); update();
  });
  document.querySelectorAll('[data-install-trigger], [data-install-action]').forEach(button => button.addEventListener('click', install));
  document.querySelector('[data-install-dismiss]')?.addEventListener('click', () => { dismissFor(7); update(); });
  if (page) {
    const options = [...page.querySelectorAll('[data-platform-option]')];
    const select = platform => {
      options.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.platformOption === platform)));
      page.querySelectorAll('[data-install-guide]').forEach(guide => { guide.hidden = guide.dataset.installGuide !== platform; });
    };
    page.querySelector('[data-install-platforms]').hidden = false;
    options.forEach(button => button.addEventListener('click', () => select(button.dataset.platformOption)));
    select(ios ? 'ios' : 'android');
  }
  standalone.addEventListener?.('change', update);
  mobile.addEventListener?.('change', update);
  window.addEventListener('pageshow', update);
  update();
  if ('serviceWorker' in navigator && window.isSecureContext && !nativeApp) {
    navigator.serviceWorker.register('/service-worker.js', {scope: '/', updateViaCache: 'none'}).catch(() => {
      // Manual home-screen instructions remain available if registration is blocked.
    });
  }
})();
