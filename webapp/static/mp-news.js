/* News & Events — what is scheduled and what has been published, for these holdings; and
 * the conditions the viewer asked to be told about.
 *
 * Scheduled events and triggered alerts are different objects and live in different lists.
 * A scheduled event is a fact about the calendar. An alert is the viewer's own condition
 * found true in the data currently on the page. Alerts are evaluated in this page only:
 * there is no background monitor, e-mail or push, and the page says so.
 */
(function (root) {
  'use strict';

  const WATCH_KEY = 'quanta.holdings.watch';
  const DAY = 864e5;
  const DISMISS_DAYS = 14;
  const readJson = (k, d) => { try { const v = JSON.parse(root.localStorage.getItem(k)); return v && typeof v === 'object' ? v : d; } catch (_) { return d; } };
  const writeJson = (k, v) => { try { root.localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* per-viewer convenience */ } };

  const RULE_TYPES = {
    weight_above: { label: 'A position is larger than', unit: '% of the account', dflt: 15, optSymbol: true },
    day_move: { label: 'A holding moves more than', unit: '% in a session', dflt: 5 },
    loss_from_cost: { label: 'A holding is down more than', unit: '% from cost', dflt: 20 },
    earnings_within: { label: 'A holding reports earnings within', unit: 'days', dflt: 7 },
    call_changed: { label: 'QUANTA’s call on a holding changes', unit: 'days back', dflt: 7 },
    filing_material: { label: 'A material SEC filing appears', unit: 'days back', dflt: 7 },
    cash_below: { label: 'Cash falls below', unit: '% of the account', dflt: 5 },
  };

  const KIND_LABEL = { earnings: 'Earnings', filing: 'Filing', dividend: 'Dividend', macro: 'Macro', news: 'News' };
  const STATUS_LABEL = { confirmed: 'Confirmed', estimate: 'Estimate', reported: 'Reported' };
  // "Today & this week" is one view with a subheading per bucket; later and recent are their own.
  const BUCKETS = [['soon', 'Today & this week'], ['later', 'Later, to 31 days'], ['recent', 'Recent filings & news']];

  // ---------------------------------------------------------------- alert evaluation (pure)
  function evaluateAlerts(pf, events, rules, now) {
    const out = [];
    const rows = (pf && pf.positions) || [];
    const o = (pf && pf.overview) || {};
    const nowMs = now || Date.now();
    const push = (rule, subject, title, detail, extra) => out.push(Object.assign({ id: `${rule.id}|${subject}`, ruleId: rule.id, title, detail, symbols: [], tab: 'holdings' }, extra || {}));
    const pctOf = (v) => `${(v * 100).toFixed(1)}%`;
    (rules || []).forEach((rule) => {
      const t = Number(rule.value);
      if (!Number.isFinite(t) || !RULE_TYPES[rule.type]) return;
      const sym = rule.symbol ? String(rule.symbol).toUpperCase() : '';
      if (rule.type === 'weight_above') {
        rows.filter((r) => (!sym || r.symbol === sym) && Number.isFinite(r.broker.weight) && r.broker.weight * 100 > t)
          .forEach((r) => push(rule, r.symbol, `${r.symbol} is ${pctOf(r.broker.weight)} of the account`, `Your condition: a position larger than ${t}%.`, { symbols: [r.symbol] }));
      } else if (rule.type === 'day_move') {
        rows.forEach((r) => {
          const d = r.broker.day_change; const v = r.broker.market_value;
          if (!Number.isFinite(d) || !Number.isFinite(v) || v - d <= 0) return;
          const p = d / (v - d) * 100;
          if (Math.abs(p) > t) push(rule, r.symbol, `${r.symbol} moved ${p >= 0 ? '+' : '−'}${Math.abs(p).toFixed(1)}% in the latest session`, `Your condition: a move larger than ${t}% in a session. Session change per Robinhood; the cause is not stated.`, { symbols: [r.symbol] });
        });
      } else if (rule.type === 'loss_from_cost') {
        rows.forEach((r) => {
          const p = r.broker.unrealized_pnl_pct;
          if (Number.isFinite(p) && p < -t) push(rule, r.symbol, `${r.symbol} is down ${Math.abs(p).toFixed(1)}% from cost`, `Your condition: down more than ${t}% from cost.`, { symbols: [r.symbol] });
        });
      } else if (rule.type === 'earnings_within') {
        ((events && events.upcoming) || []).filter((i) => i.kind === 'earnings' && Number.isFinite(i.days) && i.days >= 0 && i.days <= t)
          .forEach((i) => push(rule, `${i.symbols[0]}:${i.date}`, `${i.symbols[0]} reports ${i.days === 0 ? 'today' : `in ${i.days} ${i.days === 1 ? 'day' : 'days'}`}`, `Your condition: earnings within ${t} days. Date status: ${STATUS_LABEL[i.status] || i.status}.`, { symbols: i.symbols, tab: 'news' }));
      } else if (rule.type === 'call_changed') {
        (((pf && pf.journal) || {}).changes || []).filter((c) => (nowMs - new Date(`${c.date}T12:00:00`).getTime()) / DAY <= t)
          .forEach((c) => push(rule, `${c.symbol}:${c.date}`, `${c.symbol}: ${c.from_action || 'call'} → ${c.action}`, `Your condition: QUANTA’s call changed within ${t} days. Rule: ${c.basis_text || 'not recorded'}.`, { symbols: [c.symbol] }));
      } else if (rule.type === 'filing_material') {
        ((events && ([...(events.upcoming || []), ...(events.recent || [])])) || []).filter((i) => i.kind === 'filing' && i.material && (nowMs - new Date(`${i.date}T12:00:00`).getTime()) / DAY <= t)
          .forEach((i) => push(rule, i.id, i.title + ' filed', `Your condition: a material SEC filing within ${t} days. ${i.what}`, { symbols: i.symbols, tab: 'news' }));
      } else if (rule.type === 'cash_below') {
        const total = Number(o.total_value); const cash = Number(o.cash);
        if (Number.isFinite(total) && total > 0 && Number.isFinite(cash) && cash / total * 100 < t) push(rule, 'cash', `Cash is ${pctOf(cash / total)} of the account`, `Your condition: cash below ${t}%.`, { tab: 'overview' });
      }
    });
    return out;
  }

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const H = host._h;
    const { esc, priv, pct, plural } = H;
    const ins = X.ins;
    const state = host._state;

    const store = readJson(WATCH_KEY, {});
    ins.watch = { rules: Array.isArray(store.rules) ? store.rules.filter((r) => RULE_TYPES[r.type]) : [], dismissed: store.dismissed || {}, snoozed: store.snoozed || {} };
    if (!['soon', 'later', 'recent'].includes(ins.prefs.newsBucket)) ins.prefs.newsBucket = 'soon';
    ins.prefs.newsKind = ins.prefs.newsKind || 'all';
    const save = () => writeJson(WATCH_KEY, ins.watch);

    // ---------------------------------------------------------------- alerts
    function activeAlerts(pf) {
      const events = ins.events.data && ins.events.data.available ? ins.events.data : null;
      const all = evaluateAlerts(pf, events, ins.watch.rules, Date.now());
      const now = Date.now();
      return all.filter((a) => {
        const dis = ins.watch.dismissed[a.id];
        if (dis && now - dis < DISMISS_DAYS * DAY) return false;
        const snz = ins.watch.snoozed[a.id];
        return !(snz && snz > now);
      });
    }
    X.alertCount = () => {
      const pf = state.view && state.view.portfolio;
      return pf ? activeAlerts(pf).length : 0;
    };

    function alertsSlot(pf) {
      const alerts = activeAlerts(pf);
      const rules = ins.watch.rules;
      const rule = (r) => `<li><span>${esc(RULE_TYPES[r.type].label)} <b class="hp-num">${esc(r.value)}</b> ${esc(RULE_TYPES[r.type].unit)}${r.symbol ? ` <span class="hp-muted">for</span> <span class="hp-sym">${esc(r.symbol)}</span>` : ''}</span><button type="button" class="q-btn q-btn--link" data-hp-x="rule-del:${esc(r.id)}" aria-label="Remove condition: ${esc(RULE_TYPES[r.type].label)} ${esc(r.value)}">Remove</button></li>`;
      const alertLi = (a) => `<li class="mpn-alert"><div><strong>${esc(a.title)}</strong><p>${esc(a.detail)}</p></div>
          <div class="mpn-alert-btns">${a.symbols[0] ? `<button type="button" class="q-btn q-btn--link" data-hp-inspect="${esc(a.symbols[0])}">Details</button>` : ''}<button type="button" class="q-btn q-btn--link" data-hp-x="snooze:${esc(a.id)}">Snooze 1 day</button><button type="button" class="q-btn q-btn--link" data-hp-x="dismiss:${esc(a.id)}">Dismiss</button></div></li>`;
      const opts = Object.entries(RULE_TYPES).map(([k, v]) => `<option value="${k}">${esc(v.label)}…</option>`).join('');
      return `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-al-h">Your alerts</h2>
          <p class="q-head-sub">Conditions you set, checked against the data on this page</p></div></header>
        <div class="q-body">
          <h3 class="hp-h3">Triggered <span>${alerts.length ? `${alerts.length} now` : 'none now'}</span></h3>
          ${alerts.length ? `<ul class="mpn-alerts" role="list">${alerts.map(alertLi).join('')}</ul>` : `<p class="hp-empty">${rules.length ? 'None of your conditions is true right now.' : 'No conditions yet. Add one below and it is checked every time this page loads or refreshes.'}</p>`}
          <h3 class="hp-h3">Your conditions <span>${rules.length}</span></h3>
          ${rules.length ? `<ul class="hp-rows mpn-rules">${rules.map(rule).join('')}</ul>` : ''}
          <form class="mpn-add" data-mp-rule-form data-no-submit>
            <label class="sr-only" for="mp-rule-type">Condition</label><select id="mp-rule-type" data-hp-x="rule-type">${opts}</select>
            <label class="sr-only" for="mp-rule-value">Value</label><input id="mp-rule-value" type="number" inputmode="decimal" min="0" step="any" value="${RULE_TYPES.weight_above.dflt}" data-hp-x="rule-value">
            <span class="mpn-unit" id="mp-rule-unit">${esc(RULE_TYPES.weight_above.unit)}</span>
            <label class="sr-only" for="mp-rule-sym">Ticker (optional)</label><input id="mp-rule-sym" type="text" maxlength="10" placeholder="Any ticker" autocomplete="off" data-hp-x="rule-sym">
            <button type="button" class="q-btn q-btn--primary" data-hp-x="rule-add">Add condition</button>
          </form>
          <p class="hp-fine mpn-delivery"><strong>Where alerts appear:</strong> only on this page, when it is open. ${esc(ins.events.data && ins.events.data.alerts_delivery ? ins.events.data.alerts_delivery.text : 'There is no background monitoring, e-mail or push notification.')} Dismissed alerts return after ${DISMISS_DAYS} days if the condition still holds.</p>
        </div>`;
    }
    X.slotRenderers['news-alerts'] = alertsSlot;

    // ---------------------------------------------------------------- feed
    const etTime = (iso) => {
      try { return new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' ET'; } catch (_) { return iso; }
    };
    function whenText(i) {
      if (i.kind === 'news' || (i.kind === 'filing' && i.time)) return { text: etTime(i.time), title: `${i.time} (${i.tz})` };
      const d = X.dateText(i.date);
      const timing = i.kind === 'earnings' ? `, ${i.when_text}` : i.kind === 'macro' && i.time ? `, ${i.time} ET` : '';
      return { text: `${d}${timing}`, title: `${i.date}${i.time ? ` ${i.time}` : ''} (${i.tz})` };
    }
    function freshText(i) {
      if (i.age_hours != null) return i.age_hours < 1 ? 'under an hour ago' : i.age_hours < 48 ? `${Math.round(i.age_hours)} h ago` : `${Math.round(i.age_hours / 24)} days ago`;
      if (i.days == null) return '';
      if (i.days === 0) return i.kind === 'filing' ? 'filed today' : 'today';
      if (i.days > 0) return `in ${i.days} ${plural(i.days, 'day', 'days')}`;
      return `${-i.days} ${plural(-i.days, 'day', 'days')} ago`;
    }
    function itemHtml(i, open) {
      const w = i.when || whenText(i);
      const chips = (i.exposure.symbols || []).map((s) => `<button type="button" class="hp-sym mpn-sym" data-hp-inspect="${esc(s)}" aria-label="${esc(`${s} details`)}">${esc(s)}</button>`).join(' ');
      // General market items say so; they are not about any one holding.
      const weight = i.exposure.weight != null ? `${pct(i.exposure.weight, 1)} of the account` : (i.relevance === 'market' ? 'General market item, not about one holding' : (i.exposure.scope || ''));
      const est = i.exposure.estimate != null ? `, about ${priv(H.money(i.exposure.estimate, 2))} at your share count` : '';
      const src = i.source && /^https?:\/\//i.test(String(i.source.url || '')) ? `<a href="${esc(i.source.url)}" target="_blank" rel="noopener noreferrer">${esc(i.source.name)}<span class="sr-only"> (opens in a new tab)</span></a>` : esc((i.source || {}).name || '');
      return `<article class="mpn-item mpn-item--${esc(i.kind)}" aria-labelledby="ev-${esc(i.id)}">
        <header class="mpn-h"><span class="mpn-kind">${esc(KIND_LABEL[i.kind] || i.kind)}</span><span class="mpn-status mpn-status--${esc(i.status)}">${esc(i.kind === 'filing' ? 'Filed' : STATUS_LABEL[i.status] || i.status)}</span>
          <time class="mpn-time" datetime="${esc(i.time || i.date)}" title="${esc(w.title)}">${esc(w.text)}</time><span class="mpn-fresh">${esc(freshText(i))}</span></header>
        <h3 id="ev-${esc(i.id)}" class="mpn-title">${esc(i.title)}</h3>
        ${i.kind !== 'news' ? `<p class="mpn-what">${esc(i.what)}</p>` : ''}
        <dl class="mpn-4"><div><dt>Why it matters</dt><dd>${esc(i.why)}</dd></div>
          <div><dt>Exposure</dt><dd>${chips ? `${chips} <span class="hp-muted">${esc(weight)}${est}</span>` : esc(weight)}</dd></div>
          <div><dt>What to watch</dt><dd>${esc(i.watch)}</dd></div></dl>
        <p class="mpn-src"><span>Source: ${src}</span> <span class="hp-muted">· ${esc(i.basis)}</span></p></article>`;
    }

    function feedSlot(pf) {
      const E = ins.events;
      const head = `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-ev-h">Scheduled events and news</h2>
          <p class="q-head-sub">Sourced and dated, for the holdings you own. Times in Eastern unless a source says otherwise.</p></div></header>`;
      if (E.s === 'idle' || (E.s === 'loading' && !E.data)) return `${head}<div class="q-body"><div class="mp-skel mp-skel--chart" role="status"><span class="sr-only">Loading events…</span></div></div>`;
      if (E.s === 'error' && !E.data) return `${head}<div class="q-body"><p class="mp-unavail">Events are unavailable: ${esc(E.error)}. <button type="button" class="q-btn q-btn--link" data-hp-x="retry:events">Try again</button></p></div>`;
      if (E.s === 'missing') return `${head}<div class="q-body"><p class="mp-unavail">Available after a QUANTA restart.</p></div>`;
      const d = E.data;
      if (!d || !d.available) return `${head}<div class="q-body"><p class="mp-unavail">${esc(d.reason)}</p></div>`;
      const b = ins.prefs.newsBucket;
      const k = ins.prefs.newsKind;
      const inBucket = (i) => (b === 'soon' ? i.bucket === 'today' || i.bucket === 'week' : i.bucket === b);
      const pool = b === 'recent' ? d.recent : d.upcoming.filter(inBucket);
      const count = (key) => (key === 'soon' ? (d.counts.today || 0) + (d.counts.week || 0) : d.counts[key] || 0);
      const kindCounts = { all: pool.length };
      pool.forEach((i) => { kindCounts[i.kind] = (kindCounts[i.kind] || 0) + 1; });
      const list = pool.filter((i) => k === 'all' || i.kind === k);
      const seg = `<div class="mpc-seg mpn-buckets" role="group" aria-label="When">${BUCKETS.map(([key, label]) => `<button type="button" class="mpc-segb${b === key ? ' is-on' : ''}" data-hp-x="bucket:${key}" aria-pressed="${b === key}">${label} <span class="hp-num">${count(key)}</span></button>`).join('')}</div>`;
      const kinds = `<div class="hp-filters mpn-kinds" role="group" aria-label="Type">${[['all', 'All'], ...Object.entries(KIND_LABEL)].filter(([key]) => key === 'all' || kindCounts[key]).map(([key, label]) => `<button type="button" class="hp-filter${k === key ? ' is-on' : ''}" data-hp-x="kind:${key}" aria-pressed="${k === key}">${label} <span class="hp-num">${kindCounts[key] || 0}</span></button>`).join('')}</div>`;
      const s = d.sources;
      const health = [
        ['Earnings calendar', s.earnings.status === 'ok' ? 'ok' : 'no dates for these holdings', s.earnings.status === 'ok'],
        ['SEC filings', s.filings.status === 'ok' ? `checked ${s.filings.checked - s.filings.no_cik} ${plural(s.filings.checked - s.filings.no_cik, 'company', 'companies')}${s.filings.no_cik ? `; ${s.filings.no_cik} without SEC filer records (funds)` : ''}` : s.filings.status === 'error' ? 'unreachable' : 'nothing to check', s.filings.status === 'ok'],
        ['Fed and BLS schedules', s.macro.status === 'ok' ? `transcribed ${s.macro.retrieved_on}` : s.macro.status === 'expired' ? `out of date since ${s.macro.last_date}: update the schedule file` : 'file missing', s.macro.status === 'ok'],
        ['News feeds', s.news.status === 'ok' ? `${s.news.articles} headlines scanned` : 'no headlines loaded', s.news.status === 'ok'],
      ].map(([n, t, ok]) => `<li class="${ok ? '' : 'is-warn'}"><b>${esc(n)}</b> ${esc(t)}</li>`).join('');
      const emptyText = b === 'soon' ? 'Nothing scheduled, filed or reported for your holdings in the next seven days.' : b === 'later' ? 'Nothing further out within 31 days.' : 'No recent filings or matching headlines.';
      const group = (items, title) => (items.length ? `${title ? `<h3 class="mpn-group">${title} <span class="hp-num">${items.length}</span></h3>` : ''}<div class="mpn-list">${items.map((i) => itemHtml(i)).join('')}</div>` : '');
      const body = b === 'soon'
        ? `${group(list.filter((i) => i.bucket === 'today'), 'Today')}${group(list.filter((i) => i.bucket === 'week'), 'This week')}`
        : group(list, '');
      return `${head}<div class="q-body">${seg}${kinds}
          ${list.length ? body : `<p class="hp-empty mpn-empty">${emptyText} That covers only the sources listed below.</p>`}
          <details class="hp-more" data-hp-disclosure="news-sources"><summary>What is checked</summary><ul class="mp-defs mpn-health">${health}</ul>
            <p class="hp-fine">Confirmed means the company or agency published it. Estimate means a vendor’s date the company has not confirmed. Reported means a news outlet said it; QUANTA has not verified it and does not say a headline moved a price. Macro dates come from the Federal Reserve and the Bureau of Labor Statistics schedules; no other macro releases are covered.</p></details>
        </div>`;
    }
    X.slotRenderers['news-body'] = feedSlot;

    function news(pf) {
      return `<div class="mp-newsgrid"><section class="q-panel hp-panel mp-feed" data-mp-slot="news-body">${feedSlot(pf)}</section>
        <section class="q-panel hp-panel mp-alerts" data-mp-slot="news-alerts">${alertsSlot(pf)}</section></div>`;
    }

    // ---------------------------------------------------------------- actions
    const stamp = () => { save(); X.paintSlot('news-alerts'); };
    Object.assign(X.actions, {
      bucket(k) { ins.prefs.newsBucket = k; ins.prefs.newsKind = 'all'; X.paintSlot('news-body'); persistPrefs(); },
      kind(k) { ins.prefs.newsKind = k; X.paintSlot('news-body'); persistPrefs(); },
      dismiss(id) { ins.watch.dismissed[id] = Date.now(); stamp(); refreshBadge(); H.announce('Alert dismissed.'); },
      snooze(id) { ins.watch.snoozed[id] = Date.now() + DAY; stamp(); refreshBadge(); H.announce('Alert snoozed for one day.'); },
      'rule-del'(id) { ins.watch.rules = ins.watch.rules.filter((r) => r.id !== id); stamp(); refreshBadge(); H.announce('Condition removed.'); },
      'rule-add'() {
        const type = document.getElementById('mp-rule-type').value;
        const value = Number(document.getElementById('mp-rule-value').value);
        const sym = (document.getElementById('mp-rule-sym').value || '').trim().toUpperCase();
        if (!RULE_TYPES[type] || !Number.isFinite(value) || value < 0) { H.announce('Enter a number for the condition.'); return; }
        ins.watch.rules.push({ id: `r${Date.now().toString(36)}${Math.floor(Math.random() * 1e3)}`, type, value, symbol: RULE_TYPES[type].optSymbol && /^[A-Z0-9.\-]{1,10}$/.test(sym) ? sym : '' });
        stamp(); refreshBadge(); H.announce('Condition added and checked.');
      },
      'rule-type'() {
        const t = RULE_TYPES[document.getElementById('mp-rule-type').value];
        if (!t) return;
        document.getElementById('mp-rule-value').value = t.dflt;
        document.getElementById('mp-rule-unit').textContent = t.unit;
        document.getElementById('mp-rule-sym').hidden = !t.optSymbol;
      },
      'rule-value'() {},
      'rule-sym'() {},
    });
    function persistPrefs() { try { root.localStorage.setItem('quanta.holdings.prefs', JSON.stringify(ins.prefs)); } catch (_) { /* preference only */ } }
    function refreshBadge() {
      const tab = document.getElementById('hp-tab-news');
      if (!tab) return;
      const c = X.counts(state.view && state.view.portfolio).news;
      let n = tab.querySelector('.hp-tab-n');
      if (c) { if (!n) { n = document.createElement('span'); n.className = 'hp-tab-n'; tab.append(' ', n); } n.textContent = c; } else if (n) n.remove();
    }
    const onEvents = () => { X.paintSlot('news-alerts'); refreshBadge(); };
    X.onEvents = onEvents;
    if (typeof document !== 'undefined') {
      const view = document.getElementById('view-holdings');
      if (view && !view._mpNewsBound) {
        view._mpNewsBound = true;
        view.addEventListener('change', (e) => { if (e.target.id === 'mp-rule-type') X.actions['rule-type'](); });
      }
    }

    Object.assign(X, { news, evaluateAlerts, itemHtml, feedSlot, alertsSlot, activeAlerts });
    return X;
  }

  const api = { attach, evaluateAlerts, RULE_TYPES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaNews = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
