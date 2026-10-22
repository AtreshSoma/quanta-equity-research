/* My list and price alerts: the bell in the top bar and the tear sheet's
 * Save / Alert controls, over /api/my-list and /api/alerts (webapp/my_list.py).
 *
 * The list and the alerts live on this computer (SQLite), so every browser and
 * every restart sees the same ones. A checker on the server reads alerted
 * prices every five minutes while their market is open and posts a desktop
 * notification; fired alerts wait in the bell's inbox until dismissed.
 */
(function (root) {
  'use strict';

  const POLL_MS = 60000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const fmt = (v, d = 2) => (finite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '--');
  const signed = (v) => (finite(v) ? `${v >= 0 ? '+' : '-'}${fmt(Math.abs(v))}%` : '');
  const SYMBOL_RE = /^[A-Z0-9^][A-Z0-9.=&^-]{0,24}$/;

  const state = { data: null, quotes: {}, calls: {}, open: false, timer: 0, slots: new Set() };

  async function api(method, url, body) {
    const opts = { method, headers: { Accept: 'application/json' } };
    if (method !== 'GET') {
      opts.headers['X-QUANTA-Request'] = '1';
      opts.headers['Content-Type'] = 'application/json';
      if (body) opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    let out = null;
    try { out = await res.json(); } catch (_) { out = null; }
    if (!res.ok) throw new Error((out && out.error) || `The server answered ${res.status}.`);
    return out;
  }

  function setData(d) {
    if (!d || !Array.isArray(d.symbols)) return;
    state.data = d;
    renderBadge();
    if (state.open) renderPanel();
    state.slots.forEach((slot) => { if (slot.isConnected) renderSlot(slot); else state.slots.delete(slot); });
  }

  async function refresh() {
    try { setData(await api('GET', '/api/my-list')); } catch (_) { /* the bell keeps its last state */ }
  }

  async function refreshQuotes() {
    try {
      const out = await api('GET', '/api/my-list/quotes');
      state.quotes = (out && out.quotes) || {};
      state.calls = (out && out.calls) || {};
      if (state.open) renderPanel();
    } catch (_) { /* rows show -- */ }
  }

  async function act(method, url, body, statusEl) {
    try {
      setData(await api(method, url, body));
      if (statusEl) statusEl.textContent = '';
      return true;
    } catch (err) {
      if (statusEl) statusEl.textContent = err.message;
      return false;
    }
  }

  const inList = (sym) => Boolean(state.data && state.data.symbols.some((r) => r.symbol === sym));
  const alertsFor = (sym) => (state.data ? state.data.alerts.filter((a) => a.symbol === sym) : []);
  const openResearch = (sym) => {
    close();
    if (typeof root.openTickerTearSheet === 'function') root.openTickerTearSheet(sym);
    else root.location.hash = '#analyze';
  };
  const when = (iso) => {
    const t = Date.parse(iso || '');
    if (!Number.isFinite(t)) return '';
    const mins = Math.round((Date.now() - t) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const h = Math.round(mins / 60);
    if (h < 24) return `${h} h ago`;
    return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  // ------------------------------------------------------------- the bell

  function renderBadge() {
    const btn = document.getElementById('ml-toggle');
    if (!btn || !state.data) return;
    const n = state.data.unread || 0;
    const badge = btn.querySelector('.ml-badge');
    badge.hidden = n === 0;
    badge.textContent = n > 9 ? '9+' : String(n);
    btn.setAttribute('aria-label', n ? `My list and alerts: ${n} fired alert${n === 1 ? '' : 's'}` : 'My list and alerts');
    btn.classList.toggle('has-unread', n > 0);
  }

  function renderPanel() {
    const panel = document.getElementById('ml-panel');
    if (!panel || !state.data) return;
    // Background list/quote refreshes must keep an in-progress ticker and focus.
    const form = panel.querySelector('[data-ml-add-form]');
    const focused = form && form.contains(document.activeElement) ? document.activeElement : null;
    const d = state.data;
    // Each row: price and day change, then the briefing line: QUANTA's last
    // recorded call on the name, the move since it, and the nearest alert.
    const rows = d.symbols.map((r) => {
      const q = state.quotes[r.symbol];
      const n = alertsFor(r.symbol).length;
      const chg = q && finite(q.change_pct) ? q.change_pct : null;
      const call = state.calls[r.symbol];
      const px = q && finite(q.price) ? q.price : null;
      const bits = [];
      if (call && call.label) {
        const since = px !== null && finite(call.price) && call.price > 0 ? ((px / call.price) - 1) * 100 : null;
        const day = call.as_of ? new Date(`${call.as_of}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
        bits.push(`Last call ${esc(call.label)} (${esc(call.engine || '')}${day ? `, ${esc(day)}` : ''})${since !== null ? `, <span class="${since >= 0 ? 'bull' : 'bear'}">${signed(since)}</span> since` : ''}`);
      }
      if (px !== null && n) {
        const near = alertsFor(r.symbol).map((a) => Math.abs(a.level / px - 1) * 100).sort((a, b) => a - b)[0];
        bits.push(`nearest alert ${fmt(near, 1)}% away`);
      }
      return `<li class="ml-row">
        <button type="button" class="ml-sym" data-ml-open="${esc(r.symbol)}" title="Open the ${esc(r.symbol)} tear sheet">${esc(r.symbol)}</button>
        <span class="ml-px">${px !== null ? fmt(px) : '--'}</span>
        <span class="ml-chg ${chg === null ? '' : chg >= 0 ? 'bull' : 'bear'}">${chg === null ? '' : signed(chg)}</span>
        <span class="ml-n" title="${n} active alert${n === 1 ? '' : 's'}">${n ? `${n} alert${n === 1 ? '' : 's'}` : ''}</span>
        <button type="button" class="ml-x" data-ml-remove="${esc(r.symbol)}" aria-label="Remove ${esc(r.symbol)} from My list">&times;</button>
        ${bits.length ? `<span class="ml-brief">${bits.join('; ')}</span>` : ''}
      </li>`;
    }).join('');
    const alerts = d.alerts.map((a) => `<li class="ml-row ml-row--alert">
        <span class="ml-sym ml-sym--plain">${esc(a.symbol)}</span>
        <span class="ml-cond">${a.direction === 'above' ? 'at or above' : 'at or below'} <b>${fmt(a.level)}</b>${a.label ? ` <span class="ml-label">${esc(a.label)}</span>` : ''}</span>
        <button type="button" class="ml-x" data-ml-delete="${a.id}" aria-label="Delete the ${esc(a.symbol)} alert at ${fmt(a.level)}">&times;</button>
      </li>`).join('');
    const inbox = d.inbox.map((a) => `<li class="ml-row ml-row--fired">
        <span class="ml-fired-dot" aria-hidden="true"></span>
        <span class="ml-fired">${esc(a.symbol)} ${a.direction === 'above' ? 'rose to' : 'fell to'} <b>${fmt(a.triggered_price)}</b>${a.label ? `, ${esc(a.label)} ${fmt(a.level)}` : `, alert at ${fmt(a.level)}`}<span class="ml-when">${esc(when(a.triggered_at))}</span></span>
        <button type="button" class="q-btn q-btn--sm" data-ml-dismiss="${a.id}">Dismiss</button>
      </li>`).join('');
    const checker = d.checker || {};
    const every = checker.interval_seconds ? Math.round(checker.interval_seconds / 60) : 5;
    panel.innerHTML = `
      ${d.inbox.length ? `<section class="ml-sec ml-sec--fired" aria-labelledby="ml-fired-h">
        <div class="ml-sec-head"><h2 class="ml-h" id="ml-fired-h">Fired alerts</h2><button type="button" class="ml-link" data-ml-dismiss-all>Dismiss all</button></div>
        <ul class="ml-rows">${inbox}</ul></section>` : ''}
      <section class="ml-sec" aria-labelledby="ml-list-h">
        <div class="ml-sec-head"><h2 class="ml-h" id="ml-list-h">My list</h2><span class="ml-count">${d.symbols.length}</span></div>
        <form class="ml-add" data-ml-add-form>
          <label class="sr-only" for="ml-add-input">Add a symbol to My list</label>
          <input id="ml-add-input" type="text" placeholder="Add a ticker, e.g. MSFT or TCS.NS" autocomplete="off" spellcheck="false" maxlength="25">
          <button type="submit" class="q-btn q-btn--sm">Add</button>
        </form>
        <p class="ml-status" role="status"></p>
        ${rows ? `<ul class="ml-rows">${rows}</ul>` : '<p class="ml-empty">Nothing saved yet. Save a name from its tear sheet, or add one here.</p>'}
      </section>
      <section class="ml-sec" aria-labelledby="ml-alerts-h">
        <div class="ml-sec-head"><h2 class="ml-h" id="ml-alerts-h">Price alerts</h2><span class="ml-count">${d.alerts.length}</span></div>
        ${alerts ? `<ul class="ml-rows">${alerts}</ul>` : '<p class="ml-empty">No alerts. Set one at a stop, a target or your own price from a tear sheet.</p>'}
      </section>
      <p class="ml-foot">${checker.running
        ? `Checked every ${every} minute${every === 1 ? '' : 's'} while the market is open${checker.last_run ? `, last ${esc(when(checker.last_run))}` : ''}. A fired alert posts a desktop notification on this computer${d.email ? ' and an email' : ''} and waits here.`
        : 'The alert checker is not running on this server, so alerts are saved but not checked.'}
        ${checker.running && d.alerts.length ? '<button type="button" class="ml-link" data-ml-check>Check now</button>' : ''}</p>`;
    if (form) panel.querySelector('[data-ml-add-form]').replaceWith(form);
    if (focused) focused.focus({ preventScroll: true });
  }

  function open() {
    const panel = document.getElementById('ml-panel');
    const btn = document.getElementById('ml-toggle');
    if (!panel || !btn) return;
    state.open = true;
    panel.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    renderPanel();
    refresh();
    refreshQuotes();
    const first = panel.querySelector('#ml-add-input');
    if (first) first.focus();
  }

  function close(returnFocus) {
    const panel = document.getElementById('ml-panel');
    const btn = document.getElementById('ml-toggle');
    if (!panel || !state.open) return;
    state.open = false;
    panel.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
    if (returnFocus && btn) btn.focus();
  }

  function wireBell() {
    const btn = document.getElementById('ml-toggle');
    const panel = document.getElementById('ml-panel');
    if (!btn || !panel || btn.dataset.wired) return;
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => (state.open ? close(true) : open()));
    panel.addEventListener('click', async (ev) => {
      const t = ev.target;
      const status = panel.querySelector('.ml-status');
      const go = t.closest('[data-ml-open]');
      if (go) { openResearch(go.dataset.mlOpen); return; }
      const rm = t.closest('[data-ml-remove]');
      if (rm) { await act('DELETE', `/api/my-list/${encodeURIComponent(rm.dataset.mlRemove)}`, null, status); return; }
      const del = t.closest('[data-ml-delete]');
      if (del) { await act('DELETE', `/api/alerts/${del.dataset.mlDelete}`); return; }
      const dis = t.closest('[data-ml-dismiss]');
      if (dis) { await act('POST', `/api/alerts/${dis.dataset.mlDismiss}/dismiss`); return; }
      if (t.closest('[data-ml-dismiss-all]')) { await act('POST', '/api/alerts/dismiss-all'); return; }
      if (t.closest('[data-ml-check]')) {
        try {
          const out = await api('POST', '/api/alerts/check');
          setData(out);
          const foot = panel.querySelector('.ml-foot');
          if (foot) foot.insertAdjacentHTML('beforeend', `<span class="ml-checked" role="status"> ${out.fired && out.fired.length ? `${out.fired.length} fired.` : 'Nothing crossed.'}</span>`);
        } catch (err) { if (status) status.textContent = err.message; }
      }
    });
    panel.addEventListener('submit', async (ev) => {
      const form = ev.target.closest('[data-ml-add-form]');
      if (!form) return;
      ev.preventDefault();
      const input = form.querySelector('input');
      const sym = String(input.value || '').trim().toUpperCase();
      const status = panel.querySelector('.ml-status');
      if (!SYMBOL_RE.test(sym)) { status.textContent = 'Type a ticker such as MSFT, BRK-B or TCS.NS.'; return; }
      if (await act('POST', '/api/my-list', { symbol: sym }, status)) {
        refreshQuotes();
        const again = document.getElementById('ml-add-input');
        if (again) {
          if (again.value.trim().toUpperCase() === sym) again.value = '';
          again.focus();
        }
      }
    });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && state.open) close(true); });
    document.addEventListener('pointerdown', (ev) => {
      if (state.open && !panel.contains(ev.target) && !btn.contains(ev.target)) close(false);
    });
  }

  // ------------------------------------------------- tear sheet controls

  function renderSlot(slot) {
    const sym = slot.dataset.symbol;
    if (!sym || !state.data) return;
    const saved = inList(sym);
    const mine = alertsFor(sym);
    const has = (dir, lvl) => mine.some((a) => a.direction === dir && Math.abs(a.level - lvl) < 1e-6);
    const stop = parseFloat(slot.dataset.stop);
    const target = parseFloat(slot.dataset.target);
    const price = parseFloat(slot.dataset.price);
    const quick = [];
    if (finite(stop) && stop > 0) quick.push(`<button type="button" class="ml-chip" data-ml-quick="below" data-level="${stop}" data-label="Stop" ${has('below', stop) ? 'aria-pressed="true" disabled' : 'aria-pressed="false"'}>${has('below', stop) ? 'Alert set' : 'Alert'} at stop ${fmt(stop)}</button>`);
    if (finite(target) && target > 0) quick.push(`<button type="button" class="ml-chip" data-ml-quick="above" data-level="${target}" data-label="${esc(slot.dataset.targetLabel || 'Target')}" ${has('above', target) ? 'aria-pressed="true" disabled' : 'aria-pressed="false"'}>${has('above', target) ? 'Alert set' : 'Alert'} at target ${fmt(target)}</button>`);
    slot.innerHTML = `
      <button type="button" class="ml-save" data-ml-save aria-pressed="${saved}">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z" fill="${saved ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        ${saved ? 'In My list' : 'Save to My list'}
      </button>
      ${quick.join('')}
      <details class="ml-custom">
        <summary class="ml-chip">Price alert${mine.length ? ` <span class="ml-chip-n">${mine.length}</span>` : ''}</summary>
        <form class="ml-custom-form" data-ml-custom>
          <label class="sr-only" for="ml-dir-${esc(sym)}">Direction</label>
          <select id="ml-dir-${esc(sym)}" name="direction"><option value="above">At or above</option><option value="below">At or below</option></select>
          <label class="sr-only" for="ml-lvl-${esc(sym)}">Price</label>
          <input id="ml-lvl-${esc(sym)}" name="level" type="number" inputmode="decimal" step="any" min="0" placeholder="${finite(price) ? fmt(price) : 'Price'}" required>
          <button type="submit" class="q-btn q-btn--sm">Set</button>
          <p class="ml-status" role="status"></p>
          ${mine.length ? `<ul class="ml-mini">${mine.map((a) => `<li>${a.direction === 'above' ? 'At or above' : 'At or below'} ${fmt(a.level)}${a.label ? ` (${esc(a.label)})` : ''} <button type="button" class="ml-x" data-ml-delete="${a.id}" aria-label="Delete this alert">&times;</button></li>`).join('')}</ul>` : ''}
          <p class="ml-hint">Checked every few minutes while the market is open; you get a desktop notification here.</p>
        </form>
      </details>`;
  }

  function mountTearSheet(slot) {
    if (!slot || slot.dataset.mlWired) return;
    slot.dataset.mlWired = '1';
    state.slots.add(slot);
    const sym = slot.dataset.symbol;
    slot.addEventListener('click', async (ev) => {
      const save = ev.target.closest('[data-ml-save]');
      if (save) {
        await act(inList(sym) ? 'DELETE' : 'POST', inList(sym) ? `/api/my-list/${encodeURIComponent(sym)}` : '/api/my-list', inList(sym) ? null : { symbol: sym });
        const again = slot.querySelector('[data-ml-save]');
        if (again) again.focus();
        return;
      }
      const quick = ev.target.closest('[data-ml-quick]');
      if (quick) {
        if (!inList(sym)) await act('POST', '/api/my-list', { symbol: sym });
        await act('POST', '/api/alerts', { symbol: sym, direction: quick.dataset.mlQuick, level: parseFloat(quick.dataset.level), label: quick.dataset.label, source: 'decision' });
        return;
      }
      const del = ev.target.closest('[data-ml-delete]');
      if (del) { await act('DELETE', `/api/alerts/${del.dataset.mlDelete}`); }
    });
    slot.addEventListener('submit', async (ev) => {
      const form = ev.target.closest('[data-ml-custom]');
      if (!form) return;
      ev.preventDefault();
      const level = parseFloat(form.level.value);
      const status = form.querySelector('.ml-status');
      if (!(level > 0)) { status.textContent = 'Type a price above zero.'; return; }
      if (!inList(sym)) await act('POST', '/api/my-list', { symbol: sym });
      const details = slot.querySelector('.ml-custom');
      if (await act('POST', '/api/alerts', { symbol: sym, direction: form.direction.value, level, label: 'Your price', source: 'manual' }, status)) {
        const d2 = slot.querySelector('.ml-custom');
        if (d2 && details) d2.open = true;
      }
    });
    if (state.data) renderSlot(slot); else refresh();
  }

  function start() {
    wireBell();
    refresh();
    clearInterval(state.timer);
    state.timer = setInterval(() => { if (!document.hidden) { refresh(); if (state.open) refreshQuotes(); } }, POLL_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }

  const api_ = { start, mountTearSheet, refresh, _state: state };
  if (typeof module !== 'undefined' && module.exports) module.exports = api_;
  if (typeof window !== 'undefined') {
    window.QuantaMyList = api_;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
}(typeof window !== 'undefined' ? window : globalThis));
