(() => {
  'use strict';
  const form = document.querySelector('.order-form');
  if (!form) return;
  const custom = form.querySelector('[data-custom-occasion]');
  const occasion = form.elements.occasion;
  const updateOccasion = () => { custom.hidden = occasion.value !== 'Другое' && !custom.hasAttribute('data-has-error'); };
  occasion.addEventListener('change', () => { custom.removeAttribute('data-has-error'); updateOccasion(); });
  updateOccasion();

  if (form.dataset.startUrl) {
    const start = () => {
      form.removeEventListener('input', start);
      form.removeEventListener('change', start);
      const body = new URLSearchParams();
      for (const name of ['submission_key', 'csrfmiddlewaretoken']) body.set(name, form.elements[name].value);
      fetch(form.dataset.startUrl, {method: 'POST', body, keepalive: true, credentials: 'same-origin'}).catch(() => {});
    };
    form.addEventListener('input', start);
    form.addEventListener('change', start);
  }

  const tariff = form.elements.tariff;
  if (!tariff) return;
  const prices = new Map([...form.querySelectorAll('[data-tariff]')].map(node => [node.dataset.tariff, node.dataset]));
  const estimate = form.querySelector('[data-order-estimate]');
  function updateEstimate() {
    const selected = prices.get(tariff.value);
    let total = Number(selected?.price || 0), discuss = false;
    for (const [field, key] of [['narration', 'audio'], ['printed', 'print']]) {
      const price = selected?.[key];
      const known = price !== undefined && price !== '';
      form.querySelector(`[data-addon-label="${field}"]`).textContent = known ? `(+${price} ₽)` : '(уточним)';
      if (form.elements[field].checked) {
        if (known) total += Number(price);
        else discuss = true;
      }
    }
    estimate.hidden = !selected;
    estimate.textContent = discuss ? `PDF: ${selected?.price} ₽. Стоимость выбранных доплат уточним.` :
      `Предварительно: ${total} ₽ без доставки и твёрдого переплёта.`;
  }
  form.addEventListener('change', updateEstimate);
  updateEstimate();
})();
