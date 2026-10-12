/* My Portfolio — read-only Robinhood holdings, QUANTA Decision, portfolio context.
 *
 * Talks only to this server (/api/portfolio/*, plus the index quotes on
 * /api/dashboard for the market comparison); never to Robinhood. Holds no
 * credentials: the account is addressed by an opaque per-process handle kept in
 * memory for this page only. Browser storage holds only the viewer's own
 * choices: the "hide amounts" boolean, and (mp-*.js) the plan inputs, watch
 * conditions and chart preferences they typed. Never broker data.
 *
 * Hide amounts replaces every account-specific figure (dollar amounts, share
 * counts, cost, gain and loss) with a mask at render time, so the numbers are
 * not in the page at all, not merely blurred. Weights, market prices and the
 * research itself stay visible, so the page can be shown to someone.
 *
 * Every block is tagged with where it came from: Account data, Market data,
 * QUANTA analysis, AI interpretation. A data failure renders as "No call",
 * never as an action.
 */
(function (root) {
  'use strict';

  const API = '/api/portfolio';
  const POLL_MS = 3000;
  const HIDE_KEY = 'quanta.holdings.hideAmounts';
  const readHidden = () => { try { return root.localStorage.getItem(HIDE_KEY) === '1'; } catch (_) { return false; } };
  const saveHidden = (on) => { try { root.localStorage.setItem(HIDE_KEY, on ? '1' : '0'); } catch (_) { /* preference only */ } };

  // ---------------------------------------------------------------- helpers
  // With amounts hidden, dollar figures and share counts inside server text
  // (guidance, notes, data issues) are masked too: every string goes through esc.
  const MONEY_TEXT = /[+\-\u2212]?\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[KMB]\b)?/g;
  const SHARES_TEXT = /\b\d[\d,]*(?:\.\d+)?(?=\s+shares?\b)/g;
  const maskText = (t) => (state.hidden ? t.replace(MONEY_TEXT, '••••').replace(SHARES_TEXT, '••') : t);
  const esc = (s) => maskText(String(s == null ? '' : s)).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const num = (v) => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
  const NA = '<span class="q-na">--</span>';
  const MASK = '<span class="hp-mask"><span aria-hidden="true">••••</span><span class="sr-only">hidden</span></span>';
  // Account-specific figures only; market data (prices, targets) is public.
  const priv = (html) => (state.hidden && html !== NA ? MASK : html);
  // One number formatter per decimal count: toLocaleString builds a new one per call,
  // which dominated repaints of long tables.
  const fixedFmts = new Map();
  const fixed = (n, dec) => {
    if (!fixedFmts.has(dec)) fixedFmts.set(dec, new Intl.NumberFormat('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }));
    return fixedFmts.get(dec).format(n);
  };
  const money = (v, dec = 0) => {
    const n = num(v);
    if (n === null) return NA;
    if (state.hidden) return MASK;
    const sign = n < 0 ? '-' : '';
    return `${sign}$${fixed(Math.abs(n), dec)}`;
  };
  const signedMoney = (v, dec = 0) => {
    const n = num(v);
    if (n === null) return NA;
    if (state.hidden) return MASK;
    return `${n > 0 ? '+' : n < 0 ? '-' : ''}$${fixed(Math.abs(n), dec)}`;
  };
  const price = (v) => {
    const n = num(v);
    if (n === null) return NA;
    const dp = Math.abs(n) > 0 && Math.abs(n) < 1 ? 4 : 2;
    return `$${fixed(n, dp)}`;
  };
  // fraction=true: 0.123 -> 12.3%.  fraction=false: value already in points.
  const pct = (v, dec = 1, fraction = true, signed = false) => {
    let n = num(v);
    if (n === null) return NA;
    if (fraction) n *= 100;
    const sign = signed && n > 0 ? '+' : '';
    return `${sign}${n.toFixed(dec)}%`;
  };
  const qty = (v) => {
    const n = num(v);
    if (n === null) return NA;
    if (state.hidden) return MASK;
    return n.toLocaleString('en-US', { maximumFractionDigits: 6 });
  };
  const dirClass = (v) => { const n = num(v); return n === null || n === 0 ? '' : (n > 0 ? 'bull' : 'bear'); };
  const timeText = (iso) => {
    if (!iso) return 'not yet';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return 'unknown';
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  };
  const list = (v) => (Array.isArray(v) ? v : (v ? [v] : [])).filter((x) => x != null && String(x).trim() !== '');

  const TAGS = { broker: 'Account data', market: 'Market data', quanta: 'QUANTA analysis', ai: 'AI interpretation' };
  const tag = (key) => `<span class="hp-src hp-src--${esc(key)}">${esc(TAGS[key] || key)}</span>`;

  // Portfolio actions. Amber is the strongest tone used: guidance here asks for a
  // review, it never sounds an alarm.
  const ACTION_TONE = {
    Accumulate: 'add', Hold: 'neutral', 'Hold / No New Money': 'hold', Trim: 'warn',
    'Exit Review': 'review', Unavailable: 'muted',
  };
  const STANCE_TONE = { Bullish: 'bull', Neutral: 'neutral', Bearish: 'warn', 'Not rated': 'muted' };
  // One display word per portfolio action, on every tab (the engine's enum
  // strings stay as sent in data attributes and the journal).
  const ACTION_LABEL = { 'Hold / No New Money': 'Hold, no new money', 'Exit Review': 'Exit review', Unavailable: 'No call' };
  const actionLabel = (a) => ACTION_LABEL[a] || a;
  // The flag every call carries until its model is validated: the same words and
  // link as Picks and Research. "Validated" is the configured status
  // (LADDER.STRONG_BUY_EVIDENCE); without config, the server's own permission.
  const evidenceValidated = (ev) => {
    if (!ev) return false;
    const need = typeof thr === 'function' ? String(thr('LADDER.STRONG_BUY_EVIDENCE') || '').toUpperCase() : '';
    return need ? String(ev.status || '').toUpperCase() === need : !!ev.permits_recommendation;
  };
  const modelFlag = (ev) => (!ev || evidenceValidated(ev) ? ''
    : `<a class="pk-valid model-flag hp-model-flag" href="#track" data-view-link="track" data-model-flag title="${esc(`Model evidence: ${ev.text || 'not validated out of sample'}. Open the evidence page.`)}">Model not yet validated</a>`);
  const actionChip = (a, ev) => `<span class="hp-action hp-action--${ACTION_TONE[a] || 'muted'}">${esc(actionLabel(a))}</span>${ev && a !== 'Unavailable' ? modelFlag(ev) : ''}`;
  const stanceChip = (s) => `<span class="hp-stance hp-stance--${STANCE_TONE[s] || 'muted'}">${esc(s)}</span>`;

  const STATE_TONE = {
    connected: 'bull', partial: 'warn', awaiting_auth: 'info', auth_expired: 'warn',
    provider_error: 'warn', disconnected: 'neutral',
  };

  // ---------------------------------------------------------------- sorting
  const GRADE_RANK = { F: 5, D: 4, C: 3, B: 2, A: 1 };
  const SORTS = {
    ticker: { label: 'Ticker', dir: 'asc', key: (r) => r.symbol },
    basis: { label: 'Cost basis', dir: 'desc', key: (r) => num(r.broker.cost_basis) },
    action: { label: 'Action', dir: 'asc', key: (r) => r.guidance.priority },
    risk: {
      label: 'Risk', dir: 'desc',
      key: (r) => {
        const g = GRADE_RANK[String((r.analysis.decision || {}).risk_grade || '').toUpperCase()] || 0;
        const vol = num((r.analysis.risk || {}).annualized_volatility) || 0;
        return g * 10 + Math.min(vol, 9.99);
      },
    },
    weight: { label: 'Weight', dir: 'desc', key: (r) => num(r.broker.weight) },
    today: { label: 'Session change', dir: 'desc', key: (r) => num(r.broker.day_change) },
    pnl: { label: 'P&L', dir: 'desc', key: (r) => num(r.broker.unrealized_pnl_pct) },
    conviction: { label: 'Conviction', dir: 'desc', key: (r) => num(r.guidance.conviction) },
  };

  function sortRows(rows, sortKey = 'action', dir) {
    const spec = SORTS[sortKey] || SORTS.action;
    const d = (dir || spec.dir) === 'asc' ? 1 : -1;
    return rows.slice().sort((a, b) => {
      const ka = spec.key(a); const kb = spec.key(b);
      if (ka === null && kb === null) return (num(b.broker.weight) || 0) - (num(a.broker.weight) || 0);
      if (ka === null) return 1;          // missing values always last
      if (kb === null) return -1;
      if (ka !== kb) return (typeof ka === 'string' ? ka.localeCompare(kb) : ka - kb) * d;
      return (num(b.broker.weight) || 0) - (num(a.broker.weight) || 0);
    });
  }

  // ---------------------------------------------------------------- state
  const state = {
    status: null, view: null, accounts: [], account: null,
    sort: 'action', dir: null, filter: null, expanded: new Set(),
    timer: null, busy: false, loaded: false, error: null, hidden: readHidden(),
    market: null, marketBusy: false, holdFilter: null,
    tab: 'overview', selected: null, tabScroll: {}, gFilter: null,
  };

  async function api(method, path, body) {
    const opts = { method, headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
    if (method !== 'GET') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-QUANTA-Request'] = '1';
      opts.body = JSON.stringify(body || {});
    }
    let resp;
    try {
      resp = await fetch(API + path, opts);
    } catch (err) {
      return { ok: false, status: 0, data: { error: transportError() } };
    }
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (!data || typeof data !== 'object') data = { error: responseError(resp.status) };
    return { ok: resp.ok, status: resp.status, data };
  }

  // Say WHICH server failed and how: "no answer" and "answered, but without My
  // Portfolio" have different fixes, and a page left open can outlive its server.
  const origin = () => { try { return root.location.host || 'this address'; } catch (_) { return 'this address'; } };
  function transportError() {
    return `Cannot reach the QUANTA server at ${origin()}. It may have been stopped or restarted. Start QUANTA again (start.ps1), then reload this page.`;
  }
  function responseError(status) {
    if (status === 404) {
      return `The server at ${origin()} has no My Portfolio. It is probably an older copy of QUANTA (started from another folder or shortcut). Stop it and start QUANTA from this project.`;
    }
    return `The QUANTA server returned an unexpected response (HTTP ${status}).`;
  }

  // ---------------------------------------------------------------- header / states
  const EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-2.9 3.9M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.8-1.3"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M2 2l20 20"/></svg>';
  const REFRESH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>';
  const UNPLUG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m19 5 3-3"/><path d="m2 22 3-3"/><path d="M6.3 20.3a2.4 2.4 0 0 0 3.4 0L12 18l-6-6-2.3 2.3a2.4 2.4 0 0 0 0 3.4Z"/><path d="M7.5 13.5 10 11"/><path d="M10.5 16.5 13 14"/><path d="m12 6 6 6 2.3-2.3a2.4 2.4 0 0 0 0-3.4l-2.6-2.6a2.4 2.4 0 0 0-3.4 0Z"/></svg>';
  const CHEV_L = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>';
  const CHEV_R = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
  const CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  // Icon controls: the accessible name is aria-label; the visible tooltip repeats
  // it for pointer and keyboard users (shown on hover and focus, Esc hides it).
  const tip = (text) => `<span class="hp-tip" aria-hidden="true">${esc(text)}</span>`;
  const iconButton = (act, label, icon, extra = '') => `<button type="button" class="icon-btn hp-icon-btn" data-hp="${act}" aria-label="${esc(label)}"${extra}>${icon}${tip(label)}</button>`;
  function privacyButtonHtml() {
    const on = !!state.hidden;
    return `<button type="button" class="icon-btn hp-icon-btn hp-eye${on ? ' is-on' : ''}" data-hp="privacy" aria-pressed="${on}" aria-label="Hide amounts">${on ? EYE_OFF : EYE}${tip(on ? 'Show amounts' : 'Hide amounts')}</button>`;
  }

  const CONN_TEXT = {
    connected: 'Connected', partial: 'Connected, some data missing', provider_error: 'Connected, last read failed',
    awaiting_auth: 'Waiting for sign-in', auth_expired: 'Sign-in expired', disconnected: 'Not connected',
  };
  function headActionsHtml(status) {
    const st = (status && status.state) || 'disconnected';
    const label = CONN_TEXT[st] || ((status && status.state_label) || 'Not connected').replace(/\s*\(read-only\)/i, '');
    // Status lives in a tooltip, not on the bar: one dot, one word.
    const chip = `<span class="hp-conn hp-conn--${STATE_TONE[st] || 'neutral'}" data-state="${esc(st)}" tabindex="0" aria-describedby="hp-conn-tip"><span class="hp-conn-dot" aria-hidden="true"></span>Robinhood<span class="hp-tip hp-ro" id="hp-conn-tip" role="tooltip">${esc(label)} · Read-only access<span class="sr-only">. Trading is off.</span></span></span>`;
    const lock = state.busy ? ' disabled' : '';
    const controls = [];
    if (st === 'connected' || st === 'partial' || st === 'provider_error') {
      if (state.accounts.length > 1) {
        controls.push(`<label class="hp-acct"><span class="sr-only">Account</span><select id="hp-account" aria-label="Robinhood account"${lock}>${state.accounts.map((a) => `<option value="${esc(a.handle)}"${a.handle === state.account ? ' selected' : ''}>${esc(a.label)}${a.can_trade_via_agent ? ' (Agentic)' : ''}</option>`).join('')}</select></label>`);
      } else if (state.accounts.length === 1) {
        controls.push(`<span class="hp-acct-name">${esc(state.accounts[0].label)}</span>`);
      }
      controls.push(privacyButtonHtml());
      controls.push(iconButton('refresh', state.busy ? 'Refreshing…' : 'Refresh', REFRESH, `${lock}${state.busy ? ' aria-busy="true"' : ''}`));
      controls.push(iconButton('disconnect', 'Disconnect Robinhood', UNPLUG, lock));
    } else if (st === 'awaiting_auth') {
      controls.push('<button type="button" class="q-btn" data-hp="check">Check again</button>');
      controls.push('<button type="button" class="q-btn q-btn--link" data-hp="disconnect">Cancel</button>');
    }
    // Disconnected and expired states show their own card with the one Connect
    // (or Reconnect) button; the header does not repeat it.
    return `${chip}${controls.join('')}`;
  }

  function stateCardHtml(status) {
    const st = (status && status.state) || 'disconnected';
    const err = status && (status.load_error || status.last_error);
    const errLine = err && err.message ? `<p class="hp-state-err" role="alert">${esc(err.message)}</p>` : '';
    const promise = `
      <ul class="hp-promise">
        <li><strong>Read-only.</strong> QUANTA reads balances, positions, cost basis, tax lots, quotes and past order history. It cannot place, review or cancel orders; those tools are blocked in code.</li>
        <li><strong>Your sign-in stays with Robinhood.</strong> You sign in on robinhood.com. QUANTA keeps the resulting access token in server memory only, never on disk, in the browser, or in logs, and forgets it when you disconnect or restart.</li>
        <li><strong>Nothing is made up.</strong> If a number cannot be read, it shows as missing. If a holding cannot be analysed, it gets no call.</li>
      </ul>
      <p class="hp-fine">Robinhood grants one broad permission to connected apps; QUANTA restricts itself to read tools. Sign-in opens robinhood.com in this tab and returns here.</p>`;
    if (st === 'awaiting_auth') {
      return `<div class="q-state hp-state" role="status"><h2 class="hp-state-title">Finish signing in to Robinhood</h2>
        <p>Complete the sign-in on robinhood.com. This page updates when Robinhood sends you back. If you closed that page or it showed an error, start again.</p>${errLine}
        <div class="q-empty-actions"><button type="button" class="q-btn q-btn--primary" data-hp="connect">Start sign-in again</button></div></div>`;
    }
    if (st === 'auth_expired') {
      return `<div class="q-state q-state--warn hp-state" role="status"><h2 class="hp-state-title">Robinhood sign-in expired</h2>
        <p>Your holdings are hidden until you reconnect. Nothing was changed in your account.</p>${errLine}
        <div class="q-empty-actions"><button type="button" class="q-btn q-btn--primary" data-hp="connect">Reconnect</button></div></div>`;
    }
    if (st === 'provider_error') {
      return `<div class="q-state q-state--warn hp-state" role="status"><h2 class="hp-state-title">Robinhood could not be read</h2>
        <p>QUANTA is connected but the last request to Robinhood failed. No portfolio is shown rather than an incomplete one.</p>${errLine}
        <div class="q-empty-actions"><button type="button" class="q-btn q-btn--primary" data-hp="refresh">Try again</button></div></div>`;
    }
    return `<div class="card hp-connect">
        <div class="hp-connect-l">
          <h2 class="hp-state-title">Connect your Robinhood account</h2>
          <p>See every holding with its cost basis and gain, what QUANTA's research engine thinks of it today, and what that means for a portfolio shaped like yours.</p>
          ${promise}${errLine}
          <div class="q-empty-actions"><button type="button" class="q-btn q-btn--primary" data-hp="connect">Connect Robinhood</button></div>
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------- portfolio sections
  function negligibleReconciliation(pf) {
    const r = pf.reconciliation || {};
    const difference = num(r.difference);
    const total = num(r.reported) ?? num((pf.overview || {}).total_value);
    return r.status === 'mismatch' && difference !== null && total > 0 && Math.abs(difference) <= 1 && Math.abs(difference) / total <= 0.0001;
  }

  // The one header timestamp. Analysis time lives with Guidance, prices with the
  // price-dependent summary; both are contextual, not header metadata.
  function updatedHtml(pf) {
    const f = pf.freshness || {};
    const a = f.analysis || {};
    const running = a.state === 'running';
    return `<span>Updated ${esc(timeText(f.portfolio_retrieved_at))}</span>${running ? `<span class="hp-updated-run">Analysing ${a.done || 0} of ${a.total || 0} holdings</span><span class="hp-progress" role="progressbar" aria-label="Analysis progress" aria-valuemin="0" aria-valuemax="${a.total || 0}" aria-valuenow="${a.done || 0}"><span style="width:${a.total ? Math.round((a.done / a.total) * 100) : 0}%"></span></span>` : ''}`;
  }

  // ---------------------------------------------------------------- data status
  // Quote and data limitations are stated ONCE, in the summary's status line;
  // tickers and technical detail wait behind one disclosure.
  const LATE = ['stale', 'unknown', 'delayed'];
  const SHARED_CODES = new Set(['PORTFOLIO_DATA_INCOMPLETE', 'ACCOUNT_DATA_UNRECONCILED']);
  const isMaterial = (i) => i.severity !== 'info' || i.code === 'RETURNS_UNAVAILABLE';
  // Issues summarised by a note (or by the section they affect), so the record
  // below does not say them twice.
  const SUMMARISED = new Set(['TOTALS_MISMATCH', 'QUOTE_STALE', 'ANALYSIS_FAILED', 'RETURNS_UNAVAILABLE']);
  const plural = (n, one, many) => (n === 1 ? one : many);

  function lateQuotes(pf) {
    const rows = pf.positions || [];
    return [...new Set([
      ...rows.filter((r) => LATE.includes((r.broker.quote_freshness || {}).status)).map((r) => r.symbol),
      ...(pf.issues || []).filter((i) => i.code === 'QUOTE_STALE' && i.scope).map((i) => i.scope),
    ])];
  }

  function dataNotes(pf) {
    const rows = pf.positions || [];
    const total = rows.length;
    const r = pf.reconciliation || {};
    const notes = [];
    const late = lateQuotes(pf);
    if (late.length) {
      notes.push({
        warn: true,
        html: `${late.length === total ? `Quotes for all ${total} holdings are` : `${late.length} of ${total} quotes are`} delayed or unverified, so QUANTA gives ${plural(late.length, 'that holding', 'them')} no call until prices refresh.`,
        detail: late.map((s) => { const row = rows.find((x) => x.symbol === s); const t = row && (row.broker.quote_freshness || {}).text; return `${s}${t ? ` (${t.charAt(0).toLowerCase()}${t.slice(1)})` : ''}`; }).join(', '),
      });
    }
    const shared = new Map();
    rows.forEach((x) => (x.guidance.blocks || []).forEach((b) => { if (SHARED_CODES.has(b.code)) shared.set(b.text, b.code); }));
    const unreconciled = [...shared.values()].includes('ACCOUNT_DATA_UNRECONCILED');
    const difference = num(r.difference);
    if (r.status === 'mismatch' && !negligibleReconciliation(pf)) {
      notes.push({ warn: true, recon: true, html: `Positions plus cash differ from Robinhood’s total${difference !== null ? ` by ${money(Math.abs(difference), 2)}` : ''}${unreconciled ? '; new money is on hold until it reconciles' : ''}.` });
    } else if (r.status === 'unverifiable') {
      notes.push({ warn: true, recon: true, html: 'Totals could not be cross-checked: a balance or price is missing.' });
    } else if (unreconciled) {
      notes.push({ warn: true, html: 'Account data has not fully reconciled; new money is on hold until it does.' });
    }
    const noCall = rows.filter((x) => x.guidance.status !== 'ok' && !late.includes(x.symbol));
    if (noCall.length) {
      notes.push({
        warn: false,
        html: noCall.length <= 3 ? `No analysis for ${esc(noCall.map((x) => x.symbol).join(', '))}, so no call. A missing analysis is not a view on the stock.`
          : `${noCall.length} holdings have no analysis, so no call. A missing analysis is not a view on the stock.`,
        detail: noCall.map((x) => `${x.symbol}: ${x.guidance.unavailable_reason || x.guidance.key_reason}`).join(' · '),
      });
    }
    const coverage = [...shared.entries()].filter(([, code]) => code === 'PORTFOLIO_DATA_INCOMPLETE').map(([text]) => text);
    if (coverage.length) notes.push({ warn: false, html: `${esc(coverage.join('; '))}. New money waits on ${plural(coverage.length, 'it', 'them')}; ${plural(coverage.length, 'it is not a view', 'they are not views')} on any stock.` });
    (pf.issues || []).filter((i) => isMaterial(i) && !SUMMARISED.has(i.code) && i.code !== 'COST_BASIS_MISSING')
      .forEach((i) => notes.push({ warn: i.severity === 'error', html: esc(i.message) }));
    return notes;
  }

  // Market closure is not staleness: a last-session close on a closed market is current.
  function pricesText(pf) {
    const f = pf.freshness || {};
    const st = (pf.positions || []).map((r) => (r.broker.quote_freshness || {}).status);
    if (!f.oldest_quote) return 'Quote times not reported';
    const session = st.length && st.every((s) => s === 'live') ? 'live'
      : st.length && st.every((s) => s === 'live' || s === 'last_close') && st.includes('last_close') ? 'latest close; market closed' : '';
    return `Prices as of ${esc(timeText(f.oldest_quote))}${session ? ` <span class="hp-muted">(${session})</span>` : ''}`;
  }

  function dataStatusHtml(pf) {
    const r = pf.reconciliation || {};
    const difference = num(r.difference);
    const negligible = negligibleReconciliation(pf);
    const notes = dataNotes(pf);
    const warn = notes.filter((n) => n.warn);
    const lead = warn[0] || notes[0];
    const more = notes.length - (lead ? 1 : 0);
    const recon = r.status === 'ok' ? 'Positions plus cash match Robinhood’s total.'
      : r.status === 'mismatch' ? `Positions plus cash differ from Robinhood’s total${difference !== null ? ` by ${money(Math.abs(difference), 2)}` : ''}.`
        : 'Totals could not be cross-checked: a balance or price is missing.';
    const record = (pf.issues || []).filter((i) => !SUMMARISED.has(i.code))
      .map((i) => `<li><span class="hp-issue-scope">${esc(i.scope === 'portfolio' || i.scope === 'account' ? 'Portfolio' : i.scope)}</span> ${esc(i.message)}</li>`);
    const items = [
      // The lead is already on screen; the record adds only what it names.
      ...notes.filter((n) => !n.recon).map((n) => (n === lead
        ? (n.detail ? `<li class="${n.warn ? 'is-warn' : ''}"><span class="hp-issue-scope">Affected</span> ${esc(n.detail)}</li>` : '')
        : `<li class="${n.warn ? 'is-warn' : ''}">${n.html}${n.detail ? `<span class="hp-sub">${esc(n.detail)}</span>` : ''}</li>`)),
      `<li class="hp-recon${r.status === 'mismatch' && !negligible ? ' is-warn' : ''}">${recon}${negligible && r.status === 'mismatch' ? ' Small difference; original figures retained.' : ''}</li>`,
      ...record,
    ];
    return `<div class="hp-dq${warn.length ? ' is-warn' : ''}">
      <span class="hp-dq-dot" aria-hidden="true"></span>
      <p class="hp-dq-msg">${lead ? `<span class="hp-dq-lead${lead.recon ? ' hp-recon-note' : ''}">${lead.html}</span>` : '<span class="hp-dq-lead">Account data reconciled.</span>'}${more ? ` <span class="hp-muted">${more} more ${plural(more, 'note', 'notes')}</span>` : ''}</p>
      <p class="hp-dq-prices">${pricesText(pf)}</p>
      <details class="hp-issues" data-hp-disclosure="issues"><summary>Data details</summary><ul class="hp-issue-list">${items.join('')}</ul></details>
    </div>`;
  }

  // ---------------------------------------------------------------- shared readings
  // Every figure below is the server's own output, or an exact split of one: a
  // per-holding part computed from the same inputs with the same arithmetic,
  // which adds back up to the published total. Nothing here is a new measure,
  // score or threshold. Where a split does not reconcile, it is not drawn.
  const dayText = (iso) => {
    const d = new Date(`${iso}T12:00:00`);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  };
  const nyDate = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
    } catch (_) { return null; }
  };
  const signedPts = (v, dec = 2) => { const n = num(v); return n === null ? NA : `${n > 0 ? '+' : ''}${n.toFixed(dec)}`; };
  const posValue = (r) => num(r.broker.market_value);
  const limitedSector = (name) => !['Cash', 'Unclassified', 'Other categories', 'Unreported'].includes(name);

  // The session the account figures describe: "Today" only when every quote is live.
  function sessionOf(pf) {
    const rows = pf.positions || [];
    const dates = [...new Set(rows.map((r) => nyDate(r.broker.quote_as_of)).filter(Boolean))];
    const st = rows.map((r) => (r.broker.quote_freshness || {}).status);
    const live = st.length > 0 && st.every((s) => s === 'live');
    const date = dates.length === 1 ? dates[0] : null;
    return { date, live, label: live ? 'Today' : `Latest session${date ? ` · ${dayText(date)}` : ''}` };
  }

  function sectorOf(pf, symbol) {
    const hit = ((pf.metrics || {}).sectors || []).find((s) => (s.symbols || []).includes(symbol));
    if (hit) return hit.sector;
    const r = (pf.positions || []).find((x) => x.symbol === symbol);
    return (r && r.market && r.market.sector) || 'Unclassified';
  }

  // Day change by holding: the parts of overview.day_change, in dollars and in
  // points of the prior-close account value (the same base as day_change_pct).
  function moveParts(pf) {
    const o = pf.overview || {};
    const all = (pf.positions || []).map((r) => ({ symbol: r.symbol, sector: sectorOf(pf, r.symbol), v: num(r.broker.day_change) }));
    const total = num(o.total_value);
    const dc = num(o.day_change);
    const base = dc !== null && total !== null && total - dc > 0 ? total - dc : null;
    const rows = all.filter((x) => x.v !== null).map((x) => ({ ...x, pts: base ? x.v / base * 100 : null }))
      .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    return { rows, missing: all.filter((x) => x.v === null).map((x) => x.symbol), dc, pct: num(o.day_change_pct), base };
  }

  // Beta by holding: value × beta ÷ account value, the terms of the value-weighted
  // beta. Whatever the published account beta holds beyond the measured terms is
  // the unmeasured holdings (the server counts them at the covered average).
  function betaParts(pf) {
    const m = pf.metrics || {};
    const total = num(m.total_value) ?? num((pf.overview || {}).total_value);
    const pb = num((m.beta || {}).portfolio_beta);
    if (!total || total <= 0) return null;
    const parts = (pf.positions || [])
      .map((r) => ({ symbol: r.symbol, v: posValue(r), beta: num((r.market || {}).beta) }))
      .filter((p) => p.v !== null && p.beta !== null)
      .map((p) => ({ symbol: p.symbol, part: p.v * p.beta / total }))
      .sort((a, b) => b.part - a.part);
    if (!parts.length || parts.some((p) => p.part < 0)) return null;
    const measured = parts.reduce((s, p) => s + p.part, 0);
    const unmeasured = pb !== null && pb - measured > 0.005 ? pb - measured : 0;
    const missing = (pf.positions || []).filter((r) => posValue(r) !== null && num((r.market || {}).beta) === null).map((r) => r.symbol);
    return { parts, measured, unmeasured, missing, total: pb !== null ? pb : measured };
  }

  // Loss at model stops by holding: quantity × (price − stop), the exact terms of
  // metrics.stops.loss. If the terms do not add up to the published loss, no split.
  function stopParts(pf) {
    const m = pf.metrics || {};
    const s = m.stops || {};
    const total = num(m.total_value) ?? num((pf.overview || {}).total_value);
    const loss = num(s.loss);
    if (loss === null || !total) return null;
    const parts = [];
    for (const r of pf.positions || []) {
      const d = (r.analysis || {}).decision || {};
      if (!d.action || d.action === 'INVALID') continue;
      const stop = num((d.levels || {}).stop);
      const price = num(r.broker.last_price);
      const q = num(r.broker.quantity);
      if (!stop || !price || q === null || posValue(r) === null || stop >= price) continue;
      parts.push({ symbol: r.symbol, loss: q * (price - stop) });
    }
    const sum = parts.reduce((t, p) => t + p.loss, 0);
    if (Math.abs(sum - loss) > Math.max(1, Math.abs(loss) * 0.005)) return { parts: null, loss, total };
    return { parts: parts.sort((a, b) => b.loss - a.loss).map((p) => ({ ...p, pct: p.loss / total })), loss, total };
  }

  // Index quotes from this server's market tape, compared only within one session.
  const INDEXES = ['SPY', 'QQQ', 'DIA', 'IWM'];
  function marketCompare(pf) {
    const mk = state.market;
    const quotes = mk && Array.isArray(mk.quotes) ? mk.quotes : null;
    if (!quotes) return { status: mk ? 'unavailable' : 'loading' };
    const idx = INDEXES.map((s) => quotes.find((q) => q && q.symbol === s)).filter((q) => q && num(q.change_pct) !== null);
    const spy = idx.find((q) => q.symbol === 'SPY');
    if (!spy) return { status: 'unavailable' };
    const ses = sessionOf(pf);
    const mine = num((pf.overview || {}).day_change_pct);
    if (mine === null) return { status: 'unavailable' };
    const same = idx.filter((q) => ses.date && q.quote_date === ses.date);
    if (!same.includes(spy)) return { status: 'mismatch', market: spy.quote_date || null, mine: ses.date };
    return { status: 'ok', idx: same, spy, mine };
  }

  // ---------------------------------------------------------------- overview
  // Robinhood names its realized window ("3month", "year"); say it in words.
  const PERIOD_TEXT = { day: 'today', week: 'past week', month: 'past month', '3month': 'past 3 months', year: 'past year', all: 'all time', ytd: 'year to date', YTD: 'year to date' };
  const periodText = (p) => PERIOD_TEXT[p] || String(p || '').replace(/(\d)month/, '$1 months');
  const TRI_UP = '<svg class="hp-tri" viewBox="0 0 10 10" aria-hidden="true"><path d="M5 1.5 9 8.5H1z"/></svg>';
  const TRI_DOWN = '<svg class="hp-tri" viewBox="0 0 10 10" aria-hidden="true"><path d="M5 8.5 1 1.5h8z"/></svg>';

  // The summary strip: portfolio value with the session's move under it, then unrealized,
  // realized and cash. Realized and unrealized are separate cells; neither is netted.
  function overviewHtml(pf, opts = {}) {
    const o = pf.overview || {};
    const u = o.unrealized_pnl || {};
    const m = pf.metrics || {};
    const rows = pf.positions || [];
    const noBasis = rows.filter((r) => r.broker.cost_basis == null && r.broker.average_cost == null).map((r) => r.symbol);
    const basisTotal = rows.reduce((t, r) => t + (num(r.broker.cost_basis) !== null && num(r.broker.unrealized_pnl) !== null ? r.broker.cost_basis : 0), 0);
    const cover = u.coverage != null && u.coverage < 0.999 ? (noBasis.length ? `no cost basis for ${esc(noBasis.join(', '))}` : `${pct(u.coverage, 0)} of holdings`) : '';
    const cmp = marketCompare(pf);
    const ses = sessionOf(pf);
    const dc = num(o.day_change);
    const kpi = (k, v, sub, cls = '', lead = false, extra = '') => `<div class="hp-kpi${lead ? ' hp-kpi--lead' : ''}"><dt>${k}</dt><dd class="hp-kpi-v ${cls}">${v}</dd>${extra}<dd class="hp-kpi-s">${sub}</dd></div>`;
    const bp = num(o.buying_power);
    const invested = num(o.invested_value);
    // The session's move, as a small arrow line under the value: direction by shape and sign, not colour alone.
    const today = dc === null
      ? '<dd class="hp-today">Previous close not reported</dd>'
      : `<dd class="hp-today ${dirClass(dc)}">${dc > 0 ? TRI_UP : dc < 0 ? TRI_DOWN : ''}<b>${signedMoney(dc, 2)}</b>${o.day_change_pct != null ? ` <b>${priv(pct(o.day_change_pct, 2, false, true))}</b>` : ''} <span>${ses.live ? 'today' : 'last session'}</span>${cmp.status === 'ok' ? `<span class="hp-kpi-vs">S&amp;P 500 ${pct(cmp.spy.change_pct, 2, false, true)}</span>` : ''}</dd>`;
    const risk = o.risk || {};
    const drivers = (risk.drivers || []).length;
    const inner = `<h2 class="q-head-title sr-only" id="hp-ov-h">Portfolio Overview</h2>
      <dl class="hp-kpis">
        ${kpi('Total value', money(o.total_value, 2), `${esc(o.holdings != null ? String(o.holdings) : '--')} holdings${invested !== null ? ` · cash ${money(o.cash, 0)}` : ''}`, '', true, today)}
        ${kpi('Return on cost', u.total != null && basisTotal > 0 ? priv(pct(u.total / basisTotal, 1, true, true)) : NA, u.total != null ? `${signedMoney(u.total, 0)} unrealized${cover ? ` · ${cover}` : ''}` : 'No cost basis reported', dirClass(u.total))}
        ${kpi('Realized P&amp;L', o.realized_pnl == null ? NA : signedMoney(o.realized_pnl, 2), o.realized_pnl == null ? 'Not reported by Robinhood' : `${esc(periodText(o.realized_pnl_period)).replace(/^./, (c) => c.toUpperCase())}, closed positions`, dirClass(o.realized_pnl))}
        ${kpi('Cash', pct(m.cash_weight, 1), `${money(o.cash, 2)}${bp !== null ? ` · buying power ${money(bp, 0)}` : ''}`)}
        ${kpi('Risk', esc(risk.level || 'Not rated'), drivers ? `${drivers} ${plural(drivers, 'driver', 'drivers')}: ${esc(String(risk.drivers[0]).replace(/ of the portfolio$/, ''))}` : esc(risk.note || 'QUANTA rule, drivers listed in Review'), /high|elevated/i.test(risk.level || '') ? 'is-warn' : '')}
      </dl>
      ${dataStatusHtml(pf)}`;
    if (opts.bare) return inner;
    return `<section class="q-panel hp-panel hp-overview" aria-labelledby="hp-ov-h">${inner}</section>`;
  }

  // Chart geometry never changes the source weights or policy decisions.
  const chartPercent = (v, max = 1) => Math.max(0, Math.min(100, v / max * 100)).toFixed(3);

  // What moved the account in the session its figures describe, and how that
  // compares with the market over the same session. Diverging bars from zero.
  function mvRow(label, v, max, { value = '', cls = '', note = '' } = {}) {
    const n = num(v);
    const w = n === null || !(max > 0) ? 0 : Math.min(50, Math.abs(n) / max * 50);
    const side = n !== null && n < 0 ? `right:50%` : 'left:50%';
    return `<li class="hp-mv${cls ? ` ${cls}` : ''}"><span class="hp-mv-k">${label}</span><span class="hp-mv-bar" aria-hidden="true"><i class="${n !== null && n < 0 ? 'is-down' : 'is-up'}" style="${side};width:${w.toFixed(3)}%"></i></span>${value}<span class="hp-mv-p ${dirClass(n)}">${note}</span></li>`;
  }

  function todayHtml(pf) {
    const mv = moveParts(pf);
    if (!mv.rows.length) return '';
    const ses = sessionOf(pf);
    const cmp = marketCompare(pf);
    const pb = num(((pf.metrics || {}).beta || {}).portfolio_beta);
    const hasPts = mv.rows.every((x) => x.pts !== null);
    // Holdings: the largest movers by absolute impact; the rest net into one row.
    const top = mv.rows.slice(0, 6);
    const rest = mv.rows.slice(6);
    const restV = rest.reduce((t, x) => t + x.v, 0);
    const restP = hasPts ? rest.reduce((t, x) => t + x.pts, 0) : null;
    const scaleOf = (xs) => Math.max(...xs.map((x) => Math.abs(x)), 0);
    const hMax = scaleOf([...top.map((x) => (hasPts ? x.pts : x.v)), rest.length ? (hasPts ? restP : restV) : 0]);
    const holdRow = (label, v, p) => mvRow(label, hasPts ? p : v, hMax, {
      value: `<span class="hp-mv-v ${dirClass(v)}">${signedMoney(v)}</span>`, note: hasPts ? `${signedPts(p)}` : '',
    });
    const holdings = `<ul class="hp-mv-list">${top.map((x) => holdRow(`<button type="button" class="hp-mv-b" data-hp-inspect="${esc(x.symbol)}" aria-label="${esc(`${x.symbol} details`)}"><span class="hp-sym">${esc(x.symbol)}</span></button>`, x.v, x.pts)).join('')}${rest.length ? holdRow(`<span class="hp-muted">${rest.length} ${plural(rest.length, 'other', 'others')}</span>`, restV, restP) : ''}</ul>`;

    let left = '';
    let reading = '';
    if (cmp.status === 'ok') {
      const implied = pb !== null ? pb * num(cmp.spy.change_pct) : null;
      const rows = [
        { label: '<strong>Your portfolio</strong>', v: cmp.mine, cls: 'is-mine' },
        implied !== null ? { label: `<span title="Account beta × S&amp;P 500 move: a one-factor estimate">Beta-implied</span>`, v: implied, cls: 'is-implied' } : null,
        ...cmp.idx.map((q) => ({ label: esc(q.label || q.symbol), v: num(q.change_pct) })),
      ].filter(Boolean);
      const max = scaleOf(rows.map((x) => x.v));
      const diff = cmp.mine - num(cmp.spy.change_pct);
      left = `<h3 class="hp-h3">Market vs portfolio <span>% change, same session</span></h3>
        <ul class="hp-mv-list is-pct">${rows.map((x) => mvRow(x.label, x.v, max, { cls: x.cls, note: pct(x.v, 2, false, true) })).join('')}</ul>
        ${implied !== null ? `<p class="hp-fine">At account beta ${pb.toFixed(2)}, the S&amp;P 500 move implies about ${pct(implied, 2, false, true)}; the other ${signedPts(cmp.mine - implied)} pts came from the holdings themselves. A one-factor estimate, not attribution.</p>` : ''}`;
      const spy = num(cmp.spy.change_pct);
      reading = `The portfolio ${cmp.mine >= 0 ? 'gained' : 'lost'} ${pct(Math.abs(cmp.mine), 2, false)} ${Math.sign(cmp.mine) !== Math.sign(spy) && spy !== 0 ? 'while' : 'as'} the S&amp;P 500 ${spy >= 0 ? 'rose' : 'fell'} ${pct(Math.abs(spy), 2, false)}: <strong>${Math.abs(diff) < 0.005 ? 'in line with the market' : `${Math.abs(diff).toFixed(2)} pts ${diff > 0 ? 'ahead of' : 'behind'} the market`}</strong>.`;
    } else {
      // No same-session index quotes: attribute the move by sector instead.
      const bySector = new Map();
      mv.rows.forEach((x) => { const s = bySector.get(x.sector) || { v: 0, p: 0 }; s.v += x.v; s.p += x.pts || 0; bySector.set(x.sector, s); });
      const secRows = [...bySector.entries()].sort((a, b) => Math.abs(b[1].v) - Math.abs(a[1].v));
      const max = scaleOf(secRows.map(([, s]) => (hasPts ? s.p : s.v)));
      left = `<h3 class="hp-h3">By sector <span>${hasPts ? 'impact on account, pts' : 'impact on account'}</span></h3>
        <ul class="hp-mv-list">${secRows.map(([name, s]) => mvRow(esc(name), hasPts ? s.p : s.v, max, { value: `<span class="hp-mv-v ${dirClass(s.v)}">${signedMoney(s.v)}</span>`, note: hasPts ? signedPts(s.p) : '' })).join('')}</ul>
        <p class="hp-fine">${cmp.status === 'mismatch' ? `No market comparison: index quotes are from ${cmp.market ? esc(dayText(cmp.market)) : 'another session'}, holdings from ${cmp.mine ? esc(dayText(cmp.mine)) : 'a different or mixed session'}.` : cmp.status === 'loading' ? 'Loading index quotes for a market comparison…' : 'Index quotes are unavailable, so there is no market comparison.'}</p>`;
      if (mv.pct !== null) reading = `The portfolio ${mv.pct >= 0 ? 'gained' : 'lost'} ${pct(Math.abs(mv.pct), 2, false)}.`;
    }
    const lead = mv.rows[0];
    const second = mv.rows[1];
    const driver = lead && hasPts && Math.abs(lead.pts) >= 0.005
      ? ` <span class="hp-td-driver"><span class="hp-sym">${esc(lead.symbol)}</span> ${lead.v >= 0 ? 'added' : 'took'} ${signedPts(lead.pts)} pts${second && Math.abs(second.pts) >= 0.005 ? `, <span class="hp-sym">${esc(second.symbol)}</span> ${signedPts(second.pts)}` : ''}.</span>` : '';
    return `<section class="q-panel hp-panel hp-today" aria-labelledby="hp-td-h">
      <header class="q-head"><div class="q-head-l"><h2 class="q-head-title" id="hp-td-h">Contribution</h2><p class="q-head-sub">Actual account change · ${esc(ses.label)} · ${signedMoney(mv.dc, 2)}${mv.pct !== null ? ` (${priv(pct(mv.pct, 2, false, true))})` : ''}</p></div><div class="q-head-r">${tag('broker')}${cmp.status === 'ok' ? tag('market') : ''}</div></header>
      <div class="q-body">
        ${reading || driver ? `<p class="hp-td-read">${reading}${driver}</p>` : ''}
        <div class="hp-td">
          <div class="hp-td-l">${left}</div>
          <div class="hp-td-r"><h3 class="hp-h3">By holding <span>${hasPts ? 'impact on account, pts' : 'impact on account'}</span></h3>${holdings}
            <p class="hp-fine">Each holding’s change since the previous close as reported by Robinhood, divided by the prior-close account value; the parts add up to the account’s change.${mv.missing.length ? ` No previous close for ${esc(mv.missing.join(', '))}.` : ''}</p></div>
        </div>
      </div>
    </section>`;
  }

  // ---------------------------------------------------------------- risk
  // "What can hurt this portfolio": each measure beside the holdings that carry
  // it, against a real reference only (the market at beta 1.00; QUANTA's stop
  // budget). Volatility has no reference, so it gets a spread, not a gauge.
  function stackHtml({ segs, max, ref, refLabel, over, label, extra = '' }) {
    const w = (v) => chartPercent(v, max);
    const shown = segs.filter((s) => s.v > 0);
    return `<div class="hp-stack${over ? ' is-over' : ''}" role="img" aria-label="${esc(label)}">
      <div class="hp-stack-track">${shown.map((s) => `<i class="hp-seg${s.cls ? ` ${s.cls}` : ''}" style="width:${w(s.v)}%" title="${esc(s.title)}">${s.name && s.v / max >= 0.085 ? `<span>${esc(s.name)}</span>` : ''}</i>`).join('')}${extra}
        ${ref !== null ? `<b class="hp-stack-ref" style="left:${w(ref)}%"><span>${esc(refLabel)}</span></b>` : ''}</div>
    </div>`;
  }
  const partsLine = (items) => `<p class="hp-rk-parts">${items.join('<span aria-hidden="true"> · </span>')}</p>`;

  function exposureHtml(pf) {
    const m = pf.metrics || {};
    const pol = pf.policy || {};
    const b = m.beta || {};
    const v = m.volatility || {};
    const s = m.stops || {};
    const e = m.earnings || {};
    const risk = (pf.overview || {}).risk || {};
    const covered = (c) => (c != null && c < 0.9995 ? `; ${pct(c, 1)} covered` : '');
    const acctBeta = num(b.portfolio_beta);
    const eqBeta = num(b.equity_beta);
    const beta = acctBeta !== null ? acctBeta : eqBeta;
    const vol = num(v.volatility);
    const loss = num(s.loss);
    const lossPct = num(s.loss_pct);
    const budget = num(pol.stop_risk_budget);
    const bp = betaParts(pf);
    const sp = stopParts(pf);
    const kpi = (key, label, value, reading, visual = '') => `<div class="hp-rk" data-risk="${key}"><dt>${label}</dt><dd class="hp-rk-v">${value}</dd><dd class="hp-rk-t">${reading}</dd><dd class="hp-rk-g">${visual}</dd></div>`;

    // Market sensitivity: the account beta as a stack of holdings against 1.00.
    let betaVisual = '';
    if (beta !== null && beta >= 0 && bp && acctBeta !== null) {
      const max = Math.max(1.5, beta * 1.15);
      const top = bp.parts.slice(0, 5);
      const rest = bp.parts.slice(5);
      const restV = rest.reduce((t, x) => t + x.part, 0);
      const segs = [...top.map((x) => ({ v: x.part, name: x.symbol, title: `${x.symbol}: ${x.part.toFixed(2)} of ${acctBeta.toFixed(2)} (${pct(x.part / acctBeta, 0)})` })),
        rest.length ? { v: restV, name: `+${rest.length}`, cls: 'is-rest', title: `${rest.length} more ${plural(rest.length, 'holding', 'holdings')}: ${restV.toFixed(2)}` } : null,
        bp.unmeasured ? { v: bp.unmeasured, name: '', cls: 'is-unmeasured', title: `Not measured (${bp.missing.join(', ') || 'holdings without a beta'}): ${bp.unmeasured.toFixed(2)}, counted at the covered average` } : null].filter(Boolean);
      betaVisual = `${stackHtml({ segs, max, ref: 1, refLabel: 'Market 1.00', over: false, label: `Account beta ${acctBeta.toFixed(2)} by holding: ${segs.map((x) => x.title).join('; ')}. Market reference 1.00.` })}
        ${partsLine([...top.slice(0, 3).map((x) => `<span class="hp-sym">${esc(x.symbol)}</span> ${x.part.toFixed(2)}`), bp.unmeasured ? `<span class="hp-muted">not measured ${bp.unmeasured.toFixed(2)}${bp.missing.length ? ` (${esc(bp.missing.join(', '))})` : ''}</span>` : ''].filter(Boolean))}`;
    } else if (beta !== null && beta >= 0) {
      betaVisual = stackHtml({ segs: [{ v: beta, name: '', title: `Beta ${beta.toFixed(2)}` }], max: Math.max(1.5, beta * 1.15), ref: 1, refLabel: 'Market 1.00', label: `Beta ${beta.toFixed(2)}; market reference 1.00` });
    }
    const betaReading = beta === null ? 'Not measured'
      : `${beta >= 1.005 ? `${((beta - 1) * 100).toFixed(0)}% more` : beta <= 0.995 ? `${((1 - beta) * 100).toFixed(0)}% less` : 'As'} sensitive ${Math.abs(beta - 1) < 0.005 ? 'as' : 'than'} the market: ≈${(beta * 10).toFixed(1)}% per 10% market move${acctBeta !== null && eqBeta !== null ? `. Holdings alone ${eqBeta.toFixed(2)}; cash at zero` : ''}${covered(b.coverage)}`;

    // Loss at model stops: the holdings that carry it, against the stop budget.
    const uncovered = s.coverage != null && s.coverage < 0.9995 ? 1 - s.coverage : 0;
    const noStop = (pf.positions || []).filter((r) => posValue(r) !== null && sp && sp.parts && !sp.parts.some((x) => x.symbol === r.symbol)).map((r) => r.symbol);
    let stopVisual = '';
    if (loss !== null && lossPct !== null && lossPct >= 0 && budget > 0) {
      const max = Math.max(budget * 1.3, lossPct * 1.1);
      const segs = sp && sp.parts
        ? sp.parts.map((x) => ({ v: x.pct, name: x.symbol, title: `${x.symbol}: ${pct(x.pct, 2)} of the account (${pct(x.loss / loss, 0)} of the stop loss)` }))
        : [{ v: lossPct, name: '', title: `${pct(lossPct, 1)} of the account` }];
      stopVisual = `${stackHtml({ segs, max, ref: budget, refLabel: `Budget ${pct(budget, 0)}`, over: lossPct >= budget, label: `Loss at model stops ${pct(lossPct, 1)} of the account by holding: ${segs.map((x) => x.title).join('; ')}. QUANTA stop budget ${pct(budget, 0)}.` })}
        ${partsLine([...(sp && sp.parts ? sp.parts.slice(0, 3).map((x) => `<span class="hp-sym">${esc(x.symbol)}</span> ${pct(x.loss / loss, 0)}`) : []), noStop.length ? `<span class="hp-muted">no model stop: ${esc(noStop.join(', '))}</span>` : ''].filter(Boolean))}`;
    }
    const stopReading = loss === null ? 'No model stop levels'
      : `Hypothetical: ${pct(lossPct, 1)} of account if every stop were hit at once${budget > 0 && lossPct !== null ? `; ${pct(lossPct / budget, 0)} of the ${pct(budget, 0)} risk budget` : ''}${uncovered ? `; ${pct(uncovered, 1)} has no stop` : ''}`;

    // Volatility: each holding's own volatility beside the combined figure.
    const peers = (pf.positions || []).map((r) => ({ symbol: r.symbol, w: num(r.broker.weight), vol: num(((r.analysis || {}).risk || {}).annualized_volatility) }))
      .filter((p) => p.vol !== null && p.vol >= 0);
    const wsum = peers.reduce((t, p) => t + (p.w || 0), 0);
    const wavg = wsum > 0 ? peers.reduce((t, p) => t + (p.w || 0) * p.vol, 0) / wsum : null;
    let volVisual = '';
    if (vol !== null && peers.length) {
      const max = Math.max(0.1, vol, ...peers.map((p) => p.vol)) * 1.08;
      const x = (n) => chartPercent(n, max);
      const lanes = [];
      const dots = peers.slice().sort((a, b2) => a.vol - b2.vol).map((p) => {
        let lane = 0;
        while ((lanes[lane] || []).some((q) => Math.abs(q - p.vol) / max < 0.035)) lane += 1;
        (lanes[lane] = lanes[lane] || []).push(p.vol);
        return `<i class="hp-dot" style="left:${x(p.vol)}%;--lane:${Math.min(lane, 3)}" title="${esc(`${p.symbol}: ${pct(p.vol, 0)} a year`)}"></i>`;
      });
      const hi = peers.slice().sort((a, b2) => b2.vol - a.vol)[0];
      volVisual = `<div class="hp-spread" role="img" aria-label="${esc(`Portfolio volatility ${pct(vol, 1)}; individual holdings ${peers.map((p) => `${p.symbol} ${pct(p.vol, 0)}`).join(', ')}${wavg !== null ? `; weight-averaged ${pct(wavg, 1)}` : ''}`)}">
          <span class="hp-spread-axis"></span>${dots.join('')}
          ${wavg !== null ? `<b class="hp-spread-avg" style="left:${x(wavg)}%"></b>` : ''}
          <b class="hp-spread-port" style="left:${x(vol)}%"><span>Portfolio ${pct(vol, 0)}</span></b>
        </div>
        ${partsLine([`<i class="hp-key is-port" aria-hidden="true"></i>Portfolio ${pct(vol, 0)}`, wavg !== null ? `<i class="hp-key" aria-hidden="true"></i>weight-average of holdings ${pct(wavg, 0)}` : '', `highest <span class="hp-sym">${esc(hi.symbol)}</span> ${pct(hi.vol, 0)}`, '<span class="hp-muted">dots: each holding’s own</span>'].filter(Boolean))}`;
    }
    const volReading = vol === null ? 'Not enough shared price history'
      : `Modeled on today’s holdings over the past ${v.days || 'year of'} trading days, not the account’s own history. Historical variability, not a loss limit${wavg !== null && wavg > vol ? `; below the ${pct(wavg, 0)} weight-average of the holdings because they do not all move together` : ''}${covered(v.coverage)}`;

    const kpis = [
      kpi('beta', 'Market sensitivity', beta !== null ? beta.toFixed(2) : NA, betaReading, betaVisual),
      kpi('stops', 'Loss at model stops', loss !== null ? money(loss) : NA, stopReading, stopVisual),
      kpi('volatility', 'Annualized volatility', vol !== null ? pct(vol, 1) : NA, volReading, volVisual),
    ];
    const threshold = num(pol.correlation_threshold);
    const weights = m.weights || {};
    const pairs = (v.correlated_pairs || []).slice().sort((x, y) => y.correlation - x.correlation);
    const pairLi = (p) => {
      const combined = num(weights[p.a]) !== null && num(weights[p.b]) !== null ? weights[p.a] + weights[p.b] : null;
      return `<li class="hp-correlation"><span class="hp-pair"><span class="hp-sym">${esc(p.a)}</span><span aria-hidden="true">+</span><span class="hp-sym">${esc(p.b)}</span></span><span class="hp-num">${Number(p.correlation).toFixed(2)}</span><span class="hp-muted">${combined !== null ? `${pct(combined, 1)} of account together` : ''}</span></li>`;
    };
    const together = pairs.length
      ? `<ul class="hp-rows">${pairs.slice(0, 3).map(pairLi).join('')}</ul>${pairs.length > 3 ? `<details class="hp-more" data-hp-disclosure="pairs"><summary>${pairs.length - 3} more ${plural(pairs.length - 3, 'pair', 'pairs')}</summary><ul class="hp-rows">${pairs.slice(3).map(pairLi).join('')}</ul></details>` : ''}`
      : `<p class="hp-empty">${vol !== null ? `No pair at or above ${threshold !== null ? threshold.toFixed(2) : '0.70'}.` : 'Not measured: price history unavailable.'}</p>`;
    const upcoming = e.upcoming || [];
    const holdings = (pf.positions || []).length;
    const earnings = upcoming.length
      ? `<ul class="hp-rows hp-events">${upcoming.map((u) => `<li><span class="hp-event-date">${esc(dayText(u.date))}</span><span class="hp-event-name"><span class="hp-sym">${esc(u.symbol)}</span><span class="hp-muted">${u.days === 0 ? 'today' : u.days === 1 ? 'tomorrow' : u.days != null ? `in ${u.days} days` : 'Scheduled earnings'}</span></span><span class="hp-num">${pct(u.weight, 1)}</span></li>`).join('')}</ul>`
      : `<p class="hp-empty">${e.dates_known ? 'None scheduled.' : 'Not reported for these holdings.'}${e.dates_known != null && holdings && e.dates_known < holdings ? ` Dates known for ${e.dates_known} of ${holdings} holdings.` : ''}</p>`;
    const returnsIssue = (pf.issues || []).find((i) => i.code === 'RETURNS_UNAVAILABLE');
    const drivers = (risk.drivers || []);
    return `<section class="q-panel hp-panel hp-exposure" aria-labelledby="hp-ex-h">
      <header class="q-head"><div class="q-head-l"><h2 class="q-head-title" id="hp-ex-h">Risk exposure</h2><p class="q-head-sub">What can hurt this portfolio${risk.level ? ` · QUANTA rates it <strong class="${/high/i.test(risk.level) ? 'is-warn' : ''}">${esc(risk.level)}</strong>` : ''}</p></div><div class="q-head-r">${tag('quanta')}</div></header>
      <div class="q-body">
        <dl class="hp-measures">${kpis.join('')}</dl>
        ${returnsIssue ? `<p class="hp-note is-warn">${esc(returnsIssue.message)}</p>` : ''}
        <div class="hp-risk-lists">
          <div><h3 class="hp-h3">Move together <span>${threshold !== null ? threshold.toFixed(2) : '0.70'}+ correlation${v.days ? ` · ${v.days} days` : ''}</span></h3>${together}</div>
          <div><h3 class="hp-h3">Earnings, next ${e.window_days || 14} days <span>share of account</span></h3>${earnings}</div>
        </div>
        <div class="hp-rk-foot">
          <p class="hp-fine hp-stop-note">Model stops are QUANTA levels, not orders at Robinhood, and not a maximum loss: a gap or slippage past a stop loses more.</p>
          <details class="hp-risk-method" data-hp-disclosure="risk"><summary>Methodology</summary>
            <ul>
              <li>History: ${v.days ? `${v.days} trading days of daily returns` : 'not available'}; volatility and correlation cover ${pct(v.coverage, 1)} of invested value.</li>
              <li>Beta: from market data, covering ${pct(b.coverage, 1)} of holdings. Account beta counts cash at zero: ${acctBeta !== null ? acctBeta.toFixed(2) : 'not measured'}; invested holdings: ${eqBeta !== null ? eqBeta.toFixed(2) : 'not measured'}. Each holding’s part is its value × beta ÷ account value.</li>
              <li>Average pairwise correlation: ${num(v.avg_correlation) !== null ? Number(v.avg_correlation).toFixed(2) : 'not measured'}.</li>
              <li>Model stops cover ${pct(s.coverage, 1)} of holdings${(s.below_stop || []).length ? `; already at or below the stop: ${esc(s.below_stop.join(', '))}` : ''}. Each holding’s part is shares × (price − stop).</li>
            </ul>
            <h4 class="hp-h4">Behind the “${esc(risk.level || 'Not assessed')}” portfolio risk rating</h4>
            <ul>${drivers.map((driver) => `<li>${esc(driver)}</li>`).join('') || `<li>${esc(risk.note || 'Risk drivers not reported')}</li>`}</ul>
          </details>
        </div>
      </div>
    </section>`;
  }

  // One short label per evidence status, whatever the action (the engine only
  // attaches a qualifier to buy-tier calls).
  const EVIDENCE_SHORT = {
    VALIDATED: 'Validated', PARTIAL: 'Partly tested', NO_EDGE: 'No proven edge',
    STALE: 'Validation out of date', UNTESTED: 'Not yet tested',
  };
  const evidenceShort = (ev) => EVIDENCE_SHORT[(ev || {}).status] || (ev || {}).text || '';
  function evidenceCell(g) {
    if (g.status !== 'ok') return NA;
    const score = num(g.conviction);
    return `<span class="hp-ev">${score !== null ? `<span class="hp-score">${score.toFixed(0)}<span class="hp-score-scale">/100 score</span></span>` : ''}${evidenceValidated(g.evidence) ? `<span class="hp-ev-t">${esc(evidenceShort(g.evidence))}</span>` : modelFlag(g.evidence)}</span>`;
  }

  // ---------------------------------------------------------------- guidance
  // Presentation of the server's guidance only: every action, reason, target and
  // next step is the resolver's own output, never re-derived here.
  const KIND_LABEL = {
    investment: 'Investment view', risk: 'Risk', valuation: 'Valuation', concentration: 'Position size',
    portfolio_limit: 'Portfolio limit', missing_evidence: 'Missing evidence', account_data: 'Account data',
  };
  const GROUP_LABEL = { exit_review: 'Exit review', trim_review: 'Trim', consider_adding: 'Consider adding', do_not_add: 'Hold, no new money', hold: 'No change', unavailable: 'No call' };
  const DECISIONS = new Set(['Exit Review', 'Trim', 'Accumulate']);
  const isShared = (g) => SHARED_CODES.has((g.basis || {}).code);

  // The one scenario the server sized for a decision: trimming to the single-name limit,
  // or adding QUANTA's size. Unsized reductions (a risk-grade trim, an exit review) have none.
  function sizedScenario(g) {
    const im = g.impact || {};
    const x = g.action === 'Accumulate' ? im.add : (g.action === 'Trim' || g.action === 'Exit Review') ? im.trim_to_cap : null;
    return x && x.weight_after != null ? x : null;
  }

  // The expanded detail of one decision: outlook separate from action, every part stated.
  function guidanceBodyHtml(r, pf) {
    const g = r.guidance;
    const a = r.analysis || {};
    if (g.status !== 'ok') {
      return `<div class="hp-gi-body">
        <p><strong>No call.</strong> ${esc(g.unavailable_reason || g.key_reason)} A missing analysis is a limitation, not a negative judgement on the stock.</p>
        <div class="hp-gi-foot"><button type="button" class="q-btn q-btn--link hp-open" data-hp-inspect="${esc(r.symbol)}">${esc(r.symbol)} details and research</button></div></div>`;
    }
    const ev = g.evidence || {};
    const reduction = g.action === 'Trim' || g.action === 'Exit Review';
    const risks = list(g.risks);
    const supports = list(g.supports);
    // A reduction is argued against by the bull case; a hold or add by the next risk.
    const counter = reduction ? supports[0] : risks[1];
    const t = g.target || {};
    const rk = a.risk || {};
    const d = a.decision || {};
    const sector = ((pf.metrics || {}).sectors || []).find((x) => x.sector === (r.market || {}).sector);
    const facts = [
      d.risk_grade || rk.risk_grade ? `Risk grade ${d.risk_grade || rk.risk_grade}` : '',
      num(rk.annualized_volatility) !== null ? `Volatility ${pct(rk.annualized_volatility, 0)} a year` : '',
      num(rk.beta) !== null ? `Beta ${Number(rk.beta).toFixed(2)}` : '',
      sector && num(sector.weight) !== null ? `${sector.sector} is ${pct(sector.weight, 1)} of the account` : '',
      r.market && r.market.earnings_date ? `Next earnings ${r.market.earnings_date} (${r.market.earnings_source === 'broker' ? 'Robinhood calendar' : 'market data'})` : '',
    ].filter(Boolean);
    const im = g.impact || {};
    const scen = [['Trimmed to the limit', im.trim_to_cap], [g.action === 'Accumulate' ? 'Adding QUANTA’s size' : null, im.add], ['Selling it all', im.remove]]
      .filter(([k, x]) => k && x && x.weight_after != null)
      .map(([k, x]) => `<li><span>${k}</span><span>${pct(x.weight_before, 1)} → ${pct(x.weight_after, 1)}; ${esc(x.sector || 'sector')} ${pct(x.sector_weight_before, 0)} → ${pct(x.sector_weight_after, 0)}${x.cash_after != null ? `; cash after ${money(x.cash_after)}` : ''}</span></li>`);
    const row = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    return `<div class="hp-gi-body">
      <dl class="hp-gi-dl">
        ${row('Investment outlook', `${stanceChip(g.stance)} score ${num(g.conviction) !== null ? Number(g.conviction).toFixed(0) : '--'}/100${g.score_leaning ? `, leans ${esc(g.score_leaning.toLowerCase())}` : ''}`)}
        ${row('Portfolio action', `${esc(actionLabel(g.action))}: ${esc(g.headline)}`)}
        ${row('Weight now', `${pct(r.broker.weight, 1)} of the account`)}
        ${row('Conditional target', `${esc(t.text || 'Not computed')}${t.basis ? `<span class="hp-sub">${esc(t.basis)}</span>` : ''}`)}
        ${row('Horizon', esc(g.horizon || 'Not stated'))}
        ${row('Evidence confidence', `${esc(evidenceShort(ev) || 'Unknown')}${ev.permits_recommendation ? '' : '<span class="hp-sub">Research view, not a validated recommendation</span>'}`)}
      </dl>
      <div class="hp-chain">
        <div><h4 class="hp-h4">Why</h4>${list(g.why).map((w) => `<p>${esc(w)}</p>`).join('')}<h4 class="hp-h4">Facts</h4>${bullets(facts)}<p class="hp-fine">QUANTA analysis ${esc(timeText(a.analysis_timestamp))}${a.data_provider ? `, ${esc(a.data_provider)} data` : ''}.</p></div>
        <div><h4 class="hp-h4">Principal risk</h4><p>${esc(risks[0] || 'None reported.')}</p>
          ${!reduction && supports.length ? `<h4 class="hp-h4">Case for</h4><p>${esc(supports[0])}</p>` : ''}
          <h4 class="hp-h4">Strongest counterargument</h4><p>${esc(counter || 'None reported.')}</p></div>
        <div><h4 class="hp-h4">Concentration and funding</h4>${scen.length ? `<ul class="hp-rows hp-scen">${scen.join('')}</ul><p class="hp-fine">Arithmetic at today’s prices, not orders.</p>` : '<p class="q-na">Not calculable for this holding.</p>'}
          <h4 class="hp-h4">Review when</h4>${bullets(list(g.would_change).slice(0, 3))}</div>
      </div>
      ${g.tax_note ? `<p class="hp-fine"><strong>Tax lots:</strong> ${esc(g.tax_note)}</p>` : ''}
      <div class="hp-gi-foot"><button type="button" class="q-btn q-btn--link hp-open" data-hp-inspect="${esc(r.symbol)}">${esc(r.symbol)} details and research</button></div>
    </div>`;
  }

  // A weight on a scale that shows the single-name limit (and the review level when the
  // weight nears it): the bar is the position, the ticks are QUANTA's defaults.
  function weightBarHtml(w, after, pol, scaleMax) {
    const cap = num(pol.max_single_position);
    const review = num(pol.trim_review_position);
    const max = scaleMax || Math.max(0.12, (w || 0) * 1.15, after || 0, cap || 0);
    const at = (v) => chartPercent(v, max);
    return `<span class="hp-wbar${cap !== null && w >= cap ? ' is-over' : ''}" aria-hidden="true"><i style="--s:${(Math.min(w || 0, max) / max).toFixed(4)}"></i>${after != null ? `<b class="hp-wbar-after" style="left:${at(after)}%"></b>` : ''}${cap !== null && cap <= max ? `<em style="left:${at(cap)}%"></em>` : ''}${review !== null && review <= max ? `<em class="is-review" style="left:${at(review)}%"></em>` : ''}</span>`;
  }

  // One decision as a compact row; the full reasoning opens under it.
  function decisionRowHtml(r, pf, { reason = false, scaleMax } = {}) {
    const g = r.guidance;
    const sum = decisionSummary(r);
    const w = num(r.broker.weight);
    return `<details class="hp-gi" data-hp-disclosure="gi-${esc(r.symbol)}"><summary class="hp-gi-row">
        <span class="hp-gi-act"><span class="hp-sym">${esc(r.symbol)}</span>${actionChip(g.action, g.evidence)}</span>
        ${reason ? `<span class="hp-gi-why"><span class="hp-gi-reason">${esc(sum.reason)}</span></span>` : ''}
        <span class="hp-gi-w">${weightBarHtml(w, sum.after, pf.policy || {}, scaleMax)}<span class="hp-gi-impact">${sum.after !== null ? `<span class="hp-gi-impact-k">${esc(sum.impactLabel)}</span>` : ''}<strong>${pct(w, 1)}${sum.after !== null ? ` → ${pct(sum.after, 1)}` : ''}</strong></span></span>
        <span class="q-caret" aria-hidden="true"></span>
      </summary>${guidanceBodyHtml(r, pf)}</details>`;
  }

  // Short names for the reasons a holding waits on, for the grouped table. The full reason
  // stays on each ticker (tooltip and accessible name). Unknown codes shorten the reason.
  function reasonTag(b, pol) {
    const code = (b || {}).code || '';
    const TAGS = {
      // [F9] The hurdle comes from engine/thresholds.py (app.js thr()), never typed here.
      LOW_REWARD_RISK: typeof thr === 'function'
        ? `Reward/risk below ${fmtThreshold(thr('RR.RESEARCH_MIN'), 'rr')}` : 'Reward/risk below the minimum',
      INVALID_TARGET: 'No valuation target',
      RISK_GRADE_D_CAP: 'Risk grade D', RISK_GRADE_D_HOLD: 'Risk grade D', RISK_GRADE_F: 'Risk grade F',
      POSITION_AT_CAP: `At the ${pct(pol.max_single_position, 0)} single-name limit`, SECTOR_AT_CAP: `Sector at the ${pct(pol.max_sector, 0)} limit`,
      EARNINGS_SOON: 'Earnings within days', RISK_BUDGET_FULL: 'Stop-risk budget full', CORRELATED_CLUSTER: 'Moves with another holding',
      NO_BUYING_POWER: 'No buying power', BUYING_POWER_UNKNOWN: 'Buying power unknown',
    };
    if (TAGS[code]) return TAGS[code];
    const t = String((b || {}).text || 'Reason not reported').split(/: | \(|; /)[0];
    return t.length > 44 ? `${t.slice(0, 42).replace(/\s+\S*$/, '')}…` : t;
  }

  // Decisions, most severe first (the server's priority), then by the size of what acting
  // would move: the sized scenario in dollars, else the position's weight.
  function decisionOrder(pf) {
    const size = (r) => { const x = sizedScenario(r.guidance); return x && num(x.delta_value) !== null ? Math.abs(x.delta_value) : 0; };
    return decisionRows(pf).slice().sort((a, b) => a.guidance.priority - b.guidance.priority || size(b) - size(a) || byWeight(a, b));
  }
  // The hero: the decision whose sized scenario moves the most money; else the most severe.
  function heroOf(rows) {
    const sized = rows.filter((r) => sizedScenario(r.guidance) && num(sizedScenario(r.guidance).delta_value) !== null);
    return sized.length ? sized.slice().sort((a, b) => Math.abs(sizedScenario(b.guidance).delta_value) - Math.abs(sizedScenario(a.guidance).delta_value))[0] : rows[0];
  }

  // The largest decision, made visible: the weight before and after on a scale with the
  // limit and the review level, what it frees (or uses), and what it does to the book.
  function heroHtml(r, pf) {
    const g = r.guidance;
    const pol = pf.policy || {};
    const m = pf.metrics || {};
    const o = pf.overview || {};
    const sum = decisionSummary(r);
    const w = num(r.broker.weight);
    const x = sizedScenario(g) || ((g.impact || {}).remove && g.action !== 'Accumulate' ? g.impact.remove : null);
    const sized = !!sizedScenario(g);
    const after = x ? num(x.weight_after) : null;
    const cap = num(pol.max_single_position);
    const review = num(pol.trim_review_position);
    const max = Math.max(w || 0, after || 0, review || 0, cap || 0) * 1.12;
    const at = (v) => chartPercent(v, max);
    const bar = (label, v, cls) => `<div class="hp-hb"><span class="hp-hb-k">${label}</span><span class="hp-hb-t"><i class="${cls}" style="--s:${(v / max).toFixed(4)}"></i></span><b class="hp-hb-v">${pct(v, 1)}</b></div>`;
    const ticks = `<div class="hp-hb-ticks" aria-hidden="true">${cap !== null ? `<em style="left:${at(cap)}%"><span>${pct(cap, 0)} limit</span></em>` : ''}${review !== null ? `<em class="is-review" style="left:${at(review)}%"><span>${pct(review, 0)} review</span></em>` : ''}</div>`;
    const delta = x ? num(x.delta_value) : null;
    const total = num(m.total_value) ?? num(o.total_value);
    const cashAfter = x ? num(x.cash_after) : null;
    // Largest position after: the other holdings keep their weights (the sale goes to cash).
    const others = (pf.positions || []).filter((p) => p.symbol !== r.symbol && num(p.broker.weight) !== null).sort(byWeight);
    const top0 = (pf.positions || []).slice().sort(byWeight)[0];
    const topAfter = after !== null ? [{ symbol: r.symbol, w: after }, ...others.map((p) => ({ symbol: p.symbol, w: p.broker.weight }))].sort((a, b) => b.w - a.w)[0] : null;
    const eff = (k, a, b) => `<div><dt>${k}</dt><dd>${a} <span aria-hidden="true">→</span><span class="sr-only">to</span> ${b}</dd></div>`;
    const effects = [
      topAfter && top0 ? eff('Largest position', `<span class="hp-sym">${esc(top0.symbol)}</span> ${pct(top0.broker.weight, 1)}`, `<span class="hp-sym">${esc(topAfter.symbol)}</span> ${pct(topAfter.w, 1)}`) : '',
      x && x.sector ? eff(esc(x.sector), pct(x.sector_weight_before, 1), pct(x.sector_weight_after, 1)) : '',
      cashAfter !== null && total ? eff('Cash', pct(m.cash_weight, 1), pct(cashAfter / total, 1)) : '',
    ].join('');
    const X = api_.ext || {};
    const label = !x ? '' : g.action === 'Accumulate' ? 'After adding' : sized ? 'After trim' : 'If sold in full';
    return `<article class="hp-hero" aria-labelledby="hp-hero-h">
      <header class="hp-hero-h"><h3 id="hp-hero-h"><button type="button" class="hp-hero-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}">${esc(r.symbol)}</button>${actionChip(g.action, g.evidence)}</h3>
        <p class="hp-hero-why">${esc(sum.reason)}</p></header>
      <div class="hp-hero-b">
        <div class="hp-hero-bars" role="img" aria-label="${esc(`${r.symbol} is ${pct(w, 1)} of the account${after !== null ? `; ${label.toLowerCase()} ${pct(after, 1)}` : ''}. Single-name limit ${pct(cap, 0)}, review level ${pct(review, 0)}.`)}">
          ${bar('Now', w || 0, cap !== null && w >= cap ? 'is-over' : '')}${after !== null ? bar(label, after, 'is-after') : ''}${ticks}</div>
        <div class="hp-hero-cash">${delta !== null ? `<span class="hp-hero-k">${g.action === 'Accumulate' ? 'Cash used' : sized ? 'Cash freed' : 'Cash if sold in full'}</span><b class="hp-hero-n">${money(Math.abs(delta))}</b><span class="hp-hero-s">at today’s prices, not an order</span>` : ''}</div>
      </div>
      <div class="hp-hero-fx"><dl class="hp-hero-dl">${effects}</dl><div data-mp-slot="gd-hero-risk" class="hp-hero-risk">${X.heroRisk ? X.heroRisk(pf, r) : ''}</div></div>
      <p class="hp-hero-next">${sum.kind ? `<span class="hp-kind">${esc(sum.kind)}</span> · ` : ''}${esc(sum.next)}</p>
      <details class="hp-gi hp-gi--hero" data-hp-disclosure="gi-${esc(r.symbol)}"><summary class="hp-more-s">Full reasoning for ${esc(r.symbol)}</summary>${guidanceBodyHtml(r, pf)}</details>
    </article>`;
  }

  // Decisions that share a reason become one card: the reason and the next step once,
  // then one compact row per holding. A decision with its own reason is a card of one.
  function reasonCardsHtml(rows, pf) {
    const groups = [];
    rows.forEach((r) => {
      const b = r.guidance.basis || {};
      const key = `${b.code || ''}|${b.text || r.guidance.key_reason}`;
      let grp = groups.find((x) => x.key === key);
      if (!grp) { grp = { key, rows: [], basis: b }; groups.push(grp); }
      grp.rows.push(r);
    });
    const scaleMax = Math.max(0.12, ...rows.map((r) => (num(r.broker.weight) || 0) * 1.15), num((pf.policy || {}).max_single_position) || 0);
    return groups.map((grp) => {
      const r0 = grp.rows[0];
      const sum = decisionSummary(r0);
      const steps = [...new Set(grp.rows.map((r) => r.guidance.next_step || ''))];
      const one = grp.rows.length === 1;
      return `<section class="hp-gc" aria-label="${esc(sum.reason)}">
        <header class="hp-gc-h"><h3 class="hp-gc-t">${esc(sum.reason)}</h3>${one ? '' : `<span class="hp-gc-n">${grp.rows.length} holdings</span>`}</header>
        ${steps.length === 1 && steps[0] ? `<p class="hp-gc-s">${sum.kind ? `<span class="hp-kind">${esc(sum.kind)}</span> · ` : ''}${esc(steps[0])}</p>` : ''}
        <p class="hp-gc-cols" aria-hidden="true"><span>Holding</span><span>Weight, with the ${pct((pf.policy || {}).max_single_position, 0)} single-name limit</span></p>
        <div class="hp-gi-list">${grp.rows.map((r) => decisionRowHtml(r, pf, { scaleMax })).join('')}</div>
      </section>`;
    }).join('');
  }

  // New money on hold, grouped by reason: a tag per reason, the holdings under it. A hold
  // that waits on shared account data is counted here and explained once, in the data status.
  function holdTableHtml(pf, full) {
    const pol = pf.policy || {};
    const rows = (pf.positions || []).filter((r) => r.guidance.group === 'do_not_add').sort(byWeight);
    if (!rows.length) return '';
    const own = rows.filter((r) => !isShared(r.guidance));
    const shared = rows.length - own.length;
    const tags = new Map();
    own.forEach((r) => {
      const t = reasonTag(r.guidance.basis, pol);
      if (!tags.has(t)) tags.set(t, { kind: KIND_LABEL[(r.guidance.basis || {}).kind] || '', rows: [] });
      tags.get(t).rows.push(r);
    });
    const body = [...tags.entries()].map(([t, x]) => `<tr><th scope="row"><span class="hp-tag" title="${esc(x.kind)}">${esc(t)}</span></th>
        <td>${full
    ? `<ul class="hp-hold-full">${x.rows.map((r) => `<li><button type="button" class="hp-chip-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}">${esc(r.symbol)}</button><span>${esc(r.guidance.key_reason)}</span><span class="hp-num">${pct(r.broker.weight, 1)}</span></li>`).join('')}</ul>`
    : x.rows.map((r) => `<button type="button" class="hp-chip-sym" data-hp-inspect="${esc(r.symbol)}" title="${esc(r.guidance.key_reason)}" aria-label="${esc(`${r.symbol}: ${r.guidance.key_reason}. Details.`)}">${esc(r.symbol)}</button>`).join('')}</td></tr>`).join('');
    return `<section class="hp-holdt" aria-labelledby="hp-holdt-h">
      <header class="hp-gc-h"><h3 class="hp-gc-t" id="hp-holdt-h">New money on hold</h3><span class="hp-gc-n">${rows.length} ${plural(rows.length, 'holding', 'holdings')}</span></header>
      ${tags.size ? `<table class="hp-holdt-t"><caption class="sr-only">Holdings QUANTA keeps but would not add to, grouped by reason</caption><tbody>${body}</tbody></table>` : ''}
      ${shared ? `<p class="hp-fine">${shared} more ${plural(shared, 'waits', 'wait')} on the account data noted in the data status.</p>` : ''}
    </section>`;
  }

  // Holds and no-calls, when their filter is chosen: holding, weight, the reason.
  function plainListHtml(rows, pf) {
    if (!rows.length) return '<p class="hp-empty">None in this group.</p>';
    return `<table class="hp-plain"><tbody>${rows.map((r) => `<tr><th scope="row"><button type="button" class="hp-chip-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}">${esc(r.symbol)}</button></th><td class="hp-num">${pct(r.broker.weight, 1)}</td><td>${esc(r.guidance.status === 'ok' ? r.guidance.key_reason : (r.guidance.unavailable_reason || r.guidance.key_reason))}</td></tr>`).join('')}</tbody></table>`;
  }

  const GUIDE_ORDER = ['exit_review', 'trim_review', 'consider_adding', 'do_not_add', 'hold', 'unavailable'];
  function guidanceChipsHtml(pf) {
    const groups = GUIDE_ORDER.map((k) => (pf.groups || []).find((g) => g.key === k)).filter((g) => g && g.count);
    if (groups.length < 2) return '';
    const on = state.gFilter || '';
    return `<div class="hp-filters hp-gd-chips" role="group" aria-label="Show guidance for">${[{ key: '', title: 'All', count: (pf.positions || []).length }, ...groups].map((g) => `<button type="button" class="hp-filter${on === g.key ? ' is-on' : ''}" data-hp-gfilter="${esc(g.key)}" aria-pressed="${on === g.key}">${esc(g.key ? (GROUP_LABEL[g.key] || g.title) : 'All')} <span class="hp-num">${g.count}</span></button>`).join('')}</div>`;
  }

  // The research rating: the holdings' research scores averaged by value, on the
  // decision ladder's own bands (engine/thresholds.py, LADDER.SCORE_BANDS). Strong Buy
  // folds into Buy until the model is validated (owner decision Q4). It describes the
  // score before the risk, price and portfolio rules that set each call; it is not a call.
  const RATE_BANDS = [['buy', 'Buy', 'BUY'], ['accumulate', 'Accumulate', 'ACCUMULATE'], ['hold', 'Hold', 'HOLD'], ['reduce', 'Reduce', 'REDUCE'], ['sell', 'Sell', null]];
  function ratingBands() {
    const raw = typeof thr === 'function' ? thr('LADDER.SCORE_BANDS') : null;
    if (!Array.isArray(raw)) return null;
    const floor = (name) => { const b = raw.find((x) => x[1] === name); return b ? Number(b[0]) : null; };
    const f = { BUY: floor('BUY'), ACCUMULATE: floor('ACCUMULATE'), HOLD: floor('HOLD'), REDUCE: floor('REDUCE') };
    if (Object.values(f).some((v) => !Number.isFinite(v))) return null;
    return [
      { key: 'r-sell', name: 'Sell', from: 0, to: f.REDUCE - 1, band: 'sell' },
      { key: 'r-reduce', name: 'Reduce', from: f.REDUCE, to: f.HOLD - 1, band: 'reduce' },
      { key: 'r-hold', name: 'Hold', from: f.HOLD, to: f.ACCUMULATE - 1, band: 'hold' },
      { key: 'r-accumulate', name: 'Accumulate', from: f.ACCUMULATE, to: f.BUY - 1, band: 'accumulate' },
      { key: 'r-buy', name: 'Buy', from: f.BUY, to: 100, band: 'buy' },
    ];
  }
  function ratingHtml(pf) {
    const zones = ratingBands();
    const Ch = root.QuantaCharts;
    if (!zones || !Ch || !Ch.riskGaugeHtml) return '';
    const rows = (pf.positions || []).map((r) => ({ r, s: num(((r.analysis || {}).decision || {}).score), w: num(r.broker.weight) }));
    const scored = rows.filter((x) => x.s !== null && x.r.guidance.status === 'ok');
    const weighted = scored.filter((x) => x.w !== null && x.w > 0);
    const totalW = weighted.reduce((t, x) => t + x.w, 0);
    if (!weighted.length || !(totalW > 0)) return '';
    const score = Math.round(weighted.reduce((t, x) => t + x.s * x.w, 0) / totalW);
    const zoneOf = (v) => zones.find((z) => v >= z.from && v <= z.to) || (v > 100 ? zones[zones.length - 1] : zones[0]);
    const here = zoneOf(score);
    const counts = Object.fromEntries(zones.map((z) => [z.band, 0]));
    scored.forEach((x) => { counts[zoneOf(x.s).band] += 1; });
    const noCall = rows.length - scored.length;
    const range = (z) => (z.band === 'buy' ? `${z.from}+` : z.band === 'sell' ? `under ${z.to + 1}` : `${z.from}–${z.to}`);
    const validated = scored.some((x) => ((x.r.guidance.evidence || {}).status || '') === 'VALIDATED');
    const gauge = Ch.riskGaugeHtml({ zones, points: score, ariaLabel: `Research rating ${score} of 100 weighted by value, in the ${here.name} band. Bands: ${zones.slice().reverse().map((z) => `${z.name} ${range(z)}`).join(', ')}.` });
    const li = RATE_BANDS.map(([band, label]) => {
      const z = zones.find((x) => x.band === band);
      const n = counts[band] || 0;
      return `<li class="${n ? '' : 'is-zero'}"><i class="is-${band}" aria-hidden="true"></i><span>${label} <small>${esc(range(z))}</small></span><b>${n}</b></li>`;
    }).join('') + (noCall ? `<li><i class="is-none" aria-hidden="true"></i><span>No call</span><b>${noCall}</b></li>` : '');
    return `<div class="hp-rate" role="group" aria-labelledby="hp-rate-h">
      <div class="hp-rate-g">${gauge}<p class="hp-rate-score"><b>${score}</b><span>of 100 · ${esc(here.name)} band</span></p></div>
      <div class="hp-rate-r"><h3 id="hp-rate-h">Research rating, by value</h3>
        <p>Your holdings average a research score of <b>${score}</b>, weighted by position value: the ${esc(here.name)} band. This is the score before the risk-grade, price and portfolio rules that set each call below${validated ? '' : ', and the model is not validated: it describes the scores, it does not forecast returns'}. Strong Buy is shown as Buy until the model is validated.</p>
        <ul class="hp-rate-l" aria-label="Holdings by research score band">${li}</ul></div>
    </div>`;
  }

  function guidanceHtml(pf) {
    const rows = (pf.positions || []).slice().sort((a, b) => a.guidance.priority - b.guidance.priority || byWeight(a, b));
    const actionable = decisionOrder(pf);
    const rated = rows.filter((r) => r.guidance.status === 'ok');
    const f = pf.freshness || {};
    const a = f.analysis || {};
    const when = a.state === 'running' ? `Analysing ${a.done || 0} of ${a.total || 0}` : `Analysis ${esc(timeText(f.latest_analysis || a.finished_at))}`;
    const title = '<h2 class="q-head-title" id="hp-gd-h">Guidance</h2>';
    const chips = guidanceChipsHtml(pf);
    const filter = state.gFilter && (pf.groups || []).some((g) => g.key === state.gFilter && g.count) ? state.gFilter : null;

    if (!actionable.length && !filter) {
      // Nothing to decide: one status row, not a panel of empty states.
      const paused = !rated.length;
      const running = a.state === 'running';
      const quoteBlocked = lateQuotes(pf).length === rows.length && rows.length > 0;
      const msg = !rated.length
        ? `<strong>${running ? 'Analysis in progress.' : 'Analysis paused.'}</strong> ${running ? 'Current calls will appear as analysis completes.' : quoteBlocked ? 'Fresh quotes are required before portfolio calls can be generated.' : 'Current research is unavailable; refresh data to retry.'}`
        : `<strong>No position changes indicated.</strong> ${rows.some((r) => r.guidance.action === 'Hold / No New Money') ? 'Restrictions on new money still apply; see each holding’s reason.' : 'No portfolio changes require attention among the current calls.'}`;
      const recovery = paused ? `<div class="hp-gd-recovery"><button type="button" id="hp-guidance-refresh" class="q-btn" data-hp="refresh"${state.busy || running ? ' disabled' : ''}>${state.busy ? 'Refreshing…' : 'Refresh data'}</button></div>` : chips;
      return `<section class="q-panel hp-panel hp-guidance is-quiet" aria-labelledby="hp-gd-h">
        <div class="hp-gd-bar">${title}<p class="hp-gd-msg">${msg}</p>${recovery}
        ${paused ? `<details class="hp-gd-details hp-more" data-hp-disclosure="guidance-state"><summary>Details</summary><p class="hp-fine">${when}. Missing research is not a negative investment judgement. Account values remain available; individual data limitations are recorded in Data details and Holdings.</p></details>` : ''}</div>
        ${!paused ? `<div class="q-body">${ratingHtml(pf)}${rows.some((r) => r.guidance.group === 'do_not_add') ? holdTableHtml(pf, false) : ''}</div>` : ''}
      </section>`;
    }

    const ev = new Map();
    rated.forEach((r) => { const label = evidenceShort(r.guidance.evidence) || 'Evidence unavailable'; ev.set(label, (ev.get(label) || 0) + 1); });
    const evText = ev.size === 1 ? `${esc([...ev.keys()][0])}, ${rated.length === 1 ? 'the only call' : `all ${rated.length} calls`}`
      : [...ev].map(([label, count]) => `${esc(label)} ${count}`).join(', ');
    const X = api_.ext || {};
    const head = `<header class="q-head"><div class="q-head-l">${title}<p class="q-head-sub">${actionable.length} ${plural(actionable.length, 'needs', 'need')} a decision · <span class="hp-evidence-note">${evText}</span> · ${when}</p></div><div class="q-head-r">${tag('quanta')}</div></header>`;
    let body;
    if (filter === 'do_not_add') body = `<div class="hp-gd-one">${holdTableHtml(pf, true)}</div>`;
    else if (filter === 'hold' || filter === 'unavailable') body = `<div class="hp-gd-one">${plainListHtml(rows.filter((r) => r.guidance.group === filter), pf)}</div>`;
    else {
      const shown = filter ? actionable.filter((r) => r.guidance.group === filter) : actionable;
      const hero = heroOf(shown);
      const rest = shown.filter((r) => r !== hero);
      const side = [X.whatifPanel ? `<div data-mp-slot="gd-whatif">${X.whatifPanel(pf)}</div>` : '', filter ? '' : holdTableHtml(pf, false)].filter(Boolean).join('');
      body = `<div class="hp-gd${side ? ' has-rail' : ''}">
        <div class="hp-gd-main">${hero ? heroHtml(hero, pf) : ''}${rest.length ? reasonCardsHtml(rest, pf) : ''}</div>
        ${side ? `<aside class="hp-gd-rail" aria-label="What acting would change">${side}</aside>` : ''}
      </div>`;
    }
    return `<section class="q-panel hp-panel hp-guidance" aria-labelledby="hp-gd-h">${head}
      <div class="q-body">${ratingHtml(pf)}${chips}${body}</div>
    </section>`;
  }

  // A position's own move this session, from Robinhood's day change and value.
  const dayPct = (b) => {
    const d = num(b.day_change); const v = num(b.market_value);
    return d !== null && v !== null && v - d > 0 ? d / (v - d) * 100 : null;
  };
  // The rows as the table shows them: filter, then sort. The detail panel's
  // previous/next walk this same order.
  function tableRows(pf) {
    let rows = pf.positions || [];
    if (state.filter) rows = rows.filter((r) => r.guidance.group === state.filter);
    if (state.holdFilter) rows = rows.filter((r) => state.holdFilter.symbols.includes(r.symbol));
    return sortRows(rows, state.sort, state.dir);
  }

  function filtersHtml(pf) {
    const groups = (pf.groups || []).filter((g) => g.count);
    if (groups.length < 2) return '';
    return `<div class="hp-filters" role="group" aria-label="Filter holdings by action"><button type="button" class="hp-filter${state.filter ? '' : ' is-on'}" data-hp-filter="" aria-pressed="${!state.filter}">All <span class="hp-num">${(pf.positions || []).length}</span></button>${groups.map((g) => `<button type="button" class="hp-filter${state.filter === g.key ? ' is-on' : ''}" data-hp-filter="${esc(g.key)}" aria-pressed="${state.filter === g.key}">${esc(GROUP_LABEL[g.key] || g.title)} <span class="hp-num">${g.count}</span></button>`).join('')}</div>`;
  }

  // The exposure bars click through to the holdings behind them; this chip says so and clears it.
  function holdFilterHtml() {
    const f = state.holdFilter;
    if (!f) return '';
    return `<div class="hp-toolbar hp-holdfilter"><span class="hp-holdchip">Showing ${esc(f.label)} <span class="hp-num">${f.symbols.length}</span><button type="button" class="hp-holdchip-x" data-hp-x="clear-hold" aria-label="Show all holdings">Show all</button></span></div>`;
  }

  function tableHtml(pf) {
    const rows = tableRows(pf);
    const ariaSort = (k) => (state.sort === k ? ((state.dir || SORTS[k].dir) === 'asc' ? 'ascending' : 'descending') : 'none');
    const th = (k, label, cls = '') => `<th scope="col" class="${cls}" aria-sort="${ariaSort(k)}"><span class="hp-mobile-heading">${label}</span><button type="button" class="q-sortable" data-hp-sort="${k}">${label}<span class="q-sort${state.sort === k ? ' is-on' : ''}" aria-hidden="true">${state.sort === k && (state.dir || SORTS[k].dir) === 'asc' ? '▲' : '▼'}</span></button></th>`;
    const ses = sessionOf(pf);
    const body = rows.map((r) => {
      const g = r.guidance; const b = r.broker;
      const selected = state.selected === r.symbol;
      const pnl = b.unrealized_pnl != null
        ? `<span class="${dirClass(b.unrealized_pnl)}">${signedMoney(b.unrealized_pnl)}</span><span class="hp-sub ${dirClass(b.unrealized_pnl)}">${priv(pct(b.unrealized_pnl_pct, 1, false, true))}</span>`
        : '<span class="q-na" title="Robinhood did not report a cost basis">No basis</span>';
      const dp = dayPct(b);
      const today = b.day_change != null
        ? `<span class="${dirClass(b.day_change)}">${signedMoney(b.day_change)}</span><span class="hp-sub ${dirClass(b.day_change)}">${dp !== null ? priv(pct(dp, 2, false, true)) : ''}</span>`
        : '<span class="q-na" title="Robinhood did not report a previous close">--</span>';
      return `<tr class="q-tr hp-tr hp-tr--${esc(g.group)}${selected ? ' is-selected' : ''}" data-hp-row="${esc(r.symbol)}">
          <td><button type="button" class="hp-row-toggle" data-hp-toggle="${esc(r.symbol)}" aria-haspopup="dialog" aria-label="${esc(`${r.symbol} details`)}"><span class="hp-sym">${esc(r.symbol)}</span><span class="q-caret" aria-hidden="true"></span></button><span class="hp-sub hp-company">${esc(b.name || r.market.sector || '')}</span></td>
          <td class="num hp-col-weight" data-label="Weight">${pct(b.weight, 1)}<span class="hp-sub">${money(b.market_value)}</span></td>
          <td class="num hp-col-today" data-label="${esc(ses.live ? 'Today' : 'Last session')}">${today}</td>
          <td class="num hp-col-basis" data-label="Cost basis">${b.cost_basis != null ? money(b.cost_basis) : NA}<span class="hp-sub">${b.average_cost != null ? `${priv(price(b.average_cost))} / share` : 'Average cost unavailable'}</span></td>
          <td class="num" data-label="P&amp;L">${pnl}</td>
          <td data-label="QUANTA stance">${g.status === 'ok' ? stanceChip(g.stance) : NA}</td>
          <td data-label="Portfolio action">${actionChip(g.action)}</td>
          <td class="hp-col-ev" data-label="Evidence">${evidenceCell(g)}</td>
          <td class="hp-reason hp-col-reason" data-label="Key reason">${esc(g.key_reason || '')}</td>
        </tr>`;
    }).join('');
    const sortOpts = Object.entries(SORTS).map(([k, s]) => `<option value="${k}"${state.sort === k ? ' selected' : ''}>${s.label}</option>`).join('');
    return `<section class="q-panel hp-panel hp-holdings" aria-labelledby="hp-holdings-h">
      <header class="q-head"><div class="q-head-l"><h2 class="q-head-title" id="hp-holdings-h">Holdings <span class="hp-count">${rows.length}${state.filter || state.holdFilter ? ` / ${(pf.positions || []).length}` : ''}</span></h2>
        <p class="q-head-sub">Select a ticker for its position, portfolio call and research.</p></div>
        <div class="q-head-r"><label class="hp-sortsel">Sort by <select id="hp-sort">${sortOpts}</select></label><button type="button" class="q-btn hp-sort-direction" data-hp="sort-direction" aria-label="${(state.dir || SORTS[state.sort].dir) === 'asc' ? 'Sort descending' : 'Sort ascending'}" title="Reverse sort order">${(state.dir || SORTS[state.sort].dir) === 'asc' ? '↑' : '↓'}</button></div></header>
      ${holdFilterHtml(pf)}
      ${filtersHtml(pf) ? `<div class="hp-toolbar">${filtersHtml(pf)}</div>` : ''}
      <div class="q-tablewrap hp-table-scroll" tabindex="0" role="region" aria-label="Holdings table"><table class="q-table hp-table" role="table">
        <caption class="sr-only">Holdings with QUANTA stance and portfolio action. Select a ticker for the full analysis. Scores are out of 100, not a probability of profit.</caption>
        <thead><tr>
          ${th('ticker', 'Ticker')}${th('weight', 'Weight', 'num hp-col-weight')}${th('today', ses.live ? 'Today' : 'Last session', 'num hp-col-today')}${th('basis', 'Cost basis', 'num hp-col-basis')}${th('pnl', 'P&amp;L', 'num')}
          <th scope="col">QUANTA stance</th>${th('action', 'Portfolio action')}${th('conviction', 'Evidence', 'hp-col-ev')}<th scope="col" class="hp-col-reason">Key reason</th>
        </tr></thead>
        <tbody>${body || '<tr><td colspan="9" class="q-na">No holdings in this group. Select another action or show all holdings.</td></tr>'}</tbody>
      </table></div>
    </section>`;
  }

  // ---------------------------------------------------------------- position detail
  const dl = (pairs) => `<dl class="q-dl hp-dl">${pairs.filter(Boolean).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v == null || v === '' ? NA : v}</dd></div>`).join('')}</dl>`;
  const bullets = (items, cls = '') => (items && items.length ? `<ul class="q-bullets ${cls}">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>` : '<p class="q-na">None reported.</p>');
  const chapter = (title, tags, inner) => `<section class="hp-ch"><header class="hp-ch-h"><h3>${esc(title)}</h3><span class="hp-ch-tags">${tags.map(tag).join('')}</span></header>${inner}</section>`;

  function impactHtml(g, r) {
    const im = g.impact || {};
    const row = (label, x) => (x && x.weight_after != null ? `<div><dt>${esc(label)}</dt><dd>
        ${x.delta_value != null ? `${signedMoney(x.delta_value)}: ` : ''}weight ${pct(x.weight_before, 1)} → ${pct(x.weight_after, 1)};
        ${esc(x.sector)} ${pct(x.sector_weight_before, 0)} → ${pct(x.sector_weight_after, 0)}${x.portfolio_beta_before != null ? `; beta ${Number(x.portfolio_beta_before).toFixed(2)} → ${Number(x.portfolio_beta_after).toFixed(2)}` : ''}
        ${x.basis ? `<span class="hp-sub">${esc(x.basis)}</span>` : ''}</dd></div>` : '');
    const addLabel = g.action === 'Accumulate' ? 'Adding (QUANTA size)' : 'If you added anyway (QUANTA size)';
    return `<dl class="q-dl hp-dl hp-dl--rows">
      <div><dt>Weight now</dt><dd>${pct(r.broker.weight, 1)} of the portfolio${r.market.sector ? `; ${esc(r.market.sector)}` : ''}</dd></div>
      <div><dt>Room before the single-name limit</dt><dd>${money(im.room_to_cap)}</dd></div>
      ${row(addLabel, im.add)}${row('Trimming to the limit', im.trim_to_cap)}${row('Selling it all', im.remove)}
    </dl><p class="hp-fine">Previews assume the trade settles in cash at today’s price. They are arithmetic, not orders.</p>`;
  }

  function detailHtml(r, pf) {
    const g = r.guidance; const b = r.broker; const a = r.analysis || {}; const m = r.market || {};
    const d = a.decision || {};
    const f = a.fundamental || {}; const v = a.valuation || {}; const mi = a.market_intel || {};
    const t = a.technical || {}; const rk = a.risk || {}; const c = a.catalysts || {}; const th = a.thesis || {};
    const isEtf = !!(a.is_etf || f.is_etf);
    const lots = b.tax_lots;
    const lotLine = lots == null ? 'Not available from Robinhood' : (lots.length ? `${lots.length} lot${lots.length > 1 ? 's' : ''}` : 'None reported');
    const qf = b.quote_freshness || {};
    const noCall = g.status !== 'ok';
    const position = chapter('Position', ['broker'], dl([
      ['Shares', qty(b.quantity)],
      ['Average cost', b.average_cost != null ? priv(price(b.average_cost)) : 'Not reported'],
      ['Cost basis', b.cost_basis != null ? money(b.cost_basis, 2) : 'Not reported'],
      ['Last price', `${price(b.last_price)} <span class="hp-sub">${esc(qf.text || '')}</span>`],
      ['Market value', money(b.market_value, 2)],
      [sessionOf(pf).live ? 'Today' : 'Last session', b.day_change != null ? `<span class="${dirClass(b.day_change)}">${signedMoney(b.day_change, 2)}</span>` : NA],
      ['Unrealized', b.unrealized_pnl != null ? `<span class="${dirClass(b.unrealized_pnl)}">${signedMoney(b.unrealized_pnl, 2)} (${priv(pct(b.unrealized_pnl_pct, 1, false, true))})</span>` : 'No cost basis'],
      ['Tax lots', esc(lotLine)],
    ]));
    if (noCall) {
      return `<div class="hp-detail-grid">${position}
        ${chapter('Guidance', ['quanta'], `<p class="hp-nocall"><strong>No call.</strong> ${esc(g.unavailable_reason || g.key_reason)}</p>
          <p class="hp-fine">QUANTA does not turn missing or failed data into Buy, Hold or Sell. ${a.error && a.error.suggested_action ? esc(a.error.suggested_action) : ''}</p>`)}
      </div>`;
    }
    const thesis = chapter('Thesis', ['quanta'], `${th.summary ? `<p>${esc(th.summary)}</p>` : ''}
      <div class="hp-two"><div><h4 class="hp-h4">Bull case</h4>${bullets(list(th.bull_case), 'bull')}</div><div><h4 class="hp-h4">Bear case</h4>${bullets(list(th.bear_case), 'warn')}</div></div>`);
    const pts = (x) => (num(x) === null ? null : pct(x, 1, false));
    const fundamentals = chapter('Fundamentals', ['market', 'quanta'], isEtf ? '<p class="q-na">Not applicable to an ETF.</p>' : dl([
      ['Return on equity', pts(f.roe)], ['Net margin', pts(f.net_margin)],
      ['Revenue growth YoY', num(f.revenue_growth_yoy) === null ? null : pct(f.revenue_growth_yoy, 1, false, true)],
      ['Debt / equity', num(f.debt_to_equity) === null ? null : Number(f.debt_to_equity).toFixed(2)],
      ['Quality score', num(f.quality_score) === null ? null : Number(f.quality_score).toFixed(0)],
      ['Dividend yield', num(f.dividend_yield) > 0 ? pct(f.dividend_yield, 2) : 'None reported'],
    ]));
    const valuation = chapter('Valuation', ['market', 'quanta'], dl([
      ['P/E trailing / forward', `${num(f.pe_ratio) === null ? '--' : Number(f.pe_ratio).toFixed(1)} / ${num(f.forward_pe) === null ? '--' : Number(f.forward_pe).toFixed(1)}`],
      ['DCF vs price', v.dcf_status === 'measured' && num(v.dcf_upside) !== null ? pct(v.dcf_upside, 1, false, true) : 'Unavailable'],
      ['Valuation read', v.valuation_label && !/^(unavailable|unknown|n\/?a)$/i.test(v.valuation_label) ? esc(String(v.valuation_label).replace(/_/g, ' ').replace(/^./, (x) => x.toUpperCase())) : 'Unavailable'],
      ['12-month target (QUANTA blend)', num(v.target_base) === null ? null : price(v.target_base)],
      ['Analyst consensus', num(mi.target_mean) === null ? null : `${price(mi.target_mean)}${mi.analyst_count ? ` <span class="hp-sub">${esc(mi.analyst_count)} analysts</span>` : ''}`],
    ]));
    const technicals = chapter('Technicals', ['market', 'quanta'], dl([
      ['Trend', t.trend_direction ? esc(String(t.trend_direction).toLowerCase().replace(/^./, (x) => x.toUpperCase())) : null], ['RSI', num(t.rsi) === null ? null : Number(t.rsi).toFixed(0)],
      ['ADX', num(t.adx) === null ? null : Number(t.adx).toFixed(0)],
      ['Support / resistance', `${price(t.support_level)} / ${price(t.resistance_level)}`],
      ['52-week range', `${price(t.week_52_low)} to ${price(t.week_52_high)}`],
    ]));
    const catalysts = chapter('Catalysts', ['market'], dl([
      ['Next earnings', m.earnings_date ? `${esc(m.earnings_date)} <span class="hp-sub">${m.earnings_source === 'broker' ? 'Robinhood calendar' : 'market data'}</span>` : 'Not scheduled or not reported'],
      ['Last result', c.last_earnings_result && !/^(unknown|n\/?a|none)$/i.test(c.last_earnings_result) ? `${esc(c.last_earnings_result)}${num(c.last_earnings_surprise_pct) !== null ? ` (${pct(c.last_earnings_surprise_pct, 1, false, true)} surprise)` : ''}` : null],
      ['Ex-dividend', esc(c.ex_dividend_date || '')],
      ['Near-term catalyst', esc(c.near_term_catalyst || '')],
    ]));
    const risks = chapter('Risks', ['quanta'], `${bullets(g.risks, 'warn')}${dl([
      ['Risk grade', esc(d.risk_grade || rk.risk_grade || '')],
      ['Volatility, annualised', num(rk.annualized_volatility) === null ? null : pct(rk.annualized_volatility, 1)],
      ['Beta', num(rk.beta) === null ? 'Not measured' : Number(rk.beta).toFixed(2)],
      ['Max drawdown', num(rk.max_drawdown) === null ? null : pct(rk.max_drawdown, 1)],
    ])}`);
    const impact = chapter('Portfolio Impact', ['broker', 'quanta'], impactHtml(g, r));
    const blocks = (g.blocks || []).map((x) => `${x.text}${x.source === 'portfolio' ? ' (portfolio)' : ''}`);
    const reduction = g.action === 'Trim' || g.action === 'Exit Review';
    const counter = reduction ? list(g.supports)[0] : list(g.risks)[1];
    const guidance = chapter('Portfolio call', ['quanta'], `
      <div class="hp-three">
        <div><span class="hp-k">Model stance</span>${stanceChip(g.stance)}${g.score_leaning ? `<span class="hp-sub">Score leans ${esc(g.score_leaning.toLowerCase())}</span>` : ''}</div>
        <div><span class="hp-k">New money</span><span class="hp-v">${esc(g.new_money)}</span></div>
        <div><span class="hp-k">Existing position</span><span class="hp-v">${esc(g.existing)}</span></div>
      </div>
      <p class="hp-headline"><strong>${esc(g.headline)}.</strong> ${esc(g.horizon ? `Horizon: ${g.horizon}.` : '')}</p>
      ${g.why.map((w) => `<p>${esc(w)}</p>`).join('')}
      <div class="hp-two">
        <div><h4 class="hp-h4">What supports it</h4>${bullets(g.supports, 'bull')}</div>
        <div><h4 class="hp-h4">What blocks a stronger action</h4>${bullets(blocks)}</div>
      </div>
      <div class="hp-two">
        <div><h4 class="hp-h4">Principal risk</h4><p>${esc(list(g.risks)[0] || 'None reported.')}</p></div>
        <div><h4 class="hp-h4">Strongest counterargument</h4><p>${esc(counter || 'None reported.')}</p></div>
      </div>
      <h4 class="hp-h4">What would change it</h4>${bullets(g.would_change)}
      ${g.tax_note ? `<p class="hp-fine"><strong>Tax lots:</strong> ${esc(g.tax_note)}</p>` : ''}`);
    const ev = g.evidence || {};
    const fresh = d.freshness || {};
    const interp = r.interpretation && (r.interpretation.summary || r.interpretation.text);
    const evidence = chapter('Evidence', interp ? ['quanta', 'ai'] : ['quanta'], `${dl([
      ['Model track record', `${esc(ev.text || '')}${ev.permits_recommendation ? '' : ' <span class="hp-sub">so this is a research view, not a validated recommendation</span>'}`],
      ['Analysis run', `${esc(timeText(a.analysis_timestamp))}${a.used_cached_data || (a.cache && a.cache.hit) ? ' <span class="hp-sub">from cache</span>' : ''}`],
      ['Price bar used', `${esc(fresh.last_bar || d.as_of || '')} <span class="hp-sub">${esc(fresh.status || '')}</span>`],
      ['Data provider', esc(a.data_provider || '')],
      ['Sized against', a.account_context ? esc(a.account_context.note || '') : null],
      ['How the engine resolved it', g.trace && g.trace.length ? esc(g.trace.join('; ')) : null],
    ])}${ev.caveat ? `<p class="hp-fine">${esc(ev.caveat)}</p>` : ''}${interp ? `<p class="hp-ai">${tag('ai')} ${esc(interp)}</p>` : ''}`);
    // Portfolio call first; the company outlook (research) is a separate,
    // collapsed layer, so an outlook is never read as the portfolio action.
    return `<div class="hp-detail-grid">${position}${guidance}${impact}${risks}${evidence}</div>
      <details class="hp-research" data-hp-disclosure="research"><summary>Company research <span>Thesis, fundamentals, valuation, technicals, catalysts</span></summary>
        <div class="hp-detail-grid">${thesis}${fundamentals}${valuation}${technicals}${catalysts}</div></details>
      <div class="q-detail-actions"><button type="button" class="q-btn" data-hp-research="${esc(r.symbol)}">Open full tear sheet</button></div>`;
  }

  // ---------------------------------------------------------------- overview brief
  // The default view answers four questions in one screen: what is it worth,
  // where does it stand, what needs a decision, and where is the money. Every
  // sentence is assembled from published figures; none adds a judgement.
  const byWeight = (a, b) => (num(b.broker.weight) || 0) - (num(a.broker.weight) || 0);

  function decisionRows(pf) {
    return (pf.positions || []).filter((r) => DECISIONS.has(r.guidance.action))
      .sort((a, b) => a.guidance.priority - b.guidance.priority || byWeight(a, b));
  }
  // One reason/impact source for a decision, shared by the Overview priorities
  // and the Plan list, so the two can never disagree.
  function decisionSummary(r) {
    const g = r.guidance;
    const b = g.basis || {};
    const im = g.impact || {};
    const scenario = (g.action === 'Accumulate' && im.add) || ((g.action === 'Trim' || g.action === 'Exit Review') && im.trim_to_cap) || null;
    return {
      reason: isShared(g) ? 'Waits on the data status above' : (b.text || g.key_reason || 'Reason not reported'),
      kind: KIND_LABEL[b.kind] || '',
      next: g.next_step || '',
      after: scenario ? num(scenario.weight_after) : null,
      impactLabel: scenario ? (g.action === 'Accumulate' ? 'If added' : 'If trimmed to limit') : 'Account weight',
    };
  }

  function assessmentHtml(pf) {
    const m = pf.metrics || {};
    const o = pf.overview || {};
    const pol = pf.policy || {};
    const c = m.concentration || o.concentration || {};
    const risk = o.risk || {};
    const rows = (pf.positions || []).slice().sort(byWeight);
    const posLimit = num(pol.max_single_position);
    const secLimit = num(pol.max_sector);
    const overPos = rows.filter((r) => posLimit !== null && num(r.broker.weight) !== null && r.broker.weight >= posLimit);
    const overSec = (m.sectors || []).filter((s) => limitedSector(s.sector) && secLimit !== null && num(s.weight) !== null && s.weight >= secLimit)
      .sort((a, b) => b.weight - a.weight);
    const eff = num(c.effective_names);
    let conc;
    if (overPos.length || overSec.length) {
      const parts = [];
      if (overPos.length) parts.push(`<span class="hp-sym">${esc(overPos[0].symbol)}</span> is ${pct(overPos[0].broker.weight, 1)} of the account${overPos.length > 1 ? `, and ${overPos.length - 1} more ${plural(overPos.length - 1, 'holding is', 'holdings are')} over the ${pct(posLimit, 0)} single-name reference` : ` (reference ${pct(posLimit, 0)})`}`);
      if (overSec.length) parts.push(`${esc(overSec[0].sector)} is ${pct(overSec[0].weight, 1)} (reference ${pct(secLimit, 0)})`);
      conc = `${c.label ? `${esc(c.label)}: ` : ''}${parts.join('; ')}.`;
    } else {
      conc = `${esc(c.label || 'Concentration')}${eff !== null ? `: ${eff.toFixed(1)} effective positions across ${rows.length} holdings` : ''}; nothing is over QUANTA’s references.`;
    }
    const beta = num((m.beta || {}).portfolio_beta) ?? num((m.beta || {}).equity_beta);
    const lossPct = num((m.stops || {}).loss_pct);
    const budget = num(pol.stop_risk_budget);
    const riskParts = [];
    if (beta !== null) riskParts.push(`account beta ${beta.toFixed(2)} against the market’s 1.00`);
    if (lossPct !== null) riskParts.push(`hitting every model stop would cost ${pct(lossPct, 1)} of the account${budget > 0 ? ` (budget ${pct(budget, 0)})` : ''}`);
    const riskText = `${risk.level ? `QUANTA rates portfolio risk <strong class="${/high/i.test(risk.level) ? 'is-warn' : ''}">${esc(risk.level)}</strong>` : 'Portfolio risk is not rated'}${riskParts.length ? `: ${riskParts.join('; ')}` : ''}.`;
    const ses = sessionOf(pf);
    const mv = moveParts(pf);
    const cmp = marketCompare(pf);
    let move = '';
    if (mv.pct !== null) {
      const lead = mv.rows[0];
      const diff = cmp.status === 'ok' ? cmp.mine - num(cmp.spy.change_pct) : null;
      move = `${mv.pct >= 0 ? 'Up' : 'Down'} ${pct(Math.abs(mv.pct), 2, false)}${diff !== null ? `, ${Math.abs(diff).toFixed(2)} pts ${diff >= 0 ? 'ahead of' : 'behind'} the S&amp;P 500` : ''}${lead && lead.pts !== null && Math.abs(lead.pts) >= 0.005 ? `; <span class="hp-sym">${esc(lead.symbol)}</span> moved the account most (${signedPts(lead.pts)} pts)` : ''}.`;
    }
    const row = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    return `<dl class="hp-assess">${row('Concentration', conc)}${row('Risk', riskText)}${move ? row(esc(ses.live ? 'Today' : 'Last session'), move) : ''}</dl>`;
  }

  function prioritiesHtml(pf) {
    const rows = decisionRows(pf);
    const rated = (pf.positions || []).filter((r) => r.guidance.status === 'ok');
    const a = (pf.freshness || {}).analysis || {};
    if (!rows.length) {
      const msg = !rated.length
        ? (a.state === 'running' ? 'Analysis in progress; calls appear as it completes.' : 'No calls: current research is unavailable. Refresh to retry.')
        : 'No position changes indicated by the current calls.';
      return `<p class="hp-empty hp-pri-empty">${msg}</p>`;
    }
    const top = rows.slice(0, 3);
    return `<ol class="hp-pri-list">${top.map((r) => {
      const d = decisionSummary(r);
      return `<li class="hp-pri">
        <span class="hp-pri-act"><button type="button" class="hp-pri-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}"><span class="hp-sym">${esc(r.symbol)}</span></button>${actionChip(r.guidance.action, r.guidance.evidence)}</span>
        <span class="hp-pri-why"><span class="hp-gi-reason">${esc(d.reason)}</span><span class="hp-gi-next">${d.kind ? `<span class="hp-kind">${esc(d.kind)}</span> · ` : ''}${esc(d.next)}</span></span>
        <span class="hp-pri-impact"><span class="hp-gi-impact-k">${esc(d.impactLabel)}</span><strong>${pct(r.broker.weight, 1)}${d.after !== null ? ` → ${pct(d.after, 1)}` : ''}</strong></span>
        <button type="button" class="q-btn q-btn--link hp-pri-go" data-hp-review="${esc(r.symbol)}">Review</button>
      </li>`;
    }).join('')}</ol>${rows.length > 3 ? `<p class="hp-fine">${rows.length - 3} more in <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-tab-go="guidance">Guidance</button>.</p>` : ''}`;
  }

  // ---------------------------------------------------------------- tabs
  const TABS = [
    { key: 'overview', label: 'Overview' },
    { key: 'holdings', label: 'Holdings' },
    { key: 'guidance', label: 'Guidance' },
    { key: 'activity', label: 'Activity' },
    { key: 'review', label: 'Review' },
    { key: 'news', label: 'News & events' },
  ];
  // Older links keep working: the analysis tab became Review, and decisions have their own tab.
  const LEGACY_TAB = { analysis: 'review', plan: 'guidance' };
  const tabFromHash = () => {
    try {
      const sub = String(root.location.hash || '').replace('#', '').split('/')[1];
      const key = LEGACY_TAB[sub] || sub;
      return TABS.some((t) => t.key === key) ? key : null;
    } catch (_) { return null; }
  };

  function tabsHtml(pf) {
    const X = api_.ext || {};
    const counts = Object.assign({ holdings: (pf.positions || []).length, guidance: decisionRows(pf).length }, X.counts ? X.counts(pf) : {});
    return `<div class="hp-tabs" role="tablist" aria-label="My Portfolio views">${TABS.map((t) => {
      const on = state.tab === t.key;
      const n = counts[t.key];
      return `<button type="button" role="tab" class="hp-tab${on ? ' is-on' : ''}" id="hp-tab-${t.key}" data-hp-tab="${t.key}" aria-controls="hp-panel-${t.key}" aria-selected="${on}" tabindex="${on ? 0 : -1}">${t.label}${n ? ` <span class="hp-tab-n">${n}</span>` : ''}</button>`;
    }).join('')}</div>`;
  }
  const panel = (key, inner) => `<div class="hp-tabpanel" role="tabpanel" id="hp-panel-${key}" aria-labelledby="hp-tab-${key}"${state.tab === key ? '' : ' hidden'}>${inner}</div>`;

  function portfolioHtml(view) {
    const pf = view.portfolio;
    const X = api_.ext || {};
    if (!pf.positions || !pf.positions.length) {
      return `${overviewHtml(pf)}
        <div class="q-state hp-state" role="status"><h2 class="hp-state-title">No stock or ETF positions in this account</h2>
        <p>Robinhood reports no equity holdings here${pf.account && pf.account.cash != null ? `, and ${money(pf.account.cash, 2)} in cash` : ''}. Options and crypto are not analysed on this page.</p></div>`;
    }
    return `${X.banner ? X.banner(pf) : ''}${tabsHtml(pf)}
      ${panel('overview', X.overview ? X.overview(pf) : `${overviewHtml(pf)}${prioritiesPanelHtml(pf)}`)}
      ${panel('holdings', tableHtml(pf))}
      ${panel('guidance', guidanceHtml(pf))}
      ${panel('activity', X.activity ? X.activity(pf) : '')}
      ${panel('review', X.review ? X.review(pf, { contribution: todayHtml(pf), risk: exposureHtml(pf) }) : `${todayHtml(pf)}${exposureHtml(pf)}`)}
      ${panel('news', X.news ? X.news(pf) : '')}
      <p class="hp-fine hp-foot">QUANTA's guidance is research, not investment advice. Model evidence is shown per holding; unless it says "validated", no predictive edge has been demonstrated.</p>`;
  }

  // Fallback when the insight modules are not loaded: the three priorities alone.
  function prioritiesPanelHtml(pf) {
    return `<section class="q-panel hp-panel hp-priorities" aria-labelledby="hp-pri-h"><div class="q-body"><h2 class="q-head-title" id="hp-pri-h">Priorities</h2>${prioritiesHtml(pf)}</div></section>`;
  }

  // ---------------------------------------------------------------- detail panel
  function drawerHtml(r, pf) {
    const order = tableRows(pf);
    const i = order.findIndex((x) => x.symbol === r.symbol);
    const prev = i > 0 ? order[i - 1] : null;
    const next = i >= 0 && i < order.length - 1 ? order[i + 1] : null;
    const nav = (x, dir, label) => `<button type="button" class="icon-btn hp-icon-btn" data-hp-drawer="${dir}"${x ? '' : ' disabled'} aria-label="${esc(x ? `${label}: ${x.symbol}` : `No ${label.toLowerCase()} holding`)}">${dir === 'prev' ? CHEV_L : CHEV_R}</button>`;
    return `<header class="hp-drawer-h">
        <div class="hp-drawer-t"><h2 id="hp-drawer-title" tabindex="-1"><span class="hp-sym">${esc(r.symbol)}</span> <span class="hp-drawer-n">${esc(r.broker.name || '')}</span></h2>
          <p class="hp-drawer-s">${actionChip(r.guidance.action, r.guidance.evidence)} <span>${pct(r.broker.weight, 1)} of the account${i >= 0 ? ` · ${i + 1} of ${order.length} in Holdings` : ''}</span></p></div>
        <div class="hp-drawer-nav">${nav(prev, 'prev', 'Previous')}${nav(next, 'next', 'Next')}<button type="button" class="icon-btn hp-icon-btn" data-hp-drawer="close" aria-label="Close details">${CLOSE}</button></div>
      </header>
      <div class="hp-drawer-b">${detailHtml(r, pf)}</div>`;
  }

  // ---------------------------------------------------------------- DOM
  const $ = (sel) => (typeof document !== 'undefined' ? document.querySelector(sel) : null);
  function announce(text) { const el = $('#hp-status'); if (el) el.textContent = text; }

  function paintHead() {
    const head = $('#hp-actions');
    if (head && !head.contains(document.activeElement)) head.innerHTML = headActionsHtml((state.view && state.view.status) || state.status);
  }

  function render() {
    const rootEl = $('#hp-root'); const head = $('#hp-actions');
    if (!rootEl) return;
    const active = document.activeElement;
    const focusKey = active && ['data-hp', 'data-hp-sort', 'data-hp-filter', 'data-hp-gfilter', 'data-hp-toggle', 'data-hp-inspect', 'data-hp-tab', 'data-hp-review', 'data-hp-x'].find((key) => active.hasAttribute(key));
    const focusValue = focusKey && active.getAttribute(focusKey);
    const focusId = active && active.id;
    const selRange = active && typeof active.selectionStart === 'number' ? [active.selectionStart, active.selectionEnd] : null;
    const disclosure = active && active.closest('[data-hp-disclosure]');
    const disclosureKey = disclosure && disclosure.dataset.hpDisclosure;
    const openDisclosures = [...rootEl.querySelectorAll('details[data-hp-disclosure][open]')].map((el) => el.dataset.hpDisclosure);
    const oldScroll = $('.hp-table-scroll');
    const scrollLeft = oldScroll ? oldScroll.scrollLeft : 0;
    const status = (state.view && state.view.status) || state.status;
    if (head) head.innerHTML = headActionsHtml(status);
    const updated = $('#hp-updated');
    const pfv = state.view && state.view.portfolio;
    if (updated) { updated.innerHTML = pfv && !state.error ? updatedHtml(pfv) : ''; updated.hidden = !pfv || !!state.error; }
    rootEl.setAttribute('aria-busy', state.busy ? 'true' : 'false');
    if (state.error) {
      rootEl.innerHTML = `<div class="q-state q-state--warn hp-state" role="alert"><h2 class="hp-state-title">${esc(state.error.title || 'Something went wrong')}</h2><p>${esc(state.error.message || '')}</p>
        <div class="q-empty-actions"><button type="button" class="q-btn q-btn--primary" data-hp="check">Try again</button></div></div>`;
      return;
    }
    if (state.view && state.view.portfolio) {
      const scrollY = typeof window !== 'undefined' ? window.scrollY : 0;
      rootEl.innerHTML = portfolioHtml(state.view);
      rootEl.querySelectorAll('details[data-hp-disclosure]').forEach((el) => { el.open = openDisclosures.includes(el.dataset.hpDisclosure); });
      if (api_.ext && api_.ext.afterRender) api_.ext.afterRender(state.view.portfolio);
      const newScroll = $('.hp-table-scroll');
      if (newScroll) newScroll.scrollLeft = scrollLeft;
      // An id is unique, so it wins: two controls can share a data-hp action
      // (the header's Refresh and Guidance's "Refresh data").
      const focused = (focusId && document.getElementById(focusId))
        || (focusKey ? [...document.querySelectorAll(`[${focusKey}]`)].find((el) => el.getAttribute(focusKey) === focusValue)
          : disclosureKey ? [...rootEl.querySelectorAll('[data-hp-disclosure]')].find((el) => el.dataset.hpDisclosure === disclosureKey)?.querySelector('summary') : null);
      if (focused) {
        focused.focus({ preventScroll: true });
        if (selRange && focused === active || (selRange && focused.id && focused.id === focusId)) {
          try { focused.setSelectionRange(selRange[0], selRange[1]); } catch (_) { /* number inputs have no caret */ }
        }
      }
      if (typeof window !== 'undefined' && window.scrollY !== scrollY) window.scrollTo({ top: scrollY, behavior: 'instant' });
      syncDrawer();
      return;
    }
    if (state.busy && !status) return;
    rootEl.innerHTML = stateCardHtml(status);
  }

  function schedule() {
    clearTimeout(state.timer);
    const a = state.view && state.view.status && state.view.status.analysis;
    const awaiting = state.status && state.status.state === 'awaiting_auth';
    const visible = !!$('#view-holdings.active');
    if (visible && ((a && a.state === 'running') || awaiting)) {
      state.timer = setTimeout(() => (awaiting ? loadStatus() : loadView()), POLL_MS);
    }
  }

  async function loadView() {
    const res = await api('GET', '');
    if (res.ok) {
      state.view = res.data;
      state.status = res.data.status;
      state.error = null;
      if (!res.data.portfolio) state.view = null;
    }
    render();
    schedule();
  }

  // Index quotes for the market comparison come from this server's own market
  // tape (/api/dashboard, cached there). Optional: the page never waits on it,
  // and without same-session quotes the comparison is simply not drawn.
  const MARKET_TTL = 180000;
  async function loadMarket(force) {
    if (state.marketBusy || typeof fetch !== 'function') return;
    if (!force && state.market && Date.now() - state.market.at < MARKET_TTL) return;
    state.marketBusy = true;
    let quotes = null;
    try {
      const resp = await fetch('/api/dashboard?market=US', { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' });
      const data = resp.ok ? await resp.json() : null;
      quotes = data && data.market && Array.isArray(data.market.quotes) ? data.market.quotes : null;
    } catch (_) { quotes = null; }
    state.market = { at: Date.now(), quotes };
    state.marketBusy = false;
    if (state.view && state.view.portfolio) render();
  }

  async function loadAccounts() {
    const res = await api('GET', '/accounts');
    if (!res.ok) { await loadStatus(true); return false; }
    state.accounts = res.data.accounts || [];
    if (!state.accounts.some((a) => a.handle === state.account)) {
      // Default to an account the agent cannot trade in (the main account) when there is one.
      const main = state.accounts.find((a) => a.can_trade_via_agent === false);
      state.account = (main || state.accounts[0] || {}).handle || null;
    }
    return !!state.account;
  }

  async function refresh(returnFocusId) {
    if (state.busy) return;
    if (!state.account && !(await loadAccounts())) return;
    state.busy = true; announce('Reading your Robinhood portfolio…'); render();
    const res = await api('POST', '/refresh', { account: state.account });
    state.busy = false;
    if (res.ok) {
      state.view = res.data; state.status = res.data.status; state.error = null;
      if (api_.ext && api_.ext.reset) api_.ext.reset(true);
      const n = (res.data.portfolio && res.data.portfolio.positions || []).length;
      announce(`Portfolio loaded: ${n} holdings. QUANTA is analysing them.`);
      loadMarket(true);
    } else {
      // Never keep showing an old portfolio as if it were current after a failed read.
      state.view = null;
      await loadStatus(true);
      state.error = res.status === 401 ? null : { title: 'Robinhood could not be read', message: res.data.error };
      announce(res.data.error || 'Portfolio refresh failed');
    }
    render();
    // The contextual recovery button is replaced while loading. Return focus
    // only if the user has not moved it elsewhere during the request.
    if (returnFocusId && document.activeElement === document.body) {
      const returnFocus = document.getElementById(returnFocusId);
      if (returnFocus) returnFocus.focus({ preventScroll: true });
    }
    schedule();
  }

  async function loadStatus(noCascade) {
    const res = await api('GET', '/status');
    if (!res.ok) {
      state.error = { title: 'My Portfolio is unavailable', message: res.data.error };
      render(); return;
    }
    state.status = res.data; state.error = null;
    const st = res.data.state;
    if ((st === 'connected' || st === 'partial') && !noCascade) {
      // The account list is a live broker call; the view is cached. Load both at once,
      // and redraw only the header's account picker when the list arrives.
      if (res.data.account) { await Promise.all([loadAccounts().then(paintHead), loadView()]); return; }
      render();
      await refresh();
      return;
    }
    if (st !== 'connected' && st !== 'partial' && st !== 'provider_error') state.view = null;
    render();
    schedule();
  }

  async function connect() {
    state.busy = true; render();
    const res = await api('POST', '/connect');
    state.busy = false;
    if (res.ok && res.data.authorize_url && /^https:\/\/([a-z0-9-]+\.)*robinhood\.com\//i.test(res.data.authorize_url)) {
      announce('Opening Robinhood sign-in…');
      root.location.assign(res.data.authorize_url);
      return;
    }
    await loadStatus(true);
    state.error = { title: 'Could not start the Robinhood sign-in', message: res.data.error || 'Unexpected response from the server.' };
    render();
  }

  async function disconnect() {
    state.busy = true; render();
    await api('POST', '/disconnect');
    state.busy = false;
    state.view = null; state.accounts = []; state.account = null; state.expanded.clear(); state.filter = null; state.holdFilter = null;
    if (api_.ext && api_.ext.reset) api_.ext.reset();
    announce('Disconnected from Robinhood. Credentials and holdings were cleared.');
    await loadStatus(true);
  }

  function setHidden(on) {
    state.hidden = !!on;
    saveHidden(state.hidden);
    render();
    announce(state.hidden ? 'Amounts hidden.' : 'Amounts shown.');
    const btn = $('[data-hp="privacy"]');
    if (btn) btn.focus();
  }

  // ---------------------------------------------------------------- tabs
  // Tabs switch visibility only: every panel stays rendered, so sort, filter,
  // open disclosures and horizontal table scroll survive a round trip. Each tab
  // remembers its own vertical scroll; the URL carries the tab (#holdings/plan).
  function setTab(key, { focus = false, scroll = true } = {}) {
    if (!TABS.some((t) => t.key === key) || typeof document === 'undefined') return;
    const bar = $('.hp-tabs');
    const prev = state.tab;
    if (prev !== key && typeof window !== 'undefined') state.tabScroll[prev] = window.scrollY;
    state.tab = key;
    document.querySelectorAll('[data-hp-tab]').forEach((t) => {
      const on = t.dataset.hpTab === key;
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll('.hp-tabpanel').forEach((el) => { el.hidden = el.id !== `hp-panel-${key}`; });
    try {
      // A tab may keep its own state after its key (Activity: range, view, stocks).
      const extra = api_.ext && api_.ext.tabHash ? api_.ext.tabHash(key) : '';
      const want = key === 'overview' ? '#holdings' : `#holdings/${key}${extra}`;
      if (root.location.hash !== want && root.history) root.history.replaceState({ view: 'holdings' }, '', want);
    } catch (_) { /* sandboxed frames refuse history writes */ }
    // The tab bar is sticky: focusing it must not scroll the page (the page's
    // scroll-padding would otherwise treat a stuck tab as hidden and jump).
    if (focus) { const t = document.getElementById(`hp-tab-${key}`); if (t) t.focus({ preventScroll: true }); }
    if (scroll && prev !== key && bar && typeof window !== 'undefined') {
      // Back to where this tab was left, or to the top of the tab when it was
      // left above the fold; never jump the page when the tabs are in view.
      const offset = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topbar-h')) || 56;
      const top = bar.getBoundingClientRect().top + window.scrollY - offset;
      const saved = state.tabScroll[key];
      const target = saved != null ? saved : Math.min(window.scrollY, top);
      if (Math.abs(target - window.scrollY) > 1) window.scrollTo({ top: Math.max(0, target), behavior: 'instant' });
    }
  }

  // ---------------------------------------------------------------- detail panel
  // A native modal dialog: it traps focus, closes on Esc, and leaves the page
  // (and the table's sort, filter and scroll) exactly as it was. Full screen on
  // phones. Focus returns to the control that opened it.
  function drawerEl() {
    let d = document.getElementById('hp-drawer');
    if (!d) {
      d = document.createElement('dialog');
      d.id = 'hp-drawer';
      d.className = 'hp-drawer';
      d.setAttribute('aria-labelledby', 'hp-drawer-title');
      document.getElementById('view-holdings').appendChild(d);
      d.addEventListener('close', () => {
        const back = state.drawerReturn;
        state.drawerReturn = null;
        const row = [...document.querySelectorAll('[data-hp-toggle]')].find((el) => el.dataset.hpToggle === state.selected);
        // Opened from the table and walked to another holding: land on the row
        // of the holding last shown, where the reader now is.
        const walked = back && back.dataset && back.dataset.hpToggle && back.dataset.hpToggle !== state.selected;
        const target = back && back.isConnected && !back.closest('[hidden]') && !walked ? back : row;
        if (target) target.focus({ preventScroll: true });
      });
      // A click on the backdrop (outside the panel box) closes it.
      d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
      // showModal() keeps focus off the page, but Chrome lets Tab leave the last
      // control for the browser's own UI. Tab and Shift+Tab wrap inside the panel.
      d.addEventListener('keydown', (e) => {
        if (e.key !== 'Tab') return;
        const items = [...d.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
          .filter((el) => el.getClientRects().length && !el.closest('[hidden]') && !(el.closest('details:not([open])') && el.tagName !== 'SUMMARY'));
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        const at = document.activeElement;
        if (e.shiftKey && (at === first || at === d || !d.contains(at) || at.id === 'hp-drawer-title')) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && (at === last || !d.contains(at))) { e.preventDefault(); first.focus(); }
      });
    }
    return d;
  }
  function markSelected() {
    document.querySelectorAll('[data-hp-row]').forEach((tr) => tr.classList.toggle('is-selected', tr.dataset.hpRow === state.selected));
  }
  function openDrawer(symbol, trigger) {
    const pf = state.view && state.view.portfolio;
    const r = pf && (pf.positions || []).find((x) => x.symbol === symbol);
    if (!r || typeof document === 'undefined') return;
    const d = drawerEl();
    if (!d.open) state.drawerReturn = trigger || document.activeElement;
    state.selected = symbol;
    d.innerHTML = drawerHtml(r, pf);
    const body = d.querySelector('.hp-drawer-b');
    if (body) body.scrollTop = 0;
    if (!d.open) { if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', ''); }
    const title = d.querySelector('#hp-drawer-title');
    if (title) title.focus({ preventScroll: true });
    markSelected();
  }
  // Keep an open panel current when the page re-renders (analysis progress),
  // without moving its scroll, disclosures or focus.
  function syncDrawer() {
    const d = typeof document !== 'undefined' && document.getElementById('hp-drawer');
    const pf = state.view && state.view.portfolio;
    if (!d || !d.open || !pf) return;
    const r = (pf.positions || []).find((x) => x.symbol === state.selected);
    if (!r) { d.close(); return; }
    const body = d.querySelector('.hp-drawer-b');
    const top = body ? body.scrollTop : 0;
    const openDisclosures = [...d.querySelectorAll('details[open]')].map((el) => el.dataset.hpDisclosure);
    const activeKey = document.activeElement && d.contains(document.activeElement) ? (document.activeElement.dataset.hpDrawer || 'title') : null;
    d.innerHTML = drawerHtml(r, pf);
    d.querySelectorAll('details[data-hp-disclosure]').forEach((el) => { el.open = openDisclosures.includes(el.dataset.hpDisclosure); });
    const nb = d.querySelector('.hp-drawer-b');
    if (nb) nb.scrollTop = top;
    if (activeKey) {
      const el = activeKey === 'title' ? d.querySelector('#hp-drawer-title') : d.querySelector(`[data-hp-drawer="${activeKey}"]`);
      if (el) el.focus({ preventScroll: true });
    }
  }
  function stepDrawer(dir) {
    const pf = state.view && state.view.portfolio;
    if (!pf) return;
    const order = tableRows(pf);
    const i = order.findIndex((x) => x.symbol === state.selected);
    const next = order[i + (dir === 'next' ? 1 : -1)];
    if (!next) return;
    openDrawer(next.symbol);
    const btn = document.querySelector(`#hp-drawer [data-hp-drawer="${dir}"]`);
    if (btn && !btn.disabled) btn.focus({ preventScroll: true });
    announce(`${next.symbol}, ${order.indexOf(next) + 1} of ${order.length}.`);
  }

  function bind() {
    if (typeof document === 'undefined' || bind.done) return;
    bind.done = true;
    const view = document.getElementById('view-holdings');
    if (!view) return;
    view.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-hp]');
      if (btn) {
        const act = btn.dataset.hp;
        if (act === 'connect') connect();
        else if (act === 'disconnect') disconnect();
        else if (act === 'refresh') refresh(btn.id);
        else if (act === 'check') { state.error = null; loadStatus(); }
        else if (act === 'privacy') setHidden(!state.hidden);
        else if (act === 'sort-direction') { state.dir = (state.dir || SORTS[state.sort].dir) === 'asc' ? 'desc' : 'asc'; render(); }
        return;
      }
      const s = e.target.closest('[data-hp-sort]');
      if (s) {
        const k = s.dataset.hpSort;
        if (state.sort === k) state.dir = (state.dir || SORTS[k].dir) === 'asc' ? 'desc' : 'asc';
        else { state.sort = k; state.dir = null; }
        render(); return;
      }
      const tabBtn = e.target.closest('[data-hp-tab]');
      if (tabBtn) { setTab(tabBtn.dataset.hpTab); return; }
      const go = e.target.closest('[data-hp-tab-go]');
      if (go) { setTab(go.dataset.hpTabGo, { focus: true }); return; }
      const gf = e.target.closest('[data-hp-gfilter]');
      if (gf) {
        const key = gf.dataset.hpGfilter || null;
        state.gFilter = state.gFilter === key ? null : key;
        render();
        announce(state.gFilter ? `Guidance: ${GROUP_LABEL[state.gFilter] || state.gFilter}.` : 'Guidance: all holdings.');
        return;
      }
      const f = e.target.closest('[data-hp-filter]');
      if (f) {
        // In the table's own toolbar a filter toggles; from anywhere else it
        // opens Holdings with that filter applied.
        const inTable = !!f.closest('.hp-holdings');
        const key = f.dataset.hpFilter || null;
        state.filter = inTable && state.filter === key ? null : key;
        render();
        if (!inTable) setTab('holdings', { focus: true });
        announce(state.filter ? 'Holdings filtered by action.' : 'Showing all holdings.');
        return;
      }
      const review = e.target.closest('[data-hp-review]');
      if (review) {
        const sym = review.dataset.hpReview;
        // The decision may be filtered out of view: show every decision first.
        if (state.gFilter && !['exit_review', 'trim_review', 'consider_adding'].includes(state.gFilter)) { state.gFilter = null; render(); }
        setTab('guidance', { scroll: false });
        const item = [...document.querySelectorAll('#hp-panel-guidance .hp-gi')].find((el) => el.dataset.hpDisclosure === `gi-${sym}`);
        if (item) {
          item.open = true;
          item.scrollIntoView({ block: 'start' });
          const sm = item.querySelector('summary');
          if (sm) sm.focus({ preventScroll: true });
        }
        return;
      }
      const inspect = e.target.closest('[data-hp-inspect]');
      if (inspect) { openDrawer(inspect.dataset.hpInspect, inspect); return; }
      const dact = e.target.closest('[data-hp-drawer]');
      if (dact) {
        if (dact.dataset.hpDrawer === 'close') drawerEl().close();
        else stepDrawer(dact.dataset.hpDrawer);
        return;
      }
      const rsch = e.target.closest('[data-hp-research]');
      if (rsch) { if (typeof root.openTickerTearSheet === 'function') root.openTickerTearSheet(rsch.dataset.hpResearch); return; }
      const row = e.target.closest('[data-hp-row]');
      if (row) openDrawer(row.dataset.hpRow, row.querySelector('[data-hp-toggle]'));
    });
    // Tooltips are dismissible without moving focus or the pointer (WCAG 1.4.13).
    view.addEventListener('keydown', (e) => {
      const tabBtn = e.target.closest && e.target.closest('[data-hp-tab]');
      if (tabBtn && ['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) {
        const keys = TABS.map((t) => t.key);
        const i = keys.indexOf(tabBtn.dataset.hpTab);
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? keys.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + keys.length) % keys.length;
        setTab(keys[next], { focus: true });
        e.preventDefault();
        return;
      }
      if (e.key !== 'Escape') return;
      const t = e.target.closest && e.target.closest('.hp-icon-btn, .hp-conn');
      if (t) t.classList.add('is-tip-off');
    });
    const tipBack = (e) => { const t = e.target.closest && e.target.closest('.is-tip-off'); if (t) t.classList.remove('is-tip-off'); };
    view.addEventListener('focusout', tipBack);
    view.addEventListener('mouseout', tipBack);
    view.addEventListener('change', (e) => {
      if (e.target.id === 'hp-sort') { state.sort = e.target.value; state.dir = null; render(); }
      if (e.target.id === 'hp-account') { state.account = e.target.value; state.selected = null; refresh(); }
    });
  }

  function open() {
    bind();
    const fromUrl = tabFromHash();
    if (fromUrl && fromUrl !== state.tab) { state.tab = fromUrl; if (state.view) setTab(fromUrl, { scroll: false }); }
    loadMarket();
    if (state.opened && state.view) { loadView(); return; }
    state.opened = true;
    loadStatus();
  }

  const api_ = {
    open, sortRows, setHidden, headActionsHtml, stateCardHtml, portfolioHtml, tableHtml, detailHtml,
    overviewHtml, assessmentHtml, prioritiesHtml, drawerHtml, tabsHtml,
    todayHtml, exposureHtml, guidanceHtml, heroHtml, holdTableHtml, reasonTag, updatedHtml, ext: null,
    // Shared vocabulary for the mp-*.js modules: the same formatters, masks and
    // calls, so a figure reads the same on every tab and in the export.
    _h: {
      actionLabel,
      modelFlag,
      evidenceValidated,
      esc, num, NA, priv, money, signedMoney, price, pct, qty, signedPts, dirClass, timeText, dayText, plural, tag,
      actionChip, stanceChip, decisionRows, decisionSummary, prioritiesHtml, overviewHtml, periodText, sessionOf, moveParts, marketCompare, sectorOf, limitedSector,
      dataStatusHtml, dayPct, byWeight, decisionOrder, heroOf, sizedScenario, reasonTag, weightBarHtml, chartPercent, lateQuotes, GROUP_LABEL, KIND_LABEL, TRI_UP, TRI_DOWN,
      api: (...a) => api(...a), announce, setTab, openDrawer, render: () => render(), TABS, ACTION_TONE,
      filterHoldings(label, symbols) {
        state.holdFilter = symbols && symbols.length ? { label, symbols } : null;
        state.filter = null;
        render();
        setTab('holdings', { focus: true });
        announce(state.holdFilter ? `Holdings filtered to ${label}.` : 'Showing all holdings.');
      },
      refreshView: () => refresh(),
    },
    _state: state, _api: api, _parts: { moveParts, betaParts, stopParts, marketCompare },
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api_;
  if (typeof window !== 'undefined') {
    window.QuantaHoldings = api_;
    // A direct load of /#holdings opens the view before this deferred script ran.
    const kick = () => { if (document.querySelector('#view-holdings.active')) open(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', kick);
    else kick();
  }
}(typeof window !== 'undefined' ? window : globalThis));
