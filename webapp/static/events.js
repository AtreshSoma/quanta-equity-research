/* Material filings: the recent SEC 8-K filings of the stock on the Research page, each with its
 * items as text-and-severity chips, a link to the filing on sec.gov, and this stock's past
 * reaction to each item against SPY.
 *
 * It mounts into #material-filings and follows the ticker on screen (state.analysis, read the
 * same way plan-lab.js reads it). Every server string is escaped before it reaches the DOM,
 * and filing links are accepted only when they point at sec.gov over https. SEC text is data.
 * QUANTA never places orders; the card only reports filings and past reactions.
 *
 * Loads in the browser as window.QuantaEvents, and under node as a module
 * (tests/unit/events.test.cjs). */
(function (root) {
  'use strict';

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => ESC[c]);
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const TICKER_RE = /^[A-Z0-9^.=&-]{1,32}$/;
  const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
  const SEC_URL_RE = /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/[0-9]+\/[0-9]+\/[0-9A-Za-z.-]+$/;
  const NOTE_NOT_FORECAST = 'Past reactions of this stock to this kind of filing; small samples; not a forecast.';
  const SEVERITY_TONE = { HIGH: 'tone-bear', MEDIUM: 'tone-warn', LOW: '', UNRATED: '' };
  const SEVERITY_WORD = { HIGH: 'HIGH', MEDIUM: 'MEDIUM', LOW: 'LOW', UNRATED: 'NOT RATED' };

  const severityKey = (s) => (Object.prototype.hasOwnProperty.call(SEVERITY_TONE, s) ? s : 'UNRATED');
  const signedPct = (v) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '-' : ''}${Math.abs(v).toFixed(1)}%` : 'n/a');
  const absPct = (v) => (isNum(v) ? `±${Math.abs(v).toFixed(1)}%` : 'n/a');

  // Only sec.gov archive links are rendered as links; anything else is shown as plain text.
  function safeUrl(u) {
    const s = String(u || '');
    return SEC_URL_RE.test(s) ? s : '';
  }

  function emptyState(text) {
    return `<p class="mf-empty">${esc(text)}</p>`;
  }

  function errorState(text, fix) {
    return `<div class="mf-error" role="alert"><p>${esc(text)}</p>${fix ? `<p class="mf-fix">${esc(fix)}</p>` : ''}
      <button type="button" class="mf-retry" data-mf-retry="1">Try again</button></div>`;
  }

  function notCoveredState(text) {
    return `<p class="mf-notcovered" role="status"><span class="mf-tag">Not covered</span> ${esc(text)}</p>`;
  }

  // The past-reaction sentence for one item, from the study's 5-session window.
  function studyLine(code, stat) {
    if (!stat || typeof stat !== 'object') return '';
    const w = stat.windows && stat.windows['5'];
    if (!w) return '';
    if (w.too_few || !isNum(w.median_pct)) {
      return `<p class="mf-study mf-study--few">Item ${esc(code)}: too few past filings to say (n=${esc(w.n)}; QUANTA needs ${esc(w.min_n || 5)}).</p>`;
    }
    return `<p class="mf-study">This stock's past reaction: median ${esc(signedPct(w.median_pct))} vs SPY over 5 sessions; typical move ${esc(absPct(w.mean_abs_pct))} (n=${esc(w.n)}).</p>`;
  }

  function itemList(items, study) {
    const list = (Array.isArray(items) ? items : []).map((it) => {
      const sev = severityKey(String(it.severity || ''));
      const tone = SEVERITY_TONE[sev];
      return `<li class="mf-item">
        <span class="chip ${tone}" data-severity="${esc(sev)}"><b>${esc(SEVERITY_WORD[sev])}</b></span>
        <span class="mf-code">Item ${esc(it.code)}</span>
        <span class="mf-title">${esc(it.title)}</span>
        ${it.why ? `<span class="mf-why">${esc(it.why)}</span>` : ''}
        ${studyLine(String(it.code || ''), study && study[String(it.code || '')])}
      </li>`;
    }).join('');
    return list ? `<ul class="mf-items">${list}</ul>` : '';
  }

  // Today's move against the typical move for this kind of filing (5-session study).
  function liveReactionLine(ev, study, live) {
    if (!live || !isNum(live.dayChangePct) || !DAY_RE.test(String(ev.reaction_session || ''))) return '';
    if (String(ev.reaction_session) !== live.date) return '';
    const items = Array.isArray(ev.items) ? ev.items : [];
    let typical = null;
    items.forEach((it) => {
      const st = study && study[String(it.code || '')];
      const w = st && st.windows && st.windows['5'];
      if (w && isNum(w.mean_abs_pct) && !w.too_few && (typical === null || w.mean_abs_pct > typical)) typical = w.mean_abs_pct;
    });
    return `<p class="mf-live">Reaction so far today: <b>${esc(signedPct(live.dayChangePct))}</b>${typical !== null ? ` (typical move for this kind of filing ${esc(absPct(typical))})` : ''}.</p>`;
  }

  function eventCard(ev, study, live) {
    const url = safeUrl(ev.url);
    const date = DAY_RE.test(String(ev.date || '')) ? String(ev.date) : '';
    const react = DAY_RE.test(String(ev.reaction_session || '')) ? String(ev.reaction_session) : '';
    const sev = severityKey(String(ev.max_severity || ''));
    return `<li class="mf-event">
      <div class="mf-head">
        <time class="mf-date" datetime="${esc(date)}">${esc(date || 'Date not given')}</time>
        <span class="mf-form">${esc(ev.form || '8-K')}</span>
        <span class="chip ${SEVERITY_TONE[sev]}" data-severity="${esc(sev)}">Highest: ${esc(SEVERITY_WORD[sev])}</span>
      </div>
      ${itemList(ev.items, study)}
      ${react ? `<p class="mf-react">Reaction day: ${esc(react)}. ${esc(ev.reaction_note || '')}</p>` : ''}
      ${liveReactionLine(ev, study, live)}
      ${url
        ? `<a class="mf-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">View the filing on SEC.gov<span class="sr-only"> (opens in a new tab)</span></a>`
        : '<p class="mf-nolink">No SEC link was given for this filing.</p>'}
    </li>`;
  }

  // Render the answer of GET /api/events for the given day window.
  function renderCard(res, days, live) {
    if (!res || typeof res !== 'object') return emptyState('Run Research on a ticker to see its material filings.');
    if (res.status === 'not_covered' || res.covered === false) {
      return notCoveredState(res.message || 'Not covered: SEC filings exist only for US-registered issuers.');
    }
    if (res.available === false || res.status === 'unavailable' || res.error) {
      return errorState(res.error || res.message || 'SEC EDGAR is unavailable right now.', res.fix || '');
    }
    const recent = Array.isArray(res.recent) ? res.recent : [];
    const winDays = Number.isInteger(days) ? days : (res.days || 90);
    // /api/events returns the per-item stats under study.items (event_study.build_report);
    // a flat {code: stat} map is accepted too.
    const rawStudy = res.study && typeof res.study === 'object' ? res.study : null;
    const study = rawStudy && rawStudy.items && typeof rawStudy.items === 'object' ? rawStudy.items : rawStudy;
    const head = recent.length
      ? `<p class="mf-count">${esc(recent.length)} 8-K filing${recent.length === 1 ? '' : 's'} in the last ${esc(winDays)} days, newest first.</p>`
      : emptyState(`No 8-K filings in the last ${winDays} days.`);
    const list = recent.length ? `<ol class="mf-list">${recent.map((e) => eventCard(e, study, live)).join('')}</ol>` : '';
    const studyNote = res.study_status === 'unavailable' && res.study_message
      ? `<p class="mf-note" role="status">${esc(res.study_message)}</p>` : '';
    return `${head}${list}${studyNote}<p class="mf-note">${esc(NOTE_NOT_FORECAST)}</p>`;
  }

  async function fetchEvents(symbol, days) {
    const url = `/api/events?symbol=${encodeURIComponent(symbol)}&days=${encodeURIComponent(String(days))}`;
    const resp = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    let body = null;
    try { body = await resp.json(); } catch (_) { body = null; }
    if (!resp.ok) {
      return { ok: false, body: body || { error: 'Material filings are unavailable right now.' } };
    }
    return { ok: true, body };
  }

  // Today's session date in New York (the exchange) and the payload's day move, if any.
  function todayIso() {
    try {
      return new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
    } catch (_) {
      return '';
    }
  }
  function liveFor(data) {
    const d = data && data.day_change_pct != null ? Number(data.day_change_pct) : NaN;
    return { date: todayIso(), dayChangePct: Number.isFinite(d) ? d : null };
  }

  function mount(doc, currentAnalysis, days) {
    const host = doc.getElementById('material-filings');
    if (!host) return null;
    const windowDays = days || 90;
    host.innerHTML = `<div class="mf-head-row">
        <h3 class="mf-title-h" id="mf-title">Material filings</h3>
        <p class="mf-sub">Recent 8-K filings from SEC EDGAR, and this stock's past reactions against SPY.</p>
      </div>
      <div class="mf-body" id="mf-body" aria-live="polite">${emptyState('Run Research on a ticker to see its material filings.')}</div>`;
    host.setAttribute('aria-labelledby', 'mf-title');
    const body = doc.getElementById('mf-body');
    let run = 0;
    let lastSym = null;

    function show(html) { body.innerHTML = html; }

    async function load(sym) {
      const current = ++run;
      show(`<p class="mf-loading" role="status">Reading the SEC filings and this stock's past reactions...</p>`);
      try {
        const out = await fetchEvents(sym, windowDays);
        if (current !== run) return;
        if (out.ok) show(renderCard(out.body, windowDays, liveFor(currentAnalysis())));
        else show(errorState(out.body.error || 'Material filings are unavailable right now.', out.body.fix || ''));
      } catch (_) {
        if (current !== run) return;
        show(errorState('Material filings could not be loaded. Check the connection and try again.', ''));
      }
    }

    function syncFromAnalysis() {
      const data = currentAnalysis();
      const sym = String((data && data.ticker) || '').trim().toUpperCase();
      if (!TICKER_RE.test(sym)) {
        if (lastSym !== null) { lastSym = null; run += 1; }
        show(emptyState('Run Research on a ticker to see its material filings.'));
        return;
      }
      if (sym === lastSym) return;
      lastSym = sym;
      load(sym);
    }

    body.addEventListener('click', (ev) => {
      const btn = ev.target && ev.target.closest ? ev.target.closest('[data-mf-retry]') : null;
      if (!btn) return;
      lastSym = null;
      syncFromAnalysis();
    });

    syncFromAnalysis();
    return { syncFromAnalysis, load, renderCard };
  }

  function start() {
    if (typeof document === 'undefined') return;
    const host = document.getElementById('material-filings');
    if (!host) return;
    const analysis = () => {
      try {
        return typeof state !== 'undefined' && state ? state.analysis : null; // eslint-disable-line no-undef
      } catch (_) {
        return null;
      }
    };
    const api = mount(document, analysis, 90);
    if (!api) return;
    const results = document.getElementById('an-results');
    if (results && typeof MutationObserver === 'function') {
      new MutationObserver(() => api.syncFromAnalysis()).observe(results, { childList: true });
    }
  }

  const api = { esc, safeUrl, studyLine, renderCard, eventCard, itemList, fetchEvents, mount, signedPct, absPct, liveReactionLine };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaEvents = api;
    if (typeof document !== 'undefined') {
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
      else start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
