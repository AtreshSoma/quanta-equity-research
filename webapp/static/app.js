
/* [F9] Decision thresholds have ONE source: core/engine/thresholds.py.
   The server renders them into <script id="quanta-thresholds" type="application/json">
   and serves them at /api/thresholds; the unit tests install the committed fixture
   (tests/fixtures/thresholds.json) that a Python test keeps equal to that module.
   Nothing in this file restates a threshold: read it with thr('GROUP.KEY'). */
function loadThresholds() {
  const g = typeof globalThis !== 'undefined' ? globalThis : {};
  if (g.__QUANTA_THRESHOLDS__) return g.__QUANTA_THRESHOLDS__;
  try {
    const el = typeof document !== 'undefined' && document.getElementById('quanta-thresholds');
    const text = el ? String(el.textContent || '').trim() : '';
    if (text.startsWith('{')) return JSON.parse(text);
  } catch (_) { /* a missing or unrendered block leaves every threshold unknown */ }
  return {};
}
const QT = loadThresholds();
function thr(path) {
  const p = String(path);
  // band:NAME -> the floor of a LADDER.SCORE_BANDS band (e.g. band:HOLD -> 45).
  if (p.startsWith('band:')) {
    const hit = ((QT.LADDER || {}).SCORE_BANDS || []).find(([, name]) => name === p.slice(5));
    return hit ? hit[0] : undefined;
  }
  // The sector cap a small account gets: the configured profile's percentage.
  if (p === '@small_sector_cap') {
    const B = QT.BOOK || {};
    return (B.SECTOR_CAP_PROFILE_PCT || {})[B.SECTOR_CAP_PROFILE];
  }
  return p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), QT);
}
/* How a threshold reads in copy. rr: "1.80 : 1"; pct: 0.25 -> "25%"; money: "$100";
   num: as configured. An unknown threshold renders as n/a, never a guess. */
function fmtThreshold(v, fmt) {
  if (v == null || (typeof v === 'number' && !Number.isFinite(v))) return 'n/a';
  const n = Number(v);
  switch (fmt) {
    case 'rr': return `${n.toFixed(thr('UI.RR_DECIMALS') ?? 2)} : 1`;
    case 'pct': return `${+(n * 100).toFixed(2)}%`;
    case 'abs-pct': return `${+Math.abs(n).toFixed(2)}%`;
    case 'money': return `$${n.toLocaleString('en-US')}`;
    case 'inr': return `₹${n.toLocaleString('en-IN')}`;
    case 'crore': return `₹${(n / 1e7).toLocaleString('en-IN')} crore`;
    case 'num': return String(+n.toFixed(4));
    case 'int': return String(Math.round(n));
    default: return String(v);
  }
}
/* Fill every <span data-threshold="GROUP.KEY" data-threshold-fmt="fmt"> under root. */
function applyThresholds(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || !scope.querySelectorAll) return;
  scope.querySelectorAll('[data-threshold]').forEach((el) => {
    const v = thr(el.dataset.threshold);
    el.textContent = fmtThreshold(v, el.dataset.thresholdFmt);
    // A tick that sits AT the threshold (e.g. the score-band axis) also takes
    // its position from it.
    if (el.dataset.thresholdStyle && v != null && el.style) el.style.setProperty(el.dataset.thresholdStyle, String(v));
  });
  // [P6] The chase thresholds are per stock: the optional volatility widening, from config.
  scope.querySelectorAll('[data-chase-wide]').forEach((el) => { el.textContent = chaseWideRuleText(); });
  // Scan menu: each preset's size (and the large-cap floor) comes from UNIVERSE
  // config. The markup carries number-free fallback text; {n} and {cap} fill here.
  scope.querySelectorAll('option[data-label-tpl], option[data-text-tpl]').forEach((opt) => {
    const n = scanLimit(opt.value);
    if (n == null) return;
    const capFloor = (thr('UNIVERSE.SCAN_MIN_MARKET_CAP') || {})[opt.value];
    const fill = (tpl) => (tpl.includes('{cap}') && !(capFloor > 0) ? null
      : tpl.replace('{n}', fmtNumber(n, 0)).replace('{cap}', fmtCompactMoney(capFloor)));
    const label = opt.dataset.labelTpl && fill(opt.dataset.labelTpl);
    const text = opt.dataset.textTpl && fill(opt.dataset.textTpl);
    if (label) opt.label = label;
    if (text) opt.textContent = text;
  });
  // Position count: default and ceiling from CAPITAL config.
  const pos = scope.querySelector && scope.querySelector('#sp-positions');
  if (pos) {
    const lim = Number(thr('CAPITAL.MAX_POSITIONS_LIMIT'));
    const dflt = Number(thr('CAPITAL.MAX_POSITIONS_DEFAULT'));
    if (lim > 0) pos.max = String(lim);
    if (dflt > 0 && !pos.value) pos.value = String(dflt);
  }
}

// Research suggestions: the reader's own recent tickers for this market, then
// today's movers from the market feed. Nothing is a fixed list; with neither,
// the row hides.
const RECENT_RESEARCH_KEY = 'quanta_recent_research';
const RESEARCH_CHIPS_SHOWN = 6;
function readRecentResearch() {
  try { return JSON.parse(localStorage.getItem(RECENT_RESEARCH_KEY) || '{}') || {}; } catch (_) { return {}; }
}
function rememberResearch(ticker, market) {
  const t = String(ticker || '').trim().toUpperCase();
  if (!t) return;
  const all = readRecentResearch();
  const m = market === 'IN' ? 'IN' : 'US';
  all[m] = [t, ...(Array.isArray(all[m]) ? all[m] : []).filter((x) => x !== t)].slice(0, RESEARCH_CHIPS_SHOWN);
  try { localStorage.setItem(RECENT_RESEARCH_KEY, JSON.stringify(all)); } catch (_) { /* storage off: no recents */ }
  renderResearchChips();
}
// [PURE] Suggested tickers: recents first, then movers, de-duplicated.
function researchSuggestions(recent = [], intel = {}) {
  const movers = [...(intel.movers_up || []), ...(intel.movers_down || [])]
    .map((r) => String((r && r.symbol) || '').toUpperCase()).filter(Boolean);
  const out = [];
  [...recent, ...movers].forEach((t) => { if (!out.includes(t)) out.push(t); });
  return { tickers: out.slice(0, RESEARCH_CHIPS_SHOWN), recentCount: Math.min(recent.length, RESEARCH_CHIPS_SHOWN) };
}
function renderResearchChips() {
  const row = typeof document !== 'undefined' && document.getElementById('an-quick-chips');
  if (!row) return;
  const m = state.market === 'IN' ? 'IN' : 'US';
  const intel = (state.dashboard && state.dashboard.selected_market !== undefined && state.dashboard.selected_market !== m)
    ? {} : ((state.dashboard && state.dashboard.market_intel) || {});
  void intel;
  // Examples per index for the selected market, from UI.RESEARCH_SUGGESTIONS.
  const groups = researchIndexGroups(m);
  const list = row.querySelector('[data-chip-list]');
  if (!list) return;
  list.innerHTML = groups.map(([index, items]) => `<span class="rs-group"><span class="rs-group-k">${escapeHtml(index)}</span>${items.map(([t, name]) => `<button type="button" class="chip-btn" data-ticker="${escapeHtml(t)}" data-market="${m}" title="Analyze ${escapeHtml(name ? `${name} (${t})` : t)}, ${escapeHtml(index)}">${escapeHtml(t)}</button>`).join('')}</span>`).join('');
  row.hidden = !groups.length;
}

// [PURE] The configured example listings for one market:
// [[index, [[ticker, company], ...]], ...]. A bare ticker gets an empty name.
function researchIndexGroups(market) {
  const cfg = thr('UI.RESEARCH_SUGGESTIONS') || {};
  const groups = cfg[market === 'IN' ? 'IN' : 'US'];
  if (!Array.isArray(groups)) return [];
  return groups
    .filter((g) => Array.isArray(g) && g[0] && Array.isArray(g[1]) && g[1].length)
    .map(([index, items]) => [index, items.map((it) => (Array.isArray(it) ? [String(it[0]), String(it[1] || '')] : [String(it), '']))]);
}

// Research search: a combobox over the ticker field. Empty, it offers recent
// searches and the configured examples for both markets (the selected one
// first); typing asks /api/symbol-search for matching US and Indian listings.
// Arrow keys move, Enter researches, Escape closes, "/" focuses the field.
const MARKET_NAMES = { US: 'US', IN: 'India' };
function wireResearchSearch() {
  const input = typeof document !== 'undefined' && document.getElementById('an-ticker');
  const pop = input && document.getElementById('an-suggest');
  if (!input || !pop || input.dataset.combo) return;
  input.dataset.combo = '1';
  let active = -1; let timer = 0; let seq = 0; let n = 0;
  const options = () => Array.from(pop.querySelectorAll('[role="option"]'));
  const setActive = (i) => {
    const opts = options();
    active = opts.length ? (i + opts.length) % opts.length : -1;
    opts.forEach((o, j) => { o.setAttribute('aria-selected', String(j === active)); o.classList.toggle('is-active', j === active); });
    if (active >= 0) { input.setAttribute('aria-activedescendant', opts[active].id); opts[active].scrollIntoView({ block: 'nearest' }); } else input.removeAttribute('aria-activedescendant');
  };
  const open = (html) => { pop.innerHTML = html; pop.hidden = !html; input.setAttribute('aria-expanded', String(!!html)); active = -1; input.removeAttribute('aria-activedescendant'); };
  const close = () => { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
  const opt = (sym, market, inner, cls = '') => `<div role="option" id="rs-opt-${n++}" class="rs-opt${cls}" aria-selected="false" data-symbol="${escapeHtml(sym)}" data-market="${market}">${inner}</div>`;
  const chipOpt = (sym, market, name = '') => opt(sym, market, `<span class="rs-sym">${escapeHtml(sym)}</span>${name ? `<span class="rs-chip-name">${escapeHtml(name)}</span>` : ''}`, ' rs-opt--chip');
  const emptyHtml = () => {
    n = 0;
    const m = state.market === 'IN' ? 'IN' : 'US';
    const recent = (readRecentResearch()[m] || []).filter((t) => typeof t === 'string').slice(0, 6);
    // A market without configured examples shows no empty heading.
    const market = (k) => {
      const groups = researchIndexGroups(k);
      if (!groups.length) return '';
      return `<div class="rs-sec" role="group" aria-label="${MARKET_NAMES[k]} examples"><div class="rs-sec-h" aria-hidden="true">${MARKET_NAMES[k]} examples</div>${groups.map(([idx, items]) => `<div class="rs-line" role="group" aria-label="${escapeHtml(idx)}"><span class="rs-line-k" aria-hidden="true">${escapeHtml(idx)}</span>${items.map(([t, name]) => chipOpt(t, k, name)).join('')}</div>`).join('')}</div>`;
    };
    return `${recent.length ? `<div class="rs-sec" role="group" aria-label="Recent searches"><div class="rs-sec-h" aria-hidden="true">Recent</div><div class="rs-line">${recent.map((t) => chipOpt(t, m)).join('')}</div></div>` : ''}${market(m)}${market(m === 'US' ? 'IN' : 'US')}`;
  };
  const resultsHtml = (q, results, note) => {
    n = 0;
    const upper = q.toUpperCase();
    const exact = /^\^?[A-Z0-9][A-Z0-9.\-=&]{0,14}$/.test(upper) && !results.some((r) => r.symbol === upper)
      ? opt(upper, state.market === 'IN' ? 'IN' : 'US', `<span class="rs-sym">${escapeHtml(upper)}</span><span class="rs-name">Analyze this ticker</span>`) : '';
    const rows = results.map((r) => opt(r.symbol, r.market, `<span class="rs-sym">${escapeHtml(r.symbol)}</span><span class="rs-name">${escapeHtml(r.name || '')}</span><span class="rs-meta">${escapeHtml(r.exchange || '')}, ${escapeHtml(String(r.type || '').toLowerCase())}</span><span class="rs-mkt rs-mkt--${r.market === 'IN' ? 'in' : 'us'}">${MARKET_NAMES[r.market] || escapeHtml(r.market)}</span>`)).join('');
    const body = `${rows}${exact}${note ? `<div class="rs-note" role="presentation">${escapeHtml(note)}</div>` : ''}`;
    return body || '<div class="rs-note" role="presentation">No US or Indian listing matches. Check the spelling, or type the full ticker.</div>';
  };
  const choose = (el) => {
    if (!el) return;
    input.value = el.dataset.symbol;
    close();
    setFieldError('#an-ticker', '#an-ticker-error', '');
    runAnalysis(null, { market: el.dataset.market });
  };
  const query = () => {
    const q = input.value.trim();
    clearTimeout(timer);
    active = -1;
    input.removeAttribute('aria-activedescendant');
    options().forEach((o) => { o.setAttribute('aria-selected', 'false'); o.classList.remove('is-active'); });
    if (!q) { open(emptyHtml()); return; }
    if (q.length < 2) { open(resultsHtml(q, [], '')); return; }
    const my = ++seq;
    timer = setTimeout(async () => {
      let data = { results: [] };
      try { data = await (await fetch(`/api/symbol-search?q=${encodeURIComponent(q)}`)).json(); } catch (_) { data = { results: [], reason: 'Search is unavailable; type the full ticker.' }; }
      if (my !== seq || document.activeElement !== input) return;
      open(resultsHtml(q, data.results || [], data.reason || ''));
    }, 160);
  };
  input.addEventListener('focus', query);
  input.addEventListener('input', query);
  input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) close(); }, 120));
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (pop.hidden) query();
      setActive(active + (ev.key === 'ArrowDown' ? 1 : -1));
    } else if (ev.key === 'Enter' && !pop.hidden && active >= 0) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      choose(options()[active]);
    } else if (ev.key === 'Escape' && !pop.hidden) {
      ev.preventDefault(); close();
    } else if (ev.key === 'Tab' || ev.key === 'Enter') {
      close();
    }
  }, true);
  pop.addEventListener('mousedown', (ev) => ev.preventDefault());
  // Analyze: the glow follows the pointer across the button.
  const btn = document.getElementById('btn-analyze');
  if (btn && !btn.dataset.spot) {
    btn.dataset.spot = '1';
    btn.addEventListener('pointermove', (ev) => {
      const r = btn.getBoundingClientRect();
      btn.style.setProperty('--mx', `${Math.round(ev.clientX - r.left)}px`);
      btn.style.setProperty('--my', `${Math.round(ev.clientY - r.top)}px`);
    });
  }
  pop.addEventListener('click', (ev) => choose(ev.target.closest('[role="option"]')));
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== '/' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const tag = (ev.target && ev.target.tagName) || '';
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || (ev.target && ev.target.isContentEditable)) return;
    const view = document.getElementById('view-analyze');
    if (!view || !view.classList.contains('active')) return;
    ev.preventDefault();
    input.focus();
  });
}

// [PURE] A model evidence record (object, or its status string) counts as
// validated only at the configured status (LADDER.STRONG_BUY_EVIDENCE).
function evidenceValidated(ev) {
  const status = String((ev && typeof ev === 'object' ? ev.status : ev) || '').toUpperCase();
  const need = String(thr('LADDER.STRONG_BUY_EVIDENCE') || '').toUpperCase();
  return !!need && status === need;
}
// The flag every call carries until its model is validated: Picks, Research and
// My Portfolio show the same words and link to the evidence page.
function modelFlagHtml(ev, attrs = '') {
  if (evidenceValidated(ev)) return '';
  const why = (ev && typeof ev === 'object' && (ev.status_text || ev.text)) || 'not validated out of sample';
  return `<a class="pk-valid model-flag" href="#track" data-view-link="track" data-model-flag ${attrs} title="Model evidence: ${escapeHtml(why)}. Open the evidence page.">Model not yet validated</a>`;
}

// [PURE] Names a preset scan analyses (UNIVERSE.US_SCAN_LIMITS / INDIA_SCAN_LIMITS), or null.
function scanLimit(key) {
  const v = (thr('UNIVERSE.US_SCAN_LIMITS') || {})[key] ?? (thr('UNIVERSE.INDIA_SCAN_LIMITS') || {})[key];
  return Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null;
}

// [PURE] $10B, $300M: a market-cap floor in the menu's compact form.
function fmtCompactMoney(v) {
  const n = Number(v);
  if (!(n > 0)) return '';
  if (n >= 1e12) return `$${+(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `$${+(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${+(n / 1e6).toFixed(1)}M`;
  return `$${fmtNumber(n, 0)}`;
}

function chaseWideRuleText() {
  const min = Number(thr('CHASE.WIDE_THRESHOLD_MIN_VOL'));
  if (!(min > 0)) return 'Off: every stock uses the thresholds above in every scan.';
  const pct = (v) => fmtThreshold(v, 'pct');
  return `On for a stock whose ${thr('CHASE.VOL_LOOKBACK_BARS')}-session volatility is at least ${pct(min)}, in any scan: `
    + `gap ${pct(thr('CHASE.GROWTH_GAP_PCT'))}, runs ${pct(thr('CHASE.GROWTH_MOVE_5D_PCT'))} and ${pct(thr('CHASE.GROWTH_MOVE_10D_PCT'))}, `
    + `penalty ${fmtThreshold(thr('CHASE.GROWTH_SCORE_PENALTY'), 'num')}.`;
}

/* Index participation, shown as the fraction actually measured. A bare "75%"
   over four overlapping index ETFs reads as exchange breadth; "3/4 proxies"
   does not. Missing members are named rather than dropped silently. */
function participationText(model) {
  const d = model && model.participation;
  if (d && d.measured) {
    const miss = d.missing && d.missing.length ? ` (${d.missing.length} n/a)` : '';
    return `${d.above}/${d.measured}${miss}`;
  }
  return `${model.breadth}%`;
}


/* Every share count and weight above is account-relative. Say which account,
   and flag the placeholder — the deep dive used to silently size against a
   100,000 default while the scanner used the user's real capital. */
function renderAccountContextNote(data) {
  const ctx = data && data.account_context;
  if (!ctx || ctx.account_size == null) return '';
  const tone = ctx.assumed ? 'warn' : 'info';
  return `<div class="plan-note ${tone}">${escapeHtml(ctx.note || '')}</div>`;
}

/* Pre-trade portfolio check. "Holdings unknown" is not a passed check, and the
   difference decides whether the number above is an order or a what-if. */
function renderPortfolioCheckNote(data) {
  const chk = data && data.portfolio_risk_check;
  if (!chk || !Object.keys(chk).length) return '';
  if (!chk.holdings_known) {
    return `<div class="plan-note warn">Holdings unknown: this is a hypothetical
      allocation, not a portfolio-checked order. Declare current positions to
      enforce sector and correlation limits.</div>`;
  }
  if (chk.approved === false) {
    const warns = (chk.warnings || []).map((w) => escapeHtml(w)).join(' · ');
    return `<div class="plan-note bear">Portfolio limits bind: ${warns ||
      'the proposed size exceeds a concentration limit'}. Size above is already reduced.</div>`;
  }
  return '<div class="plan-note info">Pre-trade portfolio check passed against your declared holdings.</div>';
}


/* A NEUTRAL sentiment score means two very different things depending on
   whether any article was actually read. Show the reason when nothing was
   measured, instead of printing "Neutral" over an empty fetch. */
function sentimentLabelText(sentiment) {
  const s = sentiment || {};
  if (s.measured === false) {
    const reason = {
      NO_NEWS: 'No news found',
      PROVIDER_UNAVAILABLE: 'News providers unavailable',
      NOT_CONFIGURED: 'No news provider configured',
    }[s.status] || 'Not measured';
    const detail = s.sources_attempted
      ? ` (${s.sources_succeeded || 0}/${s.sources_attempted} sources responded)` : '';
    return `<span class="q-na">${escapeHtml(reason + detail)}</span>`;
  }
  const partial = s.status === 'PARTIAL'
    ? ` <span class="muted">(partial: ${s.sources_succeeded}/${s.sources_attempted} sources)</span>` : '';
  return escapeHtml(titleLabel(s.sentiment_label)) + partial;
}


/* Target quality is source COUNT plus how much those sources agree — not a
   confidence percentage that rose 25 points per source regardless of whether
   the sources were independent or pointed anywhere near each other. */
function renderTargetQuality(val) {
  const v = val || {};
  const n = Number(v.target_source_count || 0);
  if (!n) return '';
  const agree = v.target_agreement_pct;
  const tone = agree == null ? '' : (agree >= Number(thr('RESEARCH.TARGET_AGREEMENT_STRONG_PCT')) ? 'bull'
    : agree <= Number(thr('RESEARCH.TARGET_AGREEMENT_WEAK_PCT')) ? 'bear' : 'warn');
  const text = agree == null
    ? `${n} source${n === 1 ? '' : 's'}; agreement n/a with a single estimate`
    : `${n} sources · ${Math.round(agree)}% agreement`;
  const srcs = (v.target_sources || []).join(', ');
  return `<div class="target-quality ${tone}" title="${escapeHtml(
    'Dispersion across contributing target estimates' + (srcs ? ': ' + srcs : '') +
    '. Not a probability the target is reached.')}">${escapeHtml(text)}</div>`;
}

/* ============================================================================
   QUANTA ELITE — Frontend Logic
   Premium institutional UI bound to Flask backend.
   ============================================================================ */

const state = {
  dashboard: null,
  newsGroup: 'high-impact',
  picks: null,
  analysis: null,
  market: (typeof localStorage !== 'undefined' && localStorage && typeof localStorage.getItem === 'function' && localStorage.getItem('quanta_market')) || 'US',
  /** ISO timestamp of last successful dashboard payload (for freshness chip). */
  lastUpdated: null,
  /** Symbols rendered in the Security Monitor this cycle — cross-section dedup hints. */
  shownSymbols: new Set(),
  /** Active Security Monitor bucket (opportunities | watchlist | extended | avoid). */
  /** True once the user picks a bucket themselves; stops auto-landing. */
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------------------------------------------------------------------------
   HELPERS
   --------------------------------------------------------------------------- */

const escapeHtml = (str = '') => String(str).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

// A bare fetch() rejects with `TypeError: Failed to fetch` (or `Load failed` on
// Safari / `NetworkError ...` on Firefox) when the backend isn't reachable —
// e.g. the Flask server was stopped or the port changed. Surface a clear,
// actionable message instead of the cryptic browser default. Returns the
// original message for genuine application errors so we never mask them.
function friendlyFetchError(err) {
  const raw = (err && typeof err.message === 'string' ? err.message : String(err || '')).trim();
  const isNetwork = /failed to fetch|load failed|networkerror|network request failed|fetch failed/i.test(raw);
  if (isNetwork) {
    return 'Cannot reach the QUANTA backend. Make sure the server is running '
      + '(start.ps1 or "python webapp/app.py") on this port, then try again.';
  }
  return raw || 'Request failed';
}

// Every state-changing request carries this header; the server refuses POSTs
// without it (a cross-site page cannot set it without a CORS preflight).
const QUANTA_POST_HEADERS = Object.freeze({ 'X-QUANTA-Request': '1' });

const fmtNumber = (v, dec = 2) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  return n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
};

// A valuation multiple the provider did not report arrives as 0.0; zero is
// not a measured multiple and a negative one (loss-making) is not meaningful.
const fmtMultiple = (v, dec = 1) => {
  if (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) || Number(v) === 0) return 'Not reported';
  if (Number(v) < 0) return 'n/m (negative)';
  return fmtNumber(v, dec);
};

const fmtCurrency = (v, sym = '$', dec = 2) => {
  sym = safeSym(sym);
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  // Mega-cap and Indian-rupee market caps routinely exceed a trillion; without
  // this tier they read as "$3400.00B", which nobody parses at a glance.
  if (Math.abs(n) >= 1e12) return `${sym}${(n / 1e12).toFixed(2)}T`;
  if (Math.abs(n) >= 1e9) return `${sym}${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${sym}${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `${sym}${(n / 1e3).toFixed(2)}K`;
  return `${sym}${n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
};

// Percent formatter.
//   fraction=true  → value is a fraction (0.572 means 57.2%) and is multiplied by 100
//   fraction=false → value is ALREADY in percentage points (57.2 means 57.2%)
// The previous "auto-detect when |v|<=1.5" heuristic produced wrong upside %
// for small percent values (e.g. 0.57% rendered as 57%) and is removed.
const fmtPct = (v, dec = 1, fraction = true) => {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--';
  let n = Number(v);
  if (!Number.isFinite(n)) return '--';
  if (fraction) n *= 100;
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(dec)}%`;
};

const titleLabel = (s) => {
  if (!s) return '--';
  return String(s).replace(/[_-]/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
};
// Headings are sentence case: an engine label such as SCALE-IN PLAN reads "Scale-in plan".
const sentenceLabel = (s) => {
  if (!s) return '--';
  const t = String(s).replace(/_/g, ' ').trim().toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

// Sentence case that keeps acronyms and grades: "High Volatility" ->
// "High volatility", "ETF / Fund" -> "ETF / fund", "Risk Grade F" -> "Risk grade F".
const chipCase = (s) => String(s || '').split(' ').map((w, i) => (i === 0 || /^[A-Z0-9]+$/.test(w) ? w : w.toLowerCase())).join(' ');

const signalKey = (s) => String(s || '').toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_-]/g, '');
// A currency symbol comes from the server and is printed inside HTML by
// every formatter below: keep it to plain characters.
const safeSym = (s) => String(s ?? '').replace(/[<>&"'`]/g, '');
// A server value used as a class or data token: letters, digits, _ and -.
const cssToken = (s) => String(s ?? '').replace(/[^\w-]/g, '');
// Links from the server open only over http(s).
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : '');

// [SOURCE OF TRUTH] Canonical display metadata for every reconciled FinalAction.
// Every badge, card, table, and export derives its label / colour / behaviour
// from final_action through this map — NEVER from the raw scanner signal.
const ACTION_META = {
  STRONG_BUY: { label: 'STRONG BUY', key: 'strong_buy', tone: 'bull', funded: true, guidance: 'Full-size entry. Execute the plan below.' },
  BUY: { label: 'BUY', key: 'buy', tone: 'bull', funded: true, guidance: 'Standard entry. Execute the plan below.' },
  ACCUMULATE: { label: 'ACCUMULATE · SCALE-IN', key: 'accumulate', tone: 'bull', funded: true, guidance: 'Scale in gradually. Do NOT treat as a full-size BUY.' },
  WATCHLIST: { label: 'WATCHLIST', key: 'watchlist', tone: 'warn', funded: false, guidance: 'No new entry. Re-run the Decision after the blocking issue clears.' },
  HOLD_EXISTING: { label: 'HOLD EXISTING', key: 'hold_existing', tone: 'neutral', funded: false, guidance: 'Holders only, no new capital. Monitor the stop.' },
  HOLD: { label: 'HOLD EXISTING', key: 'hold_existing', tone: 'neutral', funded: false, guidance: 'Holders only, no new capital. Monitor the stop.' },
  AVOID: { label: 'AVOID', key: 'avoid', tone: 'bear', funded: false, guidance: 'Do not buy. Rejected by deep analysis.' },
  INVALID: { label: 'INVALID', key: 'invalid', tone: 'bear', funded: false, guidance: 'No valid setup: no entry or allocation.' },
  REDUCE: { label: 'REDUCE', key: 'reduce', tone: 'bear', funded: false, guidance: 'Trim exposure; no new capital.' },
  SELL: { label: 'SELL', key: 'sell', tone: 'bear', funded: false, guidance: 'Exit guidance only; no new capital.' },
};

// What each Decision asks a reader to do, in one plain sentence.
const ACTION_MEANING = {
  STRONG_BUY: 'Strongest setup: the model would open a full-size position.',
  BUY: 'Good setup: the model would open a standard-size position.',
  ACCUMULATE: 'Buy gradually in small steps, not a full position at once.',
  HOLD_EXISTING: 'Keep it if you own it; do not add new money.',
  HOLD: 'Keep it if you own it; do not add new money.',
  WATCHLIST: 'Do not buy yet. Wait for the main blocker to clear.',
  AVOID: 'Bearish score: do not buy. If you own it, see the holder action.',
  INVALID: 'No call: the data failed validation.',
};
const actionKey = (a) => String(a || '').toUpperCase().replace(/[\s-]+/g, '_');
// The model's track record in words a reader does not need a glossary for.
const EVIDENCE_PLAIN = {
  VALIDATED: 'Proven on past data', PARTIAL: 'Partly tested', NO_EDGE: 'Tested: no proven edge',
  UNTESTED: 'Not yet tested', STALE: 'Test out of date',
};
const evidencePlain = (ev) => {
  const base = EVIDENCE_PLAIN[String((ev && ev.status) || '').toUpperCase()]
    || (ev && ev.status_text) || 'Unknown';
  // An untested version of a model whose predecessor showed no edge says so.
  const failedBefore = ev && Array.isArray(ev.lineage) && ev.lineage.some((l) => l && l.status === 'NO_EDGE');
  return failedBefore && String(ev.status || '').toUpperCase() === 'UNTESTED'
    ? `${base} (earlier version: no edge)` : base;
};
const decisionMeaning = (action) => ACTION_MEANING[actionKey(action)] || '';

// Display words for the Decision's per-reader contract. Pure lookups of the
// backend's enum values; unknown values render as sent.
const STANCE_WORD = { BULLISH: ['Bullish score', 'bull'], NEUTRAL: ['Neutral score', ''], BEARISH: ['Bearish score', 'bear'], NONE: ['No score', ''] };
const NEW_MONEY_WORD = { ELIGIBLE: ['Eligible (risk-budget size)', 'bull'], WATCH: ['Not yet, watch', 'warn'], AVOID: ['Avoid', 'bear'], NONE: ['No call', ''] };
const HOLDER_WORD = { HOLD: ['Hold, keep the stop', ''], TRIM: ['Trim into strength', 'warn'], EXIT: ['Exit on strength', 'bear'], 'N/A': ['No call', ''] };
// Provider exchange codes in the names readers know.
const EXCHANGE_NAMES = { NYQ: 'NYSE', NMS: 'Nasdaq', NGM: 'Nasdaq', NCM: 'Nasdaq', NAS: 'Nasdaq', ASE: 'NYSE American', PCX: 'NYSE Arca', BTS: 'Cboe BZX', NSI: 'NSE', NSE: 'NSE', BSE: 'BSE', BOM: 'BSE', LSE: 'London', PNK: 'OTC', OQB: 'OTC', SNP: 'S&P Dow Jones Indices', CCC: 'CoinMarketCap', CME: 'CME', NYM: 'NYMEX', CCY: 'FX' };
const exchangeName = (code) => (code && code !== 'UNKNOWN' ? (EXCHANGE_NAMES[String(code).toUpperCase()] || code) : '');
function decisionContract(decision) {
  if (!decision || !decision.new_money) return null;
  const pick = (table, key) => table[String(key || '').toUpperCase()] || [titleLabel(key || ''), ''];
  const [stanceText, stanceTone] = pick(STANCE_WORD, decision.model_stance);
  const [newMoneyText, newMoneyTone] = pick(NEW_MONEY_WORD, decision.new_money);
  const [holderText, holderTone] = pick(HOLDER_WORD, decision.holder_action);
  return {
    stanceText, stanceTone, newMoneyText, newMoneyTone, holderText, holderTone,
    holdingsKnown: !!decision.holdings_known,
    primaryBlocker: decision.primary_blocker || null,
  };
}

const ACTION_WORD = {
  STRONG_BUY: 'Strong Buy', BUY: 'Buy', ACCUMULATE: 'Accumulate', HOLD_EXISTING: 'Hold', HOLD: 'Hold',
  WATCHLIST: 'Watchlist', AVOID: 'Avoid', INVALID: 'No call', REDUCE: 'Reduce', SELL: 'Sell',
};
const actionWord = (a) => ACTION_WORD[actionKey(a)] || titleLabel(a);
// [PURE] The score range a 0-100 score sits in, from LADDER.SCORE_BANDS floors
// (e.g. "55–65", "75+", "below 25"); config, never a page constant.
/* D3: one score scale on the page, out of 100. The scanner scores 0-10 and the
   decision ladder reads it times SCORE.TO_100 (a linear map: the LADDER bands are
   the SCORE tiers times that factor), so the page converts at display time and
   the engine keeps its own scale. */
function score100(s10) {
  if (s10 === null || s10 === undefined || s10 === '') return null;
  const n = Number(s10);
  const k = Number(thr('SCORE.TO_100'));
  return Number.isFinite(n) && k > 0 ? Math.max(0, Math.min(100, n * k)) : null;
}
function fmtScore100(s10) {
  const v = score100(s10);
  return v === null ? '' : String(Math.round(v));
}
/* Styling pass: one-line texts truncate with an ellipsis; the full text is
   offered as a title when (and only while) it does not fit. */
const TRUNCATING_TEXT = '.field-hint, .card-sub, .page-sub, .q-notice-t, .pk-co, .qpro-kicker-tail, .section-kicker-tail';
function titleIfTruncated(el) {
  if (!el) return;
  if (el.scrollWidth > el.clientWidth + 1) el.setAttribute('title', (el.textContent || '').trim());
  else if (el.dataset.ownTitle !== '1') el.removeAttribute('title');
}
function scoreBandRange(score) {
  const floors = (thr('LADDER.SCORE_BANDS') || []).map(([f]) => Number(f)).filter(Number.isFinite).sort((x, y) => y - x);
  const v = score == null || score === '' ? NaN : Number(score);
  if (!floors.length || !Number.isFinite(v)) return 'n/a';
  const i = floors.findIndex((f) => v >= f);
  if (i === -1) return `below ${floors[floors.length - 1]}`;
  return i === 0 ? `${floors[0]}+` : `${floors[i]}–${floors[i - 1]}`;
}

// Engine reasons are audit traces ("Composite score 62.7 → candidate
// ACCUMULATE"). Known shapes become plain sentences; anything else keeps its
// wording with the enum tokens made readable, so no reason is ever dropped.
const PLAIN_REASON_RULES = [
  [/^Composite score ([\d.]+) → candidate ([A-Z_]+)$/, (m) => {
    return `Overall score ${m[1]}/100 is in the ${actionWord(m[2])} range (${scoreBandRange(m[1])})`;
  }],
  [/^Risk Grade D — hold at ACCUMULATE \(reduced size\)$/, () => 'Risk grade D (high risk): kept at Accumulate, with a smaller position'],
  [/^Risk Grade D blocks fresh BUY — ([A-Z_]+) → ACCUMULATE \(reduced size\)$/, (m) => `Risk grade D (high risk): lowered from ${actionWord(m[1])} to Accumulate, with a smaller position`],
  [/^Risk Grade F blocks new-money BUY → ([A-Z_]+)$/, (m) => `Risk grade F (very high risk): no new money, moved to ${actionWord(m[1])}`],
  [/^Heavy insider selling → ([A-Z_]+) \(no new money\)$/, (m) => `Heavy insider selling: no new money, moved to ${actionWord(m[1])}`],
  [/^HOLD_EXISTING but no existing position → WATCHLIST$/, () => 'Neutral call and you do not hold it: Watchlist until the setup improves'],
  [/^AVOID but no existing position → WATCHLIST$/, () => 'Negative call and you do not hold it: Watchlist, do not buy'],
  [/^R\/R ([\d.]+) below min ([\d.]+) → ([A-Z_]+)$/, (m) => `Reward/risk ${m[1]}:1 is below the ${m[2]}:1 minimum: moved to ${actionWord(m[3])}`],
  [/^Score ([\d.]+) below min ([\d.]+) → ([A-Z_]+)$/, (m) => `Score ${m[1]} is below the ${m[2]} minimum: moved to ${actionWord(m[3])}`],
  [/^Bearish reversal \((.+)\) → ([A-Z_]+) \(wait for confirmation\)$/, (m) => `Bearish reversal pattern (${m[1]}): capped at ${actionWord(m[2])} until it is confirmed`],
  [/^DCF overvaluation (\d+)% \(>25%\) → ([A-Z_]+)$/, (m) => `Price is ${m[1]}% above its DCF value: capped at ${actionWord(m[2])}`],
  [/^(.+) → cap at ([A-Z_]+) \(wait for entry\)$/, (m) => `${m[1]}: capped at ${actionWord(m[2])} until a better entry`],
  [/^Target ([\d.]+) <= entry ([\d.]+) → ([A-Z_]+)$/, (m) => `Target ${m[1]} is at or below the ${m[2]} entry, so there is no upside: moved to ${actionWord(m[3])}`],
  [/^Stop ([\d.]+) >= entry ([\d.]+) → ([A-Z_]+)$/, (m) => `Stop ${m[1]} is at or above the ${m[2]} entry, so the plan has no valid stop: moved to ${actionWord(m[3])}`],
  [/^Invalid (stop_loss|target_price)=\S* → ([A-Z_]+)$/, (m) => `${m[1] === 'stop_loss' ? 'Stop' : 'Target'} is missing or invalid: moved to ${actionWord(m[2])}`],
  [/^Preliminary analysis; full checks were skipped\.?$/, () => 'Fast preview: news, insider, analyst and catalyst checks were skipped. Run the full analysis before acting'],
];
function plainReason(text) {
  const s = String(text || '').trim();
  for (const [re, fn] of PLAIN_REASON_RULES) {
    const m = s.match(re);
    if (m) return fn(m);
  }
  return s.replace(/\b(STRONG_BUY|HOLD_EXISTING|ACCUMULATE|WATCHLIST|BUY|AVOID|INVALID)\b/g, (t) => actionWord(t));
}

// The Decision says WHAT; the candle-timing layer says WHEN. When a funded
// Decision meets a "wait" from timing, say both in one line instead of
// leaving the reader to reconcile "Accumulate" with "Buy now: NO".
function entryTimingNote(data, action, sym = '$', target = null) {
  if (!['STRONG_BUY', 'BUY', 'ACCUMULATE'].includes(actionKey(action))) return null;
  const g = (data && data.beginner_guidance) || {};
  const buyNow = String(g.buy_now || '').toUpperCase();
  const pull = isPositive(g.pullback_entry) ? fmtPrice(g.pullback_entry, sym) : '';
  // Buying a breakout at or above the base target leaves no reward to it.
  const brkOk = isPositive(g.breakout_entry) && !(isPositive(target) && Number(g.breakout_entry) >= Number(target));
  const brk = brkOk ? fmtPrice(g.breakout_entry, sym) : '';
  if (buyNow === 'YES') return { tone: 'bull', text: 'Entry timing: the setup is actionable at today\'s price.' };
  if (buyNow.startsWith('ONLY AFTER')) {
    return { tone: 'warn', text: `Entry timing: wait for a confirming close${brk ? ` (breakout above ${brk})` : ''} before buying.` };
  }
  if (buyNow === 'NO') {
    const levels = [pull && `a pullback near ${pull}`, brk && `a breakout above ${brk}`].filter(Boolean).join(' or ');
    return { tone: 'warn', text: `Entry timing: weak today. Prefer ${levels || 'a better entry'} to buying at the current price.` };
  }
  return null;
}

// Resolve a recommendation's reconciled action metadata. Prefers final_action,
// then the server-normalised display_action, then (last resort) the raw signal.
function actionMeta(rec) {
  const decision = rec && rec.decision;
  const raw = (decision && decision.action) || (rec && (rec.final_action || rec.display_action || rec.signal)) || 'HOLD';
  const k = String(raw).toUpperCase().replace(/[\s-]+/g, '_');
  const base = ACTION_META[k] || { label: titleLabel(raw), key: signalKey(raw), tone: 'neutral', funded: false, guidance: '' };
  return decision ? { ...base, label: decision.label || base.label,
    qualifier: decision.qualifier || '', guidance: decision.caveat || base.guidance } : base;
}

// The allocation to TRUST is the post-deep-confirmation value. Fall back through
// the legacy fields only when the reconciled value is absent.
function finalAllocation(rec) {
  if (!rec) return 0;
  const a = rec.allocation_after != null ? rec.allocation_after : (rec.allocation != null ? rec.allocation : rec.position_value);
  return Number(a || 0);
}

// A pick is "funded" only when its reconciled action is fundable AND it carries
// a positive allocation after deep confirmation.
function isFundedPick(rec) {
  return actionMeta(rec).funded && finalAllocation(rec) > 0;
}

const firstUsable = (...vals) => {
  for (const v of vals) {
    if (v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v)) && Number(v) !== 0) return Number(v);
  }
  for (const v of vals) {
    if (v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
};

// True for a strictly positive, finite number — used to validate prices,
// stop-loss levels, share counts, and any quantity where 0/null/NaN is invalid.
const isPositive = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0;
};

/* ---------------------------------------------------------------------------
   QUOTE BOARD — exchange-style session data (Open / High / Low / Prev / LTP)
   Shared by the Key Market Moves card and the Deep-Dive tear sheet so both
   speak the same numbers, in the same format, with the same null handling.
   --------------------------------------------------------------------------- */

// Per-share price formatter. Unlike fmtCurrency (which compacts >= 1e3 into
// "1.45K" — correct for market cap, WRONG for a quote), this always prints the
// exact traded price with thousands separators, and widens the decimals for
// sub-unit instruments where 2dp would round a real quote to 0.00.
const fmtPrice = (v, sym = '$', dec = null) => {
  sym = safeSym(sym);
  if (v === null || v === undefined || v === '') return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  const places = dec != null ? dec : (Math.abs(n) > 0 && Math.abs(n) < 1 ? 4 : 2);
  return `${sym}${n.toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places })}`;
};

// Money formatter for allocations, capital, deployed cash and dollar risk:
// full thousands separators, never compacted (a $1,000 allocation must read
// "$1,000", not "$1.00K"). fmtCurrency's compaction is for market caps only.
const fmtMoney = (v, sym = '$', dec = 0) => {
  sym = safeSym(sym);
  if (v === null || v === undefined || v === '') return '--';
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  const sign = n < 0 ? '-' : '';
  return `${sign}${sym}${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
};

// Compact share-count formatter for volume ("25.8M", "1.2B") — never a currency.
const fmtCount = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
};

// Normalize any quote-shaped payload (movers row, tear-sheet price_chart.session,
// raw analysis object) into one canonical session quote. Every field is either a
// finite number or null — callers never have to re-check for NaN/""/undefined.
// `prevClose` is reconstructed from price + day change when the provider omits
// it, which is the common case on the broad-universe fallback feed.
function normalizeQuote(src = {}, fallback = {}) {
  const num = (...vals) => {
    for (const v of vals) {
      if (v === null || v === undefined || v === '') continue;
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
    return null;
  };
  const ltp = num(src.ltp, src.price, src.last, fallback.price);
  let prevClose = num(src.prev_close, src.previous_close, fallback.prev_close);
  let changePct = num(src.change_pct, fallback.change_pct);
  let changeAbs = num(src.change_abs, src.change, fallback.change_abs);

  // Fill in whichever leg of (prev, abs, pct) the feed left out. A prev close we
  // computed ourselves is flagged, because it must NOT count as evidence that
  // the feed carries session data — otherwise a price-only row would render a
  // quote board of four em-dashes and one number.
  const prevCloseReported = prevClose != null;
  if (prevClose == null && ltp != null && changeAbs != null) prevClose = ltp - changeAbs;
  if (prevClose == null && ltp != null && changePct != null && changePct !== -100) {
    prevClose = ltp / (1 + changePct / 100);
  }
  if (changeAbs == null && ltp != null && prevClose != null) changeAbs = ltp - prevClose;
  if (changePct == null && ltp != null && isPositive(prevClose)) {
    changePct = ((ltp - prevClose) / prevClose) * 100;
  }

  const low = num(src.day_low, src.low, fallback.day_low);
  const high = num(src.day_high, src.high, fallback.day_high);
  return {
    open: num(src.open, fallback.open),
    // Guard against an inverted range from a partial feed.
    low: low != null && high != null ? Math.min(low, high) : low,
    high: low != null && high != null ? Math.max(low, high) : high,
    prevClose,
    ltp,
    changeAbs,
    changePct,
    volume: num(src.volume, fallback.volume),
    rangeLow: num(src.range_low, src.week52_low, fallback.week52_low),
    rangeHigh: num(src.range_high, src.week52_high, fallback.week52_high),
    rangeBars: num(src.range_bars),
    date: src.date || fallback.date || null,
    prevCloseReported,
  };
}

// True when the feed actually carried session data worth rendering as a board.
// A derived prev close does not qualify: a board is only better than the plain
// stats line when it shows numbers the stats line doesn't already have.
const hasQuoteBoard = (q) => !!q
  && ([q.open, q.high, q.low].some((v) => v != null)
      || (q.prevCloseReported && q.prevClose != null));

// Where `value` sits between low and high, as a 0-100 percentage. Returns null
// when the band is degenerate (low === high) so the caller can skip the meter
// rather than draw a divide-by-zero artifact.
function rangePosition(low, high, value) {
  // Explicit null/'' rejection first: Number(null) and Number('') are both 0,
  // so a coercion-only guard would silently anchor a missing edge at zero and
  // draw a meter spanning the whole positive axis.
  const usable = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
  if (![low, high, value].every(usable)) return null;
  const lo = Number(low);
  const hi = Number(high);
  if (!(hi > lo)) return null;
  return Math.max(0, Math.min(100, ((Number(value) - lo) / (hi - lo)) * 100));
}

// Exchange-style quote board: Open / High / Low / Prev Close / LTP.
// LTP is the emphasised cell (it is the number a trader acts on); High and Low
// carry directional tint; every absent field degrades to a muted en-dash rather
// than disappearing, so the columns stay aligned across rows.
function quoteBoardHtml(q, sym = '$', opts = {}) {
  if (!hasQuoteBoard(q)) return '';
  const compact = opts.compact !== false;
  const cell = (label, value, cls = '', title = '') => `
    <div class="qb-cell ${cls}"${title ? ` title="${escapeHtml(title)}"` : ''}>
      <span class="qb-label">${escapeHtml(label)}</span>
      <span class="qb-value">${value == null ? '<span class="qb-na">--</span>' : escapeHtml(fmtPrice(value, sym))}</span>
    </div>`;
  const dirCls = q.changePct == null ? '' : (q.changePct > 0 ? 'up' : (q.changePct < 0 ? 'dn' : ''));
  const deltaBits = [];
  // Sign leads the currency symbol ("-$5.10", not "$-5.10") — the convention
  // every quote board and broker statement uses. The delta also inherits the
  // LTP's precision rather than deriving its own: a -0.81 move on a $12 stock
  // is "-$0.81", not the "-$0.8100" a magnitude-based rule would produce.
  if (q.changeAbs != null) {
    const deltaDp = q.ltp != null && Math.abs(q.ltp) >= 1 ? 2 : 4;
    deltaBits.push(`${q.changeAbs < 0 ? '-' : '+'}${fmtPrice(Math.abs(q.changeAbs), sym, deltaDp)}`);
  }
  if (q.changePct != null) deltaBits.push(fmtPct(q.changePct, 2, false));
  return `
    <div class="quote-board ${compact ? 'is-compact' : ''}" role="group" aria-label="Session quote">
      ${cell('Open', q.open)}
      ${cell('High', q.high, 'is-high', 'Session high')}
      ${cell('Low', q.low, 'is-low', 'Session low')}
      ${cell('Prev Close', q.prevClose, '', 'Previous session close')}
      <div class="qb-cell qb-ltp ${dirCls}" title="Last traded price">
        <span class="qb-label" title="Last traded price">Last</span>
        <span class="qb-value">${q.ltp == null ? '<span class="qb-na">--</span>' : escapeHtml(fmtPrice(q.ltp, sym))}</span>
        ${deltaBits.length ? `<span class="qb-delta">${escapeHtml(deltaBits.join('  '))}</span>` : ''}
      </div>
    </div>`;
}

// Horizontal band showing where the last price sits inside a low/high range.
// Used for both the intraday band and the longer trailing band.
function rangeMeterHtml(low, high, value, sym = '$', label = 'Day Range') {
  const pos = rangePosition(low, high, value);
  if (pos == null) return '';
  const tone = pos >= 66 ? 'hot' : (pos <= 33 ? 'cold' : 'mid');
  return `
    <div class="range-meter" role="img"
         aria-label="${escapeHtml(label)}: ${escapeHtml(fmtPrice(low, sym))} to ${escapeHtml(fmtPrice(high, sym))}, currently ${escapeHtml(fmtPrice(value, sym))} (${pos.toFixed(0)}% of range)">
      <span class="rm-label">${escapeHtml(label)}</span>
      <span class="rm-edge">${escapeHtml(fmtPrice(low, sym))}</span>
      <span class="rm-track">
        <span class="rm-fill ${tone}" style="width:${pos.toFixed(1)}%"></span>
        <span class="rm-thumb ${tone}" style="left:${pos.toFixed(1)}%"></span>
      </span>
      <span class="rm-edge">${escapeHtml(fmtPrice(high, sym))}</span>
    </div>`;
}

const normalizeVolumeRequirement = (text) => {
  const raw = String(text || '').trim();
  if (!raw) return '>= 1.5x avg';
  if (raw.includes('!"') || raw.includes('!') || /1\.5x\s*avg/i.test(raw)) return '>= 1.5x avg';
  return raw;
};

// Recompute risk/reward locally when the engine returns 0 or missing.
// Returns null if any input is invalid.
const computeRR = (entry, target, stop) => {
  if (!isPositive(entry) || !isPositive(target) || !isPositive(stop)) return null;
  const reward = Number(target) - Number(entry);
  const risk = Number(entry) - Number(stop);
  if (!Number.isFinite(reward) || !Number.isFinite(risk) || risk <= 0 || reward <= 0) return null;
  return reward / risk;
};

// Merge data-quality tiers; FAIL beats WARN beats PASS.
const dqMax = (a, b) => {
  const order = { PASS: 0, WARN: 1, FAIL: 2 };
  return (order[a] || 0) >= (order[b] || 0) ? a : b;
};

// [HOLD-INFLATION-FIX] Build a list of validation issues from the analysis
// payload. Each issue = { severity: 'FAIL'|'WARN', kind: 'critical'|'optional',
// text, code }.
// CRITICAL kind = fields the model NEEDS to make any decision (price, history,
//   stop, exception-leak protection, required sections). FAIL here is real.
// OPTIONAL kind = nice-to-have data (analyst coverage, Monte Carlo internal
//   consistency, peer comparison, news depth). Missing/inconsistent OPTIONAL
//   data should LOWER CONFIDENCE but must NOT force PROVISIONAL HOLD — that
//   was the root cause of the "everything is HOLD" inflation.
function buildValidationIssues(data, extras = {}) {
  const issues = [];
  const trade = data.trade_setup || {};
  const valuation = data.valuation || {};
  const mi = data.market_intel || {};
  const mc = data.monte_carlo || {};
  const currentPrice = Number(data.current_price || 0);
  const entry = Number(trade.entry_price || currentPrice);
  const target = Number(trade.target_base || valuation.target_base || mi.target_mean || 0);
  const stop = Number(trade.stop_loss || 0);

  const requiredMissing = Array.isArray(extras.requiredMissing) ? extras.requiredMissing : [];
  if (requiredMissing.length) {
    issues.push({ severity: 'FAIL', kind: 'critical', code: 'required_sections_missing', text: `Required sections missing: ${requiredMissing.join(', ')}` });
  }

  // Stop-loss validity. A stop is only REQUIRED when the engine published a
  // long stance (buy tier, hold, watchlist). An exit-stance verdict (SELL /
  // REDUCE / AVOID) deliberately publishes no long levels — flagging that as
  // "critical data missing" turned every honest sell into DATA QUALITY FAIL.
  const exitStance = ['SELL', 'STRONG_SELL', 'REDUCE', 'AVOID'].includes(String(data.final_action || data.signal || '').toUpperCase());
  const levelsExpected = trade.levels_available !== false && !exitStance;
  if (levelsExpected && (isPositive(target) || data.signal) && !isPositive(stop)) {
    const g = data.beginner_guidance || {};
    const fallbackStop = isPositive(g.long_term_stop) || isPositive(g.tactical_stop)
      || isPositive(g.existing_holder_stop) || isPositive(g.pullback_stop);
    if (fallbackStop) {
      issues.push({ severity: 'WARN', kind: 'critical', code: 'stop_fallback_used', text: 'Primary trade stop is missing/invalid; falling back to the holder / tactical / pullback stop.' });
    } else {
      issues.push({ severity: 'FAIL', kind: 'critical', code: 'no_stop_loss', text: 'No valid stop-loss available (primary, holder, tactical or pullback).' });
    }
  }

  // Risk/reward consistency. When the engine says there is no long reward
  // (valuation target at or below the entry), a zero R:R is a finding, not a
  // computation failure — and nothing may be "recomputed" from another target.
  const engineRR = Number(trade.risk_reward_ratio || 0);
  if (trade.has_long_reward === false) {
    issues.push({ severity: 'WARN', kind: 'optional', code: 'no_long_reward', text: 'Valuation target sits at or below the entry reference: no long reward, so R:R is 0 by definition.' });
  } else if (isPositive(entry) && isPositive(target) && isPositive(stop) && !isPositive(engineRR)) {
    issues.push({ severity: 'WARN', kind: 'optional', code: 'rr_recomputed', text: 'Engine returned no risk/reward despite valid entry, target and stop; recomputed locally.' });
  }

  // Analyst rating inferred from 0 analysts.
  // [HOLD-INFLATION-FIX] This is OPTIONAL data — missing analyst coverage
  // should NOT trigger PROVISIONAL HOLD. Many tradeable mid-caps have thin
  // analyst coverage. Demoted FAIL→WARN(optional) so it lowers confidence
  // but does not block the recommendation.
  // Coverage is the provider's analyst count; the rating buckets are often not
  // returned even when 50 analysts cover the name (MSFT), so they cannot be
  // the test for "no coverage".
  const analystCount = Number(mi.analyst_count || 0)
    || ((Number(mi.buy_ratings) || 0) + (Number(mi.hold_ratings) || 0) + (Number(mi.sell_ratings) || 0));
  if (analystCount === 0 && mi.analyst_rating && String(mi.analyst_rating).toUpperCase() !== 'HOLD') {
    issues.push({ severity: 'WARN', kind: 'optional', code: 'analyst_data_unavailable', text: `Analyst rating "${mi.analyst_rating}" reported with 0 analysts; rating suppressed (optional data).` });
  }

  // Monte Carlo internal consistency.
  // [HOLD-INFLATION-FIX] Monte Carlo is OPTIONAL enrichment — if its output
  // looks inconsistent we suppress the MC block but keep the core decision.
  // Was FAIL → demoted to WARN(optional) for the same reason as analyst data.
  if (mc && isPositive(mc.mean_price) && mc.expected_return != null) {
    // expected_return is the MEAN terminal return in PERCENT. It is checked
    // against the mean price, never against the median: for a skewed
    // distribution mean and median legitimately differ, even in sign.
    const meanMove = (Number(mc.mean_price) - currentPrice) / Math.max(currentPrice, 1e-9);
    const erNorm = Number(mc.expected_return) / 100;
    const inconsistent = Math.abs(erNorm - meanMove) > 0.02
      || (Number(mc.median_price) > currentPrice && Number(mc.probability_profit) < 0.05);
    if (inconsistent) {
      issues.push({ severity: 'WARN', kind: 'optional', code: 'monte_carlo_inconsistent', text: 'Monte Carlo output looks inconsistent (mean return vs mean price); block suppressed (optional data).' });
    }
  }

  if (extras.newsLimited) {
    issues.push({ severity: 'WARN', kind: 'optional', code: 'news_limited', text: 'Ticker-specific news is limited; sentiment confidence reduced.' });
  }

  // Recommendation vs timeframe consistency
  const g = data.beginner_guidance || {};
  if (String(g.buy_now || '').toUpperCase() === 'ONLY AFTER CONFIRMATION'
      && String(g.long_tf || '').toLowerCase() === 'accumulate') {
    issues.push({ severity: 'WARN', kind: 'optional', code: 'timeframe_conflict', text: 'Long-term guidance "Accumulate" conflicts with "Only after confirmation"; downgraded.' });
  }

  // Exception-text leakage check — never show raw Python tracebacks in the report.
  const exceptionMarkers = [
    'Traceback (most recent call last)',
    'object has no attribute',
    'AttributeError',
    'TypeError:',
  ];
  const textFields = [
    data.recommendation_text,
    (data.thesis || {}).summary,
    (data.signal_explanation || {}).primary_reason,
  ].filter(Boolean);
  for (const txt of textFields) {
    if (exceptionMarkers.some((m) => String(txt).includes(m))) {
      issues.push({ severity: 'FAIL', kind: 'critical', code: 'exception_leak', text: 'A Python exception was about to leak into the report; text suppressed.' });
      break;
    }
  }

  return issues;
}

// [HOLD-INFLATION-FIX] Pure: split validation issues into critical vs
// optional buckets and compute the effective DQ override. Only CRITICAL FAILs
// drive DQ=FAIL (which is what triggers PROVISIONAL HOLD downstream). Optional
// FAILs/WARNs flow into the WARN bucket and lower confidence cosmetically but
// do not block BUY/ACCUMULATE.
function summarizeValidationIssues(issues) {
  const criticalFail = issues.filter((i) => i.severity === 'FAIL' && i.kind !== 'optional');
  const optionalFail = issues.filter((i) => i.severity === 'FAIL' && i.kind === 'optional');
  const warn = issues.filter((i) => i.severity === 'WARN');
  const criticalPass = criticalFail.length === 0;
  // optionalPass: PASS if no optional fails AND no optional warns,
  // PARTIAL if any warn(optional)/fail(optional), FAIL if optional fails > 2.
  let optionalStatus;
  if (optionalFail.length >= 2) optionalStatus = 'FAIL';
  else if (optionalFail.length || warn.some((w) => w.kind === 'optional')) optionalStatus = 'PARTIAL';
  else optionalStatus = 'PASS';
  let dqOverride;
  if (!criticalPass) dqOverride = 'FAIL';
  else if (optionalStatus === 'FAIL' || warn.length) dqOverride = 'WARN';
  else dqOverride = null;
  return {
    criticalFail,
    optionalFail,
    warn,
    criticalStatus: criticalPass ? 'PASS' : 'FAIL',
    optionalStatus,
    dqOverride,
    issueCodes: issues.map((i) => i.code).filter(Boolean),
  };
}

// [HOLD-INFLATION-FIX] Pure: classify the final action / produce a precise
// label that explains WHY a non-BUY happened — WITHOUT overriding the engine's
// verdict. The engine's `final_action` (BUY/ACCUMULATE/WATCHLIST/HOLD/AVOID) is
// the source of truth; risk grade, R/R, data status etc. are surfaced as gates
// (context), never used to relabel a WATCHLIST/ACCUMULATE as HOLD.
//   - data_quality_critical_fail  → NOT_CONFIRMED ("data issue, not bearish")
//   - engine HOLD + risk F        → HOLD_EXISTING_STRICT_STOP
//   - engine ACCUMULATE + risk D/F→ LOW_CONVICTION_ACCUMULATE
//   - engine WATCHLIST            → WATCHLIST (with gate context)
// Returns { label, headline, gates: [{name,fired,detail}], severity }.
function classifyFinalAction(data, validationSummary) {
  const finalAction = String(data.final_action || data.signal || '').toUpperCase();
  const reasons = Array.isArray(data.final_action_reasons) ? data.final_action_reasons : [];
  const risk = data.risk || {};
  const grade = String(risk.risk_grade || '').toUpperCase();
  const trade = data.trade_setup || {};
  const rr = Number(trade.risk_reward_ratio || 0);
  const composite = Number((data.scores || {}).composite_score || 0);
  const buyNow = data.beginner_guidance ? String(data.beginner_guidance.buy_now || '').toUpperCase() : '';
  const targetUp = Number(data.target_upside_pct || (data.valuation || {}).target_upside_pct || 0);
  const dataStatus = String(data.data_status || '').toUpperCase();

  const gates = [];
  const fire = (name, detail) => gates.push({ name, fired: true, detail });

  if (validationSummary && validationSummary.criticalStatus === 'FAIL') {
    fire('critical_data_failed', (validationSummary.criticalFail[0] || {}).text || 'critical data missing');
  }
  if (dataStatus && dataStatus !== 'OK' && dataStatus !== '') {
    fire('provider_data_status', dataStatus.toLowerCase().replace(/_/g, ' '));
  }
  if (grade === 'F') fire('risk_grade_F', 'Risk Grade F blocks new money');
  else if (grade === 'D') fire('risk_grade_D', 'Risk Grade D caps fresh BUY at ACCUMULATE');
  const rrMin = thr('RR.RESEARCH_MIN');
  if (rr > 0 && rr < rrMin) fire('risk_reward_below_min', `R/R ${rr.toFixed(2)} below ${fmtThreshold(rrMin, 'rr')} minimum`);
  if (composite > 0 && composite < 45) fire('composite_below_avoid', `Composite ${composite.toFixed(1)} < 45 (avoid band)`);
  else if (composite >= 45 && composite < 55) fire('composite_hold_band', `Composite ${composite.toFixed(1)} in 45-55 neutral band`);
  if (buyNow === 'NO' || buyNow.startsWith('ONLY AFTER')) fire('buy_now_no', `Timing says ${buyNow.toLowerCase()}`);
  if (Number.isFinite(targetUp) && targetUp < 0) fire('negative_target_upside', `Target ${targetUp.toFixed(1)}% below current price`);

  // Reasons already returned by the engine — surface their summary too.
  reasons.forEach((r) => {
    const txt = String(r);
    if (/dcf overval/i.test(txt)) fire('dcf_overvaluation', txt);
    if (/insider/i.test(txt) && /sell/i.test(txt)) fire('heavy_insider_selling', txt);
    if (/bearish/i.test(txt) && /reversal/i.test(txt)) fire('bearish_reversal', txt);
  });

  // The engine decision owns the verdict. The checks above remain context,
  // never a second UI recommendation engine.
  if (data.decision && data.decision.action) {
    const decision = data.decision;
    // The hero already carries the caveat; here the headline is what capped
    // the call, in plain words (the score-band line is the starting point,
    // not a cap, so it is left to the reasons list).
    const caps = [...(decision.blocking || []), ...(decision.reasons || []).slice(1)].filter(Boolean);
    // Rules come from the backend, with its own statement and remedy. The
    // browser-rebuilt checks above are only a fallback for older payloads.
    // An empty list is still the engine's answer ("no rule fired"): the
    // browser-rebuilt checks are only for payloads that predate `blockers`.
    const ruleGates = Array.isArray(decision.blockers)
      ? decision.blockers.map((b) => ({ name: b.code, fired: true, detail: b.text, remedy: b.remedy, fromEngine: true }))
      : null;
    const headline = decision.primary_blocker ? decision.primary_blocker.text
      : (caps.map(plainReason).join('. ')
        || ((decision.reasons || [])[0] ? plainReason(decision.reasons[0]) : '')
        || decision.caveat || decision.label);
    return { label: decision.label, headline,
      gates: ruleGates || gates, finalAction: decision.action, reasons: decision.reasons || reasons,
      fromDecision: true, engineRules: !!ruleGates };
  }

  // Choose a precise label. CRITICAL: the engine's `final_action` is the
  // source of truth for the verdict. We NEVER downgrade a WATCHLIST /
  // ACCUMULATE / BUY verdict to HOLD just because of a risk grade or R/R —
  // the engine already weighed those. Risk grade, R/R, etc. are surfaced as
  // *gates* (the "why not BUY" context), not as a relabel. The strict-stop
  // nuance only applies when the engine itself returns HOLD/HOLD_EXISTING.
  let label = finalAction || 'NOT_CONFIRMED';
  let headline = '';
  if (validationSummary && validationSummary.criticalStatus === 'FAIL') {
    label = 'NOT_CONFIRMED';
    headline = 'Critical data missing: recommendation not confirmed. This is a data issue, not a bearish signal.';
  } else if (finalAction === 'HOLD' || finalAction === 'HOLD_EXISTING') {
    if (grade === 'F') {
      label = 'HOLD_EXISTING_STRICT_STOP';
      headline = 'Risk Grade F: hold existing with a strict stop; no new money.';
    } else if (gates.length === 0) {
      label = 'HOLD_EXISTING';
      headline = `Composite ${composite.toFixed(1)} in neutral band: hold existing, no fresh entry.`;
    } else {
      label = 'HOLD_EXISTING';
      headline = `Hold existing; blocking gates: ${gates.slice(0, 3).map((g) => g.name).join(', ')}.`;
    }
  } else if (finalAction === 'WATCHLIST') {
    label = 'WATCHLIST';
    headline = gates.length
      ? `On watchlist, not a buy yet. ${gates.slice(0, 3).map((g) => g.detail).join('; ')}.`
      : `On watchlist: composite ${composite.toFixed(1)}, waiting for a cleaner entry.`;
  } else if (finalAction === 'ACCUMULATE') {
    if (grade === 'D' || grade === 'F') {
      label = 'LOW_CONVICTION_ACCUMULATE';
      headline = `Accumulate with caution: Risk Grade ${grade}, composite ${composite.toFixed(1)}, R/R ${rr.toFixed(2)}:1.`;
    } else {
      label = 'ACCUMULATE';
      headline = `Accumulate: composite ${composite.toFixed(1)}, R/R ${rr.toFixed(2)}:1, Risk Grade ${grade || '--'}.`;
    }
  } else if (label === 'BUY' || label === 'STRONG_BUY') {
    headline = `${label.replace('_', ' ')}: composite ${composite.toFixed(1)}, R/R ${rr.toFixed(2)}:1, Risk Grade ${grade || '--'}.`;
  } else if (label === 'AVOID' || label === 'SELL') {
    headline = `${label}: composite ${composite.toFixed(1)}, R/R ${rr.toFixed(2)}:1.`;
  } else {
    headline = label;
  }

  return { label, headline, gates, finalAction, reasons };
}

// Strip raw exception messages from any free-form string before display.
function sanitizeExceptionText(s) {
  if (!s) return '';
  const str = String(s);
  const markers = ['Traceback (most recent call last)', 'object has no attribute', 'AttributeError', 'TypeError:'];
  if (markers.some((m) => str.includes(m))) return '';
  return str;
}

function renderValidationBanner(issues) {
  if (!issues.length) return '';
  const hasFail = issues.some((i) => i.severity === 'FAIL');
  const tone = hasFail ? 'bear' : 'neutral';
  const headline = hasFail
    ? 'Report is PROVISIONAL: critical validation issues detected. Do NOT treat the recommendation as fully reliable.'
    : 'Report flagged: minor validation warnings; review before acting.';
  return `
    <article class="section-card validation-banner tone-${tone}">
      <h3 class="section-kicker">Validation</h3>
      <p class="validation-headline"><strong>${escapeHtml(headline)}</strong></p>
      <ul>
        ${issues.map((i) => `<li><span class="${i.severity === 'FAIL' ? 'sev-fail' : 'sev-warn'}">${i.severity}</span>: ${escapeHtml(i.text)}</li>`).join('')}
      </ul>
      ${hasFail ? `<p class="muted validation-foot">Recommendation, confidence, and price targets are provisional until the failures above are resolved.</p>` : ''}
    </article>`;
}

const directionClass = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  if (n > 0) return 'bull';
  if (n < 0) return 'bear';
  return '';
};



const upsidePct = (target, base) => {
  const t = numOrNull(target); const b = numOrNull(base);
  if (t == null || b == null || b === 0) return null;
  return ((t - b) / b) * 100;
};

const normalizeTicker = (symbol = '') => String(symbol)
  .trim()
  .toUpperCase()
  .replace(/^\$/, '')
  .replace(/\.NS$/, '');

const isAnalyzableTicker = (symbol = '') => /^[A-Z][A-Z0-9.-]{0,9}$/.test(normalizeTicker(symbol));

function openTickerTearSheet(symbol) {
  const ticker = normalizeTicker(symbol);
  if (!ticker || !isAnalyzableTicker(ticker)) return;
  const input = $('#an-ticker');
  if (input) input.value = ticker;
  // A new name is a new report: start at the top, not where the last one was left.
  _viewScroll.analyze = 0;
  switchView('analyze');
  runAnalysis();
}

// Keep the --topbar-h CSS var in sync with the real (possibly wrapped) header
// height so sticky content always clears the bar instead of hiding behind it.
function syncTopbarHeight() {
  if (typeof document === 'undefined') return;
  const topbar = document.querySelector('.topbar');
  if (!topbar) return;
  const apply = () => {
    const h = Math.round(topbar.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty('--topbar-h', `${h}px`);
  };
  apply();
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(apply).observe(topbar);
  } else if (typeof window !== 'undefined') {
    window.addEventListener('resize', apply);
  }
}

// Views. The four primary steps are tabs; Model Evidence ("track") and the
// "How QUANTA works" guide are secondary views reached from links, so no tab
// is selected while either is open.
const VIEW_ALIASES = {
  evidence: 'track', research: 'analyze', portfolio: 'picks', overview: 'dashboard',
  'how-it-works': 'guide', about: 'guide', help: 'guide',
  'my-portfolio': 'holdings', robinhood: 'holdings',
};
const VIEWS = ['dashboard', 'discover', 'analyze', 'picks', 'holdings', 'track', 'guide'];
const SECONDARY_VIEWS = ['track', 'guide'];
let _previousView = 'dashboard';
// Each view keeps its own scroll position, so Back (or a tab) returns the
// reader to where they were instead of the top of the page.
const _viewScroll = {};

// opts.replace: rewrite the current history entry instead of adding one (initial
// load, redirects). Any other change of view is a navigation the reader chose,
// so it gets its own entry and Back retraces it.
const switchView = (rawId, opts = {}) => {
  const id = VIEW_ALIASES[rawId] || rawId;
  if (!VIEWS.includes(id)) return;
  const current = $('.view.active')?.id?.replace('view-', '');
  if (current && current !== id) {
    _previousView = current;
    if (typeof window !== 'undefined') _viewScroll[current] = window.scrollY || 0;
  }
  $$('.view').forEach((v) => v.classList.remove('active'));
  $(`#view-${id}`)?.classList.add('active');
  $$('.nav-tab').forEach((b) => {
    const active = b.dataset.view === id;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
    b.tabIndex = active ? 0 : -1;
  });
  // Secondary links reflect the open view without claiming a tab.
  $$('[data-view-link]').forEach((a) => {
    if (a.dataset.viewLink === id) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  if (id === 'track') loadTrackRecord();
  if (id === 'guide') { bindGuide(); loadGuideEvidence(); }
  // My Portfolio lives in its own script (my-portfolio.js); it loads its data on open.
  if (id === 'holdings' && typeof window !== 'undefined' && window.QuantaHoldings) window.QuantaHoldings.open();
  if (typeof window !== 'undefined' && id) {
    const desired = `#${id}`;
    // A view may keep its own sub-route after a slash (My Portfolio: #holdings/plan).
    if (window.location.hash !== desired && !window.location.hash.startsWith(`${desired}/`)) {
      const navigated = !!current && current !== id && !opts.replace;
      try {
        if (navigated) window.history.pushState({ view: id }, '', desired);
        else window.history.replaceState({ view: id }, '', desired);
      } catch (_) { /* sandboxed frames may refuse history writes */ }
    }
    if (current && current !== id && typeof window.scrollTo === 'function') {
      try { window.scrollTo({ top: _viewScroll[id] || 0, behavior: 'instant' }); } catch (_) { /* jsdom */ }
    }
  }
};

// Links that open a view (footer, next-step row, evidence lines). Delegated so
// links rendered later (tear sheet, scan results) work without re-wiring.
// Field-level error: text under the field, aria-invalid on it, and the error
// referenced by aria-describedby (the error node's role="alert" announces it).
function setFieldError(inputSel, errorSel, message) {
  const input = $(inputSel);
  const err = $(errorSel);
  if (!input || !err) return;
  if (message) {
    err.innerHTML = `${warnIconSvg('bear')}<span>${escapeHtml(message)}</span>`;
    err.hidden = false;
    input.setAttribute('aria-invalid', 'true');
  } else {
    err.textContent = '';
    err.hidden = true;
    input.removeAttribute('aria-invalid');
  }
}

// Busy feedback for long-running submits: while a run button is disabled it
// says what it is doing and exposes aria-busy; every code path that re-enables
// it restores the label, because the label follows the disabled state itself.
function bindBusyLabels() {
  if (typeof MutationObserver === 'undefined') return;
  [['#btn-analyze', 'Building…'], ['#btn-scan', 'Scanning…']].forEach(([sel, busy]) => {
    const btn = $(sel);
    if (!btn) return;
    const idle = btn.textContent.trim();
    const sync = () => {
      btn.textContent = btn.disabled ? busy : idle;
      if (btn.disabled) btn.setAttribute('aria-busy', 'true'); else btn.removeAttribute('aria-busy');
    };
    new MutationObserver(sync).observe(btn, { attributes: true, attributeFilter: ['disabled'] });
  });
}

// After a link-driven route change, move focus to the new page's heading so
// keyboard and screen-reader users start where the content starts.
function focusViewHeading(id) {
  const view = $(`#view-${VIEW_ALIASES[id] || id}`);
  const h = view && view.querySelector('h1');
  if (!h) return;
  if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1');
  h.focus({ preventScroll: true });
}

// The deferred focus below runs after the view switch settles. Skip it if the
// user has already moved focus themselves (an arrow key on the tabs, a click
// elsewhere): a late focus() would otherwise steal it.
function focusIfUntouched(origin, fn) {
  setTimeout(() => {
    const a = document.activeElement;
    if (!a || a === document.body || a === origin || !a.getClientRects().length) fn();
  }, 0);
}

function bindViewLinks() {
  document.addEventListener('click', (ev) => {
    const link = ev.target.closest?.('[data-view-link]');
    if (link) {
      ev.preventDefault();
      switchView(link.dataset.viewLink);
      const focusSel = link.dataset.focus;
      focusIfUntouched(link, () => (focusSel ? $(focusSel)?.focus() : focusViewHeading(link.dataset.viewLink)));
      return;
    }
    if (ev.target.closest?.('[data-back-view]')) {
      ev.preventDefault();
      const back = _previousView && !SECONDARY_VIEWS.includes(_previousView) ? _previousView : 'dashboard';
      switchView(back);
      focusIfUntouched(ev.target.closest('[data-back-view]'), () => focusViewHeading(back));
    }
  });
}

// ---------------------------------------------------------------------------
// HOW QUANTA WORKS — the guide's only live block. It restates what the
// evidence registry and ledger already say, in the guide's words; it adds no
// statistics and never claims a validation the registry does not record.
// ---------------------------------------------------------------------------
// One sentence of live status under the guide's trust cards, taken from the
// ledger: whether any model is validated, and how many calls are measured.
function renderGuideEvidence(data) {
  const box = $('#guide-evidence');
  const meta = $('#guide-evidence-meta');
  if (!box) return;
  if (!data || data.error) {
    if (meta) { meta.textContent = 'Status unavailable'; meta.dataset.state = 'error'; }
    const nums = $('#guide-evidence-nums'); if (nums) nums.innerHTML = '';
    box.textContent = data && data.unreachable
      ? 'The live status could not be loaded. Every Decision still shows its own evidence line.'
      : 'The evidence registry could not be read right now. Every Decision still shows its own evidence line.';
    return;
  }
  const engines = Array.isArray(data.engines) ? data.engines : [];
  const totals = data.totals || {};
  const minSample = Number(data.min_sample) || 30;
  const n = (v) => (Number.isFinite(Number(v)) ? fmtNumber(Number(v), 0) : '0');
  const validated = engines.filter((e) => e.evidence && e.evidence.permits_recommendation);
  const resolved = Number(totals.resolved) || 0;
  const model = !engines.length
    ? 'No model has an evidence record yet.'
    : !validated.length
      ? 'No QUANTA model is proven yet, so every Buy and Accumulate call carries the <span class="hw-tag">Model not yet validated</span> flag.'
      : validated.length === engines.length
        ? 'Every QUANTA model shown in the track record is validated on past data.'
        : `${escapeHtml(validated.map((e) => e.label || e.engine).join(', '))} ${validated.length === 1 ? 'is' : 'are'} validated; the other models are not.`;
  const calls = `<b>${n(totals.recorded)}</b> calls recorded, <b>${n(resolved)}</b> measured so far against ${escapeHtml(trackBenchmarkList(data))}`
    + (resolved < minSample ? ` (results count after ${minSample}).` : '.');
  if (meta) { meta.dataset.state = 'live'; meta.textContent = 'Live status'; }
  box.innerHTML = `${model} ${calls}`;
  const nums = $('#guide-evidence-nums');
  if (nums) {
    nums.innerHTML = [[n(totals.recorded), 'calls recorded'], [n(resolved), 'measured so far'], [n(minSample), 'measured calls before results count']]
      .map(([v, k]) => `<div><b>${v}</b><span>${k}</span></div>`).join('');
  }
}

// ---- Guide demonstrations (progressive enhancement; the static markup is
// each demo's finished state, so nothing is hidden without JS). Every rule
// below restates an engine rule for example inputs; tests/unit/guide.test.cjs
// checks them against the engine source.
const GUIDE_CALL_LABEL = {
  strong: 'Strong Buy', buy: 'Buy', accumulate: 'Accumulate', hold: 'Hold', watchlist: 'Watchlist', avoid: 'Avoid',
};
const GUIDE_CALL_TONE = { strong: 'bull', buy: 'bull', accumulate: 'bull', hold: 'neutral', watchlist: 'warn', avoid: 'bear' };
const GUIDE_FUNDED = ['strong', 'buy', 'accumulate'];
// The candidate floors (thresholds LADDER.SCORE_BANDS). Below the HOLD floor is
// Avoid for everyone.
const GUIDE_BAND_KEYS = { STRONG_BUY: 'strong', BUY: 'buy', ACCUMULATE: 'accumulate', HOLD: 'hold' };
const GUIDE_BANDS = [
  ...(thr('LADDER.SCORE_BANDS') || [])
    .filter(([, name]) => GUIDE_BAND_KEYS[name])
    .map(([floor, name]) => [floor, GUIDE_BAND_KEYS[name]]),
  [-Infinity, 'avoid'],
];
const guideBandFloor = (name) => ((thr('LADDER.SCORE_BANDS') || []).find(([, n]) => n === name) || [])[0];
// The Research reward/risk hurdle, and the REDUCE floor below which a holder's
// low score plus a red flag is an exit review (engine/blockers.py TRIM_FLOOR).
const GUIDE_MIN_RR = thr('RR.RESEARCH_MIN');
const GUIDE_EXIT_FLOOR = guideBandFloor('REDUCE');

const guideClamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function guideCandidate(score) {
  const s = guideClamp(Number(score) || 0, 0, 100);
  return GUIDE_BANDS.find(([floor]) => s >= floor)[1];
}

// engine/decision_engine.resolve_final_action with the overrides of
// engine/risk_overrides.py in their order, then engine/blockers.holder_rule.
function guideDecide({ score = 68, grade = 'ok', insider = false, flag = false, lowRr = false, owned = false } = {}) {
  const s = Math.round(guideClamp(Number(score) || 0, 0, 100));
  const start = guideCandidate(s);
  const down = owned ? 'hold' : 'watchlist';
  const L = GUIDE_CALL_LABEL;
  const steps = [{ text: `Score ${s} starts as ${L[start]}.` }];
  const codes = [];
  let c = start;
  const funded = () => GUIDE_FUNDED.includes(c);
  const rule = (text) => steps.push({ rule: true, text });
  if (funded() && grade === 'F') {
    c = down; codes.push('RISK_GRADE_F');
    rule(`Risk grade F allows no new money: ${L[c]}.`);
  } else if (funded() && grade === 'D') {
    if (c === 'strong' || c === 'buy') { c = 'accumulate'; rule('Risk grade D caps a fresh buy at Accumulate, at reduced size.'); }
    else rule('Risk grade D keeps it at a reduced Accumulate size.');
  }
  if (funded() && insider) { c = down; rule(`Heavy insider selling blocks new money: ${L[c]}.`); }
  if (funded() && flag) {
    if (c === 'strong' || c === 'buy') c = 'accumulate';
    rule('A bearish reversal or a price over 25% above DCF value caps it at Accumulate.');
  }
  if (funded() && lowRr) { c = down; rule(`Reward/risk under ${GUIDE_MIN_RR} : 1 means no new money: ${L[c]}.`); }
  if (c === 'hold' && !owned) { c = 'watchlist'; steps.push({ text: 'A hold-band score for someone who owns none shows as Watchlist.' }); }
  steps.push({ final: true, text: steps.length === 1 ? `No rule lowered it. Final: ${L[c]}.` : `Final: ${L[c]}.` });

  const newMoney = funded() ? 'Eligible' : c === 'avoid' ? 'Avoid' : 'Watch';
  // The holder rule reads the score and the measured red flags.
  const redFlag = insider || flag;
  const holder = s < GUIDE_EXIT_FLOOR && redFlag ? 'Exit review'
    : s < 45 ? 'Trim review'
      : codes.includes('RISK_GRADE_F') ? 'Trim review' : 'Hold';
  return { call: c, label: L[c], tone: GUIDE_CALL_TONE[c], start, newMoney, holder, steps };
}

function guideLabInputs() {
  const checked = (sel) => !!$(sel)?.checked;
  return {
    score: $('#hw-score')?.value,
    grade: ($('#view-guide input[name="hw-grade"]:checked') || {}).value || 'ok',
    insider: checked('#hw-insider'), flag: checked('#hw-flag'), lowRr: checked('#hw-rr'), owned: checked('#hw-owned'),
  };
}

function renderGuideDecision() {
  const input = $('#hw-score');
  if (!input) return null;
  const r = guideDecide(guideLabInputs());
  const out = $('#hw-score-out'); if (out) out.textContent = String(Math.round(Number(input.value) || 0));
  const bandKey = r.start === 'strong' ? 'strong' : r.start;
  $$('#view-guide .hw-bands [data-band]').forEach((b) => b.classList.toggle('is-active', b.dataset.band === bandKey));
  const v = $('#hw-verdict'); if (v) { v.textContent = r.label; v.dataset.tone = r.tone; }
  const nm = $('#hw-newmoney'); if (nm) nm.textContent = r.newMoney;
  const h = $('#hw-holder'); if (h) h.textContent = r.holder;
  const why = $('#hw-why');
  if (why) {
    why.innerHTML = r.steps.map((st, i) => `<li class="${st.rule ? 'is-rule' : ''}${st.final ? ' is-final' : ''}" style="--i:${i}">${escapeHtml(st.text)}</li>`).join('');
  }
  // The ladder: where the score started, the rungs the rules walked it
  // down, and where it landed.
  const rungs = $$('#view-guide .hw-calls [data-call]');
  const order = rungs.map((c) => c.dataset.call);
  const from = order.indexOf(r.start); const to = order.indexOf(r.call);
  rungs.forEach((c, i) => {
    c.classList.toggle('is-active', c.dataset.call === r.call);
    c.classList.toggle('is-start', c.dataset.call === r.start && r.start !== r.call);
    c.classList.toggle('is-path', from >= 0 && to > from && i >= from && i < to);
  });
  return r;
}

// SP_ELITE_PRO TradingConfig and _calculate_position_size, the risk-grade
// multiplier, then app.py's mandate scale and whole shares.
const GUIDE_SIZE = (() => {
  const S = thr('SIZING') || {};
  const call = S.SIGNAL_SIZE_MULT || {};
  const grade = S.RISK_GRADE_SIZE_MULTIPLIERS || {};
  return {
    risk: S.TARGET_RISK_PER_TRADE, riskStrong: S.TARGET_RISK_STRONG_BUY,
    ceiling: S.MAX_RISK_PER_TRADE, ceilingStrong: S.MAX_RISK_STRONG_BUY,
    callMult: { strong: call.STRONG_BUY, buy: call.BUY, accumulate: call.ACCUMULATE },
    scoreLo: S.SCORE_MULT_BASE, scoreSpan: S.SCORE_MULT_SPAN,
    maxPos: S.MAX_POSITION_PCT, small: S.SMALL_ACCOUNT_THRESHOLD,
    smallMaxPos: S.SMALL_ACCOUNT_MAX_POSITION_PCT, smallScaleFloor: S.SMALL_ACCOUNT_SCALE_FLOOR,
    minTrade: S.MIN_POSITION_SIZE, minFloor: S.MIN_POSITION_FLOOR, minPct: S.MIN_POSITION_PCT,
    grade: { A: grade.A, C: grade.C },
    mandate: { ...(S.MANDATE_SCALE || {}) },
  };
})();

/* [I5] The engine's effective minimum (TradingConfig.effective_min_position_size):
   a share of capital between the floor and the large-account minimum. */
function guideMinTrade(capital) {
  const G = GUIDE_SIZE;
  if (!(capital > 0)) return G.minTrade;
  return Math.min(G.minTrade, Math.max(G.minFloor, G.minPct * capital));
}

function guidePositionCap(capital) {
  const G = GUIDE_SIZE;
  if (!(capital > 0) || capital >= G.small) return G.maxPos;
  const scale = guideClamp((G.small - capital) / Math.max(G.small - G.smallScaleFloor, 1), 0, 1);
  return Math.min(G.smallMaxPos, Math.max(G.maxPos, G.maxPos + scale * (G.smallMaxPos - G.maxPos)));
}

function guideSize({ capital = 25000, entry = 100, stop = 95.8, call = 'buy', score = 68, grade = 'A', mandate = 'balanced' } = {}) {
  const G = GUIDE_SIZE;
  capital = Number(capital); entry = Number(entry); stop = Number(stop); score = Number(score);
  if (!(capital > 0)) return { error: 'Enter the capital to size from.', field: 'cap' };
  if (!(entry > 0)) return { error: 'Enter an entry price above zero.', field: 'entry' };
  if (!(stop > 0) || stop >= entry) return { error: 'The stop has to sit below the entry.', field: 'stop' };
  if (!Number.isFinite(score)) return { error: 'Enter a score from 55 to 100.', field: 'score' };
  const strong = call === 'strong';
  const riskPct = strong ? G.riskStrong : G.risk;
  const callMult = G.callMult[call] ?? 0.5;
  const scoreMult = G.scoreLo + (guideClamp(score / 10, 0, 10) / 10) * G.scoreSpan;
  const rawBudget = capital * riskPct * callMult * scoreMult;
  const ceiling = capital * (strong ? G.ceilingStrong : G.ceiling);
  const budget = Math.min(rawBudget, ceiling);
  const stopPct = (entry - stop) / entry;
  const riskSized = budget / stopPct;
  const capPct = guidePositionCap(capital);
  const capValue = capital * capPct;
  const binding = riskSized > capValue;
  const base = Math.min(riskSized, capValue);
  const gradeMult = G.grade[grade] ?? 1.0;
  const mandateMult = G.mandate[mandate] ?? 1.0;
  const graded = Math.round(base * gradeMult * 100) / 100;
  const res = {
    riskPct, callMult, scoreMult, budget, ceilingBound: rawBudget > ceiling, stopPct, riskSized,
    capPct, capValue, binding, gradeMult, mandateMult, shares: 0, value: 0, atRisk: 0, riskOfCapital: 0, capital,
  };
  const minTrade = guideMinTrade(capital);
  if (base < minTrade || graded < minTrade) return { ...res, afterMult: graded, none: `Below the ${guideUsd(minTrade)} minimum trade at this capital: no position` };
  const afterMult = graded * mandateMult;
  const shares = Math.floor(afterMult / entry + 1e-9);
  if (shares < 1) return { ...res, afterMult, none: 'Does not buy one whole share: needs more capital' };
  const atRisk = shares * (entry - stop);
  return { ...res, afterMult, shares, value: shares * entry, atRisk, riskOfCapital: atRisk / capital };
}

const guideUsd = (v, dec = 0) => `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
const guidePct = (v, dec = 2) => `${+(v * 100).toFixed(dec)}%`;
const GUIDE_SIZE_CALL = { strong: 'Strong Buy', buy: 'Buy', accumulate: 'Accumulate' };

function guideReceiptRows(inp, r) {
  const mult = (m) => `x${m.toFixed(2)}`;
  const rows = [
    ['Risk budget', `${guidePct(r.riskPct)} x ${GUIDE_SIZE_CALL[inp.call] || 'Buy'} ${r.callMult} x score ${r.scoreMult.toFixed(2)}${r.ceilingBound ? ', held to the ceiling' : ''}`, guideUsd(r.budget, 2)],
    ['Stop distance', `${guideUsd(inp.entry, 2)} down to ${guideUsd(inp.stop, 2)}`, `${+(r.stopPct * 100).toFixed(1)}%`],
    ['Before limits', 'Budget / stop distance', guideUsd(Math.round(r.riskSized))],
    ['Position limit', `${guidePct(r.capPct, 1)} of capital, ${r.binding ? 'binding' : 'not binding'}`, guideUsd(Math.round(r.capValue)), r.binding ? 'is-binding' : ''],
    ['Grade and mandate', `${mult(r.gradeMult)} ${mult(r.mandateMult)}`, guideUsd(Math.round(r.afterMult))],
  ];
  rows.push(r.none
    ? ['Position', r.none, 'None', 'is-final is-none']
    : ['Position', `${guideUsd(Math.round(r.atRisk))} at the stop, ${guidePct(r.riskOfCapital)} of capital`, `${r.shares} ${r.shares === 1 ? 'share' : 'shares'}`, 'is-final']);
  return rows;
}

function guideCalcInputs() {
  const val = (id) => $(`#hw-c-${id}`)?.value;
  return { capital: val('cap'), entry: val('entry'), stop: val('stop'), score: val('score'), call: val('call'), grade: val('grade'), mandate: val('mandate') };
}

function renderGuideSize(motion = false) {
  const list = $('#hw-receipt');
  if (!list) return null;
  const inp = guideCalcInputs();
  const r = guideSize(inp);
  ['cap', 'entry', 'stop', 'score'].forEach((f) => $(`#hw-c-${f}`)?.removeAttribute('aria-invalid'));
  if (r.error) {
    $(`#hw-c-${r.field}`)?.setAttribute('aria-invalid', 'true');
    list.innerHTML = `<li class="is-final is-none"><span class="hw-rc-k">Position</span><span class="hw-rc-f">${escapeHtml(r.error)}</span><b class="hw-rc-v">None</b></li>`;
    return r;
  }
  const prev = $$('.hw-rc-v', list).map((b) => b.textContent);
  const rows = guideReceiptRows({ ...inp, entry: Number(inp.entry), stop: Number(inp.stop) }, r);
  list.innerHTML = rows.map(([k, f, v, cls], i) => `<li class="${cls || ''}${motion && prev.length === rows.length && prev[i] !== v ? ' is-changed' : ''}"><span class="hw-rc-k">${k}</span><span class="hw-rc-f">${escapeHtml(f)}</span><b class="hw-rc-v">${v}</b></li>`).join('');
  if (motion) setTimeout(() => $$('.is-changed', list).forEach((li) => li.classList.remove('is-changed')), 60);
  renderGuideGeometry(Number(inp.capital), r);
  return r;
}

// Risk as an area: width is position value (full width = 25% of capital,
// the widest cap), height is the stop distance (full height = 12%). The
// dashed box is the risk budget; the hatched box is what is actually at risk.
function guideGeometry(capital, r) {
  const span = capital * GUIDE_SIZE.smallMaxPos;
  const h = guideClamp(r.stopPct / 0.12, 0, 1);
  return {
    budgetW: guideClamp(r.riskSized / span, 0, 1), fillW: guideClamp(r.value / span, 0, 1), h,
    cap: guideClamp(r.capValue / span, 0, 1),
  };
}

function renderGuideGeometry(capital, r) {
  const budget = $('#hw-geo-budget'); const fill = $('#hw-geo-fill'); const cap = $('#hw-geo-cap'); const v = $('#hw-geo-v');
  if (!budget || !fill || r.error) return;
  const g = guideGeometry(capital, r);
  budget.style.setProperty('--gw', g.budgetW.toFixed(3)); budget.style.setProperty('--gh', g.h.toFixed(3));
  fill.style.setProperty('--gw', g.fillW.toFixed(3)); fill.style.setProperty('--gh', g.h.toFixed(3));
  if (cap) cap.style.setProperty('--gc', g.cap.toFixed(3));
  if (v) v.innerHTML = r.none ? `<b>$0</b> at risk<br />${escapeHtml(r.none)}` : `<b>${guideUsd(Math.round(r.atRisk))}</b> at risk<br />${guideUsd(Math.round(r.value))} x ${+(r.stopPct * 100).toFixed(1)}%<br />Budget ${guideUsd(r.budget, 2)}`;
}

// app.py _market_posture_guidance: the band each posture carries, and the
// band that replaces it when confidence is low or the data is stale.
const GUIDE_POSTURES = {
  offensive: { name: 'Offensive', band: [65, 95], sizing: 'Normal size with staged adds.' },
  'selective-offensive': { name: 'Selective offensive', band: [65, 95], sizing: 'Normal size with staged adds.' },
  'lean-long': { name: 'Lean long', band: [50, 70], sizing: 'Moderate starter size.' },
  neutral: { name: 'Neutral', band: [45, 65], sizing: 'Measured size with staggered entries.' },
  'lean-defensive': { name: 'Lean defensive', band: [20, 50], sizing: 'Light exposure with higher cash buffer.' },
  'selective-defensive': { name: 'Selective defensive', band: [20, 50], sizing: 'Light exposure with higher cash buffer.' },
  defensive: { name: 'Defensive', band: [20, 50], sizing: 'Light exposure with higher cash buffer.' },
};
const GUIDE_POSTURE_CAP = { low: [30, 55], stale: [20, 45] };

function guidePosture(key, conf = 'normal') {
  const p = GUIDE_POSTURES[key] || GUIDE_POSTURES['lean-long'];
  const cap = GUIDE_POSTURE_CAP[conf];
  const band = cap || p.band;
  const text = cap
    ? `<b>${p.name}, ${conf === 'stale' ? 'stale data' : 'low confidence'}:</b> ${band[0]}-${band[1]}% gross, in place of ${p.band[0]}-${p.band[1]}%. Stay cautious while data confidence is low.`
    : `<b>${p.name}:</b> ${band[0]}-${band[1]}% gross exposure. ${p.sizing}`;
  return { key, band, capped: !!cap, text };
}

function renderGuidePosture() {
  const active = $('#view-guide .hw-posture-row.is-active');
  if (!active) return null;
  const conf = ($('#view-guide input[name="hw-conf"]:checked') || {}).value || 'normal';
  const r = guidePosture(active.dataset.posture, conf);
  $$('#view-guide .hw-posture-row').forEach((row) => {
    const on = row === active;
    row.classList.toggle('is-capped', on && r.capped);
    if (on && r.capped) { row.style.setProperty('--elo', String(r.band[0])); row.style.setProperty('--ehi', String(r.band[1])); }
    else { row.style.removeProperty('--elo'); row.style.removeProperty('--ehi'); }
  });
  const out = $('#hw-posture-out'); if (out) out.innerHTML = r.text;
  const band = $('#hw-dial-band');
  if (band) { band.style.setProperty('--lo', String(r.band[0])); band.style.setProperty('--hi', String(r.band[1])); }
  const dv = $('#hw-dial-v'); if (dv) dv.textContent = `${r.band[0]}-${r.band[1]}%`;
  $('#view-guide .hw-posture')?.classList.toggle('is-capped', r.capped);
  return r;
}

// TradingAnalysis_ELITE Config.REGIME_WEIGHTS, normalised as the engine
// normalises them (_get_regime_weights).
const GUIDE_REGIME_WEIGHTS = {
  sideways: { technical: 0.3333333333, fundamental: 0.2777777778, valuation: 0.1666666667, quality: 0.1111111111, sentiment: 0.1111111111 },
  bull: { technical: 0.3888888889, fundamental: 0.2222222222, valuation: 0.1333333333, quality: 0.0888888889, sentiment: 0.1666666667 },
  bear: { technical: 0.275, fundamental: 0.3125, valuation: 0.1875, quality: 0.1625, sentiment: 0.0625 },
  high_vol: { technical: 0.3125, fundamental: 0.275, valuation: 0.1875, quality: 0.125, sentiment: 0.1 },
};

function guideMix(regime = 'sideways') {
  const raw = GUIDE_REGIME_WEIGHTS[regime] || GUIDE_REGIME_WEIGHTS.sideways;
  const total = Object.values(raw).reduce((a, b) => a + b, 0);
  return Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v / total]));
}

function renderGuideMix() {
  const regime = ($('#view-guide input[name="hw-regime"]:checked') || {}).value || 'sideways';
  const w = guideMix(regime);
  $$('#hw-mix-bar [data-pillar]').forEach((s) => {
    const v = w[s.dataset.pillar];
    s.style.setProperty('--w', (v * 100).toFixed(1));
    const b = s.querySelector('b'); if (b) b.textContent = `${Math.round(v * 100)}%`;
  });
  return w;
}

// A set of role="radio" buttons: click or arrow keys select, one tab stop.
function guideRadioGroup(buttons, onSelect) {
  const select = (btn, focus) => {
    buttons.forEach((b) => {
      const on = b === btn;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
    });
    if (focus) btn.focus();
    onSelect(btn);
  };
  buttons.forEach((b, i) => {
    b.tabIndex = b.getAttribute('aria-checked') === 'true' ? 0 : -1;
    b.addEventListener('click', () => select(b, false));
    b.addEventListener('keydown', (ev) => {
      const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[ev.key];
      if (!step) return;
      ev.preventDefault();
      select(buttons[(i + step + buttons.length) % buttons.length], true);
    });
  });
}

// The scan funnel: step 0 is the whole example list, step 8 all gates run.
const GUIDE_GATES = 8;
function renderGuideFunnel(step, motion = false) {
  const box = $('#view-guide .hw-funnel');
  if (!box) return 0;
  const s = guideClamp(Math.round(Number(step) || 0), 0, GUIDE_GATES);
  box.classList.add('is-live');
  box.dataset.step = String(s);
  $$('.hw-gate', box).forEach((g) => {
    const n = Number(g.dataset.gate);
    g.classList.toggle('is-done', n <= s);
    g.classList.toggle('is-current', n === s);
    if (n === s) g.setAttribute('aria-current', 'step'); else g.removeAttribute('aria-current');
  });
  const rows = $$('.hw-row', box);
  let left = 0;
  rows.forEach((row) => {
    const out = Number(row.dataset.out);
    const status = $('.hw-row-s', row);
    if (status && !row.dataset.reason) row.dataset.reason = status.textContent;
    const cut = out > 0 && out <= s;
    if (!cut) left += 1;
    row.classList.toggle('is-cut', cut);
    row.classList.toggle('is-kept', !cut && s < GUIDE_GATES);
    row.classList.toggle('is-funded', !cut && s === GUIDE_GATES);
    row.classList.toggle('is-just-cut', motion && out === s && s > 0);
    if (status) status.textContent = cut ? row.dataset.reason : s === GUIDE_GATES ? 'Picked' : 'Still in';
  });
  $$('.hw-fbars li', box).forEach((li) => {
    const at = Number(li.dataset.at);
    li.classList.toggle('is-ahead', at > s);
    li.classList.toggle('is-now', at === s);
  });
  const count = $('#hw-funnel-count');
  if (count) {
    const gate = $(`.hw-gate[data-gate="${s}"] b`, box);
    count.innerHTML = s === 0 ? `<b>${rows.length}</b> example names enter the scan`
      : s === GUIDE_GATES ? `<b>${left}</b> of ${rows.length} picked after all eight gates`
        : `<b>${left}</b> of ${rows.length} left after ${escapeHtml(gate ? gate.textContent : `gate ${s}`)}`;
  }
  return left;
}

// ---- The reel: fifteen seconds on one Web Animations timeline ----
// Every track lasts the whole reel and places its keyframes at absolute
// times, so play, pause, scrub and chapter jumps are one currentTime for
// every track. The markup's static state is the closing frame (the poster).
const REEL_MS = 15000;
const REEL_EASE = {
  out: 'cubic-bezier(0.16, 1, 0.3, 1)', io: 'cubic-bezier(0.65, 0, 0.35, 1)',
  back: 'cubic-bezier(0.34, 1.56, 0.64, 1)', in: 'cubic-bezier(0.7, 0, 0.84, 0)', lin: 'linear',
};
const REEL_CHAPTERS = [
  [0, '01 OPEN'], [2350, '02 OVERVIEW'], [4650, '03 DISCOVER'], [7250, '04 RESEARCH'],
  [9850, '05 PICKS'], [12050, '06 MY PORTFOLIO'], [13550, '07 END'],
];
// About 400 example names, and the gate each one stops at (0 = funded).
const REEL_FIELD = { cols: 25, rows: 16, gap: 26, cut: [6, 150, 60, 50, 45, 30, 25, 20, 14] };

// Frames are [ms, props, easing to the next frame]; the first and last
// frames hold to the ends of the reel.
function reelKeyframes(frames) {
  const kf = frames.map(([ms, p, e]) => ({ ...p, offset: ms / REEL_MS, easing: REEL_EASE[e] || REEL_EASE.out }));
  if (kf[0].offset > 0) kf.unshift({ ...frames[0][1], offset: 0, easing: 'linear' });
  if (kf[kf.length - 1].offset < 1) kf.push({ ...frames[frames.length - 1][1], offset: 1, easing: 'linear' });
  return kf;
}

function reelSceneAt(t) {
  let label = REEL_CHAPTERS[0][1];
  for (const [at, name] of REEL_CHAPTERS) if (t >= at) label = name;
  return label;
}

const reelClock = (t) => {
  const s = Math.max(0, Math.min(REEL_MS, t)) / 1000;
  return `00:${String(Math.floor(s)).padStart(2, '0')}.${String(Math.floor((s % 1) * 100)).padStart(2, '0')}`;
};

// A deterministic shuffle, so the scan looks the same every time.
function reelField(svg) {
  const field = svg.querySelector('#rl-field');
  const ticks = svg.querySelector('#rl-ticks');
  if (!field || field.childElementCount) return;
  const NS = 'http://www.w3.org/2000/svg';
  const { cols, rows, gap, cut } = REEL_FIELD;
  const gates = cut.flatMap((n, g) => Array(n).fill(g));
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  for (let i = gates.length - 1; i > 0; i -= 1) { const j = Math.floor(rnd() * (i + 1)); [gates[i], gates[j]] = [gates[j], gates[i]]; }
  const groups = cut.map((_, g) => {
    const el = document.createElementNS(NS, 'g');
    el.setAttribute('id', `rl-g${g}`);
    el.setAttribute('fill', g === 0 ? '#67e8f9' : '#5ea2f7');
    field.appendChild(el);
    return el;
  });
  const x0 = 640 - ((cols - 1) / 2) * gap; const y0 = 398 - ((rows - 1) / 2) * gap;
  gates.forEach((g, i) => {
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', String(x0 + (i % cols) * gap));
    c.setAttribute('cy', String(y0 + Math.floor(i / cols) * gap));
    c.setAttribute('r', g === 0 ? '6' : '4.5');
    c.setAttribute('fill-opacity', g === 0 ? '1' : '0.72');
    groups[g].appendChild(c);
  });
  for (let g = 1; g <= 8; g += 1) {
    const r = document.createElementNS(NS, 'rect');
    r.setAttribute('id', `rl-t${g}`);
    r.setAttribute('x', String(640 - 4 * 30 + (g - 1) * 30 + 4));
    r.setAttribute('y', '628'); r.setAttribute('width', '22'); r.setAttribute('height', '4'); r.setAttribute('rx', '2');
    r.setAttribute('fill', '#5ea2f7');
    ticks.appendChild(r);
  }
}

function reelTimeline(svg) {
  const anims = [];
  const T = (id, frames) => {
    const el = svg.querySelector(`#${id}`);
    if (el) anims.push(el.animate(reelKeyframes(frames), { duration: REEL_MS, fill: 'both' }));
  };
  const up = (id, t, d = 520, dy = 26, e = 'out') => T(id, [[t, { opacity: 0, transform: `translateY(${dy}px)` }, e], [t + d, { opacity: 1, transform: 'translateY(0px)' }]]);
  const fade = (id, t, d = 400) => T(id, [[t, { opacity: 0 }], [t + d, { opacity: 1 }]]);
  const draw = (id, t, d, e = 'io') => T(id, [[t, { strokeDashoffset: 1 }, e], [t + d, { strokeDashoffset: 0 }]]);
  const none = 'translate(0px, 0px) scale(1)';

  // Background: the grid drifts and the lights cross, once, over the reel.
  T('rl-grid', [[0, { transform: 'translate(0px, 0px)' }, 'lin'], [REEL_MS, { transform: 'translate(-64px, -32px)' }]]);
  T('rl-orb-a', [[0, { transform: 'translate(0px, 0px)' }, 'io'], [REEL_MS, { transform: 'translate(520px, 190px)' }]]);
  T('rl-orb-b', [[0, { transform: 'translate(0px, 0px)' }, 'io'], [REEL_MS, { transform: 'translate(-470px, -230px)' }]]);

  // Scenes: each cut is a camera move, not a hard switch.
  T('rl-s1', [[0, { opacity: 1, transform: none }], [2050, { opacity: 1, transform: none }, 'in'], [2450, { opacity: 0, transform: 'translate(0px, 0px) scale(1.16)' }]]);
  T('rl-s2', [[2250, { opacity: 0, transform: 'translate(70px, 0px) scale(1)' }], [2650, { opacity: 1, transform: none }], [4400, { opacity: 1, transform: none }, 'in'], [4700, { opacity: 0, transform: 'translate(-90px, 0px) scale(1)' }]]);
  T('rl-s3', [[4550, { opacity: 0, transform: 'translate(0px, 30px) scale(1)' }], [4900, { opacity: 1, transform: none }], [7250, { opacity: 1, transform: none }], [7400, { opacity: 0, transform: none }]]);
  T('rl-s4', [[7250, { opacity: 0, transform: 'translate(0px, 0px) scale(1.05)' }], [7650, { opacity: 1, transform: none }],
    // the stamp lands: a short camera shake
    [9000, { opacity: 1, transform: none }, 'lin'], [9040, { opacity: 1, transform: 'translate(5px, -4px) scale(1)' }, 'lin'], [9090, { opacity: 1, transform: 'translate(-4px, 3px) scale(1)' }, 'lin'], [9150, { opacity: 1, transform: none }],
    [9650, { opacity: 1, transform: none }, 'in'], [9900, { opacity: 0, transform: 'translate(0px, -50px) scale(1)' }]]);
  T('rl-s5', [[9750, { opacity: 0, transform: 'translate(0px, 44px) scale(1)' }], [10150, { opacity: 1, transform: none }], [11950, { opacity: 1, transform: none }, 'in'], [12100, { opacity: 0, transform: 'translate(-70px, 0px) scale(1)' }]]);
  T('rl-s6', [[11950, { opacity: 0, transform: 'translate(70px, 0px) scale(1)' }], [12250, { opacity: 1, transform: none }], [13350, { opacity: 1, transform: none }, 'in'], [13600, { opacity: 0, transform: 'translate(0px, 0px) scale(0.9)' }]]);
  T('rl-s7', [[13450, { opacity: 0, transform: 'translate(0px, 0px) scale(1.08)' }], [13900, { opacity: 1, transform: none }]]);

  // 1 Open: a scan line, the mark, the name, the promise.
  draw('rl-scan', 0, 900);
  T('rl-scanhead', [[0, { opacity: 1, transform: 'translateX(-1280px)' }, 'io'], [900, { opacity: 1, transform: 'translateX(0px)' }], [1150, { opacity: 0, transform: 'translateX(0px)' }]]);
  T('rl-mark', [[300, { opacity: 0, transform: 'scale(0.2) rotate(-90deg)' }, 'back'], [1050, { opacity: 1, transform: 'scale(1) rotate(0deg)' }]]);
  for (let i = 1; i <= 6; i += 1) up(`rl-w${i}`, 850 + i * 60, 560, 44, 'back');
  up('rl-tag', 1450, 600, 16);

  // 2 Overview: the dial fills, then trims to the posture's range.
  up('rl-s2-t', 2400, 560, 30); up('rl-s2-s', 2520, 560, 20);
  T('rl-dial-track', [[2450, { strokeDasharray: '0 200' }, 'io'], [2900, { strokeDasharray: '100 200' }]]);
  T('rl-dial-band', [[2750, { strokeDasharray: '0 200', strokeDashoffset: 0 }, 'out'], [3250, { strokeDasharray: '70 200', strokeDashoffset: 0 }, 'io'], [3700, { strokeDasharray: '20 200', strokeDashoffset: -50 }]]);
  up('rl-dial-v', 3550, 450, 20); fade('rl-dial-k', 3700);
  for (let i = 1; i <= 7; i += 1) {
    ['', 'v'].forEach((s) => T(`rl-p${i}${s}`, [[2500 + i * 70, { opacity: 0, transform: 'translateX(40px)' }], [2920 + i * 70, { opacity: 1, transform: 'translateX(0px)' }]]));
  }
  T('rl-hl', [[2950, { opacity: 0, transform: 'translateY(0px)' }], [3150, { opacity: 1, transform: 'translateY(0px)' }, 'io'], [3700, { opacity: 1, transform: 'translateY(104px)' }]]);

  // 3 Discover: the field opens, eight gates fire, a few names survive.
  up('rl-s3-t', 4700, 560, 30); up('rl-s3-s', 4820, 560, 20);
  T('rl-reveal-c', [[4750, { r: '0px' }, 'out'], [5450, { r: '420px' }]]);
  up('rl-in-v', 4900, 500, 20); fade('rl-in-k', 5050);
  for (let g = 1; g <= 8; g += 1) {
    const t = 5450 + (g - 1) * 200;
    T(`rl-g${g}`, [[t, { fill: '#5ea2f7', opacity: 1, transform: 'translateY(0px)' }, 'lin'], [t + 80, { fill: '#f87171', opacity: 1, transform: 'translateY(0px)' }, 'in'], [t + 340, { fill: '#f87171', opacity: 0, transform: 'translateY(30px)' }]]);
    T(`rl-t${g}`, [[t, { opacity: 0.2 }], [t + 80, { opacity: 1 }]]);
    const label = [[t - 30, { opacity: 0, transform: 'translateY(14px)' }], [t + 50, { opacity: 1, transform: 'translateY(0px)' }]];
    if (g < 8) label.push([t + 135, { opacity: 1, transform: 'translateY(0px)' }, 'in'], [t + 168, { opacity: 0, transform: 'translateY(-12px)' }]);
    T(`rl-gl${g}`, label);
  }
  T('rl-g0', [[6950, { opacity: 1, transform: 'scale(1)' }, 'back'], [7150, { opacity: 1, transform: 'scale(1.06)' }]]);
  up('rl-out-v', 6980, 420, 20); fade('rl-out-k', 7050);
  // Match cut: one survivor fills the frame and becomes the tear sheet.
  T('rl-hero-dot', [[6960, { opacity: 0, transform: 'scale(0)' }, 'back'], [7120, { opacity: 1, transform: 'scale(1)' }, 'in'], [7240, { opacity: 0.45, transform: 'scale(28)' }, 'out'], [7340, { opacity: 0, transform: 'scale(75)' }]]);

  // 4 Research: score, four checks in engine order, then the call lands.
  up('rl-s4-t', 7400, 560, 30);
  T('rl-card', [[7300, { opacity: 0, transform: 'scale(0.9) translateY(24px)' }], [7800, { opacity: 1, transform: 'scale(1) translateY(0px)' }]]);
  T('rl-ring', [[7650, { strokeDashoffset: 100 }, 'out'], [8350, { strokeDashoffset: 32 }]]);
  up('rl-ring-v', 8000, 400, 14);
  for (let i = 1; i <= 4; i += 1) {
    T(`rl-c${i}`, [[8000 + i * 170, { opacity: 0, transform: 'translateX(-16px)' }], [8300 + i * 170, { opacity: 1, transform: 'translateX(0px)' }]]);
    draw(`rl-k${i}`, 8100 + i * 170, 260, 'out');
  }
  T('rl-stamp', [[8800, { opacity: 0, transform: 'scale(1.9) rotate(-10deg)' }, 'in'], [9000, { opacity: 1, transform: 'scale(0.96) rotate(-3deg)' }, 'out'], [9200, { opacity: 1, transform: 'scale(1) rotate(-3deg)' }]]);
  T('rl-wave', [[8990, { opacity: 0, transform: 'scale(0.9)' }, 'lin'], [9000, { opacity: 0.7, transform: 'scale(0.9)' }, 'out'], [9520, { opacity: 0, transform: 'scale(2.3)' }]]);
  up('rl-utag', 9180, 420, 12);

  // 5 Picks: axes, the risk budget, the limit, what is really at risk.
  up('rl-s5-t', 9900, 560, 30);
  draw('rl-axes', 9950, 420);
  fade('rl-ax-y', 10300); fade('rl-ax-x', 10350);
  T('rl-budget', [[10250, { opacity: 1, transform: 'scale(0, 0)' }, 'out'], [10800, { opacity: 1, transform: 'scale(1, 1)' }]]);
  draw('rl-cap', 10380, 360, 'out'); fade('rl-cap-k', 10600);
  T('rl-fill', [[10560, { opacity: 1, transform: 'scale(0, 0)' }, 'out'], [11100, { opacity: 1, transform: 'scale(1, 1)' }]]);
  up('rl-e1', 10950, 420, 18); up('rl-e2', 11150, 420, 18); up('rl-e3', 11350, 420, 18);
  T('rl-e4', [[11600, { opacity: 0, transform: 'scale(0.7)' }, 'back'], [11950, { opacity: 1, transform: 'scale(1)' }]]);

  // 6 My Portfolio: weights, the two limits, the read-only shield.
  up('rl-s6-t', 12100, 560, 30);
  for (let i = 1; i <= 6; i += 1) T(`rl-b${i}`, [[12150 + i * 60, { transform: 'scaleX(0)' }, 'out'], [12600 + i * 60, { transform: 'scaleX(1)' }]]);
  draw('rl-l10', 12500, 320, 'out'); draw('rl-l20', 12560, 320, 'out');
  fade('rl-l10k', 12700, 300); fade('rl-l20k', 12750, 300);
  up('rl-ch1', 12850, 360, 12); up('rl-ch2', 12950, 360, 12);
  draw('rl-shield', 12750, 480, 'out'); draw('rl-shield-k', 13100, 260, 'out');
  up('rl-ro', 13100, 360, 12); fade('rl-order', 13180, 200);
  draw('rl-strike', 13300, 180, 'out');

  // 7 End card.
  T('rl-emark', [[13550, { opacity: 0, transform: 'scale(0.4) rotate(90deg)' }, 'back'], [14150, { opacity: 1, transform: 'scale(1) rotate(0deg)' }]]);
  for (let i = 1; i <= 6; i += 1) up(`rl-e-w${i}`, 13700 + i * 55, 520, 40, 'back');
  for (let i = 1; i <= 5; i += 1) up(`rl-pg${i}`, 14120 + i * 70, 420, 14);
  draw('rl-pgline', 14100, 600, 'out');
  up('rl-etag', 14450, 450, 12);
  fade('rl-fine', 14650, 300);
  return anims;
}

// The player. Autoplays once, muted by nature, when it is first in view
// (never under reduced motion); pauses off screen, off the guide and in a
// hidden tab. Returns the controller, which the reel export script drives.
function bindGuideReel(motion) {
  const stage = $('#hw-reel');
  if (!stage || stage.dataset.bound === '1') return null;
  const svg = stage.querySelector('svg');
  if (!svg || typeof svg.animate !== 'function') return null;
  stage.dataset.bound = '1';
  const player = stage.closest('.hw-reel-player');
  const playBtn = $('#hw-reel-play'); const seek = $('#hw-reel-seek'); const time = $('#hw-reel-time');
  const tc = svg.querySelector('#rl-tc'); const sceneK = svg.querySelector('#rl-scene-k');
  const chapters = $$('#view-guide [data-reel-t]');
  reelField(svg);
  const anims = reelTimeline(svg);
  let raf = 0;
  const now = () => Number(anims[0]?.currentTime) || 0;
  const secs = (t) => `0:${String(Math.round(t / 1000)).padStart(2, '0')}`;
  const ui = () => {
    const t = now();
    if (seek) { seek.value = String(Math.round(t)); seek.setAttribute('aria-valuetext', `${secs(t)} of 0:15, ${reelSceneAt(t).replace(/^\d+ /, '').toLowerCase()}`); }
    if (time) time.textContent = `${secs(t)} / 0:15`;
    if (tc) tc.textContent = reelClock(t);
    if (sceneK) sceneK.textContent = reelSceneAt(t);
    const at = reelSceneAt(t);
    chapters.forEach((c, i) => { if (REEL_CHAPTERS[i] && REEL_CHAPTERS[i][1] === at) c.setAttribute('aria-current', 'true'); else c.removeAttribute('aria-current'); });
  };
  const seekTo = (t) => { const v = Math.max(0, Math.min(REEL_MS, Number(t) || 0)); anims.forEach((a) => { a.currentTime = v; }); ui(); };
  const pause = () => {
    anims.forEach((a) => a.pause());
    if (raf) cancelAnimationFrame(raf); raf = 0;
    player.classList.remove('is-playing');
    playBtn?.setAttribute('aria-label', 'Play reel');
    ui();
  };
  const tick = () => {
    ui();
    const onGuide = $('#view-guide')?.classList.contains('active');
    if (now() >= REEL_MS - 1 || !onGuide) { pause(); return; }
    raf = requestAnimationFrame(tick);
  };
  const play = () => {
    if (now() >= REEL_MS - 1) seekTo(0);
    player.classList.remove('is-idle');
    anims.forEach((a) => a.play());
    player.classList.add('is-playing');
    playBtn?.setAttribute('aria-label', 'Pause reel');
    if (!raf) raf = requestAnimationFrame(tick);
  };
  // Poster: the closing frame, paused, until someone (or the first view) plays it.
  anims.forEach((a) => a.pause());
  seekTo(REEL_MS);
  player.classList.add('is-idle');
  playBtn?.addEventListener('click', () => (player.classList.contains('is-playing') ? pause() : play()));
  $('#hw-reel-cover')?.addEventListener('click', play);
  $('#hw-reel-replay')?.addEventListener('click', () => { seekTo(0); play(); });
  // Read the new position before pausing: pause() redraws the scrubber.
  seek?.addEventListener('input', () => { const v = seek.value; pause(); player.classList.remove('is-idle'); seekTo(v); });
  chapters.forEach((c) => c.addEventListener('click', () => { seekTo(c.dataset.reelT); play(); }));
  const full = $('#hw-reel-full');
  if (full && typeof stage.requestFullscreen === 'function') {
    full.hidden = false;
    full.addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else stage.requestFullscreen().then(() => { if (!player.classList.contains('is-playing')) play(); }).catch(() => {});
    });
  }
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
  if (typeof IntersectionObserver !== 'undefined') {
    let played = !motion;
    new IntersectionObserver((en) => {
      en.forEach((e) => {
        if (e.intersectionRatio < 0.25 && player.classList.contains('is-playing')) pause();
        if (!played && e.intersectionRatio >= 0.6) { played = true; seekTo(0); play(); }
      });
    }, { threshold: [0, 0.25, 0.6] }).observe(stage);
  }
  const api = { play, pause, seek: seekTo, time: now, duration: REEL_MS };
  if (typeof window !== 'undefined') window.QuantaReel = api;
  return api;
}

// Scroll reveals. A block is hidden only in the moment before it enters from
// the bottom edge (it is "armed" inside a band just below the screen), then
// drawn in as it arrives. Everything further down stays drawn, so a
// full-page capture, a printout or an instant jump never finds a blank
// block. First match wins; the effect is the block's starting pose.
const GUIDE_REVEALS = [
  ['.hw-flow-list', 'line'],
  ['[data-reveal], .hw-ch-head', 'rise'],
  ['.hw-proof > li, .hw-flow-list > li, .hw-toolchips li, .hw-ledger > li, .hw-limits > li', 'rise'],
  ['.hw-chapters > li', 'deal'],
  ['.hw-gate, .hw-guard-col:first-child', 'left'],
  ['.hw-guard-col:last-child', 'right'],
  ['.hw-guard-gate', 'pop'],
  ['.hw-bar-b i', 'grow'],
];
const GUIDE_FX = {
  rise: { opacity: 0, translate: '0 28px' },
  deal: { opacity: 0, translate: '0 36px', rotate: '-2deg' },
  left: { opacity: 0, translate: '-36px 0' },
  right: { opacity: 0, translate: '36px 0' },
  pop: { opacity: 0, scale: '0.4' },
  grow: { scale: '0 1' },
};
const GUIDE_EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const GUIDE_SETTLE = 'cubic-bezier(0.34, 1.56, 0.64, 1)';

// One pose to the element's own resting state (the implicit end keyframe),
// held through the delay so nothing flashes before it starts.
function guideFrom(el, pose, duration, delay = 0, extra = {}) {
  if (!el || typeof el.animate !== 'function') return null;
  return el.animate([{ ...pose, offset: 0 }], { duration, delay, easing: GUIDE_EASE, fill: 'backwards', ...extra });
}

// Each demo explains itself once, the first time it scrolls into view: the
// dial sweeps to its band, the weights fill in, the call steps down the
// ladder and lands, the risk boxes grow, the workflow lights node by node.
function guideFirstLook(el) {
  if (el.matches('.hw-ch-head')) guideFrom($('.hw-ico', el), { opacity: 0, scale: '0.6' }, 560, 120, { easing: GUIDE_SETTLE });
  if (el.matches('.hw-posture')) guideFrom($('#hw-dial-band', el), { strokeDasharray: '0 200' }, 1200, 260);
  if (el.matches('.hw-mix')) guideFrom($('#hw-mix-bar', el), { clipPath: 'inset(0 100% 0 0)' }, 1100, 220);
  if (el.matches('.hw-lab')) {
    const rungs = $$('#hw-ladder > li', el);
    rungs.forEach((li, i) => guideFrom(li, { opacity: 0, translate: '-12px 0' }, 420, 220 + i * 80));
    guideFrom($('#hw-verdict', el), { opacity: 0, scale: '1.3' }, 560, 300 + rungs.length * 80, { easing: GUIDE_SETTLE });
  }
  if (el.matches('.hw-calc')) {
    $$('#hw-receipt > li', el).forEach((li, i) => guideFrom(li, { opacity: 0, translate: '0 10px' }, 380, 200 + i * 70));
    guideFrom($('#hw-geo-budget', el), { scale: '0 0' }, 900, 360);
    guideFrom($('#hw-geo-fill', el), { scale: '0 0' }, 900, 560);
    guideFrom($('#hw-geo-v', el), { opacity: 0 }, 400, 1100);
  }
  if (el.matches('.hw-flow-list')) {
    const css = getComputedStyle(el);
    const on = { backgroundColor: css.getPropertyValue('--primary').trim(), color: css.getPropertyValue('--primary-contrast').trim(), scale: '1.08' };
    $$('.hw-flow-node .hw-ico', el).forEach((ico, i) => {
      const rest = getComputedStyle(ico);
      const off = { backgroundColor: rest.backgroundColor, color: rest.color, scale: '1' };
      ico.animate([off, { ...on, offset: 0.4 }, off], { duration: 760, delay: 700 + i * 170, easing: 'ease-out' });
    });
  }
}

function bindGuideReveals(root) {
  if (typeof IntersectionObserver === 'undefined' || typeof root.animate !== 'function') return;
  const items = [];
  GUIDE_REVEALS.forEach(([sel, fx]) => $$(sel, root).forEach((el) => {
    if (!el.dataset.hwFx) { el.dataset.hwFx = fx; items.push(el); }
  }));
  const vh = () => window.innerHeight || document.documentElement.clientHeight;
  let arm = null;
  // Shown: drawn for good. Arriving from the bottom edge it animates in, in
  // reading order; landed on by a jump it is simply there.
  const reveal = new IntersectionObserver((entries) => {
    const batch = entries.filter((e) => e.isIntersecting && !e.target.classList.contains('hw-shown'))
      .sort((a, b) => (a.boundingClientRect.top - b.boundingClientRect.top) || (a.boundingClientRect.left - b.boundingClientRect.left));
    let n = 0;
    batch.forEach(({ target: el, boundingClientRect: r }) => {
      reveal.unobserve(el); arm.unobserve(el);
      el.classList.add('hw-shown');
      if (!el.classList.contains('hw-armed')) return;
      el.classList.remove('hw-armed');
      if (r.top < vh() * 0.55) return;
      const delay = Math.min(n, 8) * 70; n += 1;
      if (el.dataset.hwFx === 'line') guideFrom(el, { scale: '0 1' }, 1100, delay, { pseudoElement: '::before' });
      else guideFrom(el, GUIDE_FX[el.dataset.hwFx] || GUIDE_FX.rise, el.dataset.hwFx === 'grow' ? 900 : 720, delay);
      guideFirstLook(el);
    });
  }, { rootMargin: '0px 0px -8% 0px' });
  // Armed: inside the band just below the screen and not yet seen.
  arm = new IntersectionObserver((entries) => {
    entries.forEach(({ target: el, isIntersecting, boundingClientRect: r }) => {
      if (isIntersecting && !el.classList.contains('hw-shown') && r.top >= vh()) el.classList.add('hw-armed');
    });
  }, { rootMargin: '0px 0px 40% 0px' });
  items.forEach((el) => { reveal.observe(el); arm.observe(el); });
}

function bindGuide() {
  const root = $('#view-guide .hw');
  if (!root || root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  const motion = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (motion) { root.classList.add('hw-motion'); bindGuideReveals(root); }
  // Grouped items arrive in order; CSS reads each item's index.
  ['.hw-checks-row', '.hw-proof', '.hw-flow-list', '.hw-ledger', '.hw-limits'].forEach((sel) => {
    $$(`#view-guide ${sel} > li`).forEach((li, i) => li.style.setProperty('--i', String(i)));
  });

  // The chapter rail follows the section in the middle of the screen.
  const railBtns = $$('#view-guide .hw-rail-btn');
  if (railBtns.length && typeof IntersectionObserver !== 'undefined') {
    const mark = (id) => railBtns.forEach((b) => {
      if (b.dataset.hwJump === id) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
    const railIo = new IntersectionObserver((en) => {
      en.forEach((e) => { if (e.isIntersecting) mark(e.target.id); });
    }, { rootMargin: '-40% 0px -55% 0px' });
    $$('#view-guide .hw-ch').forEach((s) => railIo.observe(s));
  }

  // In-page jumps from the path: scroll, then hand focus to the heading.
  $$('#view-guide [data-hw-jump]').forEach((b) => b.addEventListener('click', () => {
    const sec = document.getElementById(b.dataset.hwJump);
    if (!sec) return;
    sec.scrollIntoView({ behavior: motion ? 'smooth' : 'auto', block: 'start' });
    const h = sec.querySelector('h2');
    if (h) { if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
  }));

  // Overview: posture ladder and the data-confidence cap.
  const postures = $$('#view-guide .hw-posture-row');
  if (postures.length) guideRadioGroup(postures, () => renderGuidePosture());
  $$('#view-guide input[name="hw-conf"]').forEach((i) => i.addEventListener('change', renderGuidePosture));
  renderGuidePosture();

  // Research: the decision lab.
  ['#hw-score', '#hw-insider', '#hw-flag', '#hw-rr', '#hw-owned'].forEach((sel) => {
    const el = $(sel); if (el) el.addEventListener('input', renderGuideDecision);
  });
  $$('#view-guide input[name="hw-grade"]').forEach((i) => i.addEventListener('change', renderGuideDecision));
  renderGuideDecision();

  // Research: score weights by regime.
  $$('#view-guide input[name="hw-regime"]').forEach((i) => i.addEventListener('change', renderGuideMix));
  renderGuideMix();

  // Picks: the sizing calculator.
  $$('#hw-calc input, #hw-calc select').forEach((el) => el.addEventListener('input', () => renderGuideSize(motion)));
  renderGuideSize(false);

  // My Portfolio: one holding at a time.
  const bars = $$('#view-guide .hw-bar');
  if (bars.length) {
    guideRadioGroup(bars, (btn) => {
      $$('#view-guide [data-hold-panel]').forEach((p) => { p.hidden = p.dataset.holdPanel !== btn.dataset.hold; });
    });
  }

  // Discover: on a wide screen the gates advance as their cards cross the
  // middle of the screen beside the sticky funnel; elsewhere they run once
  // when the demo comes into view. A click on a gate jumps straight to it.
  let timer = null;
  const runBtn = $('#hw-funnel-run');
  const stop = () => { if (timer) clearInterval(timer); timer = null; if (runBtn) runBtn.disabled = false; };
  const stepNow = () => Number($('#view-guide .hw-funnel')?.dataset.step || 0);
  // Without motion the reader gets the finished list, the same as the markup.
  const scrolly = motion && typeof window.matchMedia === 'function' && window.matchMedia('(min-width: 1081px)').matches;
  renderGuideFunnel(motion ? 0 : GUIDE_GATES);
  const gates = $$('#view-guide .hw-gate');
  if (scrolly && typeof IntersectionObserver !== 'undefined') {
    const gateIo = new IntersectionObserver((en) => {
      en.forEach((e) => { if (e.isIntersecting && !timer) renderGuideFunnel(e.target.dataset.gate, true); });
      // Back above the first gate: the whole list again.
      if (gates[0] && gates[0].getBoundingClientRect().top > window.innerHeight * 0.55 && !timer) renderGuideFunnel(0);
    }, { rootMargin: '-45% 0px -50% 0px' });
    gates.forEach((g) => gateIo.observe(g));
  }
  $$('#view-guide .hw-gate').forEach((g) => g.addEventListener('click', () => { stop(); renderGuideFunnel(g.dataset.gate, motion); }));
  $('#hw-funnel-reset')?.addEventListener('click', () => { stop(); renderGuideFunnel(0); });
  const run = () => {
    stop();
    if (!motion) { renderGuideFunnel(GUIDE_GATES); return; }
    let s = stepNow() >= GUIDE_GATES ? 0 : stepNow();
    renderGuideFunnel(s);
    runBtn.disabled = true;
    timer = setInterval(() => {
      // Stop if the reader left the guide.
      if (!$('#view-guide')?.classList.contains('active')) { stop(); return; }
      s += 1;
      renderGuideFunnel(s, true);
      if (s >= GUIDE_GATES) stop();
    }, 700);
  };
  runBtn?.addEventListener('click', run);
  // The gates run once by themselves the first time the demo is in full
  // view, and the run pauses if the demo scrolls away.
  const funnel = $('#view-guide .hw-funnel');
  if (funnel && typeof IntersectionObserver !== 'undefined') {
    let played = !motion || scrolly;
    new IntersectionObserver((en) => {
      en.forEach((e) => {
        if (!e.isIntersecting) { stop(); return; }
        if (!played && e.intersectionRatio >= 0.35 && stepNow() === 0) { played = true; run(); }
      });
    }, { threshold: [0, 0.35] }).observe(funnel);
  }

  // The reel: its own player and timeline.
  bindGuideReel(motion);
}

async function loadGuideEvidence() {
  const box = $('#guide-evidence');
  if (!box) return;
  box.setAttribute('aria-busy', 'true');
  try {
    const res = await fetch('/api/track-record', { cache: 'no-store' });
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    renderGuideEvidence(res.ok ? data : { error: data.error || `HTTP ${res.status}` });
  } catch (_) {
    renderGuideEvidence({ error: 'unreachable', unreachable: true });
  } finally {
    box.removeAttribute('aria-busy');
  }
}

// ---------------------------------------------------------------------------
// TRACK RECORD — the shared ledger, benchmarked. Presentation only: every
// number here comes from tracking/ledger.py; the page adds no statistics.
// ---------------------------------------------------------------------------
const TRACK_ENGINE_LABEL = { elite: 'Deep analysis', elite_pro: 'Picks scanner', shortterm: 'Short-term' };

// Benchmarks are recorded per call (Nifty for NSE names). Name what was used.
function trackBenchmarkName(b) {
  return b === '^NSEI' ? 'Nifty 50 (^NSEI)' : (b || 'SPY');
}
// "SPY", "Nifty 50 (^NSEI)", or "SPY + Nifty 50 (^NSEI)" for a mixed-market ledger.
function trackBenchmarkList(data) {
  const list = Array.isArray(data.benchmarks) && data.benchmarks.length ? data.benchmarks : [data.benchmark || 'SPY'];
  return list.map(trackBenchmarkName).join(' + ');
}

function trackPct(x, digits = 1) {
  return (x == null || !Number.isFinite(Number(x))) ? '--' : fmtPct(Number(x), digits, false);
}

function trackStateHtml({ title, body, actions = '', tone = '' }) {
  return `<div class="q-state ${tone ? `q-state--${tone}` : ''}" role="status">
    <strong class="q-state-title">${escapeHtml(title)}</strong>
    <p class="q-state-body">${body}</p>
    ${actions ? `<div class="q-state-actions">${actions}</div>` : ''}
  </div>`;
}

// Model freeze: how many of an engine's calls the code running now produced.
// A record only counts as evidence for code that did not change during it.
function trackFreezeNote(e) {
  const total = Number(e.recorded) || 0;
  const fp = String(e.code_fingerprint || '');
  if (!fp) return '';
  const current = Number(e.recorded_on_current_code) || 0;
  const changed = total > current ? ` · <b>${total - current}</b> on earlier code` : '';
  return `<small class="muted track-freeze" title="Fingerprint of the engine code running now: ${escapeHtml(fp)}"><br>code <span class="mono">${escapeHtml(fp.slice(0, 8))}</span>${total ? ` · ${current} on it${changed}` : ''}</small>`;
}

function renderTrackRecord(data = {}) {
  const body = $('#track-body');
  if (!body) return;
  const meta = $('#track-meta');
  if (data.error) {
    if (meta) meta.textContent = 'Unavailable';
    const retry = '<button type="button" class="q-btn" data-track-retry>Retry</button>';
    body.innerHTML = data.unreachable
      ? trackStateHtml({ tone: 'bear', title: 'Can’t reach the QUANTA backend',
        body: 'The evidence ledger is served by the local app, which did not respond. This says nothing about the model’s history; check the app is running, then retry.', actions: retry })
      : trackStateHtml({ tone: 'warn', title: 'Track record unavailable',
        body: `${escapeHtml(data.error)}. Scans and tear sheets are unaffected.`, actions: retry });
    body.querySelector('[data-track-retry]')?.addEventListener('click', loadTrackRecord);
    return;
  }
  const t = data.totals || {};
  const recorded = Number(t.recorded) || 0;
  const resolved = Number(t.resolved) || 0;
  const pending = Number(t.pending) || 0;
  const minSample = Number(data.min_sample) || 30;
  const bench = escapeHtml(trackBenchmarkList(data));
  const benchMixed = !!data.benchmark_mixed || (Array.isArray(data.benchmarks) && data.benchmarks.length > 1);
  if (meta) meta.textContent = `${recorded} recorded · ${resolved} resolved`;

  // 1 — Is the model validated? This comes first: it bounds how much any
  // Decision (and any track record below) can be trusted.
  const engines = data.engines || [];
  const anyValidated = engines.some((e) => (e.evidence || {}).permits_recommendation);
  const evidenceRows = engines.map((e) => {
    const ev = e.evidence || {};
    const tone = ev.permits_recommendation ? 'bull' : (ev.status === 'NO_EDGE' ? 'warn' : 'warn');
    return `<tr>
      <td class="track-engine">${escapeHtml(e.label || e.engine)}</td>
      <td><span class="q-status-cell ${tone}">${escapeHtml(ev.status_text || ev.status || 'Unknown')}</span></td>
      <td class="num">${ev.tested_through ? escapeHtml(ev.tested_through) : '<span class="q-na">--</span>'}</td>
      <td class="num">${Number(e.recorded) || 0}${trackFreezeNote(e)}</td>
      <td class="track-means">${escapeHtml(ev.summary || '')}</td>
    </tr>${(ev.lineage || []).map((l) => `<tr class="track-lineage" data-lineage="${escapeHtml(l.strategy || '')}">
      <td class="track-engine muted">↳ earlier version <span class="mono">${escapeHtml(l.strategy || '')}</span></td>
      <td><span class="q-status-cell ${l.status === 'NO_EDGE' ? 'warn' : 'warn'}">${escapeHtml(l.status_text || l.status || '')}</span></td>
      <td class="num">${l.tested_through ? escapeHtml(l.tested_through) : '<span class="q-na">--</span>'}</td>
      <td class="num"><span class="q-na">--</span></td>
      <td class="track-means">${escapeHtml(l.summary || '')}${l.change ? ` <small class="muted">(${escapeHtml(l.change)})</small>` : ''}</td>
    </tr>`).join('')}`;
  }).join('');
  const validation = `<section class="q-panel track-section" aria-labelledby="track-validation-h">
    <header class="q-head"><div class="q-head-l">
      <h2 class="q-head-title" id="track-validation-h">Model validation</h2>
      <p class="q-head-sub">${anyValidated
        ? 'At least one model has passed out-of-sample validation. Its Decisions carry that evidence; the others do not.'
        : 'No model has been validated out of sample yet. Treat every Decision as a ranked research candidate, not a proven signal.'}</p>
    </div></header>
    ${engines.length ? `<div class="q-tablewrap" tabindex="0" role="region" aria-label="Model evidence by engine">
      <table class="q-table"><thead><tr><th>Engine</th><th>Validation</th><th class="num">Tested through</th><th class="num">Decisions recorded</th><th>What it means</th></tr></thead>
      <tbody>${evidenceRows}</tbody></table>
    </div>` : '<div class="q-empty">No engines reported evidence.</div>'}
  </section>`;

  // 2 — The ledger.
  const metrics = `<div class="q-metrics track-metrics">
    <div class="q-metric"><span class="q-metric-k">Decisions recorded</span><span class="q-metric-v">${recorded}</span></div>
    <div class="q-metric"><span class="q-metric-k">Pending horizon</span><span class="q-metric-v">${pending}</span></div>
    <div class="q-metric"><span class="q-metric-k">Resolved (next-open basis)</span><span class="q-metric-v">${resolved}</span></div>
    <div class="q-metric"><span class="q-metric-k">${benchMixed ? 'Benchmarks' : 'Benchmark'}</span><span class="q-metric-v">${bench}</span></div>
  </div>`;

  const recent = data.recent || [];
  const fmtDay = (iso) => {
    const d = iso ? new Date(`${iso}T12:00:00Z`) : null;
    return d && Number.isFinite(d.getTime())
      ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }) : null;
  };
  const awaitingMaturity = Number(t.awaiting_maturity) || 0;
  const awaitingResolution = Number(t.awaiting_resolution) || 0;
  const earliestExit = fmtDay(t.earliest_expected_exit);
  // The estimate starts from each call's own entry schedule (next session after
  // publication), not from the analysis date, and skips US market holidays only.
  const matureText = earliestExit
    ? ` The earliest expected exit session is about <b>${escapeHtml(earliestExit)}</b> (estimated from publication and entry, not guaranteed).`
    : '';
  const legacyN = Number(t.legacy_excluded) || 0;

  let ledgerState = '';
  if (recorded === 0) {
    ledgerState = trackStateHtml({ title: 'No Decisions recorded yet',
      body: 'A Decision is recorded when a Picks scan publishes picks or a tear sheet completes. Use <b>Resolve outcomes</b> once a horizon has ended, or let <code>scripts/schedule_daily.sh</code> scan, resolve and check levels every weekday so the record builds itself.',
      actions: '<a class="q-btn" href="#picks" data-view-link="picks">Run a scan</a><a class="q-btn" href="#analyze" data-view-link="analyze" data-focus="#an-ticker">Research a ticker</a>' });
  } else if (resolved === 0) {
    // "No outcomes" has several distinct causes; say which ones the counts support.
    const causes = [];
    if (awaitingMaturity) causes.push(`<b>${awaitingMaturity}</b> ${awaitingMaturity === 1 ? 'is' : 'are'} still inside ${awaitingMaturity === 1 ? 'its' : 'their'} horizon`);
    if (awaitingResolution) causes.push(`<b>${awaitingResolution}</b> ${awaitingResolution === 1 ? 'has' : 'have'} passed ${awaitingResolution === 1 ? 'its' : 'their'} horizon without a recorded outcome (use <b>Resolve outcomes</b> above; if they stay pending, a price or benchmark bar is missing)`);
    if (legacyN) causes.push(`<b>${legacyN}</b> earlier as-of-close result${legacyN === 1 ? ' is' : 's are'} excluded from the record`);
    ledgerState = trackStateHtml({ title: 'No resolved track record yet',
      body: `QUANTA has recorded ${recorded} published Decision${recorded === 1 ? '' : 's'}${causes.length ? `: ${causes.join('; ')}` : ''}. Performance against ${bench} appears once outcomes resolve; statistics below ${minSample} resolved Decisions carry no weight as evidence.${matureText}` });
  }
  const sampleNote = resolved > 0 && resolved < minSample
    ? `<p class="q-note track-sample" role="status">${resolved} resolved calls so far; statistics below ${minSample} are shown but carry no weight as evidence.</p>`
    : '';

  const groups = (data.groups || []).filter((g) => (g.n_resolved || 0) + (g.n_pending || 0) > 0);
  const groupTable = resolved > 0 && groups.length ? `<h3 class="track-h">By engine, horizon and stance</h3>
    <div class="q-tablewrap" tabindex="0" role="region" aria-label="Benchmarked outcomes by group">
      <table class="q-table"><thead><tr>
        <th>Engine</th><th>Model version</th><th class="num">Horizon</th><th>Stance</th><th class="num">Resolved</th><th class="num">Pending</th>
        <th>Benchmark</th><th class="num">Hit rate</th><th class="num">Mean excess</th><th class="num">95% CI</th><th>Verdict</th>
      </tr></thead><tbody>${groups.map((g) => {
        const k = g.key || {};
        const ci = Array.isArray(g.ci95_excess_pct) ? `${trackPct(g.ci95_excess_pct[0])} to ${trackPct(g.ci95_excess_pct[1])}` : '--';
        return `<tr class="${g.sufficient ? '' : 'is-thin'}">
          <td>${escapeHtml(TRACK_ENGINE_LABEL[k.engine] || k.engine || '')}</td>
          <td class="mono">${escapeHtml(k.strategy || 'unversioned (legacy)')}</td>
          <td class="num">${k.horizon_days != null ? `${Number(k.horizon_days)}d` : '--'}</td>
          <td>${escapeHtml(titleLabel(k.stance || ''))}</td>
          <td class="num">${g.n_resolved || 0}</td><td class="num">${g.n_pending || 0}</td>
          <td>${escapeHtml((Array.isArray(g.benchmarks) && g.benchmarks.length ? g.benchmarks : [data.benchmark || 'SPY']).map(trackBenchmarkName).join(' + '))}${Array.isArray(g.benchmarks) && g.benchmarks.length > 1 ? ' <small class="muted">mixed markets</small>' : ''}</td>
          <td class="num">${g.hit_rate == null ? '--' : fmtPct(g.hit_rate * 100, 0, false).replace('+', '')}</td>
          <td class="num">${trackPct(g.mean_excess_pct, 2)}</td>
          <td class="num">${ci}</td>
          <td class="track-verdict">${escapeHtml(g.verdict || '')}</td>
        </tr>`;
      }).join('')}</tbody></table>
    </div>` : '';

  const recentRows = recent.map((r) => `<tr>
        <td class="mono">${escapeHtml(r.as_of_date || '')}</td>
        <td><button type="button" class="ticker-link" data-ticker="${escapeHtml(r.ticker || '')}">${escapeHtml(r.ticker || '')}</button></td>
        <td>${escapeHtml(TRACK_ENGINE_LABEL[r.engine] || r.engine || '')}</td>
        <td>${escapeHtml(r.label || r.stance || '')}</td>
        <td class="num">${r.horizon_days != null ? `${Number(r.horizon_days)}d` : '--'}</td>
        <td class="num">${trackPct(r.return_pct, 2)}</td>
        <td class="num">${trackPct(r.excess_return_pct, 2)}${r.excess_return_pct != null && (benchMixed || (r.benchmark && r.benchmark !== 'SPY')) ? `<small class="muted"> vs ${escapeHtml(trackBenchmarkName(r.benchmark))}</small>` : ''}</td>
        <td>${escapeHtml(r.outcome || (r.state === 'awaiting_resolution' ? 'Awaiting resolution' : (r.state === 'awaiting_maturity' ? `Awaiting maturity${fmtDay(r.expected_exit) ? ` · exit ~${fmtDay(r.expected_exit)}` : ''}` : 'Pending')))}</td>
      </tr>`).join('');
  const recentTable = recent.length ? `<div class="q-tablewrap" tabindex="0" role="region" aria-label="Latest recorded Decisions">
      <table class="q-table"><thead><tr>
        <th>As of</th><th>Ticker</th><th>Engine</th><th>Decision</th><th class="num">Horizon</th>
        <th class="num">Return</th><th class="num">${benchMixed ? 'Excess vs benchmark' : `vs ${bench}`}</th><th>Outcome</th>
      </tr></thead><tbody>${recentRows}</tbody></table>
    </div>` : '';
  // With nothing resolved, the list is a log, not a result: keep it one click away.
  const recentBlock = !recent.length ? '' : (resolved > 0
    ? `<h3 class="track-h">Latest Decisions</h3>${recentTable}`
    : `<details class="q-disclose track-pending"><summary>Pending Decisions (${recent.length})</summary><div class="q-disclose-body">${recentTable}</div></details>`);

  // Stops and targets touched by still-open calls: what needs attention today.
  const alerts = Array.isArray(data.alerts) ? data.alerts : [];
  const alertsBlock = alerts.length ? `<h3 class="track-h">Level alerts</h3>
    <p class="q-note">Open calls whose stop or target traded. Informational: an outcome is still scored only at the horizon.</p>
    <div class="q-tablewrap" tabindex="0" role="region" aria-label="Level alerts">
      <table class="q-table"><thead><tr>
        <th>Session</th><th>Ticker</th><th>Level</th><th class="num">Price</th><th>Call</th><th>Engine</th>
      </tr></thead><tbody>${alerts.map((a) => `<tr>
        <td class="mono">${escapeHtml(a.session || '')}</td>
        <td><button type="button" class="ticker-link" data-ticker="${escapeHtml(a.ticker || '')}">${escapeHtml(a.ticker || '')}</button></td>
        <td class="${({ TARGET: 'bull', STOP: 'bear' })[a.kind] || ''}">${({ TARGET: 'Target reached', STOP: 'Stop hit' })[a.kind] || escapeHtml(a.kind || '')}</td>
        <td class="num">${Number.isFinite(Number(a.level)) ? Number(a.level).toFixed(2) : '--'}</td>
        <td>${escapeHtml(a.label || titleLabel(a.stance || ''))} <small class="muted">${escapeHtml(a.as_of_date || '')}</small></td>
        <td>${escapeHtml(TRACK_ENGINE_LABEL[a.engine] || a.engine || '')}</td>
      </tr>`).join('')}</tbody></table>
    </div>` : '';

  const legacy = t.legacy_excluded ? ` ${t.legacy_excluded} earlier as-of-close result(s) are excluded.` : '';
  // 3 — Methodology: the backend's own method statement, one clause per line,
  // plus the thresholds the page applies when it reports anything.
  const methodSteps = String(data.method || '').split(/;\s*|\.\s+/).map((x) => x.trim().replace(/\.$/, '')).filter(Boolean);
  const methodology = `<section class="q-panel track-section" aria-labelledby="track-method-h">
    <header class="q-head"><div class="q-head-l">
      <h2 class="q-head-title" id="track-method-h">Methodology</h2>
      <p class="q-head-sub">How a published Decision becomes an outcome. The ledger adds no statistics of its own.</p>
    </div></header>
    <div class="q-body">
      <ol class="track-steps">
        ${methodSteps.map((x) => `<li>${escapeHtml(x.charAt(0).toUpperCase() + x.slice(1))}.</li>`).join('')}
        <li>Groups report hit rate, mean excess return versus ${benchMixed ? 'each call\u2019s own benchmark' : bench} and a 95% confidence interval; fewer than ${minSample} resolved Decisions are shown but carry no weight as evidence.</li>
        <li>A model counts as validated only when its out-of-sample test passes; until then every Decision is labelled with its evidence status.</li>
      </ol>
      ${legacy ? `<p class="track-method">${escapeHtml(legacy.trim())}</p>` : ''}
    </div>
  </section>`;
  const ledger = `<section class="q-panel track-section" aria-labelledby="track-ledger-h">
    <header class="q-head"><div class="q-head-l">
      <h2 class="q-head-title" id="track-ledger-h">Recorded calls</h2>
      <p class="q-head-sub">Every published Decision, measured against ${benchMixed ? `its own benchmark (${bench})` : bench} over its own horizon.</p>
    </div></header>
    <div class="q-body">
      ${metrics}
      ${ledgerState}${sampleNote}${alertsBlock}${groupTable}${recentBlock}
    </div>
  </section>`;

  body.innerHTML = `${validation}${ledger}${methodology}`;
  body.querySelectorAll('.ticker-link[data-ticker]').forEach((b) => b.addEventListener('click', () => {
    openTickerTearSheet(b.dataset.ticker);
  }));
}

async function loadTrackRecord() {
  const body = $('#track-body');
  if (body) body.setAttribute('aria-busy', 'true');
  try {
    const res = await fetch('/api/track-record', { cache: 'no-store' });
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    renderTrackRecord(res.ok ? data : { error: data.error || `HTTP ${res.status}` });
  } catch (err) {
    // A thrown fetch means no response at all — distinct from an empty ledger.
    renderTrackRecord({ error: 'The backend could not be reached', unreachable: true });
  } finally {
    if (body) body.removeAttribute('aria-busy');
  }
}

async function resolveTrackRecord() {
  const btn = $('#track-resolve');
  const meta = $('#track-meta');
  if (btn) { btn.disabled = true; btn.textContent = 'Resolving…'; }
  try {
    const res = await fetch('/api/track-record/resolve', { method: 'POST', headers: QUANTA_POST_HEADERS });
    const data = await res.json().catch(() => ({}));
    await loadTrackRecord();
    if (meta) {
      meta.textContent = res.ok
        ? `${data.resolved || 0} resolved · ${(data.alerts || []).length} new alert${(data.alerts || []).length === 1 ? '' : 's'}`
        : (data.error || `Resolve failed (HTTP ${res.status})`);
    }
  } catch (err) {
    if (meta) meta.textContent = 'Resolve failed: backend unreachable';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Resolve outcomes'; }
  }
}

function applyTheme(theme, { persist = false } = {}) {
  const next = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  document.documentElement.style.colorScheme = next;
  const meta = $('#theme-color-meta');
  if (meta) meta.setAttribute('content', next === 'light' ? '#F6F8FC' : '#070A12');
  const themeBtn = $('#theme-toggle');
  if (themeBtn) {
    const isDark = next === 'dark';
    themeBtn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    themeBtn.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
    themeBtn.setAttribute('title', isDark ? 'Switch to light mode' : 'Switch to dark mode');
  }
  if (persist) {
    try { localStorage.setItem('quanta_theme', next); } catch (_) {}
  }
}

const sectionUnavailableCard = (title, reason) => `
  <article class="section-card section-fallback-card">
    <h3 class="section-kicker">${escapeHtml(title)}</h3>
    <p class="section-fallback-text">Section unavailable: ${escapeHtml(reason || 'Data not returned by the analysis engine.')}</p>
  </article>`;

function splitHeadlinesByRelevance(headlines, ticker, companyName) {
  const all = Array.isArray(headlines) ? headlines.slice(0, 14).map((h) => String(h || '').trim()).filter(Boolean) : [];
  const sym = String(ticker || '').toUpperCase().trim();
  const baseName = String(companyName || '').split(/[\s,.&]+/).filter(Boolean)[0] || '';
  const baseUpper = baseName.toUpperCase();
  const tickerRegex = sym ? new RegExp(`(^|[^A-Z0-9])${sym}([^A-Z0-9]|$)`, 'i') : null;

  const isTickerSpecific = (headline) => {
    const h = String(headline || '').trim();
    if (!h) return false;
    if (tickerRegex && tickerRegex.test(h)) return true;
    const hu = h.toUpperCase();
    if (!baseUpper) return false;
    return hu.startsWith(`${baseUpper} `) || hu.startsWith(`${baseUpper}'`) || hu.startsWith(`${baseUpper}:`);
  };

  const tickerNews = all.filter((h) => isTickerSpecific(h)).slice(0, 5);
  const marketContext = all.filter((h) => !isTickerSpecific(h)).slice(0, 5);
  return {
    tickerNews,
    marketContext,
    limitedTickerNews: tickerNews.length < 3,
  };
}

function computeRequiredSectionAudit(data, derived) {
  const hasActionableEntry = isPositive(derived?.entryP)
    || isPositive(data?.beginner_guidance?.pullback_entry)
    || isPositive(data?.beginner_guidance?.breakout_entry);
  const hasActionableTarget = isPositive(derived?.baseTgt)
    || isPositive(derived?.bearTgt)
    || isPositive(derived?.bullTgt);
  const checks = [
    { key: 'ticker', label: 'Ticker', ok: !!String(data?.ticker || '').trim() },
    { key: 'current_price', label: 'Current Price', ok: isPositive(derived?.currentPrice) },
    { key: 'recommendation', label: 'Recommendation', ok: !!String(derived?.recommendation || '').trim() },
    { key: 'confidence', label: 'Confidence', ok: data?.confidence_score !== null && data?.confidence_score !== undefined && data?.confidence_score !== '' && Number.isFinite(Number(data?.confidence_score)) },
    // A non-funded Decision that states it has no valuation target (ETF, no
    // analyst or DCF source) is an honest state, not a missing section.
    { key: 'price_targets', label: 'Price Targets', ok: isPositive(derived?.baseTgt) || isPositive(derived?.bearTgt) || isPositive(derived?.bullTgt)
      || !!(data?.decision && data.decision.new_money && data.decision.new_money !== 'ELIGIBLE' && !isPositive(data.decision.levels?.target)) },
    { key: 'beginner_guidance', label: 'Beginner Guidance', ok: !!data?.beginner_guidance },
    { key: 'action_plan', label: 'Action Plan', ok: !!data?.action_plan_title || (hasActionableEntry && hasActionableTarget) },
    { key: 'portfolio_outlook', label: 'Portfolio Outlook', ok: !!data?.portfolio_outlook },
  ];
  return {
    checks,
    missing: checks.filter((c) => !c.ok).map((c) => c.label),
    renderedCount: checks.filter((c) => c.ok).length,
    requiredCount: checks.length,
  };
}

function renderRequiredMissingBanner(missing) {
  if (!missing.length) return '';
  return `
    <article class="section-card validation-banner tone-bear">
      <h3 class="section-kicker">Render validation</h3>
      <p class="validation-headline"><strong>Analysis incomplete: required sections missing.</strong></p>
      <ul>
        ${missing.map((m) => `<li>${escapeHtml(m)}</li>`).join('')}
      </ul>
    </article>`;
}

// Data Confidence summary — the one place that states, in plain words, how
// much to trust this tear sheet: engine confidence, data-quality status,
// critical / optional validation, and whether the run was a fast preview.
function renderConfidenceSummaryCard(data, validationSummary, requiredAudit, dqGate) {
  // 0-100 heuristic conviction, not a probability — see renderExecutiveSummary.
  const conf = data.confidence_score != null && Number.isFinite(Number(data.confidence_score))
    ? `${fmtNumber(data.confidence_score, 0)}/100` : '--';
  const level = titleLabel(data.confidence_level || '--');
  const dq = String((data.beginner_guidance || {}).data_quality || data.data_quality_status || 'unknown').toUpperCase();
  const vs = validationSummary || {};
  const critical = vs.criticalStatus || 'PASS';
  const optional = vs.optionalStatus || 'PASS';
  const toneFor = (s) => (s === 'PASS' || s === 'OK' ? 'bull' : (s === 'FAIL' ? 'bear' : 'neutral'));
  const mode = data.fast_mode ? 'Fast preview (optional modules skipped)' : (data.used_cached_data ? 'Cached tear sheet' : 'Full analysis');
  const audit = requiredAudit || {};
  const verdict = dqGate && dqGate.failed
    ? 'Critical data is missing: treat every figure as unverified.'
    : (critical === 'PASS' && optional === 'PASS'
      ? 'All critical and optional checks passed.'
      : `Critical checks ${critical.toLowerCase()}; optional data ${optional.toLowerCase()}, so confidence is reduced; the verdict stands.`);
  const cells = [
    ['Data Quality', dq, toneFor(dq)],
    ['Critical Checks', critical, toneFor(critical)],
    ['Optional Data', optional, toneFor(optional)],
    ['Required Sections', `${audit.renderedCount != null ? audit.renderedCount : '--'}/${audit.requiredCount != null ? audit.requiredCount : '--'}`, (audit.missing || []).length ? 'bear' : 'bull'],
    ['Run Mode', mode, data.fast_mode ? 'neutral' : 'bull'],
  ];
  return `
    <article class="section-card confidence-card">
      <h3 class="section-kicker">Data confidence</h3>
      <div class="exec-grid">
        ${cells.map(([k, v, t]) => `<div class="exec-cell"><div class="exec-label">${escapeHtml(k)}</div><div class="exec-value tone-${t}">${escapeHtml(String(v))}</div></div>`).join('')}
      </div>
      <p class="exec-reason">${escapeHtml(verdict)}</p>
    </article>`;
}

function marketRegimeCard(data) {
  const regime = titleLabel(data.market_regime || '--');
  const confidence = titleLabel(data.confidence_level || '--');
  const riskGrade = data?.risk?.risk_grade || '--';
  const timing = data?.beginner_guidance?.candlestick_timing || '--';
  return `
    <article class="section-card">
      <h3 class="section-kicker">Market regime</h3>
      <div class="compact-facts">
        <div class="fact"><span class="k">Regime</span><span class="v">${escapeHtml(regime)}</span></div>
        <div class="fact"><span class="k">Confidence</span><span class="v">${escapeHtml(confidence)}</span></div>
        <div class="fact"><span class="k">Risk Grade</span><span class="v">${escapeHtml(riskGrade)}</span></div>
        <div class="fact"><span class="k">Timing</span><span class="v">${escapeHtml(timing)}</span></div>
      </div>
    </article>`;
}

function riskFactsHtml(risk) {
  const has = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const showPct = (value) => (has(value) ? fmtPct(value, 1, true) : 'Not available');
  // Volatility is a level: "28.8%", never "+28.8%".
  const showLevel = (value) => (has(value) ? `${fmtNumber(Number(value) * 100, 1)}%` : 'Not available');
  return `
      <div class="compact-facts">
        <div class="fact"><span class="k">Volatility (Ann)</span><span class="v">${showLevel(risk.annualized_volatility)}</span></div>
        <div class="fact"><span class="k">Beta</span><span class="v">${risk.beta_status === 'measured' && has(risk.beta) ? `${fmtNumber(risk.beta, 2)} · ${escapeHtml(risk.beta_benchmark || 'benchmark')} · ${Number(risk.beta_observations || 0)} sessions` : 'Not available'}</span></div>
        <div class="fact"><span class="k">Sharpe</span><span class="v">${fmtNumber(risk.sharpe_ratio, 2)}</span></div>
        <div class="fact"><span class="k">Sortino</span><span class="v">${fmtNumber(risk.sortino_ratio, 2)}</span></div>
        <div class="fact"><span class="k">Max drawdown</span><span class="v">${showPct(risk.max_drawdown)}</span></div>
        <div class="fact"><span class="k">Daily VaR, 95%</span><span class="v">${showPct(risk.var_95_daily)}</span></div>
      </div>`;
}

function renderRiskMetricsCard(risk) {
  if (!risk) return sectionUnavailableCard('Risk metrics', 'Risk object missing in payload.');
  return `
    <article class="section-card">
      <h3 class="section-kicker">Risk metrics</h3>
      ${riskFactsHtml(risk)}
    </article>`;
}

function renderSourceLedgerCard(data) {
  const sentiment = data.sentiment || {};
  const cache = data.cache || {};
  const sourceNames = [...new Set((sentiment.key_headline_sources || []).filter(Boolean))].slice(0, 5);
  const fmtWhen = (iso) => (iso && !Number.isNaN(Date.parse(iso)) ? new Date(iso).toLocaleString() : '');
  const decisionSources = Array.isArray(data.decision?.sources) ? data.decision.sources : [];
  const priceSource = decisionSources.find((s) => s.kind === 'price');
  const ts = data.analysis_timestamp || data.retrieval_timestamp
    || data.decision?.generated_at || cache.timestamp || cache.generated_at
    || data.generated_at || data.timestamp || null;
  const cacheLabel = cache.hit === true
    ? `Hit (${cache.age_seconds || 0}s)`
    : (cache.hit === false ? 'New calculation' : 'Not available');
  return `
    <article class="section-card">
      <h3 class="section-kicker">Source ledger</h3>
      <div class="compact-facts">
        <div class="fact"><span class="k">Cache</span><span class="v">${cacheLabel}</span></div>
        <div class="fact"><span class="k">Calculated</span><span class="v">${escapeHtml(ts && !Number.isNaN(Date.parse(ts)) ? new Date(ts).toLocaleString() : 'Not available')}</span></div>
        <div class="fact"><span class="k">Price source / session</span><span class="v">${escapeHtml(priceSource ? `${priceSource.provider || 'Unknown'} · ${priceSource.as_of || 'date unknown'}` : 'Not supplied')}</span></div>
        <div class="fact"><span class="k">Price retrieved</span><span class="v">${escapeHtml(fmtWhen(priceSource?.retrieved_at) || 'Not supplied')}</span></div>
        <div class="fact"><span class="k">Street target as-of</span><span class="v">${escapeHtml(data.market_intel?.target_mean ? 'Not supplied by provider' : 'No target returned')}</span></div>
        <div class="fact"><span class="k">Headline sources</span><span class="v">${sourceNames.length ? escapeHtml(sourceNames.join(', ')) : 'None returned'}</span></div>
        ${data.symbol_note ? `<div class="fact"><span class="k">Listing</span><span class="v">${escapeHtml(data.ticker || '')}${data.requested_symbol && data.requested_symbol !== data.ticker ? ` (entered ${escapeHtml(data.requested_symbol)})` : ''}</span></div>` : ''}
      </div>
    </article>`;
}

function renderDevDebugPanel(info) {
  if (!info || !info.enabled) return '';
  const errors = Array.isArray(info.errors) ? info.errors : [];
  const missing = Array.isArray(info.missing) ? info.missing : [];
  return `
    <article class="section-card dev-debug-panel">
      <h3 class="section-kicker">Debug diagnostics</h3>
      <div class="detail-rows">
        ${detailRow('Ticker', info.ticker || '--')}
        ${detailRow('Fetch Status', info.fetchStatus || '--')}
        ${detailRow('Analysis Object', info.hasAnalysis ? 'yes' : 'no')}
        ${detailRow('Required Rendered', `${info.renderedCount || 0}/${info.requiredCount || 0}`)}
        ${detailRow('Missing Sections', missing.length ? missing.join(', ') : 'none')}
        ${detailRow('Data Quality', info.dataQuality || '--')}
        ${detailRow('Validation Errors', errors.length ? errors.join('; ') : 'none')}
        ${detailRow('Last Refresh', info.lastRefresh || '--')}
      </div>
    </article>`;
}

function isDebugUiEnabled() {
  if (typeof window === 'undefined') return false;
  const host = String(window.location.hostname || '');
  const explicit = (() => {
    try { return String(window.localStorage?.getItem('quanta_debug_ui') || '') === '1'; } catch (_) { return false; }
  })();
  // The app only ever runs on loopback, so a host check turned diagnostics on
  // for everyone. Opt in with ?debug=1 or localStorage quanta_debug_ui=1.
  const query = /[?&]debug=1\b/.test(String(window.location.search || ''));
  return explicit || (query && (host === '127.0.0.1' || host === 'localhost'));
}

/* ---------------------------------------------------------------------------
   CLOCK + FRESHNESS
   --------------------------------------------------------------------------- */
const MARKET_TZ = {
  US: { tz: 'America/New_York', label: 'ET' },
  IN: { tz: 'Asia/Kolkata',     label: 'IST' },
};

function currentMarketTz() {
  return MARKET_TZ[state.market] || MARKET_TZ.US;
}

// Where the selected exchange is in its trading day. Holidays are not modelled,
// so callers pair this with the quote's own bar date rather than trusting it alone.
function exchangeSession(marketKey = state.market, now = new Date()) {
  const cfg = MARKET_TZ[marketKey] || MARKET_TZ.US;
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone: cfg.tz, hour12: false, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).formatToParts(now).reduce((o, p) => { o[p.type] = p.value; return o; }, {});
  } catch (_) { return { today: null, phase: 'unknown', tzLabel: cfg.label }; }
  const minutes = (Number(parts.hour) % 24) * 60 + Number(parts.minute);
  const weekend = parts.weekday === 'Sat' || parts.weekday === 'Sun';
  const open = marketKey === 'IN' ? 9 * 60 + 15 : 9 * 60 + 30;
  const close = marketKey === 'IN' ? 15 * 60 + 30 : 16 * 60;
  let phase = 'closed';
  if (!weekend) {
    if (minutes >= open && minutes < close) phase = 'regular';
    else if (marketKey !== 'IN' && minutes >= 4 * 60 && minutes < open) phase = 'pre';
    else if (marketKey !== 'IN' && minutes >= close && minutes < 20 * 60) phase = 'after';
  }
  return { today: `${parts.year}-${parts.month}-${parts.day}`, phase, tzLabel: cfg.label };
}

// What a quote's price actually is: the session it belongs to, kept apart from
// when it was retrieved. The tape is built from daily bars, so during the
// regular session it is delayed, and outside it the price is the close.
function quoteFreshness(q, now = new Date()) {
  const ex = exchangeSession(state.market, now);
  const short = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  let session;
  if (!q.quote_date) session = q.price_basis === 'last_price' ? 'Last trade · session time not reported' : 'Session time not reported';
  else if (ex.today && q.quote_date === ex.today) {
    session = ex.phase === 'regular' ? `In session ${short(q.quote_date)} · delayed`
      : `Close ${short(q.quote_date)}${ex.phase === 'after' || ex.phase === 'pre' ? ' · regular session only' : ''}`;
  } else session = `Previous close ${short(q.quote_date)}`;
  const flags = [];
  if (q.stale) flags.push('stale');
  else if (q.cached) flags.push('cached');
  const got = q.retrieved_at || q.as_of;
  const gotDate = got ? new Date(got) : null;
  const retrieved = gotDate && Number.isFinite(gotDate.getTime())
    ? `retrieved ${gotDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: (MARKET_TZ[state.market] || MARKET_TZ.US).tz })} ${ex.tzLabel}` : 'retrieval time unknown';
  return { session, flags, retrieved, text: [session, ...flags, retrieved].join(' · ') };
}

let _clockTimer = null;
function startClock() {
  const el = $('#clock-text');
  const tzEl = $('#clock-tz');
  const dateEl = $('#clock-date');
  if (!el) return;
  const tick = () => {
    const { tz, label } = currentMarketTz();
    const now = new Date();
    el.textContent = now.toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: tz,
    });
    // The session date belongs beside the session time, not chained onto a
    // panel label somewhere down the page.
    if (dateEl) {
      const d = now.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: tz });
      if (dateEl.textContent !== d) dateEl.textContent = d;
    }
    if (tzEl && tzEl.textContent !== label) tzEl.textContent = label;
  };
  tick();
  if (_clockTimer) clearInterval(_clockTimer);
  _clockTimer = setInterval(tick, 1000);
}

/** Update the topbar freshness chip + brief/source status badges from the dashboard payload. */
let _freshnessTimer = null;
function setFreshness(iso) {
  if (iso) state.lastUpdated = iso;
  const chip = $('#freshness-chip');
  const timeEl = $('#freshness-time');
  if (!chip || !timeEl) return;
  const render = () => {
    if (!state.lastUpdated) {
      timeEl.textContent = '\u2014';
      chip.dataset.staleness = 'unknown';
      return;
    }
    const rel = formatRelativeTime(state.lastUpdated) || 'just now';
    const degraded = !!state.dashboard?.market?.market_regime_engine?.dataFreshness?.stale;
    timeEl.textContent = degraded ? 'data incomplete' : rel;
    chip.title = degraded
      ? `Market inputs are missing or stale (read ${rel}) — the Overview withholds guidance`
      : `Age of the current dashboard read: ${rel}`;
    const ageMs = Date.now() - new Date(state.lastUpdated).getTime();
    const ageMin = ageMs / 60000;
    const staleMin = thr('UI.HEADER_STALE_MINUTES');
    const agingMin = thr('UI.HEADER_AGING_MINUTES');
    chip.dataset.staleness = degraded ? 'stale' : (ageMin >= staleMin ? 'stale' : (ageMin >= agingMin ? 'aging' : 'fresh'));
  };
  render();
  if (_freshnessTimer) clearInterval(_freshnessTimer);
  _freshnessTimer = setInterval(render, 30000);
  // Mirror freshness onto the in-card status badges so they stop lying about "Live"
  ['#brief-status', '#source-status'].forEach((sel) => {
    const node = $(sel);
    if (node) {
      node.innerHTML = `<span class="live-dot" aria-hidden="true"></span>Updated ${escapeHtml(formatRelativeTime(state.lastUpdated) || 'just now')}`;
      node.classList.remove('badge-live');
      node.classList.add('badge-status');
    }
  });
}

function setRefreshLoading(loading) {
  const btn = $('#refresh-dashboard');
  if (!btn) return;
  btn.classList.toggle('is-loading', !!loading);
  btn.disabled = !!loading;
  btn.setAttribute('aria-busy', loading ? 'true' : 'false');
  const label = btn.querySelector('.refresh-label');
  if (label) label.textContent = loading ? 'Refreshing\u2026' : 'Refresh';
}

async function clearDataCache() {
  const btn = $('#clear-cache');
  const label = btn?.querySelector('.clear-cache-label');
  const restore = label ? label.textContent : '';
  if (btn) { btn.disabled = true; btn.classList.add('is-busy', 'has-status'); }
  if (label) label.textContent = 'Clearing\u2026';
  try {
    const res = await fetch('/api/cache/clear', { method: 'POST', headers: QUANTA_POST_HEADERS });
    const data = await res.json().catch(() => ({}));
    if (label) {
      label.textContent = data.ok ? `Cleared (${data.dropped ?? 0})` : 'Failed';
    }
  } catch (err) {
    if (label) label.textContent = 'Failed';
  } finally {
    setTimeout(() => {
      if (label) label.textContent = restore || 'Clear cache';
      if (btn) { btn.disabled = false; btn.classList.remove('is-busy', 'has-status'); }
    }, 1800);
  }
}

/* ---------------------------------------------------------------------------
   DASHBOARD
   --------------------------------------------------------------------------- */
async function fetchJSON(url, { retries = 2, timeoutMs = 25000 } = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    } catch (err) {
      clearTimeout(timer);
      lastErr = err;
      if (attempt < retries) await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr || new Error('request failed');
}

function renderIntelSkeleton() {
  const skel = (n) => `<div class="q-skelrows" aria-busy="true">${Array.from({ length: n }, () => '<span class="skel-line"></span>').join('')}</div>`;
  [['portfolio-scan-body', 4], ['setup-status-body', 2], ['top-movers-body', 3]].forEach(([id, n]) => {
    const el = $(`#${id}`); if (el) el.innerHTML = skel(n);
  });
}

let _intelPollHandle = null;
let _intelPollAttempts = 0;
let _intelRequest = null;
let _dashboardGeneration = 0;
let _dashboardRequest = null;
const INTEL_POLL_MAX_ATTEMPTS = 10;

function renderIntelPanels(intel) {
  renderResearchChips();
  renderTopOpportunities(intel);
  renderOpportunityBrief(intel);
  renderTopMovers(intel);
  renderOpenExtremes(intel);
  renderSetupStatusCard(intel);
  renderBreadthPanel(state.dashboard?.market || {}, intel);
  renderCatalystCalendar(intel, state.dashboard?.news || {});
  // The risk card counts the same earnings list: redraw it with the list that
  // just arrived, or it keeps the 0 it read while intel was still loading.
  if (state.dashboard) renderRiskPanel(state.dashboard.market || {}, state.dashboard.news || {}, intel);
  // The book and the week read the movers and the earnings list too.
  renderBookPanel();
  renderWeekCalendar();
  const body = $('#top-movers-body');
  if (body && (intel.stale || intel.feed_timeout)) {
    body.insertAdjacentHTML('afterbegin', `<div class="q-note" role="status">${intel.stale ? 'Showing the last available quotes; refresh is delayed.' : 'Quote refresh did not finish. Try again.'} <button type="button" class="q-btn" data-retry="market-intel">Retry</button></div>`);
  }
}

async function loadMarketIntel({ pollUntilReady = true, resetAttempts = false } = {}) {
  const market = state.market;
  const generation = _dashboardGeneration;
  if (_intelRequest?.market === market && _intelRequest.generation === generation) return _intelRequest.promise;
  const current = () => market === state.market && generation === _dashboardGeneration;
  const stopPolling = () => { if (_intelPollHandle) { clearTimeout(_intelPollHandle); _intelPollHandle = null; } };
  stopPolling();
  if (resetAttempts) _intelPollAttempts = 0;
  const request = { market, generation };
  _intelRequest = request;
  request.promise = (async () => {
    try {
      const url = `/api/market-intel?market=${encodeURIComponent(market)}&blocking=0`;
      let intel = await fetchJSON(url, { retries: 0, timeoutMs: 8000 });
      if (!current()) return;
      const pending = intel.loading || intel.refreshing;
      const exhausted = pending && (!pollUntilReady || _intelPollAttempts >= INTEL_POLL_MAX_ATTEMPTS);
      if (exhausted) intel = { ...intel, loading: false, refreshing: false, feed_timeout: true };
      if (state.dashboard) state.dashboard.market_intel = intel;
      renderIntelPanels(intel);
      if (pending && !exhausted) {
        _intelPollAttempts += 1;
        _intelPollHandle = setTimeout(() => loadMarketIntel({ pollUntilReady: true }), 4000);
      } else {
        _intelPollAttempts = 0;
      }
    } catch (err) {
      if (!current()) return;
      const prior = state.dashboard?.market_intel || {};
      const failed = { ...prior, loading: false, refreshing: false, feed_timeout: true, stale: !!prior.as_of };
      if (state.dashboard) state.dashboard.market_intel = failed;
      renderIntelPanels(failed);
      _intelPollAttempts = 0;
    } finally {
      if (_intelRequest === request) _intelRequest = null;
    }
  })();
  return request.promise;
}

/* A first read can miss inputs that arrive seconds later: index histories, or
   the earnings screen still warming up after a restart. Read the dashboard once
   more after the server's retry time; a second incomplete read stays as it is. */
let _regimeFollowUp = null;
let _regimeFollowUpKey = null;
function scheduleRegimeFollowUp(data, market) {
  const missing = (((data && data.market) || {}).market_regime_engine || {}).dataFreshness?.missingInputs || [];
  const wait = Number(data && data.retry_seconds);
  if (!missing.length || !(wait > 0) || _regimeFollowUpKey === market) return;
  _regimeFollowUpKey = market;
  clearTimeout(_regimeFollowUp);
  _regimeFollowUp = setTimeout(() => {
    _regimeFollowUp = null;
    if (state.market === market) loadDashboard(false);
  }, (wait + 1) * 1000);
  if (_regimeFollowUp && typeof _regimeFollowUp.unref === 'function') _regimeFollowUp.unref();
}

async function loadDashboard(force = false) {
  const marketKey = state.market;
  // A refresh the user asks for (or a new market) earns its own follow-up read.
  if (force || (_regimeFollowUpKey && _regimeFollowUpKey !== marketKey)) _regimeFollowUpKey = null;
  if (_dashboardRequest?.market === marketKey) return _dashboardRequest.promise;
  const generation = ++_dashboardGeneration;
  if (_intelPollHandle) clearTimeout(_intelPollHandle);
  _intelPollAttempts = 0;
  const current = () => generation === _dashboardGeneration && marketKey === state.market;
  if (state.dashboard?.selected_market && state.dashboard.selected_market !== marketKey) state.dashboard = null;
  const request = { market: marketKey };
  _dashboardRequest = request;
  setRefreshLoading(true);
  request.promise = (async () => {
    try {
      if (!state.dashboard) renderIntelSkeleton();
      const params = new URLSearchParams();
      if (force) params.set('refresh', '1');
      params.set('market', state.market);
      const data = await fetchJSON(`/api/dashboard?${params.toString()}`, { retries: 2, timeoutMs: 40000 });
      if (!current()) return;
      state.dashboard = data;
      setFreshness(data.generated_at || new Date().toISOString());
      renderResearchChips();
      state.shownSymbols = new Set();
      const market = data.market || {};
      const news = data.news || {};
      const intel = data.market_intel || {};
      // Render order mirrors the page's information architecture: context first
      // (tape → regime → playbook → risk → ribbon), then execution, then detail.
      renderTopHeadlines(market, news);
      renderMarketRegime(market, news, intel);
      renderPlaybook(market, news, intel);
      renderRiskPanel(market, news, intel);
      renderMarketPulse(market);
      renderOverviewChart(market);
      renderTopOpportunities(intel);
      renderOpportunityBrief(intel);
      renderSignalCheck(market, news, intel);
      renderMarketDrivers(market, news, intel);
      renderBreadthPanel(market, intel);
      renderCatalystCalendar(intel, news);
      renderTopMovers(intel);
      renderOpenExtremes(intel);
      renderSetupStatusCard(intel);
      renderNews(news.groups || {});
      renderSourceLedger(news);
      renderRegimeDebugPanel(market, news, intel);
      // October 2026 context: what changed, what would change the call, then the
      // market context and the book, which load on their own.
      renderWhatChanged(market, news, intel);
      renderMarketContext();
      // Browser-only enrichments: they never run in the test harnesses, which
      // answer requests in a fixed order.
      if (typeof window !== 'undefined') { loadMarketContext(marketKey); loadBook(); }
      scheduleRegimeFollowUp(data, marketKey);
      // If intel is still warming up, poll for it
      if (intel.loading || intel.refreshing || (intel.movers_up && intel.movers_up.length === 0 && intel.universe_size === 0)) {
        loadMarketIntel({ pollUntilReady: true });
      }
    } catch (err) {
      if (!current()) return;
      // [RESILIENCE] A transient/slow refresh must NOT destroy an already-rendered
      // dashboard. Only take over with the full offline state on the FIRST load
      // (nothing rendered yet). Otherwise keep the last good render and just mark
      // freshness as stale so the user still sees the most recent valid read.
      if (state.dashboard) {
        const chip = $('#freshness-chip');
        const timeEl = $('#freshness-time');
        if (chip) chip.dataset.staleness = 'stale';
        // Keep the compact global status compact; the full explanation and retry
        // action live in the prominent stale-data banner directly below it.
        if (timeEl) timeEl.textContent = 'refresh failed';
        if (chip) chip.title = 'Refresh failed — showing the last good dashboard read';
        if (_freshnessTimer) { clearInterval(_freshnessTimer); _freshnessTimer = null; }
        // A corner chip is too quiet for stale trading guidance: put an explicit
        // banner on the Decision Brief so nobody acts on old data unknowingly.
        const hero = $('#decision-brief');
        if (hero && !hero.querySelector('.cc-stale-banner')) {
          const lastTs = state.dashboard.generated_at
            ? new Date(state.dashboard.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : null;
          hero.insertAdjacentHTML('afterbegin', `<div class="cc-stale-banner" role="alert">
            Refresh failed — showing the last good read${lastTs ? ` from ${escapeHtml(lastTs)}` : ''}.
            Treat this guidance as stale and size conservatively.
            <button type="button" class="link-btn" data-retry="dashboard">Retry</button>
          </div>`);
        }
      } else {
        renderDashboardError(err);
      }
    } finally {
      if (current()) setRefreshLoading(false);
      if (_dashboardRequest === request) _dashboardRequest = null;
    }
  })();
  return request.promise;
}

/** Render an honest, unified error state across the dashboard when the
 *  primary fetch fails. Also marks the freshness chip as stale so it stops
 *  claiming "just now" against data that never arrived. */
function renderDashboardError(err) {
  const msg = friendlyFetchError(err) || 'Unknown error';
  // Demote freshness chip
  const chip = $('#freshness-chip');
  const timeEl = $('#freshness-time');
  if (chip) chip.dataset.staleness = 'stale';
  if (timeEl) timeEl.textContent = 'offline';
  if (_freshnessTimer) { clearInterval(_freshnessTimer); _freshnessTimer = null; }
  ['#brief-status', '#source-status'].forEach((sel) => {
    const node = $(sel); if (node) node.innerHTML = '<span class="live-dot" aria-hidden="true" style="background:var(--bear);box-shadow:none;animation:none"></span>Offline';
  });
  // Decision Brief takes the primary error
  const hero = $('#decision-brief');
  if (hero) hero.innerHTML = `<div class="error-box" role="alert">
    <strong>Dashboard temporarily unreachable.</strong>
    <button type="button" class="link-btn" data-retry="dashboard">Retry</button>
    <div class="muted" style="margin-top:6px;font-size:11px">${escapeHtml(msg)}</div>
  </div>`;
  // Quiet the loading shimmers in dependent cards
  const offline = '<div class="empty-state">Offline \u2014 retry once the backend is reachable.</div>';
  ['#news-body', '#trends-body', '#signal-check-body', '#market-drivers-body',
   '#portfolio-scan-body', '#setup-status-body', '#allocator-body', '#source-ledger-body',
   '#event-risk-body', '#top-headlines', '#top-movers-body', '#open-extremes-body',
   '#market-pulse', '#breadth-body', '#playbook-body', '#risk-body'].forEach((sel) => {
    const node = $(sel);
    if (node) node.innerHTML = offline;
  });
}
if (typeof window !== 'undefined') {
  window.loadDashboard = loadDashboard;
  window.loadMarketIntel = loadMarketIntel;
}









// [REGIME LADDER] Pure mapping from the factor tally to a posture rung. Used by
// the no-engine fallback path in computeRegimeModel(); never by the UI directly.
function regimeLadder(bull, bear, net) {
  if (net <= -2 && bear >= 3 && bull === 0)
    return { posture: 'Defensive', signal: 'Risk-Off', label: 'Defensive Market', tone: 'bear' };
  if (net <= -2)
    return { posture: 'Selective Defensive', signal: 'Selective Risk-Off', label: 'Defensive — Selective', tone: 'bear' };
  if (net === -1)
    return { posture: 'Lean Defensive', signal: 'Neutral-to-Negative', label: 'Cautious Market', tone: 'bear' };
  if (net >= 2 && bull >= 3 && bear === 0)
    return { posture: 'Offensive', signal: 'Risk-On', label: 'Constructive Market', tone: 'bull' };
  if (net >= 2)
    return { posture: 'Selective Offensive', signal: 'Selective Risk-On', label: 'Constructive — Selective', tone: 'bull' };
  if (net === 1)
    return { posture: 'Lean Long', signal: 'Neutral-to-Positive', label: 'Mildly Constructive', tone: 'bull' };
  return { posture: 'Neutral', signal: 'Mixed', label: 'Mixed Market', tone: 'neutral' };
}

function normalizeToneClass(tone) {
  if (tone === 'bull' || tone === 'bear' || tone === 'neutral') return tone;
  if (tone === 'warn') return 'neutral';
  return 'neutral';
}

// [COMMAND-CENTER] SINGLE SOURCE OF TRUTH for the regime read. Hero, Today's
// Decision, and Portfolio Action all consume this — so the page can never show
// "Constructive" next to "Reduce exposure" again.
function computeRegimeModel(market = {}, news = {}, intel = {}) {
  const engine = market.market_regime_engine || market.marketRegimeEngine || null;
  if (engine && typeof engine === 'object') {
    const factors = Array.isArray(engine.factors) ? engine.factors : [];
    const net = Number(engine.regimeScore ?? factors.reduce((acc, f) => acc + (Number(f.score) || 0), 0)) || 0;
    const bull = factors.filter((f) => Number(f.score) > 0).length;
    const bear = factors.filter((f) => Number(f.score) < 0).length;
    const flat = factors.length - bull - bear;
    const toneRaw = String(engine.colorTheme?.toneClass || '').toLowerCase();
    const postureTone = normalizeToneClass(toneRaw);
    const aligned = postureTone === 'bull'
      ? bull
      : postureTone === 'bear'
        ? bear
        : Math.max(bull, bear);
    const vix = firstUsable(market.vix_level);
    const tnx = firstUsable(market.ten_year_yield);
    const breadth = firstUsable(market.participation_pct);
    const earningsCount = earningsAhead(intel);
    const highImpact = Object.values(news.groups || {})
      .flatMap((g) => (Array.isArray(g) ? g : []))
      .filter((n) => Number(n && n.impact || 0) >= 6).length;

    const primary = engine.primaryAction || {};
    const guidance = engine.portfolioGuidance || {};
    const model = {
      tone: market.tone || 'Neutral',
      posture: engine.posture || 'Neutral',
      postureTone,
      modelSignal: engine.modelSignal || 'Balanced / Mixed',
      label: engine.marketRegime || 'Mixed Market',
      alignment: {
        aligned,
        bull,
        bear,
        flat,
        count: factors.length,
        support: factors.filter((f) => Number(f.score) > 0).map((f) => f.label || f.key),
        oppose: factors.filter((f) => Number(f.score) < 0).map((f) => f.label || f.key),
        neutral: factors.filter((f) => Number(f.score) === 0).map((f) => f.label || f.key),
      },
      breakdownText: engine.shortSummary || market.tone_detail || '',
      net,
      count: factors.length,
      factors,
      vix,
      tnx,
      breadth,
      breadthIsStructural: breadth != null,
      // Coverage-aware detail: this is a handful of index proxies, not
      // exchange constituent breadth.
      participation: market.participation || null,
      earningsCount,
      highImpact,
      keyReason: engine.detailedReasoning || engine.shortSummary || market.tone_detail || 'Signals are mixed.',
      changes: Array.isArray(market.regime_changes) ? market.regime_changes : [],
      changeBaseline: !!market.regime_baseline,
      aggressiveTrigger: engine.outlookTriggers?.strengthens || 'Breadth and momentum improve while volatility cools.',
      defensiveTrigger: engine.outlookTriggers?.weakens || 'Breadth weakens and volatility/rates stress rises.',
      nextReview: engine.dataFreshness?.stale
        ? 'Data is degraded; review again when fresh inputs are restored.'
        : 'Next session close, or sooner on high-impact catalysts.',
      timeframe: engine.timeframe || market.tone_timeframe || 'Swing-to-position horizon (days to weeks)',
      basis: market.tone_basis || '',
      confidenceScore: Number(engine.confidenceScore) || null,
      dataFreshness: engine.dataFreshness || null,
      // Backend decides; the stale flag is the fallback for older payloads.
      guidanceWithheld: engine.guidanceWithheld != null ? !!engine.guidanceWithheld : !!engine.dataFreshness?.stale,
      // The market scan's own dated list, once it has arrived, outranks the
      // engine's flag: the engine may have been built before the scan finished.
      earningsKnown: (intel && !intel.loading && Array.isArray(intel.earnings_week) && intel.earnings_week.length > 0) || (engine.earningsCalendarKnown != null ? !!engine.earningsCalendarKnown : true),
      newsLive: engine.newsFeedsLive != null ? !!engine.newsFeedsLive : true,
      riskWarnings: Array.isArray(engine.riskWarnings) ? engine.riskWarnings : [],
      // [CANONICAL] Extra engine fields consumed across the dashboard so no
      // section invents its own market interpretation.
      headline: engine.marketHeadline || '',
      confidenceLabel: engine.confidenceLabel || null,
      confidenceReasons: Array.isArray(engine.confidenceReasons) ? engine.confidenceReasons : [],
      lowConfidence: !!engine.lowConfidence,
      actionBrief: engine.actionBrief || '',
      newsImpactSummary: engine.newsImpactSummary || '',
      newsImpact: engine.newsImpact || null,
      marketDrivers: Array.isArray(engine.marketDrivers) ? engine.marketDrivers : [],
      catalystRisks: Array.isArray(engine.catalystRisks) ? engine.catalystRisks : [],
      sectorLeadership: engine.sectorLeadership || null,
      sourceHealth: engine.sourceHealth || null,
      dataConfidence: Number(engine.dataConfidence) || null,
      overrides: Array.isArray(engine.overridesTriggered) ? engine.overridesTriggered : [],
      debug: engine.debug || null,
    };
    model.action = {
      primary: primary.primary || 'Keep risk measured and follow confirmed setups only.',
      sizing: primary.sizing || 'Measured position sizing',
      avoid: primary.avoid || 'Avoid forcing low-quality setups',
      risk: primary.risk || 'Use explicit stop-loss and invalidation rules',
    };
    model.exposure = {
      band: guidance.band || null,
      headline: guidance.headline || '',
      headlineTone: normalizeToneClass(guidance.headlineTone || postureTone),
      actions: Array.isArray(guidance.actions) ? guidance.actions : [],
    };
    return model;
  }

  const factors = Array.isArray(market.tone_factors) ? market.tone_factors : [];
  const net = factors.reduce((acc, f) => acc + (Number(f.score) || 0), 0);
  const count = factors.length;
  const bull = factors.filter((f) => Number(f.score) > 0).length;
  const bear = factors.filter((f) => Number(f.score) < 0).length;
  const flat = factors.filter((f) => Number(f.score) === 0).length;

  // [CONSERVATIVE FALLBACK] Without the backend regime engine, none of its
  // veto rules (VIX cap, bearish-news cap, stale-data cap…) can run. A
  // fallback read must therefore never print an offensive/risk-on call —
  // cap it and tell the user the read is running on limited data.
  let rung = regimeLadder(bull, bear, net);
  const engineMissing = true;
  if (rung.posture === 'Offensive' || rung.posture === 'Selective Offensive') {
    rung = {
      posture: 'Lean Long',
      signal: 'Neutral-to-Positive (Unverified)',
      label: 'Mildly Constructive — Limited Data',
      tone: 'neutral',
    };
  }
  const posture = rung.posture;
  const postureTone = normalizeToneClass(rung.tone);
  const modelSignal = rung.signal;
  const label = rung.label;

  // Factor breakdown relative to the call direction (what confirms / opposes).
  const dir = postureTone === 'bull' ? 1 : postureTone === 'bear' ? -1 : 0;
  const support = factors.filter((f) => dir !== 0 && Math.sign(Number(f.score)) === dir).map((f) => f.label || f.key);
  const oppose = factors.filter((f) => dir !== 0 && Math.sign(Number(f.score)) === -dir).map((f) => f.label || f.key);
  const neutral = factors.filter((f) => Number(f.score) === 0).map((f) => f.label || f.key);
  const breakdownParts = [];
  if (dir === 0) {
    if (bull) breakdownParts.push(`${factors.filter((f) => Number(f.score) > 0).map((f) => f.label).join(' & ')} positive`);
    if (bear) breakdownParts.push(`${factors.filter((f) => Number(f.score) < 0).map((f) => f.label).join(' & ')} negative`);
  } else {
    if (support.length) breakdownParts.push(`${support.join(' & ')} confirm`);
    if (oppose.length) breakdownParts.push(`${oppose.join(' & ')} oppose`);
    if (neutral.length) breakdownParts.push(`${neutral.join(' & ')} neutral`);
  }
  const breakdownText = breakdownParts.join(' · ');
  const alignment = { aligned: dir === 0 ? Math.max(bull, bear) : support.length, bull, bear, flat, count, support, oppose, neutral };

  const vix = firstUsable(market.vix_level);
  const tnx = firstUsable(market.ten_year_yield);
  // Real participation (% of equity indices above their 50-DMA) from backend;
  // fall back to same-day green count only if absent.
  let breadth = firstUsable(market.participation_pct);
  let breadthIsStructural = breadth != null;
  if (breadth == null) {
    const quotes = (market.quotes || []).filter((q) => {
      if (q.price == null) return false;
      const s = String(q.symbol || '').toUpperCase();
      const l = String(q.label || '').toLowerCase();
      return !/vix|tnx/i.test(s) && !s.includes('=')
        && !l.includes('yield') && !l.includes('gold') && !l.includes('oil') && !l.includes('bitcoin');
    });
    const greens = quotes.filter((q) => Number(q.change_pct) > 0).length;
    breadth = quotes.length ? Math.round((greens / quotes.length) * 100) : null;
  }
  const earningsCount = earningsAhead(intel);
  const highImpact = Object.values(news.groups || {})
    .flatMap((g) => (Array.isArray(g) ? g : []))
    .filter((n) => Number(n && n.impact || 0) >= 6).length;

  // Key reason — the factors actually driving the call.
  const driverNotes = (dir > 0
    ? factors.filter((f) => Number(f.score) > 0)
    : dir < 0
      ? factors.filter((f) => Number(f.score) < 0)
      : factors
  ).map((f) => f.note).filter(Boolean);
  const keyReason = driverNotes.length
    ? driverNotes.slice(0, 3).join('  ·  ')
    : (market.tone_detail || 'Signals are split across the model — wait for confirmation.');

  const changes = Array.isArray(market.regime_changes) ? market.regime_changes : [];
  const changeBaseline = !!market.regime_baseline;

  const model = {
    tone: market.tone || 'Neutral', posture, postureTone, modelSignal, label,
    alignment, breakdownText, net, count, factors,
    vix, tnx, breadth, breadthIsStructural, participation: market.participation || null,
    earningsCount, highImpact,
    keyReason, changes, changeBaseline,
    engineMissing,
    lowConfidence: true,
    confidenceScore: null,
    confidenceLabel: 'Low',
    confidenceReasons: [
      'Regime engine data unavailable — using the limited 4-factor fallback',
      'Risk-veto checks (volatility, news, stale data) could not run',
    ],
    aggressiveTrigger: `Lead index reclaims its 50-DMA, participation reaches ${mkt('PARTICIPATION_BROAD_PCT')}%, and VIX falls back under ${mkt('VIX_CONTAINED_MAX')}.`,
    defensiveTrigger: `Index loses its 200-DMA, participation falls to ${mkt('PARTICIPATION_WEAK_PCT')}%, or VIX reaches ${mkt('VIX_STRESS')}.`,
    nextReview: vix != null
      ? `Next session close — sooner if VIX crosses ${vix >= mkt('VIX_CALM_MAX') ? mkt('VIX_CALM_MAX') : mkt('VIX_STRESS')} or a high-impact print lands.`
      : 'Next session close, or sooner on a high-impact macro print.',
    timeframe: market.tone_timeframe || 'Swing-to-position horizon (days to weeks)',
    basis: market.tone_basis || '',
  };
  model.action = regimeAction(model);
  model.exposure = regimeExposure(model);
  // Unverified read → guidance can never look confidently green.
  model.exposure.headlineTone = model.postureTone === 'bear' ? 'bear' : 'warn';
  return model;
}

// [REGIME-COHERENT] Trading actions derived from the SAME posture the hero
// shows. VIX / earnings act as risk modifiers, never as a separate regime.
function regimeAction(model) {
  const p = model.posture;
  const vix = model.vix;
  const hotVix = vix != null && vix >= mkt('VIX_STRESS');
  const elevatedVix = vix != null && vix >= mkt('VIX_CALM_MAX');
  let a;
  if (p === 'Offensive') {
    a = { primary: 'Press leaders — add to confirmed breakouts on volume.', avoid: 'Paying up for late, extended names.', risk: 'Trail stops up as volatility compresses.', sizing: 'Full-to-normal size; keep single-name caps.' };
  } else if (p === 'Selective Offensive') {
    a = { primary: 'Stay selective — buy only confirmed breakouts with volume.', avoid: 'Chasing extended names and low-volume entries.', risk: 'Define stop and invalidation before entry.', sizing: 'Normal-to-moderate size; scale in, don\u2019t front-load.' };
  } else if (p === 'Lean Long') {
    a = { primary: 'Lean long, but demand clean reward-to-risk before adding.', avoid: 'Oversized bets on a not-yet-confirmed tape.', risk: 'Starter positions with room to add on confirmation.', sizing: 'Moderate size; keep dry powder.' };
  } else if (p === 'Lean Defensive') {
    a = { primary: 'Tighten up — trade only A-grade setups, keep size light.', avoid: 'Adding beta or averaging down on weak names.', risk: 'Honor stops; no new risk without confirmation.', sizing: 'Reduced size; raise the quality bar.' };
  } else if (p === 'Selective Defensive' || p === 'Defensive') {
    a = { primary: 'Preserve capital — no new initiations until risk cools.', avoid: 'Catching knives and averaging into losers.', risk: 'Cut weak longs; keep stops visible and tight.', sizing: 'Light exposure; larger cash buffer.' };
  } else {
    a = { primary: 'Stay selective — require multiple confirmations before risk.', avoid: 'Forcing trades from a mixed, low-conviction tape.', risk: 'Define stop and invalidation before entry.', sizing: 'Measured size; staged entries.' };
  }
  // Risk modifiers (do not flip the regime, just tighten the plan).
  if (hotVix) { a.risk = `VIX at or above ${mkt('VIX_STRESS')}: halve size, keep stops visible before entry.`; a.sizing = 'Light exposure until volatility cools.'; }
  else if (elevatedVix && (p === 'Offensive' || p === 'Selective Offensive')) { a.sizing = 'Normal-to-moderate size — trim for elevated VIX.'; }
  if (model.earningsCount > 0) { a.avoid = `${a.avoid} No oversized bets into ${model.earningsCount} upcoming earnings.`; }
  return a;
}

// [REGIME-COHERENT] Portfolio exposure that matches the posture exactly.
function regimeExposure(model) {
  const p = model.posture;
  if (p === 'Offensive') {
    return { band: '80–100% gross', headline: 'Add methodically · Press winners', headlineTone: 'bull', actions: [
      { tone: 'bull', text: 'Add to leaders on confirmed volume' }, { tone: 'bull', text: 'Trail stops higher on core' },
      { tone: 'warn', text: 'Keep one hedge ready if breadth fades' }, { tone: 'neutral', text: 'Cap single-name concentration' } ] };
  }
  if (p === 'Selective Offensive') {
    return { band: '60–80% gross', headline: 'Build selectively · Confirmation required', headlineTone: 'bull', actions: [
      { tone: 'bull', text: 'Add only on confirmed breakouts with volume' }, { tone: 'bull', text: 'Hold core; trail stops up' },
      { tone: 'warn', text: 'Keep dry powder for the opposing factor' }, { tone: 'neutral', text: 'No oversized single-name bets' } ] };
  }
  if (p === 'Lean Long') {
    return { band: '50–70% gross', headline: 'Lean long · Keep dry powder', headlineTone: 'bull', actions: [
      { tone: 'bull', text: 'Starter positions in A-grade setups' }, { tone: 'warn', text: 'Hold core; add only on confirmation' },
      { tone: 'neutral', text: 'Build a watchlist for the upgrade trigger' }, { tone: 'bear', text: 'Avoid chasing extended names' } ] };
  }
  if (p === 'Lean Defensive') {
    return { band: '40–55% gross', headline: 'Tighten up · Raise the bar', headlineTone: 'warn', actions: [
      { tone: 'warn', text: 'Trim weak or extended longs into strength' }, { tone: 'neutral', text: 'Hold only high-quality core' },
      { tone: 'neutral', text: 'Build a watchlist — do not initiate yet' }, { tone: 'bull', text: 'Add only above the upgrade trigger' } ] };
  }
  if (p === 'Selective Defensive' || p === 'Defensive') {
    return { band: '25–45% gross', headline: 'Reduce exposure · Capital preservation', headlineTone: 'bear', actions: [
      { tone: 'bear', text: 'Cut weak / extended longs first' }, { tone: 'warn', text: 'Hold only high-conviction core' },
      { tone: 'neutral', text: 'Hedge or raise cash buffer' }, { tone: 'bull', text: 'Re-engage only above the upgrade trigger' } ] };
  }
  return { band: '50–65% gross', headline: 'Stay selective · Confirm before adding', headlineTone: 'neutral', actions: [
    { tone: 'bull', text: 'Add only clean reward-to-risk setups' }, { tone: 'warn', text: 'Hold core; keep starters small' },
    { tone: 'neutral', text: 'Build a watchlist of A-setups' }, { tone: 'bear', text: 'Avoid chasing gaps and thin volume' } ] };
}


// [HERO v3] Map a factor score to a scannable state (Confirmed / Neutral / Warning).
function factorState(score) {
  const s = Number(score);
  if (s <= -2) return { cls: 'warn', label: 'Critical', pips: 0 };
  if (s > 0) return { cls: 'bull', label: 'Confirmed', pips: 3 };
  if (s < 0) return { cls: 'warn', label: 'Warning', pips: 1 };
  return { cls: 'neutral', label: 'Neutral', pips: 2 };
}


// [DEV DEBUG] Only-in-development transparency panel. Shows the raw market /
// news inputs, every factor score, the final regime + confidence, the override
// rules that fired, stale-data flags, source conflicts, and the exact UI copy
// the engine selected — so anyone can verify WHY a regime was shown.
function isDebugMode() {
  if (typeof window === 'undefined') return false;
  try {
    const qs = new URLSearchParams(window.location.search || '');
    if (qs.get('debug') === '1') return true;
    const host = (window.location.hostname || '').toLowerCase();
    if (host === 'localhost' || host === '127.0.0.1') {
      return window.localStorage && window.localStorage.getItem('quanta_debug') === '1';
    }
  } catch (_e) { /* non-browser / blocked storage */ }
  return false;
}

/* ===========================================================================
   OVERVIEW (DASHBOARD) RENDERERS
   ---------------------------------------------------------------------------
   Every section below writes into a .q-panel and uses one of six primitives:
   q-head, q-table, q-tabs, q-dl, q-ribbon, q-metrics. No nested cards, no
   decorative badges, no invented numbers — a field that the backend does not
   supply renders as an explicit N/A, never as a plausible-looking figure.
   ========================================================================= */

/* -- Shared table + cell helpers ------------------------------------------ */

/** Wrap a <table> so wide trading grids scroll inside the panel, never the page. */
function qTable(headHtml, bodyHtml, cls = '') {
  return `<div class="q-tablewrap"><table class="q-table ${cls}"><thead>${headHtml}</thead><tbody>${bodyHtml}</tbody></table></div>`;
}

/** Ticker + company name cell (the identity column of every securities table). */
function qTickerCell(ticker, name) {
  return `<div class="q-tick">
    <span class="q-tick-s">${escapeHtml(ticker || '—')}</span>
    ${name ? `<span class="q-tick-n">${escapeHtml(name)}</span>` : ''}
  </div>`;
}

/** Signed percentage with a direction glyph — never colour alone (§7). */
function qChangeCell(pct, dec = 2) {
  if (pct == null || pct === '') return '<span class="q-na">N/A</span>';
  const v = Number(pct);
  if (!Number.isFinite(v)) return '<span class="q-na">N/A</span>';
  const cls = v > 0 ? 'bull' : v < 0 ? 'bear' : 'flat';
  const glyph = v > 0 ? '▲' : v < 0 ? '▼' : '–';
  return `<span class="q-chg ${cls}">${glyph} ${fmtPct(v, dec, false).replace('+', '')}</span>`;
}

/** Status text with a tone dot. The word carries the meaning; colour reinforces. */
function qStatus(label, tone = 'neutral') {
  return `<span class="q-status-cell ${tone}">${escapeHtml(label)}</span>`;
}

const qNA = (v) => (v == null || v === '' ? '<span class="q-na">N/A</span>' : v);

/** Wire expandable detail rows for any .q-table that has .q-tr / .q-detail pairs. */
function wireExpandableRows(root) {
  $$('.q-tr[data-expand]', root).forEach((tr) => {
    const toggle = () => {
      const id = tr.dataset.expand;
      const detail = root.querySelector(`.q-detail[data-detail="${id}"]`);
      if (!detail) return;
      const open = tr.classList.toggle('is-open');
      detail.hidden = !open;
      tr.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    tr.addEventListener('click', (ev) => {
      if (ev.target.closest('a, button')) return;
      toggle();
    });
    tr.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
    });
  });
  $$('[data-ticker-open]', root).forEach((btn) => {
    btn.addEventListener('click', (ev) => { ev.stopPropagation(); openTickerTearSheet(btn.dataset.tickerOpen); });
    // A row (or other non-native element) given role="button" is focusable but
    // gets no Enter/Space activation from the browser; native buttons and links
    // already do, so only the former need it.
    if (!btn.matches('button, a[href], input, select, textarea, summary')) {
      btn.addEventListener('keydown', (ev) => {
        if (ev.target !== btn || (ev.key !== 'Enter' && ev.key !== ' ')) return;
        ev.preventDefault();
        ev.stopPropagation();
        openTickerTearSheet(btn.dataset.tickerOpen);
      });
    }
  });
  wireScrollTargets(root);
}

/** Wire [data-scroll-to] buttons. Idempotent: a root may be passed through
 *  both wireExpandableRows and attachDashboardActionHandlers. */
function wireScrollTargets(root = document) {
  $$('[data-scroll-to]', root).forEach((btn) => {
    if (btn.dataset.scrollWired === '1') return;
    btn.dataset.scrollWired = '1';
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.scrollTo);
      if (!target) return;
      const view = target.closest('.view');
      if (view && !view.classList.contains('active')) switchView(view.id.replace('view-', ''));
      // A target inside a tab workspace opens its tab first.
      if (target.getAttribute('role') === 'tabpanel' && target.hidden) {
        document.querySelector(`[role="tab"][aria-controls="${target.id}"]`)?.click();
      }
      target.scrollIntoView({ behavior: (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'), block: 'start' });
    });
  });
}

/* =========================================================================
   ROW 0 — TAPE. The high-impact drivers already feeding the regime engine,
   on one line. Never a second, hand-picked headline list.
   ========================================================================= */
// Feed titles occasionally arrive with a doubled possessive ("Iran's 's").
function cleanHeadline(t) {
  return String(t || '').replace(/(\w)(['\u2019]s)\s+['\u2019]s\b/g, '$1$2').replace(/\s{2,}/g, ' ').trim();
}

function renderTopHeadlines(market = {}, news = {}) {
  const el = $('#top-headlines');
  if (!el) return;
  const engine = market.market_regime_engine || {};
  let drivers = Array.isArray(engine.marketDrivers) ? engine.marketDrivers.slice() : [];

  if (!drivers.length) {
    const groups = (news && news.groups) || {};
    drivers = dedupeNewsItems([...(groups.big_news || []), ...(groups.latest || [])])
      .filter((n) => Number(n.impact || 0) >= mkt('NEWS_IMPACT_MARKET_MOVING'))
      .slice(0, 4)
      .map((n) => ({
        title: n.title || n.headline || '',
        category: n.category || 'Macro',
        direction: n.direction || 'neutral',
        impactLevel: Number(n.impact || 0) >= mkt('NEWS_IMPACT_HIGH') ? 'High' : 'Medium',
        url: n.url || '',
      }));
  }

  const { label: netLabel, tone: netTone } = newsTiltRead(engine);

  if (!drivers.length) {
    el.innerHTML = `<div class="q-tape-inner">
      <span class="q-tape-kicker"><span class="q-tape-pulse neutral"></span>Market-moving now</span>
      <span class="q-tape-empty">${engine.newsFeedsLive === false
        ? 'No news feed is reachable — headline risk is unknown, not quiet.'
        : 'Headline tape is quiet — no high-impact stories. Price action is in charge today.'}</span>
    </div>`;
    return;
  }

  const dirTone = (dir) => ({ bullish: 'bull', bearish: 'bear', mixed: 'warn' }[String(dir || '').toLowerCase()] || 'neutral');
  const arrow = (dir) => ({ bullish: QIco.up, bearish: QIco.down }[String(dir || '').toLowerCase()] || QIco.flat);

  // Every tape item is already market-moving, so a "High" on each says nothing:
  // the flag names the kind of story, and only a lesser impact is called out.
  const items = drivers.slice(0, 4).map((d) => {
    const tone = dirTone(d.direction);
    const impact = d.impactLevel || 'High';
    const kind = titleLabel(String(d.category || 'Market').replace(/[_-]+/g, ' '));
    const inner = `<span class="q-tape-arrow ${tone}">${arrow(d.direction)}</span>
      <span class="q-tape-text">${escapeHtml(cleanHeadline(d.title).slice(0, 110))}</span>
      <span class="q-tape-flag">${escapeHtml(impact === 'High' ? kind : `${kind}, ${String(impact).toLowerCase()}`)}</span>`;
    return safeUrl(d.url)
      ? `<a class="q-tape-item" href="${escapeHtml(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(d.title || '')}">${inner}</a>`
      : `<span class="q-tape-item" title="${escapeHtml(d.title || '')}">${inner}</span>`;
  }).join('');

  el.innerHTML = `<div class="q-tape-inner">
    <span class="q-tape-kicker"><span class="q-tape-pulse ${netTone}"></span>Market-moving now
      <span class="q-tape-net ${netTone}">${escapeHtml(netLabel)}</span></span>
    <div class="q-tape-list">${items}</div>
  </div>`;
}

/* =========================================================================
   ROW 1a — MARKET REGIME. Compact institutional market state: the call, the
   confidence, the four numbers behind it, the strategy line. Everything else
   is progressive disclosure.
   ========================================================================= */
function renderMarketRegime(market = {}, news = {}, intel = {}) {
  const el = $('#decision-brief');
  if (!el) return;
  const model = computeRegimeModel(market, news, intel);
  const tone = normalizeToneClass(model.postureTone);
  const conf = confidenceRead(model);

  el.classList.remove('bull', 'bear', 'neutral', 'warn');
  el.classList.add(tone);

  const regimeTitle = regimeName(market, model);
  const al = model.alignment || {};
  const summaryLine = model.count
    ? `${al.aligned} of ${model.count} factors ${model.posture === 'Neutral' ? 'lean each way' : 'agree'} on a ${String(model.modelSignal || '').toLowerCase()} read.`
    : 'Factor data has not loaded yet — guidance stays capped to a cautious stance until it does.';

  // Two measures, each named for what it answers. Index levels, VIX and
  // participation live in the ribbon and the participation panel only.
  const dir = directionScore(model);
  const against = (al.bear || 0);
  const tallyText = model.count ? `${al.bull || 0} support, ${against} against, ${(model.count || 0) - (al.bull || 0) - against} neutral` : 'factors not loaded';
  const lean = dir.score10 > 5 ? 'positive' : dir.score10 < 5 ? 'negative' : null;
  let howTheyCombine;
  if (conf.withheld || dir.shown === '--') howTheyCombine = 'Too few inputs were measured to score the read.';
  else if (!lean) howTheyCombine = model.lowConfidence ? 'The evidence leans neither way and the inputs disagree: no directional bet, keep size small.' : 'The evidence leans neither way: wait for a factor to break the tie.';
  else howTheyCombine = model.lowConfidence ? `The evidence leans ${lean}, but the inputs disagree, so the read is a lean: size small until confidence rises.` : `The evidence leans ${lean} and the inputs agree enough to act on it.`;

  // Capped / low-confidence disclosure, inline — one line, never a nested card.
  const reasons = (model.confidenceReasons || []).length
    ? model.confidenceReasons
    : ['Signals are weak or conflicting — the model reduced its conviction.'];
  // The gauge above already states the level and where it sits; this flag
  // exists to give the reason, so it leads with the reason.
  const lowConfFlag = model.lowConfidence
    ? `<div class="q-flag" role="status"><span><strong>Why it is low:</strong> ${escapeHtml(reasons[0])}</span></div>`
    : '';
  const overrides = Array.isArray(model.overrides) ? model.overrides : [];
  const overrideFlag = overrides.length
    ? `<div class="q-flag"><span><strong>Risk caps active.</strong> ${escapeHtml(overrides.slice(0, 2).join(' · '))}</span></div>`
    : '';

  const factorList = (model.factors || []).map((f) => {
    const s = Number(f.score) || 0;
    const w = s > 0 ? 'supports' : s < 0 ? 'opposes' : 'neutral';
    return `<li><b>${escapeHtml(f.label || f.key || '')}</b> (${w}): ${escapeHtml(f.note || '')}</li>`;
  }).join('');

  // The factors as one strip, strongest support first: the balance at a glance.
  const facs = (model.factors || []).slice().sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0));
  const strip = facs.length ? `<div class="q-fstrip" role="img" aria-label="${escapeHtml(`${tallyText}: ${facs.map((f) => `${f.label || f.key} ${Number(f.score) > 0 ? 'supports' : Number(f.score) < 0 ? 'against' : 'neutral'}`).join(', ')}`)}">${facs.map((f) => {
    const sc = Number(f.score) || 0;
    return `<i class="${sc >= 2 ? 's-bull2' : sc > 0 ? 's-bull' : sc <= -2 ? 's-bear2' : sc < 0 ? 's-bear' : 's-flat'}" title="${escapeHtml(`${f.label || f.key}: ${sc > 0 ? '+' : ''}${sc}`)}"></i>`;
  }).join('')}</div>` : '';
  const age = postureAge(market.regime_history, model.posture);
  const ageText = postureAgeText(age, model.posture);
  el.innerHTML = `
    <div class="q-regime-top">
      <div>
        <span class="q-kicker">Market regime</span>
        <h2 class="q-regime-title ${tone}">${escapeHtml(regimeTitle)}</h2>
      </div>
      ${ageText ? `<span class="q-rg-age${age && age.sessions === 1 && age.prev ? ' is-new' : ''}">${escapeHtml(ageText)}</span>` : ''}
    </div>
    <div class="q-measures q-measures--dial">
    <div class="q-measure q-measure--dir">
      <figure class="q-dial" aria-label="${escapeHtml(dir.shown === '--' ? 'Direction not scored' : `Direction ${dir.shown} of 100. ${fmtNumber(mkt('SIGNAL_SCORE_BEAR') * 10, 0)} or less reads bearish, ${fmtNumber(mkt('SIGNAL_SCORE_BULL') * 10, 0)} or more bullish.`)}" role="img">
        ${directionDial(dir)}
        <figcaption><span class="q-measure-v ${dir.tone}">${dir.shown}<small>/100</small></span><span class="q-conf-cap">Direction</span></figcaption>
      </figure>
    </div>
    <div class="q-rg-side">
    <div class="q-fbal"><span class="q-conf-cap">Factor balance</span>${strip}<span class="q-measure-s">${escapeHtml(tallyText)}</span></div>
    <div class="q-conf">
      <span class="q-conf-cap">Confidence</span>
      <span class="q-conf-num">${conf.withheld ? 'No read' : (conf.score != null ? conf.score + '%' : '—')}</span>
      ${conf.withheld ? '' : `<div class="q-gauge" role="img" aria-label="Model confidence ${conf.score != null ? conf.score : 'not scored'} out of 100. Moderate begins at ${mkt('CONFIDENCE_MODERATE')}, high at ${mkt('CONFIDENCE_HIGH')}.">
        <i class="q-gauge-fill ${conf.tone}" style="width:${conf.score != null ? Math.max(1, Math.min(100, conf.score)) : 0}%"></i>
        <b class="q-gauge-tick" style="left:${mkt('CONFIDENCE_MODERATE')}%" data-at="${mkt('CONFIDENCE_MODERATE')}"></b>
        <b class="q-gauge-tick" style="left:${mkt('CONFIDENCE_HIGH')}%" data-at="${mkt('CONFIDENCE_HIGH')}"></b>
      </div>`}
      <p class="q-conf-band">${confidenceBand(conf.score, conf.label, conf.withheld)}</p>
      ${model.lowConfidence ? `<p class="q-regime-why">Confidence is low: ${escapeHtml(reasons[0])}</p>` : ''}
    </div>
    </div>
    </div>
    <p class="q-measure-note">${escapeHtml(howTheyCombine)}</p>
    ${overrideFlag}
    <button type="button" class="q-textlink" data-jump="signal-check">See the ${model.count || ''} factors behind the call</button>`;
  void lowConfFlag; void summaryLine; void factorList;
  animateRegime(el, dir, conf, facs.length);
  const jump = el.querySelector('[data-jump]');
  if (jump) jump.addEventListener('click', () => {
    const target = document.getElementById(jump.dataset.jump);
    const tab = document.getElementById('tab-signal-check');
    if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
    if (target) target.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  });
}

// Motion: only in a browser that animates, never under reduced motion, and only
// when the value it shows has changed since the last render (the dashboard
// re-renders on every refresh; an unchanged reading must not replay).
const motionOK = () => typeof window !== 'undefined' && typeof Element !== 'undefined' && !!Element.prototype.animate
  && !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
const EASE_IN_OUT = 'cubic-bezier(0.77, 0, 0.175, 1)';
function motionChanged(key, value) {
  state.motionSeen = state.motionSeen || {};
  const prev = state.motionSeen[key];
  state.motionSeen[key] = value;
  return { changed: prev !== value, prev };
}

// The seven postures from most defensive to most offensive.
const POSTURE_LADDER = ['Defensive', 'Selective Defensive', 'Lean Defensive', 'Neutral', 'Lean Long', 'Selective Offensive', 'Offensive'];
const RISK_LEVELS = ['Contained', 'Moderate', 'Elevated', 'High'];

// [PURE] How long the current posture has held, from the recorded sessions.
function postureAge(history = [], posture) {
  const rows = (Array.isArray(history) ? history : []).filter((h) => h && h.posture && h.date);
  if (!rows.length || !posture) return null;
  let n = 0;
  for (let i = rows.length - 1; i >= 0 && rows[i].posture === posture; i -= 1) n += 1;
  if (!n) return null;
  return { sessions: n, since: rows[rows.length - n].date, prev: rows.length > n ? rows[rows.length - n - 1].posture : null, all: n === rows.length };
}
function postureAgeText(age, posture) {
  if (!age) return '';
  const day = (ymd) => { const t = Date.parse(`${ymd}T12:00:00Z`); return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : ymd; };
  if (age.sessions === 1 && age.prev) return `${posture} since today, from ${age.prev}`;
  if (age.all) return `${posture} since records began, ${day(age.since)}`;
  return `${posture} since ${day(age.since)}, ${age.sessions} sessions`;
}

// [PURE] The direction dial: a half-circle 0-100 with the bearish and bullish
// zones from MARKET config, and a needle the renderer turns to the score.
function directionDial(dir) {
  const lo = mkt('SIGNAL_SCORE_BEAR') * 10;
  const hi = mkt('SIGNAL_SCORE_BULL') * 10;
  if (dir.shown === '--' || !Number.isFinite(lo) || !Number.isFinite(hi)) return '';
  const at = Math.max(0, Math.min(100, Math.round(dir.score10 * 10)));
  const pt = (v, r = 50) => { const a = Math.PI - (v / 100) * Math.PI; return `${(60 + r * Math.cos(a)).toFixed(2)} ${(60 - r * Math.sin(a)).toFixed(2)}`; };
  const arc = (a, b, cls) => `<path class="${cls}" d="M ${pt(a)} A 50 50 0 0 1 ${pt(b)}"/>`;
  const tick = (v) => `<path class="q-dial-tick" d="M ${pt(v, 43)} L ${pt(v, 57)}"/>`;
  return `<svg class="q-dial-svg" viewBox="0 0 120 66" aria-hidden="true" focusable="false">
      ${arc(0, lo - 0.6, 'z-bear')}${arc(lo + 0.6, hi - 0.6, 'z-mid')}${arc(hi + 0.6, 100, 'z-bull')}
      ${tick(lo)}${tick(hi)}
      <g class="q-dial-needle" data-at="${at}" style="transform:rotate(${(at * 1.8).toFixed(1)}deg)"><path d="M 60 60 L 16 60"/><circle cx="60" cy="60" r="4.2"/></g>
    </svg>`;
}

// The needle turns from the last reading to this one (from the bearish end on
// the first view); the confidence fill and the factor strip draw in once.
function animateRegime(el, dir, conf, nFactors) {
  if (!motionOK()) return;
  const needle = el.querySelector('.q-dial-needle');
  const at = needle ? Number(needle.dataset.at) : null;
  const d = motionChanged('regime-dir', at);
  if (needle && d.changed) {
    const from = d.prev == null ? 0 : d.prev;
    needle.animate([{ transform: `rotate(${(from * 1.8).toFixed(1)}deg)` }, { transform: `rotate(${(at * 1.8).toFixed(1)}deg)` }],
      { duration: d.prev == null ? 900 : 600, easing: d.prev == null ? EASE_OUT : EASE_IN_OUT });
  }
  const c = motionChanged('regime-conf', conf.score);
  const fill = el.querySelector('.q-gauge-fill');
  if (fill && c.changed) fill.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 520, easing: EASE_OUT, delay: 120 });
  const f = motionChanged('regime-strip', `${nFactors}:${(el.querySelector('.q-fstrip') || {}).innerHTML || ''}`);
  if (f.changed) [...el.querySelectorAll('.q-fstrip i')].forEach((seg, i) => seg.animate(
    [{ opacity: 0, transform: 'scaleY(0.3)' }, { opacity: 1, transform: 'scaleY(1)' }], { duration: 260, easing: EASE_OUT, delay: 200 + i * 35, fill: 'backwards' }));
}

// [PURE] The seven postures as one scale, Defensive to Offensive, with today's
// stance marked and each step's base gross band in its tooltip.
function postureScale(posture, bandText) {
  const idx = POSTURE_LADDER.indexOf(posture);
  if (idx < 0) return '';
  const tone = ['bear-3', 'bear-2', 'bear-1', 'flat', 'bull-1', 'bull-2', 'bull-3'];
  return `<div class="q-ladder" role="img" aria-label="${escapeHtml(`Stance ${posture}: step ${idx + 1} of 7 from Defensive to Offensive. Gross ${bandText}.`)}">
      <div class="q-ladder-row">${POSTURE_LADDER.map((name, i) => `<i class="t-${tone[i]}${i === idx ? ' is-on' : ''}" title="${escapeHtml(`${name}: ${regimeExposure({ posture: name }).band} base`)}"></i>`).join('')}</div>
      <b class="q-ladder-mk" data-idx="${idx}" style="left:${(((idx + 0.5) / 7) * 100).toFixed(2)}%"></b>
      <div class="q-ladder-ends" aria-hidden="true"><span>Defensive</span><span>Offensive</span></div>
    </div>`;
}

// [PURE] Where the direction score sits against the bearish and bullish cut-offs.
function dirZone(dir) {
  const lo = mkt('SIGNAL_SCORE_BEAR') * 10;
  const hi = mkt('SIGNAL_SCORE_BULL') * 10;
  if (dir.shown === '--' || !Number.isFinite(lo) || !Number.isFinite(hi)) return '';
  const at = Math.max(0, Math.min(100, Math.round(dir.score10 * 10)));
  return `<div class="q-zone" role="img" aria-label="${escapeHtml(`Direction ${at} of 100. ${fmtNumber(lo, 0)} or less reads bearish, ${fmtNumber(hi, 0)} or more bullish.`)}">
      <i class="z-bear" style="width:${lo}%"></i><i class="z-bull" style="left:${hi}%;width:${100 - hi}%"></i><b style="left:${at}%"></b>
      <span class="q-zone-t" style="left:${lo}%">${fmtNumber(lo, 0)}</span><span class="q-zone-t" style="left:${hi}%">${fmtNumber(hi, 0)}</span>
    </div>`;
}

/* =========================================================================
   ROW 1b — TODAY'S PLAYBOOK. Stance, exposure, sizing, what to prefer and
   what to avoid — one label→value list, no pills.
   ========================================================================= */
function renderPlaybook(market = {}, news = {}, intel = {}) {
  const body = $('#allocator-body');
  if (!body) return;
  const model = computeRegimeModel(market, news, intel);
  const exp = model.exposure;
  const act = model.action;
  const sb = strategistBrief(model);
  const meta = $('#playbook-meta');
  if (model.guidanceWithheld) {
    // No exposure band, sizing or preference is derived from missing inputs.
    const missing = (model.dataFreshness?.unmeasuredFactors || []).map((k) => String(k).replace(/_/g, ' '));
    if (meta) { meta.textContent = 'Withheld'; meta.className = 'q-head-meta neutral'; }
    body.innerHTML = `<div class="q-stack" data-guidance="withheld">
      <div class="q-lede">
        <span class="q-lede-v neutral">No playbook</span>
        <span class="q-lede-k">market data incomplete</span>
      </div>
      <p class="q-note-block">The regime read rests on too few measured inputs to set exposure or sizing${missing.length ? ` (unmeasured: ${escapeHtml(missing.slice(0, 5).join(', '))})` : ''}. Stock-level Decisions still carry their own data checks.</p>
    </div>`;
    return;
  }
  // The engine publishes the band; with none, the page says so rather than inventing one.
  const bandText = exp.band || 'No band published';
  if (meta) { meta.textContent = bandText; meta.className = `q-head-meta ${exp.headlineTone}`; }
  // A low-confidence, stale or conflicted read caps the posture's own band. Say
  // so, with the band it replaced: "Lean Long" beside 30-55% read as a contradiction.
  const norm = (b) => String(b || '').replace(/[\u2013\u2014]/g, '-').replace(/\s+/g, ' ').trim();
  const baseBand = regimeExposure({ posture: model.posture }).band;
  const capped = exp.band && baseBand && norm(exp.band) !== norm(baseBand);
  const capWhy = model.dataFreshness?.stale ? 'while data is stale' : model.lowConfidence ? 'while confidence is low' : 'while signals conflict';
  const capNote = capped ? `<span class="q-dl-note">Capped from ${escapeHtml(norm(baseBand))} ${capWhy}.</span>` : '';

  body.innerHTML = `<div class="q-stack">
    <div class="q-lede">
      <span class="q-lede-v ${exp.headlineTone}">${escapeHtml(model.posture)}</span>
      <span class="q-lede-k">portfolio stance</span>
    </div>
    ${postureScale(model.posture, bandText)}
    ${capNote ? `<p class="q-cap-note">${capNote}</p>` : ''}
    <dl class="q-dl">
      <dt>Sizing</dt><dd>${escapeHtml(act.sizing)}</dd>
      <dt>Prefer</dt><dd>${escapeHtml(sb.favored.text)}</dd>
      <dt>Avoid</dt><dd>${escapeHtml(act.avoid)}</dd>
      <dt>Risk rule</dt><dd>${escapeHtml(act.risk)}</dd>
    </dl>
  </div>`;
  const mk = body.querySelector('.q-ladder-mk');
  if (mk && motionOK()) {
    const idx = Number(mk.dataset.idx);
    const m = motionChanged('posture-idx', idx);
    const row = body.querySelector('.q-ladder-row');
    if (m.changed && m.prev != null && row) {
      const step = row.getBoundingClientRect().width / 7;
      mk.animate([{ transform: `translateX(${(m.prev - idx) * step}px)` }, { transform: 'translateX(0)' }], { duration: 600, easing: EASE_IN_OUT });
    } else if (m.changed) {
      mk.animate([{ opacity: 0, transform: 'translateY(-6px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 320, easing: EASE_OUT, delay: 250 });
    }
  }
}

/* =========================================================================
   ROW 1c — RISK. Visible before execution (§13.11). Level, the live numbers
   behind it, then the one risk that matters and both outlook triggers.
   ========================================================================= */
function renderRiskPanel(market = {}, news = {}, intel = {}) {
  const body = $('#risk-body');
  if (!body) return;
  const model = computeRegimeModel(market, news, intel);
  const sb = strategistBrief(model);
  const vix = model.vix;
  const newsGroups = news && news.groups;
  const hi = newsGroups ? highImpactNews(newsGroups).length
    : ((model.newsImpact && model.newsImpact.highImpactCount != null) ? model.newsImpact.highImpactCount : model.highImpact);

  // Explicit, rule-based level so "risk" never reads as a price direction (§13.7).
  // Every reason that raised the level is listed, and a calm VIX is named as
  // calm: "Elevated" beside a VIX of 14 otherwise reads as a contradiction.
  const vixStress = vix != null && vix >= mkt('VIX_STRESS');
  const vixAbove = vix != null && vix >= mkt('VIX_CALM_MAX');
  const newsHeavy = (hi || 0) >= mkt('HIGH_IMPACT_NEWS_WARN');
  const defensive = model.posture === 'Defensive' || model.posture === 'Selective Defensive';
  const reasons = [];
  let level = 'Moderate', tone = 'warn';
  if (model.guidanceWithheld) {
    level = 'Unknown'; tone = 'neutral';
  } else if (vixStress || defensive) {
    level = 'High'; tone = 'bear';
  } else if (vixAbove || model.lowConfidence || newsHeavy) {
    level = 'Elevated'; tone = 'warn';
  } else if (vix != null && vix < mkt('VIX_CONTAINED_MAX') && model.postureTone === 'bull') {
    level = 'Contained'; tone = 'bull';
  }
  if (vixStress) reasons.push(`VIX ${fmtNumber(vix, 1)} is in the stress band, so ranges stop normal size on noise`);
  else if (vixAbove) reasons.push(`VIX ${fmtNumber(vix, 1)} is above the calm band`);
  if (defensive) reasons.push('the model is defensive');
  if (model.lowConfidence) reasons.push(`model confidence is low${model.confidenceScore != null ? ` (${model.confidenceScore}%)` : ''}`);
  if (newsHeavy) reasons.push(`${hi} high-impact headlines are live`);
  const calm = vix != null && !vixAbove ? `Volatility itself is calm (VIX ${fmtNumber(vix, 1)}).` : '';
  let why;
  if (model.guidanceWithheld) why = 'Market inputs are missing, so the risk level cannot be measured. Unknown is not the same as calm.';
  else if (reasons.length) { const r = reasons.join('; '); why = `${r.charAt(0).toUpperCase()}${r.slice(1)}.${calm ? ` ${calm}` : ''}`; }
  else if (level === 'Contained') why = `VIX ${fmtNumber(vix, 1)} is calm and the factor model is aligned.`;
  else why = 'Standard conditions: normal risk controls apply.';

  const meta = $('#risk-meta');
  if (meta) { meta.textContent = level; meta.className = `q-head-meta ${tone}`; }

  // Event counts only: VIX and participation are in the ribbon and the
  // participation panel. Each count names its universe.
  const cell = (k, v, cls = '', sub = '') => `<div class="q-metric"><span class="q-metric-k">${escapeHtml(k)}</span><span class="q-metric-v ${cls}">${v}</span>${sub ? `<span class="q-metric-s">${escapeHtml(sub)}</span>` : ''}</div>`;
  const metrics = [
    cell('Earnings', model.earningsKnown ? String(model.earningsCount || 0) : '<span class="q-na">N/A</span>', '', earningsScopeShort(state.dashboard && state.dashboard.market_intel)),
    cell('High-impact news', model.newsLive ? String(hi || 0) : '<span class="q-na">N/A</span>', '', 'headlines live now'),
  ].join('');

  const mainRisk = model.guidanceWithheld
    ? 'Acting on a market read built from missing data.'
    : (sb.riskHidden.text || (model.riskWarnings && model.riskWarnings[0]) || model.action.avoid);

  // The level is stated once, in the panel head; the body gives its reasons.
  const li = RISK_LEVELS.indexOf(level);
  const meter = li < 0 ? '' : `<div class="q-riskm" role="img" aria-label="${escapeHtml(`Risk ${level}: level ${li + 1} of 4, Contained to High.`)}">
      <div class="q-riskm-row">${RISK_LEVELS.map((name, i) => `<i class="r-${i}${i <= li ? ' is-on' : ''}" title="${name}"></i>`).join('')}</div>
      <div class="q-riskm-ends" aria-hidden="true"><span>Contained</span><span>High</span></div>
    </div>`;
  body.innerHTML = `<div class="q-stack">
    ${meter}
    <p class="q-note-block">${escapeHtml(why)}</p>
    <div class="q-metrics">${metrics}</div>
    <div class="q-note-block"><b>Main risk</b>${escapeHtml(mainRisk)}</div>
  </div>`;
  if (li >= 0 && motionOK() && motionChanged('risk-level', li).changed) {
    [...body.querySelectorAll('.q-riskm i.is-on')].forEach((seg, i) => seg.animate(
      [{ opacity: 0.15, transform: 'scaleX(0.6)' }, { opacity: 1, transform: 'scaleX(1)' }], { duration: 240, easing: EASE_OUT, delay: 150 + i * 70, fill: 'backwards' }));
  }
}

/* =========================================================================
   ROW 2 — MARKET RIBBON. One row, one surface: indices, volatility, rates,
   commodities, crypto, then breadth. Replaces the tile grid entirely.
   ========================================================================= */
function renderMarketPulse(market = {}) {
  const el = $('#market-pulse');
  if (!el) return;
  const quotes = (market.quotes || []).filter((q) => q.price != null && Number.isFinite(Number(q.price)) && Number(q.price) > 0);
  if (!quotes.length) {
    el.innerHTML = '<div class="q-ribbon-empty">Market tape unavailable — <button class="q-btn q-btn--link" type="button" data-retry="dashboard">Retry</button></div>';
    return;
  }

  // The session most quotes share is said once, under the row; a tile names
  // its own session only when it differs (crypto, a currency that closed later).
  const sessionOf = (q) => (isCryptoSymbol(q.symbol, q.label) ? '24-hour market' : quoteFreshness(q).session.replace(/ · regular session only$/, ''));
  const tally = {};
  quotes.forEach((q) => { const k = sessionOf(q); tally[k] = (tally[k] || 0) + 1; });
  const common = Object.entries(tally).sort((a, b) => b[1] - a[1])[0][0];
  const cells = quotes.map((q) => {
    const sym = String(q.symbol || '');
    const ch = Number(q.change_pct);
    const hasCh = q.change_pct != null && Number.isFinite(ch);
    // VIX is read inversely (a fall supports risk); the glyph keeps the real direction.
    const inverse = isVolIndex(sym);
    const sign = hasCh ? (ch > 0 ? 1 : ch < 0 ? -1 : 0) : 0;
    const dir = !sign ? 'flat' : ((sign > 0) !== inverse ? 'bull' : 'bear');
    const glyph = hasCh ? (ch > 0 ? '▲' : ch < 0 ? '▼' : '–') : '·';
    const qf = quoteFreshness(q);
    // Yields move in basis points: +0.25% of a 5.24% yield is about +1.3 bp.
    const bp = isYield(sym, q.label) && hasCh ? yieldChangeBp(Number(q.price), ch) : null;
    const chText = !hasCh ? 'N/A' : bp != null ? `${bp >= 0 ? '+' : '-'}${Math.abs(bp).toFixed(1)} bp` : fmtPct(ch, 2, false).replace('+', '');
    // The session line names when the price was set; "regular session only" is
    // said once, in the note under the ribbon. Crypto has no session close.
    const sessionText = isCryptoSymbol(sym, q.label) ? '24-hour market' : qf.session.replace(/ · regular session only$/, '');
    const ticker = normalizeTicker(sym);
    const isIndex = sym.startsWith('^') || sym.includes('=');
    const canAnalyze = isAnalyzableTicker(ticker) && !isIndex;
    // An ETF standing in for an index trades at the ETF's price, not the
    // index level ("S&P 500" at 767 is SPY). Name the instrument actually quoted.
    const proxyTag = (!isIndex && q.label && sym && q.label.toUpperCase() !== sym.toUpperCase()
      && !sym.includes('-'))
      ? ` <span class="q-rb-proxy" title="Quoted via the ${escapeHtml(sym)} ETF">${escapeHtml(sym)}</span>` : '';
    const tag = canAnalyze ? 'button' : 'div';
    const attrs = canAnalyze
      ? `type="button" class="q-rb is-clickable" data-ticker="${escapeHtml(ticker)}" title="Open the ${escapeHtml(ticker)} tear sheet · ${escapeHtml(qf.text)}"`
      : `class="q-rb" title="${escapeHtml(qf.text)}"`;
    return `<${tag} ${attrs}>
      <span class="q-rb-k">${escapeHtml(q.label || sym)}${proxyTag}</span>
      <span class="q-rb-v">${fmtNumber(q.price, q.price >= 1000 ? 0 : 2)}</span>
      <span class="q-rb-c ${dir}"${inverse ? ' title="Lower volatility supports risk"' : ''}>${glyph} ${chText}</span>
      ${q.stale ? '<span class="q-rb-read">Stale quote</span>' : ''}
      <span class="q-rb-time${sessionText === common ? ' is-common' : ''}" title="${escapeHtml(qf.text || qf.session)}">${escapeHtml(sessionText)}</span>
    </${tag}>`;
  }).join('');

  // Prices only. Index participation is not a price: it lives in the
  // Participation & movers panel, once.

  // One line for the whole ribbon: session time and retrieval time are different
  // facts, and "updated just now" is only the second one.
  const ribbonFresh = quoteFreshness(quotes[0]);
  const note = `<p class="q-ribbon-note">Prices come from daily bars and can be delayed. ${escapeHtml(ribbonFresh.text)}. The topbar \u201cupdated\u201d time is when this page was fetched, not when the exchange traded.</p>`;
  el.innerHTML = `<div class="q-ribbon-scroll">${cells}</div>${note}`;
  $$('.q-rb.is-clickable', el).forEach((n) => n.addEventListener('click', () => openTickerTearSheet(n.dataset.ticker)));
}

// Overview price chart: the symbol the reader picked for this market, else the
// first symbol of the market ribbon. Quick picks are the ribbon's own symbols.
function renderOverviewChart(market = {}) {
  const body = $('#ovc-body');
  if (!body || typeof window === 'undefined' || !window.QuantaLiveChart) return;
  const quotes = (market.quotes || []).filter((q) => q.symbol);
  const mkt = String(market.market || state.market || 'US');
  const key = `quanta_overview_chart_${mkt}`;
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) { saved = null; }
  const symbol = String(saved || (quotes[0] && quotes[0].symbol) || '').toUpperCase();
  const chips = $('#ovc-chips');
  const nameEl = $('#ovc-name');
  const openEl = $('#ovc-open');
  const mark = (sym) => {
    $$('.ovc-chip', chips).forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.symbol === sym)));
    const t = normalizeTicker(sym);
    const can = isAnalyzableTicker(t) && !sym.startsWith('^') && !sym.includes('=');
    if (openEl) { openEl.hidden = !can; openEl.dataset.ticker = t; }
  };
  if (chips) {
    chips.innerHTML = quotes.slice(0, 8).map((q) => `<button type="button" class="ovc-chip" data-symbol="${escapeHtml(String(q.symbol).toUpperCase())}" aria-pressed="false" title="${escapeHtml(q.label || q.symbol)}">${escapeHtml(q.label || q.symbol)}</button>`).join('');
  }
  const pick = (sym) => {
    const clean = String(sym || '').trim().toUpperCase();
    if (!clean || !/^[A-Z0-9^.=&-]{1,32}$/.test(clean)) return;
    try { localStorage.setItem(key, clean); } catch (_) { /* private mode */ }
    mark(clean);
    if (state.overviewChart && state.overviewChart.host && state.overviewChart.host.isConnected) state.overviewChart.setSymbol(clean);
  };
  if (!symbol) { body.innerHTML = '<p class="lc-error">No market symbols to chart yet.</p>'; return; }
  // Listeners are wired once; they call the current market's pick.
  state.overviewChartPick = pick;
  if (!state.overviewChart || state.overviewChart.host !== body || state.overviewChartMarket !== mkt) {
    state.overviewChartMarket = mkt;
    state.overviewChart = window.QuantaLiveChart.mount(body, {
      symbol, context: 'overview', showQuote: true,
      onData: (d) => { if (nameEl) nameEl.textContent = `${d.name || d.symbol}${d.name && d.name !== d.symbol ? ` (${d.symbol})` : ''}${d.exchange ? ` · ${d.exchange}` : ''}`; },
    });
  }
  mark(state.overviewChart.symbol || symbol);
  if (!body.dataset.wired) {
    body.dataset.wired = '1';
    chips && chips.addEventListener('click', (ev) => { const b = ev.target.closest('.ovc-chip'); if (b) state.overviewChartPick(b.dataset.symbol); });
    const form = $('#ovc-form');
    form && form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const input = $('#ovc-ticker');
      state.overviewChartPick(input && input.value);
      if (input) input.value = '';
    });
    openEl && openEl.addEventListener('click', (ev) => { ev.preventDefault(); openTickerTearSheet(openEl.dataset.ticker); });
  }
}

/* =========================================================================
   ROW 3 — TOP OPPORTUNITIES. The execution core and the page's primary focal
   point. Full trade plan when a scan has run; honest analyst-consensus
   candidates (with the missing levels named as N/A) when one has not.
   ========================================================================= */
// Overview's opportunity summary: the first three rows of what Discover shows,
// from the same sources and with the same labels (the Decision for scan picks,
// the Street's rating for research candidates). It computes nothing new.
// Upside to a price target is an estimate, not a move the market made, so it
// is labelled and set in the model/estimate colour, never market green. An
// unlabelled green "+73.6%" read as a gain the stock had already made.
// Number() turns null/''/undefined into 0, which prints a measurement that was
// never made ("0.0%"). Missing stays missing.
function numOrNull(v) {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Street rating label; an absent rating is "Rating unavailable", never a Buy.
function streetRating(rec) {
  if (typeof rec === 'number' && Number.isFinite(rec)) return rec <= 1.5 ? 'Strong buy' : (rec <= 2.5 ? 'Buy' : 'Hold');
  const text = String(rec == null ? '' : rec).trim();
  if (!text || /^(none|null|n\/a|na|--|—)$/i.test(text)) return 'Rating unavailable';
  return titleLabel(text.replace(/[_-]+/g, ' '));
}

function upsideCell(up, caption, title) {
  if (up == null || !Number.isFinite(up)) return '<span class="q-na">N/A</span>';
  return `<span title="${escapeHtml(title)}">${fmtPct(up, 1, false)}</span><small>${escapeHtml(caption)}</small>`;
}

/** The Picks result the rest of the app may treat as current: a scan restored
 *  from an earlier session stays on the Picks page (marked as saved) and is
 *  never presented as today's book on Overview, Discover or Monitor. */
function livePicks() {
  return state.picksSavedAt ? null : state.picks;
}

function renderOpportunityBrief(intel = {}) {
  const body = $('#opportunity-brief-body');
  if (!body) return;
  const sub = $('#oppbrief-sub');
  const picks = (livePicks()?.recommendations || []).filter(isFundedPick);
  const sym = livePicks()?.currency || _currencySymbol();
  const row = (ticker, name, right, rightCls, note) => `
    <li class="q-oppbrief-row">
      <button type="button" class="q-oppbrief-tick ticker-action" role="button" data-ticker="${escapeHtml(ticker)}" aria-label="Open ${escapeHtml(ticker)} tear sheet">
        <b>${escapeHtml(ticker)}</b><span>${escapeHtml(companyName(name) || '')}</span>
      </button>
      <span class="q-oppbrief-note">${note}</span>
      <span class="q-oppbrief-v ${rightCls}">${right}</span>
    </li>`;
  let html = '';
  if (picks.length) {
    if (sub) sub.textContent = `${picks.length} scan-qualified · QUANTA Decisions`;
    html = picks.slice(0, 3).map((rec) => {
      const t = normalizeTicker(rec.ticker || rec.symbol || '');
      const am = actionMeta(rec);
      const up = upsidePct(firstUsable(rec.price_target, rec.target_price), firstUsable(rec.entry_price, rec.current_price));
      return row(t, rec.company || rec.name, upsideCell(up, 'to target', "Upside from entry to QUANTA's target"), 'quant',
        `<span class="q-status-cell ${am.tone}"${am.qualifier ? ` title="${escapeHtml(am.qualifier)}"` : ''}>${escapeHtml(am.label)}</span>${am.qualifier ? modelFlagHtml(rec.decision.evidence || rec.evidence) : ''}`);
    }).join('');
  } else if ((intel.analyst_picks || []).length) {
    const recs = livePicks()?.recommendations || [];
    if (sub) sub.textContent = livePicks()
      ? `No Buy or Accumulate call (${recs.length} Watchlist) · Street research candidates shown, not QUANTA setups`
      : 'Analyst picks, not QUANTA setups until a scan runs';
    const gapCheck = mkt('TARGET_GAP_CHECK_PCT');
    const wideGap = (p) => { const u = numOrNull(p.upside_pct); return u != null && Number.isFinite(gapCheck) && u >= gapCheck; };
    const ranked = [...intel.analyst_picks.filter((p) => !wideGap(p)), ...intel.analyst_picks.filter(wideGap)];
    const shown = ranked.slice(0, 5);
    const topUp = Math.max(...shown.map((p) => Math.abs(numOrNull(p.upside_pct) || 0)), 1);
    html = shown.map((p) => {
      const t = normalizeTicker(p.symbol || '');
      const up = numOrNull(p.upside_pct);
      const rating = streetRating(p.recommendation);
      const price = numOrNull(p.price);
      const target = numOrNull(p.target_mean);
      const n = numOrNull(p.analyst_count);
      // The evidence behind the number: price, mean target, how many analysts.
      // A very wide gap is usually a stale target or a price that fell away.
      const wide = up != null && up >= mkt('TARGET_GAP_CHECK_PCT');
      const evidence = [price != null && target != null ? `${sym}${fmtNumber(price, 2)} → ${sym}${fmtNumber(target, 2)}` : null,
        n != null ? `${n} analyst${n === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ');
      void evidence;
      return row(t, p.name, `${upsideCell(up, wide ? 'check: wide gap' : 'to Street target', wide ? 'A gap this wide is often a stale target or a price that fell away from it; check before relying on it.' : "Upside from today's price to the analysts' mean price target")}${up == null ? '' : `<span class="q-upbar" aria-hidden="true"><i style="--w:${Math.min(1, Math.abs(up) / topUp).toFixed(3)}"></i></span>`}`, wide ? 'warn' : '',
        `<span class="q-oppbrief-ev">${rating === 'Rating unavailable' ? rating : `Street ${escapeHtml(rating)}`}</span>${price != null && target != null ? `<span class="q-oppbrief-ev">${escapeHtml(`${sym}${fmtNumber(price, 2)} → ${sym}${fmtNumber(target, 2)}`)}</span>` : ''}${n != null ? `<span class="q-oppbrief-ev">${n} analyst${n === 1 ? '' : 's'}</span>` : ''}`);
    }).join('');
  } else if (intel.loading) {
    return;
  } else {
    if (sub) sub.textContent = 'Nothing ranked yet.';
    body.innerHTML = `<div class="q-state q-state--inline"><strong class="q-state-title">No ranked ideas this session</strong>
      <p class="q-state-body">Run a Picks scan to rank the universe under your mandate.</p>
      <div class="q-state-actions"><a class="q-btn q-btn--primary" href="#picks" data-view-link="picks">Run a scan</a></div></div>`;
    return;
  }
  body.innerHTML = `<ol class="q-oppbrief">${html}</ol>`;
  growOnView(body, '.q-upbar i', 'opp-bars', html.length);
  body.querySelectorAll('.q-oppbrief-tick').forEach((b) => b.addEventListener('click', () => openTickerTearSheet(b.dataset.ticker)));
}

// Wording for "why are QUANTA levels missing": never claim no scan ran when one did.
function scanRanClause() {
  if (!livePicks()) return 'no Picks scan has run this session';
  return 'the last Picks scan did not fund this name';
}

// One scan button at a time: when the card body offers its own (the empty
// state), the header's button steps aside.
function renderTopOpportunities(intel = {}) {
  renderTopOpportunitiesBody(intel);
  const body = $('#portfolio-scan-body');
  const headBtn = $('#opps-scan-btn');
  if (headBtn) headBtn.hidden = !!(body && body.querySelector('[data-jump-view="picks"]'));
}

function renderTopOpportunitiesBody(intel = {}) {
  const body = $('#portfolio-scan-body');
  if (!body) return;
  const meta = $('#scan-meta');
  const picks = livePicks()?.recommendations || [];
  const funded = picks.filter(isFundedPick);
  const scanned = livePicks()?.total_scanned || livePicks()?.universe_size || 0;
  const sym = livePicks()?.currency || _currencySymbol();

  if (funded.length) {
    const chaseCount = (livePicks()?.chase_alerts || []).length;
    const vetoCount = (livePicks()?.deep_confirmation?.rejected || []).length;
    if (meta) meta.textContent = `${funded.length} qualified · ${scanned || '—'} scanned`;

    const head = `<tr>
      <th class="mid" style="width:34px">#</th>
      <th>Ticker</th><th>Setup</th>
      <th class="num">Price</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th>
      <th class="num">Upside</th><th class="num">R:R</th><th class="num">Score</th>
      <th class="mid">Conviction</th><th>Horizon</th><th>Status</th><th style="width:22px"></th>
    </tr>`;

    const rows = funded.slice(0, 10).map((rec, i) => {
      const ticker = normalizeTicker(rec.ticker || rec.symbol || '');
      if (ticker) state.shownSymbols.add(ticker);
      const am = actionMeta(rec);
      const entry = firstUsable(rec.entry_price, rec.current_price);
      const price = firstUsable(rec.current_price, rec.entry_price);
      const target = firstUsable(rec.price_target, rec.target_price);
      const stop = firstUsable(rec.stop_loss);
      const up = upsidePct(target, entry);
      const rr = Number(rec.risk_reward_ratio || 0);
      const score = Number(rec.overall_score || 0);
      const conviction = firstUsable(rec.conviction);
      const horizon = rec.time_horizon ? titleLabel(rec.time_horizon) : 'Not supplied';
      const setup = rec.setup_type ? titleLabel(rec.setup_type) : 'Screen candidate';
      const why = sanitizeExceptionText(rec.why || rec.deep_confirmation_reason
        || (Array.isArray(rec.final_action_reasons) ? rec.final_action_reasons[rec.final_action_reasons.length - 1] : '')
        || (rec.investment_committee_notes || [])[0] || '');
      const narrative = rec.narrative || {};
      const notes = [...(rec.investment_committee_notes || []), ...(rec.final_action_reasons || [])].filter(Boolean).slice(0, 4);
      const stopDown = upsidePct(stop, entry);
      const alloc = finalAllocation(rec);

      const level = (k, v, cls = '') => `<div class="q-level ${cls}"><i>${escapeHtml(k)}</i><b>${v}</b></div>`;
      const detail = `<div class="q-detail-in">
        <div class="q-detail-sec">
          <h3>Thesis</h3>
          ${narrative.headline ? `<p>${escapeHtml(narrative.headline)}</p>` : ''}
          <p>${escapeHtml(narrative.why_now || why || 'No narrative supplied by the scanner for this name.')}</p>
        </div>
        <div class="q-detail-sec">
          <h3>Levels &amp; sizing</h3>
          <div class="q-levels">
            ${level('Entry', fmtPrice(entry, sym))}
            ${level('Stop', fmtPrice(stop, sym), 'bear')}
            ${level('Target', fmtPrice(target, sym), 'gold')}
            ${level('Risk', stopDown == null ? 'N/A' : fmtPct(stopDown, 1, false), 'bear')}
            ${level('Allocation', fmtMoney(alloc, sym, 0))}
            ${level('Shares', fmtNumber(rec.shares, 0))}
          </div>
        </div>
        <div class="q-detail-sec">
          <h3>Kill criteria</h3>
          <p>Invalidated on a close below ${escapeHtml(fmtPrice(stop, sym))}${rec.sector ? ` · sector: ${escapeHtml(rec.sector)}` : ''}. ${escapeHtml(am.guidance || 'Exit on the stop, not on opinion.')}</p>
          ${notes.length ? `<h4 style="margin-top:10px">Committee notes</h4><ul>${notes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>` : ''}
          <div class="q-detail-actions"><button type="button" class="q-btn" data-ticker-open="${escapeHtml(ticker)}">Open tear sheet</button></div>
        </div>
      </div>`;

      return `<tr class="q-tr" data-expand="opp-${i}" tabindex="0" role="button" aria-expanded="false">
          <td class="mid q-rank">${i + 1}</td>
          <td>${qTickerCell(ticker, rec.company || rec.sector || '')}</td>
          <td>${escapeHtml(setup)}</td>
          <td class="num q-price">${fmtPrice(price, sym)}</td>
          <td class="num">${fmtPrice(entry, sym)}</td>
          <td class="num bear">${fmtPrice(stop, sym)}</td>
          <td class="num gold">${fmtPrice(target, sym)}</td>
          <td class="num">${up == null ? '<span class="q-na">N/A</span>' : `<span class="q-chg ${up >= 0 ? 'bull' : 'bear'}">${fmtPct(up, 1, false)}</span>`}</td>
          <td class="num">${rr > 0 ? fmtNumber(rr, 2) + ':1' : '<span class="q-na">N/A</span>'}</td>
          <td class="num q-quant">${score ? fmtScore100(score) : '<span class="q-na">N/A</span>'}</td>
          <td class="mid">${conviction != null ? `${escapeHtml(String(conviction))}/5` : '<span class="q-na">N/A</span>'}</td>
          <td>${escapeHtml(horizon)}</td>
          <td>${qStatus(am.label, am.tone)}</td>
          <td class="mid"><span class="q-caret" aria-hidden="true"></span></td>
        </tr>
        <tr class="q-detail" data-detail="opp-${i}" hidden><td colspan="14">${detail}</td></tr>`;
    }).join('');

    body.innerHTML = qTable(head, rows) +
      `<p class="q-note"><strong>Screen candidates that passed every rule — not validated buy signals.</strong>
        ${chaseCount} chase alert${chaseCount === 1 ? '' : 's'} · ${vetoCount} veto${vetoCount === 1 ? '' : 'es'} filtered out.
        Entry timing is not proven to beat random entry; confirm each name on its own tear sheet and size by risk.</p>`;
    wireExpandableRows(body);
    return;
  }

  // -- No scan yet: analyst-consensus candidates, with QUANTA levels marked N/A.
  const consensus = (intel.analyst_picks || []).slice(0, 8);
  if (consensus.length) {
    if (meta) meta.textContent = `${consensus.length} consensus candidates${intel.stale ? ' · Stale research' : intel.partial ? ' · Partial coverage' : ''}`;
    const head = `<tr>
      <th class="mid" style="width:34px">#</th>
      <th>Ticker</th><th>Sector</th>
      <th class="num">Price</th>
      <th class="num">Street target</th><th class="num">Upside</th><th class="num">Analysts</th>
      <th title="Sell-side analyst consensus, not a QUANTA verdict">Street rating</th><th>Status</th><th style="width:22px"></th>
    </tr>`;
    const normalizeRating = streetRating;
    const rows = consensus.map((r, i) => {
      const ticker = normalizeTicker(r.symbol || '');
      if (ticker) state.shownSymbols.add(ticker);
      const up = numOrNull(r.upside_pct);
      const hasTarget = numOrNull(r.target_mean) != null;
      const detail = `<div class="q-detail-in">
        <div class="q-detail-sec">
          <h3>What is known</h3>
          <p>${normalizeRating(r.recommendation) === 'Rating unavailable' ? 'Street rating unavailable' : `Street consensus is <b>${escapeHtml(normalizeRating(r.recommendation))}</b>`} across ${r.analyst_count || 'an unreported number of'} analysts; ${hasTarget ? `mean target ${escapeHtml(fmtPrice(r.target_mean, _currencySymbol()))}${up != null ? ` (${fmtPct(up, 1, false)} vs spot)` : ''}` : 'mean target unavailable'}.</p>
        </div>
        <div class="q-detail-sec">
          <h3>What is missing</h3>
          <p>QUANTA entry, stop, target, reward-to-risk and position size are produced by the Picks scanner. They read N/A here because ${scanRanClause()} — they are not estimated.</p>
        </div>
        <div class="q-detail-sec">
          <h3>Next step</h3>
          <p>${livePicks() ? 'Rerun the Picks scan under a different mandate, or' : 'Run a Picks scan to rank and size this universe, or'} open the tear sheet for a single-name trade plan.</p>
          <div class="q-detail-actions">
            <button type="button" class="q-btn" data-ticker-open="${escapeHtml(ticker)}">Open tear sheet</button>
            <button type="button" class="q-btn q-btn--primary" data-jump-view="picks">Run scan</button>
          </div>
        </div>
      </div>`;
      return `<tr class="q-tr" data-expand="cons-${i}" tabindex="0" role="button" aria-expanded="false">
          <td class="mid q-rank">${i + 1}</td>
          <td>${qTickerCell(String(r.symbol || '').replace(/\.NS$/, ''), r.name || '')}</td>
          <td>${escapeHtml(r.sector || '—')}</td>
          <td class="num q-price">${fmtPrice(r.price, _currencySymbol())}</td>
          <td class="num gold">${fmtPrice(r.target_mean, _currencySymbol())}</td>
          <td class="num">${up != null ? `<span class="q-chg ${up >= 0 ? 'bull' : 'bear'}">${fmtPct(up, 1, false)}</span>` : '<span class="q-na" title="Upside unavailable">Upside unavailable</span>'}</td>
          <td class="num">${r.analyst_count || '<span class="q-na">N/A</span>'}</td>
          <td>${escapeHtml(normalizeRating(r.recommendation))}</td>
          <td>${qStatus('Research only', 'info')}</td>
          <td class="mid"><span class="q-caret" aria-hidden="true"></span></td>
        </tr>
        <tr class="q-detail" data-detail="cons-${i}" hidden><td colspan="10">${detail}</td></tr>`;
    }).join('');
    body.innerHTML = qTable(head, rows) +
      `<p class="q-note"><strong>Analyst consensus, not a QUANTA setup.</strong> Entry, stop, reward-to-risk and position size are absent because ${scanRanClause()} — QUANTA does not estimate levels it has not computed. ${livePicks() ? 'Adjust the mandate or rerun the scan to look again.' : 'Run a scan to add them.'}</p>`;
    wireExpandableRows(body);
    attachDashboardActionHandlers(body);
    return;
  }

  // -- Nothing at all: compact, informative empty state (§9).
  if (meta) meta.textContent = scanned ? `${scanned} scanned · 0 qualified` : (livePicks() ? 'Scan complete · 0 qualified' : 'No scan yet');
  const reasons = [
    ['Too extended', 'far above moving averages'],
    ['Weak volume', 'no accumulation on the move'],
    ['Earnings risk', 'report inside the hold window'],
    ['Poor reward-to-risk', 'stop/target below 2:1'],
    ['No clean base', 'no defined pivot to trade'],
    ['Stale data', 'inputs incomplete or delayed'],
  ];
  const scanDone = !!livePicks();
  body.innerHTML = `<div class="q-empty">
    <strong>${scanned || scanDone ? 'No qualified setups' : 'No Picks scan yet'}</strong>
    <p>${scanned ? `Scanned ${escapeHtml(String(scanned))} names — none cleared every entry gate.` : (scanDone ? 'The last Picks scan completed without qualified setups.' : 'No Picks scan has run this session.')}
       ${intel.loading ? 'Analyst research is loading.' : intel.feed_timeout ? 'Analyst research is temporarily unavailable. You can retry the market feed or run a Picks scan.' : 'Run a Picks scan to evaluate entry criteria and position sizes.'}</p>
    <details class="q-disclose"><summary>Entry criteria</summary><ul class="q-empty-reasons">${reasons.map(([k, v]) => `<li><b>${escapeHtml(k)}</b><span>${escapeHtml(v)}</span></li>`).join('')}</ul></details>
    <div class="q-empty-actions">
      <button type="button" class="q-btn q-btn--primary" data-jump-view="picks">Run a Picks scan</button>
      ${intel.feed_timeout ? '<button type="button" class="q-btn" data-retry="market-intel">Retry market feed</button>' : ''}
      <button type="button" class="q-btn" data-scroll-to="top-movers">See today's movers</button>
    </div>
  </div>`;
  attachDashboardActionHandlers(body);
}
// Back-compat alias: the intel poller and tests still call the old name.
const renderPortfolioScanPanel = renderTopOpportunities;

/* =========================================================================
   ROW 4a — MARKET SIGNAL. The factor model as a score plus diverging bars,
   not a wall of telemetry cards.
   ========================================================================= */
function renderSignalCheck(market = {}, news = {}, intel = {}) {
  const body = $('#signal-check-body');
  if (!body) return;
  const meta = $('#signal-check-meta');
  const model = computeRegimeModel(market, news, intel);
  const factors = Array.isArray(model.factors) ? model.factors : [];

  if (!factors.length) {
    body.innerHTML = '<div class="q-empty">Factor model is warming up — no signals to score yet.</div>';
    if (meta) meta.textContent = 'Warming up';
    return;
  }

  const counts = signalCheckCounts(model);
  const al = model.alignment || {};
  if (meta) meta.textContent = `${model.count} factors`;

  // Honest rescale: each factor scores -2..+2, so the sum maps linearly onto
  // 0-100 (directionScore). A rescaled sum of placeholder zeros is not a
  // reading: no headline number.
  const dir = directionScore(model);
  const scoreShown = dir.shown;
  const name = regimeName(market, model);

  const rows = factors.map((f) => {
    const s = Math.max(-2, Math.min(2, Number(f.score) || 0));
    const st = factorState(f.score);
    const half = (Math.abs(s) / 2) * 50;
    const left = s >= 0 ? 50 : 50 - half;
    const barTone = s > 0 ? 'bull' : s < 0 ? (s <= -2 ? 'bear' : 'warn') : '';
    return `<div class="q-fact">
      <span class="q-fact-n">${escapeHtml(f.label || f.key || '')}</span>
      <span class="q-dbar" role="img" aria-label="${escapeHtml(st.label)} (${s > 0 ? '+' : ''}${s})">
        <i class="${barTone}" style="left:${left}%;width:${Math.max(half, 1.5)}%"></i>
      </span>
      <span class="q-fact-note" title="${escapeHtml(f.note || '')}">${escapeHtml(f.note || '')}</span>
      <span class="q-fact-s ${st.cls}">${s > 0 ? '+' : ''}${s}</span>
    </div>`;
  }).join('');

  // Same buckets as the tally, so the sentence can never contradict it.
  const nameOf = (f) => f.label || f.key || '';
  const bucket = (pred) => factors.filter((f) => pred(Number(f.score) || 0)).map(nameOf);
  const sup = bucket((x) => x > 0), neu = bucket((x) => x === 0);
  const ag = bucket((x) => x < 0);
  const parts = [];
  if (sup.length) parts.push(`${sup.join(' & ')} ${sup.length > 1 ? 'support' : 'supports'}`);
  if (ag.length) parts.push(`${ag.join(' & ')} ${ag.length > 1 ? 'count' : 'counts'} against`);
  if (neu.length) parts.push(`${neu.length} neutral`);

  body.innerHTML = `
    <div class="q-sig-summary">
      ${model.guidanceWithheld ? '<div class="q-sig-score"><span>Direction not scored: too few measured inputs</span></div>' : ''}
      <div class="q-sig-tally" title="Each factor scores -2 to +2; their sum, rescaled to 0-100, is the direction score in the regime card. A factor counts against at -1; at -2 it is a strong opposite signal.">
        <span class="bull"><b>${counts.confirmed}</b>support</span>
        <span><b>${counts.neutral}</b>neutral</span>
        <span class="bear"><b>${counts.warning + counts.opposed}</b>against</span>
      </div>
    </div>
    ${rows}
    ${model.guidanceWithheld ? `<p class="q-sig-read">${escapeHtml(`Too few inputs were measured to score this read (${model.count} factors, none of them scored).`)}</p>` : ''}`;
  void scoreShown; void name; void parts;
}

/* =========================================================================
   ROW 4b — MARKET DRIVERS. Ranked forces on the tape: what it is, what it
   means, what to do. Strictly the engine's own drivers.
   ========================================================================= */
function renderMarketDrivers(market = {}, news = {}, intel = {}) {
  const body = $('#market-drivers-body');
  if (!body) return;
  const meta = $('#market-drivers-meta');
  const model = computeRegimeModel(market, news, intel);
  const drivers = Array.isArray(model.marketDrivers) ? model.marketDrivers.slice(0, 5) : [];
  const ni = model.newsImpact || {};
  const { label: netLabel, tone: netTone } = newsTiltRead({ newsImpact: ni, newsFeedsLive: model.newsLive });

  if (!drivers.length) {
    if (meta) meta.textContent = 'Quiet tape';
    body.innerHTML = `<div class="q-empty">
      <strong>${escapeHtml(netLabel)}: no single story is steering the market</strong>
      <p>Let the factor model lead: trend, breadth and volatility decide the posture. Keep position size measured until a driver emerges.</p>
    </div>`;
    return;
  }

  if (meta) meta.textContent = `${drivers.length} active`;
  const arrow = (dir) => ({ bullish: QIco.up, bearish: QIco.down }[String(dir || '').toLowerCase()] || QIco.flat);
  const toneFor = (dir) => ({ bullish: 'bull', bearish: 'bear', mixed: 'warn' }[String(dir || '').toLowerCase()] || 'neutral');
  const dirLabel = (dir) => ({ bullish: 'Bullish', bearish: 'Bearish', mixed: 'Mixed' }[String(dir || '').toLowerCase()] || 'Neutral');

  const rows = drivers.map((d) => {
    const tone = toneFor(d.direction);
    const rawMeaning = String(d.marketMeaning || '').trim();
    const meaning = (!rawMeaning || /^watch the tape/i.test(rawMeaning))
      ? newsMeaning({ category: d.category, impact: d.impactLevel === 'High' ? mkt('NEWS_IMPACT_HIGH') : mkt('NEWS_IMPACT_MEDIUM') })
      : rawMeaning;
    const title = String(d.title || '').slice(0, 130);
    const titleHtml = safeUrl(d.url)
      ? `<a class="q-driver-t" href="${escapeHtml(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
      : `<span class="q-driver-t">${escapeHtml(title)}</span>`;
    return `<article class="q-driver ${tone}">
      <span class="q-driver-ico">${arrow(d.direction)}</span>
      <div>
        ${titleHtml}
        <div class="q-driver-m">
          <span>${escapeHtml(String(d.category || 'Market').toUpperCase())}</span>
          <span class="${d.impactLevel === 'High' ? 'hi' : ''}">${escapeHtml(String(d.impactLevel || 'High').toUpperCase())} IMPACT</span>
          <span>${escapeHtml(dirLabel(d.direction).toUpperCase())}</span>
        </div>
        <div class="q-driver-x">
          <span><i>Means</i>${escapeHtml(meaning)}</span>
          <span><i>Do</i>${escapeHtml(driverTraderResponse(d))}</span>
        </div>
      </div>
    </article>`;
  }).join('');

  const summary = model.newsImpactSummary
    ? `<p class="q-note">${escapeHtml(model.newsImpactSummary)}</p>`
    : '';
  body.innerHTML = `<div class="q-driver-bal">
      <strong class="${netTone}">${escapeHtml(netLabel)}</strong>
      <span><b>${ni.bullishHighImpact || 0}</b> bullish · <b>${ni.bearishHighImpact || 0}</b> bearish · <b>${ni.highImpactCount || drivers.length}</b> high-impact</span>
    </div>${rows}${summary}`;
}

/* =========================================================================
   ROW 5a — BREADTH & SECTOR CONTEXT. Only measurements the backend actually
   returns: index participation, the mover feed's advance/decline split, and
   the sector mix of those movers (labelled as such, never as market breadth).
   ========================================================================= */
function renderBreadthPanel(market = {}, intel = {}) {
  const body = $('#breadth-body');
  if (!body) return;
  const meta = $('#breadth-meta');
  const part = firstUsable(market.participation_pct);
  const detail = market.participation || null;
  const members = (detail && Array.isArray(detail.members)) ? detail.members : [];
  const ctx = marketContext();
  const sectors = ((ctx && ctx.sectors) || []).filter((x) => x.above_sma50 != null);

  // An uptrend here is one rule: the fund trades above its 50-day average.
  const idxUp = members.filter((m) => m.above).length;
  const secUp = sectors.filter((x) => x.above_sma50).length;
  const total = members.length + sectors.length;
  const share = total ? ((idxUp + secUp) / total) * 100 : part;
  const verdict = share == null ? null : share >= mkt('PARTICIPATION_BROAD_PCT') ? { w: 'Broad', t: 'bull' } : share <= mkt('PARTICIPATION_WEAK_PCT') ? { w: 'Narrow', t: 'bear' } : { w: 'Mixed', t: 'warn' };
  if (meta) {
    meta.textContent = verdict ? `${verdict.w}: ${total ? `${idxUp + secUp} of ${total}` : `${fmtNumber(part, 0)}%`} in uptrends` : 'Limited';
    meta.className = `q-head-meta ${verdict ? verdict.t : ''}`;
  }

  const fallbackFrac = detail && detail.measured ? `${detail.above} of ${detail.measured}` : (part == null ? 'N/A' : `${part}%`);
  const indices = `<section class="q-pt-sec">
      <h3 class="q-pt-h">Major indices <span>${members.length ? `${idxUp} of ${members.length} in uptrends` : `${escapeHtml(fallbackFrac)} benchmarks above their 50-day average`}</span></h3>
      ${members.length ? `<ul class="q-pt-idx">${members.map((m) => `<li class="${m.above ? 'is-up' : ''}" title="${escapeHtml(m.symbol)}"><b>${escapeHtml(m.label || m.symbol)}</b><span>${m.above ? 'Uptrend' : 'Below 50-day'}</span></li>`).join('')}</ul>` : ''}
    </section>`;

  const sorted = sectors.slice().sort((a, b) => (b.above_sma50 ? 1 : 0) - (a.above_sma50 ? 1 : 0) || (b.change_pct || 0) - (a.change_pct || 0));
  const upNames = sorted.filter((x) => x.above_sma50).map((x) => x.label);
  const sectorHtml = sectors.length ? `<section class="q-pt-sec">
      <h3 class="q-pt-h">Sectors <span>${secUp} of ${sectors.length} in uptrends</span></h3>
      <div class="q-pt-meter" role="img" aria-label="${escapeHtml(`${secUp} of ${sectors.length} sector funds above their 50-day average${upNames.length ? `: ${upNames.join(', ')}` : ''}`)}">${sorted.map((x) => `<i class="${x.above_sma50 ? 'is-up' : ''}" title="${escapeHtml(`${x.label}: ${x.above_sma50 ? 'above' : 'below'} its 50-day average`)}"></i>`).join('')}</div>
      <p class="q-pt-note">${upNames.length ? `In uptrends: ${escapeHtml(upNames.join(', '))}.` : 'No sector fund is above its 50-day average.'}</p>
    </section>` : '';

  const movers = [...(intel.movers_up || []), ...(intel.movers_down || [])].filter((m) => numOrNull(m.change_pct) != null);
  let moverHtml = '';
  if (movers.length) {
    const up = movers.filter((m) => Number(m.change_pct) > 0).sort((a, b) => b.change_pct - a.change_pct);
    const dn = movers.filter((m) => Number(m.change_pct) < 0).sort((a, b) => a.change_pct - b.change_pct);
    const fl = movers.length - up.length - dn.length;
    const pct = (k) => ((k / movers.length) * 100).toFixed(1);
    const top = Math.max(...movers.map((m) => Math.abs(Number(m.change_pct))), 1);
    const chips = (list) => (list.length ? `<ol class="q-pt-list">${list.slice(0, 5).map((m) => `<li><b class="hp-sym" title="${escapeHtml(m.symbol)}">${escapeHtml(shownSymbol(m.symbol))}</b><span class="q-pt-bar"><i class="${m.change_pct > 0 ? 'up' : 'dn'}" style="width:${((Math.abs(m.change_pct) / top) * 100).toFixed(1)}%"></i></span><span class="${m.change_pct > 0 ? 'bull' : 'bear'}">${fmtPct(m.change_pct, 1, false)}</span></li>`).join('')}</ol>` : '<span class="muted">None</span>');
    moverHtml = `<section class="q-pt-sec">
        <h3 class="q-pt-h">Today's biggest movers <span>${up.length} up, ${dn.length} down</span></h3>
        <div class="q-breadth-bar" role="img" aria-label="${up.length} of the ${movers.length} biggest movers rose, ${dn.length} fell${fl ? `, ${fl} flat` : ''}">
          <i class="up" style="width:${pct(up.length)}%"></i><i class="fl" style="width:${pct(fl)}%"></i><i class="dn" style="width:${pct(dn.length)}%"></i>
        </div>
        <div class="q-pt-movers"><div><span class="q-pt-k">Top gainers</span>${chips(up)}</div><div><span class="q-pt-k">Top losers</span>${chips(dn)}</div></div>
        <p class="q-pt-note">The day's ${movers.length} largest moves: a sample, not the whole market.</p>
      </section>`;
  }
  void sectorHtml;
  body.innerHTML = `<div class="q-pt">${indices}${moverHtml}</div>`;
}

/* =========================================================================
   ROW 5b — UPCOMING CATALYSTS. Dated, scheduled events only. Breaking macro
   lives in Market Intelligence; repeating it here would be a third copy.
   ========================================================================= */
function renderCatalystCalendar(intel = {}, news = {}) {
  const body = $('#event-risk-body');
  if (!body) return;
  const meta = $('#event-risk-meta');
  const earnings = (intel.earnings_week || []).slice(0, 8);

  const allN = Math.max(earnings.length, earningsAhead(intel));
  if (meta) meta.textContent = earnings.length ? `${allN > earnings.length ? `${earnings.length} of ${allN}` : earnings.length} scheduled` : '7 calendar days';
  const tabCount = $('#event-risk-count');
  if (tabCount) tabCount.textContent = earnings.length ? String(Math.max(earnings.length, earningsAhead(intel))) : '';

  if (!earnings.length) {
    body.innerHTML = `<div class="q-empty">
      <strong>No earnings dates returned</strong>
      <p>The current feed has no scheduled earnings in the next 7 calendar days. This does not rule out macro events or unreported company dates; check the calendar before entering.</p>
    </div>`;
    return;
  }

  const dayLabel = (d) => (d <= 0 ? 'Today' : d === 1 ? 'Tomorrow' : `In ${d}d`);
  const head = `<tr><th>When</th><th>Event</th><th>Affected</th><th>Category</th><th>Impact</th><th style="width:22px"></th></tr>`;
  const rows = earnings.map((r, i) => {
    const ticker = normalizeTicker(r.symbol || '');
    if (ticker) state.shownSymbols.add(ticker);
    const d = Number(r.earnings_in_days);
    const tone = d <= 1 ? 'bear' : d <= 3 ? 'warn' : 'neutral';
    const detail = `<div class="q-detail-in">
      <div class="q-detail-sec">
        <h3>Why it matters</h3>
        <p>${escapeHtml(ticker)} reports on ${escapeHtml(r.earnings_date_pretty || 'the scheduled date')}. Watch the gap and the guidance, not the print alone — size below normal into the event.</p>
      </div>
      <div class="q-detail-sec">
        <h3>Context</h3>
        <p>${escapeHtml(r.name || ticker)}${r.sector ? ` · ${escapeHtml(r.sector)}` : ''}${r.price != null ? ` · last ${escapeHtml(fmtPrice(r.price, _currencySymbol()))}` : ''}</p>
        <div class="q-detail-actions"><button type="button" class="q-btn" data-ticker-open="${escapeHtml(ticker)}">Open tear sheet</button></div>
      </div>
    </div>`;
    return `<tr class="q-tr" data-expand="cal-${i}" tabindex="0" role="button" aria-expanded="false">
        <td>${qStatus(dayLabel(d), tone)}</td>
        <td><span class="q-tick-s">${escapeHtml(ticker)}</span> earnings</td>
        <td class="q-tick-n">${escapeHtml(r.name || r.sector || '—')}</td>
        <td>EARNINGS</td>
        <td>${d <= 1 ? 'High' : 'Medium'}</td>
        <td class="mid"><span class="q-caret" aria-hidden="true"></span></td>
      </tr>
      <tr class="q-detail" data-detail="cal-${i}" hidden><td colspan="6">${detail}</td></tr>`;
  }).join('');

  body.innerHTML = qTable(head, rows);
  wireExpandableRows(body);
}

/* =========================================================================
   TOP GAINERS & LOSERS — the raw tape, in exchange-board form.
   -------------------------------------------------------------------------
   Session open / high / low / previous close / last, ranked by move. This is
   deliberately NOT the Security Monitor: that groups the same names by what
   you can do about them, this ranks them by what they did. Rows open the
   Research tear sheet; an Open chart link shows the symbol on TradingView.
   ========================================================================= */

// The chart URL carries no layout id. A saved-layout path
// (/chart/<id>/?symbol=…) is private to the account that owns it and answers
// 403 for every other visitor, which is what broke these links; the bare
// /chart/ endpoint opens the visitor's own default layout on the symbol.
const TV_CHART_BASE = 'https://www.tradingview.com/chart/';

// yfinance venue codes → TradingView exchange prefixes. Anything unmapped
// falls through to the bare ticker, which TradingView resolves on its own.
const TV_EXCHANGE = {
  NMS: 'NASDAQ', NGM: 'NASDAQ', NCM: 'NASDAQ', NAS: 'NASDAQ',
  NASDAQGS: 'NASDAQ', NASDAQGM: 'NASDAQ', NASDAQCM: 'NASDAQ', NASDAQ: 'NASDAQ',
  NYQ: 'NYSE', NYSE: 'NYSE', PNK: 'OTC', OTC: 'OTC',
  ASE: 'AMEX', AMEX: 'AMEX', PCX: 'AMEX', 'NYSE AMERICAN': 'AMEX',
  NSI: 'NSE', NSE: 'NSE', BSE: 'BSE', BOM: 'BSE',
};

// Yahoo index / crypto tickers have no TradingView equivalent by name — a
// caret symbol resolves to nothing there. These are the ones this dashboard
// can actually put on the board (index rows reach the movers table whenever
// the quote fallback is in play), mapped to their TradingView listings.
const TV_SYMBOL_ALIAS = {
  '^GSPC': 'SP:SPX', '^IXIC': 'NASDAQ:IXIC', '^DJI': 'DJ:DJI', '^RUT': 'RUSSELL:RUT',
  '^VIX': 'TVC:VIX', '^TNX': 'TVC:US10Y', '^IRX': 'TVC:US03MY',
  '^FVX': 'TVC:US05Y', '^TYX': 'TVC:US30Y',
  '^NSEI': 'NSE:NIFTY', '^BSESN': 'BSE:SENSEX',
  '^NSEBANK': 'NSE:BANKNIFTY', '^CNXIT': 'NSE:CNXIT', '^INDIAVIX': 'NSE:INDIAVIX',
  'BTC-USD': 'CRYPTO:BTCUSD', 'ETH-USD': 'CRYPTO:ETHUSD',
};

/** Build a TradingView chart URL for a ticker, exchange-qualified when known. */
/* D4: the symbol button (keyboard and screen readers) and a click anywhere else
   on the row open the Research tear sheet; the Open chart link keeps its own job. */
function wireBoardRows(body) {
  $$('.q-board-row[data-board-ticker]', body).forEach((row) => {
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('a, button')) return;
      openTickerTearSheet(row.dataset.boardTicker);
    });
  });
  $$('.q-board-open[data-ticker-open]', body).forEach((btn) => {
    btn.addEventListener('click', (ev) => { ev.stopPropagation(); openTickerTearSheet(btn.dataset.tickerOpen); });
  });
}
function tradingViewUrl(symbol, exchange) {
  const raw = String(symbol || '').toUpperCase().trim();
  if (!raw) return null;

  let tvSymbol = TV_SYMBOL_ALIAS[raw];
  if (!tvSymbol) {
    if (raw.endsWith('.NS')) tvSymbol = `NSE:${raw.slice(0, -3)}`;
    else if (raw.endsWith('.BO')) tvSymbol = `BSE:${raw.slice(0, -3)}`;
    else if (raw.startsWith('^')) return null;      // unmapped index: no honest chart link
    else {
      // Yahoo writes share classes with a hyphen (BRK-B); TradingView uses a dot.
      const cls = /^([A-Z]+)-([A-Z])$/.exec(raw);
      const base = cls ? `${cls[1]}.${cls[2]}` : raw;
      const venue = TV_EXCHANGE[String(exchange || '').toUpperCase().trim()];
      tvSymbol = venue ? `${venue}:${base}` : base;
    }
  }
  return `${TV_CHART_BASE}?symbol=${encodeURIComponent(tvSymbol)}`;
}

// Sort state per side. Default is the ranking that makes the side meaningful:
// gainers by biggest move down the list, losers by deepest loss.
const _boardSort = {
  gainers: { key: 'change', dir: 'desc' },
  losers: { key: 'change', dir: 'asc' },
  openlow: { key: 'change', dir: 'desc' },
  openhigh: { key: 'change', dir: 'asc' },
};

function sortBoardRows(rows, sort) {
  const val = (r) => {
    if (sort.key === 'symbol') return String(r.symbol || '').toUpperCase();
    const raw = sort.key === 'ltp' ? firstUsable(r.price, r.ltp) : r.change_pct;
    return raw == null || raw === '' ? NaN : Number(raw);
  };
  const out = rows.slice().sort((a, b) => {
    const x = val(a), y = val(b);
    const direction = sort.dir === 'desc' ? -1 : 1;
    if (typeof x === 'string') return direction * x.localeCompare(y);
    if (!Number.isFinite(x)) return Number.isFinite(y) ? 1 : 0;
    if (!Number.isFinite(y)) return -1;
    return direction * (x - y);
  });
  return out;
}

// Tone and default heading per board side. Held in one place so a new board
// (Open = Low / Open = High) does not need a second ternary chain inside the
// renderer, which is how the old `side === 'gainers' ? ... : ...` would have
// mislabelled every board that was neither gainers nor losers as "Top losers".
const BOARD_TONE = { gainers: 'bull', losers: 'bear', openlow: 'bull', openhigh: 'bear' };
const BOARD_CAPTION = { gainers: 'Top gainers', losers: 'Top losers' };

function boardTable(side, rows, sym, caption) {
  const sort = _boardSort[side];
  const sorted = sortBoardRows(rows, sort);
  const arrow = (key) => (sort.key === key
    ? `<span class="q-sort is-on">${sort.dir === 'desc' ? '▼' : '▲'}</span>`
    : '<span class="q-sort">⇅</span>');

  const head = `<tr>
      <th aria-sort="${sort.key === 'symbol' ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button class="q-sortable" data-board-sort="symbol" data-board-side="${side}" type="button"
          aria-label="Sort by symbol">Symbol ${arrow('symbol')}</button></th>
      <th class="num">Open</th>
      <th class="num">High</th>
      <th class="num">Low</th>
      <th class="num">Prev. Close</th>
      <th class="num" aria-sort="${sort.key === 'change' ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button class="q-sortable" data-board-sort="change" data-board-side="${side}" type="button"
          aria-label="Sort by daily percentage change" title="Last traded price and the day's change">Last / Change ${arrow('change')}</button></th>
    </tr>`;

  const body = sorted.map((r) => {
    const ticker = normalizeTicker(r.symbol || '');
    const q = normalizeQuote(r);
    const chg = r.change_pct == null || r.change_pct === '' ? NaN : Number(r.change_pct);
    const dir = Number.isFinite(chg) ? (chg > 0 ? 'up' : chg < 0 ? 'dn' : '') : '';
    const url = tradingViewUrl(r.symbol, r.exchange);
    const abs = firstUsable(r.change_abs, q.changeAbs);
    const deltaBits = [
      abs != null ? `${abs >= 0 ? '+' : '−'}${fmtPrice(Math.abs(abs), sym)}` : null,
      Number.isFinite(chg) ? `${chg >= 0 ? '+' : ''}${fmtNumber(chg, 2)}%` : null,
    ].filter(Boolean).join('  ');
    const cell = (v) => (v == null ? '<span class="q-na">N/A</span>' : escapeHtml(fmtPrice(v, sym)));
    const extended = setupQualityForMover(r) === SETUP_QUALITY.extended;
    // D4: a row opens the in-app Research tear sheet; TradingView is a secondary link.
    return `<tr class="q-board-row" data-board-ticker="${escapeHtml(ticker)}">
      <td class="q-board-sym">
        <button type="button" class="q-board-open" data-ticker-open="${escapeHtml(ticker)}" title="Open the ${escapeHtml(ticker)} Research tear sheet">
          <b>${escapeHtml(ticker)}${extended ? '<span class="q-board-flag" title="Move is too extended for a fresh entry">Extended</span>' : ''}</b>
          <small>${escapeHtml(r.name || '')}</small>
        </button>
        ${url ? `<a class="q-board-chart" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="Open the ${escapeHtml(ticker)} chart on TradingView (opens in a new tab)">Open chart</a>` : ''}
      </td>
      <td class="num">${cell(q.open)}</td>
      <td class="num q-hi">${cell(q.high)}</td>
      <td class="num q-lo">${cell(q.low)}</td>
      <td class="num">${cell(q.prevClose)}</td>
      <td class="num q-board-ltp ${dir}">
        <b>${cell(firstUsable(r.price, q.ltp))}</b>
        ${deltaBits ? `<i>${escapeHtml(deltaBits)}</i>` : ''}
      </td>
    </tr>`;
  }).join('');

  // Caption defaults keep the movers board calling this with three arguments.
  const tone = BOARD_TONE[side] || '';
  const title = caption || BOARD_CAPTION[side] || '';
  return `<div class="q-board-col">
    <h3 class="q-board-cap ${tone}">
      ${escapeHtml(title)}
      <span>${rows.length} name${rows.length === 1 ? '' : 's'}</span>
    </h3>
    ${sorted.length ? `<div class="q-tablewrap" tabindex="0" role="region" aria-label="${escapeHtml(title)}: all ${rows.length} results"><table class="q-board-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>` : '<p class="q-empty q-empty--inline">No matching quotes in this snapshot.</p>'}
  </div>`;
}

function renderTopMovers(intel = {}) {
  const body = $('#top-movers-body');
  if (!body) return;
  const meta = $('#top-movers-meta');
  const up = (intel.movers_up || []).filter((r) => r.change_pct != null && Number.isFinite(Number(r.change_pct)));
  const dn = (intel.movers_down || []).filter((r) => r.change_pct != null && Number.isFinite(Number(r.change_pct)));

  if (!up.length && !dn.length) {
    const timedOut = !intel.loading && !!intel.feed_timeout;
    if (meta) meta.textContent = intel.loading ? 'Fetching' : timedOut ? 'Timed out' : 'No data';
    body.innerHTML = intel.loading
      ? '<div class="q-skelrows" aria-busy="true"><span class="skel-line"></span><span class="skel-line"></span><span class="skel-line"></span></div>'
      : `<div class="q-empty">
          <strong>${timedOut ? 'Quote feed timed out' : 'No ranked moves this session'}</strong>
          <p>${timedOut
            ? 'The quote request did not finish, so this is a missing feed, not a quiet market.'
            : 'The provider returned no usable quotes, so there is nothing to rank.'} The rest of the dashboard is unaffected.</p>
          <div class="q-empty-actions"><button type="button" class="q-btn" data-action="retry-dashboard">Retry</button></div>
        </div>`;
    body.querySelector('[data-action="retry-dashboard"]')?.addEventListener('click', () => loadMarketIntel({ resetAttempts: true }));
    return;
  }

  const sym = _currencySymbol();
  if (meta) meta.textContent = `${up.length} up · ${dn.length} down${intel.stale ? ' · Stale quotes' : intel.partial ? ' · Partial coverage' : ''}`;
  body.innerHTML = `<div class="q-board">${boardTable('gainers', up, sym)}${boardTable('losers', dn, sym)}</div>
    <p class="q-note">Session open, high, low and previous close as reported by the quote feed, which does not report exchange quote times: treat prices as delayed${intel.as_of ? ` (retrieved ${escapeHtml(new Date(intel.as_of).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: currentMarketTz().tz }))} ${escapeHtml(currentMarketTz().label)})` : ''}. Select a row to open its Research tear sheet; Open chart shows the symbol on TradingView in a new tab.</p>`;

  wireBoardRows(body);

  // Column sorting: same key toggles direction, a new key starts descending.
  $$('.q-sortable', body).forEach((button) => {
    const apply = () => {
      const side = button.dataset.boardSide;
      const key = button.dataset.boardSort;
      const cur = _boardSort[side];
      _boardSort[side] = { key, dir: cur.key === key && cur.dir === 'desc' ? 'asc' : 'desc' };
      renderTopMovers(intel);
    };
    button.addEventListener('click', apply);
  });
}

/* =========================================================================
   OPENED AT THE SESSION EXTREME. Two boards in the exact format of Market
   Moves, because they answer the same question about the same rows: what did
   the session actually do. Open == Low means the stock never traded below
   its first print; Open == High means it never traded above it.

   The selection is the backend's (it scans every quote fetched, not just the
   ranked movers shown elsewhere). The frontend only renders what it
   is handed — it must never re-derive the match from a truncated list and
   present the result as if it had scanned the market.
   ========================================================================= */
function renderOpenExtremes(intel = {}) {
  const body = $('#open-extremes-body');
  if (!body) return;
  const meta = $('#open-extremes-meta');
  const atLow = intel.open_at_low || [];
  const atHigh = intel.open_at_high || [];
  const scanned = Number(intel.open_extremes_scanned || 0);
  const coverage = intel.open_extremes_coverage || {};
  const matchCount = atLow.length + atHigh.length;
  const coverageHtml = `<div class="q-coverage">
    <p><strong>${matchCount} exact match${matchCount === 1 ? '' : 'es'} from ${scanned} fetched quotes.</strong> All returned matches are shown; scroll inside either table for longer lists.</p>
    <p>Coverage is the tracked stock universe${intel.screener_used ? ' plus available screener quotes' : ''}, not the whole exchange. Only a session that opened at its low or its high qualifies, at the displayed price precision.</p>
    ${coverage.comparable != null ? `<dl><div><dt>Comparable OHLC</dt><dd>${escapeHtml(String(coverage.comparable))}</dd></div><div><dt>Missing OHLC</dt><dd>${escapeHtml(String(coverage.missing_ohlc))}</dd></div><div><dt>Invalid OHLC</dt><dd>${escapeHtml(String(coverage.invalid_ohlc))}</dd></div><div><dt>Single-price quotes</dt><dd>${escapeHtml(String(coverage.flat))}</dd></div>${coverage.tracked_universe != null ? `<div><dt>Tracked symbols</dt><dd>${escapeHtml(String(coverage.tracked_universe))}</dd></div>` : ''}</dl>` : '<p>Detailed OHLC coverage is unavailable in this cached snapshot.</p>'}
  </div>`;

  if (intel.loading) {
    if (meta) meta.textContent = 'Scanning';
    body.innerHTML = '<div class="q-skelrows" aria-busy="true"><span class="skel-line"></span><span class="skel-line"></span><span class="skel-line"></span></div>';
    return;
  }

  if (!atLow.length && !atHigh.length) {
    // An empty board is a real answer on a quiet tape, so say what was
    // examined rather than leaving it looking broken.
    if (meta) meta.textContent = scanned ? `0 of ${scanned} scanned` : 'No data';
    body.innerHTML = coverageHtml + `<div class="q-empty">
      <strong>No stock opened exactly at its session extreme</strong>
      <p>${scanned
        ? `Checked ${escapeHtml(String(scanned))} quotes this cycle. A match needs the open to equal the high or the low exactly, which is uncommon on a two-sided tape.`
        : 'No quotes carried a session open, high and low this cycle, so there was nothing to compare.'}</p>
    </div>`;
    return;
  }

  const sym = _currencySymbol();
  if (meta) {
    meta.textContent = `${atLow.length} at low · ${atHigh.length} at high` +
      (scanned ? ` · ${scanned} scanned` : '') + (intel.stale ? ' · Stale quotes' : intel.partial ? ' · Partial coverage' : '');
  }
  body.innerHTML = coverageHtml + `<div class="q-board">
      ${boardTable('openlow', atLow, sym, 'Opened at the low · no lower reported price')}
      ${boardTable('openhigh', atHigh, sym, 'Opened at the high · no higher reported price')}
    </div>
    <p class="q-note">Session snapshot, not a buy or sell signal. Single-price and unusable OHLC quotes are excluded; their cause cannot be determined from prices alone. Select a row to open its Research tear sheet; Open chart shows the symbol on TradingView in a new tab. Last / Change sorts by daily percentage change.</p>`;

  wireBoardRows(body);

  $$('.q-sortable', body).forEach((button) => {
    const apply = () => {
      const side = button.dataset.boardSide;
      const key = button.dataset.boardSort;
      const cur = _boardSort[side];
      _boardSort[side] = { key, dir: cur.key === key && cur.dir === 'desc' ? 'asc' : 'desc' };
      renderOpenExtremes(intel);
    };
    button.addEventListener('click', apply);
  });
}

/* =========================================================================
   Scanner vetoes — the second half of the Avoid bucket. Kept separate from
   the mover table because the reason ("why not") is the point, not the price.
   ========================================================================= */
function renderSecurityQueues(intel = {}) {
  const movers = [...(intel.movers_up || []), ...(intel.movers_down || [])];
  const extended = livePicks()?.chase_alerts?.length
    ? livePicks().chase_alerts
    : movers.filter(row => setupQualityForMover(row) === SETUP_QUALITY.extended);
  const watchlist = livePicks()?.early_momentum || [];
  for (const [id, rows, label] of [
    ['monitor-watchlist', watchlist, 'Watchlist'], ['monitor-extended', extended, 'Extended'],
  ]) {
    const body = document.getElementById(`${id}-body`);
    if (!body) continue;
    const tab = document.getElementById(`tab-${id}`);
    if (tab) tab.textContent = `${label} (${rows.length})`;
    if (!rows.length) {
      body.innerHTML = `<div class="q-empty"><strong>${label === 'Watchlist' ? 'No developing setups from a scan yet' : 'No extended names in the available feed'}</strong><p>${label === 'Watchlist' ? 'Run a Picks scan to build the early-momentum watchlist. These names require confirmation before entry.' : 'This is a feed-level screen, not a guarantee that every security has a safe entry.'}</p><button type="button" class="q-btn" data-jump-view="picks">Open Portfolio Builder</button></div>`;
      attachDashboardActionHandlers(body);
      continue;
    }
    const head = '<tr><th>Ticker / Company</th><th class="num">Price</th><th class="num">Score</th><th class="num">RSI</th><th class="num">From 20-DMA</th><th>Signal / Entry criteria</th></tr>';
    const content = rows.map(row => {
      const ticker = normalizeTicker(row.ticker || row.symbol || '');
      const price = firstUsable(row.current_price, row.price);
      const score = firstUsable(row.score, row.overall_score);
      const rsi = firstUsable(row.rsi);
      const distance = firstUsable(row.distance_from_20dma);
      const reason = row.reason || (row.signals || []).join(' · ') || 'Extended move; wait for a defined entry from analysis.';
      return `<tr><td><button type="button" class="q-security-link" data-ticker-open="${escapeHtml(ticker)}">${qTickerCell(ticker, row.company || row.name || row.sector)}</button></td><td class="num">${price == null ? qNA(null) : fmtPrice(price, _currencySymbol())}</td><td class="num q-quant">${score == null ? qNA(null) : fmtScore100(score)}</td><td class="num">${rsi == null ? qNA(null) : fmtNumber(rsi, 1)}</td><td class="num">${distance == null ? qNA(null) : fmtPct(distance, 1, false)}</td><td>${escapeHtml(reason)}</td></tr>`;
    }).join('');
    body.innerHTML = qTable(head, content) + '<p class="q-note">Observed screen results, not entry instructions. Entry, stop and target levels require a completed analysis; select a ticker to open its tear sheet.</p>';
    wireExpandableRows(body);
  }
}

function renderSetupStatusCard(intel = {}) {
  renderSecurityQueues(intel);
  const body = $('#setup-status-body');
  if (!body) return;
  const vetoes = livePicks()?.deep_confirmation?.rejected || [];
  const movers = [...(intel.movers_up || []), ...(intel.movers_down || [])];
  const inferredChase = movers.filter((row) => setupQualityForMover(row) === SETUP_QUALITY.extended).slice(0, 5);

  const rows = [
    ...vetoes.slice(0, 6).map((row) => ({
      ticker: row.ticker, flag: SETUP_QUALITY.veto, tone: 'bear',
      reason: row.reason || 'Deep analysis withheld the setup.',
      revalidate: vetoRevalidation(row.reason_codes),
    })),
  ];

  const meta = $('#vetoes-meta');
  const avoidTab = $('#tab-scanner-vetoes');
  if (avoidTab) avoidTab.textContent = `Avoid (${rows.length})`;

  if (!rows.length) {
    // Nothing vetoed is a clean read, not missing data — say so in one line.
    if (meta) meta.textContent = 'No vetoes';
    body.innerHTML = `<div class="q-empty">
      <strong>No scanner vetoes on record</strong>
      <p>${inferredChase.length
        ? `${inferredChase.length} name${inferredChase.length === 1 ? '' : 's'} on today's board already moved too far to chase. Per-name vetoes — analyzer disagreements and entry-quality fails — appear here after a Picks scan.`
        : 'Run a Picks scan to populate the per-name veto queue — analyzer disagreements, entry-quality fails and chase alerts.'}</p>
      <div class="q-empty-actions"><button type="button" class="q-btn" data-jump-view="picks">Run Picks scan</button></div>
    </div>`;
    attachDashboardActionHandlers(body);
    return;
  }
  if (meta) meta.textContent = `${rows.length} rejected`;

  const head = `<tr><th>Ticker</th><th>Flag</th><th>Why it was rejected</th><th>Becomes valid again when</th></tr>`;
  const rowsHtml = rows.map((r) => {
    const ticker = normalizeTicker(r.ticker || '');
    return `<tr class="q-tr" data-ticker-open="${escapeHtml(ticker)}" tabindex="0" role="button">
      <td><span class="q-tick-s">${escapeHtml(ticker || '—')}</span></td>
      <td>${qStatus(r.flag, r.tone)}${r.metric ? ` <span class="q-tick-n">${escapeHtml(r.metric)}</span>` : ''}</td>
      <td>${escapeHtml(r.reason)}</td>
      <td class="q-tick-n">${escapeHtml(r.revalidate)}</td>
    </tr>`;
  }).join('');

  body.innerHTML = qTable(head, rowsHtml);
  wireExpandableRows(body);
}

/* =========================================================================
   ROW 7 — MARKET INTELLIGENCE. Ranked headlines with interpretation, led by
   the live impact chain. Not a headline dump.
   ========================================================================= */
function renderNews(groups) {
  updateNewsTabCounts(groups);
  const group = state.newsGroup;
  const preferred = group === 'high-impact'
    ? highImpactNews(groups)
    : dedupeNewsItems(Array.isArray(groups[group]) ? groups[group] : []).filter(isTradeRelevantNews);
  const fetched = Number(state.dashboard?.news?.fetched_count || 0);
  const fallback = fetched === 0
    ? ([groups.big_news, groups.latest, state.dashboard?.news?.articles].find((items) => Array.isArray(items) && items.length) || [])
    : [];
  // Market Drivers already carries the top stories with Means/Do — exclude
  // them so this section is "the rest of the tape", not a second copy.
  const engineDrivers = (state.dashboard?.market?.market_regime_engine?.marketDrivers) || [];
  const driverKeys = new Set(engineDrivers.map((d) => String(d.url || d.title || '').trim().toLowerCase()).filter(Boolean));
  const basePool = preferred.length ? preferred : fallback;
  const deduped = driverKeys.size
    ? basePool.filter((it) => !driverKeys.has(String(it.url || it.title || it.headline || '').trim().toLowerCase()))
    : basePool;
  // Relevance first, then the newest within it: a day-old item no longer sits
  // above an hour-old one of the same relevance.
  const rank = { high: 2, med: 1, low: 0 };
  const ts = (it) => { const t = new Date(it.published || 0).getTime(); return Number.isFinite(t) ? t : 0; };
  const items = (deduped.length >= 3 ? deduped : basePool)
    .map((it, i) => ({ it, i, r: rank[newsImpactLevel(effectiveNewsImpact(it))] || 0, t: ts(it) }))
    .sort((a, b) => (b.r - a.r) || (b.t - a.t) || (a.i - b.i))
    .map((x) => x.it)
    .slice(0, 12);

  const meta = $('#news-meta');
  // The fetch total, before de-duplication and the relevance filter, so it is
  // labelled as such and not read as the sum of the tab counts.
  if (meta) meta.textContent = fetched ? `${fetched} fetched` : 'Sources';

  const bodyEl = $('#news-body');
  if (!bodyEl) return;

  if (!items.length) {
    const sources = (state.dashboard?.news?.sources || []).slice(0, 4);
    const groupLabel = group === 'high-impact' ? 'high-impact' : titleLabel(group);
    const feedStatus = state.dashboard?.news?.source_status || [];
    const feedsDown = feedStatus.length > 0 && !feedStatus.some((f) => f && f.ok);
    const emptyTitle = feedsDown ? 'News feeds unreachable'
      : (group === 'earnings' ? 'No near-term earnings headlines' : `No ${groupLabel} headlines detected`);
    const emptyText = feedsDown
      ? 'None of the news sources answered, so headline risk is unknown — not quiet.'
      : (fetched ? `The source ledger is active, but no relevant ${groupLabel} catalysts were found this cycle.` : 'No relevant catalysts were found in the articles fetched.');
    bodyEl.innerHTML = `<div class="q-empty"${feedsDown ? ' data-news-state="unreachable"' : ''}>
      <strong>${escapeHtml(emptyTitle)}</strong>
      <p>${escapeHtml(emptyText)}</p>
      <div class="q-empty-actions">
        <button class="q-btn" type="button" id="retry-news-refresh">Refresh feeds</button>
        ${sources.map((s) => (safeUrl(s.url) ? `<a class="q-btn" href="${escapeHtml(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.name || '')}</a>` : '')).join('')}
      </div>
    </div>`;
    $('#retry-news-refresh')?.addEventListener('click', () => loadDashboard(true));
    return;
  }

  // The impact chain leads the What changed panel; it is not repeated here.
  const chainHtml = '';

  const VISIBLE = 3;
  const itemsHtml = items.map((item, idx) => {
    const impact = effectiveNewsImpact(item);
    const relClass = newsImpactLevel(impact);
    const relLabel = { high: 'High', med: 'Med', low: 'Low' }[relClass];
    const time = formatRelativeTime(item.published);
    const label = item.catalyst_label || titleLabel(item.category || group);
    const ticker = relatedTickerFromHeadline(item);
    const hidden = idx >= VISIBLE ? ' hidden' : '';
    return `<a class="q-news q-news-extra-${idx >= VISIBLE ? '1' : '0'}" href="${escapeHtml(safeUrl(item.url) || '#')}" target="_blank" rel="noopener noreferrer"${hidden}>
      <span class="q-news-rel ${relClass}" title="Keyword relevance, not a measured market-impact score">
        <b>${relLabel}</b><i>relevance</i>
      </span>
      <span>
        <span class="q-news-h">${escapeHtml(cleanHeadline(item.title || item.headline || ''))}</span>
        <span class="q-news-m">
          <span class="cat">${escapeHtml(String(label).toUpperCase())}</span>
          ${ticker ? `<span class="tkr">${escapeHtml(ticker)}</span>` : ''}
          <span>${escapeHtml(item.source || '')}</span>
          ${time ? `<span>${escapeHtml(time)}</span>` : ''}
          ${!preferred.length ? '<span>Fallback feed</span>' : ''}
        </span>
        <span class="q-news-x">
          <span><i>Means</i>${escapeHtml(newsMeaning(item))}</span>
        </span>
      </span>
    </a>`;
  }).join('');

  const extra = Math.max(0, items.length - VISIBLE);
  bodyEl.innerHTML = chainHtml + itemsHtml
    + (extra ? `<button type="button" class="q-more" id="news-view-more">Show ${extra} more ${extra === 1 ? 'headline' : 'headlines'}</button>` : '');
  const moreBtn = $('#news-view-more');
  if (moreBtn) {
    moreBtn.addEventListener('click', () => {
      $$('.q-news-extra-1', bodyEl).forEach((el) => el.removeAttribute('hidden'));
      moreBtn.remove();
    });
  }
}

/* =========================================================================
   DATA HEALTH — the trust indicator lives in the top bar (X). The chip shows
   quality at a glance; the popover carries the per-source ledger.
   ========================================================================= */
const FEED_STATE_LABEL = { live: 'Live', stale: 'Stale', empty: 'No items', error: 'Unavailable' };

function feedAgeText(hours) {
  if (hours == null || !Number.isFinite(Number(hours))) return '';
  const h = Number(hours);
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) return `${Math.round(h)}h`;
  return `${Math.round(h / 24)}d`;
}

// The ledger lists the feeds the backend actually polled, with the state the
// backend assigned (live = a dated article inside the freshness window). It
// used to list display links and join them to feeds by name, so a working
// "MarketWatch Top Stories" feed showed as a dead "MarketWatch".
function renderSourceLedger(news) {
  const body = $('#source-ledger-body');
  const feeds = ((news && news.source_status) || []).map((f) => ({
    ...f,
    state: f.state || (f.ok ? 'live' : (Number(f.items || 0) > 0 ? 'stale' : 'error')),
  }));
  const chipValue = $('#data-health-value');
  const chipDot = $('#data-health-dot');

  if (!feeds.length) {
    if (chipValue) chipValue.textContent = 'unavailable';
    if (chipDot) chipDot.className = 'q-health-dot warn';
    if (body) body.innerHTML = '<div class="q-empty">No news feed status was returned.</div>';
    return;
  }

  const live = feeds.filter((f) => f.state === 'live').length;
  const ratio = live / feeds.length;
  const pct = Math.round(ratio * 100);
  const tone = ratio >= 0.8 ? 'bull' : ratio >= 0.5 ? 'warn' : 'bear';
  const fetched = Number((news && news.fetched_count) || 0);
  const updated = state.lastUpdated ? formatRelativeTime(state.lastUpdated) : 'this refresh';

  if (chipValue) chipValue.textContent = `${pct}%`;
  if (chipDot) chipDot.className = `q-health-dot ${tone}`;
  const toggle = $('#data-health-toggle');
  if (toggle) toggle.title = `News feeds ${live} of ${feeds.length} live · ${fetched} recent articles · updated ${updated}`;
  if (!body) return;

  const down = feeds.length - live;
  const warn = ratio < 0.8
    ? `<div class="q-src-warn">${down} of ${feeds.length} feed${feeds.length === 1 ? '' : 's'} not live — headline coverage is partial. Prices are unaffected.</div>`
    : '';
  body.innerHTML = `
    <div class="q-src-head">
      <strong>News feeds ${pct}%</strong>
      <span>${live}/${feeds.length} live · ${fetched} recent articles</span>
    </div>
    ${warn}
    <div class="q-src-list">
      ${feeds.map((f) => {
        const detail = f.state === 'live' || f.state === 'stale'
          ? (feedAgeText(f.age_hours) ? `${feedAgeText(f.age_hours)} ago` : 'undated')
          : (f.error || FEED_STATE_LABEL[f.state] || '');
        return `<div class="q-src-row is-${escapeHtml(f.state)}" title="${escapeHtml(f.url || '')}">
          <i aria-hidden="true"></i><span>${escapeHtml(f.name || '')}</span>
          <b>${escapeHtml(FEED_STATE_LABEL[f.state] || f.state)}${detail ? ` · ${escapeHtml(detail)}` : ''}</b>
        </div>`;
      }).join('')}
    </div>
    <div class="q-src-foot">Live = newest article under 72h old. Updated ${escapeHtml(updated || 'now')}. News coverage never affects price or technical data.</div>`;
}


function renderRegimeDebugPanel(market = {}, news = {}, intel = {}) {
  if (typeof document === 'undefined') return;
  let panel = document.getElementById('dev-debug');
  if (!isDebugMode()) {
    if (panel) panel.remove();
    return;
  }
  const model = computeRegimeModel(market, news, intel);
  const dbg = model.debug || {};
  const scores = dbg.factorScores || {};
  const raw = dbg.rawInputs || {};
  if (!panel) {
    panel = document.createElement('section');
    panel.id = 'dev-debug';
    panel.className = 'cc-debug-panel';
    const host = document.getElementById('view-dashboard') || document.body;
    host.appendChild(panel);
  }
  const row = (k, v) => `<div class="cc-debug-row"><span>${escapeHtml(k)}</span><b>${escapeHtml(String(v))}</b></div>`;
  const overrides = (model.overrides || []);
  const warnings = (model.riskWarnings || []);
  panel.innerHTML = `
    <header class="cc-debug-head">
      <strong>Regime Debug</strong>
      <span>dev-only · ?debug=1</span>
    </header>
    <div class="cc-debug-grid">
      <div class="cc-debug-col">
        <h4>Final regime</h4>
        ${row('Regime', model.label)}
        ${row('Posture', model.posture)}
        ${row('Model signal', model.modelSignal)}
        ${row('Tone', model.postureTone)}
        ${row('Confidence', model.confidenceScore != null ? model.confidenceScore + '%' : 'n/a')}
        ${row('Score sum', dbg.scoreSum != null ? dbg.scoreSum : model.net)}
        ${row('Headline', model.headline || '(fallback)')}
      </div>
      <div class="cc-debug-col">
        <h4>Factor scores</h4>
        ${Object.keys(scores).map((k) => row(k, scores[k])).join('') || '<em>none</em>'}
      </div>
      <div class="cc-debug-col">
        <h4>Raw inputs</h4>
        ${Object.keys(raw).map((k) => row(k, raw[k] == null ? '—' : raw[k])).join('') || '<em>none</em>'}
        ${row('News net', dbg.newsNetDirection || 'n/a')}
        ${row('News high-impact', dbg.newsHighImpact != null ? dbg.newsHighImpact : 0)}
        ${row('Stale', dbg.stale ? 'YES' : 'no')}
        ${row('Conflicts', dbg.conflicts != null ? dbg.conflicts : 0)}
      </div>
      <div class="cc-debug-col">
        <h4>Overrides fired (${overrides.length})</h4>
        ${overrides.length ? overrides.map((o) => `<div class="cc-debug-flag">${escapeHtml(o)}</div>`).join('') : '<em>none — score-driven</em>'}
        <h4 style="margin-top:10px">Risk warnings (${warnings.length})</h4>
        ${warnings.length ? warnings.map((w) => `<div class="cc-debug-warn">${escapeHtml(w)}</div>`).join('') : '<em>none</em>'}
      </div>
    </div>`;
}

// [ICONS] Shared inline SVG icon set for the dashboard brief / signal sections.
const QIco = {
  target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="1.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22.5" y2="12"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="20" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="4" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>',
  flat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="7.5" r="0.6" fill="currentColor" stroke="none"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2l8 3v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5z"/></svg>',
};

// [PURE] Confirmed / Neutral / Warning / Opposed counts from the factor model.
// Direction-aware: a factor that pushes WITH the posture is "confirmed", one
// that pushes against it is "opposed". Used by the Signal Check header chips.
// Tally the factors by the status the engine itself assigns to each score
// (+1/+2 Confirmed, 0 Neutral, -1 Warning, -2 Critical = opposed). The four
// counts partition the factors, so they always sum to the total and agree with
// the bar colours and the API. The old tally was posture-relative: under a
// defensive call the green "confirm" chip counted the BEARISH factors, and a
// -2 counted as both confirm and warning (5+3+3+2 shown for 10 factors).
/* =========================================================================
   OVERVIEW CONTEXT (October 2026). Your book against the call, what changed
   since the last session (and the story the tape is testing), what would
   change the call, sectors, key levels and the expected move, cross-asset
   gauges, the dated week and index futures.
   Market context loads after the dashboard (/api/market-context), so the
   market read never waits on it. The book reads My Portfolio's own cached
   view with GETs only; nothing here signs in, refreshes or trades.
   ========================================================================= */

// [PURE] A report's timing in words: the broker writes am / pm, other feeds
// bmo / amc. Anything else is not stated.
function earningsTimingWords(t) {
  const s = String(t || '').trim().toLowerCase();
  if (s === 'am' || s === 'bmo' || s === 'pre' || s === 'before open') return 'before the open';
  if (s === 'pm' || s === 'amc' || s === 'post' || s === 'after close') return 'after the close';
  return '';
}

// [PURE] "30-55% gross" -> [30, 55].
function parseBand(band) {
  const m = /(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*%/.exec(String(band || ''));
  return m ? [Number(m[1]), Number(m[2])] : null;
}

// [PURE] Calendar days from one ISO date to another (noon UTC, no DST edges).
function daysFromTo(fromYmd, toYmd) {
  const a = Date.parse(`${String(fromYmd).slice(0, 10)}T12:00:00Z`);
  const b = Date.parse(`${String(toYmd).slice(0, 10)}T12:00:00Z`);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 864e5) : null;
}

// [PURE] The book against the posture's band, today's P&L, and the holdings
// that report or move this week. Every figure is the broker's or missing.
function bookVsCall(pf, band, intel = {}, today = null) {
  if (!pf || !pf.overview) return null;
  const o = pf.overview;
  const total = numOrNull(o.total_value);
  const invested = numOrNull(o.invested_value);
  const pct = total && invested != null ? (invested / total) * 100 : null;
  const b = parseBand(band);
  let position = null;
  let gap = null;
  if (pct != null && b) {
    if (pct > b[1]) { position = 'above'; gap = pct - b[1]; } else if (pct < b[0]) { position = 'below'; gap = b[0] - pct; } else position = 'inside';
  }
  const ahead = mkt('CALENDAR_DAYS_AHEAD');
  const positions = Array.isArray(pf.positions) ? pf.positions : [];
  const reporting = positions.map((p) => {
    const br = p.broker || {};
    const d = br.earnings_date ? String(br.earnings_date).slice(0, 10) : null;
    const days = d && today ? daysFromTo(today, d) : null;
    return { symbol: p.symbol, date: d, days, timing: br.earnings_timing || null, verified: br.earnings_verified, weight: numOrNull(br.weight) };
  }).filter((r) => r.days != null && r.days >= 0 && Number.isFinite(ahead) && r.days <= ahead)
    .sort((a, b2) => a.days - b2.days);
  const moverMap = new Map([...(intel.movers_up || []), ...(intel.movers_down || [])].map((m) => [String(m.symbol || '').toUpperCase(), m]));
  const movers = positions.filter((p) => moverMap.has(String(p.symbol || '').toUpperCase()))
    .map((p) => ({ symbol: p.symbol, change_pct: numOrNull(moverMap.get(String(p.symbol).toUpperCase()).change_pct) }));
  return {
    invested: pct, band: b, position, gap, holdings: positions.length,
    dayChange: numOrNull(o.day_change), dayChangePct: numOrNull(o.day_change_pct), reporting, movers,
  };
}

// [PURE] Reports still ahead this week (today included). The table keeps ten;
// the count is the whole list's.
function earningsAhead(intel = {}) {
  const n = numOrNull(intel.earnings_week_count);
  return n != null ? n : (intel.earnings_week || []).length;
}
// [PURE] What the earnings count covers, in a few words.
function earningsScopeShort(intel = {}) {
  return /over \$/.test(String((intel || {}).earnings_scope || '')) ? 'next 7 days, large caps and tracked names' : 'next 7 days, tracked universe';
}
// [PURE] A company name in the case people write it: "HCL TECHNOLOGIES LTD"
// reads "HCL Technologies Ltd". Short all-caps words stay as acronyms.
const LOWER_WORDS = new Set(['AND', 'OF', 'THE', 'FOR', 'IN']);
const TITLE_WORDS = new Set(['LTD', 'INC', 'CO', 'CORP', 'PLC']);
function companyName(name) {
  const n = String(name || '').trim();
  if (!n || n !== n.toUpperCase() || !/[A-Z]{4,}/.test(n)) return n;
  return n.split(/\s+/).map((w, i) => {
    if (LOWER_WORDS.has(w) && i > 0) return w.toLowerCase();
    if (w.length <= 3 && !TITLE_WORDS.has(w.replace(/\W/g, ''))) return w;
    return w[0] + w.slice(1).toLowerCase();
  }).join(' ');
}

// [PURE] A listing's symbol without its exchange suffix (RELIANCE.NS -> RELIANCE).
const shownSymbol = (sym) => String(sym || '').replace(/\.(NS|BO)$/i, '');
// [PURE] The market's index quotes: the participation members when known,
// else every quote that is not volatility, a yield, crypto, a currency or a commodity.
function indexQuotes(market = {}) {
  const quotes = (market.quotes || []).filter((q) => numOrNull(q.change_pct) != null);
  const members = ((market.participation || {}).members || []).map((m) => String(m.symbol || '').toUpperCase());
  if (members.length) return members.map((sym) => quotes.find((q) => String(q.symbol || '').toUpperCase() === sym)).filter(Boolean);
  return quotes.filter((q) => !isVolIndex(q.symbol) && !isYield(q.symbol, q.label) && !isCryptoSymbol(q.symbol, q.label)
    && !/gold|oil|crude|\/|=x$/i.test(`${q.label || ''} ${q.symbol || ''}`)).slice(0, 4);
}
// [PURE] The price story for a market without the US index funds: the same
// tests, on the market's own indices and its volatility index.
function indexChain(market = {}) {
  const idx = indexQuotes(market).map((q) => ({ label: q.label || q.symbol, ch: Number(q.change_pct) }));
  if (idx.length < 2) return null;
  const spread = Number(thr('MARKET.CHAIN_LEADERSHIP_SPREAD_PCT'));
  const vol = (market.quotes || []).find((q) => isVolIndex(q.symbol) && numOrNull(q.change_pct) != null);
  const volCh = vol ? Number(vol.change_pct) : null;
  const up = idx.every((x) => x.ch > 0);
  const dn = idx.every((x) => x.ch < 0);
  const sorted = idx.slice().sort((a, b) => b.ch - a.ch);
  const lead = up ? sorted[0] : dn ? sorted[sorted.length - 1] : null;
  const even = sorted[0].ch - sorted[sorted.length - 1].ch < spread;
  const chain = up || dn ? {
    label: `${up ? 'Broad advance' : 'Broad decline'}${!even && lead ? `, led by ${lead.label}` : ''}`,
    links: [
      { step: up ? 'Indices rise together' : 'Indices fall together', check: true },
      { step: 'No clear leadership', check: even },
      { step: up ? 'Volatility eases' : 'Volatility rises', check: volCh != null && (up ? volCh < 0 : volCh > 0) },
    ],
  } : {
    label: 'Mixed tape',
    links: [
      { step: 'Indices split', check: true },
      { step: 'No clear leadership', check: even },
      { step: 'Volatility steady', check: volCh != null && Math.abs(volCh) < spread * 2 },
    ],
  };
  chain.confirmed = chain.links.filter((l) => l.check).length;
  chain.total = chain.links.length;
  chain.verified = chain.confirmed >= Math.ceil(chain.total * Number(thr('MARKET.CHAIN_CONFIRM_SHARE')));
  return chain;
}

// [PURE] An index's name from the ribbon's own quotes; the symbol otherwise.
function indexName(sym, market = {}) {
  const q = (market.quotes || []).find((x) => String(x.symbol || '').toUpperCase() === String(sym || '').toUpperCase());
  return (q && q.label) || sym || 'the benchmark';
}
// [PURE] A factor state in one word: scores or the engine's own words.
function factorWord(v) {
  if (typeof v === 'number') return v > 0 ? 'Supportive' : v < 0 ? 'Headwind' : 'Neutral';
  const w = String(v == null ? '' : v).replace(/^an?\s+/i, '');
  return w ? w[0].toUpperCase() + w.slice(1) : '';
}

// [PURE] Live conditions against the model's own thresholds: what would make
// the read stronger, and what would weaken it. A condition whose input or
// threshold is missing is left out rather than shown as met or unmet.
function regimeTriggers(model = {}, levels = {}, part = null, benchName = null) {
  const vix = numOrNull(model.vix);
  const pct = numOrNull(model.breadth);
  const conf = numOrNull(model.confidenceScore);
  const dir = directionScore(model);
  const last = numOrNull(levels.last);
  const s50 = numOrNull(levels.sma50);
  const s200 = numOrNull(levels.sma200);
  const dist = (ma) => (last != null && ma ? ((last / ma) - 1) * 100 : null);
  const t = {
    contained: mkt('VIX_CONTAINED_MAX'), stress: mkt('VIX_STRESS'), broad: mkt('PARTICIPATION_BROAD_PCT'),
    weak: mkt('PARTICIPATION_WEAK_PCT'), confMod: mkt('CONFIDENCE_MODERATE'), bull: mkt('SIGNAL_SCORE_BULL') * 10,
  };
  const bench = benchName || levels.symbol || 'The benchmark';
  // Index participation as a count of funds when the count is known: "3 of 4".
  const n = part && numOrNull(part.measured);
  const above = part && numOrNull(part.above);
  const unit = part && Array.isArray(part.members) && part.members.length && part.members.every((m) => /^\^/.test(String(m.symbol || ''))) ? 'indices' : 'index funds';
  const partLabel = (cut, more) => (n
    ? `${more ? Math.ceil((n * cut) / 100) : Math.floor((n * cut) / 100)} of ${n} ${unit} in uptrends${more ? ' or more' : ' or fewer'}`
    : `Index participation ${fmtNumber(cut, 0)}%${more ? ' or more' : ' or less'}`);
  const partNow = n && above != null ? `${above} of ${n} now` : `${fmtNumber(pct, 0)}% now`;
  const row = (ok, label, now, met) => (ok ? { label, now, met: !!met } : null);
  const strengthen = [
    row(vix != null && Number.isFinite(t.contained), `VIX below ${fmtNumber(t.contained, 0)}`, `${fmtNumber(vix, 1)} now`, vix < t.contained),
    row(pct != null && Number.isFinite(t.broad), partLabel(t.broad, true), partNow, pct >= t.broad),
    row(dist(s50) != null, `${bench} above its 50-day average`, `${fmtPct(dist(s50), 1, false)} from it`, dist(s50) > 0),
    row(conf != null && Number.isFinite(t.confMod), `Confidence ${fmtNumber(t.confMod, 0)} or more`, `${fmtNumber(conf, 0)} now`, conf >= t.confMod),
    row(dir.shown !== '--' && Number.isFinite(t.bull), `Direction score ${fmtNumber(t.bull, 0)} or more`, `${dir.shown} now`, dir.score10 * 10 >= t.bull),
  ].filter(Boolean);
  const weaken = [
    row(vix != null && Number.isFinite(t.stress), `VIX ${fmtNumber(t.stress, 0)} or more`, `${fmtNumber(vix, 1)} now`, vix >= t.stress),
    row(pct != null && Number.isFinite(t.weak), partLabel(t.weak, false), partNow, pct <= t.weak),
    row(dist(s50) != null, `${bench} below its 50-day average`, `${fmtPct(dist(s50), 1, false)} from it`, dist(s50) < 0),
    row(dist(s200) != null, `${bench} below its 200-day average`, `${fmtPct(dist(s200), 1, false)} from it`, dist(s200) < 0),
  ].filter(Boolean);
  return { strengthen, weaken };
}

// [PURE] Posture -> strip tone. Rows recorded before the engine carry only the
// 4-factor tone and are drawn lighter.
const POSTURE_TONE = {
  Offensive: 'bull-3', 'Selective Offensive': 'bull-2', 'Lean Long': 'bull-1', Neutral: 'flat',
  'Lean Defensive': 'bear-1', 'Selective Defensive': 'bear-2', Defensive: 'bear-3',
};
const TONE_LEGACY = { 'Risk-on': 'bull-1', Neutral: 'flat', 'Risk-off': 'bear-1' };
function historyCell(r = {}) {
  if (r.posture) return { tone: POSTURE_TONE[r.posture] || 'flat', legacy: false, label: `${r.date}: ${r.posture}${r.direction != null ? `, direction ${r.direction}` : ''}${r.confidence != null ? `, confidence ${r.confidence}%` : ''}` };
  return { tone: TONE_LEGACY[r.tone] || 'flat', legacy: true, label: `${r.date}: ${r.tone || 'no read'} (4-factor read)` };
}

// [PURE] The sector tiles in heat order, each with a 0-1 intensity.
function sectorHeat(sectors = []) {
  const rows = sectors.filter((s) => numOrNull(s.change_pct) != null);
  const full = mkt('SECTOR_HEAT_FULL_PCT');
  const cap = Number.isFinite(full) && full > 0 ? full : Math.max(...rows.map((s) => Math.abs(s.change_pct)), 1);
  return rows.slice().sort((a, b) => b.change_pct - a.change_pct)
    .map((s) => ({ ...s, heat: Math.min(1, Math.abs(s.change_pct) / cap) }));
}

// [PURE] One week of dated events: macro releases with their times, earnings
// in the tracked universe, and the holdings' own reports with their timing.
function weekEvents(today, macro = [], earnings = [], held = []) {
  const ahead = mkt('CALENDAR_DAYS_AHEAD');
  const n = Number.isFinite(ahead) ? ahead : 7;
  const days = Array.from({ length: n }, (_, i) => {
    const d = new Date(Date.parse(`${today}T12:00:00Z`) + i * 864e5).toISOString().slice(0, 10);
    return { date: d, items: [] };
  });
  const at = (d) => days.find((x) => x.date === String(d || '').slice(0, 10));
  macro.forEach((e) => { const day = at(e.date); if (day) day.items.push({ kind: 'macro', macroKind: e.kind, title: e.title, time: e.time, source: e.source, url: e.url }); });
  const heldBy = new Map(held.map((h) => [String(h.symbol || '').toUpperCase(), h]));
  const rowBy = new Map(earnings.map((r) => [String(r.symbol || '').toUpperCase(), r]));
  const seen = new Set();
  const info = (r = {}) => ({ name: r.name || null, cap: numOrNull(r.market_cap), eps: numOrNull(r.eps_estimate) });
  held.forEach((h) => {
    const day = at(h.date);
    const sym = String(h.symbol || '').toUpperCase();
    if (day) { day.items.push({ kind: 'earnings', symbol: h.symbol, timing: h.timing || (rowBy.get(sym) || {}).earnings_timing || null, held: true, ...info(rowBy.get(sym)) }); seen.add(`${sym}|${h.date}`); }
  });
  earnings.forEach((r) => {
    const sym = String(r.symbol || '').toUpperCase();
    const d = String(r.earnings_date_pretty || r.earnings_date || '').slice(0, 10);
    if (!sym || seen.has(`${sym}|${d}`)) return;
    seen.add(`${sym}|${d}`);
    const day = at(d);
    if (day) day.items.push({ kind: 'earnings', symbol: sym, timing: (heldBy.get(sym) || {}).timing || r.earnings_timing || null, held: heldBy.has(sym), ...info(r) });
  });
  // Within a day: releases by time, then your holdings, then the largest companies.
  days.forEach((x) => x.items.sort((a, b) => (a.kind !== b.kind ? (a.kind === 'macro' ? -1 : 1)
    : a.kind === 'macro' ? String(a.time || '99').localeCompare(String(b.time || '99'))
      : (b.held ? 1 : 0) - (a.held ? 1 : 0) || (b.cap || 0) - (a.cap || 0))));
  return days;
}

/* -- loaders --------------------------------------------------------------- */
async function loadMarketContext(market = state.market) {
  state.marketContext = state.marketContext || {};
  try {
    // A server started before this read existed answers 404: that is a restart,
    // not a provider failure, and the panels say so.
    const head = await fetch(`/api/market-context?market=${encodeURIComponent(market)}`, { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' });
    if (head.status === 404) {
      if (market === state.market) { state.marketContext[market] = { available: false, reason: 'restart' }; renderMarketContext(); }
      return;
    }
    if (!head.ok) throw new Error(`HTTP ${head.status}`);
    const data = await head.json();
    if (market !== state.market) return;
    state.marketContext[market] = data;
    // Something missed the server's time budget: read once more shortly.
    if (data && Array.isArray(data.missing) && data.missing.length && !loadMarketContext.retried) {
      loadMarketContext.retried = true;
      setTimeout(() => { loadMarketContext.retried = false; if (market === state.market) loadMarketContext(market); }, 30000);
    }
  } catch (err) {
    if (market !== state.market) return;
    state.marketContext[market] = { available: false, error: friendlyFetchError(err) || 'unavailable' };
  }
  renderMarketContext();
}

async function loadBook() {
  state.book = { s: 'loading' };
  renderBookPanel();
  const get = async (path) => {
    try {
      const r = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' });
      return r.ok ? await r.json() : null;
    } catch (_) { return null; }
  };
  const status = await get('/api/portfolio/status');
  const st = status && status.state;
  if (!status || !(st === 'connected' || st === 'partial') || !status.account) {
    state.book = { s: 'off', status };
    renderBookPanel();
    return;
  }
  const view = await get('/api/portfolio/');
  state.book = view && view.portfolio ? { s: 'ok', pf: view.portfolio } : { s: 'off', status };
  renderBookPanel();
  renderWeekCalendar();
}

function renderMarketContext() {
  // A refresh that brings the same data repaints nothing: the context panels
  // are rebuilt only when what they show has changed.
  const d = state.dashboard || {};
  const intel = d.market_intel || {};
  let sig = null;
  try {
    sig = JSON.stringify([state.market, marketContext(), d.market || {}, intel, state.book || null,
      exchangeSession().today]);
  } catch (_) { sig = null; }
  if (sig && sig === state.ctxSig && document.querySelector('#sector-body > *')) return;
  state.ctxSig = sig;
  if (state.dashboard) renderBreadthPanel(state.dashboard.market || {}, state.dashboard.market_intel || {});
  renderSectorMap();
  renderKeyLevels();
  renderCrossAsset();
  renderWeekCalendar();
  renderTriggers();
  renderFutures();
}
const marketContext = () => (state.marketContext || {})[state.market] || null;
// [PURE] The one line a context panel shows when the read is not there.
function contextNotice(ctx) {
  if (!ctx) return null;
  if (ctx.available !== false) return null;
  return ctx.reason === 'restart'
    ? 'This read arrives with the latest QUANTA server. Restart QUANTA (start.ps1) to load sectors, levels and cross-asset gauges.'
    : `Market context could not be read${ctx.error ? ` (${ctx.error})` : ''}. It retries with the next refresh.`;
}
const hideAmounts = () => { try { return localStorage.getItem('quanta.holdings.hideAmounts') === '1'; } catch (_) { return false; } };

/* -- your book against the call ------------------------------------------- */
function renderBookPanel() {
  const body = $('#book-body');
  if (!body) return;
  const book = state.book || { s: 'loading' };
  if (book.s === 'loading') { body.innerHTML = '<div class="q-empty q-empty--inline">Reading your portfolio…</div>'; return; }
  if (book.s !== 'ok') {
    body.innerHTML = `<div class="q-bookoff"><p>Connect My Portfolio to see your book against today's call: invested against the band, today's change, and holdings with events this week. Read-only.</p>
      <a class="q-btn q-btn--sm" href="#holdings" data-view-link="holdings">Open My Portfolio</a></div>`;
    return;
  }
  const d = state.dashboard || {};
  const model = computeRegimeModel(d.market || {}, d.news || {}, d.market_intel || {});
  const today = exchangeSession().today;
  const r = bookVsCall(book.pf, model.exposure && model.exposure.band, d.market_intel || {}, today);
  if (!r) { body.innerHTML = '<div class="q-empty q-empty--inline">The portfolio view carries no overview yet.</div>'; return; }
  const hidden = hideAmounts();
  const money = (v) => (hidden ? '••••' : `${v < 0 ? '-' : v > 0 ? '+' : ''}$${fmtNumber(Math.abs(v), 2)}`);
  const pos = r.invested == null ? null : Math.max(0, Math.min(100, r.invested));
  const tone = r.position === 'inside' ? 'bull' : r.position ? 'warn' : 'neutral';
  const verdict = r.position == null ? 'No band to compare with'
    : r.position === 'inside' ? `Inside the ${r.band[0]}-${r.band[1]}% band`
      : `${fmtNumber(r.gap, 0)} points ${r.position} the ${r.band[0]}-${r.band[1]}% band`;
  const gauge = pos == null ? '' : `<div class="q-bookbar" role="img" aria-label="${escapeHtml(`Invested ${fmtNumber(r.invested, 0)}% of the account. ${verdict}.`)}">
      ${r.band ? `<i class="q-bookbar-band" style="left:${r.band[0]}%;width:${Math.max(0, r.band[1] - r.band[0])}%"></i>` : ''}
      <b class="q-bookbar-mark ${tone}" style="left:${pos}%"></b>
    </div>
    <div class="q-bookbar-axis" aria-hidden="true"><span>0%</span><span>50%</span><span>100%</span></div>`;
  const rep = r.reporting.length
    ? r.reporting.slice(0, 4).map((x) => `<span class="q-chipline"><b class="hp-sym">${escapeHtml(x.symbol)}</b> ${escapeHtml(x.days === 0 ? 'today' : x.days === 1 ? 'tomorrow' : `in ${x.days} days`)}${earningsTimingWords(x.timing) ? ` · ${escapeHtml(earningsTimingWords(x.timing))}` : ''}</span>`).join('')
      + (r.reporting.length > 4 ? `<span class="q-chipline muted">+${r.reporting.length - 4} more</span>` : '')
    : '<span class="muted">None of your holdings report in the next week</span>';
  const mov = r.movers.length
    ? r.movers.slice(0, 4).map((m) => `<span class="q-chipline"><b class="hp-sym">${escapeHtml(m.symbol)}</b> <span class="${m.change_pct > 0 ? 'bull' : m.change_pct < 0 ? 'bear' : ''}">${m.change_pct == null ? 'N/A' : fmtPct(m.change_pct, 1, false)}</span></span>`).join('')
    : '<span class="muted">None of your holdings are among the largest movers</span>';
  body.innerHTML = `<div class="q-bookgrid">
      <div class="q-bookhead">
        <span class="q-conf-cap">Invested</span>
        <span class="q-measure-v ${tone === 'warn' ? 'warn' : ''}">${r.invested == null ? 'N/A' : `${fmtNumber(r.invested, 0)}%`}</span>
        <span class="q-measure-s ${tone}">${escapeHtml(verdict)}</span>
      </div>
      ${gauge}
    </div>
    <dl class="q-dl q-bookdl">
      <dt>Today</dt><dd class="${r.dayChange > 0 ? 'bull' : r.dayChange < 0 ? 'bear' : ''}">${r.dayChange == null ? '<span class="q-na">N/A</span>' : `${money(r.dayChange)}${r.dayChangePct != null && !hidden ? ` (${fmtPct(r.dayChangePct, 2, false)})` : ''}`} <span class="muted">across ${r.holdings} holdings</span></dd>
      <dt>Reporting</dt><dd class="q-chiprow">${rep}</dd>
      <dt>Moving today</dt><dd class="q-chiprow">${mov}</dd>
    </dl>`;
}

/* -- what changed, and the story the tape is testing ---------------------- */
function renderWhatChanged(market = {}, news = {}, intel = {}) {
  const body = $('#changed-body');
  if (!body) return;
  const meta = $('#changed-meta');
  const short = (ymd) => { const t = Date.parse(`${String(ymd).slice(0, 10)}T12:00:00Z`); return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : String(ymd || ''); };

  // 1. How prices moved: the index funds as bars, then the story's checks.
  const chain = impactChain(market, news, intel);
  const idx = indexQuotes(market);
  const span = Math.max(...idx.map((q) => Math.abs(Number(q.change_pct))), 0.5);
  void idx;
  const bars = idx.length ? `<div class="q-moves" role="img" aria-label="${escapeHtml(idx.map((q) => `${q.label || q.symbol} ${fmtPct(q.change_pct, 2, false)}`).join(', '))}">
      ${idx.map((q) => { const c = Number(q.change_pct); const w = (Math.abs(c) / span) * 50; const tone = c > 0 ? 'up' : c < 0 ? 'dn' : 'fl';
        return `<span class="q-move-l">${escapeHtml(q.label || q.symbol)}</span><span class="q-move-b"><i class="${tone}" style="left:${c >= 0 ? 50 : 50 - w}%;width:${Math.max(w, 0.8).toFixed(2)}%"></i></span><span class="q-move-v ${c > 0 ? 'bull' : c < 0 ? 'bear' : ''}">${fmtPct(c, 2, false)}</span>`; }).join('')}
    </div>` : '';
  const story = chain && chain.links && chain.links.length ? `<section class="q-wc-sec">
      <h3 class="q-wc-h">How prices moved today <span class="q-wc-tag">${escapeHtml(chain.label)}</span></h3>
      <ul class="q-checks">${chain.links.map((l) => `<li class="${l.check ? 'ok' : ''}"><span class="q-check" aria-hidden="true">${l.check ? '✓' : ''}</span>${escapeHtml(l.step)}<span class="sr-only">${l.check ? ': agrees with prices' : ': not seen in prices'}</span></li>`).join('')}</ul>
      <p class="q-wc-cap">${chain.confirmed} of ${chain.total} checks agree with prices${chain.verified ? '.' : ': a working idea, not a confirmed story.'}</p>
    </section>` : '';

  // 2. What the model changed since its last recorded session, was and now.
  const hist = (Array.isArray(market.regime_history) ? market.regime_history : []).filter((h) => h && (h.posture || h.tone));
  const today = exchangeSession().today;
  const prior = hist.filter((h) => h.date && h.date < (today || '9999')).pop();
  const changes = Array.isArray(market.regime_changes) ? market.regime_changes : [];
  const fmtState = (c, v) => (c.kind === 'direction' ? `${v} of 100`
    : c.kind === 'factor' ? (typeof v === 'number' ? `${factorWord(v)} (${v > 0 ? '+' : ''}${v})` : factorWord(v)) : String(v == null ? '' : v));
  const changeRows = changes.map((c) => {
    const word = c.tone === 'bull' ? 'Stronger' : c.tone === 'bear' ? 'Weaker' : 'Changed';
    const has = c.label && c.from != null && c.to != null;
    return `<tr class="${escapeHtml(c.tone || 'neutral')}">${has
      ? `<th scope="row">${escapeHtml(c.label)}</th><td>${escapeHtml(fmtState(c, c.from))}</td><td><span class="q-wc-arrow" aria-hidden="true">→</span>${escapeHtml(fmtState(c, c.to))}</td>`
      : `<th scope="row" colspan="3">${escapeHtml(String(c.text || ''))}</th>`}<td class="q-wc-dir"><span>${c.tone === 'bull' ? '▲' : c.tone === 'bear' ? '▼' : '•'}</span> ${word}</td></tr>`;
  }).join('');
  const since = prior ? `since ${short(prior.date)}` : 'since the last session';
  const changed = `<section class="q-wc-sec">
      <h3 class="q-wc-h">What the model changed ${escapeHtml(since)}</h3>
      ${market.regime_baseline ? '<p class="q-note-block">This is the first recorded session for this market; changes show from the next one.</p>'
        : changes.length ? `<table class="q-wc-tbl"><thead><tr><th scope="col">Measure</th><th scope="col">Was</th><th scope="col">Now</th><th scope="col"><span class="sr-only">Effect on the read</span></th></tr></thead><tbody>${changeRows}</tbody></table>`
          : '<p class="q-note-block">Nothing in the model changed: the same posture, direction and factor reads as the last session.</p>'}
    </section>`;

  // 3. The model's call over the recorded sessions, dated.
  let strip = '';
  if (hist.length) {
    const cells = hist.map(historyCell);
    const side = (h) => { const t = (historyCell(h).tone || ''); return t.startsWith('bull') ? 'off' : t.startsWith('bear') ? 'def' : 'neu'; };
    const n = { off: 0, neu: 0, def: 0 };
    hist.forEach((h) => { n[side(h)] += 1; });
    const lastH = hist[hist.length - 1];
    const nowWord = lastH.posture || lastH.tone || 'no read';
    const caption = `Offensive side on ${n.off} of ${hist.length} sessions, neutral on ${n.neu}, defensive on ${n.def}. Latest: ${nowWord}.`;
    const mid = hist[Math.floor((hist.length - 1) / 2)];
    strip = `<section class="q-wc-sec q-hist">
        <h3 class="q-wc-h">The model's call, last ${hist.length} sessions</h3>
        <div class="q-hist-strip" role="img" aria-label="${escapeHtml(caption)}">${cells.map((c, i) => `<i class="t-${c.tone}${c.legacy ? ' is-legacy' : ''}${i === cells.length - 1 ? ' is-last' : ''}" title="${escapeHtml(c.label)}"></i>`).join('')}</div>
        <div class="q-hist-axis" aria-hidden="true"><span>${escapeHtml(short(hist[0].date))}</span>${hist.length > 4 ? `<span>${escapeHtml(short(mid.date))}</span>` : ''}<span>${escapeHtml(short(lastH.date))}</span></div>
        <p class="q-wc-cap">${escapeHtml(caption)}</p>
        <div class="q-hist-key" aria-hidden="true"><span><i class="t-bull-2"></i>Offensive</span><span><i class="t-flat"></i>Neutral</span><span><i class="t-bear-2"></i>Defensive</span>${cells.some((c) => c.legacy) ? '<span><i class="t-flat is-legacy"></i>Earlier 4-factor read</span>' : ''}</div>
      </section>`;
  }
  if (meta) meta.textContent = market.regime_baseline ? 'First session' : `${changes.length} model change${changes.length === 1 ? '' : 's'}`;
  body.innerHTML = `<div class="q-wc">${story}${changed}${strip}</div>`;
}

/* -- what would change the call -------------------------------------------- */
function renderTriggers() {
  const body = $('#triggers-body');
  if (!body) return;
  const d = state.dashboard || {};
  const market = d.market || {};
  const model = computeRegimeModel(market, d.news || {}, d.market_intel || {});
  const ctx = marketContext();
  const levels = (ctx && ctx.levels) || {};
  const t = regimeTriggers(model, levels, market.participation || null, levels.symbol ? indexName(levels.symbol, market) : null);
  const meta = $('#triggers-meta');
  const metS = t.strengthen.filter((r) => r.met).length;
  const metW = t.weaken.filter((r) => r.met).length;
  if (meta) meta.textContent = '';
  // A meter per side: one segment per condition, filled when it holds now.
  const meter = (list, bad) => `<span class="q-trig-m" aria-hidden="true">${list.map((r) => `<i class="${r.met ? (bad ? 'is-bad' : 'is-met') : ''}"></i>`).join('')}</span>`;
  const rows = (list, bad) => list.map((r) => `<li class="${r.met ? (bad ? 'is-bad' : 'is-met') : ''}">
      <span class="q-trig-dot" aria-hidden="true"></span>
      <span class="q-trig-l">${escapeHtml(r.label)}<span class="sr-only">: ${r.met ? 'holds now' : 'not yet'}</span></span>
      <span class="q-trig-v">${escapeHtml(r.now)}</span></li>`).join('');
  const dir = directionScore(model);
  void dir;
  const lo = mkt('SIGNAL_SCORE_BEAR') * 10;
  const hi = mkt('SIGNAL_SCORE_BULL') * 10;
  const dNow = dir.shown === '--' ? null : Math.round(dir.score10 * 10);
  const gauge = dNow == null || !Number.isFinite(lo) || !Number.isFinite(hi) ? '' : `<div class="q-trig-g">
      <h3 class="q-trig-h">Direction score <span>${dNow} of 100</span></h3>
      <div class="q-trig-gt" role="img" aria-label="${escapeHtml(`Direction score ${dNow}. ${fmtNumber(lo, 0)} or less reads bearish, ${fmtNumber(hi, 0)} or more reads bullish.`)}">
        <i class="z-bear" style="width:${lo}%"></i><i class="z-mid" style="left:${lo}%;width:${hi - lo}%"></i><i class="z-bull" style="left:${hi}%;width:${100 - hi}%"></i>
        <b style="left:${Math.max(0, Math.min(100, dNow))}%"></b>
      </div>
      <div class="q-trig-ga" aria-hidden="true"><span style="left:0">0</span><span style="left:${lo}%">${fmtNumber(lo, 0)}</span><span style="left:${hi}%">${fmtNumber(hi, 0)}</span><span style="left:100%">100</span></div>
      <p class="q-wc-cap">${dNow >= hi ? 'In the bullish zone.' : dNow <= lo ? 'In the bearish zone.' : `Between the zones: ${fmtNumber(hi - dNow, 0)} points from a bullish read, ${fmtNumber(dNow - lo, 0)} from a bearish one.`}</p>
    </div>`;
  body.innerHTML = `<div class="q-trig">
      <div><h3 class="q-trig-h">For a stronger read ${meter(t.strengthen, false)}<span>${metS} of ${t.strengthen.length} hold</span></h3><ul>${rows(t.strengthen, false)}</ul></div>
      <div><h3 class="q-trig-h">For a weaker read ${meter(t.weaken, true)}<span>${metW} of ${t.weaken.length} hold</span></h3><ul>${rows(t.weaken, true)}</ul></div>
    </div>${ctx ? '' : '<p class="q-note-block">Index levels load with the market context.</p>'}`;
}

/* -- sectors ---------------------------------------------------------------- */
function renderSectorMap() {
  const body = $('#sector-body');
  if (!body) return;
  const ctx = marketContext();
  const meta = $('#sector-meta');
  if (!ctx) { body.innerHTML = '<div class="q-sbar q-sbar--skel" aria-busy="true">' + '<i></i>'.repeat(11) + '</div>'; return; }
  if (contextNotice(ctx)) { body.innerHTML = `<p class="q-ctx-note">${escapeHtml(contextNotice(ctx))}</p>`; if (meta) meta.textContent = ''; return; }
  const rows = sectorHeat(ctx.sectors || []);
  const sp = ctx.sector_participation || {};
  if (meta) meta.textContent = sp.measured ? `${sp.above} of ${sp.measured} above 50-DMA` : 'No trend read';
  if (!rows.length) { body.innerHTML = '<p class="q-note-block">Sector quotes are unavailable this cycle.</p>'; return; }
  // Ranked, strongest first, each move drawn from one zero line: the spread
  // between leaders and laggards is the read, so magnitude is shown, not tinted.
  const maxAbs = Math.max(...rows.map((x) => Math.abs(x.change_pct)), 0.25);
  body.innerHTML = `<div class="q-sbar" role="list">${rows.map((x) => {
    const dirc = x.change_pct > 0 ? 'up' : x.change_pct < 0 ? 'dn' : 'flat';
    const w = Math.max(0.02, Math.abs(x.change_pct) / maxAbs);
    const trend = x.above_sma50 == null ? '' : x.above_sma50 ? ' is-above' : ' is-below';
    return `<button type="button" role="listitem" class="q-sbar-row ${dirc}${trend}" data-chart-symbol="${escapeHtml(x.symbol)}"
        aria-label="${escapeHtml(`${x.label}: ${fmtPct(x.change_pct, 2, false)} today${x.above_sma50 == null ? '' : `, ${x.above_sma50 ? 'above' : 'below'} its 50-day average`}. Chart it.`)}">
        <span class="q-sbar-n">${escapeHtml(x.label)}</span>
        <span class="q-sbar-t" aria-hidden="true"><i style="--w:${w.toFixed(3)}"></i></span>
        <span class="q-sbar-v">${fmtPct(x.change_pct, 2, false)}</span>
        <span class="q-sbar-ma" aria-hidden="true" title="${x.above_sma50 == null ? '' : x.above_sma50 ? 'Above its 50-day average' : 'Below its 50-day average'}"></span>
      </button>`;
  }).join('')}</div>
    <p class="q-sbar-key"><span><i class="q-sbar-dot is-above" aria-hidden="true"></i>Above its 50-day average</span><span><i class="q-sbar-dot" aria-hidden="true"></i>Below it</span><span>Select a sector to chart it</span></p>`;
  $$('.q-sbar-row', body).forEach((b) => b.addEventListener('click', () => {
    if (typeof state.overviewChartPick === 'function') {
      state.overviewChartPick(b.dataset.chartSymbol);
      $('#overview-chart')?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    }
  }));
  growOnView(body, '.q-sbar-t i', 'sector-bars', rows.map((x) => x.symbol + x.change_pct).join());
}

// Bars grow from their zero line the first time they scroll into view, and
// again only when the values change; never under reduced motion.
function growOnView(root, sel, key, signature) {
  if (!motionOK() || typeof IntersectionObserver === 'undefined') return;
  if (!motionChanged(key, signature).changed) return;
  const bars = [...root.querySelectorAll(sel)];
  if (!bars.length) return;
  const io = new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting)) return;
    io.disconnect();
    bars.forEach((b, i) => b.animate([{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }],
      { duration: 520, easing: EASE_OUT, delay: Math.min(i, 12) * 35, fill: 'backwards' }));
  }, { threshold: 0.3 });
  io.observe(root);
}

/* -- key levels and the expected move -------------------------------------- */
function renderKeyLevels() {
  const body = $('#levels-body');
  if (!body) return;
  const ctx = marketContext();
  const meta = $('#levels-meta');
  if (!ctx) { body.innerHTML = '<div class="q-skelrows" aria-busy="true"><span class="skel-line"></span><span class="skel-line"></span><span class="skel-line"></span></div>'; return; }
  if (contextNotice(ctx)) { body.innerHTML = `<p class="q-ctx-note">${escapeHtml(contextNotice(ctx))}</p>`; if (meta) meta.textContent = ''; return; }
  const L = ctx.levels || {};
  if (meta) meta.textContent = L.symbol || '';
  // The ribbon's quote for the same symbol, so the ruler and the ribbon agree.
  const rq = ((state.dashboard && state.dashboard.market && state.dashboard.market.quotes) || []).find((q) => String(q.symbol || '').toUpperCase() === String(L.symbol || '').toUpperCase());
  const last = numOrNull(rq && rq.price) != null ? numOrNull(rq.price) : numOrNull(L.last);
  if (last == null) { body.innerHTML = '<p class="q-note-block">Benchmark levels are unavailable this cycle.</p>'; return; }
  const em = L.expected_move || null;
  const ses = L.session || null;
  const pr = L.prior || null;
  const bands = [
    em ? { k: 'Expected move', a: em.low, b: em.high, cls: 'em' } : null,
    pr ? { k: 'Prior session', a: pr.low, b: pr.high, cls: 'pr' } : null,
    ses ? { k: 'This session', a: ses.low, b: ses.high, cls: 'se' } : null,
  ].filter(Boolean);
  const vals = bands.flatMap((x) => [x.a, x.b]).concat([last]).filter((v) => Number.isFinite(v));
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1;
  const x = (v) => ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * 100;
  const p2 = (v) => fmtNumber(v, 2);
  const ruler = `<div class="q-ruler" role="img" aria-label="${escapeHtml(`Last ${p2(last)}. ${bands.map((b) => `${b.k} ${p2(b.a)} to ${p2(b.b)}`).join('. ')}.`)}">
      ${bands.map((b) => `<div class="q-ruler-row"><span class="q-ruler-k">${b.k}</span><span class="q-ruler-track"><i class="${b.cls}" style="left:${x(b.a).toFixed(2)}%;width:${Math.max(0.8, x(b.b) - x(b.a)).toFixed(2)}%"></i></span></div>`).join('')}
      <span class="q-ruler-last" style="left:calc(var(--rk) + (100% - var(--rk)) * ${(x(last) / 100).toFixed(4)})"><b>${p2(last)}</b></span>
    </div>`;
  const dist = (ma) => (ma ? fmtPct(((last / ma) - 1) * 100, 1, false) : null);
  body.innerHTML = `${ruler}
    <dl class="q-dl q-levels-dl">
      ${em ? `<dt>Expected move</dt><dd>±${p2(em.points)} (±${fmtNumber(em.pct, 2)}%) <span class="muted">from ${escapeHtml(em.vol_symbol)} ${fmtNumber(em.vol_level, 2)}, one session</span></dd>` : ''}
      ${pr ? `<dt>Prior session</dt><dd>${p2(pr.low)} to ${p2(pr.high)} <span class="muted">close ${p2(pr.close)}</span></dd>` : ''}
      ${L.atr != null ? `<dt>Average range</dt><dd>${p2(L.atr)} <span class="muted">ATR, ${L.atr_sessions} sessions</span></dd>` : ''}
      ${L.sma50 != null ? `<dt>50-day average</dt><dd>${p2(L.sma50)} <span class="${L.above_sma50 ? 'bull' : 'bear'}">${dist(L.sma50)}</span></dd>` : ''}
      ${L.sma200 != null ? `<dt>200-day average</dt><dd>${p2(L.sma200)} <span class="${L.above_sma200 ? 'bull' : 'bear'}">${dist(L.sma200)}</span></dd>` : ''}
    </dl>`;
}

/* -- cross-asset ------------------------------------------------------------ */
function renderCrossAsset() {
  const body = $('#cross-body');
  if (!body) return;
  const ctx = marketContext();
  if (!ctx) { body.innerHTML = '<div class="q-skelrows" aria-busy="true"><span class="skel-line"></span><span class="skel-line"></span><span class="skel-line"></span></div>'; return; }
  if (contextNotice(ctx)) { body.innerHTML = `<p class="q-ctx-note">${escapeHtml(contextNotice(ctx))}</p>`; return; }
  const X = ctx.cross_asset || {};
  const quotes = (X.quotes || []).filter((q) => numOrNull(q.price) != null);
  const reads = X.reads || [];
  // A pair's ratio means nothing on its own (copper over gold is 0.0016): the
  // row shows what today's change says, with the ratio kept in the tooltip.
  const readTone = (r) => (/appetite/.test(r.read || '') ? 'bull' : /aversion|^inverted/.test(r.read || '') ? 'bear' : 'neutral');
  const readRow = (r) => {
    const ch = numOrNull(r.change);
    const bp = r.change_unit === 'bp';
    const chText = ch == null ? '' : `${ch > 0 ? '▲' : ch < 0 ? '▼' : '–'} ${ch > 0 ? '+' : ''}${fmtNumber(ch, bp ? 1 : 2)}${bp ? ' bp' : '%'}`;
    const val = r.unit === 'bp' ? `${r.value > 0 ? '+' : ''}${fmtNumber(r.value, 0)} bp` : '';
    const read = r.read ? r.read[0].toUpperCase() + r.read.slice(1) : '';
    return `<li${r.unit === 'ratio' ? ` title="${escapeHtml(`Ratio ${fmtNumber(r.value, 4)}`)}"` : ''}><span class="q-x-l">${escapeHtml(r.label)}${r.note ? `<small>${escapeHtml(r.note)}</small>` : ''}</span><span class="q-x-v">${val ? escapeHtml(val) : `<span class="q-x-chip ${readTone(r)}">${escapeHtml(read)}</span>`}</span><span class="q-x-c ${ch > 0 ? 'bull' : ch < 0 ? 'bear' : ''}">${escapeHtml(chText)}</span>${val && read ? `<span class="q-x-r"><span class="q-x-chip ${readTone(r)}">${escapeHtml(read)}</span></span>` : ''}</li>`;
  };
  const used = new Set(['^IRX', '^TNX', 'HYG', 'LQD', 'HG=F', 'GC=F'].filter(() => reads.length));
  const quoteRow = (q) => {
    const ch = numOrNull(q.change_pct);
    return `<li><span class="q-x-l">${escapeHtml(q.label)}${q.note ? `<small>${escapeHtml(q.note)}</small>` : ''}</span><span class="q-x-v">${fmtNumber(q.price, q.price >= 1000 ? 0 : 2)}</span><span class="q-x-c ${ch > 0 ? 'bull' : ch < 0 ? 'bear' : ''}">${ch == null ? '' : `${ch > 0 ? '▲' : ch < 0 ? '▼' : '–'} ${fmtPct(ch, 2, false)}`}</span></li>`;
  };
  const rest = quotes.filter((q) => !used.has(q.symbol));
  if (!reads.length && !rest.length) { body.innerHTML = '<p class="q-note-block">Cross-asset quotes are unavailable this cycle.</p>'; return; }
  body.innerHTML = `<ul class="q-x">${rest.map(quoteRow).join('')}${reads.map(readRow).join('')}</ul>`;
}

/* -- the week --------------------------------------------------------------- */
// [PURE] Why the week's earnings list is short, when it is: a server that
// predates the earnings calendar, or a calendar read that failed.
function earningsCalendarNote(intel = {}, market = 'US') {
  if (market !== 'US' || !intel || intel.loading || !Object.keys(intel).length) return null;
  if (!('earnings_scope' in intel)) return 'Only the tracked universe\'s reports are listed: the full earnings calendar arrives with the latest QUANTA server. Restart QUANTA (start.ps1) to load it.';
  if (intel.earnings_calendar_status === 'unavailable') return 'Yahoo\'s earnings calendar did not answer, so only the tracked universe\'s reports are listed. It is read again with the next refresh.';
  return null;
}

// Short names for the publishers' release kinds; anything else keeps its title.
const MACRO_SHORT = { cpi: 'CPI', ppi: 'PPI', jobs: 'Jobs report', fomc: 'Fed decision', gdp: 'GDP', pce: 'PCE inflation', retail: 'Retail sales', claims: 'Jobless claims', jolts: 'Job openings' };

// [PURE] The next dated event from now: a release later today, else the first
// event on a later day. nowHHMM is the exchange's wall time ("09:41").
function nextWeekEvent(days, nowHHMM) {
  // After the close nothing left today is still ahead.
  const closed = nowHHMM && nowHHMM >= '16:00';
  const slot = (e) => (e.kind === 'macro' ? (e.time || '99:99') : /^(am|bmo)$/i.test(String(e.timing || '')) ? '09:00' : /^(pm|amc)$/i.test(String(e.timing || '')) ? '16:01' : '12:00');
  for (let i = 0; i < days.length; i += 1) {
    if (i === 0 && closed) continue;
    const items = days[i].items.slice().sort((a, b) => slot(a).localeCompare(slot(b)) || (b.held ? 1 : 0) - (a.held ? 1 : 0) || (b.cap || 0) - (a.cap || 0));
    for (const e of items) {
      if (i === 0 && e.kind === 'macro' && e.time && nowHHMM && e.time < nowHHMM) continue;
      if (i === 0 && e.kind === 'earnings' && /^(am|bmo)$/i.test(String(e.timing || '')) && nowHHMM && nowHHMM > '09:30') continue;
      return { ...e, date: days[i].date, inDays: i };
    }
  }
  return null;
}

function renderWeekCalendar() {
  const body = $('#week-body');
  if (!body) return;
  const ctx = marketContext();
  const d = state.dashboard || {};
  const intel = d.market_intel || {};
  const ex = exchangeSession();
  const today = ex.today;
  if (!today) return;
  const held = state.book && state.book.s === 'ok'
    ? (bookVsCall(state.book.pf, null, intel, today) || { reporting: [] }).reporting : [];
  const days = weekEvents(today, (ctx && ctx.calendar) || [], intel.earnings_calendar || intel.earnings_week || [], held);
  const meta = $('#week-meta');
  const count = (k) => days.reduce((t, x) => t + x.items.filter((e) => e.kind === k).length, 0);
  const macroN = count('macro');
  const earnN = count('earnings');
  const mine = days.reduce((t, x) => t + x.items.filter((e) => e.held).length, 0);
  const parts = (ymd) => {
    const dt = new Date(`${ymd}T12:00:00Z`);
    return {
      dow: dt.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' }),
      num: dt.getUTCDate(),
      mon: dt.toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' }),
      weekend: dt.getUTCDay() === 0 || dt.getUTCDay() === 6,
    };
  };
  const first = parts(days[0].date);
  const last = parts(days[days.length - 1].date);
  const span = first.mon === last.mon ? `${first.mon} ${first.num} to ${last.num}` : `${first.mon} ${first.num} to ${last.mon} ${last.num}`;
  if (meta) meta.textContent = [`${span}: ${earnN} earnings report${earnN === 1 ? '' : 's'}`, macroN ? `${macroN} release${macroN === 1 ? '' : 's'}` : '', mine ? `${mine} you hold` : ''].filter(Boolean).join(', ');
  const tz = (MARKET_TZ[state.market] || MARKET_TZ.US).tz;
  let nowHHMM = null;
  try { nowHHMM = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }); } catch (_) { nowHHMM = null; }
  const next = nextWeekEvent(days, nowHHMM);
  const eps = (v) => (v == null ? '' : `EPS est. ${v < 0 ? '-' : ''}$${fmtNumber(Math.abs(v), 2)}`);
  const timingWord = (t) => (/^(am|bmo)$/i.test(String(t || '')) ? 'Before open' : /^(pm|amc)$/i.test(String(t || '')) ? 'After close' : '');
  const name = (e) => (e.kind === 'macro' ? (MACRO_SHORT[String(e.macroKind || '').toLowerCase()] || e.title) : `${companyName(e.name) || shownSymbol(e.symbol)} earnings`);
  // Sun before the open, moon after the close: drawn, not emoji, and always
  // paired with the words for screen readers.
  const icon = (t) => (/^(am|bmo)$/i.test(String(t || ''))
    ? '<svg class="ev-ic ev-ic--am" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="3.2"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/></svg>'
    : /^(pm|amc)$/i.test(String(t || ''))
      ? '<svg class="ev-ic ev-ic--pm" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M10.8 2.2a6 6 0 1 0 3 9.6A5 5 0 0 1 10.8 2.2Z"/></svg>'
      : '<svg class="ev-ic ev-ic--tbd" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="8" cy="8" r="2"/></svg>');
  const nextHtml = next ? `<div class="q-wk-next">
      <span class="q-wk-pulse" aria-hidden="true"></span>
      <span class="q-wk-next-k">Next up</span>
      <b>${escapeHtml(name(next))}</b>
      <span>${escapeHtml(`${parts(next.date).dow} ${parts(next.date).mon} ${parts(next.date).num}`)}${next.time ? `, ${escapeHtml(next.time)} ET` : next.timing ? `, ${escapeHtml(earningsTimingWords(next.timing))}` : ''}${next.kind === 'earnings' && next.eps != null ? `, ${escapeHtml(eps(next.eps))}` : ''}</span>
      <span class="q-wk-next-in" data-ymd="${escapeHtml(next.date)}" data-hhmm="${escapeHtml(next.time || (/^(am|bmo)$/i.test(String(next.timing || '')) ? '09:30' : /^(pm|amc)$/i.test(String(next.timing || '')) ? '16:00' : ''))}">${escapeHtml(countdownText(next.date, next.time || null, next.timing, tz, next.inDays))}</span>
    </div>` : '';
  const perDay = mkt('EARNINGS_PER_DAY_SHOWN');
  const SHOWN = Number.isFinite(perDay) && perDay > 0 ? perDay : 3;
  const maxN = Math.max(1, ...days.map((x) => x.items.filter((e) => e.kind === 'earnings').length));
  const ev = (e) => {
    if (e.kind === 'macro') {
      const short = MACRO_SHORT[String(e.macroKind || '').toLowerCase()];
      const title = escapeHtml(short || e.title);
      const link = safeUrl(e.url) ? `<a href="${escapeHtml(safeUrl(e.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(e.title)}">${title}<span class="sr-only"> (opens the publisher's schedule)</span></a>` : title;
      return `<li class="ev ev--macro"><span class="ev-time">${escapeHtml(e.time || 'TBD')}</span><span class="ev-name">${link}</span></li>`;
    }
    const t = timingWord(e.timing);
    const tip = `${companyName(e.name) || shownSymbol(e.symbol)}${t ? `, ${t.toLowerCase()}` : ''}${e.eps != null ? `, ${eps(e.eps)}` : ''}`;
    return `<li class="ev ev--earn${e.held ? ' is-held' : ''}"><button type="button" class="ev-row" data-ticker="${escapeHtml(normalizeTicker(e.symbol))}" title="${escapeHtml(tip)}">${icon(e.timing)}<span class="ev-sym hp-sym">${escapeHtml(shownSymbol(e.symbol))}</span><span class="sr-only">${escapeHtml(`${t ? `${t}. ` : ''}${e.eps != null ? `${eps(e.eps)}. ` : ''}Open its research.`)}</span>${e.held ? '<span class="ev-held">Yours</span>' : ''}</button></li>`;
  };
  const cols = days.map((x, i) => {
    const pp = parts(x.date);
    const macro = x.items.filter((e) => e.kind === 'macro');
    const earn = x.items.filter((e) => e.kind === 'earnings');
    const shown = earn;
    const rest = [];
    void SHOWN;
    const cls = [i === 0 ? 'is-today' : '', pp.weekend ? 'is-weekend' : '', x.items.length ? 'has-ev' : ''].filter(Boolean).join(' ');
    const label = `${i === 0 ? 'Today, ' : ''}${pp.dow} ${pp.mon} ${pp.num}: ${x.items.length ? [macro.length ? `${macro.length} release${macro.length === 1 ? '' : 's'}` : '', earn.length ? `${earn.length} earnings report${earn.length === 1 ? '' : 's'}` : ''].filter(Boolean).join(', ') : pp.weekend ? 'market closed' : 'no dated events'}`;
    const more = rest.length ? `<li class="ev-more"><details><summary>+${rest.length} more report${rest.length === 1 ? '' : 's'}</summary>
        <ul class="ev-more-list">${rest.map((e) => `<li title="${escapeHtml(`${companyName(e.name) || shownSymbol(e.symbol)}${e.timing ? `, ${earningsTimingWords(e.timing)}` : ''}${e.eps != null ? `, ${eps(e.eps)}` : ''}`)}"><b class="hp-sym">${escapeHtml(shownSymbol(e.symbol))}</b>${e.timing ? `<span>${/^(am|bmo)$/i.test(e.timing) ? 'AM' : 'PM'}</span>` : ''}</li>`).join('')}</ul></details></li>` : '';
    const bar = earn.length ? `<span class="q-wk-bar" aria-hidden="true"><i style="transform:scaleY(${(earn.length / maxN).toFixed(3)})"></i></span>` : '<span class="q-wk-bar is-empty" aria-hidden="true"></span>';
    return `<li class="${cls}" aria-label="${escapeHtml(label)}">
        <div class="q-wk-date"><span class="q-wk-dow">${i === 0 ? 'Today' : escapeHtml(pp.dow)}</span><span class="q-wk-num">${pp.num}</span>${earn.length ? `<span class="q-wk-cnt" aria-hidden="true">${earn.length} report${earn.length === 1 ? '' : 's'}</span>` : ''}</div>
        ${pp.weekend && !x.items.length ? '' : bar}
        ${x.items.length ? `<ul class="q-wk-evs">${macro.map(ev).join('')}${shown.map(ev).join('')}${more}</ul>`
          : `<span class="q-wk-none">${pp.weekend ? 'Closed' : 'No dated events'}</span>`}
      </li>`;
  }).join('');
  const scope = intel.earnings_scope || 'the tracked universe';
  const calNote = earningsCalendarNote(intel, state.market);
  body.innerHTML = `${calNote ? `<p class="q-ctx-note q-wk-note">${escapeHtml(calNote)}</p>` : ''}${nextHtml}
    <ol class="q-wk" aria-label="The next seven days" style="--wk-cols:${days.map((x) => (parts(x.date).weekend && !x.items.length ? 'minmax(0,0.42fr)' : 'minmax(0,1fr)')).join(' ')}">${cols}</ol>
    <div class="q-wk-foot">
      <span class="q-wk-legend"><span>${icon('bmo')}Before open</span><span>${icon('amc')}After close</span><span><i class="lg lg--macro"></i>Economic release</span>${mine ? '<span><i class="lg lg--held"></i>Your holding</span>' : ''}</span>
      <span class="q-wk-src">${macroN || state.market === 'US' ? `Fed and BLS schedules${state.market === 'US' ? '' : ' (US)'}; earnings` : 'Earnings'} for ${escapeHtml(scope)}.${macroN ? ' Times are Eastern.' : ''}</span>
    </div>`;
  $$('.ev-row[data-ticker]', body).forEach((b) => b.addEventListener('click', () => openTickerTearSheet(b.dataset.ticker)));
  weekMotion(body);
  weekCountdown(body, tz);
}

// [PURE] "in 2 h 15 m", "in 1 d 18 h", "in 3 days": the next event from now.
function countdownText(ymd, hhmm, timing, tz, inDays) {
  const at = hhmm || (/^(am|bmo)$/i.test(String(timing || '')) ? '09:30' : /^(pm|amc)$/i.test(String(timing || '')) ? '16:00' : null);
  const t = at ? zonedEpoch(ymd, at, tz) : null;
  if (t == null) return inDays === 0 ? 'today' : inDays === 1 ? 'tomorrow' : `in ${inDays} days`;
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins <= 0) return 'now';
  if (mins < 60) return `in ${mins} m`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `in ${h} h ${mins % 60} m`;
  return `in ${Math.floor(h / 24)} d ${h % 24} h`;
}

// [PURE] The UTC instant of a wall-clock time in a time zone ("2026-10-12", "16:00").
function zonedEpoch(ymd, hhmm, tz) {
  const [y, mo, da] = String(ymd).split('-').map(Number);
  const [hh, mm] = String(hhmm).split(':').map(Number);
  if (![y, mo, da, hh, mm].every(Number.isFinite)) return null;
  const guess = Date.UTC(y, mo - 1, da, hh, mm);
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      .formatToParts(new Date(guess)).reduce((o, p) => ({ ...o, [p.type]: p.value }), {});
    const seen = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute));
    return guess - (seen - guess);
  } catch (_) { return null; }
}

// The histogram grows in once, when the week first scrolls into view.
function weekMotion(body) {
  if (!motionOK() || typeof IntersectionObserver === 'undefined') return;
  const bars = [...body.querySelectorAll('.q-wk-bar i')];
  if (!bars.length || state.weekBarsShown) return;
  const io = new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting)) return;
    io.disconnect();
    state.weekBarsShown = true;
    bars.forEach((b, i) => {
      const end = b.style.transform || 'scaleY(1)';
      b.animate([{ transform: 'scaleY(0)' }, { transform: end }], { duration: 520, easing: EASE_OUT, delay: i * 55, fill: 'backwards' });
    });
    [...body.querySelectorAll('.q-wk > li')].forEach((col, c) => [...col.querySelectorAll('.ev')].forEach((chip, k) => chip.animate(
      [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: 240, easing: EASE_OUT, delay: 180 + c * 45 + Math.min(k, 12) * 18, fill: 'backwards' })));
  }, { threshold: 0.35 });
  io.observe(body);
}

// The countdown keeps time while the page is open: one update a minute.
function weekCountdown(body, tz) {
  if (typeof window === 'undefined') return;
  if (state.weekTimer) clearInterval(state.weekTimer);
  const el = body.querySelector('.q-wk-next-in');
  if (!el) return;
  state.weekTimer = setInterval(() => {
    if (!document.body.contains(el)) { clearInterval(state.weekTimer); return; }
    el.textContent = countdownText(el.dataset.ymd, el.dataset.hhmm || null, null, tz, daysFromTo(exchangeSession().today, el.dataset.ymd) || 0);
  }, 60000);
}

/* -- futures, outside the regular session --------------------------------- */
function renderFutures() {
  const el = $('#futures-strip');
  if (!el) return;
  const ctx = marketContext();
  const rows = ((ctx && ctx.futures) || []).filter((f) => numOrNull(f.price) != null);
  const phase = exchangeSession().phase;
  if (!rows.length || phase === 'regular') { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = `<span class="q-fut-k">Index futures${phase === 'pre' ? ', pre-market' : phase === 'after' ? ', after hours' : ', market closed'}</span>
    ${rows.map((f) => { const ch = numOrNull(f.change_pct); return `<span class="q-fut"><b>${escapeHtml(f.label)}</b> ${fmtNumber(f.price, f.price >= 1000 ? 0 : 2)} <span class="${ch > 0 ? 'bull' : ch < 0 ? 'bear' : ''}">${ch == null ? '' : `${ch > 0 ? '▲' : ch < 0 ? '▼' : '–'} ${fmtPct(ch, 2, false)}`}</span></span>`; }).join('')}`;
}

// The regime's one name. The engine appends a confidence suffix for thin
// clients and also publishes a second wording per posture (modelSignal); the
// page uses this name everywhere so the regime is never called two things.
function regimeName(market = {}, model = {}) {
  const engine = market.market_regime_engine || market.marketRegimeEngine || {};
  return engine.marketRegimeBase
    || String(model.label || '').replace(/\s*[\u2014\u2013-]\s*(low|moderate|high)\s+confidence\s*$/i, '')
    || model.label || 'Mixed Market';
}

// Direction: the factor scores summed and rescaled onto 0-100 (each factor
// scores -2..+2). It says which way the evidence leans, not how sure the model
// is; confidence is the separate, agreement-and-freshness measure.
function directionScore(model = {}) {
  const factors = Array.isArray(model.factors) ? model.factors : [];
  const maxAbs = factors.length * 2;
  const score10 = maxAbs ? Math.max(0, Math.min(10, 5 + (Number(model.net || 0) / maxAbs) * 5)) : 5;
  const tone = score10 >= mkt('SIGNAL_SCORE_BULL') ? 'bull' : score10 <= mkt('SIGNAL_SCORE_BEAR') ? 'bear' : score10 >= mkt('SIGNAL_SCORE_NEUTRAL') ? 'neutral' : 'warn';
  return { score10, shown: model.guidanceWithheld || !factors.length ? '--' : fmtScore100(score10), tone };
}

function signalCheckCounts(model = {}) {
  const factors = Array.isArray(model.factors) ? model.factors : [];
  let confirmed = 0, neutral = 0, warning = 0, opposed = 0;
  factors.forEach((f) => {
    const s = Number(f.score) || 0;
    if (s > 0) confirmed += 1;
    else if (s === 0) neutral += 1;
    else if (s === -1) warning += 1;
    else opposed += 1;
  });
  return { confirmed, neutral, warning, opposed, total: factors.length };
}

// [PURE] Derive a concise trader response for a market driver from its
// direction + category (the backend driver has no explicit response field).
function driverTraderResponse(driver = {}) {
  const dir = String(driver.direction || '').toLowerCase();
  const cat = String(driver.category || '').toLowerCase();
  if (dir === 'bearish') {
    if (cat.includes('macro') || cat.includes('rate') || cat.includes('politic')) return 'Trim risk; keep stops tight and size light.';
    return 'Avoid weak names; demand confirmation before new risk.';
  }
  if (dir === 'bullish') {
    if (cat.includes('earning')) return 'Trade the confirmed reaction, not the pre-print.';
    return 'Lean into confirmed leaders on volume.';
  }
  if (cat.includes('earning')) return 'Wait for the print; avoid pre-earnings risk.';
  if (cat.includes('politic') || cat.includes('geo')) return 'Let the tape pick a side; don\u2019t pre-position on the headline.';
  return 'Watch the tape reaction before acting.';
}

// [PURE] "a bullish", "an insufficient": the article a phrase takes.
const withArticle = (phrase) => `${/^[aeiou]/i.test(String(phrase || '').trim()) ? 'an' : 'a'} ${phrase}`;

// [PURE] Market-read bands. Every cut-off is MARKET.* config from
// engine/thresholds.py (GET /api/thresholds); the page holds no number of its own.
const mkt = (k) => Number(thr(`MARKET.${k}`));
// Instruments the ribbon reads with their own units or conventions.
const isVolIndex = (sym) => /^\^(VIX|VXN|VVIX|RVX|INDIAVIX)$/i.test(String(sym || ''));
const isYield = (sym, label) => /^\^(TNX|TYX|FVX|IRX)$/i.test(String(sym || '')) || /yield/i.test(String(label || ''));
const isCryptoSymbol = (sym, label) => /-USD$|-INR$/i.test(String(sym || '')) || /bitcoin|ethereum|crypto/i.test(String(label || ''));
// A yield's day change in basis points from its level (percent) and its percent change.
function yieldChangeBp(level, pct) {
  if (!Number.isFinite(level) || !Number.isFinite(pct) || pct <= -100) return null;
  const prev = level / (1 + pct / 100);
  return (level - prev) * 100;
}
function vixTone(v) {
  if (v == null || !Number.isFinite(Number(v))) return '';
  return v >= mkt('VIX_STRESS') ? 'bear' : v >= mkt('VIX_CALM_MAX') ? 'warn' : 'bull';
}
function participationTone(p) {
  if (p == null || !Number.isFinite(Number(p))) return '';
  return p >= mkt('PARTICIPATION_BROAD_PCT') ? 'bull' : p <= mkt('PARTICIPATION_WEAK_PCT') ? 'bear' : 'warn';
}
// 'high' | 'med' | 'low' for a headline's impact score.
function newsImpactLevel(impact) {
  const n = Number(impact || 0);
  return n >= mkt('NEWS_IMPACT_MARKET_MOVING') ? 'high' : n >= mkt('NEWS_IMPACT_MEDIUM') ? 'med' : 'low';
}

// [PURE] The headline tilt as the tape shows it. "Quiet tape" is a reading
// (feeds answered and nothing moved); with no news read, or no live feed, the
// tilt is unknown and says so.
function newsTiltRead(engine = {}) {
  const dir = String((engine.newsImpact && engine.newsImpact.netDirection) || '').toLowerCase();
  const known = { bullish: ['Bullish tilt', 'bull'], bearish: ['Bearish tilt', 'bear'], mixed: ['Two-sided', 'warn'], neutral: ['Quiet tape', 'neutral'] }[dir];
  if (!known || engine.newsFeedsLive === false) return { label: 'News read unavailable', tone: 'neutral', known: false };
  return { label: known[0], tone: known[1], known: true };
}

// [PURE] Plain sentence placing a confidence reading against the model's own
// thresholds, so the number says how far it is from the next band.
function confidenceBand(score, label, withheld = false) {
  void label;
  if (withheld) return 'No read: too few market inputs were measured to score confidence.';
  if (score == null) return 'Not scored yet.';
  const hi = mkt('CONFIDENCE_HIGH');
  const mod = mkt('CONFIDENCE_MODERATE');
  if (score >= hi) return `<b>High</b>: at or above the ${hi} threshold.`;
  if (score >= mod) return `<b>Moderate</b>: high begins at ${hi}.`;
  return `<b>Low</b>: moderate begins at ${mod}.`;
}

// [PURE] Common confidence read derived once and shared by the brief + chips.
// A read built from missing inputs (guidance withheld) carries no score: a
// number there would look like a measurement.
function confidenceRead(model = {}) {
  if (model.guidanceWithheld) return { score: null, label: null, tone: 'neutral', withheld: true };
  const score = model.confidenceScore;
  const label = model.confidenceLabel || (score == null ? null
    : (score >= mkt('CONFIDENCE_HIGH') ? 'High' : score >= mkt('CONFIDENCE_MODERATE') ? 'Moderate' : 'Low'));
  const tone = label === 'High' ? 'bull' : label === 'Low' ? 'warn' : 'neutral';
  return { score, label, tone, withheld: false };
}




// Posture-specific plain-English explanation (fallback only — the hero prefers
// the backend's data-driven marketHeadline). Kept generic so no fixed factor
// count or fixed bias is ever shown from the fallback path.
function regimeExplanation(model) {
  const p = model.posture;
  const map = {
    'Offensive': 'Trend, breadth and volatility line up: press quality leaders, but keep single-name risk capped.',
    'Selective Offensive': 'The tape leans constructive but is not fully confirmed: favor leaders, demand volume, and avoid chasing.',
    'Lean Long': 'Slight positive edge with unconfirmed signals: lean long in A-grade setups and keep dry powder.',
    'Neutral': 'Signals are split: position sizing and discipline matter more than prediction here.',
    'Lean Defensive': 'The edge is tilting down: raise the quality bar, keep size light, and protect gains.',
    'Selective Defensive': 'Risk signals outweigh support: preserve capital and wait for a cleaner tape.',
    'Defensive': 'Risk is elevated across the model: preserve cash and demand much stronger setups.',
  };
  return map[p] || 'Signals are split: keep allocations measured and require confirmation.';
}

// [STRATEGIST BRIEF] A morning-note synthesis layer over the SAME regime model
// (no new data, no contradiction). It adds the dimensions a decision card does
// NOT: what KIND of tape this is, where the hidden risk sits, which setups are
// favored, and the single thing that matters most today. Pure + testable.
function strategistBrief(model = {}) {
  const al = model.alignment || {};
  const support = al.support || [];
  const oppose = al.oppose || [];
  const neutral = al.neutral || [];
  const bull = al.bull || 0;
  const bear = al.bear || 0;
  const count = model.count || 0;
  const tone = model.postureTone || 'neutral';
  const p = model.posture || 'Neutral';
  const vix = model.vix;
  const breadth = model.breadth;
  const narrowBreadth = breadth != null && breadth < mkt('PARTICIPATION_NARROW_PCT');
  const strongBreadth = breadth != null && breadth >= mkt('PARTICIPATION_BROAD_PCT');
  const hotVix = vix != null && vix >= mkt('CHAIN_VIX_REGIME');
  const calmVix = vix != null && vix < mkt('VIX_CONTAINED_MAX');
  const eventRisk = (model.highImpact || 0) >= mkt('HIGH_IMPACT_NEWS_WARN');
  const earnings = (model.earningsCount || 0) > 0;

  // MARKET TYPE — investor's (broad) vs trader's (selective) vs wait-and-see.
  let marketType;
  if (tone === 'bear') {
    marketType = { label: 'Defensive tape', text: 'Protect capital first; opportunity is secondary here.' };
  } else if (tone === 'neutral') {
    marketType = { label: 'Two-way tape', text: 'Range-bound and headline-driven: levels beat direction.' };
  } else if (strongBreadth && !hotVix) {
    marketType = { label: "Investor's tape", text: 'Broad participation: index and trend exposure both work.' };
  } else {
    marketType = { label: "Stock-picker's tape", text: 'Selectivity pays: own confirmed leaders, not the index.' };
  }

  // WHERE RISK HIDES — the single most material, least-obvious risk right now.
  let riskHidden;
  if (tone !== 'bear' && narrowBreadth) {
    riskHidden = { text: 'Concentration: a thin set of leaders is holding the tape up.' };
  } else if (hotVix) {
    riskHidden = { text: 'Volatility: wide ranges stop normal size on noise, not on being wrong.' };
  } else if (eventRisk) {
    riskHidden = { text: 'Event risk: a scheduled print can override the tape.' };
  } else if (earnings) {
    riskHidden = { text: `Single-name gaps: ${model.earningsCount} earnings can undo a setup overnight.` };
  } else if (calmVix && tone === 'bull') {
    riskHidden = { text: 'Complacency: the real risk is your position size, not the tape.' };
  } else {
    riskHidden = { text: 'Whipsaw: unconfirmed signals reverse before they commit.' };
  }

  // FAVORED SETUPS — the style of trade the tape is paying for.
  let favored;
  if (tone === 'bear') {
    favored = { text: 'Support-reclaim reversals in A-quality names; cash over fresh longs.' };
  } else if (tone === 'neutral') {
    favored = { text: 'Mean-reversion at range edges: small starters, confirm before pressing.' };
  } else if (p === 'Offensive') {
    favored = { text: 'Breakouts and high-tight bases in leaders, on expanding volume.' };
  } else {
    favored = { text: 'Pullbacks and tight bases in confirmed leaders, not extended chases.' };
  }

  // WHAT MATTERS MOST TODAY — one watch item, preferring the unconfirmed factor.
  let watch;
  if (eventRisk) {
    watch = { text: 'The macro print: it can reset the regime in one session.' };
  } else if (oppose.length) {
    watch = { text: `${oppose.join(' & ')}: the factor still withholding; a turn flips the call first.` };
  } else if (neutral.length) {
    watch = { text: `${neutral.slice(0, 2).join(' & ')}: still neutral; confirmation here strengthens the tape.` };
  } else if (hotVix) {
    watch = { text: 'VIX: treat strength as a chance to trim, not add.' };
  } else {
    watch = { text: 'Volume on leadership: broadening confirms; fading volume warns first.' };
  }

  // LEAD — the strategist's one-line frame (distinct from the hero sub-line).
  let lead;
  if (tone === 'bull') {
    const head = p === 'Offensive' ? 'The tape has earned offense' : 'Constructive, but the tape still wants proof';
    const backing = `${support.length || bull} of ${count} factors back the call`;
    const tail = oppose.length ? `, with ${oppose.join(' & ')} still withholding` : '';
    lead = `${head}: ${backing}${tail}.`;
  } else if (tone === 'bear') {
    lead = `Risk is in control: ${bear || support.length} of ${count} factors lean defensive; today the job is to lose less, not win big.`;
  } else if (bull + bear === 0) {
    lead = 'No edge to press: every factor reads neutral; this is a wait-and-see tape, not a place to predict.';
  } else {
    lead = `No edge worth pressing: the model is split ${bull}\u2013${bear}; the tape is asking for patience, not prediction.`;
  }

  return { lead, marketType, riskHidden, favored, watch };
}

// (renderStrategistBrief / renderTape removed — their host elements were
// retired with the hero compaction; strategistBrief() above remains the pure,
// unit-tested synthesis used by tear sheets.)

// [MARKET PULSE] One-word interpretation per instrument (meaning over numbers).
function pulseInterp(symbol, label, changePct, price) {
  const sym = String(symbol || '').toUpperCase();
  const lbl = String(label || '').toLowerCase();
  const ch = Number(changePct);
  const finite = Number.isFinite(ch);
  if (/vix/i.test(sym) || lbl.includes('vix')) {
    if (price == null) return { tone: 'neutral', text: 'no read' };
    if (price >= mkt('VIX_STRESS')) return { tone: 'bear', text: 'risk elevated' };
    if (price >= mkt('VIX_CALM_MAX')) return { tone: 'warn', text: 'not risk-on confirmation' };
    return { tone: 'bull', text: 'calm, supports risk' };
  }
  if (sym.includes('TNX') || lbl.includes('yield') || lbl.includes('10y')) {
    if (!finite) return { tone: 'neutral', text: 'rates' };
    // Same cut-offs as the model's Rates / Credit factor (app.py), so the
    // ribbon never calls "steady" a move the model scores as tightening.
    // Short enough for a ribbon cell: the longer wording was cut to "valuation p…".
    if (ch >= mkt('YIELD_SHARP_PCT')) return { tone: 'bear', text: 'rising sharply' };
    if (ch >= mkt('YIELD_RISING_PCT')) return { tone: 'warn', text: 'rising, rate pressure' };
    if (ch <= mkt('YIELD_EASING_PCT')) return { tone: 'bull', text: 'easing, relief' };
    return { tone: 'neutral', text: 'steady' };
  }
  if (lbl.includes('oil') || sym.startsWith('USO') || sym.startsWith('CL')) {
    if (!finite) return { tone: 'neutral', text: 'energy' };
    if (ch >= mkt('OIL_MOVE_PCT')) return { tone: 'warn', text: 'spiking, inflation risk' };
    if (ch <= -mkt('OIL_MOVE_PCT')) return { tone: 'bull', text: 'falling, cost relief' };
    return { tone: 'neutral', text: 'steady' };
  }
  if (lbl.includes('gold') || sym.startsWith('GLD') || sym.startsWith('GC')) {
    if (!finite) return { tone: 'neutral', text: 'haven' };
    if (ch >= mkt('GOLD_MOVE_PCT')) return { tone: 'warn', text: 'haven bid' };
    if (ch <= -mkt('GOLD_MOVE_PCT')) return { tone: 'neutral', text: 'risk appetite' };
    return { tone: 'neutral', text: 'steady' };
  }
  if (lbl.includes('bitcoin') || sym.startsWith('BTC')) {
    if (!finite) return { tone: 'neutral', text: 'crypto' };
    if (ch >= mkt('CRYPTO_MOVE_PCT')) return { tone: 'bull', text: 'risk appetite' };
    if (ch <= -mkt('CRYPTO_MOVE_PCT')) return { tone: 'bear', text: 'risk-off' };
    return { tone: 'neutral', text: 'range-bound' };
  }
  if (sym.includes('INR=') || lbl.includes('usd/')) {
    if (!finite) return { tone: 'neutral', text: 'fx' };
    if (ch >= mkt('FX_MOVE_PCT')) return { tone: 'bear', text: 'rupee weaker' };
    if (ch <= -mkt('FX_MOVE_PCT')) return { tone: 'bull', text: 'rupee firmer' };
    return { tone: 'neutral', text: 'steady' };
  }
  // Equity indices
  if (!finite) return { tone: 'neutral', text: '\u2014' };
  if (ch >= mkt('INDEX_LEADING_PCT')) return { tone: 'bull', text: 'leading' };
  if (ch > mkt('INDEX_FLAT_PCT')) return { tone: 'bull', text: 'firm' };
  if (ch <= -mkt('INDEX_LEADING_PCT')) return { tone: 'bear', text: 'under pressure' };
  if (ch < -mkt('INDEX_FLAT_PCT')) return { tone: 'bear', text: 'soft' };
  return { tone: 'neutral', text: 'flat' };
}






// Headline impact, weighted for age exactly as the regime engine does
// (_summarize_news_for_engine: half-life four days, undated = live). Every
// "high impact" count on the page uses this, so the Risk panel, the High
// Impact tab and each headline's relevance label can never disagree, and a
// week-old Fed statement no longer reads as "High".
const NEWS_HALF_LIFE_DAYS = 4;
function effectiveNewsImpact(item = {}, now = Date.now()) {
  const raw = Number(item && item.impact || 0);
  const t = item && item.published ? new Date(item.published).getTime() : NaN;
  if (!Number.isFinite(t)) return raw;
  const ageDays = Math.max(0, (now - t) / 86400000);
  return raw * Math.pow(0.5, ageDays / NEWS_HALF_LIFE_DAYS);
}
function highImpactNews(groups) {
  return dedupeNewsItems(Object.values(groups || {}).flatMap((items) => Array.isArray(items) ? items : []))
    .filter(isTradeRelevantNews)
    .filter((item) => effectiveNewsImpact(item) >= mkt('NEWS_IMPACT_MARKET_MOVING'));
}

function updateNewsTabCounts(groups) {
  const uniqueRelevant = (items) => dedupeNewsItems(items).filter(isTradeRelevantNews);
  const countFor = (key) => {
    if (key === 'high-impact') return highImpactNews(groups).length;
    return uniqueRelevant(Array.isArray(groups[key]) ? groups[key] : []).length;
  };
  $$('#news-tabs .q-tab').forEach((button) => {
    const key = button.dataset.newsGroup;
    const label = button.dataset.label || titleLabel(key);
    button.innerHTML = `${escapeHtml(label)}<span class="q-tab-count">${countFor(key)}</span>`;
  });
}

function dedupeNewsItems(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    const key = String(item.url || item.title || item.headline || '').trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function isTradeRelevantNews(item) {
  const text = String(item.title || item.headline || '').toLowerCase();
  const marketTerms = [
    'stock', 'stocks', 'shares', 'market', 'markets', 'nasdaq', 's&p', 'dow', 'russell',
    'fed', 'fomc', 'rate', 'rates', 'treasury', 'yield', 'bond', 'inflation', 'cpi', 'pce',
    'jobs', 'payroll', 'gdp', 'recession', 'oil', 'opec', 'dollar', 'gold', 'bitcoin', 'crypto',
    'earnings', 'guidance', 'revenue', 'profit', 'merger', 'acquisition', 'ipo', 'deal',
    'tariff', 'sanction', 'semiconductor', 'chip', 'ai', 'bank', 'credit', 'default',
  ];
  const hasMarketTerm = marketTerms.some((term) => {
    if (/[^a-z0-9]/.test(term) || term.includes(' ')) return text.includes(term);
    if (term.length <= 4) return new RegExp(`(^|[^a-z0-9])${term}([^a-z0-9]|$)`).test(text);
    return text.includes(term);
  });
  return hasMarketTerm || !!relatedTickerFromHeadline(item);
}

function relatedTickerFromHeadline(item) {
  if (item.ticker || item.symbol) return normalizeTicker(item.ticker || item.symbol);
  const title = String(item.title || item.headline || '');
  const match = title.match(/\b[A-Z]{2,5}\b/);
  const generic = new Set(['FED', 'FOMC', 'CPI', 'PCE', 'GDP', 'CEO', 'CFO', 'ETF', 'IPO', 'SEC', 'USA', 'UK', 'US', 'NEWS', 'LIVE', 'OIL', 'GOLD']);
  if (!match || generic.has(match[0])) return '';
  return match[0];
}

// [COMMAND-CENTER] Market Impact Chain — a causal HYPOTHESIS, then verified
// against the live tape. Each chain carries the cross-asset checks that would
// confirm it; we only label it "confirmed" when the data actually agrees, so
// it can be wrong on screen instead of always looking right.
function impactChain(market = {}, news = {}, intel = {}) {
  const bySym = {};
  (market.quotes || []).forEach((q) => { bySym[String(q.symbol || '').toUpperCase()] = q; });
  const chg = (k) => { const q = bySym[k]; return q && Number.isFinite(Number(q.change_pct)) ? Number(q.change_pct) : null; };
  const oilCh = chg('USO'); const tnxCh = chg('^TNX'); const spyCh = chg('SPY'); const qqqCh = chg('QQQ');
  const iwmCh = chg('IWM'); const gldCh = chg('GLD'); const btcCh = chg('BTC-USD');
  if (spyCh == null && qqqCh == null) {
    const own = indexChain(market);
    if (own) return own;
  }
  const vix = firstUsable(market.vix_level);
  // [COHERENCE] Drive the narrative selection from the canonical regime engine
  // (posture + news tilt), NOT the legacy market.tone — otherwise the chain can
  // claim "Risk-on rotation" while the headline regime reads Mixed/Bearish.
  const engine = market.market_regime_engine || market.marketRegimeEngine || {};
  const postureTone = String((engine.colorTheme && engine.colorTheme.toneClass)
    || engine.postureTone || '').toLowerCase();
  const newsTilt = String((engine.newsImpact && engine.newsImpact.netDirection) || '').toLowerCase();
  const idxMoves = [chg('SPY'), chg('QQQ'), chg('IWM')].filter((v) => v != null);
  const allUp = idxMoves.length >= 2 && idxMoves.every((v) => v > 0);
  const allDown = idxMoves.length >= 2 && idxMoves.every((v) => v < 0);
  // The tape outranks a narrative: a bearish read on an all-green tape (or a
  // bullish one on an all-red tape) is a broad move, not a rotation.
  const bullishRegime = postureTone === 'bull' && newsTilt !== 'bearish' && !allDown;
  const bearishRegime = (postureTone === 'bear' || newsTilt === 'bearish') && !allUp;

  // Each candidate: a trigger + the downstream links, each with a live check.
  // `check` true = the tape corroborates that link right now. A missing quote
  // never corroborates anything. Every trigger level is MARKET.* config.
  const oilShock = Number(thr('MARKET.CHAIN_OIL_SHOCK_PCT'));
  const vixRegime = Number(thr('MARKET.CHAIN_VIX_REGIME'));
  const yieldJump = Number(thr('MARKET.CHAIN_YIELD_JUMP_PCT'));
  const spread = Number(thr('MARKET.CHAIN_LEADERSHIP_SPREAD_PCT'));
  let chain;
  if (oilCh != null && oilCh >= oilShock) {
    chain = { label: 'Energy shock', links: [
      { step: 'Oil spikes', check: oilCh >= oilShock },
      { step: 'Inflation/rate fear', check: tnxCh != null && tnxCh >= 0 },
      { step: 'Growth multiples pressured', check: qqqCh != null && spyCh != null && qqqCh <= spyCh },
      { step: 'Defensives & energy relative-bid', check: spyCh != null && qqqCh != null && spyCh >= qqqCh },
    ] };
  } else if (vix != null && vix >= vixRegime) {
    chain = { label: 'Volatility regime', links: [
      { step: 'Volatility elevated', check: vix >= vixRegime },
      { step: 'Risk budgets cut', check: spyCh != null && spyCh < 0 },
      { step: 'High-beta sold first', check: (qqqCh != null && spyCh != null && qqqCh < spyCh) || (iwmCh != null && spyCh != null && iwmCh < spyCh) },
      { step: 'Havens bid', check: (gldCh != null && gldCh > 0) || (tnxCh != null && tnxCh < 0) },
    ] };
  } else if (tnxCh != null && tnxCh >= yieldJump) {
    chain = { label: 'Rates pressure', links: [
      { step: 'Yields rise', check: tnxCh >= yieldJump },
      { step: 'Long-duration tech de-rates', check: qqqCh != null && spyCh != null && qqqCh < spyCh },
      { step: 'Value/cyclicals relative-lead', check: spyCh != null && qqqCh != null && spyCh >= qqqCh },
    ] };
  } else if (bullishRegime) {
    // Each link is confirmed only by the move it names, in the direction it
    // names. Equities lead; bitcoin counts only when the index read is absent.
    chain = { label: 'Risk-on rotation', links: [
      { step: 'Yields ease / stable', check: tnxCh != null && tnxCh <= 0 },
      { step: 'Risk appetite returns', check: spyCh != null ? spyCh > 0 : (btcCh != null && btcCh > 0) },
      { step: 'Breadth broadens', check: iwmCh != null && spyCh != null && iwmCh > 0 && iwmCh >= spyCh },
      { step: 'Leaders extend', check: qqqCh != null && qqqCh > 0 },
    ] };
  } else if (bearishRegime) {
    // A risk-off link is not confirmed by an index that rose: "leaders sold"
    // needs leaders down, not merely up less. (A bearish news tilt on a green
    // tape used to read "Confirmed 4/4"; it now reads as a hypothesis.)
    chain = { label: 'Risk-off rotation', links: [
      { step: 'Risk appetite fades', check: spyCh != null ? spyCh < 0 : (btcCh != null && btcCh < 0) },
      { step: 'Leaders sold first', check: qqqCh != null && spyCh != null && qqqCh < 0 && qqqCh <= spyCh },
      { step: 'Breadth narrows', check: iwmCh != null && spyCh != null && iwmCh < 0 && iwmCh <= spyCh },
      { step: 'Havens / cash bid', check: (gldCh != null && gldCh > 0) || (tnxCh != null && tnxCh < 0) },
    ] };
  } else {
    // A mixed tape is a claim like any other: each link is tested on the quotes
    // it names, and an unmeasured link stays unconfirmed.
    const idx = [spyCh, qqqCh, iwmCh].filter((v) => v != null);
    const together = idx.length >= 2 && (idx.every((v) => v > 0) || idx.every((v) => v < 0));
    if (together) {
      // Every measured index moved the same way: that is a broad move, not a
      // split tape, whatever the regime is called.
      const up = idx[0] > 0;
      chain = { label: up ? 'Broad advance' : 'Broad decline', links: [
        { step: up ? 'Indices rise together' : 'Indices fall together', check: true },
        { step: 'No clear leadership', check: qqqCh != null && spyCh != null && Math.abs(qqqCh - spyCh) < spread },
        { step: up ? 'Small caps keep pace' : 'Small caps fall with it', check: iwmCh != null && spyCh != null && Math.sign(iwmCh) === Math.sign(spyCh) && Math.abs(iwmCh - spyCh) < spread },
      ] };
    } else chain = { label: 'Mixed tape', links: [
      { step: 'Indices split', check: idx.length >= 2 && idx.some((v) => v > 0) && idx.some((v) => v < 0) },
      { step: 'No clear leadership', check: qqqCh != null && spyCh != null && Math.abs(qqqCh - spyCh) < spread },
      { step: 'Small caps diverge', check: iwmCh != null && spyCh != null && (Math.sign(iwmCh) !== Math.sign(spyCh) || Math.abs(iwmCh - spyCh) >= spread) },
    ] };
  }
  const confirmed = chain.links.filter((l) => l.check).length;
  chain.confirmed = confirmed;
  chain.total = chain.links.length;
  chain.verified = confirmed >= Math.ceil(chain.total * Number(thr('MARKET.CHAIN_CONFIRM_SHARE')));
  return chain;
}


// Plain-English "what it means" + "what to do" for a news catalyst.
function newsMeaning(item = {}) {
  const cat = String(item.category || '').toLowerCase();
  const impact = Number(item.impact || 0);
  if (item.catalyst_label === 'Market Wrap') return 'Index-level move: context for the tape, not a single-name catalyst.';
  if (cat.includes('macro')) return 'Moves rate-cut odds and cross-asset risk appetite.';
  if (cat.includes('earning')) return 'Resets expectations for the name and its peers.';
  if (cat.includes('deal')) return 'M&A signal — can re-rate the whole sector.';
  if (cat.includes('politic') || cat.includes('geo')) return 'Policy / geopolitical risk premium in play.';
  if (cat.includes('stock')) return 'Single-name catalyst; watch for sympathy moves.';
  return impact >= mkt('NEWS_IMPACT_MARKET_MOVING') ? 'Market-moving — confirm the tape reaction.' : 'Context for the tape, not yet a trigger.';
}



function formatRelativeTime(iso) {
  if (!iso) return '';
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '';
  const diffMs = Date.now() - t.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}


/* ---------------------------------------------------------------------------
   MARKET INTEL — Movers / Analyst Picks / Earnings
   --------------------------------------------------------------------------- */
function _currencySymbol() {
  const m = state.dashboard?.market?.currency || (state.market === 'IN' ? '₹' : '$');
  return m;
}

const SETUP_QUALITY = {
  buy: 'Buy-Qualified',
  watch: 'Watchlist',
  confirm: 'Needs Confirmation',
  extended: 'Too Extended',
  veto: 'Deep Analysis Veto',
  avoid: 'Avoid',
};

function setupQualityForMover(row) {
  const change = Number(row.change_pct || 0);
  const rsi = Number(row.rsi || row.rsi_14 || 0);
  const from20 = Number(row.distance_from_20dma || row.from_20dma || 0);
  if (Math.abs(change) >= mkt('EXTENDED_MOVE_PCT') || rsi >= mkt('EXTENDED_RSI') || from20 >= mkt('EXTENDED_FROM_20DMA')) return SETUP_QUALITY.extended;
  if (change > 0) return SETUP_QUALITY.watch;
  if (change < 0) return SETUP_QUALITY.avoid;
  return SETUP_QUALITY.confirm;
}


function attachDashboardActionHandlers(root = document) {
  $$('[data-jump-view]', root).forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.jumpView));
  });
  $$('[data-run-scan]', root).forEach((btn) => {
    // A scan shortcut opens Picks. It never changes the mandate (audit F14
    // retired the "aggressive scan" shortcut with the Aggressive mandate).
    btn.addEventListener('click', () => switchView('picks'));
  });
  // Dashboard empty states carry [data-scroll-to] buttons but never go through
  // wireExpandableRows, so those buttons did nothing. Wire them here too.
  wireScrollTargets(root);
}




// [VETO INTELLIGENCE] What must change for a rejected name to be valid again,
// keyed off the backend's stable rule codes (reason_codes) — the rule that
// actually blocked it, never words found in the display prose.
const VETO_REVALIDATION = {
  INVALID_TICKER: 'Not tradable as entered. Correct the symbol; nothing about the setup can be assessed until it resolves.',
  INVALID_INPUTS: 'Valid again once the analysis has usable, validated inputs.',
  INVALID_PRICE: 'Valid again once a current price is available from the data provider.',
  INVALID_STOP: 'Valid again once a usable stop level can be computed.',
  INVALID_TARGET: 'Valid again once a usable target can be computed.',
  STOP_ABOVE_ENTRY: 'Valid again once a stop sits below the entry price.',
  TARGET_BELOW_ENTRY: 'Valid again once the target sits above the entry price.',
  RISK_GRADE_F: 'Valid again only if the risk grade improves above F.',
  RISK_GRADE_D_CAP: 'Capped at Accumulate (reduced size) while the risk grade is D; a Buy needs the grade to improve.',
  RISK_GRADE_D_HOLD: 'Stays at reduced size while the risk grade is D; it needs the grade to improve for a full position.',
  HEAVY_INSIDER_SELLING: 'Valid again when verified 90-day insider selling no longer breaches the materiality threshold.',
  BUY_NOW_NO: 'Valid again when the entry timing signal turns from wait to buy.',
  TIMING_WAIT: 'Valid again when the entry timing signal turns from wait to buy.',
  BEARISH_REVERSAL: 'Re-assess once the reversal pattern is confirmed or invalidated by the next sessions.',
  DCF_OVERVALUATION: 'Valid again if price falls back toward modelled value, or the valuation inputs are revised.',
  // LOW_REWARD_RISK / SCANNER_LOW_RR: the engine writes the remedy from the
  // binding levels (binding_levels.remedy), with the hurdle that fired.
  LOW_REWARD_RISK: 'Valid again when reward/risk on the binding levels reaches the Research minimum.',
  SCANNER_LOW_RR: 'Valid again when reward/risk on the scanner levels reaches the scanner minimum.',
  LOW_SCORE: 'Valid again if the composite score rises above the minimum for a buy.',
  HOLD_NOT_OWNED: 'A hold rating gives no entry; valid again only if the score and gates support a buy.',
  AVOID_NOT_OWNED: 'Avoid. Valid again only if the score and gates improve enough to support a buy.',
  PROVIDER_RATE_LIMITED: 'Not a verdict on the setup: the data provider rate-limited the check. Re-run after the cooldown, or use safe mode.',
  PROVIDER_DATA_UNAVAILABLE: 'Not a verdict on the setup: the provider returned no usable data. Re-run the check when the provider recovers.',
  INSUFFICIENT_HISTORY: 'Valid again once enough price history exists to run the checks.',
  STALE_DATA: 'Valid again once fresh market data is available.',
  DEEP_CONFIRMATION_FAILED: 'Not a verdict on the setup: the confirmation check failed to run. Re-run it.',
  NOT_INDEPENDENTLY_CONFIRMED: 'Not a verdict on the setup: it was not independently confirmed. Re-run the confirmation check.',
};

function vetoRevalidation(codes, quietWhenEmpty = false) {
  const list = (Array.isArray(codes) ? codes : [codes]).map((c) => String(c || '').toUpperCase()).filter(Boolean);
  if (quietWhenEmpty && !list.length) return '';
  const seen = new Set();
  const lines = [];
  list.forEach((c) => {
    const text = VETO_REVALIDATION[c];
    if (text && !seen.has(text)) { seen.add(text); lines.push(text); }
  });
  return lines.length ? lines.join(' ') : 'The scanner did not report which rule applied, so no specific condition can be stated. Re-run the analysis for this name.';
}

/* ---------------------------------------------------------------------------
   SCAN / PICKS
   --------------------------------------------------------------------------- */

// Map US-universe scan_type → equivalent India scan_type (and vice versa)
// so flipping the market keeps a sensible default selected.
const SCAN_MARKET_EQUIV = {
  US_TO_IN: {
    quick: 'india_quick',
    standard: 'india_standard',
    comprehensive: 'india_comprehensive',
    full: 'india_comprehensive',
    focused_growth: 'india_growth',
    focused_value: 'india_value',
    focused_dividend: 'india_dividend',
    focused_momentum: 'india_momentum',
    focused_etf: 'india_standard',
    large_cap: 'india_large',
    mid_cap: 'india_mid',
    small_cap: 'india_small',
    pre_momentum: 'india_standard',
  },
  IN_TO_US: {
    india_quick: 'quick',
    india_standard: 'standard',
    india_sectoral: 'standard',
    india_comprehensive: 'comprehensive',
    india_full: 'full',
    india_growth: 'focused_growth',
    india_value: 'focused_value',
    india_dividend: 'focused_dividend',
    india_momentum: 'focused_momentum',
    india_large: 'large_cap',
    india_mid: 'mid_cap',
    india_small: 'small_cap',
  },
};

// Apply market-specific UI to the Portfolio-Scan form:
//   * hide/show scan options tagged with data-market
//   * pick a sensible default if the current selection is not allowed
//   * swap capital-budget label currency / hint
/* [A10] The budget typed for each market, kept across market switches so a
   valid large US budget is never replaced by a default on the way back. */
const picksBudgets = {};

/* [Scan filters] Units per market: cap in $ billions or Rs crore, traded value
   in $ millions or Rs crore, price in the market's currency. */
const SCAN_FILTER_UNITS = {
  US: { cap: ['$B', 1e9], value: ['$M', 1e6], price: ['$', 1] },
  IN: { cap: ['\u20b9 crore', 1e7], value: ['\u20b9 crore', 1e7], price: ['\u20b9', 1] },
};

function initScanFilters(market) {
  const box = $('#sp-sectors');
  if (box && !box.childElementCount) {
    box.innerHTML = (thr('UNIVERSE.SECTORS') || []).map((s) => `<label class="sp-f-check"><input type="checkbox" value="${escapeHtml(s)}"><span>${escapeHtml(s)}</span></label>`).join('');
    $('#sp-filters')?.addEventListener('input', updateScanFiltersCount);
    $('#sp-filters')?.addEventListener('change', updateScanFiltersCount);
    $('#sp-filters-clear')?.addEventListener('click', () => {
      document.querySelectorAll('#sp-filters input').forEach((i) => { if (i.type === 'checkbox') i.checked = false; else i.value = ''; });
      updateScanFiltersCount();
    });
  }
  const units = SCAN_FILTER_UNITS[market] || SCAN_FILTER_UNITS.US;
  document.querySelectorAll('.sp-f-unit').forEach((el) => { el.textContent = `(${units[el.dataset.unit][0]})`; });
  // Numbers mean different amounts in each market, so a market switch clears them.
  const panel = $('#sp-filters');
  if (panel && panel.dataset.market && panel.dataset.market !== market) {
    ['#sp-f-capmin', '#sp-f-capmax', '#sp-f-pmin', '#sp-f-pmax', '#sp-f-value'].forEach((sel) => { const el = $(sel); if (el) el.value = ''; });
  }
  if (panel) panel.dataset.market = market;
  updateScanFiltersCount();
}

/* The filters as the server reads them (raw amounts in the market's currency),
   or {error} when a range is reversed. */
function pickScanFilters() {
  const market = ($('#sp-market')?.value || 'US') === 'IN' ? 'IN' : 'US';
  const u = SCAN_FILTER_UNITS[market];
  const num = (sel, unit) => { const v = parseFloat($(sel)?.value); return Number.isFinite(v) && v > 0 ? v * unit : undefined; };
  const sectors = [...document.querySelectorAll('#sp-sectors input:checked')].map((i) => i.value);
  const f = {
    sectors: sectors.length ? sectors : undefined,
    min_market_cap: num('#sp-f-capmin', u.cap[1]), max_market_cap: num('#sp-f-capmax', u.cap[1]),
    min_price: num('#sp-f-pmin', 1), max_price: num('#sp-f-pmax', 1),
    min_daily_value: num('#sp-f-value', u.value[1]),
  };
  const out = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined));
  if ((out.min_market_cap && out.max_market_cap && out.min_market_cap > out.max_market_cap)
    || (out.min_price && out.max_price && out.min_price > out.max_price)) return { error: 'A "from" value is above its "to" value.' };
  return out;
}

function updateScanFiltersCount() {
  const f = pickScanFilters();
  const n = f.error ? 0 : Object.keys(f).length;
  const el = $('#sp-filters-count');
  if (el) el.textContent = n ? `${n} set` : 'optional, none set';
  $('#sp-filters')?.classList.toggle('is-active', n > 0);
}

/* The select shows each scan's short label; the hint under it spells out what
   that scan reads (the option's full text, without the name the label shows). */
function syncScanHint() {
  const sel = $('#sp-scan');
  const hint = $('#sp-scan-hint');
  if (!sel || !hint) return;
  const opt = sel.options[sel.selectedIndex];
  if (!opt) { hint.textContent = ''; return; }
  const text = opt.textContent.trim();
  const detail = text.includes(': ') ? text.slice(text.indexOf(': ') + 2) : text;
  hint.textContent = detail.charAt(0).toUpperCase() + detail.slice(1);
}

function applyPicksMarketUI(market) {
  const m = (market || 'US').toUpperCase();
  const select = $('#sp-scan');
  if (!select) return;
  initScanFilters(m === 'IN' ? 'IN' : 'US');

  // Filter <option> + <optgroup> visibility by data-market (no tag = both).
  Array.from(select.querySelectorAll('option')).forEach((opt) => {
    const tag = opt.dataset.market;
    opt.hidden = !!(tag && tag !== m);
    opt.disabled = opt.hidden;
  });
  Array.from(select.querySelectorAll('optgroup')).forEach((grp) => {
    const tag = grp.dataset.market;
    const visible = !tag || tag === m;
    grp.hidden = !visible;
    grp.disabled = !visible;
  });

  // If the currently selected scan_type is no longer valid for this market,
  // pick the equivalent (or first visible) option.
  const current = select.value;
  const currentOpt = select.querySelector(`option[value="${CSS.escape(current)}"]`);
  if (!currentOpt || currentOpt.hidden) {
    const equiv = m === 'IN'
      ? (SCAN_MARKET_EQUIV.US_TO_IN[current] || 'india_standard')
      : (SCAN_MARKET_EQUIV.IN_TO_US[current] || 'standard');
    const equivOpt = select.querySelector(`option[value="${CSS.escape(equiv)}"]`);
    select.value = equivOpt && !equivOpt.hidden ? equiv : (() => {
      const first = Array.from(select.querySelectorAll('option')).find((o) => !o.hidden);
      return first ? first.value : current;
    })();
  }

  syncScanHint();
  // Show/hide custom tickers field after any reset.
  const customField = $('#custom-tickers-field');
  if (customField) customField.classList.toggle('hidden', select.value !== 'custom');

  // Currency and floor for the capital field, stated under the input rather
  // than inside its label — a label that wraps offsets its own control from
  // the one beside it, which is what put this row a line out of true.
  const hint = $('#sp-capital-hint');
  const marketHint = $('#sp-market-hint');
  const capInput = $('#sp-capital');
  // A market switch resets the capital value and its hint, so any pending
  // field error no longer applies.
  if (hint) { delete hint.dataset.hint; hint.classList.remove('is-error'); }
  capInput?.removeAttribute('aria-invalid');
  const C = thr('CAPITAL') || {};
  const min = m === 'IN' ? C.MIN_INDIA : C.MIN_US;
  const fallback = m === 'IN' ? C.DEFAULT_INDIA : C.DEFAULT_US;
  if (m === 'IN') {
    if (hint) hint.textContent = `INR, minimum ₹${Number(C.MIN_INDIA).toLocaleString('en-IN')}`;
    if (marketHint) marketHint.textContent = 'NSE listings, prices in INR';
  } else {
    if (hint) hint.textContent = `USD, minimum ${fmtThreshold(C.MIN_US, 'money')}`;
    if (marketHint) marketHint.textContent = 'US listings, prices in USD';
  }
  // [A12] Before an India scan: listings with no corporate-event source fail
  // closed (EVENTS.UNCOVERED_LISTINGS), so the page says so up front.
  const notice = $('#sp-market-notice');
  if (notice) {
    const uncovered = m === 'IN' ? Object.values(thr('EVENTS.UNCOVERED_LISTINGS') || {}) : [];
    setNotice(notice, uncovered.length ? {
      text: `Watchlist calls only for ${uncovered.join(' and ')} listings`, moreLabel: 'Why',
      more: `Before a name gets new money, QUANTA checks it for a pending merger or delisting, and that check has no ${uncovered.join(' or ')} source yet.`,
    } : null);
  }
  if (capInput) {
    capInput.min = String(min);
    const prev = capInput.dataset.market;
    if (prev && prev !== m) {
      // Leaving a market: keep its budget, then restore this market's own.
      if (capInput.value !== '') picksBudgets[prev] = capInput.value;
      capInput.value = String(picksBudgets[m] ?? fallback);
    } else if (Number(capInput.value || 0) < min) {
      capInput.value = String(fallback);
    }
    capInput.dataset.market = m;
  }
}

// What each mandate actually does to position size. This used to be crammed
// into the option text, where it was clipped by the width of the control.
// Wording must track RISK_PROFILE_NOTE in app.py: the mandate only scales the
// allocation (and the narrative's tone). It does NOT change the universe,
// the screening gates or the candidate pool.
// One hint per offered mandate (SIZING.MANDATE_SCALE), each quoting its scale.
const MANDATE_HINTS = Object.fromEntries(Object.entries(thr('SIZING.MANDATE_SCALE') || {}).map(([k, v]) => [
  k, v >= 1 ? 'Full risk budget' : `${fmtThreshold(v, 'pct')} of the risk budget`,
]));

function applyMandateHint() {
  const hint = $('#sp-risk-hint');
  if (!hint) return;
  hint.textContent = MANDATE_HINTS[$('#sp-risk')?.value] || MANDATE_HINTS.balanced;
}

async function runScan(event, opts = {}) {
  if (event) event.preventDefault();
  const safeMode = !!opts.safeMode;
  const diversifyEl = $('#sp-diversify');
  // [A10] A safe-mode retry re-sends the interrupted scan's own parameters;
  // safe mode only lowers concurrency server-side, the universe is unchanged.
  const params = opts.params ? { ...opts.params, safe_mode: safeMode } : {
    market: $('#sp-market').value,
    capital: Number($('#sp-capital').value || thr('CAPITAL.DEFAULT_US')),
    scan_type: $('#sp-scan').value,
    risk_profile: $('#sp-risk').value,
    max_positions: Number($('#sp-positions').value || thr('CAPITAL.MAX_POSITIONS_DEFAULT')),
    diversify: diversifyEl ? !!diversifyEl.checked : true,
    safe_mode: safeMode,
  };
  if (!opts.params) {
    const filters = pickScanFilters();
    const hint = $('#sp-filters-hint');
    if (filters.error) {
      if (hint) { hint.textContent = filters.error; hint.classList.add('is-error'); }
      $('#sp-filters')?.setAttribute('open', '');
      return;
    }
    if (hint) { hint.textContent = 'Filters only narrow the scan; every built-in liquidity, price and safety rule still applies.'; hint.classList.remove('is-error'); }
    if (Object.keys(filters).length) params.filters = filters;
  }
  if (params.scan_type === 'custom' && !opts.params) {
    params.custom_tickers = $('#sp-custom-tickers').value
      .split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (!params.custom_tickers.length) {
      alert('Please enter at least one ticker.');
      return;
    }
  }

  state.lastScanParams = { ...params, safe_mode: false };
  const btn = $('#btn-scan'); const prog = $('#sp-progress'); const bar = $('#sp-bar'); const status = $('#sp-status'); const results = $('#picks-results');
  const scanToken = nextJobToken('scan');
  const scanCurrent = () => jobTokenCurrent('scan', scanToken);
  btn.disabled = true; prog.classList.add('visible'); prog.classList.remove('hidden');
  bar.style.width = '0%'; bar.style.background = '';
  status.textContent = safeMode ? 'Starting safe-mode retry (same scan, lower concurrency)…' : 'Starting scan…';
  setPicksMeta('Scanning…');
  results.innerHTML = '<div class="empty-state large"><h3>Building the position list…</h3><p>QUANTA is checking factor scores, regime fit, liquidity, sector exposure, risk/reward, and chase-risk before showing candidates.</p></div>';

  try {
    const res = await fetch('/api/stock-picks', { method: 'POST', headers: { ...QUANTA_POST_HEADERS, 'Content-Type': 'application/json' }, body: JSON.stringify(params) });
    const payload = await res.json();
    if (!scanCurrent()) return;
    if (res.status === 409 && payload.running_job_id) {
      // Another scan holds the single scan slot. Offer to give up on it rather
      // than leaving the user with a dead end until it times out.
      status.textContent = 'Another scan is running';
      setPicksMeta('Scan blocked — another scan is running');
      results.innerHTML = `<div class="error-box">
        <p>${escapeHtml(payload.error || 'A scan is already running.')}</p>
        <button type="button" class="q-btn" id="btn-cancel-running">Cancel the running scan</button>
      </div>`;
      const cancelBtn = $('#btn-cancel-running');
      if (cancelBtn) {
        cancelBtn.addEventListener('click', async () => {
          cancelBtn.disabled = true;
          try {
            await fetch(`/api/jobs/${encodeURIComponent(payload.running_job_id)}/cancel`,
              { method: 'POST', headers: QUANTA_POST_HEADERS });
          } catch (_) { /* fall through to the retry either way */ }
          runScan(null, opts);
        });
      }
      btn.disabled = false;
      return;
    }
    if (!res.ok) throw new Error(payload.error || 'Scan failed to start');
    renderScanStages(null);
    pollJob(payload.job_id, bar, status, (data) => {
      // The scan belongs to the market it was launched for.
      if (!scanCurrent()) return;
      onUserScanComplete(data, typeof window !== 'undefined' && !!window.matchMedia
        && window.matchMedia(`(max-width: ${PHONE_MAX_WIDTH}px)`).matches);
    }, btn, prog, results, scanCurrent);
  } catch (err) {
    if (!scanCurrent()) return;
    const msg = friendlyFetchError(err);
    status.textContent = 'Request failed';
    // The header still claimed "Scanning…" from the optimistic set above.
    setPicksMeta('Scan failed to start');
    results.innerHTML = `<div class="error-box">${escapeHtml(msg)}</div>`;
    btn.disabled = false;
  }
}

// [PROVIDER-RESILIENCE] Safe-mode retry: re-run with a smaller universe and
// minimal concurrency to recover from a data-provider rate limit.
function retrySafeMode() {
  return runScan(null, { safeMode: true, params: state.lastScanParams || null });
}

function convictionStars(score) {
  const n = Math.max(0, Math.min(5, Math.round(Number(score) || 0)));
  return `<span class="conviction-stars" role="img" aria-label="Conviction ${n} of 5" title="Conviction ${n}/5"><span aria-hidden="true">${'★'.repeat(n)}<span class="empty">${'★'.repeat(5 - n)}</span></span></span>`;
}

// One vector warning glyph for every caution/stop notice. Emoji (⚠ ⛔) render
// as colour pictographs on some platforms and ignore the theme tokens.
function warnIconSvg(tone = 'warn') {
  const path = tone === 'bear'
    ? '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 12h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
    : '<path d="M12 3.5 2.5 20h19L12 3.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17.3" r="1.1" fill="currentColor"/>';
  return `<svg class="q-ico q-ico--${tone}" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false">${path}</svg>`;
}

function aggregateSectors(picks) {
  const map = new Map();
  let total = 0;
  picks.forEach((p) => {
    const s = p.sector || 'Unknown';
    const val = finalAllocation(p);
    map.set(s, (map.get(s) || 0) + val);
    total += val;
  });
  return Array.from(map.entries())
    .map(([sector, value]) => ({ sector, value, pct: total ? (value / total) * 100 : 0 }))
    .sort((a, b) => b.value - a.value);
}

// Build the Deep Confirmation reconciliation summary table. Returns '' when
// there is nothing meaningful to show.
/* [A16] Which level set decided each published row, read from the row's own
   binding_levels (label and horizon), grouped. */
function levelSourceLine(recs) {
  const groups = new Map();
  (recs || []).forEach((p) => {
    const b = bindingLevels(p);
    const label = b ? String(b.label || titleLabel(b.source || 'levels')) : 'no binding level set published';
    const h = b && Number(b.horizon_days) > 0 ? `, ${Number(b.horizon_days)}-session horizon` : '';
    groups.set(label + h, (groups.get(label + h) || 0) + 1);
  });
  return [...groups].map(([k, n]) => `${k}: ${n} row${n === 1 ? '' : 's'}`).join(' · ');
}

function deepConfirmSummaryHtml(data, sym) {
  const dc = (data && data.deep_confirmation) || {};
  const records = Array.isArray(dc.records) ? dc.records : [];
  if (!records.length) return '';
  const downgrades = records.filter((r) => r.downgrade_flag);
  const headline = downgrades.length
    ? `${downgrades.length} of ${records.length} candidates restricted by additional risk checks`
    : `All ${records.length} candidates passed additional risk checks`;
  const rows = records.map((r) => {
    const changed = r.downgrade_flag;
    const before = Number(r.allocation_before || 0);
    const after = Number(r.allocation_after || 0);
  const mode = r.deep_mode === 'fast+insider' ? ' <span class="muted">(fast + insider)</span>'
    : r.deep_mode === 'fast' ? ' <span class="muted">(fast)</span>' : '';
    return `<tr class="${changed ? 'is-changed' : ''}">
      <td class="ticker">${escapeHtml(r.ticker || '')}</td>
      <td>${escapeHtml(titleLabel(r.scanner_action || ''))}</td>
      <td>${escapeHtml(titleLabel(r.deep_action || r.final_action || ''))}${mode}</td>
      <td>${pkActionBadge(r.final_action)}</td>
      <td class="mono">${changed ? `${fmtMoney(before, sym, 0)} → ${fmtMoney(after, sym, 0)}` : fmtMoney(after, sym, 0)}</td>
      <td class="muted">${r.scanner_risk_grade && r.deep_risk_grade && r.scanner_risk_grade !== r.deep_risk_grade
        ? `Risk ${escapeHtml(r.scanner_risk_grade)} (scanner) / ${escapeHtml(r.deep_risk_grade)} (additional checks). ` : ''}${escapeHtml(r.reason || '')}</td>
    </tr>`;
  }).join('');
  return `
    <section class="deep-confirm-summary">
      <header>
        <strong>Additional Risk Checks</strong>
        <span class="muted">${escapeHtml(headline)}</span>
      </header>
      <div class="dc-table-wrap table-scroll" tabindex="0" role="region" aria-label="Deep confirmation summary table">
        <table class="dc-table">
          <thead><tr><th>Ticker</th><th>Scanner signal</th><th>Raw deep verdict</th><th>Final Decision</th><th>Proposed $</th><th>Binding reason</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="qr-caveat muted">Checks data validity, risk grade, insider activity, reversal patterns and DCF overvaluation; this is not independent performance validation.${(data.recommendations || []).length ? ` Levels used: ${escapeHtml(levelSourceLine(data.recommendations))}.` : ''}</p>
    </section>`;
}

// Build deep-confirmation safety warnings (disabled override, unconfirmed picks).
function deepConfirmWarningsHtml(data) {
  const dc = (data && data.deep_confirmation) || {};
  const parts = [];
  if (dc.enabled === false) {
    parts.push(`<div class="dc-warning bear" role="alert">
      <strong>${warnIconSvg('bear')} Deep confirmation is DISABLED.</strong> These picks reflect the raw scanner only and have NOT been independently validated. Developer-only override — do not trade from this view.
    </div>`);
  }
  const notConfirmed = Array.isArray(dc.not_confirmed) ? dc.not_confirmed : [];
  if (notConfirmed.length) {
    parts.push(`<div class="dc-warning" role="status">
      <strong>${notConfirmed.length} pick${notConfirmed.length === 1 ? '' : 's'} could not complete additional risk checks:</strong> ${notConfirmed.map((r) => escapeHtml(r.ticker || '')).join(', ')}. No capital is allocated to them.
    </div>`);
  }
  return parts.join('');
}

// The page footer already says "educational use only, not investment advice";
// this line keeps only what the footer does not say (audit D1).
/* [P5] " · built on the <fetch> fetch's close $X of <session>" for a level set. */
function basisText(pb, sym) {
  if (!pb || !isPositive(pb.price)) return '';
  return ` · built on the ${escapeHtml(pb.fetch || 'scan')} fetch's close ${fmtPrice(pb.price, sym)}${pb.session ? ` of ${escapeHtml(pb.session)}` : ''}`;
}

/* [A12] One compact row for a long list: ticker, decision, allocation and the
   binding reason; the full card mounts when the row is opened. */
function compactPickRow(rec, sym, idx) {
  const meta = actionMeta(rec);
  const funded = isFundedPick(rec);
  const why = sanitizeExceptionText(rec.why || rec.deep_confirmation_reason || (rec.final_action_reasons || []).at(-1) || '');
  return `<details class="pick-lazy ${funded ? 'pick-funded' : 'pick-watch'}" data-pick-index="${idx}">
      <summary><strong>${escapeHtml(rec.ticker || '?')}</strong> <span class="signal-pill ${meta.key} ${meta.tone}">${escapeHtml(meta.label)}</span>
      <span class="mono">${funded ? fmtMoney(finalAllocation(rec), sym, 0) : 'no new capital'}</span>
      <span class="muted">${escapeHtml(why)}</span></summary>
    </details>`;
}

/* One failing book gate in words: the value now and the value it needs
   (closest to qualifying, and the I3 event-held diagnostic). */
function gateShortfallText(r, sym) {
  const now = Number(r.now); const need = Number(r.needed);
  switch (r.gate) {
    case 'risk_reward':
      if (r.stop != null && r.entry_needed != null) {
        return `Reward to risk ${fmtRR(now)}, needs ${fmtRR(need)}: entry at or below ${fmtPrice(r.entry_needed, sym)} with stop ${fmtPrice(r.stop, sym)} and target ${fmtPrice(r.target, sym)}, or target at or above ${fmtPrice(r.target_needed, sym)}`;
      }
      return `Reward to risk ${fmtRR(now)}, needs ${fmtRR(need)}`;
    case 'score': return `Score ${fmtScore100(now)} of 100, needs ${fmtScore100(need)}`;
    case 'actionable': return `Score ${fmtScore100(now)} of 100; Accumulate needs ${fmtScore100(need)}`;
    case 'conviction': return `Conviction ${fmtNumber(now, 0)} of 5, needs ${fmtNumber(need, 0)}${r.kept_if_none_clear ? ' (kept and flagged if no name clears it)' : ''}`;
    case 'cost': return `Expected return ${fmtPct(now)} does not cover ${fmtPct(need)} of trading costs${r.target_needed != null ? `: target at or above ${fmtPrice(r.target_needed, sym)}` : ''}`;
    case 'risk_grade': return `Risk grade ${r.now} is excluded`;
    default: return '';
  }
}

/* A list of names: the first UI.NAME_LIST_SHOWN items, the rest behind a
   "Show all" control so a long list never floods the result. */
function nameListHtml(items, cls) {
  const shown = Math.max(1, Number(thr('UI.NAME_LIST_SHOWN')) || items.length);
  const head = items.slice(0, shown).join('');
  const rest = items.slice(shown);
  return `<ul class="${cls}">${head}</ul>${rest.length ? `<details class="name-list-more"><summary>Show all ${items.length}</summary><ul class="${cls}">${rest.join('')}</ul></details>` : ''}`;
}

/* [I3] Names held at Watch by the corporate-event check, run through the
   later book gates. Diagnostic only: the book never funds them. */
function eventHeldDiagnosticsHtml(data) {
  const rows = (data && data.event_held_diagnostics) || [];
  if (!rows.length) return '';
  const sym = data.currency || '$';
  const items = rows.map((r) => {
    const verdict = r.verdict === 'event_only'
      ? 'Blocked only by the event check'
      : `Also fails: ${(r.also_fails || []).map((f) => gateShortfallText(f, sym)).filter(Boolean).join(' · ')}`;
    return `<li data-diag-ticker="${escapeHtml(r.ticker)}"><strong>${escapeHtml(r.ticker)}</strong> <span class="muted">${escapeHtml(r.company || '')}</span> ${escapeHtml(verdict)}</li>`;
  });
  return `<section class="watchlist-group event-diagnostic" data-event-diagnostic aria-labelledby="event-diag-h">
      <header><strong id="event-diag-h">If the event check had cleared them (${rows.length}) · diagnostic only, they stay on the Watchlist</strong>
      <span class="muted">Held at Watchlist by the corporate-event check, run through the risk grade, score, reward-to-risk, conviction and cost rules. Position, sector, correlation and stop-risk limits and the Research check were not assessed.</span></header>
      ${nameListHtml(items, 'closest-list')}
    </section>`;
}

/* [P3] Names that passed every rule but were not funded, with selection's
   one plain reason and, when one share is too costly, the capital needed. */
function passedNotFundedHtml(data) {
  const rows = (data && data.passed_not_funded) || [];
  if (!rows.length) return '';
  const sym = data.currency || '$';
  const items = rows.map((r) => {
    const need = Number(r.capital_needed) > 0 ? ` · Capital needed ${fmtMoney(Number(r.capital_needed), sym, 0)}` : '';
    return `<li data-pnf-ticker="${escapeHtml(r.ticker)}"><strong>${escapeHtml(r.ticker)}</strong> <span class="muted">${escapeHtml(r.company || '')}</span> ${escapeHtml(r.text || '')}${escapeHtml(need)}</li>`;
  });
  return `<section class="watchlist-group passed-not-funded" data-passed-not-funded aria-labelledby="pnf-h">
      <header><strong id="pnf-h">Passed the rules, no position in this book (${rows.length})</strong>
      <span class="muted">Each passed every screening and risk rule; the portfolio limits kept it out of this book.</span></header>
      ${nameListHtml(items, 'closest-list')}
    </section>`;
}

/* [A12] Book names nearest to passing, each with the value its first failing
   gate needs (from result.closest_to_qualifying; prices from published levels). */
function closestToQualifyingHtml(data) {
  const rows = (data && data.closest_to_qualifying) || [];
  if (!rows.length) return '';
  const sym = data.currency || '$';
  const one = (r) => gateShortfallText(r, sym);
  return `<section class="watchlist-group closest" data-closest aria-labelledby="closest-h">
      <header><strong id="closest-h">Closest to qualifying (${rows.length})</strong>
      <span class="muted">Names that reached the book, nearest to their first failing rule. Not picks.</span></header>
      <ul class="closest-list">${rows.map((r) => `<li data-closest-ticker="${escapeHtml(r.ticker)}"><strong>${escapeHtml(r.ticker)}</strong> <span class="muted">${escapeHtml(r.company || '')}</span> ${escapeHtml(one(r))}</li>`).join('')}</ul>
    </section>`;
}

/* [A12] The one evidence and disclaimer note for a result, placed directly
   after the summary. Evidence words come from the picks' own status; the
   confidence caveat from the pick data when the engine supplied one. */
function sharedResultsNoteHtml(picks) {
  const statuses = [...new Set((picks || []).map((p) => (p.decision?.evidence || {}).status).filter(Boolean))];
  const evidence = statuses.length
    ? `Model evidence: ${statuses.map((st) => evidencePlain({ status: st })).join(', ')}. Proposed sizing is a research risk budget until the model is validated out of sample.`
    : 'Model evidence unavailable: treat proposed sizing as a research risk budget.';
  const caveat = (picks || []).map((p) => p.signal_strength_caveat).find(Boolean)
    || 'Scanner strength is a heuristic tier, not a win probability.';
  return `<aside class="results-note" data-shared-note role="note">
      <p>${escapeHtml(evidence)} ${escapeHtml(caveat)}</p>
      <p class="muted">Planned loss at stops assumes fills at the stop and excludes gaps and execution costs. Sector weights use deployed capital; sizing caps use total capital. Existing holdings are not included. Validate independently and paper-trade before risking capital.</p>
    </aside>`;
}


// ---------------------------------------------------------------------------
// [SCAN-HEALTH] Scan-outcome messaging + error-details surfacing.
// These keep a systemic failure (e.g. 100% data-provider errors) from being
// mislabelled as a benign "no buy-qualified setups" result.
// ---------------------------------------------------------------------------
const ERROR_TYPE_LABELS = {
  analysis_exception: 'Analysis Exception',
  data_provider_error: 'Data Provider Error',
  provider_rate_limited: 'Provider Rate Limited',
  missing_price_data: 'Missing Price Data',
  missing_fundamentals: 'Missing Fundamentals',
  import_error: 'Backend Import Error',
  serialization_error: 'Serialization Error',
  timeout: 'Timeout',
  invalid_ticker: 'Invalid Ticker',
};

function errorTypeLabel(t) {
  return ERROR_TYPE_LABELS[t] || titleLabel(String(t || 'Error').replace(/_/g, ' '));
}

// [PROVIDER-RESILIENCE] Build the tear-sheet rate-limit message shape from the
// structured `error_detail` returned by /api/analyze. Pure function → testable.
function tearSheetRateLimitMessage(detail) {
  const d = detail || {};
  const cooldown = Math.max(0, Math.round(Number(d.cooldown_remaining_seconds || 0)));
  const ticker = d.ticker || 'this ticker';
  return {
    tone: 'warn',
    rateLimited: true,
    ticker: d.ticker || null,
    provider: d.provider || 'yfinance',
    errorType: d.error_type || 'provider_rate_limited',
    attempts: Number(d.attempts || 0),
    retryable: d.retryable !== false,
    cooldown,
    lastSuccess: d.last_successful_fetch || null,
    usedCachedData: !!d.used_cached_data,
    title: 'Data provider rate-limited',
    body: d.error_message
      || `Yahoo Finance is temporarily rate-limiting requests for ${ticker}.`,
    suggested: d.suggested_action
      || 'Wait for the cooldown to elapse, retry in Safe Mode, or use cached data.',
  };
}

let _tearSheetCooldownTimer = null;

// [PROVIDER-RESILIENCE] Render the rate-limit banner into the tear-sheet panel
// with a live cooldown countdown and Safe-Mode / Use-cached-data actions.
function renderTearSheetRateLimit(detail, hasCache) {
  const results = document.getElementById('an-results');
  if (!results) return;
  const m = tearSheetRateLimitMessage(detail);
  const ticker = m.ticker || (document.getElementById('an-ticker')?.value || '').trim().toUpperCase();
  const lastSuccess = m.lastSuccess
    ? new Date(m.lastSuccess).toLocaleString()
    : '—';
  const cacheBtn = hasCache
    ? `<button type="button" class="btn-secondary" id="ts-use-cache">Use cached data</button>`
    : '';
  results.innerHTML = `
    <div class="error-box" role="alert" style="background:rgba(255,180,90,0.10);border:1px solid rgba(255,180,90,0.35);">
      <h3 style="margin:0 0 6px;">⏳ ${escapeHtml(m.title)}</h3>
      <p style="margin:0 0 10px;">${escapeHtml(m.body)}</p>
      <ul style="margin:0 0 12px;padding-left:18px;line-height:1.7;">
        <li><strong>Ticker:</strong> ${escapeHtml(ticker || '—')}</li>
        <li><strong>Provider:</strong> ${escapeHtml(m.provider)}</li>
        <li><strong>Error type:</strong> ${escapeHtml(errorTypeLabel(m.errorType))}</li>
        <li><strong>Attempts:</strong> ${m.attempts}</li>
        <li><strong>Retryable:</strong> ${m.retryable ? 'Yes' : 'No'}</li>
        <li><strong>Cooldown remaining:</strong> <span id="ts-cooldown">${m.cooldown}s</span></li>
        <li><strong>Last successful fetch:</strong> ${escapeHtml(lastSuccess)}</li>
      </ul>
      <p style="margin:0 0 12px;opacity:0.85;">${escapeHtml(m.suggested)}</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <button type="button" class="btn-primary" id="ts-retry-safe" disabled>Retry in Safe Mode</button>
        ${cacheBtn}
      </div>
    </div>`;

  const safeBtn = document.getElementById('ts-retry-safe');
  const cdSpan = document.getElementById('ts-cooldown');
  let remaining = m.cooldown;

  const tick = () => {
    remaining = Math.max(0, remaining - 1);
    if (cdSpan) cdSpan.textContent = `${remaining}s`;
    if (remaining <= 0) {
      window.clearInterval(_tearSheetCooldownTimer);
      _tearSheetCooldownTimer = null;
      if (safeBtn) { safeBtn.disabled = false; safeBtn.textContent = 'Retry now'; }
    }
  };
  if (_tearSheetCooldownTimer) window.clearInterval(_tearSheetCooldownTimer);
  if (remaining > 0) {
    _tearSheetCooldownTimer = window.setInterval(tick, 1000);
  } else if (safeBtn) {
    safeBtn.disabled = false;
    safeBtn.textContent = 'Retry now';
  }

  if (safeBtn) safeBtn.addEventListener('click', () => runAnalysis(null, { safeMode: true }));
  const useCacheBtn = document.getElementById('ts-use-cache');
  if (useCacheBtn) useCacheBtn.addEventListener('click', () => runAnalysis(null, { preferCache: true }));
}

// [PROVIDER-RESILIENCE] Detect a structured rate-limit job error and render the
// dedicated tear-sheet banner. Returns true when it handled the payload.
function handleAnalyzeJobError(payload) {
  const detail = payload && payload.error_detail;
  if (!detail) return false;
  if (detail.status === 'provider_rate_limited') {
    renderTearSheetRateLimit(detail, false);
    return true;
  }
  if (detail.category && RESEARCH_FAILURE[detail.category]) {
    renderResearchDiagnostic(detail);
    return true;
  }
  return false;
}

// One label and tone per failure category the backend publishes
// (engine.symbols.FAILURE_CATEGORIES). The copy itself comes from the backend.
const RESEARCH_FAILURE = {
  invalid_symbol: { chip: 'Symbol not found', tone: 'bear' },
  unsupported_instrument: { chip: 'Not a stock or fund', tone: 'info' },
  insufficient_data: { chip: 'Too new to score', tone: 'info' },
  stale_data: { chip: 'No current prices', tone: 'warn' },
  provider_unavailable: { chip: 'Provider gap', tone: 'warn' },
  temporary_failure: { chip: 'Temporary', tone: 'warn' },
  engine_error: { chip: 'Analysis error', tone: 'bear' },
};

// A failed Research run still says what is known and what to do next,
// instead of collapsing the page to one sentence.
function researchDiagnosticHtml(detail) {
  const d = detail || {};
  const meta = RESEARCH_FAILURE[d.category] || { chip: 'No Decision', tone: 'warn' };
  const f = d.facts || {};
  const sym = f.currency_symbol || '$';
  const known = [
    ['You entered', f.requested_symbol],
    f.resolved_symbol && f.resolved_symbol !== f.requested_symbol ? ['Looked up as', f.resolved_symbol] : null,
    ['Name', f.company_name],
    ['Instrument', f.quote_type ? titleLabel(String(f.quote_type).toLowerCase()) : ''],
    ['Exchange', exchangeName(f.exchange)],
    ['Last price', f.last_price != null ? `${fmtPrice(f.last_price, sym)}${f.last_bar_date ? ` · ${f.last_bar_date}` : ''}` : ''],
    ['Market cap', f.market_cap ? fmtCurrency(f.market_cap, sym, 0) : ''],
    ['Price history', f.history_bars ? `${f.history_bars} trading session${f.history_bars === 1 ? '' : 's'}${f.first_bar_date ? ` since ${f.first_bar_date}` : ''}` : ''],
  ].filter((row) => row && row[1]);
  const other = state.market === 'IN' ? 'US' : 'IN';
  const actions = [
    d.retryable ? '<button type="button" class="q-btn q-btn--primary" data-diag-action="retry">Retry</button>' : '',
    d.can_switch_market ? `<button type="button" class="q-btn" data-diag-action="market" data-market="${other}">Try the ${other === 'IN' ? 'India (NSE/BSE)' : 'US'} listing</button>` : '',
    ...(d.alternatives || []).map((alt) => `<button type="button" class="q-btn" data-diag-action="ticker" data-ticker="${escapeHtml(alt)}">Research ${escapeHtml(alt)}</button>`),
  ].filter(Boolean).join('');
  return `
    <article class="research-diagnostic tone-${meta.tone}" role="alert" data-diagnostic="${escapeHtml(d.category || '')}">
      <header class="rd-head">
        <span class="rd-chip">${escapeHtml(meta.chip)}</span>
        <h3 class="rd-title">${escapeHtml(d.title || 'No Decision for this symbol')}</h3>
        <p class="rd-body">${escapeHtml(d.error_message || '')}</p>
      </header>
      ${f.symbol_note ? `<p class="rd-note">${escapeHtml(f.symbol_note)}</p>` : ''}
      ${known.length ? `<section class="rd-known" aria-label="What is known">
        <h4>What the provider has</h4>
        <dl class="rd-facts">${known.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('')}</dl>
      </section>` : ''}
      <section class="rd-next" aria-label="Next step">
        <h4>Next step</h4>
        <p>${escapeHtml(d.suggested_action || '')}</p>
        ${actions ? `<div class="rd-actions">${actions}</div>` : ''}
      </section>
      <p class="rd-meta muted">No Decision was produced, so nothing on this page is a recommendation. Source: ${escapeHtml(d.provider || 'provider')} · status ${escapeHtml(d.data_status || d.category || '')}${d.attempts ? ` · ${d.attempts} attempt${d.attempts === 1 ? '' : 's'}` : ''}</p>
    </article>`;
}

function renderResearchDiagnostic(detail) {
  const results = document.getElementById('an-results');
  if (!results) return;
  results.innerHTML = researchDiagnosticHtml(detail);
  results.querySelectorAll('[data-diag-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const act = btn.getAttribute('data-diag-action');
      if (act === 'market') {
        const toggle = document.querySelector(`.market-toggle .mkt-btn[data-market="${btn.getAttribute('data-market')}"]`);
        if (toggle) toggle.click();
      } else if (act === 'ticker') {
        const input = document.getElementById('an-ticker');
        if (input) input.value = btn.getAttribute('data-ticker');
      }
      runAnalysis();
    });
  });
}

// [PROVIDER-RESILIENCE] Banner shown atop a tear sheet that was served from
// cache because the provider was rate-limiting at fetch time.
function tearSheetCacheBannerHtml(data) {
  if (!data || !data.used_cached_data) return '';
  const ts = data.cache_timestamp
    ? new Date(Number(data.cache_timestamp) * 1000).toLocaleTimeString()
    : null;
  const when = ts ? ` from ${ts}` : '';
  const rl = data.provider_rate_limited
    ? ' Yahoo Finance is currently rate-limiting live requests.'
    : '';
  return `
    <div class="error-box is-warn" role="status">
      <strong>Using cached data${escapeHtml(when)}.</strong>${escapeHtml(rl)}
      Figures may be stale until the provider cooldown clears.
    </div>`;
}


// [PERF-FASTMODE] Banner shown when a tear sheet was produced in Fast Mode.
// The network-heavy optional modules (news, insider, analyst consensus,
// catalysts, peers) were skipped and replaced with neutral defaults, so the
// recommendation is PRELIMINARY. Offers a one-click "Load full analysis".
// The Decision's own qualifiers under the headline label: its horizon, and
// whether the model behind it has been validated out of sample. The label is
// the engine's; nothing here derives or softens an action.
function decisionEvidenceHtml(decision) {
  if (!decision) return '';
  const ev = decision.evidence || {};
  const horizon = decision.horizon && decision.horizon.label;
  const status = (ev.status || ev.status_text) ? evidencePlain(ev) : '';
  const tone = ev.permits_recommendation ? 'bull' : 'warn';
  return `<span class="muted decision-evidence" data-evidence-status="${escapeHtml(ev.status || 'UNKNOWN')}">`
    + [horizon && escapeHtml(horizon),
       status && `Track record: <b class="${tone}" title="${escapeHtml(ev.status_text || '')}">${escapeHtml(status)}</b>`]
      .filter(Boolean).join(' · ')
    + '</span>'
    + (decision.caveat ? `<span class="muted decision-caveat">${escapeHtml(decision.caveat)}</span>` : '');
}

function tearSheetFastModeBannerHtml(data) {
  if (!data || !data.fast_mode) return '';
  const t = escapeHtml(String(data.ticker || '').toUpperCase());
  // One line beside the call: what was skipped and the action that runs it.
  return `
    <p class="ts-fast-note" role="status"><span><strong>Fast preview: preliminary.</strong> News, insider activity, analyst consensus, catalysts and peers were skipped and treated as neutral.</span> <button type="button" class="q-btn q-btn--sm" data-load-full-analysis="${t}">Load full analysis</button></p>`;
}


function scanStatusMessage(data) {
  const universe = Number(data?.error_summary?.universe_size || data?.funnel?.universe_size || data?.total_scanned || 0);
  const failed = Number(data?.analysis_failed_count || data?.error_summary?.analysis_failed_count || 0);
  const rate = Number(data?.exception_rate || data?.error_summary?.exception_rate || 0);
  const pct = Math.round(rate * 100);
  const providerStatus = data?.provider_status || data?.error_summary?.provider_status || {};
  const cooldown = Math.round(Number(providerStatus.cooldown_remaining_seconds || 0));
  const rlCount = Number(data?.provider_rate_limited_count || data?.error_summary?.provider_rate_limited_count || 0);
  const cooldownLine = cooldown > 0 ? ` Provider cooldown: ~${cooldown}s remaining.` : '';
  switch (data?.scan_status) {
    case 'no_universe':
      return { tone: 'bear', title: 'Scan failed: empty universe',
        body: 'No tickers entered the scan. Check the scan type, market, or custom ticker list.' };
    case 'provider_rate_limited':
      return { tone: 'bear', rateLimited: true,
        title: `Scan interrupted: data provider rate-limited after ${rlCount || failed} failures out of ${universe} tickers`,
        body: `Provider: yfinance · Failure type: Rate limited · Retryable: Yes.${cooldownLine} `
          + 'Wait ~60 seconds, then click "Retry with safe mode" (same scan, lower concurrency), '
          + 'or choose a smaller scan yourself.' };
    case 'failed':
      return { tone: 'bear', title: `Scan failed: ${failed}/${universe} tickers hit analysis exceptions (${pct}%)`,
        body: 'This is a backend/data-provider failure, not a filtering result. Review the error details below.' };
    case 'partial':
      return { tone: 'warn', title: `Partial scan: ${failed} of ${universe} names hit provider or analysis errors`,
        body: 'Some tickers could not be analyzed. No setups qualified among the ones that succeeded.' };
    case 'partial_with_picks':
      return { tone: 'warn', rateLimited: rlCount > 0,
        title: `Partial scan completed: ${failed} of ${universe} names hit errors${rlCount > 0 ? ` (${rlCount} rate-limited)` : ''}`,
        body: rlCount > 0
          ? `The data provider rate-limited part of the scan.${cooldownLine} Picks below come from the names that succeeded; `
            + 'wait ~60s and retry with Quick Scan or safe mode for full coverage.'
          : 'Some tickers could not be analyzed. Picks below come from the names that succeeded.' };
    default:
      return null;
  }
}

function scanStatusBannerHtml(data) {
  const msg = scanStatusMessage(data);
  if (!msg) return '';
  const icon = warnIconSvg(msg.tone === 'bear' ? 'bear' : 'warn');
  const retryBtn = msg.rateLimited
    ? '<button class="btn-mini" data-safe-mode-retry>Retry with safe mode</button>'
    : '';
  return `
    <div class="scan-status-banner ${msg.tone === 'bear' ? 'bear' : 'warn'}" role="status">
      <p><strong>${icon} ${escapeHtml(msg.title)}</strong></p>
      <p class="muted">${escapeHtml(msg.body)}</p>
      ${retryBtn}
    </div>`;
}

/* [I1] The corporate-event check could not run for some names: say why and
   what clears it. Labels and fixes come from the payload (the engine's
   UNAVAILABLE_REASONS), so the page never words a remedy of its own. */
function eventCheckBannerHtml(data) {
  const ev = data && data.event_check;
  if (!ev) return '';
  const contact = ev.contact || {};
  const contactBad = contact.state === 'missing' || contact.state === 'malformed';
  const n = Number(ev.unavailable || 0);
  if (!contactBad && !n) return '';
  const reasons = Array.isArray(ev.reasons) ? ev.reasons : [];
  const plural = (k) => `${k} name${k === 1 ? '' : 's'}`;
  const shown = Math.max(1, Number(thr('UI.NAME_LIST_SHOWN')) || 5);
  const items = reasons.map((r) => {
    const book = Array.isArray(r.book_tickers) && r.book_tickers.length
      ? ` (${r.book_tickers.join(', ')} reached the book)` : '';
    // [P2] Name every ticker the check could not read; a long list folds behind Show all.
    const all = Array.isArray(r.tickers) ? r.tickers.map(String) : [];
    const names = all.length ? `: ${escapeHtml(all.slice(0, shown).join(', '))}${all.length > shown
      ? ` <details class="name-list-more"><summary>Show all ${all.length}</summary>${escapeHtml(all.slice(shown).join(', '))}</details>` : ''}` : '';
    return `<li><strong>${escapeHtml(r.label || r.reason || '')}</strong> · ${escapeHtml(plural(Number(r.count || 0)))}${names}${escapeHtml(book)}. Fix: ${escapeHtml(r.fix || '')}</li>`;
  });
  if (contactBad && !reasons.some((r) => r.reason === `contact_${contact.state}`)) {
    items.unshift(`<li><strong>${escapeHtml(contact.label || '')}</strong>. Fix: ${escapeHtml(contact.fix || '')}</li>`);
  }
  const of = Number(ev.checked || 0);
  const scope = of ? ` for ${n} of ${plural(of)} checked` : '';
  return `
    <div class="scan-status-banner warn event-check-banner" role="status" data-event-check-banner>
      <p><strong>${warnIconSvg('warn')} Corporate-event check unavailable${escapeHtml(scope)}</strong></p>
      <p class="muted">A name the check could not clear stays on the Watchlist and gets no Buy call.</p>
      <ul class="event-check-reasons">${items.join('')}</ul>
    </div>`;
}

/* Styling pass: one notice component. One line; the steps ("Fix") or the
   reason ("Why") sit behind a disclosure. */
function noticeHtml(n) {
  if (!n || !n.text) return '';
  const line = `<span class="q-notice-t">${escapeHtml(n.text)}</span>`;
  if (!n.more) return `<div class="q-notice-line">${line}</div>`;
  return `<details><summary>${line}<span class="q-notice-more">${escapeHtml(n.moreLabel || 'Details')}</span></summary>`
    + `<p class="q-notice-body">${escapeHtml(n.more)}</p></details>`;
}
function setNotice(el, n) {
  if (!el) return;
  el.innerHTML = noticeHtml(n);
  el.hidden = !(n && n.text);
}

/* [I1] Before a scan: the server's SEC contact state from /api/health. */
function secContactNotice(health) {
  const c = health && health.sec_contact;
  if (!c || (c.state !== 'missing' && c.state !== 'malformed')) return null;
  return { text: `Buy calls blocked: ${c.label || ''}`, moreLabel: 'Fix',
    more: `The corporate-event check cannot run, so no US name can get a Buy call. ${c.fix || ''}.` };
}
function secContactNoticeText(health) {
  const c = health && health.sec_contact;
  if (!c || (c.state !== 'missing' && c.state !== 'malformed')) return '';
  return `Corporate-event check unavailable: ${c.label || ''}. No US name can get a Buy call until it is fixed. Fix: ${c.fix || ''}.`;
}

async function refreshSecContactNotice() {
  const el = $('#sp-sec-notice');
  if (!el) return;
  try {
    const health = await fetchJSON('/api/health?probe=0', { retries: 0, timeoutMs: 8000 });
    setNotice(el, ($('#sp-market')?.value || 'US') === 'US' ? secContactNotice(health) : null);
  } catch (_) { el.hidden = true; }
}

// Wire any "Retry with safe mode" buttons inside a rendered container.
function wireSafeModeRetry(container) {
  if (!container) return;
  container.querySelectorAll('[data-safe-mode-retry]').forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      if (typeof retrySafeMode === 'function') retrySafeMode();
    });
  });
}

function errorDetailsCardHtml(data) {
  const summary = data?.error_summary || {};
  const records = Array.isArray(summary.records) ? summary.records : [];
  if (!records.length) return '';
  const byType = summary.by_type || {};
  const typeChips = Object.entries(byType)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([k, v]) => `<span class="err-chip">${escapeHtml(errorTypeLabel(k))} · ${escapeHtml(String(v))}</span>`)
    .join('');
  const rows = records.slice(0, 20).map((r) => `
    <tr>
      <td class="ticker">${escapeHtml(r.ticker || '')}</td>
      <td><span class="err-type-tag">${escapeHtml(errorTypeLabel(r.error_type))}</span></td>
      <td class="err-msg">${escapeHtml(String(r.error_message || '').slice(0, 160))}</td>
      <td>${escapeHtml(r.stage || '')}</td>
      <td>${r.retryable ? '<span class="bull">Yes</span>' : '<span class="muted">No</span>'}</td>
      <td class="err-fix">${escapeHtml(r.suggested_fix || '')}</td>
    </tr>`).join('');
  const more = records.length > 20 ? `<p class="muted" style="margin:8px 0 0;font-size:11.5px;">Showing top 20 of ${records.length}. Use the download buttons for the full log.</p>` : '';
  return `
    <details class="error-details-card" style="margin:14px 0;">
      <summary style="cursor:pointer;padding:12px 16px;font-weight:700;font-size:13px;list-style:none;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span>Analysis Error Details (${records.length})</span>
        <span style="display:flex;gap:6px;flex-wrap:wrap;">${typeChips}</span>
      </summary>
      <div style="padding:0 16px 14px;">
        <div style="display:flex;gap:8px;margin:4px 0 10px;">
          <button class="btn-mini" data-err-export="csv">Download CSV</button>
          <button class="btn-mini" data-err-export="json">Download JSON</button>
        </div>
        <div style="overflow-x:auto;">
          <table class="error-details-table" style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead>
              <tr style="text-align:left;">
                <th style="padding:6px 8px;">Ticker</th><th style="padding:6px 8px;">Error Type</th>
                <th style="padding:6px 8px;">Message</th><th style="padding:6px 8px;">Stage</th>
                <th style="padding:6px 8px;">Retryable?</th><th style="padding:6px 8px;">Suggested Fix</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        ${more}
      </div>
    </details>`;
}

function wireErrorExport(container, data) {
  const records = (data?.error_summary?.records) || [];
  if (!records.length) return;
  container.querySelectorAll('[data-err-export]').forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      const kind = btn.getAttribute('data-err-export');
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      if (kind === 'json') {
        downloadBlob(JSON.stringify(records, null, 2), `scan-errors-${stamp}.json`, 'application/json');
      } else {
        const cols = ['ticker', 'error_type', 'error_message', 'exception_class', 'stage', 'retryable', 'suggested_fix'];
        const esc = (v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
        const csv = [cols.join(',')].concat(records.map((r) => cols.map((c) => esc(r[c])).join(','))).join('\n');
        downloadBlob(csv, `scan-errors-${stamp}.csv`, 'text/csv');
      }
    });
  });
}

function downloadBlob(content, filename, mime) {
  try {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    console.warn('download failed', e);
  }
}

// [SCAN-HEALTH] Backend health panel — surfaces python env, import status,
// data-provider availability and last-scan meta so a 100% scan failure can be
// traced to its real cause (import error vs provider outage vs no network).

/* Every panel whose content belongs to one country. On a market switch these
   must ALL be cleared: the switch used to blank three of them and leave the
   rest — ribbon, headlines, drivers, catalysts, news — showing the previous
   market's data until the fetch resolved, or indefinitely if it failed. A US
   headline sitting above an Indian position list is not a slow update, it is
   the wrong country's information presented as this one's. */
const MARKET_DEPENDENT_PANELS = [
  'top-movers-body', 'open-extremes-body', 'setup-status-body',
  'portfolio-scan-body', 'monitor-watchlist-body', 'monitor-extended-body',
  'event-risk-body', 'signal-check-body', 'market-drivers-body',
  'breadth-body', 'news-body', 'top-headlines', 'market-pulse',
  'decision-brief',
];

/** Reopen the last completed scan for ``market`` (saved server-side), marked as
 *  saved, so a restart or a new tab does not start on an empty Picks page. It
 *  never replaces a scan the user has run or started since. */
async function restoreLastScan(market) {
  const m = market === 'IN' ? 'IN' : 'US';
  // Any scan started (or failed) while this request is in flight wins.
  const scanToken = _jobTokens.scan;
  let saved;
  try {
    const res = await fetch(`/api/stock-picks/last?market=${m}`, { cache: 'no-store' });
    if (!res.ok) return false;
    saved = await res.json();
  } catch (_) { return false; }
  const scanBtn = document.getElementById('btn-scan');
  const marketNow = ($('#sp-market')?.value || 'US') === 'IN' ? 'IN' : 'US';
  if (!saved || !saved.result || state.picks || marketNow !== m || (scanBtn && scanBtn.disabled)
      || !jobTokenCurrent('scan', scanToken)) return false;
  renderPicks(saved.result, { savedAt: saved.saved_at || 'unknown' });
  const when = saved.saved_at ? new Date(saved.saved_at) : null;
  const whenText = when && !Number.isNaN(when.getTime())
    ? when.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : 'an earlier session';
  setPicksMeta(`Saved scan · ${whenText}`);
  const results = document.getElementById('picks-results');
  if (results) {
    results.insertAdjacentHTML('afterbegin', `<p class="q-note picks-restored" role="status">Showing your last scan, from <b>${escapeHtml(whenText)}</b>. Prices and calls may have moved since: run a new scan before acting on it.</p>`);
  }
  return true;
}

/** Blank every country-specific panel and say which market is loading. */
function clearMarketDependentPanels(market) {
  // A scan still in flight belongs to the market that just went away: discard
  // its result and release the form it was holding.
  nextJobToken('scan');
  const scanBtn = document.getElementById('btn-scan');
  const scanWasRunning = !!(scanBtn && scanBtn.disabled);
  if (scanBtn) scanBtn.disabled = false;
  const scanProg = document.getElementById('sp-progress');
  if (scanProg) scanProg.classList.remove('visible');
  const scanResults = document.getElementById('picks-results');
  if (scanResults && (state.picks || scanWasRunning)) {
    scanResults.innerHTML = `<div class="empty-state large"><h3>Nothing scanned yet</h3><p>The ${escapeHtml(market === 'IN' ? 'India' : 'United States')} market is selected. Run a scan to build its position list.</p></div>`;
  }
  state.picks = null;
  state.picksSavedAt = null;
  state.dashboard = null;
  state.shownSymbols = new Set();
  const label = market === 'IN' ? 'India' : 'United States';
  const notice = `<div class="q-empty q-empty--inline">Loading ${escapeHtml(label)} data…</div>`;
  MARKET_DEPENDENT_PANELS.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = notice;
  });
  setPicksMeta('No scan yet');
  restoreLastScan(market);
  // Meta lines make claims about the market that just went away.
  ['top-movers-meta', 'open-extremes-meta', 'vetoes-meta', 'event-risk-meta',
   'signal-check-meta', 'market-drivers-meta', 'breadth-meta'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.textContent = 'Loading…';
  });
}

/** One-line status for the Proposed Allocation header. */
function setPicksMeta(text) {
  const el = $('#picks-meta');
  if (el) el.textContent = text;
}

// The engine's filter counters, in words. An unknown counter reads with its
// underscores as spaces rather than as a raw key (audit U2).
const FILTER_WORDS = {
  user_sector: 'outside your sector filter',
  user_market_cap: 'market cap outside your filter',
  user_price: 'price outside your filter',
  user_liquidity: 'traded value under your filter',
  score_below_min: 'score below the minimum',
  score_below_min_post_penalty: 'score below the minimum after penalties',
  signal_not_actionable: 'Hold signal, no buy setup',
  signal_avoid: 'signal says avoid',
  risk_reward_low: 'reward-to-risk below the hurdle',
  low_volume: 'trading volume too low',
  market_cap_too_low: 'market cap too small',
  insufficient_data: 'not enough price history',
  data_validation_failed: 'data failed validation',
  price_outside_range: 'price outside the allowed range',
  provider_rate_limited: 'data provider rate-limited',
  earnings_calendar_missing: 'no earnings date available',
  committee_hold: 'investment committee says hold',
  risk_grade_size_below_minimum: 'risk-grade size under the minimum position',
  info_unavailable: 'no company information',
  tech_indicators_failed: 'indicators could not be computed',
  no_market_cap: 'no market cap from any data source',
  low_dollar_volume: 'daily dollar volume too low',
  failed_liquidity: 'position too large for daily volume',
  failed_var_limit: 'position risk above the per-trade limit',
  leveraged_inverse_excluded: 'leveraged or inverse fund',
  style_screen_failed: "did not pass the scan's style screen",
  crypto_score_low: 'crypto score below the minimum',
  provider_circuit_open: 'skipped after provider throttling',
  circuit_breaker_open: 'skipped after repeated provider errors',
  analysis_exception: 'analysis error',
  data_provider_error: 'data provider error',
  missing_price_data: 'no usable price history',
  timeout: 'analysis timed out',
  import_error: 'missing backend component',
  serialization_error: 'unexpected provider response',
};
function filterWords(key) {
  if (key === 'zero_allocation') {
    // [I5] The minimum the scan applied at its own capital, as the scan published it.
    const p = state && state.picks;
    const known = Number(p && p.min_position_effective) > 0 || Number(p && p.capital) > 0;
    const min = Number(p && p.min_position_effective) > 0 ? Number(p.min_position_effective) : guideMinTrade(Number(p && p.capital));
    return `sized to ${fmtMoney(0)} (under the ${fmtMoney(min)} minimum position${known ? ' at this capital' : ''}, or capped)`;
  }
  return FILTER_WORDS[key] || String(key).replace(/_/g, ' ');
}

/* [A13] Scan stages under the progress bar: finished, current (with counts), pending.
   Labels come from the job's stage record; the order mirrors the worker's. */
// Phone layout breakpoint (matches quanta.css's phone media queries).
const PHONE_MAX_WIDTH = 640;
const SCAN_STAGE_ORDER = [
  ['selecting', 'Choosing the universe'], ['analysing', 'Analysing names'],
  ['book', 'Applying the book rules'], ['research_check', 'Running the Research check'],
];
function renderScanStages(stage, log) {
  const list = $('#sp-stages');
  if (!list) return;
  if (!stage || !stage.key) { list.innerHTML = ''; return; }
  // [Picks revamp] Before analysis starts: source size, kept size, method, fallback.
  const selLine = (sel) => {
    const t = selectionText(sel);
    return t ? `${t.charAt(0).toUpperCase()}${t.slice(1)}` : '';
  };
  const selOf = (s) => (s && s.selection) || (log && log.selecting && log.selecting.selection) || null;
  // [I4][P1] A terminal record (complete, failed, cancelled) lists every stage as
  // done, failed or skipped, with its final count or reason; none is current.
  if (stage.terminal && Array.isArray(stage.stages)) {
    list.innerHTML = stage.stages.map((s) => {
      const counts = Number(s.total) > 0 ? `${Number(s.done)} of ${Number(s.total)}` : '';
      const detail = s.note || (s.key === 'selecting' && selLine(selOf(s))) || counts;
      return `<li data-state="${escapeHtml(s.state || 'done')}">${escapeHtml(`${s.label || s.key}${detail ? `: ${detail}` : ''}`)}</li>`;
    }).join('');
    return;
  }
  const at = SCAN_STAGE_ORDER.findIndex(([k]) => k === stage.key);
  list.innerHTML = SCAN_STAGE_ORDER.map(([k, fallback], i) => {
    const state = at < 0 ? 'pending' : (i < at ? 'done' : (i === at ? 'current' : 'pending'));
    const label = i === at ? (stage.label || fallback) : fallback;
    const sel = k === 'selecting' && i <= at ? selLine(selOf(stage)) : '';
    const clock = i === at && Number(stage.elapsed_s) > 0 ? ` · ${Math.round(Number(stage.elapsed_s))} s` : '';
    const counts = i === at && !sel && Number(stage.total) > 0 ? `: ${Number(stage.done)} of ${Number(stage.total)}${clock}` : (i === at && !sel ? clock : '');
    const detail = sel ? `: <span class="sst-d">${escapeHtml(sel)}</span>` : '';
    return `<li data-state="${state}"${state === 'current' ? ' aria-current="step"' : ''}>${escapeHtml(label + counts)}${detail}</li>`;
  }).join('');
}

/* [A13] The three clocks of a scan, kept apart. */
function fmtStamp(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function dataDatesLineHtml(data) {
  const dd = data && data.data_dates;
  if (!dd || !(dd.bar_session || dd.scan_completed)) return '';
  const parts = [];
  if (dd.bar_session) {
    const counts = dd.bar_sessions || {};
    const latest = Number(counts[dd.bar_session] || 0);
    const older = Object.entries(counts).filter(([k]) => k !== dd.bar_session).reduce((t, [, n]) => t + Number(n || 0), 0);
    parts.push(`Daily bars through ${dd.bar_session}${latest ? ` (${latest} name${latest === 1 ? '' : 's'}${older ? `; ${older} on an earlier session` : ''})` : ''}`);
  }
  parts.push(dd.quote_time && fmtStamp(dd.quote_time) ? `provider quote ${fmtStamp(dd.quote_time)}` : 'no provider quote time');
  if (dd.scan_completed && fmtStamp(dd.scan_completed)) parts.push(`scan completed ${fmtStamp(dd.scan_completed)}`);
  const line = parts.join(' · ');
  return `<p class="qr-caveat muted data-dates" data-dates>${escapeHtml(line.charAt(0).toUpperCase() + line.slice(1))}.</p>`;
}

/* [A12] A scan the user started has finished: show it, fold the setup on a
   phone, and move focus to the result heading. Re-renders (restored state, a
   market switch) go through renderPicks alone and never move focus. */
function onUserScanComplete(data, isPhone) {
  renderPicks(data);
  collapseSetupAfterRun(!!isPhone);
  const heading = document.querySelector('#picks-results [data-result-heading]');
  if (heading && typeof heading.focus === 'function') heading.focus({ preventScroll: false });
}

/* [A13] After a run the setup panel folds to a one-line summary of what was
   scanned, so the report takes the full width; a button reopens it. */
function setupSummaryText() {
  const val = (sel) => { const el = document.querySelector(sel); return el ? el.value : ''; };
  const scan = val('#sp-scan');
  const capital = Number(val('#sp-capital'));
  const div = document.querySelector('#sp-diversify');
  return [scan ? scanTypeName(scan) : '',
    Number.isFinite(capital) && capital > 0 ? fmtMoney(capital, val('#sp-market') === 'IN' ? '\u20b9' : '$', 0) : '',
    val('#sp-risk') ? `${titleLabel(val('#sp-risk'))} mandate` : '',
    val('#sp-positions') ? `up to ${val('#sp-positions')} positions` : '',
    div ? (div.checked ? 'sector caps on' : 'sector caps off') : '',
    (() => { const f = pickScanFilters(); const n = f.error ? 0 : Object.keys(f).length; return n ? `${n} filter${n === 1 ? '' : 's'}` : ''; })()]
    .filter(Boolean).join(', ');
}

// The setup stays open beside the report wherever the two sit side by side;
// only the stacked layout (narrow screens), where the form would push the
// report a screen down, folds it to a one-line summary after a run.
const SETUP_STACKED_MQ = '(max-width: 1080px)';
function setupIsStacked() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(SETUP_STACKED_MQ).matches;
}
function collapseSetupAfterRun() {
  const card = document.querySelector('.form-card');
  const toggle = $('#sp-setup-toggle');
  if (!card || !toggle) return;
  const summary = card.querySelector('#sp-setup-summary');
  const set = (collapsed) => {
    card.classList.toggle('is-collapsed', collapsed);
    toggle.hidden = !setupIsStacked();
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    toggle.textContent = collapsed ? 'Edit scan setup' : 'Hide scan setup';
    if (summary) {
      summary.textContent = setupSummaryText();
      summary.hidden = !collapsed;
    }
  };
  if (!toggle.dataset.bound) {
    toggle.dataset.bound = '1';
    toggle.addEventListener('click', () => set(!card.classList.contains('is-collapsed')));
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      const mq = window.matchMedia(SETUP_STACKED_MQ);
      const follow = () => { if (!mq.matches) set(false); else toggle.hidden = false; };
      if (typeof mq.addEventListener === 'function') mq.addEventListener('change', follow);
    }
  }
  set(setupIsStacked());
}

/* [Picks revamp] The screen a style scan runs on the names it kept. */
const STYLE_SCREEN_OF = {
  focused_growth: 'growth', focused_value: 'value', focused_momentum: 'uptrend', focused_dividend: 'dividend',
  india_growth: 'growth', india_value: 'value', india_momentum: 'uptrend', india_dividend: 'dividend',
};
/* [A01][Picks revamp] One sentence on how the universe was chosen, every part
   read from result.universe_selection (or the selecting stage's copy): source
   size, kept size, ranking rule, names that passed the floors but were not
   analysed, the style screen that follows, and any fallback. Starts lowercase. */
function selectionText(sel) {
  if (!sel || !sel.method) return '';
  const n = Number(sel.selected || 0);
  const of = Number(sel.candidates || 0);
  const secs = Number(sel.seconds);
  const took = Number.isFinite(secs) ? `${secs.toFixed(1)} s` : '';
  const names = (k) => `${k} name${k === 1 ? '' : 's'}`;
  // [I8] What "of N" counts, and what the seconds measured, as the engine recorded them.
  const download = sel.seconds_measure === 'price_download';
  const pool = sel.pool_label
    ? `${of} ${sel.pool_label}${Number(sel.universe_total) > of ? ` (${Number(sel.universe_total)} names in the full universe)` : ''}`
    : names(of);
  const pf = sel.passed_floors == null ? null : Number(sel.passed_floors);
  const screenFirst = STYLE_SCREEN_OF[sel.scan_type] && sel.screened_before_cap ? STYLE_SCREEN_OF[sel.scan_type] : '';
  const ps = Number(sel.passed_screen);
  const floors = !Number.isFinite(pf) ? ''
    : screenFirst && Number.isFinite(ps)
      ? `; ${pf} passed the price and volume floors and ${ps} the ${screenFirst} screen${ps > n ? `, ${ps - n} of them not analysed` : ''}`
      : `; ${pf} passed the price and volume floors${pf > n ? `, ${pf - n} of them not analysed` : ''}`;
  const sampled = Number(sel.prefilter_sampled) > 0
    ? `; the liquidity download read an even spread of ${Number(sel.prefilter_sampled)} of them` : '';
  let line;
  switch (sel.method) {
    case 'liquidity_ranked': line = `top ${n} of ${pool}, ranked by ${sel.rank_by || 'average daily traded value'}${floors}${sampled}`
      + (took ? (download ? `; price download ${took}` : `; ranked in ${took}`) : ''); break;
    case 'index_list': line = `all ${n} ${sel.pool_label || 'index names'}`; break;
    case 'profile_ranked': line = download
      ? `${n} of ${pool}, ranked on the scan's price and volume profile${took ? `; price download ${took}` : ''}`
      : `${n} of ${names(of)} ranked on the scan's price and volume profile${took ? ` in ${took}` : ''}`; break;
    case 'even_spread': line = `fallback: an even spread of ${n} of ${names(of)}, not liquidity-ranked${sel.cause ? `: ${sel.cause}` : ''}${took ? ` (stopped after ${took})` : ''}`; break;
    case 'custom_list': line = `your list of ${names(n)}`; break;
    case 'sub_industry': line = `all ${n} ${sel.pool_label || 'index names in the scan\'s sub-industries'}`; break;
    case 'etf_directory': line = `all ${n} ${sel.pool_label || 'US-listed ETFs'} in the exchange symbol directory`; break;
    case 'none': line = `no names${sel.cause ? `: ${sel.cause}` : ''}`; break;
    default: line = n >= of ? `all ${names(n)}, index names first, then the rest of the list`
      : `${n} of ${names(of)}, index names first, then the rest of the list`;
  }
  const screen = STYLE_SCREEN_OF[sel.scan_type];
  if (screen && n > 0 && !screenFirst) line += `; the ${screen} screen then runs on these ${n}`;
  // [Scan types] Which copy of the index list the scan read.
  if (sel.index_source === 'snapshot') {
    line += ` (index list from the ${sel.index_as_of || 'saved'} snapshot${sel.index_cause ? `: ${sel.index_cause}` : ''})`;
  } else if (sel.index_source === 'none') {
    line += ` (index list unavailable${sel.index_cause ? `: ${sel.index_cause}` : ''})`;
  } else if (sel.index_source === 'live' && sel.index_as_of) {
    line += ` (index list read ${sel.index_as_of})`;
  }
  return line;
}
function coverageLineHtml(data) {
  const line = selectionText(data && data.universe_selection);
  if (!line) return '';
  return `<p class="qr-caveat muted coverage-line" data-coverage>Universe: ${escapeHtml(line)}.${escapeHtml(eventCoverageText(data))}</p>`;
}

/* [I8] The corporate-event check's coverage, from result.event_check. */
function eventCoverageText(data) {
  const ev = data && data.event_check;
  if (!ev || !Number(ev.checked)) return '';
  const n = Number(ev.checked);
  const bad = Number(ev.unavailable || 0);
  if (bad) {
    const why = (ev.reasons || []).map((r) => r.label || r.reason).filter(Boolean).join('; ');
    return ` Corporate-event check: unavailable for ${bad} of ${n} names${why ? ` (${why})` : ''}.`;
  }
  const flagged = Number(ev.flagged || 0);
  return ` Corporate-event check: ${n} of ${n} names checked${flagged ? `, ${flagged} flagged` : ''}.`;
}

/* [A04] Compact verdict for a scan that funded nothing: the counts the stage
   table reconciles, the capital and the parameters the scan ran with. */
function scanVerdictHtml(data) {
  const a = data && data.accounting;
  if (!a || !a.version) return '';
  const sym = data.currency || '$';
  const when = data.timestamp && !Number.isNaN(Date.parse(data.timestamp))
    ? new Date(data.timestamp).toLocaleString() : '';
  const cells = [
    ['Buy or Accumulate', Number(a.funded || 0)],
    ['Watchlist', Number(a.watch || 0)],
    ['Could not be analysed', Number((a.could_not_analyse || {}).count || 0)],
    ['Scanned', Number(a.universe || 0)],
    ['Capital', fmtMoney(Number(data.capital || 0), sym, 0)],
  ];
  const params = [data.scan_type ? `${scanTypeName(data.scan_type)} scan` : '',
    data.max_positions ? `up to ${data.max_positions} positions` : '',
    (data.risk_profile || {}).profile ? `${titleLabel(data.risk_profile.profile)} mandate` : '',
    when && !data.data_dates ? `run ${when}` : ''].filter(Boolean).join(' · ');
  return `<div class="scan-verdict" data-scan-verdict>
      <dl class="scan-verdict-row">${cells.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('')}</dl>
      ${params ? `<p class="muted scan-verdict-params">${escapeHtml(params)}</p>` : ''}
    </div>`;
}

function emptyScanExplanationHtml(data) {
  const explanation = String(data?.empty_scan_explanation || '')
    .split('\n')
    .map((line) => line.replace(/^Filter counts: (.*)$/, (_, list) => `Why names were screened out: ${list.split(/;\s*/)
      .map((pair) => { const [k, n] = pair.split('='); return n === undefined ? pair : `${filterWords(k.trim())} ${n.trim()}`; })
      .join(' · ')}`))
    .join('\n');
  return explanation
    ? `<p class="muted" style="white-space:pre-line;">${escapeHtml(explanation)}</p>`
    : '';
}

/* ===========================================================================
   [Picks redesign] One ranked Stock Picks table (result.stock_picks): every
   name that reached the book, ranked by score, each with its one Decision and
   either its position or the one reason it is not funded. Everything else
   (gates, event checks, alerts, checklists) sits in one collapsed section.
   =========================================================================== */
const PK_TONE = {
  strong_buy: 'bull', buy: 'bull', accumulate: 'bull', watchlist: 'warn', hold: 'neutral',
  hold_existing: 'neutral', avoid: 'bear', reduce: 'bear', sell: 'bear',
};
const pkNum = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));


/* [Fix 1] A signed percent: "+7.7%", "-0.3%", never "+-0.3%". */
function pkSignedPct(v, digits = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '-';
  return `${n > 0 ? '+' : n < 0 ? '-' : ''}${Math.abs(n).toFixed(digits)}%`;
}

function pkSignalHtml(signal) {
  const tone = PK_TONE[signalKey(signal)] || 'neutral';
  return `<span class="pk-signal is-${tone}">${escapeHtml(signal ? titleLabel(signal) : '—')}</span>`;
}

/* The one reason a row is not funded, in words. */
function pkReason(r, sym) {
  if (r.status === 'below_rule' && r.gate) return gateShortfallText(r.gate, sym) || 'Below a book rule';
  return r.reason || '';
}

function pkEntryText(r, sym) {
  const lo = pkNum(r.entry_low);
  const hi = pkNum(r.entry_high);
  if (lo && hi && Math.abs(hi - lo) > 0.004) return `${fmtPrice(lo, sym)}-\u0000${fmtPrice(hi, sym)}`;
  return pkNum(r.entry) ? fmtPrice(r.entry, sym) : '-';
}


/* The scan type as the Scan Setup menu names it. */
function scanTypeName(value) {
  try {
    const opt = typeof document !== 'undefined' && document.querySelector(`#sp-scan option[value="${String(value).replace(/"/g, '')}"]`);
    if (opt && opt.textContent) return opt.textContent.split(/: | — /)[0].replace(/…$/, '').trim();
  } catch (_) { /* fall through */ }
  return titleLabel(String(value || '').replace(/^focused_/, ''));
}

function pkShortDate(iso) {
  const t = Date.parse(iso || '');
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
}



/* [Why picked] How a stock becomes a QUANTA pick, from the rules this scan applied. */
function pkMethodHtml(data) {
  const money = (k) => fmtMoney(Number(thr(k)) || 0, '$', 0);
  const floors = thr('UNIVERSE.SCAN_MIN_MARKET_CAP') || {};
  const cap = Number(floors[data.scan_type] || thr('UNIVERSE.MIN_MARKET_CAP')) || 0;
  const big = (v) => (v >= 1e9 ? `$${(v / 1e9).toFixed(v % 1e9 ? 1 : 0)}B` : `$${Math.round(v / 1e6)}M`);
  const style = { focused_growth: 'growth', focused_value: 'value', focused_dividend: 'dividend', focused_momentum: 'momentum',
    india_growth: 'growth', india_value: 'value', india_dividend: 'dividend', india_momentum: 'momentum' }[data.scan_type];
  const pct1 = (k) => `${(Number(thr(k)) * 100).toFixed(1).replace(/\.0$/, '')}%`;
  const pct = (k) => `${Math.round(Number(thr(k)) * 100)}%`;
  const styleRule = {
    growth: `revenue growth of at least ${pct('STYLE.GROWTH_MIN_REVENUE_GROWTH')}, plus earnings growth of at least ${pct('STYLE.GROWTH_MIN_EARNINGS_GROWTH')} or a forward EPS above trailing`,
    value: `positive earnings (and positive free cash flow where it is reported), and cheap on at least ${thr('STYLE.VALUE_MIN_PASSES')} of: forward P/E at most ${thr('STYLE.VALUE_MAX_FORWARD_PE')}, EV/EBITDA at most ${thr('STYLE.VALUE_MAX_EV_EBITDA')}, free-cash-flow yield at least ${pct('STYLE.VALUE_MIN_FCF_YIELD')}, price/book at most ${thr('STYLE.VALUE_MAX_PB')}`,
    dividend: pkIsIndia(data)
      ? `a dividend yield of at least ${pct1('STYLE.DIVIDEND_MIN_YIELD_NO_LIST')}, payout at most ${pct('STYLE.DIVIDEND_MAX_PAYOUT')} of earnings (REITs exempt)`
      : `on the S&P 500 Dividend Aristocrats list (25+ years of increases), paying a dividend now, payout at most ${pct('STYLE.DIVIDEND_MAX_PAYOUT')} of earnings (REITs exempt)`,
    momentum: `price above its 50-day average, which is above the 200-day, and a 3-month return of at least ${pct('STYLE.MOMENTUM_MIN_RETURN_3M')}`,
  }[style];
  const etf = data.scan_type === 'focused_etf';
  // [India] The rules an India scan applies: rupee price band, volume and value, crore market cap.
  const india = pkIsIndia(data);
  const inr = (k) => `\u20b9${(Number(thr(k)) || 0).toLocaleString('en-IN')}`;
  const crore = (v) => `\u20b9${(v / 1e7).toLocaleString('en-IN')} crore`;
  const uncovered = Object.values(thr('EVENTS.UNCOVERED_LISTINGS') || {}).join(' and ');
  // How the names were chosen is the Universe line under this list (coverageLineHtml), once.
  // A scan with its own US floors (Small-Cap) states those, from config.
  const usFloor = (k) => Number(((thr('UNIVERSE.SCAN_FLOORS') || {})[data.scan_type] || {})[k] ?? thr(`UNIVERSE.${k}`)) || 0;
  const steps = [
    india
      ? ['Tradable', `price ${inr('UNIVERSE.INDIA_MIN_PRICE')} to ${inr('UNIVERSE.INDIA_MAX_PRICE')}, at least ${Number(thr('UNIVERSE.INDIA_MIN_AVG_VOLUME')).toLocaleString('en-IN')} shares and ${crore(Number(thr('UNIVERSE.INDIA_MIN_DAILY_VALUE')))} traded a day, market cap at least ${crore(Number((thr('UNIVERSE.INDIA_SCAN_MIN_MARKET_CAP') || {})[data.scan_type] || thr('UNIVERSE.INDIA_MIN_MARKET_CAP')))}, 200+ days of history.`]
      : ['Tradable', `price ${fmtMoney(usFloor('MIN_PRICE'), '$', 0)} to ${money('UNIVERSE.MAX_PRICE')}, at least ${usFloor('MIN_AVG_VOLUME').toLocaleString('en-US')} shares and ${big(usFloor('MIN_DAILY_DOLLAR_VOLUME'))} traded a day${etf ? '' : `, market cap at least ${big(cap)}`}, 200+ days of history.`],
    styleRule ? ['Style screen', `${styleRule}.`] : null,
    pkFiltersText(data) ? ['Your filters', `${pkFiltersText(data)}.`] : null,
    ['Scored 0 to 10', etf
      ? 'a fund is scored on trend and momentum only, weighted for the market regime.'
      : `technicals, fundamentals, momentum, quality and value, weighted for the market regime. Analyst consensus, ${india ? 'news catalysts' : 'SEC and news catalysts, insider trades'} and estimate revisions can move the score by at most ±${thr('SCORE.ADJUSTMENT_CAP')}.`],
    ['Signal', `Accumulate from ${thr('SCORE.ACCUMULATE')}, Buy from ${thr('SCORE.GOOD')}; the bars rise in weaker markets.`],
    ['Committee', `at least ${thr('COMMITTEE.UPSIDE_HURDLE_PCT')}% upside to target and reward/risk of ${Number(thr('RR.SCANNER_MIN')).toFixed(1)}:1 after trading costs; risk grade ${(thr('BOOK.BANNED_GRADES') || []).join(', ')} excluded.`],
    ['Corporate events', india
      ? `no corporate-event source covers ${uncovered || 'these'} listings yet, so a name can reach Watchlist at most; no Buy or Accumulate call is made.`
      : 'SEC filings are checked for a pending acquisition or delisting. If the check cannot be read, the name stays on the Watchlist.'],
    ['Research check', 'each pick is re-analysed by the full Research engine, which can only lower the call.'],
    ['Sizing', 'each position risks a set share of capital to its stop, capped per position and per sector, then scaled by your mandate.'],
  ].filter(Boolean);
  return `<section class="pk-method" data-pk-method>
      <h4>What makes a QUANTA pick</h4>
      <ol>${steps.map(([k, v]) => `<li><b>${escapeHtml(k)}:</b> ${escapeHtml(v)}</li>`).join('')}</ol>
    </section>`;
}



/* A partial or throttled scan, as one line (with the safe-mode retry when throttled). */
function pkStatusLineHtml(data) {
  const msg = scanStatusMessage(data);
  if (!msg) return '';
  const retry = msg.rateLimited ? ' <button type="button" class="btn-mini" data-safe-mode-retry>Retry with safe mode</button>' : '';
  return `<p class="pk-alert" role="status" data-pk-status><strong>${escapeHtml(msg.title)}.</strong> ${escapeHtml(msg.body || '')}${retry}</p>`;
}

/* One line above the table only when something blocks funding: a missing or
   malformed SEC contact, or names in the book the event check could not read. */
function pkAlertHtml(data) {
  const ev = data.event_check || {};
  const contact = ev.contact || {};
  const names = (ev.reasons || []).flatMap((r) => r.book_tickers || []);
  if (!names.length && contact.state !== 'missing' && contact.state !== 'malformed') return '';
  const shown = Math.max(1, Number(thr('UI.NAME_LIST_SHOWN')) || 5);
  const list = ` (${names.slice(0, shown).join(', ')}${names.length > shown ? `, +${names.length - shown} more` : ''})`;
  const text = names.length
    ? `Corporate-event check could not read ${names.length} name${names.length === 1 ? '' : 's'} in the book${list}; they stay on the Watchlist. Cause and fix under How this scan decided.`
    : `Corporate-event check unavailable: ${contact.label || 'no SEC contact configured'}. No US name can get a Buy call until it is fixed.`;
  return `<p class="pk-alert" role="status" data-pk-alert>${escapeHtml(text)}</p>`;
}

function pkEmptyHtml(data, rows) {
  const acc = data.accounting || {};
  const reasons = Object.entries((acc.rule_rejected || {}).by_reason || {})
    .filter(([, n]) => Number(n) > 0).sort((x, y) => Number(y[1]) - Number(x[1])).slice(0, 3)
    .map(([k, n]) => `${filterWords(k)} (${Number(n)})`);
  const wl = (rows || []).filter((r) => pkGroupOf(r) === 'watchlist').length;
  // Names on an exchange no corporate-event source covers can reach Watchlist at most.
  const uncovered = ((data.event_check || {}).reasons || []).find((x) => x.reason === 'not_covered' && Number(x.count) > 0);
  const exchanges = Object.values(thr('EVENTS.UNCOVERED_LISTINGS') || {});
  const reached = Number(acc.reached_book);
  return `<div class="pk-empty" data-pk-empty>
      <p class="pk-empty-h"><b>No Buy or Accumulate picks right now.</b>${wl ? ` ${wl} name${wl === 1 ? ' is' : 's are'} on the Watchlist.` : ''}</p>
      ${reasons.length ? `<p>${reached === 0 ? 'No name passed the per-name rules. ' : ''}Top reasons: ${escapeHtml(reasons.join(', '))}.</p>` : ''}
      ${uncovered ? `<p data-pk-uncovered>${escapeHtml(`${exchanges.length ? exchanges.join(' and ') : 'These'} listings get no Buy or Accumulate call: no corporate-event source covers them yet, so the best call a name can reach is Watchlist.`)}</p>` : ''}
    </div>`;
}




/* CSV of the table, as shown (one row per stock). */
/* Tickers to copy: the Buy and Accumulate calls, or the Watchlist. */
function pkCopyText(rows, which) {
  return (rows || []).filter((r) => (which === 'watchlist' ? pkGroupOf(r) === 'watchlist' : ['buy', 'accumulate'].includes(pkGroupOf(r))))
    .map((r) => r.ticker).join(',');
}

/* [Method] Names that are not picks: excluded for safety, then screened out. */
function pkExcludedHtml(data) {
  const ex = data.excluded_for_safety || [];
  if (!ex.length) return '';
  return pkPanel('Excluded for safety', `<ul class="pk-exlist">${ex.map((e) => `<li><b>${escapeHtml(e.ticker)}</b> <span class="pk-co-inline">${escapeHtml(e.company || '')}</span><span class="pk-ex-why">${escapeHtml(e.reason || '')}</span><span class="pk-wp-meta">${escapeHtml([e.source, e.filed ? `filed ${e.filed}` : ''].filter(Boolean).join(', '))}${safeUrl(e.url) ? ` <a href="${escapeHtml(safeUrl(e.url))}" target="_blank" rel="noopener noreferrer">filing</a>` : ''}</span></li>`).join('')}</ul>`,
    { meta: `${ex.length} name${ex.length === 1 ? '' : 's'}`, attrs: ' data-pk-excluded' });
}

function pkScreenedHtml(data) {
  // Names that reached the book and missed one of its rules. The per-name rules'
  // counts are in "Where the names went" above, so each count shows once.
  const book = data.screened_out || [];
  if (!book.length) return '';
  const sym = data.currency || '$';
  const rows = book.map((b) => `<li><span><b>${escapeHtml(b.ticker)}</b> <span class="pk-co-inline">${escapeHtml(b.company || '')}</span></span><span>${escapeHtml(b.gate ? gateShortfallText(b.gate, sym) || 'Missed a book rule' : b.reason || 'Missed a book rule')}</span></li>`);
  return pkPanel('Screened out at the book rules', `<ul class="pk-sclist">${rows.join('')}</ul>`,
    { meta: `${book.length} name${book.length === 1 ? '' : 's'}`, attrs: ' data-pk-screened' });
}

function stockPicksCsv(rows) {
  const cols = ['rank', 'ticker', 'company', 'call', 'price', 'entry', 'target', 'upside_pct', 'stop', 'risk_reward',
    'score', 'risk_grade', 'your_capital', 'suggested_dollars', 'suggested_shares', 'reason', 'why_picked'];
  const cell = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const value = (r, c) => {
    if (c === 'call') return pkDecision(r).label;
    if (c === 'your_capital') return pkCapital(r, '$').text;
    if (c === 'suggested_dollars') return pkIsSized(r) ? Number(r.allocation) : '';
    if (c === 'suggested_shares') return pkIsSized(r) ? Number(r.shares) : '';
    if (c === 'reason') return pkGroupOf(r) === 'watchlist' ? pkReason(r, '$') : '';
    if (c === 'why_picked') return (r.why_picked || {}).summary;
    return r[c];
  };
  const body = (rows || []).map((r) => cols.map((c) => cell(value(r, c))).join(','));
  return `${cols.join(',')}\n${body.join('\n')}\n`;
}




function pkStars(conviction) {
  const c = Math.max(0, Math.min(5, Math.round(Number(conviction) || 0)));
  return { text: '★'.repeat(c) + '☆'.repeat(5 - c), n: c };
}

/* The risk gauge: volatility, beta and the risk grade (never conviction). */
function pkGauge(r) {
  const vol = Number(r.volatility) || 0;
  const beta = pkNum(r.beta);
  const grade = String(r.risk_grade || '').toUpperCase();
  let pts = 0;
  if (vol >= Number(thr('WHY.GAUGE_VOL_HIGH'))) pts += 2;
  else if (vol >= Number(thr('WHY.GAUGE_VOL_ELEVATED'))) pts += 1;
  if (beta !== null && Math.abs(beta - 1) >= Number(thr('WHY.GAUGE_BETA_GAP'))) pts += 1;
  if (grade === 'D' || grade === 'F') pts += 2;
  else if (grade === 'C') pts += 1;
  const filled = Math.max(1, Math.min(pts + 1, 5));
  const level = pts <= 1 ? 'Low' : pts <= 3 ? 'Moderate' : 'High';
  const parts = [grade ? `Grade ${grade}` : '', vol ? `vol ${(vol * 100).toFixed(0)}%` : '',
    beta !== null ? `beta ${beta.toFixed(2)}` : ''].filter(Boolean);
  return { filled, level, tone: pts <= 1 ? 'neutral' : 'warn', detail: parts.join(' · ') };
}

function pkGaugeHtml(g) {
  const dots = Array.from({ length: 5 }, (_, k) => `<i class="${k < g.filled ? 'on' : ''}"></i>`).join('');
  return `<span class="pk-gauge is-${g.tone}" role="img" aria-label="Risk ${g.level}, ${g.filled} of 5">${dots}</span><b class="pk-gauge-label is-${g.tone}">${g.level}</b>`;
}



/* How to act on a pick: entry, stop, exits and hold for a funded name; no entry otherwise. */
function pkExecutionHtml(r, sym) {
  const rows = [];
  if (['buy', 'accumulate'].includes(pkGroupOf(r)) && r.status !== 'avoid') {
    const entry = pkNum(r.entry) || pkNum(r.price);
    const atr = pkNum(r.atr);
    const add = entry && atr ? entry - atr : null;
    const zone = pkEntryText(r, sym).replace('\u0000', '');
    const sig = signalKey(r.signal);
    rows.push(['Entry', sig === 'accumulate'
      ? `Scale in: a third near ${fmtPrice(entry, sym)}${add ? `, add a third at ${fmtPrice(add, sym)} on confirmation` : ''} (buy zone ${zone}).`
      : `Limit ${fmtPrice(entry, sym)} for half${add ? `, add the rest at ${fmtPrice(add, sym)}` : ''} (buy zone ${zone}).`]);
    rows.push(['Stop', 'Exit in full on a close below the stop.']);
    const target = pkNum(r.target);
    if (entry && target) {
      const t1 = entry + (target - entry) / 2;
      rows.push(['Exits', `Take a third at ${fmtPrice(t1, sym)} (${pkSignedPct((t1 / entry - 1) * 100)}), a third at the target, then trail ${thr('LEVELS.TRAILING_ATR_MULTIPLIER')}× ATR${pkNum(r.trailing_stop) ? ` (now ${fmtPrice(r.trailing_stop, sym)})` : ''}.`]);
    }
    if (pkNum(r.horizon_days)) rows.push(['Hold', `About ${Number(r.horizon_days)} trading sessions.`]);
  } else {
    rows.push(['Entry', r.status === 'avoid' ? 'No new entry: the name is on the avoid list.'
      : 'No entry for now; the reason leads the caution list.']);
  }
  return `<dl class="pk-card-dl">${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl>`;
}

function pkPositionText(r, data, sym) {
  if (!pkIsSized(r)) return pkCapital(r, sym).text;
  const capital = Number(data.capital || 0);
  const shares = Number(r.shares || 0);
  const entry = pkNum(r.entry) || pkNum(r.price) || 0;
  const atStop = shares * Math.max(0, entry - (pkNum(r.stop) || entry));
  const pct = (v) => (capital > 0 ? ` (${(v / capital * 100).toFixed(capital && v / capital < 0.01 ? 2 : 1)}% of capital)` : '');
  return `${shares} share${shares === 1 ? '' : 's'} · ${fmtMoney(r.allocation, sym, 0)}${pct(Number(r.allocation || 0))} · risk at stop ${fmtMoney(atStop, sym, 0)}${pct(atStop)}`;
}




/* ── [Analysis] Charts, gauges and checklists, only from measured data ───── */

/* A simple moving average aligned to its series (null until n closes exist). */
function pkSma(closes, n) {
  const out = new Array(closes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < closes.length; i += 1) {
    sum += closes[i];
    if (i >= n) sum -= closes[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}









/* Two or three picks side by side, from the same rows the table shows. */
function pkCompareHtml(rows, tickers, sym) {
  const picks = tickers.map((t) => rows.find((r) => r.ticker === t)).filter(Boolean);
  if (picks.length < 2) return '';
  const tech = (r, k) => pkNum((r.tech || {})[k]);
  const dec = Number(thr('UI.RR_DECIMALS')) || 2;
  const metrics = [
    ['Call', (r) => pkDecision(r).label],
    ['Price', (r) => (pkNum(r.price) ? fmtPrice(r.price, sym) : '-')],
    ['Score', (r) => (pkNum(r.score) !== null ? fmtScore100(r.score) : '-')],
    ...['technical', 'fundamental', 'momentum', 'quality', 'value'].map((k) => [titleLabel(k), (r) => { const v = pkNum((r.scores || {})[k]); return v ? fmtScore100(v) : '-'; }]),
    ['Upside to target', (r) => (r.status !== 'avoid' && pkNum(r.upside_pct) !== null ? pkSignedPct(r.upside_pct) : '-')],
    ['R:R', (r) => (r.status !== 'avoid' && pkNum(r.risk_reward) ? Number(r.risk_reward).toFixed(dec) : '-')],
    ['RSI (14)', (r) => (tech(r, 'rsi') !== null ? tech(r, 'rsi').toFixed(1) : '-')],
    ['1-month return', (r) => (tech(r, 'momentum_1m') !== null ? pkSignedPct(tech(r, 'momentum_1m') * 100) : '-')],
    ['3-month return', (r) => (tech(r, 'momentum_3m') !== null ? pkSignedPct(tech(r, 'momentum_3m') * 100) : '-')],
    ['Risk grade', (r) => r.risk_grade || '-'],
    ['Volatility', (r) => (pkNum(r.volatility) ? `${(r.volatility * 100).toFixed(0)}%` : '-')],
    ['Next earnings', (r) => (pkNum(r.days_to_earnings) !== null ? `${r.days_to_earnings} days` : '-')],
  ];
  return `<div class="pk-cmp-wrap"><table class="pk-cmp-table">
      <caption class="sr-only">Comparison of ${escapeHtml(picks.map((r) => r.ticker).join(', '))}</caption>
      <thead><tr><th scope="col">Measure</th>${picks.map((r) => `<th scope="col">${escapeHtml(r.ticker)}</th>`).join('')}</tr></thead>
      <tbody>${metrics.map(([k, f]) => `<tr><th scope="row">${escapeHtml(k)}</th>${picks.map((r) => `<td>${escapeHtml(String(f(r)))}</td>`).join('')}</tr>`).join('')}</tbody>
    </table></div>`;
}


/* ── [Terminal panels] Status words: Funded, Watch, Set aside ───────────── */
const PK_GROUPS = [['buy', 'Buy'], ['accumulate', 'Accumulate'], ['watchlist', 'Watchlist']];
/* QUANTA's call: the server's `call`, or the same mapping from the engine's
   final action for an older payload (STRONG_BUY folds into Buy). */
const pkGroupOf = (r) => {
  if (r.call) return r.call;
  if (r.status === 'avoid' || r.status === 'below_rule') return '';
  if (r.status === 'watch') return 'watchlist';
  const k = signalKey(r.signal);
  return k === 'strong_buy' || k === 'buy' ? 'buy' : k === 'accumulate' || k === 'watchlist' ? k : '';
};
const pkIsSized = (r) => (r.capital ? r.capital.kind === 'sized' : r.status === 'funded') && Number(r.allocation || 0) > 0;
function pkCardRows(rows) {
  return PK_GROUPS.flatMap(([k]) => rows.filter((r) => pkGroupOf(r) === k));
}
function pkDecision(r) {
  const g = pkGroupOf(r);
  const label = (PK_GROUPS.find(([k]) => k === g) || [g, titleLabel(g)])[1];
  return { label, tone: g === 'buy' ? 'buy' : g === 'accumulate' ? 'acc' : 'watch', call: '' };
}

/* [Calls] Capital fit, apart from the call: suggested size, or what one share needs. */
function pkCapital(r, sym) {
  const c = r.capital || (r.status === 'funded' ? { kind: 'sized' } : { kind: 'none', text: r.reason || '' });
  if (c.kind === 'sized' || (pkIsSized(r) && !r.capital)) {
    const n = Number(r.shares || 0);
    const less = c.reduced_for_holding ? ', less what you already hold' : '';
    return { sized: true, html: `<strong>${escapeHtml(fmtMoney(r.allocation, sym, 0))}</strong><span class="pk-sub">${n} share${n === 1 ? '' : 's'}${less}</span>`,
      text: `${fmtMoney(r.allocation, sym, 0)}, ${n} share${n === 1 ? '' : 's'}${less}` };
  }
  return { sized: false, html: `<span class="pk-why${c.kind === 'none' ? ' is-muted' : ''}">${escapeHtml(c.text || '')}</span>`, text: c.text || '' };
}
/* A final action as the same call badge the Overview uses (Buy solid, Accumulate outline, Watchlist dashed). */
function pkActionBadge(action) {
  const m = actionMeta({ final_action: action });
  const tone = m.key === 'buy' || m.key === 'strong_buy' ? 'is-buy' : m.key === 'accumulate' ? 'is-acc' : m.key === 'watchlist' ? 'is-watch' : 'is-neutral';
  return `<span class="pk-signal ${tone}">${escapeHtml(m.label)}</span>`;
}
/* D2: share classes fail closed. A class held back with this row's class (the
   Research check held this one back) is named on the row, with the reason. */
/* B-89: a name the account already holds (broker positions loaded in My Portfolio). */
function pkOwnHtml(r, sym) {
  const h = r && r.held;
  if (!h) return '';
  const pct = pkNum(h.pct_of_capital);
  const q = pkNum(h.quantity);
  const mv = pkNum(h.market_value);
  const title = `${q !== null ? `${q} share${q === 1 ? '' : 's'}, ` : ''}${mv !== null ? fmtMoney(mv, sym, 0) : 'value unknown'}`;
  return `<span class="pk-own" data-pk-own title="${escapeHtml(title)}">Already held${pct !== null ? ` · ${escapeHtml(String(pct))}% of capital` : ''}</span>`;
}
function pkHoldingsNoteHtml(data) {
  const hc = data && data.holdings_check;
  if (!hc || !hc.state) return '';
  const text = hc.state === 'checked'
    ? `Checked against your broker holdings${hc.retrieved_at ? ` (read ${String(hc.retrieved_at).slice(0, 16).replace('T', ' ')} UTC)` : ''}: ${Number(hc.held) || 0} of these names already held; their sizes count what you own.`
    : (hc.text || '');
  return text ? `<p class="pk-note" data-pk-holdings>${escapeHtml(text)}</p>` : '';
}
function pkHeldClasses(r) {
  return (r.other_classes || []).filter((o) => o && o.held_back);
}
function pkHeldClassesHtml(r) {
  const held = pkHeldClasses(r);
  if (!held.length) return '';
  const names = held.map((o) => o.ticker).join(', ');
  return `<span class="pk-held" data-pk-held title="${escapeHtml(held[0].reason || '')}">${escapeHtml(names)} held back too: same company</span>`;
}
function pkShareClassText(r) {
  const others = (r.other_classes || []).map((o) => o.ticker).join(', ');
  const held = pkHeldClasses(r);
  if (held.length) {
    const names = held.map((o) => o.ticker).join(', ');
    return `Also trades as ${others}: the same company. The Research check held ${r.ticker} back, so ${names} ${held.length === 1 ? 'is' : 'are'} held back too; no class of this company gets new money.`;
  }
  return `Also trades as ${others}: the same company. One class per company is shown, chosen after the Research check.`;
}
function pkDecisionHtml(r) {
  const d = pkDecision(r);
  return `<span class="pk-signal is-${d.tone}">${escapeHtml(d.label)}</span>`;
}

/* A titled panel: header strip, then a body whose rows share its height. */
function pkPanel(title, body, opts = {}) {
  return `<section class="pkp${opts.cls ? ` ${opts.cls}` : ''}"${opts.attrs || ''}><header class="pkp-h"><h5>${escapeHtml(title)}</h5>${opts.meta ? `<span class="pkp-meta">${escapeHtml(opts.meta)}</span>` : ''}</header><div class="pkp-b">${body}</div></section>`;
}
const pkMissing = (text) => `<p class="pk-wp-empty">${escapeHtml(text)}</p>`;
const pkAbbr = (k, title) => `<abbr title="${escapeHtml(title)}">${escapeHtml(k)}</abbr>`;
const PK_TIPS = {
  rr: 'Reward to risk: the gain to the target for each dollar risked to the stop, after trading costs.',
  rsi: 'Relative Strength Index over 14 sessions: above the upper band the run is stretched, below the lower band it is oversold.',
  atr: 'Average True Range: the typical daily price range, as a share of the price.',
  dcf: 'Discounted cash flow: projected free cash flows discounted to today.',
};

/* ── Overview ──────────────────────────────────────────────────────────── */
/* The market a result scanned: its own field, else the currency it priced in. */
const pkIsIndia = (data) => /^IN(DIA)?$/i.test(String(data.market || '')) || (!data.market && data.currency === '\u20b9');

function pkHeadHtml(data, rows, sym) {
  void sym;
  const when = data.timestamp && !Number.isNaN(Date.parse(data.timestamp)) ? fmtStamp(data.timestamp) : '';
  const meta = [when ? `Run ${when}` : '', pkIsIndia(data) ? 'India' : 'US',
    (data.risk_profile || {}).profile ? `${titleLabel(data.risk_profile.profile)} mandate` : '',
    data.max_positions ? `Up to ${Number(data.max_positions)} positions` : '',
    pkFiltersText(data) ? `Filters: ${pkFiltersText(data)}` : ''].filter(Boolean);
  const ba = rows.filter((r) => ['buy', 'accumulate'].includes(pkGroupOf(r))).length;
  const wl = rows.filter((r) => pkGroupOf(r) === 'watchlist').length;
  const scanned = Number(data.total_scanned || (data.accounting || {}).universe || 0);
  if (data.scan_type) meta.unshift(`${scanTypeName(data.scan_type)} scan`);
  const copy = [ba ? '<button type="button" class="btn-mini" data-pk-copy="ba">Copy Buy and Accumulate</button>' : '',
    wl ? '<button type="button" class="btn-mini" data-pk-copy="watchlist">Copy Watchlist</button>' : ''].join('');
  // One strip: the count, the run settings on one line, and one Export menu.
  const tools = rows.length ? `<details class="pk-export"><summary class="btn-mini">Export</summary>
      <div class="pk-actions pk-export-menu" role="group" aria-label="Export">${copy}<button type="button" class="btn-mini" data-pk-csv>Download CSV</button></div></details>` : '';
  return `<header class="pk-head" data-picks-head>
      <h3 class="pk-title" data-result-heading tabindex="-1">${escapeHtml(`${ba} Buy or Accumulate, ${wl} Watchlist${scanned ? `, from ${scanned} scanned` : ''}`)}</h3>
      <ul class="pk-meta" aria-label="Scan settings" title="${escapeHtml(meta.join(' \u00b7 '))}">${meta.map((m) => `<li>${escapeHtml(m)}</li>`).join('')}</ul>
      ${tools}
    </header>`;
}

/* One row of figures; each appears once in the report. */
function pkSummaryCardsHtml(data, rows, sym) {
  const capital = Number(data.capital || 0);
  const ba = rows.filter((r) => ['buy', 'accumulate'].includes(pkGroupOf(r)));
  const watch = rows.filter((r) => pkGroupOf(r) === 'watchlist');
  const sized = rows.filter(pkIsSized);
  const invested = sized.reduce((a, r) => a + Number(r.allocation || 0), 0);
  const names = (list) => (list.length ? list.slice(0, 3).map((r) => r.ticker).join(', ') + (list.length > 3 ? ` and ${list.length - 3} more` : '') : 'None');
  const scanned = Number(data.total_scanned || (data.accounting || {}).universe || 0);
  const out = (data.screened_out || []).length + Number(((data.accounting || {}).rule_rejected || {}).count || 0);
  const ex = (data.excluded_for_safety || []).length;
  const failed = Number(((data.accounting || {}).could_not_analyse || {}).count || 0);
  const pct = (v) => (capital > 0 ? `${(v / capital * 100).toFixed(1)}%` : '');
  const regime = data.regime || {};
  const unknown = /UNKNOWN/i.test(regime.type || '');
  const cell = (k, v, sub, cls = '') => `<div class="pk-sc${cls}"><dt>${escapeHtml(k)}</dt><dd><b>${escapeHtml(v)}</b>${sub ? `<span>${escapeHtml(sub)}</span>` : ''}</dd></div>`;
  const regimeSub = [pkNum(regime.vix) > 0 ? `${pkIsIndia(data) ? 'India VIX' : 'VIX'} ${Number(regime.vix).toFixed(1)}${regime.vix_is_proxy ? ' (est.)' : ''}` : '',
    pkNum(regime.exposure) !== null ? `exposure ${Number(regime.exposure)}%` : '',
    unknown ? 'trend unread: benchmark history unavailable' : ''].filter(Boolean).join(', ');
  return `<dl class="pk-scards" data-pk-summary>
      ${cell('Buy and Accumulate', String(ba.length), names(ba))}
      ${cell('Watchlist', String(watch.length), names(watch))}
      ${cell('Scanned', String(scanned), [out ? `${out} screened out` : '', ex ? `${ex} excluded for safety` : '',
    failed ? `${failed} not analysed` : ''].filter(Boolean).join(' · '))}
      ${cell('Suggested deployment', fmtMoney(invested, sym, 0), capital ? `${pct(invested)} of ${fmtMoney(capital, sym, 0)} capital` : '')}
      ${regime.type ? cell('Regime', unknown ? 'Unknown' : titleLabel(regime.type), regimeSub) : ''}
    </dl>`;
}

/* [Result filters] Narrow the table without rescanning. */
function pkResultFiltersHtml(rows) {
  if (rows.length < 2) return '';
  const uniq = (xs) => [...new Set(xs.filter(Boolean))].sort();
  const sectors = uniq(rows.map((r) => r.sector));
  const grades = uniq(rows.map((r) => r.risk_grade));
  const opt = (v, l) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`;
  return `<div class="pk-rf" role="group" aria-label="Filter the calls">
      ${sectors.length > 1 ? `<label class="pk-rf-f"><span>Sector</span><select data-pk-rf="sector">${opt('', 'All')}${sectors.map((s) => opt(s, s)).join('')}</select></label>` : ''}
      ${grades.length > 1 ? `<label class="pk-rf-f"><span>Risk grade</span><select data-pk-rf="grade">${opt('', 'Any')}${grades.map((g) => opt(g, `${g} or better`)).join('')}</select></label>` : ''}
      <label class="pk-rf-f"><span>Score at least</span><input type="number" inputmode="numeric" min="0" max="100" step="5" placeholder="Any" data-pk-rf="score"></label>
      <label class="pk-rf-f"><span>R:R at least</span><input type="number" inputmode="decimal" min="0" step="0.1" placeholder="Any" data-pk-rf="rr"></label>
      <button type="button" class="btn-mini" data-pk-rf-clear hidden>Clear</button>
      <span class="pk-rf-count" data-pk-rf-count aria-live="polite"></span>
    </div>`;
}

/* The scan filters a result ran with, in words ("" when none). */
function pkFiltersText(data) {
  const f = (data && data.filters) || {};
  if (!Object.keys(f).length) return '';
  const india = pkIsIndia(data);
  const cap = (v) => (india ? `\u20b9${(v / 1e7).toLocaleString('en-IN')} crore` : `$${(v / 1e9).toLocaleString('en-US')}B`);
  const val = (v) => (india ? `\u20b9${(v / 1e7).toLocaleString('en-IN')} crore` : `$${(v / 1e6).toLocaleString('en-US')}M`);
  const px = (v) => fmtMoney(v, india ? '\u20b9' : '$', 0);
  const range = (lo, hi, fmt) => (lo != null && hi != null ? `${fmt(lo)} to ${fmt(hi)}` : lo != null ? `at least ${fmt(lo)}` : `up to ${fmt(hi)}`);
  const parts = [];
  if ((f.sectors || []).length) parts.push(f.sectors.length <= 2 ? f.sectors.join(', ') : `${f.sectors.length} sectors`);
  if (f.min_market_cap != null || f.max_market_cap != null) parts.push(`market cap ${range(f.min_market_cap, f.max_market_cap, cap)}`);
  if (f.min_price != null || f.max_price != null) parts.push(`price ${range(f.min_price, f.max_price, px)}`);
  if (f.min_daily_value != null) parts.push(`at least ${val(f.min_daily_value)} traded a day`);
  return parts.join('; ');
}

function pkStatusChipsHtml(rows) {
  if (rows.length < 2) return '';
  const present = PK_GROUPS.filter(([k]) => rows.some((r) => pkGroupOf(r) === k));
  if (present.length < 2) return '';
  return `<div class="pk-chips" role="group" aria-label="Show stocks by call">
      <button type="button" class="pk-chip-f" aria-pressed="true" data-pk-filter="all">All</button>
      ${present.map(([k, label]) => `<button type="button" class="pk-chip-f" aria-pressed="false" data-pk-filter="${k}">${escapeHtml(label)}</button>`).join('')}
    </div>`;
}

function pkRowHtml(r, i, sym) {
  const sized = pkIsSized(r);
  const rr = pkNum(r.risk_reward);
  const dec = Number(thr('UI.RR_DECIMALS')) || 2;
  const sub = r.status !== 'avoid' ? r.setup || '' : '';
  const position = pkCapital(r, sym).html;
  return `
    <tr class="pk-row${sized ? ' is-sized' : ''}" data-pk-ticker="${escapeHtml(r.ticker)}" data-pk-outcome="${cssToken(pkGroupOf(r))}" data-pk-sector="${escapeHtml(r.sector || '')}" data-pk-grade="${escapeHtml(r.risk_grade || '')}" data-pk-score="${score100(r.score) ?? ''}" data-pk-rr="${pkNum(r.risk_reward) ?? ''}">
      <td class="pk-c-rank num">${escapeHtml(String(r.rank))}</td>
      <td class="pk-c-stock"><button type="button" class="pk-ticker" data-ticker="${escapeHtml(r.ticker)}" title="Open the ${escapeHtml(r.ticker)} tear sheet">${escapeHtml(r.ticker)}</button><span class="pk-co">${escapeHtml(r.company || '')}</span>${pkHeldClassesHtml(r)}${pkOwnHtml(r, sym)}</td>
      <td class="pk-c-price num" data-label="Price">${pkNum(r.price) ? escapeHtml(fmtPrice(r.price, sym)) : PK_NA}</td>
      <td class="pk-c-signal">${pkDecisionHtml(r)}${sub ? `<span class="pk-setup">${escapeHtml(sub)}</span>` : ''}</td>
      <td class="pk-c-levels">${pkLevelsCellHtml(r, sym)}</td>
      <td class="pk-c-rr num" data-label="R:R">${r.status !== 'avoid' && rr ? escapeHtml(rr.toFixed(dec)) : PK_NA}</td>
      <td class="pk-c-score" data-label="Score">${pkScoreCellHtml(r)}</td>
      <td class="pk-c-pos">${position}</td>
      <td class="pk-c-more"><button type="button" class="pk-expand" data-pk-open="${escapeHtml(r.ticker)}" aria-label="Open the ${escapeHtml(r.ticker)} analysis"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></td>
    </tr>`;
}

function pkOverviewHtml(data, rows, sym, foot) {
  const hasBuy = rows.some((r) => ['buy', 'accumulate'].includes(pkGroupOf(r)));
  const ev = (rows.find((r) => r.evidence) || {}).evidence;
  const note = modelFlagHtml(ev, 'data-pk-valid');
  return `${pkSummaryCardsHtml(data, rows, sym)}
    ${hasBuy ? '' : pkEmptyHtml(data, rows)}
    ${rows.length ? `<div class="pk-ov-tools">${pkStatusChipsHtml(rows)}${pkResultFiltersHtml(rows)}
      <div class="pk-ov-sub"><p class="pk-note">Buy, then Accumulate, then Watchlist; by score within each.</p>${note}</div>${pkHoldingsNoteHtml(data)}</div>
      ${pkTableHtml(rows, sym)}<p class="pk-nomatch" data-pk-nomatch hidden>No call matches these filters.</p>${pkWarningsHtml(data, rows)}` : ''}
    ${foot}`;
}

/* ── Analysis ──────────────────────────────────────────────────────────── */
function pkAnalysisHtml(rows, data, sym) {
  const ordered = pkCardRows(rows);
  if (!ordered.length) return '<p class="pk-panel-empty">No Buy, Accumulate or Watchlist call in this scan, so there is nothing to analyse.</p>';
  const first = ordered[0].ticker;
  const list = PK_GROUPS.map(([k, label]) => {
    const items = ordered.filter((r) => pkGroupOf(r) === k);
    if (!items.length) return '';
    return `<div class="pk-alist-group"><p class="pk-alist-h" id="pk-alist-h-${k}">${escapeHtml(label)}</p><ul aria-labelledby="pk-alist-h-${k}">${items.map((r) => `
        <li class="pk-alist-li"><input type="checkbox" class="pk-cmp-box" data-pk-cmp="${escapeHtml(r.ticker)}" aria-label="Add ${escapeHtml(r.ticker)} to the comparison"><button type="button" class="pk-alist-item" aria-controls="pk-card-${escapeHtml(r.ticker)}"${r.ticker === first ? ' aria-current="true"' : ''} data-pk-pick="${escapeHtml(r.ticker)}">
          <span class="pk-alist-t">${escapeHtml(r.ticker)}</span>
          <span class="pk-alist-s">${escapeHtml(r.setup || r.sector || '')}</span>
          <span class="pk-alist-n">${pkNum(r.score) !== null ? escapeHtml(fmtScore100(r.score)) : ''}</span>
        </button></li>`).join('')}</ul></div>`;
  }).join('');
  return `<div class="pk-analysis">
      <nav class="pk-alist" aria-label="Buy, Accumulate and Watchlist calls">
        <div class="pk-cmp-bar"><button type="button" class="btn-mini" data-pk-compare disabled>Compare</button><span class="pk-cmp-hint">Tick 2 to ${escapeHtml(String(thr('WHY.COMPARE_MAX')))}</span></div>
        ${list}
      </nav>
      <div class="pk-acards">
        <section class="pk-compare" data-pk-compare-panel hidden aria-label="Comparison"></section>
        ${ordered.map((r) => pkCardHtml(r, data, sym, false).replace('<article ', r.ticker === first ? '<article ' : '<article hidden ')).join('')}
      </div>
    </div>`;
}

/* The figures a decision rests on, each labelled once. */
function pkKeysHtml(r, sym) {
  const price = pkNum(r.price);
  const stop = pkNum(r.stop);
  const target = pkNum(r.target);
  const dec = Number(thr('UI.RR_DECIMALS')) || 2;
  const g = pkGauge(r);
  const cells = [['Price', price ? fmtPrice(price, sym) : '-', price ? 'last close' : '', '']];
  if (r.status !== 'avoid' && target && price) {
    const up = (target / price - 1) * 100;
    cells.push(['Target', fmtPrice(target, sym), up >= 0 ? `${pkSignedPct(up)} from price` : 'below the price',
      up >= 0 ? 'is-bull' : 'is-flat', up >= 0 ? '' : 'The level target sits under today\'s price, so the plan expects no gain from here.']);
  }
  if (r.status !== 'avoid' && stop && price) cells.push(['Stop', fmtPrice(stop, sym), `${pkSignedPct((stop / price - 1) * 100)} from price`, 'is-bear']);
  if (r.status !== 'avoid' && pkNum(r.risk_reward)) cells.push([pkAbbr('R:R', PK_TIPS.rr), Number(r.risk_reward).toFixed(dec), 'net of costs', '', '', true]);
  if (pkNum(r.score) !== null) cells.push(['Score', fmtScore100(r.score), 'of 100', '']);
  return `<dl class="pk-keys">${cells.map(([k, v, sub, cls, tip, rawKey]) => `<div class="${cls || ''}"${tip ? ` title="${escapeHtml(tip)}"` : ''}><dt>${rawKey ? k : escapeHtml(k)}</dt><dd><b>${escapeHtml(v)}</b>${sub ? `<span>${escapeHtml(sub)}</span>` : ''}</dd></div>`).join('')}
      <div class="pk-keys-risk"><dt>Risk</dt><dd>${pkGaugeHtml(g)}${g.detail ? `<span>${escapeHtml(g.detail)}</span>` : ''}</dd></div></dl>`;
}

/* Price chart: SVG lines stretch to the panel; labels are HTML. */
function pkChartHtml(r, sym) {
  const h = r.history;
  const n = Number(thr('WHY.CHART_SESSIONS')) || 126;
  if (!h || !Array.isArray(h.close) || h.close.length < 30) return pkMissing('No price history was kept for this name, so there is no chart.');
  const closes = h.close.map(Number);
  const start = Math.max(0, closes.length - n);
  const win = closes.slice(start);
  const dates = (h.dates || []).slice(start);
  const ma50 = pkSma(closes, 50).slice(start);
  const ma200 = pkSma(closes, 200).slice(start);
  const stop = pkNum(r.stop); const target = pkNum(r.target);
  const zl = pkNum(r.entry_low) || pkNum(r.entry); const zh = pkNum(r.entry_high) || pkNum(r.entry);
  const levels = r.status === 'avoid' ? [] : [stop, target, zl, zh].filter((v) => v);
  const vals = win.concat(ma50.filter((v) => v), ma200.filter((v) => v), levels);
  let lo = Math.min(...vals); let hi = Math.max(...vals);
  const pad = (hi - lo || hi || 1) * 0.06; lo -= pad; hi += pad;
  const W = 1000; const H = 400;
  const x = (i) => (i / Math.max(1, win.length - 1)) * W;
  const yv = (v) => ((hi - v) / (hi - lo || 1)) * H;
  const pctY = (v) => `${((yv(v) / H) * 100).toFixed(2)}%`;
  const line = (arr) => {
    const segs = []; let cur = [];
    arr.forEach((v, i) => { if (v === null || !Number.isFinite(v)) { if (cur.length) segs.push(cur); cur = []; } else cur.push(`${x(i).toFixed(1)},${yv(v).toFixed(1)}`); });
    if (cur.length) segs.push(cur);
    return segs.map((s) => `M${s.join('L')}`).join(' ');
  };
  const band = zl && zh && r.status !== 'avoid'
    ? `<rect class="pk-ch-zone" x="0" y="${yv(Math.max(zl, zh)).toFixed(1)}" width="${W}" height="${Math.max(3, Math.abs(yv(zl) - yv(zh))).toFixed(1)}"></rect>` : '';
  const hline = (v, cls) => (v ? `<line class="${cls}" x1="0" x2="${W}" y1="${yv(v).toFixed(1)}" y2="${yv(v).toFixed(1)}"></line>` : '');
  const last = win[win.length - 1];
  const tags = (r.status === 'avoid' ? [] : [['Target', target, 'is-bull'], ['Stop', stop, 'is-bear']])
    .concat([['Now', last, 'is-now']]).filter(([, v]) => v);
  const ma = (arr) => { const v = arr.filter((z) => z !== null).pop(); return v ? fmtPrice(v, sym) : null; };
  const m50 = ma(ma50); const m200 = ma(ma200);
  const legend = [['pk-lg-price', 'Price'], m50 ? ['pk-lg-ma50', `50-day avg ${m50}`] : null, m200 ? ['pk-lg-ma200', `200-day avg ${m200}`] : null,
    band ? ['pk-lg-zone', 'Entry zone'] : null, levels.length ? ['pk-lg-stop', 'Stop'] : null, levels.length ? ['pk-lg-target', 'Target'] : null].filter(Boolean);
  const first = win[0];
  const chg = first ? (last / first - 1) * 100 : null;
  const label = `${r.ticker} closes ${dates[0] || ''} to ${dates[dates.length - 1] || ''}: ${fmtPrice(first, sym)} to ${fmtPrice(last, sym)}, ${pkSignedPct(chg)}.`;
  return `<figure class="pk-chart">
      <div class="pk-ch-plot" role="img" aria-label="${escapeHtml(label)}">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          ${band}${hline(stop, 'pk-ch-stop')}${hline(target, 'pk-ch-target')}
          <path class="pk-ch-ma200" d="${line(ma200)}"></path>
          <path class="pk-ch-ma50" d="${line(ma50)}"></path>
          <path class="pk-ch-price" d="${line(win)}"></path>
        </svg>
        <span class="pk-ch-dot" style="top:${pctY(last)}" aria-hidden="true"></span>
        ${tags.map(([t, v, c]) => `<span class="pk-ch-tag ${c}" style="top:${pctY(v)}" aria-hidden="true">${escapeHtml(t)}</span>`).join('')}
      </div>
      <div class="pk-ch-dates" aria-hidden="true"><span>${escapeHtml(dates[0] || '')}</span><span>${escapeHtml(pkSignedPct(chg))} over the window</span><span>${escapeHtml(dates[dates.length - 1] || '')}</span></div>
      <figcaption class="pk-legend">${legend.map(([c, t]) => `<span class="${c}">${escapeHtml(t)}</span>`).join('')}</figcaption>
    </figure>`;
}

/* One gauge per line: label, track, value, reading. */
function pkGaugeRow(label, value, pct, reading, opts = {}) {
  const zones = (opts.zones || []).map(([a, b, c]) => `<span class="pk-gz ${c}" style="left:${a}%;width:${b - a}%"></span>`).join('');
  const fill = opts.diverge
    ? `<span class="pk-gfill ${opts.tone || ''}" style="left:${Math.min(50, pct).toFixed(1)}%;width:${Math.abs(pct - 50).toFixed(1)}%"></span><span class="pk-gmid"></span>`
    : opts.marker ? `<span class="pk-gmark" style="left:${pct.toFixed(1)}%"></span>` : `<span class="pk-gfill ${opts.tone || ''}" style="left:0;width:${pct.toFixed(1)}%"></span>`;
  return `<div class="pk-grow"><dt>${opts.rawLabel ? label : escapeHtml(label)}</dt><dd><span class="pk-gtrack" aria-hidden="true">${zones}${fill}</span><span class="pk-gval">${escapeHtml(value)}</span><span class="pk-gread">${escapeHtml(reading || '')}</span></dd></div>`;
}

function pkTechHtml(r) {
  const t = r.tech || {};
  const price = pkNum(r.price);
  const rows = [];
  const clamp = (v) => Math.max(0, Math.min(100, v));
  const rsi = pkNum(t.rsi);
  const lo = Number(thr('WHY.RSI_OVERSOLD')); const hi = Number(thr('WHY.RSI_STRETCHED'));
  if (rsi !== null) {
    rows.push(pkGaugeRow(pkAbbr('RSI (14)', PK_TIPS.rsi), rsi.toFixed(1), clamp(rsi), rsi >= hi ? 'stretched' : rsi <= lo ? 'oversold' : 'neutral',
      { marker: true, zones: [[0, lo, 'is-low'], [hi, 100, 'is-high']], rawLabel: true }));
  }
  [['sma_20', '20-day'], ['sma_50', '50-day'], ['sma_200', '200-day']].forEach(([k, nm]) => {
    const m = pkNum(t[k]);
    if (!m || !price) return;
    const d = (price / m - 1) * 100;
    rows.push(pkGaugeRow(`Price vs ${nm} avg`, pkSignedPct(d), clamp(50 + d * 2.5), d >= 0 ? 'above' : 'below', { diverge: true, tone: 'is-info' }));
  });
  const vr = pkNum(t.volume_ratio);
  if (vr) rows.push(pkGaugeRow('Volume vs 20-day avg', `${vr.toFixed(2)}×`, clamp((vr / 3) * 100), vr >= 1 ? 'above avg' : 'below avg', { tone: 'is-info' }));
  const atrp = pkNum(t.atr) && price ? (t.atr / price) * 100 : pkNum(t.atr_percent);
  if (atrp) rows.push(pkGaugeRow(`${pkAbbr('ATR', PK_TIPS.atr)} daily range`, `${atrp.toFixed(1)}%`, clamp(atrp * 10), 'of price', { tone: 'is-info', rawLabel: true }));
  [['momentum_1m', '1-month return'], ['momentum_3m', '3-month return']].forEach(([k, nm]) => {
    const v = pkNum(t[k]);
    if (v === null) return;
    const p = v * 100;
    rows.push(pkGaugeRow(nm, pkSignedPct(p), clamp(50 + p * 2), p >= 0 ? 'gain' : 'loss', { diverge: true, tone: p >= 0 ? 'is-bull' : 'is-bear' }));
  });
  const h52 = pkNum(t.high_52w); const l52 = pkNum(t.low_52w);
  if (h52 && l52 && price && h52 > l52) {
    rows.push(pkGaugeRow('52-week range', pkSignedPct((price / h52 - 1) * 100), clamp(((price - l52) / (h52 - l52)) * 100),
      'from the high', { marker: true }));
  }
  return pkPanel('Technicals', rows.length ? `<dl class="pk-gauges">${rows.join('')}</dl>` : pkMissing('No technical readings were kept for this name.'), { cls: 'pk-tech' });
}

/* Factor scores, then the analysts and insiders: one panel, one source each. */
function pkStreetHtml(r, sym) {
  const sc = r.scores || {};
  const keys = ['technical', 'fundamental', 'momentum', 'quality', 'value'].filter((k) => pkNum(sc[k]) !== null && Number(sc[k]) !== 0);
  const rows = keys.map((k) => pkGaugeRow(titleLabel(k), Number(sc[k]).toFixed(1), Math.max(0, Math.min(100, Number(sc[k]) * 10)), '', { tone: 'is-info' }));
  const notes = [];
  if (!keys.length) notes.push(pkMissing('No factor scores were measured for this name.'));
  else if (r.is_fund) notes.push('<p class="pk-wp-note">A fund is scored on trend and momentum only.</p>');
  const a = r.analyst || {};
  const minCount = Number(thr('WHY.ANALYST_MIN_COUNT'));
  const s0 = r.analyst_split;
  const s = s0 && { buy: Number(s0.buy) || 0, hold: Number(s0.hold) || 0, sell: Number(s0.sell) || 0 };
  if (s && (s.buy + s.hold + s.sell) > 0) {
    const tot = s.buy + s.hold + s.sell;
    const w = (x) => `${((x / tot) * 100).toFixed(1)}%`;
    rows.push(`<div class="pk-grow pk-split"><dt>Analyst ratings</dt><dd><span class="pk-split-bar" aria-hidden="true"><span class="is-bull" style="width:${w(s.buy)}"></span><span class="is-info" style="width:${w(s.hold)}"></span><span class="is-bear" style="width:${w(s.sell)}"></span></span><span class="pk-gval">${tot}</span><span class="pk-gread pk-split-key"><span class="is-bull">${s.buy} buy</span> <span class="is-info">${s.hold} hold</span> <span class="is-bear">${s.sell} sell</span></span></dd></div>`);
  } else if (a.count >= minCount && pkNum(a.rec_mean)) {
    // 1 = strong buy ... 5 = sell, on the analysts' own scale.
    const pos = Math.max(0, Math.min(100, ((Number(a.rec_mean) - 1) / 4) * 100));
    rows.push(pkGaugeRow('Analyst consensus', `${titleLabel(a.rating || '')}`.trim() || Number(a.rec_mean).toFixed(1), pos,
      `${a.count} analysts, ${Number(a.rec_mean).toFixed(1)} of 5`, { marker: true }));
  } else {
    notes.push(pkMissing(a.count ? `Analysts: ${a.count} cover it, fewer than the ${minCount} a consensus needs.` : 'Analysts: no coverage reported.'));
  }
  const ins = r.insider || {};
  if (ins.buys || ins.sells) {
    const net = Number(ins.net_value || 0);
    const tone = net > 0 ? 'is-bull' : net < 0 ? 'is-bear' : 'is-info';
    const nb = Number(ins.buys) || 0;
    const ns = Number(ins.sells) || 0;
    const bw = nb + ns ? ((nb / (nb + ns)) * 100).toFixed(1) : '0';
    // [Picks revamp] Same row shape as Analyst ratings: bar, figure, reading.
    rows.push(`<div class="pk-grow pk-ins"><dt>Insiders, 90 days</dt><dd><span class="pk-split-bar" aria-hidden="true"><span class="is-bull" style="width:${bw}%"></span><span class="is-bear" style="width:${(100 - Number(bw)).toFixed(1)}%"></span></span><span class="pk-gval ${tone}">${net ? `${net > 0 ? '+' : '-'}${escapeHtml(fmtCurrency(Math.abs(net), sym, 0))}` : '0'}</span><span class="pk-gread pk-ins-line">${nb} buy${nb === 1 ? '' : 's'}, ${ns} sale${ns === 1 ? '' : 's'}, net</span></dd></div>`);
  } else {
    notes.push(pkMissing('Insiders: no open-market trades in the last 90 days.'));
  }
  return pkPanel('Factors and analysts', `${rows.length ? `<dl class="pk-gauges">${rows.join('')}</dl>` : ''}${notes.join('')}`, { cls: 'pk-street', meta: keys.length ? '0 to 10' : '' });
}

/* [Fix 4] Every estimate and the price on one labelled scale. */
function pkValuationHtml(r, sym) {
  const v = r.valuation;
  if (!v || !Array.isArray(v.marks) || !v.marks.length || !pkNum(v.price)) {
    return pkPanel('Valuation', pkMissing((v && v.missing && v.missing[0]) || 'No valuation estimates were measured for this name.'), { cls: 'pk-val' });
  }
  const lo = Number(v.low); const hi = Number(v.high);
  const at = (x) => Math.max(0, Math.min(100, ((x - lo) / (hi - lo || 1)) * 100));
  const get = (k) => v.marks.find((m) => m.key === k);
  const aLo = get('analyst_low'); const aHi = get('analyst_high');
  const range = aLo && aHi ? `<span class="pk-vrange" style="left:${at(aLo.value).toFixed(1)}%;width:${Math.max(1, at(aHi.value) - at(aLo.value)).toFixed(1)}%"></span>` : '';
  const ticks = v.marks.map((m) => `<span class="pk-vtick is-${cssToken(m.key)}" style="left:${at(m.value).toFixed(1)}%"></span>`).join('');
  const label = (m) => (m.key === 'dcf' ? `${pkAbbr('DCF', PK_TIPS.dcf)} fair value` : escapeHtml(m.label));
  const list = v.marks.concat([{ key: 'price', label: 'Price', value: v.price }]).sort((a, b) => a.value - b.value)
    .map((m) => `<li class="is-${cssToken(m.key)}"><span class="pk-vkey"></span><span class="pk-vname">${label(m)}</span><b>${escapeHtml(fmtPrice(m.value, sym))}</b><span class="pk-vgap">${m.key === 'price' ? '' : escapeHtml(pkSignedPct((m.value / v.price - 1) * 100))}</span></li>`).join('');
  return pkPanel('Valuation', `<div class="pk-vbar" aria-hidden="true"><span class="pk-gtrack"></span>${range}${ticks}<span class="pk-vprice" style="left:${at(v.price).toFixed(1)}%"></span></div>
      <ul class="pk-vlist">${list}</ul>
      ${v.sentence ? `<p class="pk-vsent">${escapeHtml(v.sentence)}</p>` : ''}
      ${(v.missing || []).map((m) => pkMissing(m)).join('')}`, { cls: 'pk-val' });
}

/* "Thesis fails if": each condition, its live value, and whether it holds. */
function pkFailsChecklistHtml(r, data, sym) {
  void sym;
  const price = pkNum(r.price);
  const items = [];
  const stop = pkNum(r.stop);
  if (stop && price && r.status !== 'avoid') {
    const gap = (price / stop - 1) * 100;
    items.push([gap <= 0, 'Close below the stop', `price ${Math.abs(gap).toFixed(1)}% ${gap >= 0 ? 'above' : 'below'} it`]);
  }
  const m50 = pkNum((r.tech || {}).sma_50) || pkNum(r.sma_50);
  if (m50 && price) items.push([price < m50, 'Loses the 50-day average', `price ${price >= m50 ? 'above' : 'below'} it`]);
  const vix = pkNum((data.regime || {}).vix);
  const skip = Number(thr('REGIME.VIX_SKIP'));
  if (vix && vix > 0) items.push([vix > skip, `VIX above ${skip}`, `VIX ${vix.toFixed(1)} now`]);
  const d = pkNum(r.days_to_earnings);
  if (!r.is_fund && d !== null) {
    const near = d <= Number(thr('WHY.EARNINGS_NEAR_DAYS'));
    items.push([null, 'Guidance cut at earnings', `earnings in ${d} day${d === 1 ? '' : 's'}${near ? ', soon' : ''}`]);
  } else if (!r.is_fund) items.push([null, 'Guidance cut at earnings', 'no earnings date reported']);
  if (!items.length) return pkMissing('No live condition could be checked for this name.');
  return `<ul class="pk-check">${items.map(([hit, k, v]) => `<li class="${hit === true ? 'is-hit' : hit === false ? 'is-ok' : 'is-wait'}"><span class="pk-check-k">${escapeHtml(k)}</span><span class="pk-check-v">${escapeHtml(v)}</span><span class="pk-check-s">${hit === true ? 'Triggered' : hit === false ? 'Holding' : 'Pending'}</span></li>`).join('')}</ul>`;
}

/* One stock in full: key strip; chart beside the plan; reasons; three evidence panels. */
function pkCardHtml(r, data, sym, inline) {
  void inline;
  const w = r.why_picked || { for: [], against: [], news: [], notes: [] };
  const sized = pkIsSized(r);
  const isCall = ['buy', 'accumulate'].includes(pkGroupOf(r));
  const stars = pkStars(r.conviction);
  const cap = pkCapital(r, sym);
  const d = pkDecision(r);
  const watchWhy = !isCall ? [pkReason(r, sym), r.fix ? `Fix: ${r.fix}` : ''].filter(Boolean).join(' ') : '';
  const against = (watchWhy ? [{ label: d.label, text: watchWhy }] : []).concat(w.against || []);
  const list = (items) => `<ul class="pk-bullets">${items.map((it) => `<li><b>${escapeHtml(it.label || '')}.</b> ${escapeHtml(it.text || '')}${it.source ? ` <span class="pk-wp-src">${escapeHtml(it.source)}</span>` : ''}</li>`).join('')}</ul>`;
  const news = (w.news || []).length ? `<h6>Recent company news</h6><ul class="pk-news">${w.news.map((n) => {
    const meta = [n.publisher, pkShortDate(n.published)].filter(Boolean).join(' · ');
    const title = safeUrl(n.url) ? `<a href="${escapeHtml(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(n.title)}</a>` : escapeHtml(n.title);
    return `<li>${title}${meta ? `<span class="pk-wp-meta">${escapeHtml(meta)}</span>` : ''}</li>`;
  }).join('')}</ul>` : '';
  const plan = `${pkExecutionHtml(r, sym)}
      <dl class="pk-card-dl">
        <div><dt>Your capital</dt><dd>${escapeHtml(sized ? pkPositionText(r, data, sym) : cap.text)}</dd></div>
        ${isCall ? `<div><dt>Conviction</dt><dd><span class="pk-stars">${stars.text}</span> ${stars.n}/5 <span class="pk-wp-src">a heuristic tier, not a win probability</span></dd></div>` : ''}
        ${(r.other_classes || []).length ? `<div><dt>Share classes</dt><dd>${escapeHtml(pkShareClassText(r))}</dd></div>` : ''}
      </dl>
      <h6>Thesis fails if</h6>
      ${pkFailsChecklistHtml(r, data, sym)}`;
  const why = `<p class="pk-wp-summary">${escapeHtml(w.summary || (isCall && r.why ? r.why : 'No single measured factor stands out; it ranks on its overall score.'))}</p>
      <div class="pk-flow">
        <h6>Working for it</h6>
        ${(w.for || []).length ? list(w.for) : '<p class="pk-wp-empty">Nothing measured stands out beyond the overall score.</p>'}
        <h6 class="is-caution">Caution</h6>
        ${against.length ? list(against) : '<p class="pk-wp-empty">Nothing measured counts against it.</p>'}
        ${(w.notes || []).map((n) => `<p class="pk-wp-note">${escapeHtml(n)}</p>`).join('')}
        ${news}
      </div>`;
  return `<article class="pk-card is-${d.tone}" id="pk-card-${escapeHtml(r.ticker)}" data-pk-card="${escapeHtml(r.ticker)}" tabindex="-1" aria-labelledby="pk-card-h-${escapeHtml(r.ticker)}">
      <header class="pk-card-head">
        <span class="pk-card-rank">#${escapeHtml(String(r.rank))}</span>
        <h4 class="pk-card-title" id="pk-card-h-${escapeHtml(r.ticker)}"><button type="button" class="pk-ticker" data-ticker="${escapeHtml(r.ticker)}">${escapeHtml(r.ticker)}</button><span class="pk-card-co">${escapeHtml(r.company || '')}</span></h4>
        ${r.sector ? `<span class="pk-card-sector">${escapeHtml(r.sector)}</span>` : ''}
        <span class="pk-card-tags">${pkDecisionHtml(r)}${['buy', 'accumulate'].includes(pkGroupOf(r)) ? modelFlagHtml(r.evidence) : ''}</span>${pkOwnHtml(r, sym)}
      </header>
      ${pkKeysHtml(r, sym)}
      <div class="pk-row2">
        ${pkPanel('Price', pkChartHtml(r, sym), { cls: 'pk-chartp', meta: `${Number(thr('WHY.CHART_SESSIONS'))} sessions` })}
        ${pkPanel('Plan', plan, { cls: 'pk-plan' })}
      </div>
      ${pkPanel('Why this stock was picked', why, { cls: 'pk-whyp', attrs: ' data-why-picked' })}
      <div class="pk-row3">
        ${pkTechHtml(r)}
        ${pkStreetHtml(r, sym)}
        ${pkValuationHtml(r, sym)}
      </div>
      <footer class="pk-card-foot"><button type="button" class="btn-mini pk-open" data-ticker="${escapeHtml(r.ticker)}">Open full analysis</button>${r.levels_label ? `<span class="pk-wp-src">${escapeHtml(r.levels_label)}${pkNum(r.horizon_days) ? ` · ${Number(r.horizon_days)}-session horizon` : ''}${r.price_basis && pkNum(r.price_basis.price) ? ` · levels from the ${escapeHtml(fmtPrice(r.price_basis.price, sym))} close${r.price_basis.session ? ` of ${escapeHtml(r.price_basis.session)}` : ''}` : ''}</span>` : ''}</footer>
    </article>`;
}

/* Only the warnings that hold for this result. */
function pkWarningsHtml(data, rows) {
  if (!rows.length) return '';
  const out = [];
  const vix = pkNum((data.regime || {}).vix);
  if (vix !== null && vix > Number(thr('REGIME.VIX_SKIP'))) out.push(`VIX ${vix.toFixed(1)} is above ${thr('REGIME.VIX_SKIP')}: consider standing down.`);
  if (rows.some(pkIsSized)) out.push(`Re-check the entry if the open gaps more than ${(Number(thr('CHECKLIST.PREMARKET_GAP_PCT')) * 100).toFixed(0)}% against you.`);
  const sectors = {};
  rows.forEach((r) => { if (r.sector) sectors[r.sector] = (sectors[r.sector] || 0) + 1; });
  const conc = Object.entries(sectors).filter(([, n]) => n >= 2).map(([s, n]) => `${s} (${n})`);
  if (conc.length) out.push(`Concentration: ${conc.join(', ')} among these calls; their prices tend to move together.`);
  return out.length ? `<ul class="pk-warn" data-pk-warn>${out.map((t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>` : '';
}






const PK_NA = '<span class="pk-na" aria-label="not available">-</span>';



const PK_SORT = {
  rank: (r) => -Number(r.rank), price: (r) => r.price, levels: (r) => r.upside_pct,
  rr: (r) => r.risk_reward, score: (r) => r.score,
};
const PK_COLS = [['rank', '#'], ['stock', 'Stock'], ['price', 'Price'], ['signal', 'Call'], ['levels', 'Levels'],
  ['rr', 'R:R'], ['score', 'Score'], ['pos', 'Your capital']];

/* [Overview] Stop, entry zone, target and price on one scale, each labelled. */
function pkLevelsCellHtml(r, sym) {
  const stop = pkNum(r.stop);
  const target = pkNum(r.target);
  const price = pkNum(r.price);
  if (r.status === 'avoid' || !stop || !target) return PK_NA;
  const zl = pkNum(r.entry_low) || pkNum(r.entry);
  const zh = pkNum(r.entry_high) || pkNum(r.entry);
  const vals = [stop, target, price, zl, zh].filter((v) => v);
  const lo = Math.min(...vals); const hi = Math.max(...vals);
  const at = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo || 1)) * 100));
  const up = pkNum(r.upside_pct);
  const zone = zl && zh ? `<span class="pk-lv-zone" style="left:${at(Math.min(zl, zh)).toFixed(1)}%;width:${Math.max(1.5, Math.abs(at(zh) - at(zl))).toFixed(1)}%"></span>` : '';
  return `<div class="pk-lv">
      <div class="pk-lv-bar" aria-hidden="true"><span class="pk-lv-track"></span>${zone}<span class="pk-lv-stop" style="left:${at(stop).toFixed(1)}%"></span><span class="pk-lv-target" style="left:${at(target).toFixed(1)}%"></span>${price ? `<span class="pk-lv-price" style="left:${at(price).toFixed(1)}%"></span>` : ''}</div>
      <div class="pk-lv-labels"><span class="pk-px-stop">Stop ${escapeHtml(fmtPrice(stop, sym))}</span><span class="pk-px-target">Target ${escapeHtml(fmtPrice(target, sym))}${up !== null ? ` <span class="pk-lv-up${up < 0 ? ' is-neg' : ''}">${escapeHtml(pkSignedPct(up))}</span>` : ''}</span></div>
      <div class="pk-lv-entry">Entry ${escapeHtml(pkEntryText(r, sym)).split('\u0000').map((x) => `<span class="pk-zone">${x}</span>`).join('')}</div>
    </div>`;
}

/* [Overview] The score as a bar on its 0 to 10 scale. */
function pkScoreCellHtml(r) {
  const s = pkNum(r.score);
  if (s === null) return PK_NA;
  const pct = score100(s);
  return `<div class="pk-sb"><b>${escapeHtml(fmtScore100(s))}</b><span class="pk-sb-track" aria-hidden="true"><span style="width:${pct.toFixed(0)}%"></span></span></div>`;
}


function pkTableHtml(rows, sym) {
  const head = PK_COLS.map(([k, label]) => {
    const sortable = Object.prototype.hasOwnProperty.call(PK_SORT, k);
    const num = ['rank', 'price', 'rr'].includes(k) ? ' num' : '';
    return `<th scope="col" class="pk-c-${k}${num}"${k === 'rank' ? ' aria-sort="ascending"' : ''}>${sortable
      ? `<button type="button" class="pk-sort" data-sort="${k}">${escapeHtml(label)}</button>` : escapeHtml(label)}</th>`;
  }).join('');
  return `<div class="pk-tablewrap">
      <table class="pk-table">
        <caption class="sr-only">QUANTA's calls: Buy, then Accumulate, then Watchlist, by score within each</caption>
        <thead><tr>${head}<th scope="col" class="pk-c-more"><span class="sr-only">Analysis</span></th></tr></thead>
        <tbody>${rows.map((r, i) => pkRowHtml(r, i, sym)).join('')}</tbody>
      </table>
    </div>`;
}







/* The report's sections. Each is a tab; the Overview opens first. */
function pkSectionsHtml(data, rows, sym) {
  const chase = data.chase_alerts || [];
  const early = data.early_momentum || [];
  const filled = data.chase_filled || [];
  const sized = rows.filter(pkIsSized);
  const nSignals = chase.length + early.length + filled.length;
  const tabs = [
    ['overview', 'Overview', ''],
    ['analysis', 'Analysis', rows.length ? String(rows.length) : ''],
    ['signals', 'Momentum alerts', nSignals ? String(nSignals) : ''],
    ['risk', 'Risk and execution', ''],
    ['method', 'Method', ''],
  ];
  const foot = '<p class="pk-foot">Suggested sizes come from your capital and each stop. QUANTA places no orders. Paper-trade before risking capital.</p>';
  const signals = nSignals ? renderPicksBuckets(chase, early, filled)
    : '<p class="pk-panel-empty">No chase alerts, early-momentum setups or filled gaps in this scan.</p>';
  const panel = (key, body, extra = '') => `<section class="pk-panel${extra}" id="pk-panel-${key}" role="tabpanel" aria-labelledby="pk-tabbtn-${key}" tabindex="0"${key === 'overview' ? '' : ' hidden'} data-pk-panel="${key}">${body}</section>`;
  return `<div class="pk-tabs" role="tablist" aria-label="Scan report sections">${tabs.map(([k, label, n], i) => `
      <button type="button" class="pk-tab" role="tab" id="pk-tabbtn-${k}" aria-controls="pk-panel-${k}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}" data-pk-tab="${k}">${escapeHtml(label)}${n ? `<span class="pk-tab-n">${escapeHtml(n)}</span>` : ''}</button>`).join('')}
    </div>
    ${panel('overview', pkOverviewHtml(data, rows, sym, foot))}
    ${panel('analysis', pkAnalysisHtml(rows, data, sym))}
    ${panel('signals', signals)}
    ${panel('risk', `${eventCheckBannerHtml(data)}${eventHeldDiagnosticsHtml(data)}${deepConfirmSummaryHtml(data, sym)}<div class="pk-pro" data-pro-slot></div>${sized.length ? '' : '<p class="pk-panel-empty">No suggested position in this scan, so there is nothing to size or execute.</p>'}`)}
    ${panel('method', `${pkExcludedHtml(data)}${pkScreenedHtml(data)}${data.scan_status === 'provider_rate_limited' || Number(data.provider_rate_limited_count || 0) > 0 ? '' : pkStatusLineHtml(data)}${pkMethodHtml(data)}${coverageLineHtml(data)}${dataDatesLineHtml(data)}${sharedResultsNoteHtml((data.recommendations || []).filter(isFundedPick))}${errorDetailsCardHtml(data)}`, ' portfolio-diagnostics')}`;
}

function renderPicksTableView(container, data) {
  const sym = data.currency || '$';
  // Only QUANTA's calls are picks; anything else is listed under Method.
  const rows = (data.stock_picks || []).filter(pkGroupOf);
  setPicksMeta('');
  const view = document.createElement('div');
  view.className = 'picks-stream picks-v2';
  view.setAttribute('data-picks-v2', '');
  view.pkData = data;
  container.innerHTML = '';
  container.appendChild(view);
  view.innerHTML = `
      ${data.scan_status === 'provider_rate_limited' || Number(data.provider_rate_limited_count || 0) > 0 ? pkStatusLineHtml(data) : ''}
      ${pkAlertHtml(data)}
      ${pkHeadHtml(data, rows, sym)}
      ${pkSectionsHtml(data, rows, sym)}`;
  wireErrorExport(container, data);
  wireSafeModeRetry(container);
  wirePicksTable(container, data);
}

/* Roving focus for a tablist: arrows, Home and End move and select. */
function pkTablistKeys(list, select) {
  list.addEventListener('keydown', (ev) => {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const vertical = list.getAttribute('aria-orientation') === 'vertical';
    const next = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: vertical ? 1 : 0, ArrowUp: vertical ? -1 : 0, Home: -i, End: tabs.length - 1 - i }[ev.key];
    if (!next) return;
    ev.preventDefault();
    const t = tabs[(i + next + tabs.length) % tabs.length];
    t.focus();
    select(t);
  });
}

function wirePicksTable(container, data) {
  const view = container.querySelector('[data-picks-v2]');
  if (!view || view.dataset.wired === '1') return;
  view.dataset.wired = '1';
  data = data || view.pkData || {};
  const table = container.querySelector('table.pk-table');
  const showTab = (key) => {
    view.querySelectorAll('.pk-tabs [role="tab"]').forEach((t) => {
      const on = t.dataset.pkTab === key;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    });
    view.querySelectorAll('[data-pk-panel]').forEach((p) => { p.hidden = p.dataset.pkPanel !== key; });
  };
  const showPick = (ticker, focus) => {
    const list = view.querySelector('.pk-alist');
    if (!list) return;
    list.querySelectorAll('[data-pk-pick]').forEach((b) => {
      if (b.dataset.pkPick === ticker) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    });
    view.querySelectorAll('.pk-acards [data-pk-card]').forEach((c) => { c.hidden = c.dataset.pkCard !== ticker; });
    const cmpPanel = view.querySelector('[data-pk-compare-panel]');
    if (cmpPanel) cmpPanel.hidden = true;
    const card = [...view.querySelectorAll('.pk-acards [data-pk-card]')].find((c) => c.dataset.pkCard === ticker);
    if (card && focus) {
      if (typeof card.scrollIntoView === 'function') card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      try { card.focus({ preventScroll: true }); } catch (_) { card.focus(); }
    }
  };
  const tabs = view.querySelector('.pk-tabs');
  if (tabs) {
    tabs.querySelectorAll('[role="tab"]').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.pkTab)));
    pkTablistKeys(tabs, (t) => showTab(t.dataset.pkTab));
  }
  const alist = view.querySelector('.pk-alist');
  if (alist) {
    alist.querySelectorAll('[data-pk-pick]').forEach((b) => b.addEventListener('click', () => showPick(b.dataset.pkPick, false)));
    const max = Number(thr('WHY.COMPARE_MAX')) || 3;
    const boxes = [...alist.querySelectorAll('[data-pk-cmp]')];
    const cmpBtn = alist.querySelector('[data-pk-compare]');
    const chosen = () => boxes.filter((b) => b.checked).map((b) => b.dataset.pkCmp);
    boxes.forEach((b) => b.addEventListener('change', () => {
      const n = chosen().length;
      if (cmpBtn) cmpBtn.disabled = n < 2 || n > max;
      boxes.forEach((x) => { x.disabled = !x.checked && n >= max; });
    }));
    cmpBtn?.addEventListener('click', () => {
      const panel = view.querySelector('[data-pk-compare-panel]');
      if (!panel) return;
      const sym = data.currency || '$';
      panel.innerHTML = `<div class="pk-cmp-head"><h5>Comparing ${escapeHtml(chosen().join(', '))}</h5><button type="button" class="btn-mini" data-pk-cmp-close>Close comparison</button></div>${pkCompareHtml((data && data.stock_picks) || [], chosen(), sym)}`;
      view.querySelectorAll('.pk-acards [data-pk-card]').forEach((c) => { c.hidden = true; });
      panel.hidden = false;
      panel.querySelector('[data-pk-cmp-close]').addEventListener('click', () => {
        const cur = alist.querySelector('[aria-current="true"]');
        showPick(cur ? cur.dataset.pkPick : chosen()[0], false);
      });
    });
  }
  const openAnalysis = (ticker) => { showTab('analysis'); showPick(ticker, true); };
  const rf = { call: 'all', sector: '', grade: '', score: null, rr: null };
  const applyRf = () => {
    const trs = [...view.querySelectorAll('tr.pk-row')];
    let shown = 0;
    trs.forEach((tr) => {
      const d = tr.dataset;
      const ok = (rf.call === 'all' || d.pkOutcome === rf.call) && (!rf.sector || d.pkSector === rf.sector)
        && (!rf.grade || (d.pkGrade && d.pkGrade <= rf.grade))
        && (rf.score == null || (d.pkScore !== '' && Number(d.pkScore) >= rf.score))
        && (rf.rr == null || (d.pkRr !== '' && Number(d.pkRr) >= rf.rr));
      tr.hidden = !ok;
      if (ok) shown += 1;
    });
    const active = rf.call !== 'all' || rf.sector || rf.grade || rf.score != null || rf.rr != null;
    const count = view.querySelector('[data-pk-rf-count]');
    if (count) count.textContent = active ? `Showing ${shown} of ${trs.length}` : '';
    const clear = view.querySelector('[data-pk-rf-clear]');
    if (clear) clear.hidden = !(rf.sector || rf.grade || rf.score != null || rf.rr != null);
    const none = view.querySelector('[data-pk-nomatch]');
    if (none) none.hidden = shown > 0;
  };
  view.querySelectorAll('[data-pk-filter]').forEach((chip) => {
    chip.addEventListener('click', () => {
      rf.call = chip.dataset.pkFilter;
      view.querySelectorAll('[data-pk-filter]').forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
      applyRf();
    });
  });
  view.querySelectorAll('[data-pk-rf]').forEach((el) => {
    const key = el.dataset.pkRf;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
      if (el.tagName === 'SELECT') rf[key] = el.value;
      else { const v = parseFloat(el.value); rf[key] = Number.isFinite(v) && v > 0 ? v : null; }
      applyRf();
    });
  });
  view.querySelector('[data-pk-rf-clear]')?.addEventListener('click', () => {
    view.querySelectorAll('[data-pk-rf]').forEach((el) => { el.value = ''; });
    Object.assign(rf, { sector: '', grade: '', score: null, rr: null });
    applyRf();
  });
  container.querySelectorAll('.pk-ticker, .pk-open').forEach((b) => {
    b.addEventListener('click', (ev) => { ev.stopPropagation(); if (typeof openTickerTearSheet === 'function') openTickerTearSheet(b.dataset.ticker); });
  });
  container.querySelectorAll('[data-pk-open]').forEach((b) => {
    b.addEventListener('click', (ev) => { ev.stopPropagation(); openAnalysis(b.dataset.pkOpen); });
  });
  container.querySelectorAll('tr.pk-row').forEach((tr) => {
    tr.addEventListener('click', (ev) => {
      if (ev.target.closest('button, a')) return;
      openAnalysis(tr.dataset.pkTicker);
    });
  });
  if (table) {
    table.querySelectorAll('.pk-sort').forEach((btn) => {
      btn.addEventListener('click', () => {
        const th = btn.closest('th');
        const key = btn.dataset.sort;
        const dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
        table.querySelectorAll('thead th[aria-sort]').forEach((x) => x.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort', dir);
        const rowsData = new Map(((data && data.stock_picks) || []).map((r) => [String(r.ticker), r]));
        const tbody = table.querySelector('tbody');
        const trs = [...tbody.querySelectorAll('tr.pk-row')];
        const val = (tr) => { const r = rowsData.get(tr.dataset.pkTicker); return r ? pkNum(PK_SORT[key](r)) : null; };
        trs.sort((a, b) => {
          const va = val(a); const vb = val(b);
          if (va === null && vb === null) return 0;
          if (va === null) return 1;
          if (vb === null) return -1;
          return dir === 'descending' ? vb - va : va - vb;
        });
        trs.forEach((row) => tbody.appendChild(row));
      });
    });
  }
  const rows = (data && data.stock_picks) || [];
  container.querySelectorAll('[data-pk-copy]').forEach((btn) => btn.addEventListener('click', async (ev) => {
    const text = pkCopyText(rows, btn.dataset.pkCopy);
    try { await navigator.clipboard.writeText(text); ev.target.textContent = 'Copied'; } catch (_) { ev.target.textContent = text; }
  }));
  container.querySelector('[data-pk-csv]')?.addEventListener('click', () => {
    const blob = new Blob([stockPicksCsv(rows)], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `quanta-stock-picks-${String(data.timestamp || '').slice(0, 10) || 'scan'}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 0);
  });
}

function renderPicks(data, { savedAt = null } = {}) {
  state.picks = data;
  state.picksSavedAt = savedAt;
  // A finished scan re-ranks Discover and the Overview summary at once.
  try {
    const intel = (state.dashboard && state.dashboard.market_intel) || {};
    renderTopOpportunities(intel);
    renderOpportunityBrief(intel);
  } catch (_) { /* summary panels are optional */ }
  const container = $('#picks-results');
  const picksList = (data && data.recommendations) || [];
  const chaseList = (data && data.chase_alerts) || [];
  const earlyList = (data && data.early_momentum) || [];

  // [SCAN-HEALTH] A failed scan must NOT be rendered as a benign no-picks state,
  // and must never surface (possibly stale) picks. Short-circuit here.
  // [PROVIDER-RESILIENCE] provider_rate_limited (no usable picks) is treated the
  // same way: show the throttle banner + safe-mode retry, never stale picks.
  if (data && (data.scan_status === 'failed' || data.scan_status === 'no_universe'
      || data.scan_status === 'provider_rate_limited')) {
    const msg = scanStatusMessage(data) || { title: 'Scan failed', body: '' };
    const isRl = data.scan_status === 'provider_rate_limited';
    setPicksMeta(isRl ? 'Scan interrupted — provider rate-limited'
      : (data.scan_status === 'no_universe' ? 'Scan failed — empty universe' : 'Scan failed'));
    const retryBtn = isRl
      ? '<button class="btn-mini" data-safe-mode-retry style="margin-top:12px;">Retry with safe mode</button>'
      : '';
    // [A11] Theme tokens (quanta.css .scan-fail), never fixed colours.
    const failHtml = `
      <div class="empty-state large scan-fail${isRl ? ' is-throttle' : ''}">
        <h3 data-result-heading tabindex="-1">${escapeHtml(msg.title)}</h3>
        <p>${escapeHtml(msg.body)}</p>
        ${retryBtn}
      </div>`;
    container.innerHTML = `<div class="picks-stream">${failHtml}${errorDetailsCardHtml(data)}</div>`;
    wireErrorExport(container, data);
    wireSafeModeRetry(container);
    // Deliberately do NOT inject the Pro picks layer here: a failed scan must
    // never surface (possibly stale) recommendations, watchlist exports, or
    // allocation tables. Only the failure banner + error log are shown.
    return;
  }

  // [Picks redesign] A result carrying the ranked table renders as one table.
  if (data && Array.isArray(data.stock_picks)) {
    renderPicksTableView(container, data);
    requestAnimationFrame(() => {
      if (window.QuantaPro && typeof window.QuantaPro.injectPicksExtras === 'function') {
        try { window.QuantaPro.injectPicksExtras(data); } catch (_) { /* non-fatal */ }
      }
      renderPortfolioScanPanel(state.dashboard?.market_intel || {});
      renderSetupStatusCard(state.dashboard?.market_intel || {});
    });
    return;
  }

  if (!picksList.length) {
    const scanned = data?.total_scanned || data?.universe_size || data?.scanned || 0;
    const scanType = $('#sp-scan')?.value || 'standard';
    // The header still read "Scanning…" from the moment the scan started.
    setPicksMeta(data?.scan_status === 'partial'
      ? `Partial scan · ${Number(data.analysis_failed_count || 0)} of ${scanned} names hit errors · no Buy or Accumulate call`
      : `Scan complete · ${scanned ? `${scanned} scanned · ` : ''}no Buy or Accumulate call`);
    const regime = (data && data.regime) || {};
    const sideHtml = (chaseList.length || earlyList.length)
      ? ` QUANTA still flagged <strong>${earlyList.length}</strong> early-momentum setup${earlyList.length === 1 ? '' : 's'} and <strong>${chaseList.length}</strong> chase alert${chaseList.length === 1 ? '' : 's'} — review them below.`
      : '';
    const regimeLine = regime.type
      ? `<p class="muted" style="margin-top:6px;font-size:12px;">Regime · <strong>${escapeHtml(titleLabel(regime.type))}</strong>${(regime.exposure_pct ?? regime.exposure) != null ? ` · Suggested exposure ${escapeHtml(String(regime.exposure_pct ?? regime.exposure))}%` : ''}${regime.vix != null ? ` · ${regime.vix_is_proxy ? 'VIX estimate (no live quote)' : 'VIX'} ${fmtNumber(regime.vix, 1)}` : ''}</p>`
      : '';
    // Surface rejected candidates (Deep Analysis vetoes) so users see WHY no picks
    const rejected = (data && data.deep_confirmation && Array.isArray(data.deep_confirmation.rejected))
      ? data.deep_confirmation.rejected
      : [];
    const rejectedHtml = rejected.length ? `
        <div class="rejected-block">
          <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:var(--text-primary);">Deep Analysis vetoes (${rejected.length})</p>
          <ul style="margin:0;padding-left:18px;font-size:12.5px;line-height:1.55;">
            ${rejected.slice(0, 8).map((r) => `<li><strong>${escapeHtml(r.ticker || '')}</strong> — ${escapeHtml(r.reason || 'Rejected by deep confirmation')}</li>`).join('')}
          </ul>
          <p class="muted" style="margin:8px 0 0;font-size:11.5px;">Additional risk checks withheld these scanner candidates. Data failures are unconfirmed, not bearish calls; inspect each reason before reconsidering.</p>
        </div>` : '';
    const verdictHtml = scanVerdictHtml(data);
    // With the reconciled accounting the verdict row and the stage table carry
    // the counts; the backend prose is kept for payloads that predate it.
    const explanationHtml = verdictHtml ? '' : emptyScanExplanationHtml(data);
    const emptyHtml = verdictHtml ? `
      <div class="empty-state large scan-empty">
        <h3 data-result-heading tabindex="-1">No positions sized · hold cash</h3>
        ${verdictHtml}
        ${coverageLineHtml(data)}
        ${dataDatesLineHtml(data)}
        <p>No scanned name passed every screening and risk rule for new money. This is the result for this universe, not a market-wide conclusion.${sideHtml}</p>
        ${regimeLine}
        ${rejectedHtml}
      </div>` : `
      <div class="empty-state large">
        <h3 data-result-heading tabindex="-1">No buy-qualified setups right now</h3>
        <p>QUANTA scanned <strong>${escapeHtml(String(scanned || '—'))}</strong> name${scanned === 1 ? '' : 's'} and none met the current screening and risk rules with your <strong>${escapeHtml(scanType)}</strong> parameters. Holding cash is the current result.${sideHtml}</p>
        <p style="margin-top:8px">Review the scan audit and data coverage before changing your mandate or screening rules.</p>
        ${regimeLine}
        ${explanationHtml}
        ${rejectedHtml}
      </div>`;

    // Even with zero picks, surface chase + early-momentum buckets if present
    const sideBuckets = renderPicksBuckets(chaseList, earlyList, (data && data.chase_filled) || []);
    // [SCAN-HEALTH] A partial scan (some tickers failed) gets a warning banner +
    // the error-details table so the user knows it was not a clean no-picks run.
    const partialBanner = scanStatusBannerHtml(data);
    const errorCard = errorDetailsCardHtml(data);
    container.innerHTML = `<div class="picks-stream">${partialBanner}${eventCheckBannerHtml(data)}${emptyHtml}${passedNotFundedHtml(data)}${eventHeldDiagnosticsHtml(data)}${closestToQualifyingHtml(data)}${eventRejectionsHtml(data)}${errorCard}${sideBuckets}</div>`;
    wireErrorExport(container, data);
    wireSafeModeRetry(container);
    // Empty-state funnel + analyzer-disagreement watchlist (Pro layer)
    if (window.QuantaPro && typeof window.QuantaPro.injectPicksExtras === 'function') {
      try { window.QuantaPro.injectPicksExtras(data); } catch (_) { /* non-fatal */ }
    }
    renderPortfolioScanPanel(state.dashboard?.market_intel || {});
    renderSetupStatusCard(state.dashboard?.market_intel || {});
    return;
  }

  const sym = data.currency || '$';
  const regime = data.regime || {};
  const picks = picksList;
  const chase = chaseList;
  const early = earlyList;

  // [SOURCE OF TRUTH] Split the reconciled picks. Only funded picks (fundable
  // final_action + positive post-confirmation allocation) drive portfolio
  // totals and the execution cards. Everything else is watchlist context.
  const fundedPicks = picks.filter(isFundedPick);
  const watchPicks = picks.filter((p) => !isFundedPick(p));

  // Portfolio aggregates — funded picks ONLY, using post-confirmation allocation.
  // Capital comes from the scan result itself (the figure the engine sized
  // against), not from whatever the form input says now.
  const totalDeployed = fundedPicks.reduce((acc, p) => acc + finalAllocation(p), 0);
  const totalCapital = Number(data.capital || $('#sp-capital')?.value || thr('CAPITAL.DEFAULT_US'));
  const mandate = data.risk_profile || {};
  // A position the mandate scaled below one whole share is not "trimmed" —
  // it is gone. Report the two outcomes separately so the count of trimmed
  // positions never quietly includes ones that left the portfolio.
  const mandateDropped = mandate.dropped || [];
  const mandateTrimmed = (mandate.adjusted || []).filter((a) => !a.dropped);
  const mandateCounts = [
    mandateTrimmed.length
      ? `${mandateTrimmed.length} position${mandateTrimmed.length === 1 ? '' : 's'} trimmed`
      : 'no positions trimmed',
    mandateDropped.length
      ? `${mandateDropped.length} sized out (${mandateDropped.map((d) => escapeHtml(String(d.ticker || ''))).join(', ')})`
      : '',
  ].filter(Boolean).join(' · ');
  const mandateLine = mandate.profile
    ? `<p class="portfolio-mandate"><strong>${escapeHtml(titleLabel(mandate.profile))} mandate</strong> · allocation multiplier ${fmtNumber(mandate.multiplier, 2)}× · ${mandateCounts}${data.max_positions ? ` · max ${Number(data.max_positions) || 0} positions` : ''}</p>`
    : '';
  const chaseTickers = new Set(chase.map((c) => String(c.ticker || '').toUpperCase()).filter(Boolean));
  const utilization = totalCapital ? (totalDeployed / totalCapital) * 100 : 0;
  const avgRR = fundedPicks.length ? fundedPicks.reduce((a, p) => a + Number(p.risk_reward_ratio || 0), 0) / fundedPicks.length : 0;
  const totalMaxLoss = fundedPicks.reduce((acc, p) => {
    const entry = Number(p.entry_price || p.current_price || 0);
    const stop = Number(p.stop_loss || 0);
    const shares = Number(p.shares || 0);
    if (!entry || !stop || !shares) return acc;
    return acc + Math.abs((entry - stop) * shares);
  }, 0);
  const sectors = aggregateSectors(fundedPicks);

  // With nothing funded there is no risk taken and no reward ratio to average.
  // Printing "-$0" and "0.00:1" states a measurement that was never made; an
  // em dash says the figure does not exist yet, which is the truth.
  const funded = fundedPicks.length > 0;
  const cashReserve = totalCapital - totalDeployed;
  const scanTime = data.timestamp && !Number.isNaN(Date.parse(data.timestamp))
    ? new Date(data.timestamp).toLocaleString() : 'time unavailable';
  const naCell = '<span class="pm-na">—</span>';
  const watchTail = watchPicks.length
    ? ` <span class="portfolio-title-tail">+ ${watchPicks.length} Watchlist</span>` : '';

  setPicksMeta(funded
    ? `${fundedPicks.length} proposed · ${fmtMoney(totalDeployed, sym, 0)} sized`
    : (watchPicks.length ? `No positions sized · ${watchPicks.length} Watchlist` : 'No positions sized'));

  const summaryHtml = `
    <section class="portfolio-summary">
      <header class="portfolio-header">
        <h2 class="portfolio-title" data-result-heading tabindex="-1">${funded ? `${fundedPicks.length} proposed position${fundedPicks.length === 1 ? '' : 's'}` : 'No eligible positions · hold cash'}</h2>
        <span class="portfolio-regime-badge">${escapeHtml(titleLabel(regime.type || 'Market'))} · ${escapeHtml(titleLabel(regime.volatility || 'Normal'))} vol</span>
      </header>
      <p class="portfolio-count">${fundedPicks.length} sized position${fundedPicks.length === 1 ? '' : 's'}${watchTail}</p>
      <p class="portfolio-count">${fmtMoney(totalDeployed, sym, 0)} proposed · ${fmtMoney(cashReserve, sym, 0)} cash held back</p>
      ${coverageLineHtml(data)}
      ${dataDatesLineHtml(data) || `<p class="qr-caveat muted">Scan completed ${escapeHtml(scanTime)}.</p>`}
      ${mandateLine}
      ${concentrationHtml(picks)}
      <div class="portfolio-metrics">
        <div class="pm-cell"><div class="pm-label">Capital</div><div class="pm-value">${fmtMoney(totalCapital, sym, 0)}</div></div>
        <div class="pm-cell"><div class="pm-label">Proposed</div><div class="pm-value ${funded ? 'gold' : 'dim'}">${fmtMoney(totalDeployed, sym, 0)}</div></div>
        <div class="pm-cell"><div class="pm-label">% capital</div><div class="pm-value">${fmtPct(utilization, 1, false)}</div></div>
        <div class="pm-cell"><div class="pm-label">Planned loss at stops</div><div class="pm-value ${totalMaxLoss > 0 ? 'bear' : 'dim'}">${fmtMoney(totalMaxLoss, sym, 0)}</div></div>
        <div class="pm-cell"><div class="pm-label">Avg net R:R</div><div class="pm-value ${avgRR > 0 ? 'bull' : 'dim'}">${avgRR > 0 ? `${fmtNumber(avgRR, 2)}:1` : naCell}</div></div>
        <div class="pm-cell"><div class="pm-label">Scanned</div><div class="pm-value dim">${escapeHtml(String((data.funnel && data.funnel.universe_size) || data.total_scanned || '')) || naCell}</div></div>
      </div>
      ${sectors.length ? `
        <div class="sector-strip">
          <span class="sector-strip-label">Sectors</span>
          ${sectors.map((s) => `<span class="sector-chip">${escapeHtml(s.sector)} <span class="pct">${s.pct.toFixed(0)}%</span></span>`).join('')}
        </div>` : ''}
    </section>
  `;

  const warningsHtml = deepConfirmWarningsHtml(data);
  const dcSummaryHtml = deepConfirmSummaryHtml(data, sym);
  const sectorPct = Object.fromEntries(sectors.map((s) => [s.sector, s.pct]));
  const cardCtx = { capital: totalCapital, sectorPct, chaseTickers };
  // [A12] Past UI.COMPACT_LIST_AFTER rows, each pick starts as one compact
  // row and mounts its full card when opened.
  const compact = picks.length > Number(thr('UI.COMPACT_LIST_AFTER') || Infinity);
  const ordered = [...fundedPicks, ...watchPicks];
  const rowFor = (p, idx) => (compact ? compactPickRow(p, sym, idx) : pickCard(p, sym, idx, cardCtx));
  const cardsHtml = fundedPicks.map((p, idx) => rowFor(p, idx)).join('');
  const watchHtml = watchPicks.length ? `
    <section class="watchlist-group">
      <header>
        <strong>Watchlist · Not Actionable (${watchPicks.length})</strong>
        <span class="muted">Scanner-flagged but reconciled to a non-funded action — no capital allocated.</span>
      </header>
      ${watchPicks.map((p, idx) => rowFor(p, fundedPicks.length + idx)).join('')}
    </section>` : '';

  const bucketsHtml = renderPicksBuckets(chase, early, data.chase_filled || []);
  // [P6] Chase alerts and early-momentum setups get their own collapsed
  // section with the counts in its label, not a place inside the audit.
  const sideCounts = [chase.length ? `Chase alerts (${chase.length})` : '',
    early.length ? `Early-momentum setups (${early.length})` : '',
    (data.chase_filled || []).length ? `Gaps filled (${data.chase_filled.length})` : ''].filter(Boolean).join(' · ');
  const sideHtml = bucketsHtml
    ? `<details class="side-buckets" data-side-buckets><summary>${escapeHtml(sideCounts)}</summary>${bucketsHtml}</details>` : '';

  // [SCAN-HEALTH] If some tickers failed (partial_with_picks), warn above the
  // portfolio and surface the error-details table below the picks.
  const partialBanner = scanStatusBannerHtml(data);
  const errorCard = errorDetailsCardHtml(data);
  container.innerHTML = `<div class="picks-stream">${partialBanner}${eventCheckBannerHtml(data)}${warningsHtml}${summaryHtml}${sharedResultsNoteHtml(picks)}${cardsHtml}${watchHtml}${passedNotFundedHtml(data)}${eventHeldDiagnosticsHtml(data)}${closestToQualifyingHtml(data)}${eventRejectionsHtml(data)}${sideHtml}<details class="portfolio-diagnostics"><summary>Scan method and risk-check audit</summary>${dcSummaryHtml}${errorCard}</details></div>`;
  wireErrorExport(container, data);
  wireSafeModeRetry(container);
  const wireTicker = (root) => $$('.pick-card .ticker-action', root).forEach((node) => {
    node.addEventListener('click', () => openTickerTearSheet(node.dataset.ticker));
  });
  wireTicker(container);
  $$('.pick-lazy', container).forEach((row) => {
    row.addEventListener('toggle', () => {
      if (!row.open || row.dataset.mounted) return;
      const p = ordered[Number(row.dataset.pickIndex)];
      if (!p) return;
      row.dataset.mounted = '1';
      row.insertAdjacentHTML('beforeend', pickCard(p, sym, Number(row.dataset.pickIndex), cardCtx));
      wireTicker(row);
    });
  });

  // Animate RR bars
  requestAnimationFrame(() => {
    $$('.rr-fill').forEach((el) => {
      const w = el.dataset.fill || '0';
      el.style.width = w + '%';
    });
    if (window.QuantaPro && typeof window.QuantaPro.injectPicksExtras === 'function') {
      try { window.QuantaPro.injectPicksExtras(data); } catch (_) { /* non-fatal */ }
    }
    renderPortfolioScanPanel(state.dashboard?.market_intel || {});
    renderSetupStatusCard(state.dashboard?.market_intel || {});
  });
}

/* ---------------------------------------------------------------------------
   QUICK-REFERENCE TABLE
   The terminal opens with a one-line-per-pick ranked table (#, TICKER, SECTOR,
   ACTION, ENTRY ZONE, STOP, TARGET, R:R, CONF, SETUP) that the web builder had
   no equivalent of — it jumped straight from the portfolio summary into full
   detail cards, so there was no way to compare picks side by side. Same
   columns, same ordering, same caveat line as `display_elite_minimal`.
   --------------------------------------------------------------------------- */

/* [F2] One R:R format everywhere: the configured decimals (UI.RR_DECIMALS). */
function fmtRR(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  return `${n.toFixed(thr('UI.RR_DECIMALS') ?? 2)}:1`;
}

/* [F2] The ONE level set the engine published for this pick (Research levels
   when the Research check ran, else the scanner's, labelled). The page never
   computes an entry zone of its own. */
function bindingLevels(rec) {
  const b = rec && rec.binding_levels;
  return b && Number(b.entry) > 0 ? b : null;
}

function entryZoneText(rec, sym) {
  const b = bindingLevels(rec);
  const entry = Number((b && b.entry) || rec.entry_price || rec.current_price || 0);
  if (!entry) return '--';
  const low = b ? Number(b.entry_zone_low) : NaN;
  if (!(low > 0) || low >= entry) return fmtPrice(entry, sym);
  return `${fmtPrice(low, sym)}–${fmtPrice(entry, sym)}`;
}

/* [F3] What drove the one risk grade: the factors that added points, as the
   engine measured them, and the window they were read on. */
function riskDriversLine(rec) {
  const d = (rec && rec.decision) || {};
  const drivers = Array.isArray(d.risk_grade_drivers) ? d.risk_grade_drivers
    : (Array.isArray(rec.risk_grade_drivers) ? rec.risk_grade_drivers : []);
  if (!drivers.length) return '';
  const grade = d.risk_grade || rec.risk_grade || '';
  const adding = drivers.filter((x) => Number(x.points) > 0)
    .map((x) => `${x.label} ${String(x.detail || '').split(' (')[0]} (+${Number(x.points)})`);
  const days = Number(d.risk_grade_lookback_days || rec.risk_grade_lookback_days || 0);
  const body = adding.length ? adding.join(' · ') : 'no factor adds risk points';
  // The grade reads base + drivers, capped: without the base the drivers alone
  // (say +3 +2 +2) look too few for an F (audit R1).
  const base = Number(thr('RISK_GRADE.BASELINE_POINTS'));
  const cap = Number(thr('RISK_GRADE.MAX_POINTS'));
  const raw = base + drivers.reduce((t, x) => t + (Number(x.points) || 0), 0);
  const total = Number.isFinite(raw) && Number.isFinite(cap)
    ? ` · ${base} base points, ${raw > cap ? `${raw} capped at ${cap}` : raw} of ${cap}` : '';
  return `<p class="qr-caveat" data-risk-drivers>Risk grade ${escapeHtml(grade)}: ${escapeHtml(body)}${escapeHtml(total)}${days ? ` · measured on ${days} sessions` : ''}</p>`;
}

/* [F1] Names the corporate-event gate rejected (a pending acquisition or a
   delisting), each with the reason built from its own SEC filing. The engine
   publishes them as result.rejected; without this list a deal target simply
   vanished from Picks instead of reading Avoid (audit U1). */
function eventRejectionsHtml(data) {
  const rows = (data && Array.isArray(data.rejected)) ? data.rejected : [];
  if (!rows.length) return '';
  const item = (r) => {
    const ev = r.event_check || {};
    const url = String(ev.url || '');
    const source = url.startsWith('https://www.sec.gov/')
      ? ` <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Filing on SEC EDGAR</a>` : '';
    const company = r.company && r.company !== r.ticker ? ` <span class="muted">${escapeHtml(r.company)}</span>` : '';
    return `<li class="event-reject" data-event-reject="${escapeHtml(r.ticker || '')}">
        <strong>${escapeHtml(r.ticker || '')}</strong>${company}
        <span class="signal-pill bear">${escapeHtml(titleLabel(r.final_action || 'AVOID'))}</span>
        <p>${escapeHtml(r.reason || r.code || '')}${source}</p>
      </li>`;
  };
  return `
    <section class="watchlist-group event-rejections" aria-labelledby="event-rejections-h">
      <header>
        <strong id="event-rejections-h">Avoid · pending acquisition or delisting (${rows.length})</strong>
        <span class="muted">Scanned names whose own SEC filings end the price thesis. Never funded.</span>
      </header>
      <ul class="event-reject-list">${rows.map(item).join('')}</ul>
    </section>`;
}

/* [F2] What clears the R:R gate that fired, as the engine wrote it from the
   binding levels (Research 1.50 vs scanner 1.80, with price triggers). */
function rrRemedy(rec) {
  const b = bindingLevels(rec);
  return (b && b.remedy) || '';
}

/* [A12] Sector concentration (2+ funded positions in one sector), shown once
   in the summary; it used to sit under the removed quick-reference table. */
function concentrationHtml(picks) {
  const counts = {};
  (picks || []).filter(isFundedPick).forEach((p) => {
    const s = p.sector || 'Unknown';
    counts[s] = (counts[s] || 0) + 1;
  });
  const concentrated = Object.keys(counts).filter((s) => counts[s] >= 2);
  return concentrated.length
    ? `<p class="qr-caveat" data-concentration>${warnIconSvg()} <strong>Concentration:</strong> ${escapeHtml(concentrated.join(', '))}: 2+ positions, correlation risk.</p>`
    : '';
}

/* [A07] What fired, measured on which bar. Older payloads carry only the
   reason sentence, so it is shown as written. */
function chaseTriggerText(c) {
  const move = Number(c.move_pct);
  const thr = Number(c.threshold_pct);
  const over = Number.isFinite(thr) ? ` (over ${fmtPct(thr, 0, false).replace('+', '')})` : '';
  // [I6] How much of a gap has been given back, and how far a run sits below its peak.
  const fill = Number(c.gap_fill_pct);
  const fade = Number(c.fade_since_gap_close_pct);
  const gapTail = Number.isFinite(fill) && fill > 0
    ? ` · partly filled ${Math.round(fill)}%${Number.isFinite(fade) ? `, ${fade > 0 ? '+' : ''}${fade.toFixed(1)}% since the gap-day close` : ''}` : '';
  const off = Number(c.off_peak_pct);
  const runTail = Number.isFinite(off) && off < 0
    ? ` · ${Math.abs(off).toFixed(1)}% below its ${c.peak_date ? `${c.peak_date} ` : ''}peak` : '';
  if (c.trigger === 'gap' && Number.isFinite(move)) return `Gap ${fmtPct(move, 1, false)} at the open${over}${gapTail}`;
  if (c.trigger === 'run_5d' && Number.isFinite(move)) return `5-day run ${fmtPct(move, 1, false)}${over}${runTail}`;
  if (c.trigger === 'run_10d' && Number.isFinite(move)) return `10-day run ${fmtPct(move, 1, false)}${over}${runTail}`;
  return String(c.reason || '');
}

/* [I6] Gaps that have filled: informational, apart from the active alerts. */
function chaseFilledHtml(filled) {
  if (!filled || !filled.length) return '';
  const items = filled.map((c) => `<li data-chase-filled-ticker="${escapeHtml(c.ticker)}"><strong>${escapeHtml(c.ticker)}</strong> <span class="muted">${escapeHtml(c.company || '')}</span> ${escapeHtml(c.reason || chaseTriggerText(c))}</li>`);
  return `<section class="watchlist-group chase-filled" data-chase-filled aria-labelledby="chase-filled-h">
      <header><strong id="chase-filled-h">Gap filled · informational, not chase alerts (${filled.length})</strong>
      <span class="muted">These gaps closed back at or below the pre-gap close, so they carry no chase penalty.</span></header>
      ${nameListHtml(items, 'closest-list')}
    </section>`;
}

function chaseBarsText(c) {
  if (!c.bar_date) return 'Not recorded';
  const span = c.from_date ? `${c.from_date} to ${c.bar_date}` : c.bar_date;
  return c.session_date && c.session_date !== c.bar_date ? `${span} · latest bar ${c.session_date}` : span;
}

function renderPicksBuckets(chase, early, filled) {
  chase = chase || [];
  early = early || [];
  const filledHtml = chaseFilledHtml(filled);
  const chaseHtml = chase.length ? `
    <section class="bucket-card chase" tabindex="0" role="region" aria-label="Chase alerts table">
      <header class="bucket-header">
        <div class="bucket-title">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 2L1 21h22L12 2zm0 6l7.53 13H4.47L12 8zm-1 4v4h2v-4h-2zm0 6v2h2v-2h-2z"/></svg>
          Chase alerts · recent gap or run-up
        </div>
        <span class="bucket-meta">${chase.length} flagged</span>
      </header>
      <p class="qr-caveat muted bucket-note">A gap or run-up above its configured threshold. Wait for a pullback before entry.</p>
      <div class="table-scroll" tabindex="0" role="region" aria-label="Chase alerts, scrollable">
      <table class="bucket-table">
        <thead><tr><th>Ticker</th><th>Company</th><th>Trigger</th><th>Bar date</th><th>From 20-day avg</th><th>1M</th><th>RSI</th></tr></thead>
        <tbody>
          ${chase.map((c) => `
            <tr data-chase="${escapeHtml(c.ticker)}">
              <td class="ticker">${escapeHtml(c.ticker)}</td>
              <td>${c.company ? escapeHtml(c.company) : '<span class="muted">Not recorded</span>'}</td>
              <td>${escapeHtml(chaseTriggerText(c))}</td>
              <td class="mono">${escapeHtml(chaseBarsText(c))}</td>
              <td>${c.distance_from_20dma == null ? '<span class="muted">Not recorded</span>' : fmtPct(c.distance_from_20dma)}</td>
              <td class="${Number(c.momentum_1m) >= 0 ? 'bull' : 'bear'}">${fmtPct(c.momentum_1m)}</td>
              <td>${fmtNumber(c.rsi, 1)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      </div>
    </section>` : '';

  const earlyHtml = early.length ? `
    <section class="bucket-card early" tabindex="0" role="region" aria-label="Early-momentum watchlist table">
      <header class="bucket-header">
        <div class="bucket-title">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M5 19h14v2H5v-2zM13 5.83l3.59 3.58L18 8l-6-6-6 6 1.41 1.41L11 5.83V17h2V5.83z"/></svg>
          Early-Momentum Watchlist
        </div>
        <span class="bucket-meta">${early.length} setups · not yet extended</span>
      </header>
      <table class="bucket-table">
        <thead><tr><th>Ticker</th><th>Sector</th><th>Score</th><th>EM</th><th>RSI</th><th>Vol×</th><th>Signals</th></tr></thead>
        <tbody>
          ${early.map((e) => `
            <tr>
              <td class="ticker">${escapeHtml(e.ticker)}</td>
              <td>${escapeHtml(e.sector || '--')}</td>
              <td class="bull">${fmtScore100(e.score)}</td>
              <td><span class="em-pill">${escapeHtml(String(e.em_score))}/6</span></td>
              <td>${fmtNumber(e.rsi, 1)}</td>
              <td>${fmtNumber(e.volume_ratio, 2)}×</td>
              <td style="white-space:normal;font-family:var(--font-sans);font-size:11.5px;color:var(--text-secondary);">${(e.signals || []).map((s) => escapeHtml(s)).join(' · ')}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </section>` : '';

  return `${earlyHtml}${chaseHtml}${filledHtml}`;
}

function pickCard(rec, sym, idx, ctx = {}) {
  // [SOURCE OF TRUTH] Drive the badge/colour/behaviour from the reconciled
  // final_action, never the raw scanner signal.
  const meta = actionMeta(rec);
  const sigKey = meta.key;
  const funded = isFundedPick(rec);
  const entry = firstUsable(rec.entry_price, rec.current_price);
  const target = firstUsable(rec.price_target, rec.target_price);
  const stop = firstUsable(rec.stop_loss);
  const upside = upsidePct(target, entry);
  const stopDown = upsidePct(stop, entry);
  const score = Number(rec.overall_score || 0);
  const conviction = Number(rec.conviction || Math.round(score / 2));
  const bind = bindingLevels(rec);
  const rr = Number((bind && bind.risk_reward) ?? rec.risk_reward_ratio ?? 0);
  // [F2] Say which level set decided, and keep the scanner's only as a reference.
  const ref = rec.scanner_levels;
  const scannerLevelsHtml = (bind && bind.source === 'research' && ref)
    ? `<p class="qr-caveat muted" data-scanner-levels>Scanner reference levels (not binding): entry ${fmtPrice(ref.entry, sym)} · stop ${fmtPrice(ref.stop, sym)} · target ${fmtPrice(ref.target, sym)} · R:R ${fmtRR(ref.risk_reward)}${basisText(ref.price_basis, sym)}</p>`
    : '';
  const dayChg = firstUsable(rec.day_change_pct, rec.change_pct);
  const allocAfter = finalAllocation(rec);
  const allocBefore = Number(rec.allocation_before != null ? rec.allocation_before : allocAfter);
  const downgraded = !!rec.downgrade_flag;
  const scannerAction = rec.scanner_action ? titleLabel(rec.scanner_action) : '';
  const deepAction = rec.deep_action ? titleLabel(rec.deep_action) : '';

  const setupTag = rec.setup_type ? `<span class="setup-tag">${escapeHtml(titleLabel(rec.setup_type))}</span>` : '';
  const emScore = Number(rec.early_momentum_score || 0);
  const emTag = emScore >= 3 ? `<span class="setup-tag" style="background:var(--info-dim);color:var(--info);border-color:rgba(56,189,248,0.3);">Early Mom ${emScore}/6</span>` : '';
  const insiderSent = (rec.insider_sentiment || '').toUpperCase();
  const narrative = rec.narrative || {};

  const insiderLine = rec.insider_note || (insiderSent && insiderSent !== 'UNKNOWN'
    ? `${titleLabel(insiderSent)} insider activity (${rec.insider_buys_90d || 0} buys / ${rec.insider_sells_90d || 0} sells, last 90d)`
    : '');
  const reasons = Array.isArray(rec.final_action_reasons) ? rec.final_action_reasons : [];
  const committeeNotes = decisionConsistentLines([
    ...(rec.investment_committee_notes || []),
    ...reasons,
  ].filter(Boolean), rec.decision).slice(0, 4);

  // Reconciliation notice: only when the deep analyzer changed the action.
  const deepModeNote = rec.deep_mode === 'fast+insider' ? ' (fast + insider checks)'
    : rec.deep_mode === 'fast' ? ' (fast checks)' : '';
  const downgradeNotice = downgraded ? `
    <div class="reconcile-notice">
      <strong>Additional risk-check downgrade${escapeHtml(deepModeNote)}:</strong> ${escapeHtml(scannerAction || 'Scanner')} → ${escapeHtml(meta.label)}.
      ${rec.deep_confirmation_reason ? escapeHtml(rec.deep_confirmation_reason) : ''}
    </div>` : '';

  const notConfirmedNotice = rec.not_independently_confirmed ? `
    <div class="reconcile-notice bear">
      <strong>Not independently confirmed.</strong> The deep analyzer could not validate this pick — treat with caution.
    </div>` : '';

  // The client mandate can scale an otherwise actionable pick below one whole
  // share. It then shows a buy action with $0 allocated, which needs saying
  // out loud rather than leaving the reader to infer it from the zero.
  const mandateUnfundedNotice = rec.mandate_unfunded ? `
    <div class="reconcile-notice">
      <strong>Sized out by the client mandate.</strong> ${escapeHtml(rec.mandate_unfunded_reason || 'The scaled allocation does not buy a whole share, so nothing is allocated.')}
    </div>` : '';

  // [WHY] One sentence per row: the backend's reconciled reason, else the
  // engine's last final-action reason, else the committee note.
  const whyText = sanitizeExceptionText(rec.why || rec.deep_confirmation_reason || reasons[reasons.length - 1] || (rec.investment_committee_notes || [])[0] || '');
  // Advice comes from the rule that blocked the pick (backend reason_codes), not
  // from words in its explanation.
  const reasonCodes = (rec.deep_confirmation && rec.deep_confirmation.reason_codes) || rec.reason_codes || [];
  const rrGate = reasonCodes.some((c) => ['LOW_REWARD_RISK', 'SCANNER_LOW_RR'].includes(String(c).toUpperCase()));
  const reviewCondition = (rrGate && rrRemedy(rec))
    ? [rrRemedy(rec), vetoRevalidation(reasonCodes.filter((c) => !['LOW_REWARD_RISK', 'SCANNER_LOW_RR', 'RESEARCH_DECISION_CAP'].includes(String(c).toUpperCase())), true)].filter(Boolean).join(' ')
    : reasonCodes.length
    ? vetoRevalidation(reasonCodes)
    // [I2] The decision names its first blocking rule and what clears it.
    : (rec.decision && rec.decision.primary_blocker && rec.decision.primary_blocker.remedy)
    ? `${rec.decision.primary_blocker.remedy} (rule ${rec.decision.primary_blocker.code}).`
    : (rec.not_independently_confirmed
      ? 'Retry after the data source recovers and additional risk checks complete.'
      : 'The blocking rule was not reported; rerun the Decision to see what changes it.');

  // [CHASE] Flag a name the scanner's own chase filter caught, or one that is
  // on the chase-alert list for this scan.
  const chaseHit = ctx.chaseTickers && ctx.chaseTickers.has(String(rec.ticker || '').toUpperCase());
  const chaseTag = (chaseHit || rec.is_chasing || rec.chase_risk) ? '<span class="setup-tag warn tag" data-chase-flag>Chase risk</span>' : '';

  // [PORTFOLIO IMPACT] What this position does to the book.
  const capital = Number(ctx.capital || 0);
  const pctOfCapital = capital > 0 ? (allocAfter / capital) * 100 : (rec.position_pct != null ? Number(rec.position_pct) : null);
  const sectorPct = ctx.sectorPct && rec.sector ? ctx.sectorPct[rec.sector] : null;
  const impactBits = [];
  if (funded && pctOfCapital != null && Number.isFinite(pctOfCapital)) impactBits.push(`<span><i>Of capital</i><b>${fmtPct(pctOfCapital, 1, false).replace('+', '')}</b></span>`);
  if (funded && sectorPct != null) impactBits.push(`<span><i>${escapeHtml(rec.sector || 'Sector')} share</i><b>${fmtPct(sectorPct, 0, false).replace('+', '')}</b> of deployed</span>`);
  if (funded && isPositive(entry) && isPositive(stop) && isPositive(rec.shares)) impactBits.push(`<span><i>Risk at stop</i><b>${fmtMoney(Math.abs((entry - stop) * Number(rec.shares)), sym, 0)}</b></span>`);
  if (rec.market_cap_tier) impactBits.push(`<span><i>Cap tier</i><b>${escapeHtml(titleLabel(rec.market_cap_tier))}</b></span>`);
  const impactLine = impactBits.length ? `<div class="pick-impact">${impactBits.join('')}</div>` : '';

  // [A12] One decision row per pick; every value appears once. The rest is
  // detail on demand.
  const horizonText = bind && Number(bind.horizon_days) > 0
    ? `${Number(bind.horizon_days)}-session horizon`
    : (rec.decision?.horizon?.label ? `horizon ${rec.decision.horizon.label}` : '');
  const levelsHead = [funded ? '' : 'Reference levels (watch only)', bind ? (bind.label || 'Levels') : 'Scanner levels', horizonText]
    .filter(Boolean).join(' · ');
  const noReward = (rec.trade_setup || {}).has_long_reward === false;
  const showLevels = !(meta.key === 'avoid' || meta.key === 'invalid') && isPositive(entry);
  const levelsCell = showLevels
    ? `Entry ${entryZoneText(rec, sym)} · Stop ${fmtPrice((bind && bind.stop) || stop, sym)} · Target ${fmtPrice((bind && bind.target) || target, sym)} · Net R:R ${noReward ? '0:1' : (rr > 0 ? fmtRR(rr) : 'not published')}`
    : 'No levels: not a candidate for new money';
  // [P5] The size is shares x the binding entry: say which price that is.
  const nShares = Number(rec.shares || 0);
  const sizedAt = isPositive(entry) ? ` at ${fmtPrice(entry, sym)}${bind ? ' (binding entry)' : ''}` : '';
  const allocText = funded
    ? `${fmtMoney(allocAfter, sym, 0)}${allocBefore > allocAfter ? ` <s class="pmt-sub">${fmtMoney(allocBefore, sym, 0)}</s>` : ''} · ${fmtNumber(nShares, 0)} share${nShares === 1 ? '' : 's'}${escapeHtml(sizedAt)}`
    : `${fmtMoney(0, sym, 0)} · no new capital`;
  // [P5] The spot price the binding levels were built on, with its session and source.
  const pb = bind && bind.price_basis;
  const provider = rec.decision?.sources?.find((x) => x.kind === 'price')?.provider;
  const spotLine = pb && isPositive(pb.price)
    ? `<span class="pd-spot muted" data-spot>Spot ${fmtPrice(pb.price, sym)}${pb.session ? ` · close of ${escapeHtml(pb.session)}` : ''}${provider ? ` · ${escapeHtml(provider)}` : ''}${pb.fetch ? ` · ${escapeHtml(pb.fetch)} fetch` : ''}</span>`
    : '';
  const evidenceText = rec.not_independently_confirmed ? 'Checks incomplete' : evidencePlain(rec.decision?.evidence);

  return `
    <article class="pick-card signal-${sigKey} ${funded ? 'pick-funded' : 'pick-watch'}" data-ticker="${escapeHtml(rec.ticker || '')}" style="animation-delay:${idx * 60}ms;">
      <div class="pick-head">
        <div class="pick-ident">
          <div class="pick-ticker-row">
            <button type="button" class="linklike ticker-action pick-ticker" data-ticker="${escapeHtml(rec.ticker || '')}" title="Open ${escapeHtml(rec.ticker || '')} tear sheet">${escapeHtml(rec.ticker || '?')}</button>
            <span class="pick-company">${escapeHtml(rec.company || '')}</span>
            <span class="pick-sector">${escapeHtml(rec.sector || '')}</span>
          </div>
          <div class="pick-tag-row">
            <span class="tag ${funded ? 'bull' : ''}" data-funded="${funded ? '1' : '0'}">${funded ? 'Proposed' : 'No allocation'}</span>
            ${chaseTag}
          </div>
        </div>
      </div>

      <dl class="pick-decision" data-decision-row>
        <div class="pd-action"><dt>Decision</dt><dd><span class="signal-pill ${sigKey} ${meta.tone}">${escapeHtml(meta.label)}</span></dd></div>
        <div class="pd-why"><dt>Why</dt><dd class="pick-why">${escapeHtml(whyText || 'Reason unavailable')}</dd></div>
        <div class="pd-levels"><dt data-levels-source>${escapeHtml(levelsHead)}</dt><dd class="mono">${levelsCell}${spotLine ? `<br>${spotLine}` : ''}</dd></div>
        <div class="pd-alloc"><dt>Allocation</dt><dd class="mono">${allocText}</dd></div>
        <div class="pd-evidence"><dt>Evidence</dt><dd>${escapeHtml(evidenceText)}</dd></div>
      </dl>
      ${mandateUnfundedNotice}
      ${funded ? '' : `<div class="watch-guidance ${meta.tone}"><strong>${meta.tone === 'warn' ? 'Watch only' : (meta.tone === 'bear' ? 'No entry' : 'Holders only')}</strong><span>${escapeHtml(reviewCondition)}</span></div>`}

      <details class="pick-details"><summary>Detail</summary>
      <div class="pick-detail-facts">
        <span><i>Last price</i><b>${fmtPrice(entry, sym)}</b>${dayChg != null ? ` <span class="${dayChg >= 0 ? 'bull' : 'bear'}">${fmtPct(dayChg, 2, false)}</span>` : ''}</span>
        <span><i>Score</i><b>${fmtScore100(score)}/100</b></span>
        <span title="Heuristic scanner setup strength; not a win probability"><i>Scanner strength</i><b>${conviction}/5</b></span>
        ${funded && showLevels ? `<span><i>Upside</i><b class="bull">${fmtPct(upside, 1, false)}</b></span><span><i>Stop distance</i><b class="bear">${fmtPct(stopDown, 1, false)}</b></span>` : ''}
        ${setupTag}${emTag}
        ${(deepAction && downgraded) ? `<span class="setup-tag tag">Deep: ${escapeHtml(deepAction)}</span>` : ''}
      </div>
      ${impactLine}
      ${riskDriversLine(rec)}
      ${rec.decision?.as_of ? `<p class="qr-caveat muted">Price session ${escapeHtml(rec.decision.as_of)} · source ${escapeHtml(rec.decision.sources?.find((s) => s.kind === 'price')?.provider || 'unknown')}${rec.decision.freshness?.status ? ` · ${escapeHtml(rec.decision.freshness.status)}` : ''}</p>` : ''}
      ${bind && (bind.target_basis || bind.rr_basis) ? `<p class="qr-caveat muted">${bind.target_basis ? `Target: ${escapeHtml(bind.target_basis)}` : ''}${bind.target_basis && bind.rr_basis ? ' · ' : ''}${bind.rr_basis ? `R:R ${escapeHtml(bind.rr_basis)}` : ''}</p>` : ''}
      ${scannerLevelsHtml}
      ${downgradeNotice}
      ${notConfirmedNotice}
      <div class="pick-body">
        ${narrative.headline || narrative.why_now ? `
          <div class="narrative-panel">
            <strong>Why now</strong>
            ${narrative.headline ? `<p style="margin-bottom:4px;color:var(--text-primary);">${escapeHtml(narrative.headline)}</p>` : ''}
            ${narrative.why_now ? `<p>${escapeHtml(narrative.why_now)}</p>` : ''}
          </div>` : ''}
        ${insiderLine ? `<div class="narrative-panel insider"><strong>Insider read</strong><p>${escapeHtml(insiderLine)}</p></div>` : ''}
        ${committeeNotes.length ? `
          <div class="narrative-panel committee">
            <strong>Model rationale</strong>
            <ul>${committeeNotes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>
          </div>` : ''}
        ${narrative.risk_factors ? `<div class="narrative-panel risk"><strong>Principal risk</strong><p>${escapeHtml(narrative.risk_factors)}</p></div>` : ''}
      </div>
      </details>
    </article>
  `;
}

/* ---------------------------------------------------------------------------
   DEEP ANALYSIS
   --------------------------------------------------------------------------- */
async function runAnalysis(event, opts) {
  if (event) event.preventDefault();
  const options = opts || {};
  const ticker = $('#an-ticker').value.trim().toUpperCase();
  // Validate before the request so the reason sits under the field, named.
  const tickerError = !ticker ? 'Enter a ticker symbol (a stock or an ETF).'
    : (!/^\^?[A-Z0-9][A-Z0-9.\-=&]{0,14}$/.test(ticker) ? `"${ticker}" is not a ticker symbol — use letters, digits and "." or "-" only; add .NS or .BO for an NSE or BSE listing.` : '');
  setFieldError('#an-ticker', '#an-ticker-error', tickerError);
  if (tickerError) { $('#an-ticker').focus(); return; }
  rememberResearch(ticker, options.market || state.market);

  const btn = $('#btn-analyze'); const prog = $('#an-progress'); const bar = $('#an-bar'); const status = $('#an-status'); const results = $('#an-results');
  const anToken = nextJobToken('analysis');
  const anCurrent = () => jobTokenCurrent('analysis', anToken);
  btn.disabled = true; prog.classList.add('visible'); prog.classList.remove('hidden');
  bar.style.width = '0%'; bar.style.background = '';
  // [PERF-FASTMODE] Fast Mode (from the form toggle or an explicit opts.fastMode)
  // skips the slow optional modules for a quick preliminary tear sheet.
  const fastEl = document.querySelector('#an-fast-mode');
  const fastMode = options.fastMode != null ? !!options.fastMode : !!(fastEl && fastEl.checked);
  const modeLabel = fastMode ? ' (fast preview)' : (options.safeMode ? ' (safe mode)' : (options.preferCache ? ' (cached)' : ''));
  status.textContent = `Starting analysis for ${ticker}${modeLabel}…`;
  results.innerHTML = `<div class="empty-state large"><h3>Analyzing ${escapeHtml(ticker)}${escapeHtml(modeLabel)}…</h3><p>${fastMode ? 'Fetching price, technicals and risk for a fast preview; optional analytics are skipped.' : 'QUANTA is assembling valuation, technicals, Monte Carlo, analyst consensus, insider activity, catalysts, and trade-plan risk.'}</p></div>`;

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST', headers: { ...QUANTA_POST_HEADERS, 'Content-Type': 'application/json' },
      // The market toggle routes a bare symbol (India: NSE, then BSE, then US).
      body: JSON.stringify({ ticker, safe_mode: !!options.safeMode, prefer_cache: !!options.preferCache, fast_mode: fastMode, market: options.market || state.market }),
    });
    const payload = await res.json();
    if (!anCurrent()) return;
    if (!res.ok) throw new Error(payload.error || 'Analysis failed to start');
    pollJob(payload.job_id, bar, status, (data) => {
      if (anCurrent()) renderAnalysis(data);
    }, btn, prog, results, anCurrent);
  } catch (err) {
    if (!anCurrent()) return;
    const msg = friendlyFetchError(err);
    status.textContent = 'Request failed';
    // A rejected symbol is a field error; anything else is a request error.
    if (/ticker|symbol/i.test(msg)) {
      setFieldError('#an-ticker', '#an-ticker-error', `${msg.charAt(0).toUpperCase()}${msg.slice(1)}. Check the symbol and try again.`);
      $('#an-ticker')?.focus();
      prog.classList.add('hidden');
    }
    results.innerHTML = `<div class="error-box" role="alert"><strong>Could not start the tear sheet.</strong> ${escapeHtml(msg)}. <button type="button" class="link-btn" data-retry-analysis>Retry</button></div>`;
    results.querySelector('[data-retry-analysis]')?.addEventListener('click', () => runAnalysis());
    btn.disabled = false;
  }
}

function signalTone(sig) {
  const k = signalKey(sig);
  if (['strong_buy', 'buy', 'accumulate'].includes(k)) return 'bull';
  if (['sell', 'strong_sell', 'reduce', 'avoid'].includes(k)) return 'bear';
  // hold / hold_existing / watchlist / invalid => neutral.
  // (INVALID never reaches here — renderAnalysis early-returns above.)
  return 'neutral';
}

// Display-friendly label for the engine's FinalAction (preferred) or legacy signal.
// Prefers the conviction-prefixed recommendation_label produced by the CLI
// when present so the UI shows "LOW-CONVICTION HOLD" instead of plain "Hold".
function recommendationLabel(data) {
  if (data && data.decision && data.decision.label) return data.decision.label;
  if (data && data.recommendation_label) return data.recommendation_label;
  const fa = data && data.final_action;
  if (fa) return titleLabel(fa);
  return titleLabel(data && data.signal ? data.signal : 'HOLD');
}

// Factor scores are on a 0-100 scale. The label and its reading share a line;
// the fill beneath is the same reading drawn, with no second colour.
function scoreBar(label, value) {
  const has = value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const n = has ? Number(value) : 0;
  const fill = Math.max(0, Math.min(100, n));
  return `
    <div class="score-row${has ? '' : ' is-na'}">
      <div class="label">${escapeHtml(label)}</div>
      <div class="score-value">${has ? fmtNumber(n, 1) : '--'}</div>
      <div class="score-track" role="img" aria-label="${escapeHtml(label)} ${has ? fmtNumber(n, 1) : 'not available'} of 100"><div class="score-fill" data-fill="${fill}" style="width:${fill}%"></div></div>
    </div>`;
}

// [DECISION-INTEGRITY] Legacy analyzer commentary sometimes names a verdict
// ("HOLD signal with 45% confidence") that the reconciled Decision overrode.
// Beside the Decision, such lines read as a second recommendation, so they are
// withheld from lists; wording that agrees with the Decision stays. Only
// upper-case verdict tokens count, so "buyers" or "on hold" never trigger it.
const VERDICT_TOKEN_RE = /\b(STRONG[ _-]BUY|STRONG[ _-]SELL|BUY|SELL|HOLD|ACCUMULATE|REDUCE|AVOID|WATCHLIST)\b/g;
function decisionConsistentLines(items, decision) {
  const list = (Array.isArray(items) ? items : []).filter(Boolean).map(String);
  if (!decision || !decision.action) return list;
  const own = String(decision.action).toUpperCase().replace(/[\s-]+/g, '_');
  const ownLabel = String(decision.label || '').toUpperCase();
  const agrees = (tok) => {
    const k = tok.replace(/[\s-]+/g, '_');
    return k === own || ownLabel.includes(tok) || (k === 'HOLD' && own === 'HOLD_EXISTING');
  };
  return list.filter((line) => (line.match(VERDICT_TOKEN_RE) || []).every(agrees));
}

// Emoji in engine strings are terminal decoration; the UI carries tone itself.
function stripEmoji(text) {
  return String(text == null ? '' : text)
    .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]/gu, '')
    .replace(/\s{2,}/g, ' ').trim();
}

function targetRow(label, price, base, sym, featured = false) {
  if (!price) return '';
  const pct = upsidePct(price, base);
  const maxRange = Math.max(Math.abs(pct || 0), 30);
  const fillPct = pct != null ? Math.min(100, (Math.abs(pct) / maxRange) * 100) : 0;
  return `
    <div class="target-row ${featured ? 'featured' : ''}">
      <span class="label">${escapeHtml(label)}</span>
      <div class="bar"><div class="bar-fill" style="width:${fillPct}%;"></div></div>
      <span class="price">${escapeHtml(fmtPrice(price, sym))}</span>
      <span class="pct ${pct >= 0 ? 'bull' : 'bear'}">${fmtPct(pct, 1, false)}</span>
    </div>`;
}

function analystVotes(mi) {
  const buys = Number(mi.buy_ratings || 0);
  const holds = Number(mi.hold_ratings || 0);
  const sells = Number(mi.sell_ratings || 0);
  const total = Math.max(1, buys + holds + sells);
  return `
    <div class="analyst-votes">
      <div class="vote-row buy"><span class="label">Buy</span><div class="vote-bar"><div class="vote-fill" style="width:${(buys / total) * 100}%"></div></div><span class="count">${buys}</span></div>
      <div class="vote-row hold"><span class="label">Hold</span><div class="vote-bar"><div class="vote-fill" style="width:${(holds / total) * 100}%"></div></div><span class="count">${holds}</span></div>
      <div class="vote-row sell"><span class="label">Sell</span><div class="vote-bar"><div class="vote-fill" style="width:${(sells / total) * 100}%"></div></div><span class="count">${sells}</span></div>
    </div>`;
}

function detailRow(k, v) {
  return `<div class="detail-row"><span class="k">${escapeHtml(k)}</span><span class="v">${v == null || v === '' ? '--' : escapeHtml(String(v))}</span></div>`;
}

function detailSection(title, rows) {
  return `
    <div class="detail-section">
      <h5>${escapeHtml(title)}</h5>
      <div class="detail-rows">${rows.map(([k, v]) => detailRow(k, v)).join('')}</div>
    </div>`;
}

/* ---------------------------------------------------------------------------
   CLI-PARITY PANELS — mirror the redesigned QUANTA Elite CLI report sections.
   Render Executive Summary, Beginner Guidance, Existing Holder Profit Plan /
   Action Plan, Candlestick Patterns, and Portfolio Outlook.
   --------------------------------------------------------------------------- */

function buyNowTone(v) {
  const k = String(v || '').toUpperCase();
  if (k === 'YES') return 'bull';
  if (k === 'NO') return 'bear';
  return 'neutral';
}

function timingTone(v) {
  const k = String(v || '').toUpperCase();
  if (k.includes('NOW')) return 'bull';
  if (k.includes('WAIT')) return 'neutral';
  return 'neutral';
}

function renderBeginnerGuidance(data, { sym, dqOverride, sharedRRLabel, dqGate, decisionLabel }) {
  const g = data.beginner_guidance;
  if (!g) return '';
  const gate = dqGate || {};
  // Engine DQ is a baseline; validation layer can escalate it via dqOverride.
  const engineDq = String(g.data_quality || 'PASS').toUpperCase();
  const dq = dqOverride ? dqMax(engineDq, dqOverride) : engineDq;
  const dqTone = dq === 'PASS' ? 'bull' : (dq === 'WARN' ? 'neutral' : 'bear');
  // If "Buy Now" requires confirmation, never claim Long-Term Accumulate.
  let longTf = g.long_tf;
  if (String(g.buy_now || '').toUpperCase() === 'ONLY AFTER CONFIRMATION'
      && String(longTf || '').toLowerCase() === 'accumulate') {
    longTf = 'Conditional Accumulate after confirmation';
  }
  const rrLabel = sharedRRLabel || g.rr_label || 'N/A';
  const warnings = (g.warnings || []).filter(Boolean);
  // When DQ fails, override new-buyer / existing-holder copy for consistency
  // with the hero decision card (requirement #2 & #7).
  const recommendationLabelCell = decisionLabel || gate.overrideRecoLabel || g.recommendation_label;
  const buyNowCell = gate.failed ? 'NO' : g.buy_now;
  const newBuyerCell = gate.newBuyerAction || g.new_buyer;
  const existingHolderCell = gate.existingHolderAction || g.existing_holder;
  const pullbackEntryText = isPositive(g.pullback_entry)
    ? (g.pullback_is_deep
        ? `Deep pullback only — ${fmtPrice(g.pullback_entry, sym)}${g.pullback_basis ? ` (${g.pullback_basis})` : ''}`
        : `${fmtPrice(g.pullback_entry, sym)}${g.pullback_basis ? ` (${g.pullback_basis})` : ''}`)
    : 'Not available';
  const longTermStop = isPositive(g.long_term_holder_stop) ? g.long_term_holder_stop : g.existing_holder_stop;
  const cells = [
    ['Recommendation', recommendationLabelCell],
    ['Buy Now', buyNowCell],
    ['Candlestick Timing', g.candlestick_timing],
    ['Entry Quality', g.entry_quality],
    ['Risk/Reward', rrLabel],
    ['Risk Grade', g.risk_grade],
    ['New Buyer', newBuyerCell],
    ['Existing Holder', existingHolderCell],
    ['Aggressive Buyer', g.aggressive_buyer],
    ['Short Term', g.short_tf],
    ['Mid Term', g.mid_tf],
    ['Long Term', longTf],
    ['Long-Term Holder Stop', isPositive(longTermStop) ? fmtPrice(longTermStop, sym) : 'Not available'],
    ['Tactical Stop (short-term)', isPositive(g.tactical_stop) ? `${fmtPrice(g.tactical_stop, sym)}${g.tactical_stop_basis ? ` (${g.tactical_stop_basis})` : ''}` : 'Not available'],
    ['Pullback Entry', pullbackEntryText],
    ['Pullback Stop', isPositive(g.pullback_stop) ? fmtPrice(g.pullback_stop, sym) : 'Not available'],
    ['Breakout Entry', isPositive(g.breakout_entry) ? fmtPrice(g.breakout_entry, sym) : 'Not available'],
    ['Volume Requirement', normalizeVolumeRequirement(g.volume_requirement)],
    ['Data Quality', dq],
  ];
  return `
    <article class="section-card beginner-card">
      <h3 class="section-kicker">Beginner guidance</h3>
      <div class="beginner-grid">
        ${cells.map(([k, v]) => `
          <div class="beginner-cell ${k === 'Data Quality' ? `tone-${dqTone}` : ''}">
            <div class="bg-label">${escapeHtml(k)}</div>
            <div class="bg-value">${escapeHtml(v != null && v !== '' ? String(v) : '--')}</div>
          </div>`).join('')}
      </div>
      ${g.beginner_action ? `<p class="beginner-action"><strong>Beginner Action:</strong> ${escapeHtml(g.beginner_action)}</p>` : ''}
      ${g.risk_conflict_warning ? `<p class="beginner-warnings tone-warn"><strong>${warnIconSvg()} Caution:</strong> ${escapeHtml(g.risk_conflict_warning)}</p>` : ''}
      ${warnings.length ? `<p class="beginner-warnings"><strong>Warnings:</strong> ${warnings.map(escapeHtml).join('; ')}</p>` : ''}
    </article>`;
}

// Format an R/R verdict + ratio (e.g. "Favorable · 3.20:1", "Unfavorable · 0.15:1").
// Returns null when any input is missing so the caller can render "N/A".
function rrVerdict(entry, target, stop) {
  const rr = computeRR(entry, target, stop);
  if (rr == null) return null;
  let label = 'Unfavorable';
  if (rr >= 2.0) label = 'Favorable';
  else if (rr >= 1.5) label = 'Acceptable';
  return { rr, label, text: `${label} \u00b7 ${fmtNumber(rr, 2)}:1` };
}

function renderActionOrHolderPlan(data, { sym, currentPrice, baseTgt, bearTgt, bullTgt, stopL, entryP, sharedRRLabel, railEntry = null, railStop = null, sizingHtml = '', held = false }) {
  const g = data.beginner_guidance || {};
  const trade = data.trade_setup || {};
  // "Buy now: NO" is timing. A funded Decision with weak timing is a scale-in
  // plan, not a holders-only plan.
  const funds = ['STRONG_BUY', 'BUY', 'ACCUMULATE'].includes(actionKey((data.decision || {}).action || data.final_action));
  const isHolderPlan = String(g.buy_now || '').toUpperCase() === 'NO' && !funds;
  const title = data.action_plan_title || (isHolderPlan ? 'Existing holder profit plan' : 'Action plan');
  const note = data.action_plan_note || '';

  // ---- Tiered stops ----
  const holderStop = isPositive(g.existing_holder_stop) ? Number(g.existing_holder_stop) : null;
  const pullbackStop = isPositive(g.pullback_stop) ? Number(g.pullback_stop) : null;
  const breakoutEntry = isPositive(g.breakout_entry) ? Number(g.breakout_entry) : null;
  const breakoutStop = isPositive(g.breakout_stop) ? Number(g.breakout_stop) : null;
  const primaryStop = isPositive(stopL) ? Number(stopL) : null;
  const pullbackEntry = isPositive(g.pullback_entry) ? Number(g.pullback_entry) : null;

  // Only levels the engine supplied are shown, and no "not supplied"
  // placeholders. The valuation target and the reward/risk live once, in the
  // Decision rail's key levels; the plan adds only the alternative entries
  // (with their distance from the price and their basis), the tiered stops and
  // the volume condition.
  const away = (lvl) => {
    const pct = isPositive(currentPrice) ? upsidePct(lvl, currentPrice) : null;
    return pct == null ? '' : `${fmtPct(pct, 1, false)} from current`;
  };
  const cell = (label, value, cls = '', sub = '') => `<div class="trade-cell"><div class="label">${escapeHtml(label)}</div><div class="value ${cls}">${value}</div>${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ''}</div>`;
  const cells = [
    entryP && !(isPositive(railEntry) && fmtPrice(railEntry, sym) === fmtPrice(entryP, sym)) ? cell('Entry zone', fmtPrice(entryP, sym)) : '',
    pullbackEntry ? cell('Pullback entry', fmtPrice(pullbackEntry, sym), '', [away(pullbackEntry), g.pullback_basis || ''].filter(Boolean).join(' · ')) : '',
    breakoutEntry ? cell('Breakout entry', fmtPrice(breakoutEntry, sym), '', away(breakoutEntry)) : '',
    primaryStop && isPositive(railStop) && fmtPrice(railStop, sym) === fmtPrice(primaryStop, sym) ? '' : cell('Trade stop', primaryStop ? fmtPrice(primaryStop, sym) : '<span class="warn">Not published</span>', primaryStop ? 'bear' : ''),
    holderStop && holderStop !== primaryStop ? cell('Existing-holder stop', fmtPrice(holderStop, sym), 'bear') : '',
    pullbackStop ? cell('Pullback stop', fmtPrice(pullbackStop, sym), 'bear') : '',
    breakoutStop ? cell('Breakout stop', fmtPrice(breakoutStop, sym), 'bear') : '',
    g.volume_requirement ? cell('Volume required', escapeHtml(normalizeVolumeRequirement(g.volume_requirement)), '', breakoutEntry ? 'on the breakout day' : '') : '',
  ].filter(Boolean).join('');
  const entries = pullbackEntry || breakoutEntry;

  // A holders' plan matters to someone who holds the name. For a reader who
  // holds none, and a call that funds nothing, it is a closed disclosure; a
  // withheld size always stays in view.
  if (isHolderPlan && !held && !/data-sizing-withheld/.test(sizingHtml)) {
    const count = (cells.match(/class="trade-cell"/g) || []).length;
    return `
    <details class="detail-card ts-plan ts-plan--closed" data-plan-closed>
      <summary>${escapeHtml(sentenceLabel(title))} <span class="section-kicker-tail">you hold none${count ? `; ${count} level${count === 1 ? '' : 's'}${entries ? ', hitting one re-runs the Decision' : ''}` : ''}</span></summary>
      <div class="detail-body">
        ${note ? `<p class="plan-note">${escapeHtml(note)}</p>` : ''}
        ${cells ? `<div class="trade-grid">${cells}</div>` : ''}
        ${sizingHtml}
      </div>
    </details>`;
  }
  return `
    <article class="section-card ts-plan">
      <div class="section-kicker">${escapeHtml(sentenceLabel(title))}${entries ? ` <span class="section-kicker-tail">${isHolderPlan && held ? 'levels for adding to your position; the position’s stop is the Decision stop above' : 'hitting a level re-runs the Decision'}</span>` : ''}</div>
      ${note ? `<p class="plan-note">${escapeHtml(note)}</p>` : ''}
      ${cells ? `<div class="trade-grid">${cells}</div>` : ''}
      ${sizingHtml}
    </article>`;
}

function renderCandlestickPanel(data, { compositeScore } = {}) {
  const t = data.technical || {};
  const patterns = t.candlestick_patterns || [];
  if (!patterns.length && !t.candlestick_bias) return '';
  const bias = t.candlestick_bias || 'NEUTRAL';
  const biasTone = bias === 'BULLISH' ? 'bull' : (bias === 'BEARISH' ? 'bear' : 'neutral');
  const score = t.candlestick_score != null ? `${fmtNumber(t.candlestick_score, 0)}/100` : '--';
  const prior = t.prior_trend || 'SIDEWAYS';
  // Conflict explainer when candlestick disagrees with the broader technical stack.
  const cs = Number(compositeScore);
  const conflict = bias === 'BULLISH' && Number.isFinite(cs) && cs < 50;
  const conflictNote = conflict ? `
      <p class="plan-note warn candle-conflict">
        <strong>Note:</strong> Candlestick shows a bullish reversal attempt, but the overall technical stack is still weak (composite ${fmtNumber(cs, 1)}/100). Action: wait for confirmation before entering.
      </p>` : '';
  const rows = patterns.map((p) => `
    <tr>
      <td>${escapeHtml(p.name || '--')}</td>
      <td><span class="tone-${(p.bias || '').toLowerCase() === 'bullish' ? 'bull' : (p.bias || '').toLowerCase() === 'bearish' ? 'bear' : 'neutral'}">${escapeHtml(p.bias || '--')}</span></td>
      <td>${escapeHtml(p.type || '--')}</td>
      <td>${escapeHtml(p.reliability || '--')}</td>
      <td>${escapeHtml(p.context || '--')}</td>
    </tr>`).join('');
  return `
    <article class="section-card">
      <h3 class="section-kicker">Candlestick patterns</h3>
      <div class="candle-meta">
        <span class="chip tone-${biasTone}">Bias: ${escapeHtml(bias)}</span>
        <span class="chip">Score: ${score}</span>
        <span class="chip">Prior trend: ${escapeHtml(prior)}</span>
      </div>
      ${patterns.length ? `
        <div class="candle-table-wrap table-scroll" tabindex="0" role="region" aria-label="Candlestick patterns table">
          <table class="candle-table">
            <thead><tr><th>Pattern</th><th>Bias</th><th>Type</th><th>Reliability</th><th>Context</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>` : '<p class="muted">No defined candlestick pattern detected on the latest bars.</p>'}
      ${conflictNote}
    </article>`;
}

function renderPortfolioOutlook(data, decision = null) {
  const po = data.portfolio_outlook;
  if (!po) return '';
  const reco = (decision && decision.label) || po.recommendation || '--';
  return `
    <article class="section-card portfolio-outlook">
      <h3 class="section-kicker">Portfolio outlook</h3>
      <div class="po-reco"><span class="po-label">Decision</span><span class="po-value">${escapeHtml(reco)}</span></div>
      ${po.bottom_line ? `<p class="po-line"><strong>Bottom line:</strong> ${escapeHtml(po.bottom_line)}</p>` : ''}
      ${po.reason ? `<p class="po-line"><strong>Reason:</strong> ${escapeHtml(po.reason)}</p>` : ''}
    </article>`;
}

// Span actually covered by the returned bars, e.g. "6-month" / "6M". The
// request period and the bars kept can differ, so the title reads the dates.
function chartPeriodLabel(startDate, endDate) {
  const a = Date.parse(startDate); const b = Date.parse(endDate);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return null;
  const days = Math.round((b - a) / 86400000);
  const months = days / 30.44;
  if (months < 1.5) return { long: `${days}-day`, short: `${days}D` };
  if (months >= 23) { const y = Math.round(months / 12); return { long: `${y}-year`, short: `${y}Y` }; }
  if (months >= 11.5) return { long: '1-year', short: '1Y' };
  const m = Math.max(2, Math.round(months));
  return { long: `${m}-month`, short: `${m}M` };
}

function renderPriceChartCard(chart, { ticker, currentPrice, target, stop, sym, vbWidth = 1200, vbHeight = 380, targetLabel = 'Target' }) {
  // A missing close is a gap, not a price of zero: Number(null) === 0 would pass
  // a finite check and draw a crash to nothing. Keep every bar's slot on the
  // x axis, but only plot bars that carry a real price.
  const allBars = Array.isArray(chart?.points) ? chart.points : [];
  const bars = allBars.map((pt, slot) => ({ slot, date: (pt && pt.date) || '', close: pt ? numOrNull(pt.close) : null }));
  const points = bars.filter((bar) => bar.close != null);
  if (points.length < 2) {
    return `
      <article class="section-card chart-card">
        <h3 class="section-kicker">Price chart</h3>
        <div class="empty-state">Price chart unavailable for ${escapeHtml(ticker || 'this ticker')} right now.</div>
      </article>`;
  }

  // The viewBox matches the width the chart is drawn at, so its 12px labels
  // render near 12px: 1200 for a full row, narrower for the desk's 2:1 tile.
  const width = vbWidth;
  const height = vbHeight;
  const padL = 16;
  const padR = 84;        // room for the price axis
  const padY = 22;
  const padB = 30;        // room for the date axis
  const closes = points.map((point) => point.close);
  const validLevel = (value) => Number.isFinite(Number(value)) && Number(value) > 0;
  // Scale the vertical axis to the PRICE series (plus the current price, which is
  // the latest close). Target/stop are drawn as clamped boundary markers rather
  // than autoscale inputs, so a far-away target no longer compresses the price
  // line into the lower sliver of the card (removes the dead space above it).
  const scaleValues = closes.slice();
  if (validLevel(currentPrice)) scaleValues.push(Number(currentPrice));
  const rawMin = Math.min(...scaleValues);
  const rawMax = Math.max(...scaleValues);
  const headroom = (rawMax - rawMin || Math.abs(rawMax) || 1) * 0.08;
  const min = rawMin - headroom;
  const max = rawMax + headroom;
  const span = max === min ? 1 : max - min;
  const plotW = width - padL - padR;
  const plotH = height - padY - padB;
  const lastSlot = Math.max(1, allBars.length - 1);
  const x = (slot) => padL + (slot / lastSlot) * plotW;
  const y = (value) => padY + ((max - value) / span) * plotH;
  const clampY = (value) => Math.max(padY + 1, Math.min(padY + plotH - 1, y(value)));
  // Contiguous runs of real prices; a missing bar splits the line.
  const runs = [];
  points.forEach((point, i) => {
    if (i > 0 && point.slot === points[i - 1].slot + 1) runs[runs.length - 1].push(point);
    else runs.push([point]);
  });
  const runPts = (run) => run.map((point) => `${x(point.slot).toFixed(1)},${y(point.close).toFixed(1)}`).join(' ');
  const hasGap = runs.length > 1;
  const first = closes[0];
  const last = closes[closes.length - 1];
  const change = first ? ((last - first) / first) * 100 : null;
  const dir = last >= first ? 'bull' : 'bear';
  const startDate = points[0]?.date || '';
  const endDate = points[points.length - 1]?.date || '';
  const period = chartPeriodLabel(startDate, endDate);
  const gid = `chart-fill-${String(ticker || 'ticker').replace(/[^A-Za-z0-9_-]/g, '')}`;
  // Series for the hover/keyboard readout: date, close and plot position.
  const series = points.map((pt) => [pt.date || '', pt.close, +x(pt.slot).toFixed(1), +y(pt.close).toFixed(1)]);
  const hiIdx = closes.indexOf(Math.max(...closes));
  const loIdx = closes.indexOf(Math.min(...closes));
  const summary = `${ticker || 'Ticker'} price, ${startDate} to ${endDate}: ${fmtPct(change, 1, false)}. `
    + `High ${fmtPrice(closes[hiIdx], sym)} on ${points[hiIdx]?.date || ''}, low ${fmtPrice(closes[loIdx], sym)} on ${points[loIdx]?.date || ''}. `
    + 'Use the left and right arrow keys to read individual closes.';
  // Four gridlines on round-ish prices, labelled on the right axis.
  const grid = [0, 1, 2, 3].map((i) => {
    const v = min + (span * (i + 0.5)) / 4;
    const gy = y(v).toFixed(1);
    return `<line class="chart-grid" x1="${padL}" x2="${padL + plotW}" y1="${gy}" y2="${gy}"></line>
      <text class="chart-axis" x="${width - padR + 8}" y="${(Number(gy) + 4).toFixed(1)}">${escapeHtml(fmtPrice(v, sym, v >= 1000 ? 0 : 2))}</text>`;
  }).join('');
  const midPt = points[Math.floor((points.length - 1) / 2)];
  const dateTicks = [[points[0].slot, startDate, 'start'], [midPt.slot, midPt.date || '', 'middle'], [points[points.length - 1].slot, endDate, 'end']]
    .map(([slot, d, anchor]) => `<text class="chart-axis chart-date" x="${x(slot).toFixed(1)}" y="${height - 8}" text-anchor="${anchor}">${escapeHtml(d)}</text>`).join('');
  // Level labels are laid out together: a target pinned off-scale at the top
  // edge and "Now" just under it used to print on top of each other.
  const levelSpecs = [[targetLabel, target, 'target'], ['Stop', stop, 'stop'], ['Now', currentPrice, 'now']]
    .filter(([, v]) => validLevel(v))
    .map(([label, value, cls]) => {
      const ly = clampY(Number(value));
      return { label, value, cls, ly, ty: ly <= padY + 14 ? ly + 14 : ly - 6 };
    })
    .sort((a, b) => a.ty - b.ty);
  for (let i = 1; i < levelSpecs.length; i += 1) {
    if (levelSpecs[i].ty - levelSpecs[i - 1].ty < 18) levelSpecs[i].ty = levelSpecs[i - 1].ty + 18;
  }
  const levelLine = (label, value, cls) => {
    if (!validLevel(value)) return '';
    const spec = levelSpecs.find((l) => l.label === label) || {};
    const ly = spec.ly != null ? spec.ly : clampY(Number(value));
    const ty = spec.ty != null ? spec.ty : (ly <= padY + 14 ? ly + 14 : ly - 6);
    // A level outside the plotted price range is pinned to the nearest edge; say
    // so, or its position reads as a real price on the axis.
    const off = Number(value) > max ? 'above' : (Number(value) < min ? 'below' : '');
    const offMark = off ? ` ${off === 'above' ? '▲' : '▼'} off scale` : '';
    return `
    <g class="chart-level ${cls}${off ? ' off-scale' : ''}">
      <line x1="${padL}" x2="${padL + plotW}" y1="${ly.toFixed(1)}" y2="${ly.toFixed(1)}"${off ? ' stroke-dasharray="2 5"' : ''}></line>
      <text x="${padL + 6}" y="${ty.toFixed(1)}">${escapeHtml(label)} ${escapeHtml(fmtCurrency(Number(value), sym))}${off ? `<tspan class="chart-offscale">${offMark}</tspan>` : ''}</text>
    </g>`;
  };

  return `
    <article class="section-card chart-card">
      <h3 class="section-kicker">Price chart</h3>
      <div class="chart-header">
        <div>
          <h4>${escapeHtml(ticker || '')} · ${escapeHtml(period ? `${period.long} price action` : 'Price action')}</h4>
          <span>${escapeHtml(startDate)} to ${escapeHtml(endDate)}</span>
        </div>
        <div class="chart-return ${directionClass(change)}">
          <small class="chart-return-cap">Latest${period ? ` · ${escapeHtml(period.short)}` : ''}</small>
          <strong>${fmtCurrency(last, sym)}</strong>
          <span>${fmtPct(change, 1, false)}</span>
        </div>
      </div>
      <div class="chart-wrap">
      <svg class="price-chart" viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${escapeHtml(summary)}"
           data-series='${escapeHtml(JSON.stringify(series))}' data-sym="${escapeHtml(sym)}" data-vw="${width}" data-plot-top="${padY}" data-plot-bottom="${padY + plotH}">
        <defs>
          <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" class="chart-stop-top ${dir}"></stop>
            <stop offset="100%" class="chart-stop-bottom ${dir}"></stop>
          </linearGradient>
        </defs>
        ${grid}
        ${runs.filter((run) => run.length > 1).map((run) => `<path class="chart-area" fill="url(#${gid})" d="M ${runPts(run)} L ${x(run[run.length - 1].slot).toFixed(1)},${padY + plotH} L ${x(run[0].slot).toFixed(1)},${padY + plotH} Z"></path>`).join('')}
        ${runs.filter((run) => run.length > 1).map((run) => `<polyline class="chart-line ${dir}" points="${runPts(run)}"></polyline>`).join('')}
        ${levelLine(targetLabel, target, 'target')}
        ${levelLine('Stop', stop, 'stop')}
        ${levelLine('Now', currentPrice, 'now')}
        <circle class="chart-last ${dir}" cx="${x(points[points.length - 1].slot).toFixed(1)}" cy="${y(last).toFixed(1)}" r="3.5"></circle>
        ${dateTicks}
        <g class="chart-cross" visibility="hidden" aria-hidden="true">
          <line class="chart-cross-line" x1="0" x2="0" y1="${padY}" y2="${padY + plotH}"></line>
          <circle class="chart-cross-dot ${dir}" cx="0" cy="0" r="4.5"></circle>
        </g>
      </svg>
      <div class="chart-tip" hidden></div>
      <p class="sr-only chart-live" aria-live="polite"></p>
      </div>
      <div class="chart-legend">
        ${validLevel(target) ? `<span>Target ${fmtPrice(target, sym)}</span>` : ''}
        ${validLevel(stop) ? `<span>Stop ${fmtPrice(stop, sym)}</span>` : ''}
        ${validLevel(currentPrice) ? `<span>Now ${fmtPrice(currentPrice, sym)}</span>` : ''}
        ${hasGap ? '<span>Gaps: no close reported</span>' : ''}
      </div>
    </article>`;
}

// [ASSET-TYPE] Pure helper — structural classification + display chips +
// caveat text + per-section applicability for the deep-dive tear sheet.
// Exported for unit tests; must stay side-effect free.
function assetTypeMeta(data) {
  data = data || {};
  const qt = String(data.quote_type || '').toUpperCase();
  const isEtf = !!data.is_etf || ['ETF', 'MUTUALFUND', 'INDEX'].includes(qt);
  const assetType = String(
    data.asset_type || (isEtf ? 'etf' : (data.is_invalid ? 'invalid' : 'common_stock'))
  ).toLowerCase();
  const isAdr = !!data.is_adr || assetType === 'adr';
  const isFinancial = !!data.is_financial || assetType === 'financial';
  const isHighVol = !!data.is_high_volatility;
  const LABELS = {
    common_stock: 'Common Stock', etf: 'ETF / Fund', adr: 'ADR',
    financial: 'Financial', high_volatility: 'High Volatility', invalid: 'Invalid',
  };
  const primaryLabel = LABELS[assetType] || 'Common Stock';
  const chips = [{ label: primaryLabel, kind: 'asset' }];
  if (isAdr && assetType !== 'adr') chips.push({ label: 'ADR', kind: 'adr' });
  if (isFinancial && assetType !== 'financial') chips.push({ label: 'Financial', kind: 'financial' });
  if (isHighVol) chips.push({ label: 'High Volatility', kind: 'highvol' });

  let caveat = '';
  if (isEtf) {
    caveat = 'Single-company sections (insider activity, company DCF, '
      + 'Piotroski / Altman, single-name thesis) are not applicable and are marked N/A.';
  } else if (isAdr) {
    caveat = 'Foreign issuer / ADR' + (data.country ? ' (' + data.country + ')' : '')
      + '; fundamentals are filed under home-country standards; insider-trading disclosure, '
      + 'reporting cadence, and dividend tax treatment can differ from US domestics.';
  } else if (isFinancial) {
    caveat = 'Financial-sector issuer, so generic multiples (EV/EBITDA, FCF yield) are less '
      + 'meaningful for banks / insurers; weight book value, net interest margin, and capital ratios.';
  }
  return { assetType, isEtf, isAdr, isFinancial, isHighVol, primaryLabel, chips, caveat };
}

// [ASSET-TYPE] Which single-company sections are N/A for this instrument.
function assetApplicability(data) {
  const m = assetTypeMeta(data);
  const na = m.isEtf ? 'na_etf' : 'ok';
  return {
    insider: na,
    dcf: na,
    piotroski: na,
    altman: na,
    companyThesis: na,
    valuationCaveat: m.isFinancial,
    adrCaveat: m.isAdr,
  };
}

function assetCaveatBannerHtml(data) {
  const m = assetTypeMeta(data);
  if (!m.caveat) return '';
  const tone = m.isEtf || m.isAdr ? 'info' : 'warn';
  return `<div class="plan-note ${tone}" role="note">`
    + `<strong>${escapeHtml(m.primaryLabel)}:</strong> ${escapeHtml(m.caveat)}</div>`;
}

// [DQ-GATE] Centralised data-quality / low-confidence gate. Pure + testable.
// Decides how the tear sheet must de-emphasise an unreliable analysis so a
// beginner cannot misread a failed/low-confidence report as a buy setup.
// Does NOT change any trading logic, scoring, or thresholds — display only.
function dataQualityGate({ dataQualityFailed, confidenceScore, confidenceLevel, riskGrade } = {}) {
  const failed = !!dataQualityFailed;
  const conf = Number(confidenceScore);
  const lowConfidence = Number.isFinite(conf)
    ? conf < 50
    : /low/i.test(String(confidenceLevel || ''));
  const provisionalHold = failed && lowConfidence;
  const grade = String(riskGrade || '').trim().toUpperCase();
  const gradePhrase = grade ? `Risk Grade ${grade}` : 'risk guardrails';
  const whyReasons = [];
  if (failed) whyReasons.push('data quality failed');
  if (lowConfidence) whyReasons.push('low confidence');
  if (grade === 'D' || grade === 'F') whyReasons.push('high risk');
  // [HOLD-INFLATION-FIX] More precise label than the previous generic
  // "PROVISIONAL HOLD — DO NOT BUY NOW". When the only problem is data
  // quality (critical), the right verdict is NOT_CONFIRMED — a data issue,
  // not a bearish recommendation.
  let overrideRecoLabel = null;
  if (provisionalHold) overrideRecoLabel = 'NOT CONFIRMED: DATA QUALITY ISSUE';
  else if (failed) overrideRecoLabel = 'NOT CONFIRMED: DATA QUALITY ISSUE';
  return {
    failed,
    lowConfidence,
    provisionalHold,
    riskGrade: grade,
    overrideRecoLabel,
    buyNowAnswer: failed ? 'NO' : null,
    holdAnswer: failed ? 'Only with a strict stop-loss' : null,
    whyReasons,
    newBuyerAction: failed ? 'Avoid new entry until data quality passes.' : null,
    existingHolderAction: failed ? 'Hold only with strict stop-loss.' : null,
    rejectionThesis: failed
      ? `Critical data missing or invalid — recommendation NOT confirmed. This is a data issue, not a bearish signal. ${gradePhrase} adds caution.`
      : null,
    metricsTone: failed ? 'muted' : null,
  };
}

// [DQ-GATE] Prominent fail banner shown above the hero when DQ=FAIL.
function dataQualityFailBannerHtml(gate) {
  if (!gate || !gate.failed) return '';
  // The shared notice: one line, the reason behind "Why".
  return `
    <div class="q-notice dq-fail-banner" role="alert">${noticeHtml({
    text: 'Data quality failed: do not use this analysis for new trades', moreLabel: 'Why',
    more: 'Targets, upside and the composite score below are preliminary and have not passed validation. Treat them as unverified, not as a trade signal.',
  })}</div>`;
}

// [HOLD-INFLATION-FIX] Render Critical-vs-Optional split + Why-not-BUY +
// Blocking Gates table. Surfaces existing backend `final_action_reasons`
// plus locally-derived gate fires so the user can see exactly WHY a verdict
// is not BUY — instead of staring at "PROVISIONAL HOLD" with no context.
const GATE_NAMES = {
  critical_data_failed: 'Key data', provider_data_status: 'Data status', risk_grade_F: 'Risk grade',
  risk_grade_D: 'Risk grade', risk_reward_below_min: 'Reward / risk', composite_below_avoid: 'Overall score',
  composite_hold_band: 'Overall score', buy_now_no: 'Entry timing', negative_target_upside: 'Target',
  dcf_overvaluation: 'Valuation', heavy_insider_selling: 'Insider selling', bearish_reversal: 'Candle pattern',
};
function plainGateDetail(g) {
  const d = String(g.detail || '');
  if (g.name === 'buy_now_no') {
    return /only after/i.test(d) ? 'Candle timing says wait for a confirming close' : 'Candle timing says wait for a better entry';
  }
  if (g.name === 'risk_grade_D') return 'Grade D (high risk) caps a fresh buy at Accumulate, with a smaller position';
  if (g.name === 'risk_grade_F') return 'Grade F (very high risk) blocks new money';
  return plainReason(d);
}

function whyNotBuyHtml(finalActionInfo, validationSummary, { omitHeadline = '', primaryCode = '' } = {}) {
  if (!finalActionInfo) return '';
  // The main blocker is already stated with the call; a headline that only
  // repeats it word for word is left out here.
  const headline = finalActionInfo.headline && finalActionInfo.headline !== omitHeadline ? finalActionInfo.headline : '';
  const action = actionKey(finalActionInfo.finalAction || finalActionInfo.label);
  const isBuy = ['STRONG_BUY', 'BUY', 'ACCUMULATE'].includes(action);
  // For full BUYs with no fired gates, no need for this block.
  if (isBuy && (!finalActionInfo.gates || finalActionInfo.gates.length === 0)
      && !(finalActionInfo.fromDecision && action === 'ACCUMULATE')) return '';
  const gates = finalActionInfo.gates || [];
  const reasons = finalActionInfo.reasons || [];
  const critStatus = (validationSummary && validationSummary.criticalStatus) || 'PASS';
  const optStatus = (validationSummary && validationSummary.optionalStatus) || 'PASS';
  const critCls = critStatus === 'PASS' ? 'ok' : 'fail';
  const optCls = optStatus === 'PASS' ? 'ok' : (optStatus === 'PARTIAL' ? 'partial' : 'fail');
  const engineRules = !!finalActionInfo.engineRules;
  // One row per rule: its name, what it found and what would change it. The
  // rule that is the main blocker is already spelled out with the call, so its
  // row points there instead of saying it twice.
  const gateRows = gates.length
    ? gates.map((g) => {
      const isPrimary = engineRules && primaryCode && g.name === primaryCode;
      return `
        <li class="gate-row">
          <span class="gate-name" data-gate="${escapeHtml(g.name)}">${escapeHtml(engineRules ? chipCase(titleLabel(String(g.name).toLowerCase())) : (GATE_NAMES[g.name] || g.name.replace(/_/g, ' ')))}</span>
          <span class="gate-detail${isPrimary ? ' muted' : ''}">${isPrimary ? 'Main blocker: its finding and remedy are shown with the call.' : escapeHtml(engineRules ? g.detail : plainGateDetail(g))}</span>
          ${engineRules && g.remedy && !isPrimary ? `<span class="gate-remedy">Would change with: ${escapeHtml(g.remedy)}</span>` : ''}
        </li>`;
    }).join('')
    : `<li class="gate-row muted">${finalActionInfo.fromDecision ? 'No rules fired.' : 'No blocking gates fired.'}</li>`;
  const reasonItems = reasons.length
    ? reasons.map((r) => `<li>${escapeHtml(plainReason(r))}</li>`).join('')
    : '<li class="muted">No engine override reasons recorded.</li>';
  return `
    <article class="section-card why-not-buy" data-why-not-buy>
      <h3 class="section-kicker">${escapeHtml(({ STRONG_BUY: 'Risk context', BUY: 'Why not Strong Buy?', ACCUMULATE: 'Why only Accumulate?' })[action] || 'Why not Buy?')}${engineRules && gates.length ? ' <span class="section-kicker-tail">rules in the order they fired</span>' : ''}</h3>
      ${headline ? `<p class="validation-headline"><strong>${escapeHtml(headline)}</strong></p>` : ''}
      <div class="gate-chips">
        <span class="gate-chip ${critCls}">Critical data: ${escapeHtml(critStatus)}</span>
        <span class="gate-chip ${optCls}">Optional data: ${escapeHtml(optStatus)}</span>
      </div>
      ${finalActionInfo.fromDecision && !engineRules ? '<p class="muted small-note">The verdict is the engine Decision. The checks below are shown for context and do not change it.</p>' : ''}
      ${engineRules && !gates.length ? '<p class="muted small-note">No gate or cap changed this Decision; it is the score band\u2019s own call.</p>'
        : `<ol class="gate-list" aria-label="${engineRules ? 'Decision rules' : (finalActionInfo.fromDecision ? 'Context checks' : 'Blocking gates')}">${gateRows}</ol>`}
      <details>
        <summary>How the engine got here (${reasons.length})</summary>
        <ul>${reasonItems}</ul>
      </details>
    </article>`;
}

// Session quote block for the tear-sheet hero. The hero already owns "what does
// this trade at right now", so the exchange quote board belongs here rather than
// in a disconnected table further down the page: Open/High/Low/Prev/LTP, then
// the intraday band and the trailing band that give those five numbers context.
//
// Everything is derived from data the tear sheet already fetched
// (`price_chart`), so this costs no extra request. Returns '' when the payload
// has no usable session data — an empty board is worse than no board.
function priceReconNoteHtml(data) {
  const recon = (data && data.price_reconciliation) || {};
  return recon.discrepancy && recon.note
    ? `<p class="ds-recon" role="note">${escapeHtml(recon.note)}</p>`
    : '';
}

function decisionSessionHtml(data, opts = {}) {
  const sym = opts.sym || '$';
  const chart = data.price_chart || {};
  const session = chart.session || {};
  const q = normalizeQuote(session, {
    price: data.current_price,
    change_pct: data.day_change_pct,
  });
  const board = quoteBoardHtml(q, sym);
  const dayMeter = rangeMeterHtml(q.low, q.high, q.ltp, sym, 'Day Range');

  // Label the trailing band by the bars we actually hold — never claim "52W"
  // when the series is a 6-month slice.
  const bars = Number(q.rangeBars) || 0;
  const trailingLabel = bars >= 240 ? '52W Range' : (bars >= 100 ? '6M Range' : (bars ? `${bars}-Bar Range` : 'Range'));
  const trailingMeter = rangeMeterHtml(q.rangeLow, q.rangeHigh, q.ltp, sym, trailingLabel);

  if (!board && !dayMeter && !trailingMeter) return '';

  const volStr = fmtCount(q.volume);
  const asOf = q.date ? `Session ${q.date}` : '';
  const notes = [asOf, volStr ? `Volume ${volStr}` : ''].filter(Boolean).join(' · ');
  const degraded = chart.degraded || chart.from_cache;
  // [PRICE-RECONCILE] The backend compares the analysis price with the
  // session LTP once; when they disagree past tolerance, say so in the
  // block that shows both numbers, rather than leaving two prices unexplained.
  const reconNote = opts.omitRecon ? '' : priceReconNoteHtml(data);

  return `
    <div class="decision-session" aria-label="Session quote">
      <div class="ds-head">
        <span class="ds-kicker">Session quote</span>
        ${notes ? `<span class="ds-note">${escapeHtml(notes)}</span>` : ''}
        ${degraded ? '<span class="ds-stale" title="Served from the short-lived price cache">cached</span>' : ''}
      </div>
      ${board}
      ${dayMeter || trailingMeter ? `<div class="ds-meters">${dayMeter}${trailingMeter}</div>` : ''}
      ${reconNote}
    </div>`;
}

function renderAnalysis(data) {
  const container = $('#an-results');
  if (!data) { container.innerHTML = '<div class="empty-state large"><h3>No analysis data returned</h3></div>'; return; }
  state.analysis = data;
  if (data.is_invalid || String(data.final_action || '').toUpperCase() === 'INVALID') {
    container.innerHTML = `
      <div class="empty-state large">
        <h3>${escapeHtml(data.ticker || 'Ticker')}: data unavailable</h3>
        <p>${escapeHtml(data.recommendation_text || 'No usable price or fundamentals were returned. The ticker may be delisted, suspended, or rate-limited.')}</p>
      </div>`;
    return;
  }

  const sym = data.currency_symbol || '$';
  const trade = data.trade_setup || {};
  const valuation = data.valuation || {};
  const scores = data.scores || {};
  const technical = data.technical || {};
  const fundamental = data.fundamental || {};
  const risk = data.risk || {};
  const mc = data.monte_carlo || {};
  const thesis = data.thesis || {};
  const mi = data.market_intel || {};
  const explain = data.signal_explanation || {};
  // The published Decision is authoritative. Legacy sizing is only a fallback
  // for old payloads that predate the Decision contract.
  const sizing = (data.decision && data.decision.sizing) || data.position_sizing || {};
  const catalysts = data.catalysts || {};
  const sentiment = data.sentiment || {};
  const peer = data.peer_comparison || {};
  const insider = data.insider || {};
  const currentPrice = Number(data.current_price || 0);

  // Prefer the engine's authoritative FinalAction; fall back to the legacy signal.
  const tone = signalTone(data.final_action || data.signal);
  const recoLabel = recommendationLabel(data);
  const assetMeta = assetTypeMeta(data);
  const applicability = assetApplicability(data);
  const isEtf = assetMeta.isEtf;
  const aumOrCap = isEtf ? Number(data.net_assets || data.market_cap || 0) : Number(data.market_cap || 0);
  const capLabel = isEtf ? 'AUM' : 'Market Cap';
  const expenseRatio = Number(data.expense_ratio || 0);
  // Plain (no "+") percent for fee display — fmtPct prepends "+" which reads as a change.
  const expenseLabel = expenseRatio > 0
    ? `${(expenseRatio <= 1.5 ? expenseRatio * 100 : expenseRatio).toFixed(2)}%`
    : '';
  const aTgt = firstUsable(mi.target_mean);
  const aMed = firstUsable(mi.target_median);
  const aLow = firstUsable(mi.target_low);
  const aHigh = firstUsable(mi.target_high);
  // Model scenario rows must not silently become Street or simulation targets.
  const baseTgt = firstUsable(trade.target_base, valuation.target_base);
  const bearTgt = firstUsable(valuation.target_conservative, trade.target_conservative);
  const bullTgt = firstUsable(trade.target_aggressive, valuation.target_aggressive);
  const stopL = firstUsable(trade.stop_loss);
  const entryP = firstUsable(trade.entry_price, currentPrice);
  const baseUp = upsidePct(baseTgt, currentPrice);
  // Upside is measured from the price on this sheet, so the Street mean and its
  // percent agree with every other target here; the provider's own figure
  // (taken against its own quote) is only a fallback.
  const analystUp = upsidePct(aTgt, currentPrice) ?? numOrNull(mi.implied_upside);
  const primaryReason = explain.primary_reason || thesis.summary || 'QUANTA has completed the factor stack and risk review for this ticker.';
  const newsSplit = splitHeadlinesByRelevance((sentiment.key_headlines || []), data.ticker, data.company_name);

  const summaryItems = [primaryReason, ...(data.key_insights || []).slice(0, 2)].filter(Boolean);
  const actionItems = (data.action_items || []).filter(Boolean).slice(0, 4);

  const cacheText = data.cache?.hit ? `Cached ${data.cache.age_seconds}s` : 'Fresh analysis';

  // ---- Validation layer: derive critical issues BEFORE rendering, so the
  // banner, Beginner Guidance DQ and conditional blocks all see the same truth.
  const requiredAudit = computeRequiredSectionAudit(data, {
    currentPrice,
    recommendation: recoLabel,
    baseTgt,
    bearTgt,
    bullTgt,
    entryP,
  });
  const validationIssues = buildValidationIssues(data, {
    requiredMissing: requiredAudit.missing,
    newsLimited: newsSplit.limitedTickerNews,
  });
  const validationBanner = renderValidationBanner(validationIssues);
  const requiredBanner = renderRequiredMissingBanner(requiredAudit.missing);
  // [HOLD-INFLATION-FIX] Critical vs optional split prevents OPTIONAL data
  // gaps (missing analyst coverage, MC inconsistency) from triggering
  // PROVISIONAL HOLD on otherwise tradeable setups.
  const validationSummary = summarizeValidationIssues(validationIssues);
  const dqOverride = validationSummary.dqOverride;
  const finalActionInfo = classifyFinalAction(data, validationSummary);

  // [DQ-GATE] Resolve the data-quality / low-confidence display gate once, then
  // thread the result through the hero, executive summary and beginner guidance
  // so every panel tells the same story when the analysis is unreliable.
  const dqGate = dataQualityGate({
    dataQualityFailed: dqOverride === 'FAIL',
    confidenceScore: data.confidence_score,
    confidenceLevel: data.confidence_level,
    riskGrade: risk.risk_grade,
  });
  // When DQ fails, suppress bullish styling and relabel the recommendation.
  const heroTone = dqGate.failed ? 'neutral' : tone;
  // [HOLD-INFLATION-FIX] Prefer the precise finalActionInfo label
  // (NOT_CONFIRMED / LOW_CONVICTION_ACCUMULATE / AVOID_NEW_BUY / ...)
  // over the generic engine label, but only when it differs meaningfully.
  // A published Decision's label is never replaced in the browser: a failed
  // display check adds the "Provisional" badge beside it instead. The
  // relabels below apply only to legacy payloads without a Decision.
  let preciseLabel = recoLabel;
  if (data.decision && data.decision.label) preciseLabel = data.decision.label;
  else if (dqGate.overrideRecoLabel) preciseLabel = dqGate.overrideRecoLabel;
  else if (finalActionInfo && finalActionInfo.label === 'LOW_CONVICTION_ACCUMULATE') preciseLabel = 'LOW-CONVICTION ACCUMULATE: CONFIRMATION ONLY';
  else if (finalActionInfo && finalActionInfo.label === 'AVOID_NEW_BUY') preciseLabel = 'AVOID NEW BUY: POOR RISK/REWARD';
  else if (finalActionInfo && finalActionInfo.label === 'HOLD_EXISTING_STRICT_STOP') preciseLabel = 'HOLD EXISTING: STRICT STOP';
  const displayThesis = dqGate.rejectionThesis
    || sanitizeExceptionText(primaryReason)
    || 'Thesis unavailable for this ticker.';
  const suppressMC = validationIssues.some((i) => i.text.includes('Monte Carlo'));
  const suppressAnalystRating = validationIssues.some((i) => i.text.includes('Analyst rating'));

  // R/R is a named backend scenario net of execution costs. The browser must
  // not manufacture a gross ratio from whichever levels happen to be present.
  // "No long reward" is a finding (the valuation target sits at or below the
  // entry); a missing target is an absence, and says so instead.
  const noTarget = trade.has_long_reward === false && !isPositive(trade.target_base);
  const noLongReward = trade.has_long_reward === false && !noTarget;
  const effectiveRR = (noLongReward || noTarget) ? null
    : (isPositive(trade.risk_reward_ratio) ? Number(trade.risk_reward_ratio) : null);
  const sharedRRLabel = noTarget
    ? 'Not available (no target)'
    : noLongReward
    ? 'No long reward · 0:1'
    : ((data.beginner_guidance && data.beginner_guidance.rr_label && data.beginner_guidance.rr_label !== 'N/A')
      ? data.beginner_guidance.rr_label
      : (effectiveRR ? `${fmtNumber(effectiveRR, 2)}:1` : 'Not available'));

  const mcMedianMovePct = isPositive(currentPrice) && isPositive(mc.median_price)
    ? ((Number(mc.median_price) - currentPrice) / currentPrice) * 100
    : null;
  const mcExpected = Number(mc.expected_return);
  const mcProb = Number(mc.probability_profit);
  // Mean and median may differ in sign for a skewed distribution, so only the
  // median-vs-probability pairing (same central tendency) can conflict.
  const mcSanityWarn = (Number.isFinite(mcMedianMovePct) && mcMedianMovePct > 0
    && Number.isFinite(mcProb) && mcProb < 0.05)
    ? 'Monte Carlo direction/probability conflict detected.'
    : '';

  const debugPanel = renderDevDebugPanel({
    enabled: isDebugUiEnabled(),
    ticker: data.ticker,
    fetchStatus: 'complete',
    hasAnalysis: !!data,
    renderedCount: requiredAudit.renderedCount,
    requiredCount: requiredAudit.requiredCount,
    missing: requiredAudit.missing,
    dataQuality: dqOverride || String((data.beginner_guidance || {}).data_quality || 'PASS').toUpperCase(),
    errors: validationIssues.map((i) => `${i.severity}: ${i.text}`),
    lastRefresh: state.lastUpdated ? formatRelativeTime(state.lastUpdated) : 'just now',
  });

  // ---- Decision integrity: the engine Decision is the only verdict. Legacy
  // commentary that names a different action is withheld from the lists that
  // sit beside it (it remains in the plain-language guidance, labelled).
  const decision = data.decision || null;
  const consistent = (items) => decisionConsistentLines(items, decision);
  const levels = (decision && decision.levels) || {};
  const refPrice = firstUsable(levels.reference_price, entryP, currentPrice);
  // R/R is measured from the entry, so the hero shows the entry it used.
  const heroEntry = firstUsable(levels.entry, refPrice);
  const heroAction = (decision && decision.action) || data.final_action || data.signal;
  const heroQualifier = dqGate.failed ? '' : ((decision && decision.qualifier) || data.recommendation_qualifier || '');
  const heroMeaning = dqGate.failed || dqGate.overrideRecoLabel ? '' : decisionMeaning(heroAction);
  // With a Decision, the hero shows only the levels it published: an exit or
  // no-target Decision has none, and the valuation blend (shown in the
  // Valuation section) must not stand in as its "target".
  const heroStop = decision ? firstUsable(levels.stop) : firstUsable(levels.stop, stopL);
  const heroTarget = decision ? firstUsable(levels.target) : firstUsable(levels.target, baseTgt);
  const heroTiming = dqGate.failed ? null : entryTimingNote(data, heroAction, sym, heroTarget);
  const heroUp = upsidePct(heroTarget, currentPrice);
  const horizonLabel = (decision && decision.horizon && decision.horizon.label)
    || (trade.time_horizon ? titleLabel(trade.time_horizon) : '');
  const ev = (decision && decision.evidence) || data.evidence || {};
  const evTone = ev.permits_recommendation ? 'bull' : (ev.status === 'NO_EDGE' ? 'warn' : 'warn');
  const fresh = (decision && decision.freshness) || {};
  const freshText = fresh.status === 'fresh' ? `Fresh · last bar ${fresh.last_bar || '--'}`
    : fresh.status === 'stale' ? `Stale · ${fresh.lag_sessions != null ? `${fresh.lag_sessions} sessions old` : 'out of date'}`
      : (data.cache?.hit ? `Cached ${data.cache.age_seconds}s` : 'Freshness unknown');
  const conviction = data.confidence_score != null && Number.isFinite(Number(data.confidence_score))
    ? `${fmtNumber(data.confidence_score, 0)}/100${data.confidence_level ? ` · ${titleLabel(data.confidence_level)}` : ''}` : '--';
  const whyItems = decision
    ? [...(decision.reasons || [])].filter(Boolean).slice(0, 4)
    : [displayThesis];
  // The Decision publishes what it means for each reader (engine.blockers):
  // the score's direction, new-money eligibility, the conditional holder
  // action and the first blocking rule. Rendered as sent, never re-derived.
  const contract = decisionContract(decision);
  const blockerList = decision && Array.isArray(decision.blockers) ? decision.blockers : [];
  const primaryBlocker = contract ? contract.primaryBlocker : null;
  const changeItems = decision
    ? (blockerList.length
      ? blockerList.filter((b) => !primaryBlocker || b.code !== primaryBlocker.code).map((b) => b.text)
      : (decision.blocking || []).map(plainReason))
      .filter(Boolean).slice(0, 3)
    : [];
  // Measured evidence that disagrees with the call, stated beside it.
  const conflictItems = decision ? (decision.conflicts || []).filter(Boolean).slice(0, 3) : [];
  // A call with nothing holding it back still names its main risk: the first
  // condition that would withdraw it. The full list stays in Decision detail.
  const watchItem = (!primaryBlocker && !changeItems.length && !conflictItems.length && !dqGate.failed
    && decision && Array.isArray(decision.invalidation) && decision.invalidation[0]) || '';
  const targetMonths = levels.target_horizon_days ? Math.max(1, Math.round(Number(levels.target_horizon_days) / 21)) : null;
  // The longer-dated valuation reference the horizon target is pro-rated from.
  const valuationTarget = decision && isPositive(levels.valuation_target) ? Number(levels.valuation_target) : null;
  const valuationMonths = levels.valuation_target_horizon_days ? Math.max(1, Math.round(Number(levels.valuation_target_horizon_days) / 21)) : 12;
  // A value that is words (not a figure) is set in the sans, never in mono.
  const wordCls = (v) => (typeof v === 'string' && v && !/^[-+\u2212]?[$\u20b9\u20ac\u00a3]?\d/.test(v.replace(/<[^>]*>/g, '').trim()) ? ' is-word' : '');
  const composite = scores.composite_score != null ? `${fmtNumber(scores.composite_score, 1)}/100` : '--';
  const fact = (k, v, cls = '', title = '', attrs = '') => `<div class="dfact"${title ? ` title="${escapeHtml(title)}"` : ''}${attrs ? ` ${attrs}` : ''}><dt>${escapeHtml(k)}</dt><dd class="${cls}">${v}</dd></div>`;
  const level = (k, v, cls = '', sub = '') => `<div class="dlevel"><span class="dlevel-k">${escapeHtml(k)}</span><span class="dlevel-v ${cls}${wordCls(v)}">${v}</span>${sub ? `<span class="dlevel-s">${sub}</span>` : ''}</div>`;

  // Chapter scaffold. The layer (facts, signals, interpretation, Decision) is
  // kept as data; opinion and commentary are labelled on the card that holds
  // them. Empty chapters are dropped after the Pro layer runs.
  const chapter = (key, title, layer, inner) => `
      <section class="ts-chapter" id="ts-${key}" data-ch="${key}" data-layer="${escapeHtml(layer)}" aria-labelledby="ts-${key}-h">
        <header class="ts-chapter-head"><h2 id="ts-${key}-h">${escapeHtml(title)}</h2></header>
        <div class="ts-chapter-body" data-ch-body="${key}">${inner}</div>
      </section>`;
  // Tab panels group the chapters; the summary above them is always visible.
  const panel = (key, label, chapters) => `
      <section class="ts-panel" id="ts-panel-${key}" data-panel="${key}" data-label="${escapeHtml(label)}" role="tabpanel" aria-labelledby="ts-tab-${key}-n" tabindex="-1">${chapters}</section>`;

  const factsCard = (kicker, rows, extra = '') => `
      <article class="section-card">
        <h3 class="section-kicker">${escapeHtml(kicker)}</h3>
        <dl class="fact-grid">${rows.filter(Boolean).map(([k, v, cls]) => `<div class="fact-row"><dt>${escapeHtml(k)}</dt><dd class="${cls || ''}${wordCls(v == null ? '' : String(v))}">${v == null || v === '' ? '<span class="q-na">--</span>' : escapeHtml(String(v))}</dd></div>`).join('')}</dl>
        ${extra}
      </article>`;
  const naEtf = (v) => (isEtf ? 'N/A (ETF)' : v);
  // The engine reports fundamentals already in percent (ROE 148.75 = 148.75%).
  const pctLevel = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? 'Not reported' : `${fmtNumber(Number(v), 1)}%`);
  const pctChange = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? 'Not reported' : fmtPct(Number(v), 1, false));

  const fundamentalsCard = factsCard('Fundamental quality', [
    ['Return on equity', naEtf(pctLevel(fundamental.roe))],
    ['Net margin', naEtf(pctLevel(fundamental.net_margin))],
    ['Revenue growth YoY', naEtf(pctChange(fundamental.revenue_growth_yoy)), isEtf ? '' : directionClass(fundamental.revenue_growth_yoy)],
    ['Debt / equity', naEtf(fundamental.debt_to_equity == null ? 'Not reported' : fmtNumber(fundamental.debt_to_equity, 2))],
    ['Piotroski F-score', isEtf ? 'N/A (ETF)' : (fundamental.piotroski_f_score != null ? `${fundamental.piotroski_f_score}/9` : `N/A (${Number(fundamental.piotroski_components_available || 0)}/9 inputs reported)`)],
    ['Altman Z', isEtf ? 'N/A (ETF)' : (fundamental.altman_z_status === 'not_applicable' ? 'N/A (financial)'
      : (isPositive(Math.abs(Number(fundamental.altman_z_score))) && fundamental.altman_z_score != null
        ? `${fmtNumber(fundamental.altman_z_score, 2)}${fundamental.altman_z_variant === 'private' ? ' (Z′ book-equity)' : ''}`
        : 'N/A (statements unavailable)'))],
  ]);
  const valuationCard = factsCard('Valuation multiples', [
    ['P/E (trailing)', fmtMultiple(fundamental.pe_ratio, 1)],
    ['P/E (forward)', fmtMultiple(fundamental.forward_pe, 1)],
    ['PEG', naEtf(fmtMultiple(fundamental.peg_ratio, 2))],
    ['EV / EBITDA', naEtf(fmtMultiple(fundamental.ev_ebitda, 1))],
    // A DCF that was rejected or does not apply is not a 0% gap to price.
    ['DCF vs price', valuation.dcf_status && valuation.dcf_status !== 'measured'
      ? (valuation.dcf_status === 'not_applicable' ? (isEtf ? 'N/A (ETF)' : 'N/A (financial)') : 'Unavailable')
      : (valuation.dcf_upside != null && Number.isFinite(Number(valuation.dcf_upside)) ? fmtPct(valuation.dcf_upside, 1, false) : null),
      valuation.dcf_status === 'measured' ? directionClass(valuation.dcf_upside) : ''],
    // Stored as a fraction (0.0098 = 0.98%).
    ['Dividend yield', Number(fundamental.dividend_yield) > 0 ? `${fmtNumber(Number(fundamental.dividend_yield) * 100, 2)}%` : 'None reported'],
    ['Valuation read', valuation.valuation_label ? titleLabel(valuation.valuation_label) : null],
  ]);
  const technicalCard = factsCard('Technical setup', [
    ['Trend', [titleLabel(technical.trend_direction), titleLabel(technical.trend_strength)].filter((x) => x && x !== '--').join(' / ')],
    ['RSI (14)', fmtNumber(technical.rsi, 1)],
    ['ADX', fmtNumber(technical.adx, 1)],
    ['MACD', technical.macd_signal ? titleLabel(technical.macd_signal) : null],
    ['Volume vs average', technical.volume_ratio ? `${fmtNumber(technical.volume_ratio, 2)}×` : null],
    ['Support', isPositive(technical.support_level) ? fmtPrice(technical.support_level, sym) : null],
    ['Resistance', isPositive(technical.resistance_level) ? fmtPrice(technical.resistance_level, sym) : null],
    ['52-week range', isPositive(technical.week_52_low) && isPositive(technical.week_52_high) ? `${fmtPrice(technical.week_52_low, sym)} – ${fmtPrice(technical.week_52_high, sym)}` : null],
  ]);
  const riskGradeTone = /^[AB]$/.test(risk.risk_grade || '') ? 'bull' : (/^[DF]$/.test(risk.risk_grade || '') ? 'bear' : 'warn');
  const riskCard = !data.risk ? renderRiskMetricsCard(null) : `
      <article class="section-card risk-profile-card">
        <h3 class="section-kicker">Risk profile <span class="risk-grade-chip ${risk.risk_grade ? riskGradeTone : ''}">Risk grade ${escapeHtml(risk.risk_grade || '--')}</span></h3>
        ${riskFactsHtml(risk)}
        ${Array.isArray(risk.grade_drivers) && risk.grade_drivers.length ? `
        <div class="grade-drivers" data-grade-drivers>
          <h4>Why grade ${escapeHtml(risk.risk_grade || '--')}: ${Number(risk.risk_points || 0)} of 10 risk points (baseline 3)</h4>
          <ul>${risk.grade_drivers.map((d) => `<li class="${Number(d.points) > 0 ? 'adds' : ''}"><span class="gd-label">${escapeHtml(d.label)}</span><span class="gd-pts">${Number(d.points) > 0 ? `+${Number(d.points)}` : '0'}</span><span class="gd-detail">${escapeHtml(d.detail)}</span></li>`).join('')}</ul>
          <p class="muted small-note">A stop limits a planned loss; it is not a maximum-loss guarantee, and gaps can open through it.</p>
        </div>` : ''}
      </article>`;

  const keyPoints = consistent(summaryItems.slice(1));
  const notesCard = (keyPoints.length || actionItems.length) ? `
      <article class="section-card">
        <h3 class="section-kicker">Key points <span class="section-kicker-tail">engine commentary, not the Decision</span></h3>
        <div class="brief-columns brief-columns--2">
          <div class="brief-col">
            <h4>What matters now</h4>
            <ul>${keyPoints.map((x) => `<li>${escapeHtml(stripEmoji(x))}</li>`).join('') || '<li class="muted">No additional points.</li>'}</ul>
          </div>
          <div class="brief-col">
            <h4>Monitoring</h4>
            <ul>${consistent(actionItems).map((x) => `<li>${escapeHtml(stripEmoji(x))}</li>`).join('') || '<li class="muted">Follow the plan levels in Decision.</li>'}</ul>
          </div>
        </div>
      </article>` : '';
  // Risk lines are shown once, in the Risks chapter's drivers card.

  // What the company is, in the provider's words (a fact, cited as such).
  const businessCard = data.business_summary ? `
      <article class="section-card business-card" data-business>
        <h3 class="section-kicker">The business <span class="section-kicker-tail">provider description</span></h3>
        <p class="business-summary">${escapeHtml(String(data.business_summary).length > 700 ? `${String(data.business_summary).slice(0, 700).replace(/\s+\S*$/, '')}…` : data.business_summary)}</p>
        <dl class="business-facts">
          ${[['Sector', data.sector], ['Industry', data.industry], ['Country', data.country], ['Exchange', exchangeName(data.exchange)], ['Currency', data.currency]]
            .filter(([, v]) => v && v !== 'Unknown' && v !== 'UNKNOWN').map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('')}
        </dl>
      </article>` : '';

  // Stock versus its own market's benchmark over the same sessions.
  const rel = data.relative_performance || {};
  const relRows = Array.isArray(rel.rows) ? rel.rows.filter((r) => r.stock_pct != null) : [];
  const relMax = Math.max(1, ...relRows.map((r) => Math.abs(Number(r.excess_pct) || 0)));
  const relBar = (x) => (x == null || !Number.isFinite(Number(x)) ? '' : `<span class="rp-bar" aria-hidden="true"><span class="rp-bar-fill ${Number(x) >= 0 ? 'is-up' : 'is-down'}" style="width:${((Math.abs(Number(x)) / relMax) * 50).toFixed(1)}%"></span></span>`);
  const relativeCard = relRows.length ? `
      <article class="section-card ts-relative" data-relative-performance>
        <h3 class="section-kicker">Performance vs ${escapeHtml(rel.benchmark || 'benchmark')} <span class="section-kicker-tail">${escapeHtml(rel.basis || '')}</span></h3>
        <div class="q-tablewrap" tabindex="0" role="region" aria-label="Relative performance">
          <table class="q-table"><thead><tr><th>Window</th><th class="num">${escapeHtml(data.ticker || 'Stock')}</th><th class="num">${escapeHtml(rel.benchmark || 'Benchmark')}</th><th class="num">Difference</th></tr></thead>
          <tbody>${relRows.map((r) => `<tr><td>${escapeHtml(r.window)}</td><td class="num ${directionClass(r.stock_pct)}">${fmtPct(r.stock_pct, 1, false)}</td><td class="num">${r.benchmark_pct != null ? fmtPct(r.benchmark_pct, 1, false) : '<span class="q-na">n/a</span>'}</td><td class="num rp-diff ${r.excess_pct != null ? directionClass(r.excess_pct) : ''}">${relBar(r.excess_pct)}${r.excess_pct != null ? `${r.excess_pct >= 0 ? '+' : ''}${fmtNumber(r.excess_pct, 1)} pts` : '<span class="q-na">n/a</span>'}</td></tr>`).join('')}</tbody></table>
        </div>
      </article>` : '';

  // How the 12-month target is built: each method, its weight, its upside.
  const comps = Array.isArray(valuation.target_components) ? valuation.target_components : [];
  const COMPONENT_NAME = {
    analyst_consensus: `Analyst consensus${Number(mi.analyst_count) ? ` (${Number(mi.analyst_count)} analysts)` : ''}`,
    dcf: 'DCF intrinsic value',
    intrinsic_value: 'Intrinsic value (DCF / Graham)',
    technical_resistance: 'Technical resistance (tactical level)',
    technical_r1: 'Pivot R1 (tactical level)',
  };
  const targetBuildCard = comps.length === 1 ? `
      <article class="section-card" data-target-build>
        <h3 class="section-kicker">12-month target build-up <span class="section-kicker-tail">one usable method</span></h3>
        <p class="tb-one">Weighted 12-month target <b class="mono">${fmtPrice(baseTgt, sym)}</b> <span class="mono ${directionClass(upsidePct(baseTgt, currentPrice))}">${fmtPct(upsidePct(baseTgt, currentPrice), 1, false)}</span>, from ${escapeHtml(COMPONENT_NAME[comps[0].source] || titleLabel(comps[0].source))}, the only method that produced a usable estimate.</p>
        <p class="muted small-note">Methods that failed or do not apply are left out, not set to today’s price.</p>
      </article>` : comps.length ? `
      <article class="section-card" data-target-build>
        <h3 class="section-kicker">12-month target build-up <span class="section-kicker-tail">model estimate from ${comps.length} source${comps.length === 1 ? '' : 's'}</span></h3>
        <div class="q-tablewrap" tabindex="0" role="region" aria-label="Target components">
          <table class="q-table"><thead><tr><th>Method</th><th class="num">Estimate</th><th class="num">Weight</th><th class="num">vs price</th></tr></thead>
          <tbody>${comps.map((c) => `<tr><td>${escapeHtml(COMPONENT_NAME[c.source] || titleLabel(c.source))}</td><td class="num">${fmtPrice(c.value, sym)}</td><td class="num">${fmtNumber(Number(c.weight) * 100, 0)}%</td><td class="num ${directionClass(upsidePct(c.value, currentPrice))}">${fmtPct(upsidePct(c.value, currentPrice), 1, false)}</td></tr>`).join('')}
          <tr class="is-total"><td><strong>Weighted 12-month target</strong></td><td class="num"><strong>${fmtPrice(baseTgt, sym)}</strong></td><td class="num">100%</td><td class="num ${directionClass(upsidePct(baseTgt, currentPrice))}"><strong>${fmtPct(upsidePct(baseTgt, currentPrice), 1, false)}</strong></td></tr></tbody></table>
        </div>
        ${renderTargetQuality(valuation)}
        <p class="muted small-note">A weighted blend of the methods that produced a usable estimate. Methods that failed or do not apply are left out, not set to today’s price.</p>
      </article>` : '';

  // Measurable conditions that would withdraw a funded call.
  const invalidationCard = decision && Array.isArray(decision.invalidation) && decision.invalidation.length ? `
      <article class="section-card" data-invalidation>
        <h3 class="section-kicker">What would withdraw this call <span class="section-kicker-tail">the engine’s own rules</span></h3>
        <ul class="q-bullets">${decision.invalidation.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>
      </article>` : '';

  const scenarioRows = decision && Array.isArray(decision.scenarios) && decision.scenarios.length ? `
      <article class="section-card">
        <h3 class="section-kicker">Decision scenarios <span class="section-kicker-tail">levels the Decision was built on</span></h3>
        <div class="q-tablewrap" tabindex="0" role="region" aria-label="Decision scenarios">
          <table class="q-table"><thead><tr><th>Scenario</th><th class="num">Price</th><th class="num">Return</th><th>Method</th></tr></thead>
          <tbody>${decision.scenarios.map((sc) => `<tr><td>${escapeHtml(titleLabel(sc.name))}</td><td class="num">${fmtPrice(sc.price, sym)}</td><td class="num ${directionClass(sc.return_pct)}">${fmtPct(sc.return_pct, 1, false)}</td><td>${escapeHtml(sc.method || '')}</td></tr>`).join('')}</tbody></table>
        </div>
      </article>` : '';

  // The method ladder shows only when a method produced a target; an ETF with
  // none gets the simulation across the tile, not an empty half.
  const showLadder = !comps.length && [bearTgt, baseTgt, bullTgt, aTgt].some(isPositive);
  const priceTargetsCard = `
      <article class="section-card">
        <h3 class="section-kicker">${showLadder ? 'Price targets &amp; simulation' : `${escapeHtml(String(mc.days_simulated || 30))}-day simulation`}</h3>
        <div class="targets-grid${showLadder ? '' : ' targets-grid--sim'}">
          ${!showLadder ? '' : `<div class="target-ladder">
            ${targetRow('Low method estimate (12m)', bearTgt, currentPrice, sym)}
            ${targetRow('Weighted estimate (12m)', baseTgt, currentPrice, sym, true)}
            ${targetRow('High method estimate (12m)', bullTgt, currentPrice, sym)}
            ${targetRow('Street consensus', aTgt, currentPrice, sym)}
            ${renderTargetQuality(valuation)}
            ${isPositive(bearTgt) && isPositive(currentPrice) && Number(bearTgt) > Number(currentPrice) ? `<p class="target-note" data-bear-above-price>The low method estimate is above today\u2019s price, so it is a valuation range, not a downside.${isPositive(stopL) ? ` The loss limit is the stop at ${escapeHtml(fmtPrice(stopL, sym))}.` : ''}</p>` : ''}
          </div>`}
          ${suppressMC ? `
          <div class="forecast-grid forecast-grid--suppressed">
            <div class="forecast-cell"><div class="label">Monte Carlo</div><div class="value">Suppressed: output inconsistent. See Evidence.</div></div>
          </div>` : `
          <div class="forecast-grid">
            <div class="forecast-cell"><div class="label">MC median</div><div class="value">${fmtPrice(mc.median_price, sym)}</div></div>
            <div class="forecast-cell"><div class="label" title="Mean terminal return across simulated paths; the median is shown separately.">Mean return</div><div class="value ${directionClass(mc.expected_return)}">${mc.expected_return != null ? fmtPct(mc.expected_return, 1, false) : '--'}</div></div>
            <div class="forecast-cell"><div class="label" title="${escapeHtml(mc.probability_event || 'Terminal outcome at the simulated horizon.')}">P(up at ${mc.days_simulated || '30'}d)</div><div class="value">${mc.probability_profit != null ? fmtPct(mc.probability_profit, 1, true) : '--'}</div></div>
            <div class="forecast-cell"><div class="label">95% VaR</div><div class="value bear">${mc.var_95 != null ? fmtPct(mc.var_95, 1, false) : '--'}</div></div>
          </div>`}
        </div>
        ${!suppressMC && mc.probability_event ? `<p class="mc-model-note"><strong>What this probability means:</strong> ${escapeHtml(mc.probability_event)}</p>` : ''}
        ${mcSanityWarn ? `<p class="mc-model-note"><strong>Sanity warning:</strong> ${escapeHtml(mcSanityWarn)}</p>` : ''}
        ${data.mc_model_note && !suppressMC ? `<p class="mc-model-note"><strong>Model note:</strong> ${escapeHtml(data.mc_model_note)}</p>` : ''}
        ${!showLadder ? '' : '<p class="mc-model-note"><strong>Valuation range:</strong> low/high are the observed range of included methods, not probability-weighted operating scenarios or tactical exit levels.</p>'}
      </article>`;

  const analystCard = (() => {
    const analystCount = Number(mi.analyst_count || 0)
      || ((Number(mi.buy_ratings) || 0) + (Number(mi.hold_ratings) || 0) + (Number(mi.sell_ratings) || 0));
    const hasRating = analystCount > 0 && !suppressAnalystRating;
    const hasTargets = isPositive(aTgt) || isPositive(aMed) || isPositive(aLow) || isPositive(aHigh);
    if (!hasRating && !hasTargets && !insider?.insider_sentiment) return '';
    // Zero counts are a measurement only when a source answered. A skipped module
    // (fast preview) or an outage must not read as "Neutral · 0 buys / 0 sells".
    const insiderStatus = insider && (insider.data_status || (data.fast_mode ? 'not_fetched' : 'measured'));
    const insiderBox = insider && insider.insider_sentiment ? (applicability.insider === 'na_etf'
      ? `<div class="insider-box"><span class="label">Insider activity (90D)</span><div class="value"><span class="muted">Not applicable</span> · ETFs / funds have no single-issuer insider filings.</div></div>`
      : insiderStatus === 'not_fetched'
      ? `<div class="insider-box"><span class="label">Insider activity (90D)</span><div class="value"><span class="muted">Not fetched in fast preview</span> · run the full analysis to measure insider buying and selling.</div></div>`
      : insiderStatus === 'unavailable'
      ? `<div class="insider-box"><span class="label">Insider activity (90D)</span><div class="value"><span class="muted">Unavailable</span> · no insider-transaction source responded, so buys and sells are unknown (not zero).</div></div>`
      : (() => {
        const dispLabel = insider.display_label || insider.insider_sentiment;
        const labelTone = dispLabel === 'BULLISH' ? 'bull' : (dispLabel === 'BEARISH' ? 'bear' : 'muted');
        const ctxNote = insider.context_missing ? '<div class="note">Net selling detected, but sale context is incomplete (sale type and time window not available).</div>' : '';
        return `<div class="insider-box"><span class="label">Insider activity (90D)</span>
          <div class="value"><span class="${labelTone}">${escapeHtml(titleLabel(dispLabel))}</span> · ${Number(insider.recent_buys || 0)} buys / ${Number(insider.recent_sells || 0)} sells · Net ${fmtCurrency(insider.net_flow, sym)}</div>${ctxNote}</div>`;
      })()) : '';
    return `
      <article class="section-card">
        <h3 class="section-kicker">Street view <span class="section-kicker-tail">third-party opinion, not QUANTA&rsquo;s</span></h3>
        <div class="analyst-grid">
          <div class="analyst-rating">
            <span class="label">Street rating</span>
            ${hasRating ? `
              <span class="verdict">${escapeHtml(titleLabel(mi.analyst_rating || 'HOLD'))}</span>
              ${Number.isFinite(analystUp)
                ? `<span class="upside ${analystUp >= 0 ? 'bull' : 'bear'}">${fmtPct(analystUp, 1, false)} <span class="is-word">implied upside</span></span>`
                : '<span class="upside muted">Upside unavailable</span>'}
              <span class="muted small-note">${Number(mi.rating_score) > 0 ? `Score ${fmtNumber(mi.rating_score, 2)} of 5 · ` : ''}${analystCount} analysts</span>` : `
              <span class="verdict muted">Unavailable</span>
              <span class="muted small-note">No analyst count returned${suppressAnalystRating ? ' (rating reported without analyst coverage)' : ''}${data.fast_mode ? '; skipped in fast preview' : ''}.</span>`}
          </div>
          <div>
            ${hasRating && (Number(mi.buy_ratings) || Number(mi.hold_ratings) || Number(mi.sell_ratings)) ? analystVotes(mi) : ''}
            ${hasTargets ? `
              <div class="mini-targets">
                <div class="mini-target">Low <strong>${fmtPrice(aLow, sym)}</strong></div>
                <div class="mini-target">Mean <strong>${fmtPrice(aTgt, sym)}</strong></div>
                <div class="mini-target">Median <strong>${fmtPrice(aMed, sym)}</strong></div>
                <div class="mini-target">High <strong>${fmtPrice(aHigh, sym)}</strong></div>
              </div>
              ${!hasRating ? '<p class="muted small-note">Price targets are from data providers; analyst rating counts were not returned.</p>' : ''}` : ''}
            ${insiderBox}
          </div>
        </div>
      </article>`;
  })();

  // Sizing is part of the plan: a sub-block of the plan card, not a card of
  // its own holding one sentence.
  const sizingHtml = (() => {
    const shares = Number(sizing.recommended_shares || 0);
    const positionValue = Number(sizing.position_value || 0);
    const portfolioWt = sizing.portfolio_weight;
    const hasShares = isPositive(shares);
    const hasValue = isPositive(positionValue);
    const hasWt = portfolioWt != null && Number(portfolioWt) > 0;
    // A size beside a failed data check would read as permission to buy.
    if (dqGate.failed && (hasShares || hasValue || hasWt)) {
      return `
        <section class="plan-sizing" data-sizing-withheld>
          <h4 class="section-kicker">Position sizing</h4>
          <p class="section-fallback-text">Sizing withheld: this tear sheet failed a data check. A size appears once the checks pass.</p>
        </section>`;
    }
    if (!hasShares && !hasValue && !hasWt) {
      return `
        <section class="plan-sizing">
          <h4 class="section-kicker">Position sizing</h4>
          <p class="section-fallback-text">No position for this Decision. Sizing is computed only when the Decision permits new money and the plan has a valid entry and stop.</p>
        </section>`;
    }
    const cell = (label, value, cls = '') => `<div class="trade-cell"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;
    return `
        <section class="plan-sizing">
          <h4 class="section-kicker">Position sizing <span class="section-kicker-tail">${decision && decision.sizing_basis === 'validated' ? 'validated sizing' : 'risk budget, not a validated edge'}</span></h4>
          <div class="trade-grid">
            ${cell('Shares', hasShares ? fmtNumber(shares, 0) : '<span class="muted">Not provided</span>')}
            ${cell('Position value', hasValue ? fmtMoney(positionValue, sym, 0) : '<span class="muted">Not provided</span>')}
            ${cell('Portfolio weight', hasWt ? `${(Number(portfolioWt) * 100).toFixed(1)}%` : '<span class="muted">Not provided</span>')}
          </div>
          ${renderAccountContextNote(data)}
          ${renderPortfolioCheckNote(data)}
        </section>`;
  })();

  const newsCard = `
      <article class="section-card">
        <h3 class="section-kicker">News &amp; catalysts</h3>
        <div class="news-brief-grid">
          <div>
            <h4 class="headline-title">Ticker-specific news</h4>
            <div class="headline-list">
              ${data.fast_mode ? '<div class="plan-note info">News was not fetched in fast preview; load the full analysis for headlines and sentiment.</div>' : (newsSplit.limitedTickerNews ? '<div class="plan-note warn">Ticker-specific news limited. Sentiment confidence reduced.</div>' : '')}
              ${newsSplit.tickerNews.length
                ? newsSplit.tickerNews.map((h) => `<div class="headline-row">${escapeHtml(h)}</div>`).join('')
                : (data.fast_mode ? '' : '<div class="headline-row is-empty">No ticker-specific headlines returned.</div>')}
            </div>
            ${newsSplit.marketContext.length
              ? `<h4 class="headline-title">Sector / market context</h4><div class="headline-list">${newsSplit.marketContext.map((h) => `<div class="headline-row is-context">${escapeHtml(h)}</div>`).join('')}</div>`
              : ''}
          </div>
          <div>
            <h4 class="headline-title">Dated catalysts</h4>
            <div class="compact-facts compact-facts--list">
              <div class="fact"><span class="k">Next earnings</span><span class="v">${escapeHtml(catalysts.earnings_date || mi.next_earnings_date || 'Not reported')}</span></div>
              <div class="fact"><span class="k">Days to earnings</span><span class="v${!isEtf && numOrNull(catalysts.days_to_earnings) != null && Number(catalysts.days_to_earnings) >= 0 && Number(catalysts.days_to_earnings) <= Number(thr('WHY.EARNINGS_NEAR_DAYS')) ? ' warn' : ''}">${catalysts.days_to_earnings != null && Number(catalysts.days_to_earnings) >= 0 ? `${catalysts.days_to_earnings}d` : '--'}</span></div>
              <div class="fact"><span class="k">Last result</span><span class="v">${catalysts.last_earnings_result ? escapeHtml(titleLabel(catalysts.last_earnings_result)) : '--'}</span></div>
              <div class="fact"><span class="k">News sentiment</span><span class="v">${sentimentLabelText(sentiment)}</span></div>
              <div class="fact"><span class="k">Earnings beat rate</span><span class="v">${mi.earnings_beat_rate != null ? fmtPct(mi.earnings_beat_rate, 0, true) : '--'}</span></div>
            </div>
          </div>
        </div>
      </article>`;

  const scoresCard = `
      <article class="section-card ts-scores">
        <h3 class="section-kicker">Multi-factor scores <span class="section-kicker-tail">quantitative signals · 0–100 · ranking inputs${ev.permits_recommendation ? '' : ', not validated'}</span></h3>
        <div class="score-grid score-strip">
          ${scoreBar('Composite', scores.composite_score).replace('class="score-row', 'class="score-row is-lead')}
          ${scoreBar('Technical', scores.technical_score)}
          ${scoreBar('Fundamental', scores.fundamental_score)}
          ${scoreBar('Valuation', scores.valuation_score)}
          ${scoreBar('Quality', scores.quality_score)}
          ${scoreBar('Sentiment', scores.sentiment_score)}
          ${scoreBar('Risk', scores.risk_score)}
        </div>
      </article>`;

  const guidance = `
      <details class="detail-card ts-guidance">
        <summary>Plain-language guidance <span class="section-kicker-tail">entry styles, stops and holder view; the Decision governs</span></summary>
        <div class="detail-body detail-body--stack">
          ${data.beginner_guidance ? renderBeginnerGuidance(data, { sym, dqOverride, sharedRRLabel, dqGate, decisionLabel: decision ? decision.label : null }) : sectionUnavailableCard('Beginner guidance', 'beginner_guidance object missing in payload')}
          ${renderPortfolioOutlook(data, decision) || sectionUnavailableCard('Portfolio outlook', 'portfolio_outlook object missing in payload')}
        </div>
      </details>`;

  const modelEvidenceCard = `
      <article class="section-card model-evidence-card">
        <h3 class="section-kicker">Model evidence</h3>
        <div class="model-evidence">
          <span class="q-status-cell ${evTone}">${escapeHtml(ev.status_text || ev.status || 'Unknown')}</span>
          <p>${escapeHtml(ev.summary || 'No evidence record returned for this model.')}${ev.tested_through ? ` Tested through ${escapeHtml(ev.tested_through)}.` : ''}</p>
          ${ev.strategy ? `<span class="model-evidence-strategy mono">${escapeHtml(ev.strategy)}</span>` : ''}
        </div>
        ${(ev.caveats || []).length ? `<ul class="q-bullets">${ev.caveats.map((c) => `<li class="warn">${escapeHtml(c)}</li>`).join('')}</ul>` : ''}
        <a class="q-btn q-btn--link" href="#track" data-view-link="track">Open model evidence and track record</a>
      </article>`;

  const sessionQuote = decisionSessionHtml(data, { sym, omitRecon: true });
  const sessionTile = sessionQuote ? `<article class="section-card ts-session" data-session>${sessionQuote}</article>` : '';

  // Section tabs: the six names and nothing else. The Decision rail beside
  // them already carries the call and its figures; each panel owns its own.
  const tabsHtml = Object.entries(TS_SECTIONS).map(([key, label]) => `<button type="button" class="ts-tab" role="tab" id="ts-tab-${key}" aria-controls="ts-panel-${key}" data-ts-tab="${key}" aria-labelledby="ts-tab-${key}-n" aria-selected="false" tabindex="-1"><span class="ts-tab-name" id="ts-tab-${key}-n">${escapeHtml(label)}</span></button>`).join('');

  // The engine's own rules (decision.blockers) are stated once, in the band's
  // why column, in the order they fired: the main blocker first with its
  // remedy, then the rest. The Fast preview rule carries the action that
  // clears it, so the sheet needs no separate preliminary banner.
  const engineBlockers = decision && Array.isArray(decision.blockers) ? decision.blockers.filter(Boolean) : null;
  const fastBlocker = engineBlockers ? engineBlockers.find((b) => /FAST/.test(String(b.code || ''))) : null;
  const loadFullBtn = data.fast_mode ? `<button type="button" class="q-btn q-btn--sm" data-load-full-analysis="${escapeHtml(String(data.ticker || '').toUpperCase())}">Load full analysis</button>` : '';
  const ruleRemedy = (b) => (b.remedy ? `<span class="decision-remedy">Would change with: ${escapeHtml(b.remedy)}</span>` : '');
  // The engine's primary blocker is shown even when its rule list omits it.
  const mainRule = engineBlockers ? (engineBlockers.find((b) => primaryBlocker && b.code === primaryBlocker.code) || primaryBlocker || null) : null;
  const otherRules = engineBlockers ? engineBlockers.filter((b) => b !== mainRule) : [];
  const rulesHtml = engineBlockers ? `
            ${mainRule ? `<h3>Main blocker</h3><p class="decision-blocker warn" data-primary-blocker="${escapeHtml(mainRule.code || '')}"><span class="gate-name" data-gate="${escapeHtml(mainRule.code || '')}">${escapeHtml(mainRule.text || '')}</span>${ruleRemedy(mainRule)}${mainRule === fastBlocker && loadFullBtn ? ` ${loadFullBtn}` : ''}</p>` : ''}
            ${otherRules.length ? `<h3>${mainRule ? 'Also holding it back' : 'Holding it back'}</h3><ul class="q-bullets decision-rules">${otherRules.map((b) => `<li class="warn"><span class="gate-name" data-gate="${escapeHtml(b.code || '')}">${escapeHtml(b.text || '')}</span>${ruleRemedy(b)}${b === fastBlocker && loadFullBtn ? ` ${loadFullBtn}` : ''}</li>`).join('')}</ul>` : ''}
            ${!engineBlockers.length && !mainRule ? '<p class="decision-norules">No gate or cap changed this Decision; it is the score band\u2019s own call.</p>' : ''}`
    : `
            ${primaryBlocker ? `<h3>Main blocker</h3><p class="decision-blocker warn" data-primary-blocker="${escapeHtml(primaryBlocker.code || '')}">${escapeHtml(primaryBlocker.text || '')}${primaryBlocker.remedy ? `<span class="decision-remedy">Would change with: ${escapeHtml(primaryBlocker.remedy)}</span>` : ''}</p>` : ''}
            ${changeItems.length ? `<h3>${primaryBlocker ? 'Also holding it back' : 'Holding it back'}</h3><ul class="q-bullets">${changeItems.map((x) => `<li class="warn">${escapeHtml(x)}</li>`).join('')}</ul>` : ''}`;
  // A fast run whose engine sent no fast rule still says so, once, in the band.
  const fastNotice = data.fast_mode && !fastBlocker ? tearSheetFastModeBannerHtml(data) : '';

  // The call's facts sit with the call in the rail, one line each. Data
  // freshness is stated only when it is a caution (stale); fresh data is the
  // normal state and reads as a tooltip on the price.
  const railFactsHtml = `
            <dl class="decision-facts">
              ${horizonLabel ? fact('Horizon', escapeHtml(horizonLabel)) : ''}
              ${contract ? fact('Model stance', escapeHtml(contract.stanceText), contract.stanceTone, 'Direction of the 0\u2013100 score before the risk, valuation and data rules', 'data-contract="model_stance"') : ''}
              ${contract ? (dqGate.failed && String((decision || {}).new_money || '').toUpperCase() === 'ELIGIBLE'
                ? fact('New money', 'Withheld until data checks pass', 'warn', `The engine's Decision reads ${contract.newMoneyText}, but this tear sheet failed a data check, so no new money until it passes`, 'data-contract="new_money" data-withheld="data-quality"')
                : fact('New money', escapeHtml(contract.newMoneyText), contract.newMoneyTone, 'Whether someone without a position may open one now', 'data-contract="new_money"')) : ''}
              ${contract ? fact('If you hold it', escapeHtml(contract.holderText), contract.holderTone, contract.holdingsKnown ? 'Based on your declared holdings' : 'QUANTA does not know your holdings; this applies only if you own it', 'data-contract="holder_action"') : ''}
              ${fact('Conviction', `${escapeHtml(conviction)}${Number.isFinite(Number(data.confidence_score)) ? `<span class="dmeter${dqGate.lowConfidence ? ' is-low' : ''}" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, Number(data.confidence_score)))}%"></span></span>` : ''}`, dqGate.lowConfidence ? 'warn' : '', 'Heuristic 0–100 conviction, not a calibrated probability')}
              ${risk.risk_grade ? fact('Risk grade', `<a href="#ts-risks" data-ts-jump="risks" class="dgrade dgrade--${/^[DF]$/.test(risk.risk_grade) ? 'poor' : (/^[AB]$/.test(risk.risk_grade) ? 'good' : 'mid')}">${escapeHtml(risk.risk_grade)}</a>`, '', 'Open the risk profile and its grade drivers') : ''}
              ${fresh.status === 'stale' ? fact('Data', escapeHtml(freshText), 'warn', '', 'data-fact="data"') : ''}
            </dl>`;

  const html = `
    <div class="analysis-stack">
      <div class="ts-banners">
        ${data.symbol_note ? `<div class="plan-note info" role="note" data-symbol-note><strong>Listing analysed: ${escapeHtml(data.ticker || '')}.</strong> ${escapeHtml(data.symbol_note)}</div>` : ''}
        ${tearSheetCacheBannerHtml(data)}
        ${dataQualityFailBannerHtml(dqGate)}
        ${assetCaveatBannerHtml(data)}
        ${priceReconNoteHtml(data)}
      </div>

      <div class="ts-desk">
      <section class="decision-hero ts-summary ts-rail ${heroTone}" id="ts-snapshot" aria-labelledby="ts-snapshot-h">
        <div class="decision-top">
          <div class="decision-id">
            <div class="decision-ticker-row">
              <h2 class="decision-ticker" id="ts-snapshot-h">${escapeHtml(data.ticker || '')}</h2>
              <div class="decision-price">
                <span class="decision-price-value" title="${escapeHtml(`Data: ${freshText}`)}" data-freshness="${escapeHtml(freshText)}">${escapeHtml(fmtPrice(currentPrice, sym))}</span>
                <span class="decision-price-change ${directionClass(data.day_change_pct)}">${fmtPct(data.day_change_pct, 2, false)} today</span>
              </div>
            </div>
            <span class="decision-company">${escapeHtml([data.company_name, data.sector].filter(Boolean).join(' · '))}${aumOrCap ? `<span class="decision-cap"> · ${escapeHtml(capLabel)} <span class="mono">${fmtCurrency(aumOrCap, sym, 0)}</span></span>` : ''}</span>
            <div class="decision-tags">
              ${assetMeta.chips
                .filter((c) => !(c.kind === 'asset' && assetMeta.assetType === 'common_stock'))
                .map((c) => `<span class="tag" data-asset-chip="${escapeHtml(c.kind)}">${escapeHtml(chipCase(c.label))}</span>`).join('')}
              ${isEtf && String(data.quote_type || '').toUpperCase() !== 'ETF' ? `<span class="tag">${escapeHtml(String(data.quote_type || 'ETF').toUpperCase())}</span>` : ''}
              ${data.market_regime ? `<span class="tag" data-regime-chip title="Market regime, not this stock">Market: ${escapeHtml(titleLabel(data.market_regime).toLowerCase())}</span>` : ''}
              ${expenseLabel ? `<span class="tag">Expense ${expenseLabel}</span>` : ''}
              ${dqGate.failed ? `<span class="tag warn" data-status-chip="dq">Data quality fail</span>` : ''}
            </div>
          </div>
        </div>

        <section class="decision-action ${heroTone}" aria-label="QUANTA Decision">
          <div class="decision-call">
            <span class="label">QUANTA Decision</span>
            <span class="signal">${escapeHtml(preciseLabel)}</span>
            ${decision || heroQualifier ? modelFlagHtml(ev) : ''}
            ${dqGate.failed ? '<span class="provisional-badge">Provisional: data quality failed</span>' : ''}
            ${heroMeaning ? `<p class="decision-meaning">${escapeHtml(heroMeaning)}</p>` : ''}
            ${heroTiming ? `<p class="decision-timing ${heroTiming.tone}" data-entry-timing>${escapeHtml(heroTiming.text)}</p>` : ''}
          </div>
          ${railFactsHtml}
          <section class="decision-why" aria-label="Why this Decision"${engineBlockers ? ' data-why-not-buy' : ''}>
            <h3>Why this Decision</h3>
            <ul class="q-bullets">${whyItems.map((x) => `<li>${escapeHtml(plainReason(stripEmoji(x)))}</li>`).join('') || '<li class="muted">No reasons returned.</li>'}</ul>
            ${rulesHtml}
            ${fastNotice}
            ${conflictItems.length ? `<h3>Weighs against this call</h3><ul class="q-bullets" data-decision-conflicts>${conflictItems.map((x) => `<li class="warn">${escapeHtml(x)}</li>`).join('')}</ul>` : ''}
            ${dqGate.failed ? `
            <div class="decision-verdict">
              <div><strong>Should I buy now?</strong> <span class="answer">${escapeHtml(dqGate.buyNowAnswer)}</span>. ${escapeHtml(dqGate.newBuyerAction)}</div>
              <div><strong>Should I hold existing?</strong> ${escapeHtml(dqGate.holdAnswer)}. ${escapeHtml(dqGate.existingHolderAction)}</div>
              <div><strong>Why?</strong> ${escapeHtml(dqGate.whyReasons.join(', ') || 'analysis is unreliable')}.</div>
            </div>` : ''}
            ${watchItem ? `<h3>What would withdraw it</h3><p class="decision-watch">${escapeHtml(watchItem)}${decision.invalidation.length > 1 ? ` <a href="#ts-decision" data-ts-jump="decision">All ${decision.invalidation.length} conditions</a>` : ''}</p>` : ''}
          </section>

          <section class="decision-levels" aria-label="Key levels">
            <h3>Key levels</h3>
            <div class="dlevels">
            ${level(isPositive(levels.entry) ? 'Entry' : 'Reference price', fmtPrice(heroEntry, sym))}
            ${level('Stop', isPositive(heroStop) ? fmtPrice(heroStop, sym) : '--', isPositive(heroStop) ? 'bear' : '')}
            ${level(`${targetMonths ? (valuationTarget ? `${targetMonths}-month target` : `${targetMonths}-month valuation target`) : (decision ? 'Target' : 'Base target')}${dqGate.failed ? ' (not validated)' : ''}`, isPositive(heroTarget) ? fmtPrice(heroTarget, sym) : '--', isPositive(heroTarget) ? (dqGate.failed ? 'muted' : 'gold') : '',
              isPositive(heroTarget) && heroUp != null ? `<span class="${dqGate.failed ? 'muted' : directionClass(heroUp)}">${fmtPct(heroUp, 1, false)}</span>` : (decision ? 'none published by this Decision' : ''))}
            ${level('Reward / risk', escapeHtml(sharedRRLabel), '', /^\d/.test(sharedRRLabel) ? `to the ${targetMonths ? `${targetMonths}-month ` : ''}target, after costs` : '')}
            ${whyItems.some((x) => /\d\/100\b/.test(plainReason(stripEmoji(String(x))))) ? '' : level('Composite', `${composite}${dqGate.failed ? ' (preliminary)' : ''}`, dqGate.failed ? 'muted' : '')}
            ${valuationTarget ? level(`${valuationMonths}-month valuation target`, fmtPrice(valuationTarget, sym), 'muted',
              `<span class="${directionClass(upsidePct(valuationTarget, currentPrice))}">${fmtPct(upsidePct(valuationTarget, currentPrice), 1, false)}</span><span class="dlevel-note">context, not the R/R target</span>`) : ''}
            </div>
            ${levelLadderHtml(heroStop, heroEntry, heroTarget, currentPrice, sym, dqGate.failed)}
          </section>

            ${decision && decision.caveat ? `<p class="decision-caveat">${escapeHtml(decision.caveat)}</p>` : ''}
            ${!decision && sanitizeExceptionText(data.recommendation_text) ? `<p class="decision-caveat">${escapeHtml(sanitizeExceptionText(data.recommendation_text))}</p>` : ''}
        </section>
      </section>

      <div class="ts-main">
      <div class="ts-glance">
        <section class="ts-chart" id="ts-chart" aria-label="${escapeHtml(data.ticker || '')} price chart" data-symbol="${escapeHtml(String(data.ticker || '').toUpperCase())}"></section>
        ${technicalSignalsHtml(technical, currentPrice, sym) || technicalCard}
        ${sessionTile}
        ${scoresCard}
        ${relativeCard}
      </div>
      <nav class="ts-nav" aria-label="Tear sheet sections">
        <a class="ts-nav-id" href="#ts-snapshot" data-ts-jump="snapshot"><span class="ts-nav-tk">${escapeHtml(data.ticker || '')}</span><span class="ts-nav-call ${heroTone}">${escapeHtml(preciseLabel)}</span><span class="ts-nav-px">${escapeHtml(fmtPrice(currentPrice, sym))}</span><span class="sr-only"> Decision summary</span></a>
        <div class="ts-tabs" role="tablist">${tabsHtml}</div>
      </nav>

      <div class="ts-panels">
      ${panel('decision', TS_SECTIONS.decision, chapter('decision', 'Decision', 'Authoritative', (engineBlockers ? '' : whyNotBuyHtml(finalActionInfo, validationSummary, { omitHeadline: primaryBlocker ? primaryBlocker.text : '', primaryCode: primaryBlocker ? primaryBlocker.code || '' : '' }))
        + invalidationCard
        + renderActionOrHolderPlan(data, { sym, currentPrice, baseTgt, bearTgt, bullTgt, stopL, entryP, sharedRRLabel, railEntry: heroEntry, railStop: heroStop, sizingHtml, held: !!data.has_existing_position })
        + guidance))}
      ${panel('business', TS_SECTIONS.business, chapter('thesis', 'Business & thesis', 'Facts & interpretation', businessCard + notesCard)
        + chapter('fundamentals', 'Fundamentals', 'Facts', fundamentalsCard))}
      ${panel('valuation', TS_SECTIONS.valuation, chapter('valuation', 'Valuation', 'Facts & estimates', targetBuildCard + analystCard + valuationCard)
        + chapter('scenarios', 'Scenarios', 'Model output', priceTargetsCard + (comps.length ? '' : scenarioRows)))}
      ${panel('technicals', TS_SECTIONS.technicals, chapter('technicals', 'Technicals', 'Facts & signals',
        // The price history is the live chart beside the Decision; this panel
        // holds the session and the technical readings.
        (renderCandlestickPanel(data, { compositeScore: scores.composite_score }) || sectionUnavailableCard('Candlestick patterns', 'No candlestick patterns or candlestick bias returned.'))))}
      ${panel('risks', TS_SECTIONS.risks, chapter('risks', 'Risks', 'Facts & interpretation', riskCard)
        + chapter('catalysts', 'Catalysts', 'Facts', newsCard))}
      ${panel('evidence', TS_SECTIONS.evidence, chapter('evidence', 'Evidence', 'Validation', modelEvidenceCard + `
        <section class="validation-section" id="an-validation" aria-label="Data confidence and validation">
          <h3 class="section-kicker">Data confidence &amp; validation</h3>
          ${renderConfidenceSummaryCard(data, validationSummary, requiredAudit, dqGate)}
          ${validationBanner}
          ${requiredBanner}
          ${renderSourceLedgerCard(data)}
          ${debugPanel}
        </section>`))}
      </div>
      </div>
      </div>
    </div>
  `;
  container.innerHTML = html;
  buildTearSheetNav(container);
  wirePriceChart(container);
  // The live chart draws the Decision's own stop and target, named as the
  // Decision names them; levels far outside the range are listed under it.
  const chartHost = container.querySelector('#ts-chart');
  if (chartHost && typeof window !== 'undefined' && window.QuantaLiveChart) {
    const chartLevels = [
      isPositive(heroStop) ? { label: 'Stop', value: Number(heroStop), tone: 'stop' } : null,
      isPositive(heroTarget) ? { label: targetMonths ? `${targetMonths}-month target` : 'Target', value: Number(heroTarget), tone: 'target' } : null,
    ].filter(Boolean);
    window.QuantaLiveChart.mount(chartHost, { symbol: chartHost.dataset.symbol, context: 'research', showQuote: false, levels: chartLevels });
  } else if (chartHost) {
    chartHost.innerHTML = renderPriceChartCard(data.price_chart, { ticker: data.ticker, currentPrice, sym, vbWidth: 720, vbHeight: 300,
      target: isPositive(heroTarget) ? heroTarget : (decision ? null : baseTgt),
      targetLabel: isPositive(heroTarget) && targetMonths ? `${targetMonths}-month target` : 'Target',
      stop: isPositive(heroStop) ? heroStop : (decision ? null : stopL) });
    wirePriceChart(container);
  }
  // The request bar's progress is for the run, not a permanent fixture.
  setTimeout(() => { $('#an-progress')?.classList.add('hidden'); }, 300);

  // [PERF-FASTMODE] Wire the "Load full analysis" button on the preliminary
  // (fast-mode) banner — re-runs the same ticker with every module enabled.
  container.querySelectorAll('[data-load-full-analysis]').forEach((b) => {
    b.addEventListener('click', () => {
      const tk = b.getAttribute('data-load-full-analysis');
      const input = document.querySelector('#an-ticker');
      if (input && tk) input.value = tk;
      const fastEl = document.querySelector('#an-fast-mode');
      if (fastEl) fastEl.checked = false;
      runAnalysis(null, { fastMode: false });
    });
  });

  requestAnimationFrame(() => {
    $$('.score-fill', container).forEach((el) => {
      el.style.width = `${el.dataset.fill || '0'}%`;
    });
    if (window.QuantaPro && typeof window.QuantaPro.injectAnalysisExtras === 'function') {
      try { window.QuantaPro.injectAnalysisExtras(data); } catch (_) { /* non-fatal */ }
    }
    buildTearSheetNav(container);
  });
}

// Technical readings as signals: each row names the reading, its value and
// what it says in one word, coloured by direction (green bullish, red bearish,
// amber near a level, gray neutral). Bands come from config (UI.TECH_*,
// REGIME.RSI_OVERBOUGHT). The header tallies the directional rows.
function technicalSignalsHtml(technical, price, sym) {
  const t = technical || {};
  const num = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
  const p = num(price);
  const rows = [];
  const add = (key, label, value, tone, word, extra = '', note = '') => rows.push({ key, label, value, tone, word, extra, note });
  const dir = String(t.trend_direction || '').toUpperCase();
  if (dir) {
    const cross = t.golden_cross ? '<span class="sg-note sg-note--bull">Golden cross</span>' : (t.death_cross ? '<span class="sg-note sg-note--bear">Death cross</span>' : '');
    add('trend', 'Trend', escapeHtml(`${titleLabel(dir)}${t.trend_strength ? ` / ${titleLabel(t.trend_strength)}` : ''}`),
      /UP|BULL/.test(dir) ? 'bull' : (/DOWN|BEAR/.test(dir) ? 'bear' : 'neutral'),
      /UP|BULL/.test(dir) ? 'Uptrend' : (/DOWN|BEAR/.test(dir) ? 'Downtrend' : 'No clear trend'), '', cross);
  }
  if (t.macd_signal) {
    const m = String(t.macd_signal).toUpperCase();
    add('macd', 'MACD', escapeHtml(titleLabel(m)), /BULL/.test(m) ? 'bull' : (/BEAR/.test(m) ? 'bear' : 'neutral'),
      /BULL/.test(m) ? 'Momentum up' : (/BEAR/.test(m) ? 'Momentum down' : 'Flat'));
  }
  const rsi = num(t.rsi);
  if (rsi != null) {
    const ob = Number(thr('REGIME.RSI_OVERBOUGHT')); const os = Number(thr('UI.TECH_RSI_OVERSOLD'));
    const tone = rsi >= ob ? 'bear' : (rsi <= os ? 'bull' : 'neutral');
    add('rsi', 'RSI (14)', fmtNumber(rsi, 1), tone, rsi >= ob ? 'Overbought' : (rsi <= os ? 'Oversold' : 'Neutral'),
      `<span class="sg-gauge" aria-hidden="true"><span class="sg-zone sg-zone--os" style="width:${os}%"></span><span class="sg-zone sg-zone--ob" style="left:${ob}%;width:${100 - ob}%"></span><span class="sg-mark" style="left:${Math.max(0, Math.min(100, rsi))}%"></span></span>`);
  }
  const adx = num(t.adx);
  if (adx != null) {
    const strong = adx >= Number(thr('UI.TECH_ADX_TREND')); const weak = adx < Number(thr('UI.TECH_ADX_WEAK'));
    add('adx', 'Trend strength (ADX)', fmtNumber(adx, 1), 'neutral', strong ? 'Strong' : (weak ? 'Weak' : 'Building'));
  }
  const vr = num(t.volume_ratio);
  if (vr != null && vr > 0) {
    add('volume', 'Volume vs average', `${fmtNumber(vr, 2)}\u00d7`, 'neutral',
      vr >= Number(thr('UI.TECH_VOLUME_HEAVY')) ? 'Heavy' : (vr <= Number(thr('UI.TECH_VOLUME_LIGHT')) ? 'Light' : 'Normal'));
  }
  const near = Number(thr('UI.TECH_NEAR_LEVEL_PCT'));
  const lvl = (key, label, v, side) => {
    const n = num(v);
    if (!(n > 0)) { add(key, label, '<span class="q-na">Not identified</span>', 'neutral', ''); return; }
    const gap = p > 0 ? ((n - p) / p) * 100 : null;
    const isNear = gap != null && Math.abs(gap) <= near;
    add(key, label, escapeHtml(fmtPrice(n, sym)),
      isNear ? 'warn' : 'neutral', isNear ? `Near ${side}` : (gap != null ? `${Math.abs(gap).toFixed(1)}% ${gap < 0 ? 'below' : 'above'}` : ''));
  };
  lvl('support', 'Support', t.support_level, 'support');
  lvl('resistance', 'Resistance', t.resistance_level, 'resistance');
  const lo = num(t.week_52_low); const hi = num(t.week_52_high);
  if (lo > 0 && hi > lo && p > 0) {
    const pos = Math.max(0, Math.min(100, ((p - lo) / (hi - lo)) * 100));
    add('range', '52-week range', `<span class="sg-range-lo">${escapeHtml(fmtPrice(lo, sym))}</span><span class="sg-range" aria-hidden="true"><span class="sg-range-mark" style="left:${pos.toFixed(1)}%"></span></span><span class="sg-range-hi">${escapeHtml(fmtPrice(hi, sym))}</span>`, 'neutral', `${pos.toFixed(0)}% of range`);
  }
  if (t.candlestick_bias) {
    const b = String(t.candlestick_bias).toUpperCase();
    const pat = (t.candlestick_patterns || [])[0];
    add('candles', 'Candles', pat && pat.name ? escapeHtml(pat.name) : escapeHtml(titleLabel(b)), /BULL/.test(b) ? 'bull' : (/BEAR/.test(b) ? 'bear' : 'neutral'), titleLabel(b));
  }
  if (!rows.length) return '';
  const directional = rows.filter((r) => ['trend', 'macd', 'rsi', 'candles'].includes(r.key));
  const count = (tone) => directional.filter((r) => r.tone === tone).length;
  const bull = count('bull'); const bear = count('bear'); const flat = directional.length - bull - bear;
  const pct = (n) => (directional.length ? (n / directional.length) * 100 : 0).toFixed(1);
  return `
      <article class="section-card ts-signals" data-signals aria-labelledby="ts-signals-h">
        <h3 class="section-kicker" id="ts-signals-h">Technical signals <span class="section-kicker-tail">${bull} bullish, ${bear} bearish, ${flat} neutral</span></h3>
        <div class="sg-tally" role="img" aria-label="${bull} bullish, ${bear} bearish and ${flat} neutral of ${directional.length} directional signals"><span class="sg-tally-bull" style="width:${pct(bull)}%"></span><span class="sg-tally-flat" style="width:${pct(flat)}%"></span><span class="sg-tally-bear" style="width:${pct(bear)}%"></span></div>
        <dl class="sg-list">${rows.map((r) => `
          <div class="sg-row" data-signal="${r.key}"><dt>${escapeHtml(r.label)}${r.note}</dt><dd class="sg-v">${r.value}${r.extra}</dd><dd class="sg-word">${r.word ? `<span class="sg-pill sg--${r.tone}">${escapeHtml(r.word)}</span>` : ''}</dd></div>`).join('')}
        </dl>
      </article>`;
}

// Risk against reward on one scale: the stop to the entry (red, the planned
// loss) beside the entry to the target (gold), with today's price marked. Drawn
// only when the Decision published all three levels in order.
function levelLadderHtml(stop, entry, target, price, sym, muted) {
  const s = Number(stop); const e = Number(entry); const t = Number(target); const p = Number(price);
  if (!(s > 0 && e > 0 && t > 0) || !(s < e && e < t)) return '';
  const lo = Math.min(s, p > 0 ? p : s); const hi = Math.max(t, p > 0 ? p : t);
  const at = (v) => `${(((v - lo) / (hi - lo)) * 100).toFixed(2)}%`;
  const riskPct = ((e - s) / e) * 100; const rewardPct = ((t - e) / e) * 100;
  return `
            <div class="dladder${muted ? ' is-muted' : ''}" role="img" aria-label="Risk ${riskPct.toFixed(1)}% to the stop against reward ${rewardPct.toFixed(1)}% to the target">
              <div class="dladder-track">
                <span class="dladder-risk" style="left:${at(s)};width:calc(${at(e)} - ${at(s)})"></span>
                <span class="dladder-reward" style="left:${at(e)};width:calc(${at(t)} - ${at(e)})"></span>
                ${p > 0 ? `<span class="dladder-now" style="left:${at(p)}" title="Price ${escapeHtml(fmtPrice(p, sym))}"></span>` : ''}
              </div>
              <div class="dladder-k"><span class="bear">Risk ${riskPct.toFixed(1)}%</span><span class="dladder-mid">Price ${escapeHtml(fmtPrice(p > 0 ? p : e, sym))}</span><span class="gold">Reward ${rewardPct.toFixed(1)}%</span></div>
            </div>`;
}

// Price chart readout: a crosshair with the exact date and close follows the
// pointer; the arrow keys step through closes when the chart has focus, and
// each keyboard step is announced once through a polite live region.
function wirePriceChart(root) {
  $$('.price-chart[data-series]', root).forEach((svg) => {
    if (svg.dataset.wired === '1') return;
    svg.dataset.wired = '1';
    let series;
    try { series = JSON.parse(svg.dataset.series); } catch (_) { return; }
    if (!series.length) return;
    const wrap = svg.closest('.chart-wrap');
    const tip = wrap.querySelector('.chart-tip');
    const live = wrap.querySelector('.chart-live');
    const cross = svg.querySelector('.chart-cross');
    const line = svg.querySelector('.chart-cross-line');
    const dot = svg.querySelector('.chart-cross-dot');
    const sym = svg.dataset.sym || '$';
    const vw = Number(svg.dataset.vw) || 1;
    let idx = series.length - 1;
    const show = (i, announce) => {
      idx = Math.max(0, Math.min(series.length - 1, i));
      const [date, close, px, py] = series[idx];
      line.setAttribute('x1', px); line.setAttribute('x2', px);
      dot.setAttribute('cx', px); dot.setAttribute('cy', py);
      cross.setAttribute('visibility', 'visible');
      const first = series[0][1];
      const chg = first ? ((close - first) / first) * 100 : null;
      tip.innerHTML = `<b>${escapeHtml(fmtPrice(close, sym))}</b><span>${escapeHtml(date)}</span>${chg != null ? `<i class="${directionClass(chg)}">${escapeHtml(fmtPct(chg, 1, false))} vs start</i>` : ''}`;
      tip.hidden = false;
      const scale = svg.getBoundingClientRect().width / vw;
      const left = px * scale;
      tip.style.left = `${Math.max(6, Math.min(left, svg.clientWidth - tip.offsetWidth - 6))}px`;
      tip.style.top = `${Math.max(6, py * scale - tip.offsetHeight - 14)}px`;
      if (announce && live) live.textContent = `${date}: close ${fmtPrice(close, sym)}`;
    };
    const hide = () => { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; };
    svg.addEventListener('pointermove', (ev) => {
      const r = svg.getBoundingClientRect();
      const vx = ((ev.clientX - r.left) / r.width) * vw;
      let best = 0; let bestD = Infinity;
      series.forEach((pt, i) => { const d = Math.abs(pt[2] - vx); if (d < bestD) { bestD = d; best = i; } });
      show(best, false);
    });
    svg.addEventListener('pointerleave', () => { if (document.activeElement !== svg) hide(); });
    svg.addEventListener('focus', () => show(idx, true));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (ev) => {
      const step = ev.shiftKey ? 5 : 1;
      const map = { ArrowLeft: idx - step, ArrowRight: idx + step, Home: 0, End: series.length - 1 };
      if (!(ev.key in map)) return;
      ev.preventDefault();
      show(map[ev.key], true);
    });
  });
}

// Tear sheet tabs: one per panel that holds a non-empty chapter; the chosen tab
// survives the next ticker. Rebuilt after the Pro layer injects its cards.
const TS_SECTIONS = Object.freeze({
  decision: 'Decision detail', business: 'Business', valuation: 'Valuation',
  technicals: 'Technicals', risks: 'Risks & catalysts', evidence: 'Evidence & data',
});

// Chapter tiles sit two to a row. A tile left alone in its row (an odd count,
// or the one before a full-width chart or disclosure) takes the whole row, so
// no row ends in an empty half.
const TS_WIDE = '.detail-card, .validation-section, .risk-profile-card, [data-business], [data-session], .ts-note, .ts-scores';
function balanceChapterTiles(container) {
  container.querySelectorAll('.ts-chapter-body').forEach((body) => {
    const tiles = Array.from(body.children).filter((el) => !el.hidden);
    tiles.forEach((el) => el.classList.remove('ts-span'));
    let open = null;
    tiles.forEach((el) => {
      if (el.matches(TS_WIDE)) {
        el.classList.add('ts-span');
        if (open) open.classList.add('ts-span');
        open = null;
      } else {
        open = open ? null : el;
      }
    });
    if (open) open.classList.add('ts-span');
  });
}

// Two tiles share a row only when their natural heights match within this
// ratio (surface brief: "within 15 percent"); otherwise each takes the full
// row, so neither tile carries a dead floor. The chart keeps its 2 : 1 pair.
const TS_PAIR_TOLERANCE = 1.15;
function fitChapterPairs(panel) {
  if (!panel || panel.hidden || typeof getComputedStyle !== 'function') return;
  panel.querySelectorAll('.ts-chapter-body').forEach((body) => {
    const tiles = Array.from(body.children).filter((el) => !el.hidden);
    tiles.forEach((el) => el.classList.remove('ts-solo'));
    const cs = getComputedStyle(body);
    if (cs.display !== 'grid' || cs.gridTemplateColumns.trim().split(/\s+/).length < 2) return;
    const pairs = [];
    let open = null;
    tiles.forEach((el) => {
      if (el.classList.contains('ts-span') || el.matches(TS_WIDE)) { open = null; return; }
      if (open) { pairs.push([open, el]); open = null; } else open = el;
    });
    if (!pairs.length) return;
    body.classList.add('ts-measure');
    const heights = pairs.map((pair) => pair.map((el) => el.getBoundingClientRect().height));
    body.classList.remove('ts-measure');
    pairs.forEach((pair, i) => {
      if (pair.some((el) => el.matches('.chart-card'))) return;
      const [a, b] = heights[i];
      if (a > 0 && b > 0 && Math.max(a, b) > TS_PAIR_TOLERANCE * Math.min(a, b)) pair.forEach((el) => el.classList.add('ts-solo'));
    });
  });
}

// The band's levels sit beside its why column. Their grid is two or three
// across, whichever leaves less empty height beside the reasons, so the two
// columns end together whatever the length of the engine's reasons.
function balanceDecisionBand(band) {
  const why = band && band.querySelector('.decision-why');
  const lv = band && band.querySelector('.decision-levels');
  if (!why || !lv || typeof getComputedStyle !== 'function') return;
  if (getComputedStyle(band.querySelector('.decision-action')).display !== 'grid') { band.classList.remove('is-levels-wide'); return; }
  const natural = (el) => Array.from(el.children).reduce((m, c) => Math.max(m, c.getBoundingClientRect().bottom), el.getBoundingClientRect().top) - el.getBoundingClientRect().top;
  const slack = (wide) => {
    band.classList.toggle('is-levels-wide', wide);
    return Math.abs(natural(why) - natural(lv));
  };
  const narrowSlack = slack(false);
  const wideSlack = slack(true);
  band.classList.toggle('is-levels-wide', wideSlack < narrowSlack);
}

// The chart shares its row with the Signals card: its plot height is set so the
// two tiles end level (bounded so the chart stays a compact, half-size view).
// The rail height feeds the sticky offset of the Decision column.
function fitChartToRail(container) {
  const desk = container.querySelector('.ts-desk');
  const rail = container.querySelector('.ts-rail');
  const chart = container.querySelector('#ts-chart');
  const partner = container.querySelector('.ts-glance > .ts-signals, .ts-glance > .section-card');
  if (!desk || !rail || !chart || typeof getComputedStyle !== 'function') return;
  if (getComputedStyle(desk).display === 'grid') desk.style.setProperty('--ts-rail-h', `${Math.ceil(rail.getBoundingClientRect().height)}px`);
  const glance = chart.parentElement;
  const twoUp = glance && getComputedStyle(glance).gridTemplateColumns.trim().split(/\s+/).length > 1;
  const plot = chart.querySelector('.lc-plot');
  if (!twoUp || !partner || !plot) { chart.style.removeProperty('--lc-h'); return; }
  const now = plot.getBoundingClientRect().height;
  // The partner's own content height, not its stretched row height.
  const pr = partner.getBoundingClientRect();
  const last = partner.lastElementChild;
  const natural = last ? last.getBoundingClientRect().bottom - pr.top + (parseFloat(getComputedStyle(partner).paddingBottom) || 0) + 1 : pr.height;
  const want = Math.round(Math.max(160, Math.min(320, now + natural - chart.getBoundingClientRect().height)));
  if (Math.abs(want - now) > 2) chart.style.setProperty('--lc-h', `${want}px`);
}

// Two lists side by side in one tile (supporting factors against key risks)
// stack when their lengths differ by more than 15 percent.
function stackUnevenLists(container) {
  container.querySelectorAll('.qpro-hold-risk-grid').forEach((grid) => {
    const cols = Array.from(grid.children);
    if (cols.length !== 2) return;
    grid.classList.remove('is-stacked');
    const [a, b] = cols.map((c) => c.getBoundingClientRect().height);
    const natural = (c) => Array.from(c.children).reduce((m, x) => m + x.getBoundingClientRect().height, 0);
    const [na, nb] = cols.map(natural);
    if (a && b && Math.max(na, nb) > 1.15 * Math.min(na, nb) + 24) grid.classList.add('is-stacked');
  });
}

// The rail sticks under the topbar while it fits the window. When it is taller
// it scrolls with the page until its foot is in view and holds there, so every
// line stays reachable without a scrollbar inside the rail.
function watchTearSheetLayout(container) {
  const desk = container.querySelector('.ts-desk');
  const rail = container.querySelector('.ts-rail');
  const panels = container.querySelector('.ts-panels');
  if (!desk || !rail || typeof ResizeObserver !== 'function') return;
  if (container._tsObserver) container._tsObserver.disconnect();
  let frame = 0;
  const sync = () => {
    frame = 0;
    balanceDecisionBand(rail);
    fitChartToRail(container);
    stackUnevenLists(container);
    fitChapterPairs(container.querySelector('.ts-panel:not([hidden])'));
  };
  // Once the Decision band has scrolled away, the section bar carries the
  // ticker, call and price, so the reader always knows what they are reading.
  const band = container.querySelector('#ts-snapshot');
  const nav = container.querySelector('.ts-nav');
  if (container._tsBandObserver) container._tsBandObserver.disconnect();
  if (band && nav && typeof IntersectionObserver === 'function') {
    const topbarH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')) || 56;
    const io = new IntersectionObserver(([entry]) => {
      nav.classList.toggle('is-stuck', !entry.isIntersecting && entry.boundingClientRect.top < topbarH);
    }, { rootMargin: `-${topbarH + 41}px 0px 0px 0px` });
    io.observe(band);
    container._tsBandObserver = io;
  }
  const ro = new ResizeObserver(() => { if (!frame) frame = requestAnimationFrame(sync); });
  ro.observe(rail);
  if (panels) ro.observe(panels);
  const chartHost = container.querySelector('#ts-chart');
  if (chartHost) ro.observe(chartHost);
  container._tsObserver = ro;
  sync();
}

function buildTearSheetNav(container) {
  const nav = container && container.querySelector('.ts-nav');
  if (!nav) return;
  balanceChapterTiles(container);
  container.querySelectorAll('.ts-chapter').forEach((ch) => {
    const body = ch.querySelector('.ts-chapter-body');
    ch.hidden = !body || !body.children.length;
  });
  const panels = Array.from(container.querySelectorAll('.ts-panel'));
  const live = panels.filter((p) => p.querySelector('.ts-chapter:not([hidden])'));
  panels.forEach((p) => {
    p.classList.toggle('is-single', p.querySelectorAll('.ts-chapter:not([hidden])').length === 1);
    if (!live.includes(p)) p.hidden = true;
  });
  const tablist = nav.querySelector('.ts-tabs');
  // A section with nothing to show keeps its tab out of the bar.
  tablist.querySelectorAll('[data-ts-tab]').forEach((t) => {
    t.hidden = !live.some((p) => p.dataset.panel === t.dataset.tsTab);
  });
  const keys = live.map((p) => p.dataset.panel);
  selectTearSheetPanel(container, keys.includes(state.researchTab) ? state.researchTab : keys[0]);
  watchTearSheetLayout(container);

  live.forEach((p) => {
    if (p.dataset.wired) return;
    p.dataset.wired = '1';
    // Find-in-page reveals a closed panel instead of skipping it.
    p.addEventListener('beforematch', () => selectTearSheetPanel(container, p.dataset.panel));
  });
  if (!container.dataset.tsNavWired) {
    container.dataset.tsNavWired = '1';
    container.addEventListener('click', (ev) => {
      const tab = ev.target.closest?.('[data-ts-tab]');
      if (tab) { openTearSheetPanel(container, tab.dataset.tsTab); return; }
      const link = ev.target.closest?.('[data-ts-jump]');
      if (!link) return;
      const target = container.querySelector(`#ts-${link.dataset.tsJump}`);
      if (!target) return;
      ev.preventDefault();
      const owner = target.closest('.ts-panel');
      if (owner) selectTearSheetPanel(container, owner.dataset.panel);
      scrollTearSheetTo(owner && owner.classList.contains('is-single') ? owner : target);
    });
    container.addEventListener('keydown', (ev) => {
      const tab = ev.target.closest?.('[data-ts-tab]');
      if (!tab) return;
      const tabs = Array.from(container.querySelectorAll('[data-ts-tab]:not([hidden])'));
      const i = tabs.indexOf(tab);
      const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[ev.key];
      if (next == null) return;
      ev.preventDefault();
      const to = tabs[(next + tabs.length) % tabs.length];
      selectTearSheetPanel(container, to.dataset.tsTab);
      to.focus();
    });
  }
}

const TS_UNTIL_FOUND = typeof document !== 'undefined' && !!document.body && 'onbeforematch' in document.body;

function selectTearSheetPanel(container, key) {
  if (!key) return;
  state.researchTab = key;
  container.querySelectorAll('.ts-panel').forEach((p) => {
    const empty = !p.querySelector('.ts-chapter:not([hidden])');
    if (p.dataset.panel === key && !empty) p.removeAttribute('hidden');
    else if (empty) p.hidden = true;
    else if (TS_UNTIL_FOUND) p.setAttribute('hidden', 'until-found');
    else p.hidden = true;
  });
  container.querySelectorAll('[data-ts-tab]').forEach((t) => {
    const on = t.dataset.tsTab === key;
    t.setAttribute('aria-selected', on ? 'true' : 'false');
    t.tabIndex = on ? 0 : -1;
  });
  fitChapterPairs(container.querySelector(`#ts-panel-${key}`));
  // The tab bar scrolls sideways on a phone: keep the open tab inside it.
  const tabs = container.querySelector('.ts-tabs');
  const onTab = container.querySelector(`[data-ts-tab="${key}"]`);
  if (tabs && onTab && tabs.scrollWidth > tabs.clientWidth) {
    const l = onTab.getBoundingClientRect().left - tabs.getBoundingClientRect().left + tabs.scrollLeft;
    if (l < tabs.scrollLeft || l + onTab.offsetWidth > tabs.scrollLeft + tabs.clientWidth) tabs.scrollLeft = l;
  }
}

// A tab picked while reading further down brings its panel's top up under the
// sticky bar instead of leaving the reader in the middle of the new panel.
function openTearSheetPanel(container, key) {
  selectTearSheetPanel(container, key);
  const nav = container.querySelector('.ts-nav');
  const panel = container.querySelector(`#ts-panel-${key}`);
  if (nav && panel && panel.getBoundingClientRect().top < nav.getBoundingClientRect().bottom) scrollTearSheetTo(panel, false);
}

function scrollTearSheetTo(target, focus = true) {
  const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  if (focus && typeof target.focus === 'function') {
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
  }
}

/* ---------------------------------------------------------------------------
   JOB POLLING (SSE + fallback)
   --------------------------------------------------------------------------- */
async function fetchJobResult(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || 'Job result fetch failed');
  return payload.result;
}

// Result identity: each scan / analysis request takes a token; a job callback
// only touches the UI while its token is still the latest one for that view.
// Otherwise a slow older response would overwrite a newer ticker or market.
const _jobTokens = { scan: 0, analysis: 0 };
function nextJobToken(kind) { return ++_jobTokens[kind]; }
function jobTokenCurrent(kind, token) { return _jobTokens[kind] === token; }

// A failed or cancelled Picks scan: one state in the results panel, with the
// reason once and one button that runs the scan again.
function scanErrorHtml(payload) {
  const cancelled = !!(payload && payload.error_detail && payload.error_detail.reason === 'cancelled');
  const reason = (payload && payload.error) || 'The scan did not return a result.';
  return `<div class="error-box empty-state large scan-error" role="alert">
    <h3>${cancelled ? 'Scan cancelled' : 'The scan did not finish'}</h3>
    <p>${escapeHtml(reason)}</p>
    <button type="button" class="btn-secondary empty-cta" data-run-scan-now>Run the scan again</button>
  </div>`;
}

function pollJob(jobId, bar, status, onDone, btn, prog, results, isCurrent) {
  const current = typeof isCurrent === 'function' ? isCurrent : () => true;
  const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream`);
  let closed = false;

  source.onmessage = async (event) => {
    if (closed) return;
    if (!current()) { closed = true; source.close(); return; }
    try {
      const payload = JSON.parse(event.data);
      bar.style.width = `${payload.progress || 0}%`;
      status.textContent = payload.message || '';
      if (payload.stage) renderScanStages(payload.stage);

      if (payload.status === 'complete') {
        closed = true;
        source.close();
        bar.style.width = '100%';
        status.textContent = 'Loading results…';
        const result = await fetchJobResult(jobId);
        if (!current()) return;
        status.textContent = 'Complete';
        window.setTimeout(() => {
          if (!current()) return;
          prog.classList.remove('visible');
          btn.disabled = false;
        }, 500);
        onDone(result);
      }
      if (payload.status === 'error') {
        closed = true;
        source.close();
        // An informational outcome (too new, not a stock) is not a red failure.
        bar.style.background = ({ info: 'var(--info)', warn: 'var(--warn)' })[
          (RESEARCH_FAILURE[(payload.error_detail || {}).category] || {}).tone] || 'var(--bear)';
        status.textContent = (payload.error_detail && payload.error_detail.title) || payload.error || 'Job failed';
        btn.disabled = false;
        // [PROVIDER-RESILIENCE] Structured Research failure → diagnostic state.
        if (results && results.id === 'an-results' && handleAnalyzeJobError(payload)) return;
        // A failed scan must not leave the Proposed Allocation header claiming one
        // is still in flight.
        if (results && results.id === 'picks-results') {
          const outcome = payload.error_detail && payload.error_detail.reason === 'cancelled' ? 'Scan cancelled' : 'Scan failed';
          setPicksMeta(outcome);
          // The panel shows the reason; the sidebar status names the outcome only.
          status.textContent = outcome;
          results.innerHTML = scanErrorHtml(payload);
          return;
        }
        results.innerHTML = `<div class="error-box">${escapeHtml(payload.error || 'Job failed')}</div>`;
      }
    } catch (_) {
      if (closed && current()) {
        // The result fetch or render failed after the stream ended.
        fallbackPoll(jobId, bar, status, onDone, btn, prog, results, isCurrent);
        return;
      }
      source.close();
      if (current()) fallbackPoll(jobId, bar, status, onDone, btn, prog, results, isCurrent);
    }
  };

  source.onerror = () => {
    if (closed) return;
    source.close();
    if (current()) fallbackPoll(jobId, bar, status, onDone, btn, prog, results, isCurrent);
  };
}

function fallbackPoll(jobId, bar, status, onDone, btn, prog, results, isCurrent) {
  const current = typeof isCurrent === 'function' ? isCurrent : () => true;
  const timer = window.setInterval(async () => {
    if (!current()) { window.clearInterval(timer); return; }
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const payload = await res.json();
      if (!current()) { window.clearInterval(timer); return; }
      if (!res.ok) {
        window.clearInterval(timer);
        prog.classList.remove('visible');
        btn.disabled = false;
        status.textContent = payload.error || 'Job unavailable';
        return;
      }
      bar.style.width = `${payload.progress || 0}%`;
      status.textContent = payload.message || '';
      if (payload.stage) renderScanStages(payload.stage);
      if (payload.status === 'complete') {
        window.clearInterval(timer);
        status.textContent = 'Complete';
        window.setTimeout(() => {
          if (!current()) return;
          prog.classList.remove('visible'); btn.disabled = false;
        }, 500);
        onDone(payload.result);
      }
      if (payload.status === 'error') {
        window.clearInterval(timer);
        // An informational outcome (too new, not a stock) is not a red failure.
        bar.style.background = ({ info: 'var(--info)', warn: 'var(--warn)' })[
          (RESEARCH_FAILURE[(payload.error_detail || {}).category] || {}).tone] || 'var(--bear)';
        status.textContent = (payload.error_detail && payload.error_detail.title) || payload.error || 'Job failed';
        btn.disabled = false;
        // [PROVIDER-RESILIENCE] Structured Research failure → diagnostic state.
        if (results && results.id === 'an-results' && handleAnalyzeJobError(payload)) return;
        // A failed scan must not leave the Proposed Allocation header claiming one
        // is still in flight.
        if (results && results.id === 'picks-results') {
          const outcome = payload.error_detail && payload.error_detail.reason === 'cancelled' ? 'Scan cancelled' : 'Scan failed';
          setPicksMeta(outcome);
          // The panel shows the reason; the sidebar status names the outcome only.
          status.textContent = outcome;
          results.innerHTML = scanErrorHtml(payload);
          return;
        }
        results.innerHTML = `<div class="error-box">${escapeHtml(payload.error || 'Job failed')}</div>`;
      }
    } catch (_) {
      status.textContent = 'Waiting for server…';
    }
  }, 1500);
}

/* ---------------------------------------------------------------------------
   BINDINGS
   --------------------------------------------------------------------------- */
function bindTickerKeyboardActions() {
  // Dynamically rendered ticker rows use role=button; give them the same
  // Enter/Space behavior as native buttons without changing their click path.
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const row = ev.target.closest?.('.ticker-action[role="button"][data-ticker]');
    if (!row) return;
    ev.preventDefault();
    row.click();
  });
}

function bindEvents() {
  $$('.nav-tab').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));
  // Arrow keys move between primary tabs (roving tabindex, WAI-ARIA tabs).
  $('.nav-tabs')?.addEventListener('keydown', (ev) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return;
    const tabs = $$('.nav-tab');
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    ev.preventDefault();
    const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? tabs.length - 1
      : (i + (ev.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    switchView(tabs[next].dataset.view);
  });
  bindViewLinks();
  bindBusyLabels();
  $('#an-ticker')?.addEventListener('input', () => setFieldError('#an-ticker', '#an-ticker-error', ''));
  // Portfolio form: a failed constraint turns the field's own hint into the
  // error (same place, same aria-describedby link) until the value is fixed.
  const picksForm = $('#picks-form');
  picksForm?.addEventListener('invalid', (ev) => {
    const input = ev.target;
    const hint = input.getAttribute('aria-describedby') && $(`#${input.getAttribute('aria-describedby')}`);
    input.setAttribute('aria-invalid', 'true');
    if (hint) {
      if (!hint.dataset.hint) hint.dataset.hint = hint.textContent;
      hint.textContent = input.validationMessage || hint.dataset.hint;
      hint.classList.add('is-error');
    }
  }, true);
  picksForm?.addEventListener('input', (ev) => {
    const input = ev.target;
    if (input.getAttribute('aria-invalid') !== 'true' || !input.checkValidity()) return;
    input.removeAttribute('aria-invalid');
    const hint = $(`#${input.getAttribute('aria-describedby')}`);
    if (hint && hint.dataset.hint) { hint.textContent = hint.dataset.hint; hint.classList.remove('is-error'); }
  });
  bindTickerKeyboardActions();
  $('#refresh-dashboard')?.addEventListener('click', () => loadDashboard(true));
  $('#track-refresh')?.addEventListener('click', loadTrackRecord);
  $('#track-resolve')?.addEventListener('click', resolveTrackRecord);
  $('#clear-cache')?.addEventListener('click', clearDataCache);

  // Global search — collapsed to its icon until asked for. Ctrl/Cmd-K opens it
  // from anywhere; Escape closes it; it closes on blur only when left empty, so
  // a half-typed ticker is never thrown away.
  const searchWrap = $('#global-search');
  const searchInput = $('#global-search-input');
  const searchToggle = $('#global-search-toggle');
  const setSearchOpen = (open) => {
    if (!searchWrap) return;
    searchWrap.classList.toggle('is-open', open);
    searchToggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (searchInput) searchInput.tabIndex = open ? 0 : -1;
    if (open) searchInput?.focus();
    else searchInput?.blur();
  };
  searchToggle?.addEventListener('click', () => setSearchOpen(!searchWrap.classList.contains('is-open')));
  searchInput?.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { searchInput.value = ''; setSearchOpen(false); searchToggle?.focus(); } });
  searchInput?.addEventListener('blur', () => { if (!searchInput.value.trim()) setSearchOpen(false); });
  document.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && String(ev.key).toLowerCase() === 'k') { ev.preventDefault(); setSearchOpen(true); }
  });

  $('#global-search')?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const input = $('#global-search-input');
    const raw = (input?.value || '').trim();
    if (!raw) return;
    const ticker = normalizeTicker(raw);
    if (!isAnalyzableTicker(ticker)) {
      input?.setCustomValidity('Enter a ticker symbol, e.g. NVDA or RELIANCE.NS');
      input?.reportValidity();
      setTimeout(() => input?.setCustomValidity(''), 2000);
      return;
    }
    if (input) input.value = '';
    setSearchOpen(false);
    openTickerTearSheet(ticker);
  });

  // Data-health disclosure in the top bar (click, Escape, click-away).
  const healthBtn = $('#data-health-toggle');
  const healthPanel = $('#data-health-panel');
  if (healthBtn && healthPanel) {
    const setOpen = (open) => {
      healthPanel.hidden = !open;
      healthBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    healthBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      setOpen(healthPanel.hidden);
    });
    document.addEventListener('click', (ev) => {
      if (!healthPanel.hidden && !healthPanel.contains(ev.target) && ev.target !== healthBtn) setOpen(false);
    });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') setOpen(false); });
  }

  // Retry buttons are rendered as markup; one delegated listener serves them all,
  // so no inline handler is needed (the Content-Security-Policy forbids them).
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest && ev.target.closest('[data-retry]');
    if (!btn) return;
    if (btn.dataset.retry === 'market-intel') loadMarketIntel({ resetAttempts: true });
    else if (btn.dataset.retry === 'dashboard') loadDashboard(true);
  });
  // Calculator and alert forms compute in place; Enter must not reload the page.
  ['mouseover', 'focusin'].forEach((type) => document.addEventListener(type, (ev) => {
    const el = ev.target.closest?.(TRUNCATING_TEXT);
    if (el) titleIfTruncated(el);
  }));
  document.addEventListener('submit', (ev) => {
    if (ev.target.matches && ev.target.matches('[data-no-submit]')) ev.preventDefault();
  });

  $('#picks-form')?.addEventListener('submit', runScan);
  $('#analyze-form')?.addEventListener('submit', runAnalysis);
  $('#an-ticker')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') runAnalysis(e); });

  // Theme toggle (light / dark)
  const themeBtn = $('#theme-toggle');
  if (themeBtn) {
    applyTheme(document.documentElement.getAttribute('data-theme') || 'dark');
    themeBtn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next, { persist: true });
    });
  }

  // Market toggle (US / IN)
  const syncMarketToggle = () => {
    $$('.market-toggle .mkt-btn').forEach((btn) => {
      const selected = btn.dataset.market === state.market;
      btn.classList.toggle('active', selected);
      btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
  };
  // How a bare symbol is routed depends on the market toggle; say so where
  // the symbol is typed (the backend applies the same order).
  const syncMarketHint = () => {
    const hint = document.getElementById('an-market-hint');
    if (hint) hint.textContent = state.market === 'IN'
      ? 'India mode: a plain ticker tries NSE, then BSE, then the US. Add .NS or .BO to be exact.'
      : 'US tickers as listed. For India, add .NS (NSE) or .BO (BSE).';
  };
  syncMarketToggle();
  syncMarketHint();
  $$('.market-toggle .mkt-btn').forEach((btn) => btn.addEventListener('click', syncMarketHint));
  $$('.market-toggle .mkt-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const m = btn.dataset.market;
      if (!m || m === state.market) return;
      state.market = m;
      try { localStorage.setItem('quanta_market', m); } catch (_) {}
      syncMarketToggle();
      // Also align Picks form market selector
      const spm = $('#sp-market'); if (spm) { spm.value = m; applyPicksMarketUI(m); }
      // Restart clock so the timezone label tracks the selected market
      startClock();
      clearMarketDependentPanels(m);
      loadDashboard(false);
    });
  });

  $('#sp-scan')?.addEventListener('change', () => {
    $('#custom-tickers-field').classList.toggle('hidden', $('#sp-scan').value !== 'custom');
    syncScanHint();
  });

  // The mandate hint states the sizing effect the option text no longer has room for.
  $('#sp-risk')?.addEventListener('change', applyMandateHint);
  applyMandateHint();

  // The empty Proposed Allocation panel explains what a scan returns and offers to run one,
  // so the largest area on the page is not a dead box.
  // Delegated: the failed-scan state renders its own copy of the button.
  $('#picks-results')?.addEventListener('click', (e) => {
    if (e.target.closest('[data-run-scan-now]')) $('#btn-scan')?.click();
  });

  // Picks form: keep market dropdown in sync with the global market state and
  // re-filter scan-type options whenever the market changes.
  const spmInit = $('#sp-market');
  if (spmInit) {
    spmInit.value = state.market || 'US';
    applyPicksMarketUI(spmInit.value);
    refreshSecContactNotice();
    restoreLastScan(spmInit.value);
    spmInit.addEventListener('change', () => {
      const m = spmInit.value;
      applyPicksMarketUI(m);
      refreshSecContactNotice();
      if (m !== state.market) {
        state.market = m;
        try { localStorage.setItem('quanta_market', m); } catch (_) {}
        syncMarketToggle();
        startClock();
        clearMarketDependentPanels(m);
        loadDashboard(false);
      }
    });
  }

  // Tear-sheet quick-chip helpers (ETFs included)
  // Suggestion chips are rebuilt from recent searches and today's movers, so one
  // delegated listener serves whatever is rendered.
  $('#an-quick-chips')?.addEventListener('click', (ev) => {
    const chip = ev.target.closest && ev.target.closest('[data-ticker]');
    const t = chip && chip.dataset.ticker;
    if (!t) return;
    const input = $('#an-ticker');
    if (input) input.value = t;
    runAnalysis(null, { market: chip.dataset.market || state.market });
  });
  renderResearchChips();
  wireResearchSearch();

  $$('#news-tabs .q-tab').forEach((b) => {
    b.addEventListener('click', () => {
      // aria-selected must track .is-active — it is what a screen reader
      // announces for role="tab"; the class alone conveys nothing.
      $$('#news-tabs .q-tab').forEach((x) => {
        const active = x === b;
        x.classList.toggle('is-active', active);
        x.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      state.newsGroup = b.dataset.newsGroup;
      renderNews(state.dashboard?.news?.groups || {});
    });
  });
}

if (typeof window !== 'undefined') {
  window.addEventListener('DOMContentLoaded', () => {
    applyThresholds(document);
    const cap = document.getElementById('sp-capital');
    if (cap && !cap.value) cap.value = String(thr('CAPITAL.DEFAULT_US') ?? '');
    if (cap && !cap.min) cap.min = String(thr('CAPITAL.MIN_US') ?? '');
    bindEvents();
    syncTopbarHeight();
    startClock();
    loadDashboard(false);
    const h = String(window.location.hash || '').replace('#', '').split('/')[0];
    const routed = VIEW_ALIASES[h] || h;
    // Scroll restoration is ours (per view, see _viewScroll): the browser's own
    // would land on a position from a different view's layout.
    try { if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'; } catch (_) { /* ignore */ }
    switchView(VIEWS.includes(routed) ? routed : 'dashboard', { replace: true });
  });
  // Links, bookmarks and back/forward all route through the hash.
  window.addEventListener('hashchange', () => {
    const h = String(window.location.hash || '').replace('#', '').split('/')[0];
    const routed = VIEW_ALIASES[h] || h;
    if (VIEWS.includes(routed)) switchView(routed);
  });
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    earningsAhead, earningsScopeShort, indexName, factorWord, shownSymbol, indexQuotes, indexChain, earningsCalendarNote, companyName, dirZone, growOnView, postureAge, postureAgeText, directionDial, postureScale, countdownText, zonedEpoch,
    researchIndexGroups,
    technicalSignalsHtml,
    selectionText,
    pkCardHtml, pkGauge, pkChartHtml, pkTechHtml, pkValuationHtml, pkCompareHtml, pkSma, pkMethodHtml, pkCopyText, pkCapital, pkGroupOf,
    pkFiltersText, pickScanFilters, initScanFilters, scanTypeName, pkAnalysisHtml, pkSignedPct, pkDecision, setupSummaryText,
    thr, fmtThreshold, applyThresholds, fmtRR, bindingLevels,
    researchDiagnosticHtml,
    renderResearchDiagnostic,
    handleAnalyzeJobError,
    renderGuideEvidence,
    guideDecide,
    renderGuideDecision,
    guideSize,
    guidePositionCap,
    renderGuideSize,
    guidePosture,
    renderGuidePosture,
    renderGuideFunnel,
    bindGuideReel,
    reelKeyframes,
    reelSceneAt,
    REEL_MS,
    REEL_CHAPTERS,
    REEL_FIELD,
    guideMix,
    renderGuideMix,
    guideGeometry,
    GUIDE_REGIME_WEIGHTS,
    GUIDE_SIZE,
    GUIDE_POSTURES,
    GUIDE_POSTURE_CAP,
    GUIDE_MIN_RR,
    GUIDE_EXIT_FLOOR,
    bindGuide,
    switchView,
    plainReason,
    entryTimingNote,
    decisionMeaning,
    evidencePlain,
    fmtPct,
    fmtMultiple,
    fmtPrice,
    fmtMoney,
    fmtCount,
    normalizeQuote,
    rangePosition,
    quoteBoardHtml,
    rangeMeterHtml,
    decisionSessionHtml,
    upsidePct,
    computeRR,
    rrVerdict,
    splitHeadlinesByRelevance,
    computeRequiredSectionAudit,
    buildValidationIssues,
    normalizeVolumeRequirement,
    actionMeta,
    finalAllocation,
    isFundedPick,
    renderPriceChartCard,
    scanStatusMessage,
    emptyScanExplanationHtml,
    scanVerdictHtml,
    coverageLineHtml,
    eventCheckBannerHtml,
    secContactNoticeText,
    dataDatesLineHtml,
    renderScanStages,
    collapseSetupAfterRun,
    onUserScanComplete,
    filterWords,
    concentrationHtml,
    closestToQualifyingHtml,
    renderPicksTableView,
    wirePicksTable,
    stockPicksCsv,
    passedNotFundedHtml,
    chaseTriggerText,
    chaseFilledHtml,
    guideMinTrade,
    eventHeldDiagnosticsHtml,
    gateShortfallText,
    nameListHtml,
    renderPicksBuckets,
    errorDetailsCardHtml,
    errorTypeLabel,
    tearSheetRateLimitMessage,
    tearSheetCacheBannerHtml,
    tearSheetFastModeBannerHtml,
    assetTypeMeta,
    assetApplicability,
    assetCaveatBannerHtml,
    dataQualityGate,
    dataQualityFailBannerHtml,
    summarizeValidationIssues,
    classifyFinalAction,
    whyNotBuyHtml,
    friendlyFetchError,
    computeRegimeModel,
    factorState,
    strategistBrief,
    signalCheckCounts, contextNotice, nextWeekEvent, earningsTimingWords, renderWeekCalendar, renderWhatChanged, renderBookPanel, renderMarketContext, renderTriggers, parseBand, daysFromTo, bookVsCall, regimeTriggers, historyCell, sectorHeat, weekEvents, regimeName, directionScore, yieldChangeBp, cleanHeadline, isVolIndex, isYield, isCryptoSymbol,
    driverTraderResponse,
    confidenceRead,
    confidenceBand,
    vixTone,
    participationTone,
    newsImpactLevel,
    newsTiltRead,
    impactChain,
    applyThresholds,
    scanLimit,
    evidenceValidated,
    modelFlagHtml,
    withArticle,
    scoreBandRange,
    score100,
    scheduleRegimeFollowUp,
    titleIfTruncated,
    noticeHtml,
    secContactNotice,
    scanErrorHtml,
    fmtScore100,
    researchSuggestions,
    fmtCompactMoney,
    effectiveNewsImpact,
    highImpactNews,
    pulseInterp,
    newsMeaning,
    upsideCell,
    // DOM section renderers (exercised by tests/dom_render.test.cjs via jsdom)
    renderTopHeadlines,
    renderMarketRegime,
    renderPlaybook,
    renderRiskPanel,
    renderMarketPulse,
    renderSignalCheck,
    renderMarketDrivers,
    renderBreadthPanel,
    renderTopOpportunities,
    renderOpportunityBrief,
    renderSetupStatusCard,
    attachDashboardActionHandlers,
    applyMandateHint,
    MANDATE_HINTS,
    renderTopMovers,
    renderOpenExtremes,
    clearMarketDependentPanels,
    tradingViewUrl,
    renderCatalystCalendar,
    renderNews,
    renderSourceLedger,
    renderDashboardError,
    bindTickerKeyboardActions,
    decisionEvidenceHtml,
    decisionConsistentLines,
    stripEmoji,
    scoreBar,
    renderTrackRecord,
    // Whole-view renderers (tests/dom_redesign.test.cjs)
    renderAnalysis,
    runAnalysis,
    setFieldError,
    renderPicks,
    renderConfidenceSummaryCard,
    pickCard,
    // Result identity, data truthfulness and navigation (tests/unit/audit_fixes.test.cjs)
    numOrNull,
    streetRating,
    vetoRevalidation,
    chartPeriodLabel,
    quoteFreshness,
    exchangeSession,
    pollJob,
    runScan,
    retrySafeMode,
    applyPicksMarketUI,
    nextJobToken,
    jobTokenCurrent,
    wireExpandableRows,
    state,
  };
}
