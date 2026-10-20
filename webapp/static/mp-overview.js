/* My Portfolio Overview: value, trend, risk and the next action in one screen.
 *
 * Rows: KPI strip (each figure with its own small visual) · performance vs the S&P 500
 * | allocation · holdings | what drove return · action queue with the holdings'
 * catalysts | session movers.
 * Every figure is the server's own output or an exact split of one; a visual whose data
 * is missing shows a one-line empty state instead of a guess.
 *
 * Linked highlighting: pointing at a holding anywhere (a table row, a mover, a treemap
 * tile, a contribution bar, a risk driver) marks it everywhere, and its allocation slice.
 */
(function (root) {
  'use strict';

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const H = host._h;
    const { esc, num, NA, priv, money, signedMoney, pct, dirClass, plural } = H;
    const ins = X.ins;
    const state = host._state;
    const Q = () => root.QuantaCharts;
    ins.ov = { sort: 'weight', dir: 'desc', pin: null, counted: false };
    const reduced = () => { try { return root.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; } };

    // The portfolio risk rule's bands (analytics.risk_level): points 0-1, 2-3, 4-5, 6 and up.
    const RISK_ZONES = [{ key: 'low', label: 'Low', from: 0, to: 1 }, { key: 'moderate', label: 'Moderate', from: 2, to: 3 },
      { key: 'elevated', label: 'Elevated', from: 4, to: 5 }, { key: 'high', label: 'High', from: 6, to: 9 }];
    const RISK_MAX = 9;
    const zoneOf = (level) => (RISK_ZONES.find((z) => z.label === level) || {}).key || 'none';
    X.RISK_ZONES = RISK_ZONES;
    X.RISK_MAX = RISK_MAX;
    X.riskGauge = (risk, o = {}) => {
      const Ch = Q();
      if (!Ch || !risk || num(risk.points) === null) return '';
      return Ch.riskGaugeHtml({ zones: RISK_ZONES, points: Math.min(RISK_MAX, risk.points), ghost: o.ghost, size: o.size,
        ariaLabel: o.ariaLabel || `Portfolio risk ${risk.level}: ${risk.points} of ${RISK_MAX} points on QUANTA’s rule. Bands: Low 0 to 1, Moderate 2 to 3, Elevated 4 to 5, High 6 or more.` });
    };

    // ------------------------------------------------------------ KPI strip
    const ACT_PERIOD = { day: 'day', week: 'week', month: 'month', '3month': '3month', year: 'year', ytd: 'ytd', YTD: 'ytd', all: 'all' };
    const COUNT_FMT = { money2: (v) => money(v, 2), pct1s: (v) => pct(v, 1, true, true), pct1: (v) => pct(v, 1) };
    const counter = (v, fmt) => (num(v) === null || state.hidden ? '' : ` data-count="${Number(v)}" data-count-fmt="${fmt}"`);

    // A risk driver as a short tag, and where clicking it goes.
    function driverTag(text, pf) {
      const t = String(text);
      const top = ((pf.metrics || {}).concentration || {}).top;
      let m = t.match(/^Largest position is (\d+%)/);
      if (m) return { label: `Largest position ${m[1]}`, go: 'conc', sym: top ? top.symbol : null };
      m = t.match(/^(.+?) is (\d+%) of the portfolio$/);
      if (m) return { label: `${m[1]} ${m[2]}`, go: 'sector', sector: m[1] };
      m = t.match(/^Holdings beta ([\d.]+)/);
      if (m) return { label: `Beta ${m[1]}`, go: 'review' };
      m = t.match(/^Annualised volatility (\d+%)/);
      if (m) return { label: `Volatility ${m[1]}`, go: 'review' };
      m = t.match(/^Loss to stops would be (\d+%)/);
      if (m) return { label: `Loss at stops ${m[1]}`, go: 'review' };
      return { label: t.replace(/ of the portfolio$/, ''), go: 'review' };
    }

    function kpiInner(pf) {
      const o = pf.overview || {};
      const u = o.unrealized_pnl || {};
      const m = pf.metrics || {};
      const rows = pf.positions || [];
      const Ch = Q();
      const ses = H.sessionOf(pf);
      const cmp = H.marketCompare(pf);
      const dc = num(o.day_change);
      const cell = (k, body, cls = '') => `<div class="hp-kpi${cls}"><dt>${k}</dt>${body}</div>`;
      // Total value: the session move under it, and today's shares over the past month.
      const perf = ins.perf;
      const m1 = perf.data && perf.data.simulation && perf.data.simulation['1M'];
      const spark = perf.s === 'ok' && m1 && m1.available && Array.isArray(m1.value)
        ? `<dd class="mpk-g" title="Today’s shares valued over the past month (a simulation, not account history)">${Ch ? Ch.areaSparkHtml(m1.value, 'kpi-value') : ''}<span class="mpk-gl">1 month, today’s shares</span></dd>`
        : perf.s === 'idle' || perf.s === 'loading' ? '<dd class="mpk-g"><span class="mp-skel mpk-skel"></span></dd>' : '';
      const today = dc === null
        ? '<dd class="hp-today">Previous close not reported</dd>'
        : `<dd class="hp-today ${dirClass(dc)}">${dc > 0 ? H.TRI_UP : dc < 0 ? H.TRI_DOWN : ''}<b>${signedMoney(dc, 2)}</b>${o.day_change_pct != null ? ` <b>${priv(pct(o.day_change_pct, 2, false, true))}</b>` : ''} <span>${ses.live ? 'today' : 'last session'}</span>${cmp.status === 'ok' ? `<span class="hp-kpi-vs">S&amp;P 500 ${pct(cmp.spy.change_pct, 2, false, true)}</span>` : ''}</dd>`;
      const value = cell('Total value', `<dd class="hp-kpi-v mpk-hero"${counter(o.total_value, 'money2')}>${money(o.total_value, 2)}</dd>${today}${spark}
        <dd class="hp-kpi-s">${esc(o.holdings != null ? String(o.holdings) : '--')} holdings${num(o.invested_value) !== null ? ` · cash ${money(o.cash, 0)}` : ''}</dd>`, ' hp-kpi--lead');

      // Return on cost: market value against cost basis, for the holdings that report one.
      const covered = rows.filter((r) => num(r.broker.cost_basis) !== null && num(r.broker.unrealized_pnl) !== null);
      const basis = covered.reduce((t, r) => t + r.broker.cost_basis, 0);
      const noBasis = rows.filter((r) => r.broker.cost_basis == null && r.broker.average_cost == null).map((r) => r.symbol);
      const roc = u.total != null && basis > 0 ? u.total / basis : null;
      let bullet = '';
      if (roc !== null) {
        const val = basis + u.total;
        const max = Math.max(basis, val);
        bullet = `<dd class="mpk-g"><span class="mpk-bullet" role="img" aria-label="${esc(state.hidden ? `Market value against cost basis: ${pct(roc, 1, true, true)}` : `Market value ${money(val, 0)} against cost basis ${money(basis, 0)}`)}"><i class="mpk-bullet-cost" style="--s:${(basis / max).toFixed(4)}"></i><i class="mpk-bullet-val ${val >= basis ? 'is-up' : 'is-down'} mpo-anim" style="--s:${(val / max).toFixed(4)}"></i><b style="left:${(basis / max * 100).toFixed(2)}%"></b></span><span class="mpk-gl"><i class="mpk-key is-cost"></i>Cost <i class="mpk-key is-val"></i>Value</span></dd>`;
      }
      const ret = cell('Return on cost', `<dd class="hp-kpi-v ${dirClass(u.total)}"${roc === null ? '' : counter(roc, 'pct1s')}>${roc === null ? NA : priv(pct(roc, 1, true, true))}</dd>
        <dd class="hp-kpi-s">${u.total != null ? `${signedMoney(u.total, 0)} unrealized${noBasis.length ? ` · no cost basis for ${esc(noBasis.join(', '))}` : ''}` : 'No cost basis reported'}</dd>${bullet}`);

      // Realized: the broker's window, with its buckets as gain/loss bars when they load.
      const period = ACT_PERIOD[o.realized_pnl_period];
      const act = period && ins.act ? ins.act[period] : null;
      let bars = '';
      if (o.realized_pnl != null && act && act.s === 'ok' && act.data && act.data.available && (act.data.buckets || []).length) {
        const st = act.data.stats || {};
        bars = `<dd class="mpk-g"><span role="img" aria-label="${esc(`Realized by ${act.data.buckets.length > 20 ? 'day' : 'week'} over the window: ${st.positive || 0} with gains, ${st.negative || 0} with losses${st.unknown_buckets ? `, ${st.unknown_buckets} without a figure` : ''}.`)}">${Q() ? Q().miniBarsHtml(act.data.buckets.map((b) => (b.gain == null ? null : Number(b.gain)))) : ''}</span></dd>`;
      } else if (o.realized_pnl != null && act && (act.s === 'loading' || act.s === 'idle')) bars = '<dd class="mpk-g"><span class="mp-skel mpk-skel"></span></dd>';
      const realized = cell('Realized P&amp;L', `<dd class="hp-kpi-v ${dirClass(o.realized_pnl)}">${o.realized_pnl == null ? NA : signedMoney(o.realized_pnl, 2)}</dd>
        <dd class="hp-kpi-s">${o.realized_pnl == null ? 'Not reported by Robinhood' : `${esc(H.periodText(o.realized_pnl_period)).replace(/^./, (c) => c.toUpperCase())}, closed positions`}</dd>${bars}`);

      const bp = num(o.buying_power);
      const cash = cell('Cash', `<dd class="hp-kpi-v">${pct(m.cash_weight, 1)}</dd>
        <dd class="hp-kpi-s">${money(o.cash, 2)}${bp !== null ? ` · buying power ${money(bp, 0)}` : ''}</dd>
        <dd class="mpk-empty">No target cash range set</dd>`);

      const risk = o.risk || {};
      const drivers = (risk.drivers || []).map((d) => driverTag(d, pf));
      ins.ov.drivers = drivers;
      const riskCell = cell('Risk', `<dd class="mpk-risk"><span class="mpk-gauge">${X.riskGauge(risk, { size: 'sm' })}</span>
          <span class="mpk-risk-r"><b class="hp-kpi-v mpk-lvl mpk-lvl--${zoneOf(risk.level)}">${esc(risk.level || 'Not rated')}</b>${num(risk.points) !== null ? `<span class="hp-kpi-s">${risk.points} of ${RISK_MAX} points${risk.partial ? ', partial' : ''}</span>` : `<span class="hp-kpi-s">${esc(risk.note || 'Not assessed')}</span>`}</span></dd>
        ${risk.partial && num(risk.points) !== null ? `<dd class="hp-kpi-s mpk-risk-partial" data-risk-partial>Partial risk assessment: ${esc((risk.unmeasured || []).join(' and ') || 'some inputs')} not measured, so the true score may be higher.</dd>` : ''}
        ${drivers.length ? `<dd class="mpk-drivers" aria-label="${esc(`${drivers.length} ${plural(drivers.length, 'driver', 'drivers')}`)}">${drivers.map((d, i) => `<button type="button" class="mpk-tag" data-hp-x="driver:${i}"${d.sym ? ` data-sym="${esc(d.sym)}"` : ''}${d.sector ? ` data-sector="${esc(d.sector)}"` : ''} title="${esc(risk.drivers[i])}">${esc(d.label)}</button>`).join('')}</dd>` : ''}`,
      ` mpk-risk-c is-${zoneOf(risk.level)}`);
      return `<h2 class="q-head-title sr-only" id="hp-ov-h">Portfolio Overview</h2>
        <dl class="hp-kpis">${value}${ret}${realized}${cash}${riskCell}</dl>
        ${H.dataStatusHtml(pf)}`;
    }
    X.slotRenderers['kpi-overview'] = kpiInner;

    // ------------------------------------------------------------ session movers
    function moversInner(pf) {
      const ses = H.sessionOf(pf);
      const sparks = (ins.perf.data && ins.perf.data.sparks) || null;
      const rows = (pf.positions || []).map((r) => ({ r, d: H.dayPct(r.broker) })).filter((x) => x.d !== null).sort((a, b) => b.d - a.d);
      const head = `<header class="mpo-h"><h2 id="mpo-mov-h">Movers <span class="mpo-h-s">${esc(ses.label)}</span></h2></header>`;
      if (!rows.length) return `${head}<p class="mpo-empty">No previous close reported, so there are no session movers.</p>`;
      const n = Math.min(3, Math.floor(rows.length / 2) || 1);
      const best = rows.slice(0, n).filter((x) => x.d > 0);
      const worst = rows.slice(-n).reverse().filter((x) => x.d < 0);
      const chip = (x) => {
        const sp = sparks && sparks[x.r.symbol];
        return `<li><button type="button" class="mpo-mv" data-hp-inspect="${esc(x.r.symbol)}" data-sym="${esc(x.r.symbol)}" aria-label="${esc(`${x.r.symbol} ${pct(x.d, 2, false, true)} ${ses.live ? 'today' : 'last session'}. Details.`)}">
          <span class="mpo-mv-s">${esc(x.r.symbol)}<small>${esc(x.r.broker.name || '')}</small></span><span class="mpo-n ${dirClass(x.d)}">${priv(pct(x.d, 2, false, true))}</span>${sp && Q() ? `<span class="mpo-mv-g" title="Past month">${Q().sparkHtml(sp.values, '')}</span>` : '<span></span>'}</button></li>`;
      };
      const col = (title, xs) => `<div class="mpo-mv-col"><h3>${title}</h3>${xs.length ? `<ul>${xs.map(chip).join('')}</ul>` : `<p class="mpo-empty">None ${title === 'Best' ? 'up' : 'down'}.</p>`}</div>`;
      // The account against the indexes, only when their quotes are from the same session.
      const cmp = H.marketCompare(pf);
      let market = '';
      if (cmp.status === 'ok') {
        const items = [{ label: 'Your holdings', v: cmp.mine, mine: true }, ...cmp.idx.slice(0, 2).map((q) => ({ label: q.label || q.symbol, v: num(q.change_pct) }))];
        const max = Math.max(...items.map((x) => Math.abs(x.v)), 0.05);
        market = `<div class="mpo-mk"><h3>Against the market, same session</h3><ul>${items.map((x) => `<li class="${x.mine ? 'is-mine' : ''}"><span>${esc(x.label)}</span><span class="mpo-cbar" aria-hidden="true"><i class="${x.v < 0 ? 'is-down' : 'is-up'} mpo-anim" style="--s:${(Math.abs(x.v) / max / 2).toFixed(4)}"></i></span><span class="mpo-n ${dirClass(x.v)}">${x.mine ? priv(pct(x.v, 2, false, true)) : pct(x.v, 2, false, true)}</span></li>`).join('')}</ul></div>`;
      }
      return `${head}<div class="mpo-mv-cols">${col('Best', best)}${col('Worst', worst)}</div>${market}`;
    }
    X.slotRenderers['movers-overview'] = moversInner;

    // ------------------------------------------------------------ action queue
    const URGENCY = { 'Exit Review': ['High', 'high'], Trim: ['Medium', 'med'], Accumulate: ['Low', 'low'] };
    const VERB = { 'Exit Review': 'Review whether to keep', Trim: 'Trim', Accumulate: 'Consider adding to' };
    function queueInner(pf) {
      const rows = H.decisionOrder(pf);
      const pol = pf.policy || {};
      const body = rows.slice(0, 3).map((r) => {
        const d = H.decisionSummary(r);
        const [u, cls] = URGENCY[r.guidance.action] || ['Low', 'low'];
        const w = num(r.broker.weight);
        return `<li class="mpo-q" data-sym="${esc(r.symbol)}"><span class="mpo-u mpo-u--${cls}">${u}</span>
          <span class="mpo-q-t"><b>${esc(VERB[r.guidance.action] || r.guidance.action)} <button type="button" class="mpo-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}">${esc(r.symbol)}</button></b><span>${esc(d.reason)}</span></span>
          <span class="mpo-q-i">${H.weightBarHtml(w, d.after, pol)}<span>${pct(w, 1)}${d.after !== null ? ` → ${pct(d.after, 1)}` : ''}</span></span>
          <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-review="${esc(r.symbol)}">Review</button></li>`;
      }).join('');
      const a = (pf.freshness || {}).analysis || {};
      const flag = rows.length ? H.modelFlag(rows[0].guidance.evidence) : '';
      return `<header class="mpo-h"><h2 id="hp-pri-h">Action queue <span class="hp-count">${rows.length || ''}</span></h2>${flag}${rows.length ? `<button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-tab-go="guidance">See all ${rows.length}</button>` : ''}</header>
        ${rows.length ? `<ol class="mpo-ql" aria-labelledby="hp-pri-h">${body}</ol>`
    : `<p class="hp-empty hp-pri-empty">${a.state === 'running' ? 'Analysis in progress; calls appear as it completes.' : (pf.positions || []).some((r) => r.guidance.status === 'ok') ? 'No position changes indicated by the current calls.' : 'No calls: current research is unavailable. Refresh to retry.'}</p>`}
        <div class="mpo-cat-slot" data-mp-slot="events-line">${catalystsInner(pf)}</div>`;
    }

    // ------------------------------------------------------------ catalysts
    // What is scheduled for the names you hold (earnings, ex-dividend, filings) over
    // the next CATALYST_DAYS, nearest first, with each name's weight: the dates a
    // queued decision should be made before. Macro releases stay on the week line.
    const CATALYST_DAYS = 30;
    const CAT_KIND = { earnings: 'Earnings', dividend: 'Ex-dividend', filing: 'Filing' };
    function catalystsInner(pf) {
      const E = ins.events;
      const week = X.weekLine();
      const head = `<h3 class="mpo-cat-h">Catalysts <span>next ${CATALYST_DAYS} days</span></h3>`;
      if (!E || E.s === 'idle' || (E.s === 'loading' && !E.data)) return `${head}<p class="mpo-empty">Loading your holdings' calendar…</p>`;
      if (E.s !== 'ok' || !E.data || !E.data.available) return `${head}<p class="mpo-empty">The holdings calendar is unavailable right now.</p>${week}`;
      const held = new Set((pf.positions || []).map((r) => r.symbol));
      const weight = (sym) => num(((pf.positions || []).find((r) => r.symbol === sym) || { broker: {} }).broker.weight);
      const items = (E.data.upcoming || [])
        .filter((i) => CAT_KIND[i.kind] && Number.isFinite(i.days) && i.days >= 0 && i.days <= CATALYST_DAYS)
        .filter((i) => (i.symbols || []).some((x) => held.has(x)))
        .sort((x, y) => x.days - y.days || (weight((y.symbols || [])[0]) || 0) - (weight((x.symbols || [])[0]) || 0));
      if (!items.length) return `${head}<p class="mpo-empty">No earnings, ex-dividend dates or filings scheduled for your holdings.</p>${week}`;
      const when = (i) => (i.days === 0 ? 'Today' : i.days === 1 ? 'Tomorrow' : `In ${i.days} days`);
      const day = (i) => new Date(`${i.date}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const detail = (i) => (i.kind === 'earnings' ? (i.status === 'confirmed' ? i.when_text || 'confirmed' : 'estimated date') : i.kind === 'dividend' ? 'confirmed' : 'filed');
      const shown = items.slice(0, 4);
      const li = (i) => {
        const sym = (i.symbols || [])[0] || '';
        const w = weight(sym);
        return `<li class="mpo-cat" data-sym="${esc(sym)}">
          <span class="mpo-cat-d"><b>${esc(day(i))}</b><span>${esc(when(i))}</span></span>
          <span class="mpo-cat-t"><button type="button" class="mpo-sym" data-hp-inspect="${esc(sym)}" aria-label="${esc(`${sym} details`)}">${esc(sym)}</button><span>${esc(CAT_KIND[i.kind])} · ${esc(detail(i))}</span></span>
          <span class="mpo-cat-w mpo-n">${w === null ? '' : pct(w, 1)}</span></li>`;
      };
      const more = items.length - shown.length;
      return `${head}<ul class="mpo-catl">${shown.map(li).join('')}</ul>${more > 0 ? `<button type="button" class="q-btn q-btn--link hp-inline-link mpo-cat-more" data-hp-tab-go="news">${more} more in News &amp; Events</button>` : ''}${week}`;
    }
    X.slotRenderers['events-line'] = catalystsInner;

    // ------------------------------------------------------------ allocation
    // Categorical colour follows the entity, not its rank: a sector keeps its slot as
    // weights move. Unmapped names take the first free slot; past six, "Other".
    const SLOT_MAP = {
      sector: { Technology: 1, 'Consumer Cyclical': 2, Healthcare: 3, 'Consumer Defensive': 4, 'Communication Services': 5, Industrials: 6, 'Financial Services': 7 },
      asset_class: { 'Individual stocks': 1, 'Stock funds': 3, 'Bond funds': 7, 'Commodity funds': 4, 'Crypto-linked funds': 5 },
      geography: { 'United States': 1 },
    };
    function slices(v, kind, cashWeight) {
      const map = SLOT_MAP[kind] || {};
      const items = v.items.filter((b) => (b.weight || 0) > 0);
      const named = items.filter((b) => b.classified !== false);
      const loose = items.filter((b) => b.classified === false);
      const used = new Set();
      const out = [];
      const other = { key: 'other', label: 'Other', value: 0, symbols: [], slot: 'other' };
      // Six named slices at most (by weight); mapped names claim their own slot first,
      // so an unmapped name can never push a mapped one off its colour.
      const shown = named.slice(0, 6);
      named.slice(6).forEach((b) => { other.value += b.weight; other.symbols.push(...b.symbols); });
      const slotOf = {};
      shown.forEach((b) => { if (map[b.name]) { slotOf[b.name] = map[b.name]; used.add(map[b.name]); } });
      const present = new Set(Object.values(slotOf));
      shown.forEach((b) => {
        if (slotOf[b.name]) return;
        const free = [1, 2, 3, 4, 5, 6, 7].filter((k) => !used.has(k));
        const slot = free.find((k) => !Object.values(map).includes(k)) || free.find((k) => !present.has(k));
        slotOf[b.name] = slot;
        used.add(slot);
      });
      shown.forEach((b) => out.push({ key: b.name, label: b.name, value: b.weight, symbols: b.symbols, slot: slotOf[b.name], over: b.over, limit: b.limit }));
      // The leftover slice is named for what it holds: overflow, unclassified (funds), or both.
      const overflow = other.value > 0;
      loose.forEach((b) => { other.value += b.weight; other.symbols.push(...b.symbols); });
      if (loose.length) other.label = overflow ? 'Other and not classified' : loose.length === 1 ? loose[0].name : 'Not classified';
      if (other.value > 0) out.push(other);
      if (cashWeight > 0) out.push({ key: 'cash', label: 'Cash', value: cashWeight, symbols: [], slot: 'cash' });
      return out;
    }

    const posOf = (pf, sym) => (pf.positions || []).find((r) => r.symbol === sym);
    const callText = (g) => (g.status !== 'ok' ? 'No call' : H.actionLabel(g.action, g));
    const callChip = (g) => `<span class="mpo-call mpo-call--${esc({ Trim: 'trim', 'Exit Review': 'exit', Accumulate: 'add', Hold: 'hold', 'Hold / No New Money': 'noadd' }[g.action] || 'none')}">${esc(callText(g))}</span>`;

    function allocData(pf) {
      const E = ins.expo;
      return (E.s === 'missing' || (E.s === 'error' && !E.data) || !(E.data && E.data.available)) ? X.fallbackExposure(pf) : E.data;
    }
    const ALLOC_VIEWS = [['sector', 'Sector'], ['asset_class', 'Asset'], ['geography', 'Region'], ['treemap', 'Treemap']];

    // The pinned group: every holding in it, largest first, with a way into Holdings.
    function pinPanel(pf, g) {
      const rows = g.symbols.map((s) => posOf(pf, s)).filter(Boolean).sort(H.byWeight);
      return `<div class="mpo-pin" role="region" aria-label="${esc(`${g.label}: ${rows.length} ${plural(rows.length, 'holding', 'holdings')}`)}">
        <header><b>${esc(g.label)}</b> <span>${pct(g.value, 1)} · ${rows.length} ${plural(rows.length, 'holding', 'holdings')}</span>
          <button type="button" class="icon-btn hp-icon-btn mpo-pin-x" data-hp-x="alloc-pin:" aria-label="Close ${esc(g.label)}">×</button></header>
        <table class="mpo-pin-t"><thead><tr><th scope="col">Symbol</th><th scope="col" class="num">Weight</th><th scope="col" class="num">Value</th><th scope="col" class="num">On cost</th><th scope="col">Call</th></tr></thead>
          <tbody>${rows.map((r) => `<tr data-sym="${esc(r.symbol)}"><th scope="row"><button type="button" class="mpo-sym" data-hp-inspect="${esc(r.symbol)}">${esc(r.symbol)}</button></th><td class="num">${pct(r.broker.weight, 1)}</td><td class="num">${money(r.broker.market_value)}</td><td class="num ${dirClass(r.broker.unrealized_pnl_pct)}">${r.broker.unrealized_pnl_pct == null ? NA : priv(pct(r.broker.unrealized_pnl_pct, 1, false, true))}</td><td>${callChip(r.guidance)}</td></tr>`).join('')}</tbody></table>
        <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-x="alloc:${esc(g.key)}">Filter Holdings to ${esc(g.label)}</button>
      </div>`;
    }

    function treemapHtml(pf, data) {
      const Ch = Q();
      const rows = (pf.positions || []).filter((r) => (num(r.broker.weight) || 0) > 0);
      const cashW = num(data.cash_weight) || 0;
      const items = [...rows.map((r) => ({ key: r.symbol, value: r.broker.weight })), ...(cashW > 0 ? [{ key: '__cash', value: cashW }] : [])];
      const Wd = 160; const Hd = 100;
      const tiles = Ch ? Ch.squarify(items, Wd, Hd) : [];
      const max = 2;                                   // a ±2% session move saturates the scale
      const body = tiles.map((t) => {
        const box = `left:${(t.x / Wd * 100).toFixed(3)}%;top:${(t.y / Hd * 100).toFixed(3)}%;width:${(t.w / Wd * 100).toFixed(3)}%;height:${(t.h / Hd * 100).toFixed(3)}%`;
        const area = (t.w / Wd) * (t.h / Hd);
        if (t.key === '__cash') return `<span class="mpt-tile is-cash" style="${box}"><span class="mpt-s">Cash</span>${area > 0.012 ? `<span class="mpt-w">${pct(cashW, 1)}</span>` : ''}</span>`;
        const r = posOf(pf, t.key);
        const d = H.dayPct(r.broker);
        const k = d === null ? 0 : Math.max(-1, Math.min(1, d / max));
        const mix = Math.round(Math.abs(k) * 48 + (d === null || d === 0 ? 0 : 12));   // 12-60%: tile text keeps AA contrast
        const size = area > 0.05 ? ' is-lg' : area > 0.016 ? ' is-md' : ' is-sm';
        return `<button type="button" class="mpt-tile ${d === null ? 'is-na' : d > 0 ? 'is-up' : d < 0 ? 'is-down' : 'is-flat'}${size}" style="${box};--mix:${mix}%" data-hp-inspect="${esc(r.symbol)}" data-sym="${esc(r.symbol)}" aria-label="${esc(`${r.symbol}: ${pct(r.broker.weight, 1)} of the account, ${d === null ? 'no session change' : `${pct(d, 2, false, true)} ${H.sessionOf(pf).live ? 'today' : 'last session'}`}. Details.`)}">
          <span class="mpt-s">${esc(r.symbol)}</span><span class="mpt-w">${pct(r.broker.weight, 1)}</span>${d !== null ? `<span class="mpt-d">${priv(pct(d, 2, false, true))}</span>` : ''}</button>`;
      }).join('');
      return `<div class="mpt" role="group" aria-label="Treemap of holdings: area is weight, colour is the session change">${body}</div>
        <p class="mpt-key" aria-hidden="true"><span>−${max}%</span><i></i><span>0</span><i class="is-up"></i><span>+${max}%</span><span class="mpt-key-l">Area: weight · Colour: ${H.sessionOf(pf).live ? 'today' : 'last session'}</span></p>`;
    }

    function allocInner(pf) {
      const E = ins.expo;
      const head = (seg = '') => `<header class="mpo-h"><h2 id="mpo-alloc-h">Allocation</h2>${seg}</header>`;
      if (E.s === 'idle' || (E.s === 'loading' && !E.data)) return `${head()}<div class="mpo-alloc"><span class="mp-skel mp-skel--ring" role="status"><span class="sr-only">Loading allocation…</span></span><span class="mp-skel mp-skel--legend"></span></div>`;
      const data = allocData(pf);
      const views = ALLOC_VIEWS.filter(([k]) => k === 'treemap' || data.views[k]);
      const kind = views.some(([k]) => k === ins.prefs.alloc) ? ins.prefs.alloc : 'sector';
      const seg = views.length > 1 ? `<div class="mpo-seg" role="group" aria-label="Allocation by">${views.map(([k, l]) => `<button type="button" class="${k === kind ? 'is-on' : ''}" data-hp-x="alloc-view:${k}" aria-pressed="${k === kind}">${l}</button>`).join('')}</div>` : '';
      if (kind === 'treemap') { ins.allocSegs = []; return `${head(seg)}${treemapHtml(pf, data)}`; }
      const v = data.views[kind];
      const segs = slices(v, kind, data.cash_weight || 0);
      ins.allocSegs = segs;
      const lead = segs.filter((x) => x.key !== 'cash').sort((a, b) => b.value - a.value)[0];
      const Ch = Q();
      const pin = segs.find((x) => x.key === ins.ov.pin && x.symbols.length);
      const legend = segs.map((x) => {
        const badge = x.over ? `<span class="mpd-badge" title="${esc(`Over the ${pct(x.limit, 0)} QUANTA default`)}">Over ${pct(x.limit, 0)}</span>` : '';
        const inner = `<i class="mpd-sw mpd-s-${esc(x.slot)}" aria-hidden="true"></i><span class="mpd-l">${esc(x.label)}</span>${badge}<span class="mpd-v${x.over ? ' is-over' : ''}">${pct(x.value, 1)}</span>`;
        return x.symbols.length
          ? `<li><button type="button" class="mpd-row${pin === x ? ' is-pinned' : ''}" data-hp-x="alloc-pin:${esc(x.key)}" data-seg-key="${esc(x.key)}" aria-pressed="${pin === x}" aria-label="${esc(`${x.label}: ${pct(x.value, 1)} of the account${x.over ? `, over the ${pct(x.limit, 0)} QUANTA default` : ''}. Show ${x.symbols.length} ${plural(x.symbols.length, 'holding', 'holdings')}.`)}">${inner}</button></li>`
          : `<li><span class="mpd-row" data-seg-key="${esc(x.key)}">${inner}</span></li>`;
      }).join('');
      const funds = ((data.lookthrough || {}).funds || []);
      const fundSeg = segs.find((x) => /fund/i.test(x.label));
      const notes = [
        lead && lead.over ? `${esc(lead.label)} is over the ${pct(lead.limit, 0)} QUANTA sector default.` : '',
        fundSeg && funds.length ? `Funds: ${esc(funds.join(', '))}; not looked through.` : data.fallback ? 'Asset class and region load after a QUANTA restart.' : '',
      ].filter(Boolean).join(' ');
      return `${head(seg)}
        <div class="mpo-alloc${pin ? ' has-pin' : ''}">${Ch ? Ch.donutHtml(segs, { centerValue: lead ? pct(lead.value, 0) : '', centerLabel: lead ? lead.label : '' }) : ''}
          <ul class="mpd-legend" aria-label="${esc(`Allocation by ${v.title.toLowerCase()}, share of the whole account`)}">${legend}</ul>
          <div class="mpd-tip" role="tooltip" hidden></div>
          ${pin ? pinPanel(pf, pin) : ''}</div>
        ${notes ? `<p class="mpo-foot">${notes}</p>` : ''}`;
    }
    X.slotRenderers['alloc-overview'] = allocInner;

    // ------------------------------------------------------------ holdings + concentration
    const OV_SORT = {
      symbol: { dir: 'asc', key: (r) => r.symbol },
      weight: { dir: 'desc', key: (r) => num(r.broker.weight) },
      session: { dir: 'desc', key: (r) => H.dayPct(r.broker) },
      ret: { dir: 'desc', key: (r) => num(r.broker.unrealized_pnl_pct) },
      trend: { dir: 'desc', key: (r) => { const s = ins.perf.data && ins.perf.data.sparks && ins.perf.data.sparks[r.symbol]; return s ? s.change : null; } },
      call: { dir: 'asc', key: (r) => r.guidance.priority },
    };
    function sortOv(rows) {
      const s = OV_SORT[ins.ov.sort] || OV_SORT.weight;
      const d = (ins.ov.dir || s.dir) === 'asc' ? 1 : -1;
      return rows.slice().sort((a, b) => {
        const ka = s.key(a); const kb = s.key(b);
        if (ka === null && kb === null) return H.byWeight(a, b);
        if (ka === null) return 1;
        if (kb === null) return -1;
        if (ka !== kb) return (typeof ka === 'string' ? ka.localeCompare(kb) : ka - kb) * d;
        return H.byWeight(a, b);
      });
    }

    // Top five holdings and the rest, as one bar of the whole account, with the single-name
    // limit and the review level marked from the left: the lead segment reads against them.
    function concBar(pf) {
      const pol = pf.policy || {};
      const rows = (pf.positions || []).filter((r) => num(r.broker.weight) !== null).sort(H.byWeight);
      if (!rows.length) return '';
      const top = rows.slice(0, 5);
      const restW = rows.slice(5).reduce((t, r) => t + r.broker.weight, 0);
      const cashW = num((pf.metrics || {}).cash_weight) || 0;
      const cap = num(pol.max_single_position);
      const review = num(pol.trim_review_position);
      const top5 = top.reduce((t, r) => t + r.broker.weight, 0);
      const seg = (w, label, cls, sym) => `<i class="mpo-cb-s ${cls}" style="flex-basis:${(w * 100).toFixed(3)}%"${sym ? ` data-sym="${esc(sym)}"` : ''} title="${esc(`${label} ${pct(w, 1)}`)}">${w >= 0.07 ? `<span>${esc(label)} ${pct(w, 0)}</span>` : ''}</i>`;
      return `<div class="mpo-cb" id="mpo-conc" role="img" aria-label="${esc(`Top five holdings ${pct(top5, 1)} of the account: ${top.map((r) => `${r.symbol} ${pct(r.broker.weight, 1)}`).join(', ')}; other holdings ${pct(restW, 1)}; cash ${pct(cashW, 1)}. Single-name limit ${pct(cap, 0)}, review level ${pct(review, 0)}.`)}">
        <p class="mpo-cb-h"><span>Top five <b>${pct(top5, 1)}</b></span></p>
        <div class="mpo-cb-bar">${top.map((r, i) => seg(r.broker.weight, r.symbol, `is-${i}${cap !== null && r.broker.weight >= cap ? ' is-over' : ''}`, r.symbol)).join('')}${restW > 0 ? seg(restW, 'Other', 'is-rest') : ''}${cashW > 0 ? seg(cashW, 'Cash', 'is-cash') : ''}
          ${cap !== null ? `<em style="left:${(cap * 100).toFixed(2)}%"><span>${pct(cap, 0)} limit</span></em>` : ''}${review !== null ? `<em class="is-review" style="left:${(review * 100).toFixed(2)}%"><span>${pct(review, 0)} review</span></em>` : ''}</div>
      </div>`;
    }

    function holdInner(pf) {
      const rows = sortOv(pf.positions || []);
      const sparks = (ins.perf.data && ins.perf.data.sparks) || null;
      const loadingSparks = ins.perf.s === 'idle' || ins.perf.s === 'loading';
      // Why a trend is missing: the price history could not be read at all, or this name has
      // fewer than a month of daily prices.
      const sparkWhy = ins.perf.s === 'error' || (ins.perf.data && ins.perf.data.available === false) ? 'Price history could not be loaded' : 'Less than a month of price history';
      const maxW = Math.max(...rows.map((r) => num(r.broker.weight) || 0), 0.01);
      const rets = rows.map((r) => num(r.broker.unrealized_pnl_pct)).filter((x) => x !== null);
      const maxR = Math.max(...rets.map(Math.abs), 1);
      const cap = num((pf.policy || {}).max_single_position);
      const Ch = Q();
      const ses = H.sessionOf(pf);
      const body = rows.map((r) => {
        const b = r.broker;
        const w = num(b.weight);
        const day = H.dayPct(b);
        const ret = num(b.unrealized_pnl_pct);
        const sp = sparks && sparks[r.symbol];
        return `<tr data-mpo-row="${esc(r.symbol)}" data-sym="${esc(r.symbol)}">
          <th scope="row"><button type="button" class="mpo-sym" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol} details`)}">${esc(r.symbol)}</button><span class="mpo-name" title="${esc(b.name || '')}">${esc(b.name || '')}</span></th>
          <td><span class="mpo-bar"><i class="mpo-anim${cap !== null && w >= cap ? ' is-over' : ''}" style="--s:${((w || 0) / maxW).toFixed(4)}"></i></span><span class="mpo-n">${pct(w, 1)}</span></td>
          <td class="mpo-n ${dirClass(day)}">${day === null ? NA : priv(pct(day, 2, false, true))}</td>
          <td><span class="mpo-div" aria-hidden="true">${ret === null ? '' : `<i class="${ret < 0 ? 'is-down' : 'is-up'} mpo-anim" style="--s:${(Math.abs(ret) / maxR / 2).toFixed(4)}"></i>`}</span><span class="mpo-n ${dirClass(ret)}">${ret === null ? NA : priv(pct(ret, 1, false, true))}</span></td>
          <td>${sp && Ch ? `${Ch.sparkHtml(sp.values, sp.change >= 0 ? 'bull' : 'bear')}<span class="mpo-n ${dirClass(sp.change)}">${pct(sp.change, 1, true, true)}</span>` : loadingSparks ? '<span class="mp-skel mpo-sk"></span>' : `<span class="mpo-n" title="${esc(sparkWhy)}">${NA}</span><span class="sr-only">${esc(sparkWhy)}</span>`}</td>
          <td>${callChip(r.guidance)}</td>
        </tr>`;
      }).join('');
      const s = ins.ov.sort;
      const dir = ins.ov.dir || (OV_SORT[s] || {}).dir;
      const th = (k, label) => `<th scope="col" aria-sort="${s === k ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" class="mpo-sort${s === k ? ' is-on' : ''}" data-hp-x="ovsort:${k}">${label}<span aria-hidden="true">${s === k ? (dir === 'asc' ? '▲' : '▼') : ''}</span></button></th>`;
      return `<header class="mpo-h"><h2 id="mpo-hold-h">Holdings <span class="hp-count">${rows.length}</span></h2><button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-tab-go="holdings">Full table</button></header>
        ${concBar(pf)}
        <div class="q-tablewrap mpo-tw" tabindex="0" role="region" aria-labelledby="mpo-hold-h"><table class="mpo-t">
          <caption class="sr-only">Holdings: weight, ${esc(ses.live ? 'today' : 'last session')}, return on cost, the past month's price change, and QUANTA's call. Column headers sort.</caption>
          <colgroup><col class="c-sym"><col class="c-w"><col class="c-d"><col class="c-r"><col class="c-t"><col class="c-c"></colgroup>
          <thead><tr>${th('symbol', 'Symbol')}${th('weight', 'Weight')}${th('session', ses.live ? 'Today' : 'Session')}${th('ret', 'Return on cost')}${th('trend', '30 days')}${th('call', 'Call')}</tr></thead>
          <tbody>${body}</tbody></table></div>`;
    }
    X.slotRenderers['hold-overview'] = holdInner;

    // Unrealized gains by holding: each holding's unrealized gain on cost, largest first, and the total.
    // (Not a return attribution: it leaves out sold positions, dividends and the period.)
    function contribInner(pf) {
      const u = (pf.overview || {}).unrealized_pnl || {};
      const rows = (pf.positions || []).filter((r) => num(r.broker.unrealized_pnl) !== null).map((r) => ({ symbol: r.symbol, v: r.broker.unrealized_pnl }))
        .sort((a, b) => b.v - a.v);
      const missing = (pf.positions || []).filter((r) => num(r.broker.unrealized_pnl) === null).map((r) => r.symbol);
      const head = `<header class="mpo-h"><h2 id="mpo-con-h">Unrealized gains by holding</h2><span class="mpo-h-s">Gain on cost, positions still held</span></header>`;
      if (!rows.length) return `${head}<p class="mpo-empty">No cost basis reported, so there is nothing to attribute.</p>`;
      const LIMIT = 18;
      let shown = rows;
      let rest = null;
      if (rows.length > LIMIT) {
        // Keep the largest gains and the largest losses; net the middle into one row.
        const lose = rows.filter((x) => x.v < 0).slice(-3);
        const win = rows.filter((x) => !lose.includes(x)).slice(0, LIMIT - lose.length);
        const mid = rows.filter((x) => !win.includes(x) && !lose.includes(x));
        shown = [...win, ...lose];
        rest = { n: mid.length, v: mid.reduce((t, x) => t + x.v, 0) };
      }
      const total = num(u.total) !== null ? u.total : rows.reduce((t, x) => t + x.v, 0);
      const max = Math.max(...rows.map((x) => Math.abs(x.v)), Math.abs(total), 1e-9);
      const bar = (v, cls = '') => `<span class="mpo-cbar${cls}" aria-hidden="true"><i class="${v < 0 ? 'is-down' : 'is-up'} mpo-anim" style="--s:${(Math.abs(v) / max / 2).toFixed(4)}"></i></span>`;
      const li = (x) => `<li><button type="button" class="mpo-cr" data-hp-inspect="${esc(x.symbol)}" data-sym="${esc(x.symbol)}" aria-label="${esc(`${x.symbol}: ${state.hidden ? 'amount hidden' : signedMoney(x.v).replace(/<[^>]+>/g, '')} unrealized. Details.`)}"><span class="mpo-cr-s">${esc(x.symbol)}</span>${bar(x.v)}<span class="mpo-n ${dirClass(x.v)}">${signedMoney(x.v)}</span></button></li>`;
      return `${head}
        <ol class="mpo-crl" aria-labelledby="mpo-con-h">${shown.map(li).join('')}
          ${rest ? `<li><span class="mpo-cr is-rest"><span class="mpo-cr-s">${rest.n} more</span>${bar(rest.v)}<span class="mpo-n ${dirClass(rest.v)}">${signedMoney(rest.v)}</span></span></li>` : ''}
          <li class="mpo-cr-total"><span class="mpo-cr"><span class="mpo-cr-s">Total</span>${bar(total, ' is-total')}<span class="mpo-n ${dirClass(total)}">${signedMoney(total)}</span></span></li></ol>
        ${missing.length ? `<p class="mpo-foot">No cost basis: ${esc(missing.join(', '))}.</p>` : ''}`;
    }
    X.slotRenderers['contrib-overview'] = contribInner;

    // ------------------------------------------------------------ composition
    function overview(pf) {
      const card = (cls, slot, label, inner) => `<section class="q-panel hp-panel mpo-card ${cls}"${slot ? ` data-mp-slot="${slot}"` : ''} aria-labelledby="${label}">${inner}</section>`;
      return `<div class="mpo" data-mpo>
        <section class="q-panel hp-panel hp-overview mpo-kpi" data-mp-slot="kpi-overview" aria-labelledby="hp-ov-h">${kpiInner(pf)}</section>
        <div class="mpo-grid mpo-grid--a">
          <section class="q-panel hp-panel mpo-card mpo-perf" aria-labelledby="mp-perf-overview-h"><div class="mp-perf" data-mp-slot="perf-overview">${X.slotRenderers['perf-overview'](pf)}</div></section>
          ${card('mpo-alloc-c', 'alloc-overview', 'mpo-alloc-h', allocInner(pf))}
        </div>
        <div class="mpo-grid mpo-grid--h">
          ${card('mpo-hold', 'hold-overview', 'mpo-hold-h', holdInner(pf))}
          ${card('mpo-contrib', 'contrib-overview', 'mpo-con-h', contribInner(pf))}
        </div>
        <div class="mpo-grid mpo-grid--q">
          ${card('mpo-queue', 'queue-overview', 'hp-pri-h', queueInner(pf))}
          ${card('mpo-movers', 'movers-overview', 'mpo-mov-h', moversInner(pf))}
        </div>
      </div>`;
    }
    X.slotRenderers['queue-overview'] = queueInner;

    // ------------------------------------------------------------ motion + linking
    function onMount(scope) {
      if (typeof document === 'undefined' || !scope || !scope.querySelectorAll) return;
      const grow = [...scope.querySelectorAll('.mpo-anim:not(.is-in)')];
      if (grow.length) {
        if (reduced() || typeof requestAnimationFrame !== 'function') grow.forEach((el) => el.classList.add('is-in'));
        else requestAnimationFrame(() => requestAnimationFrame(() => grow.forEach((el) => el.classList.add('is-in'))));
      }
      const nums = [...scope.querySelectorAll('[data-count]')];
      if (!nums.length || ins.ov.counted) return;
      ins.ov.counted = true;
      if (reduced() || typeof requestAnimationFrame !== 'function') return;
      nums.forEach((el) => {
        const to = Number(el.dataset.count);
        const fmt = COUNT_FMT[el.dataset.countFmt];
        if (!fmt || !Number.isFinite(to)) return;
        const final = el.innerHTML;
        const t0 = root.performance ? root.performance.now() : Date.now();
        const step = (t) => {
          const k = Math.min(1, (t - t0) / 300);
          if (k >= 1) { el.innerHTML = final; return; }
          el.textContent = fmt(to * (1 - (1 - k) ** 3)).replace(/<[^>]+>/g, '');
          requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
    }

    // One highlight at a time: a holding (and the slice it sits in), or a group (and its holdings).
    function highlight(sc, target) {
      sc.querySelectorAll('.is-hl').forEach((el) => el.classList.remove('is-hl'));
      sc.querySelectorAll('.is-focus').forEach((el) => el.classList.remove('is-focus'));
      const tip = sc.querySelector('.mpd-tip');
      const center = sc.querySelector('.mpd-c');
      const segs = ins.allocSegs || [];
      const lead = segs.filter((x) => x.key !== 'cash').sort((a, b) => b.value - a.value)[0];
      const setCenter = (g) => { if (!center) return; const x = g || lead; center.querySelector('b').textContent = x ? pct(x.value, 0).replace(/<[^>]+>/g, '') : ''; center.querySelector('span').textContent = x ? x.label : ''; };
      if (!target) { setCenter(null); if (tip) tip.hidden = true; return; }
      const pf = state.view && state.view.portfolio;
      let group = null;
      let syms = [];
      if (target.sym) {
        syms = [target.sym];
        group = segs.find((g) => g.symbols.includes(target.sym)) || null;
      } else if (target.group) {
        group = segs.find((g) => g.key === target.group) || null;
        syms = group ? group.symbols : [];
      } else if (target.sector) {
        group = segs.find((g) => g.key === target.sector) || null;
        syms = group ? group.symbols : [];
      }
      syms.forEach((s) => sc.querySelectorAll(`[data-sym="${CSS.escape(s)}"]`).forEach((el) => el.classList.add('is-hl')));
      if (group) {
        const donut = sc.querySelector('.mpd');
        if (donut) donut.classList.add('is-focus');
        sc.querySelectorAll(`[data-seg="${CSS.escape(group.key)}"], [data-seg-key="${CSS.escape(group.key)}"]`).forEach((el) => el.classList.add('is-hl'));
        setCenter(group);
      }
      // The group's holdings, top five by weight, beside the donut.
      if (tip && group && target.fromDonut && pf) {
        const rows = group.symbols.map((s) => posOf(pf, s)).filter(Boolean).sort(H.byWeight);
        tip.innerHTML = `<b>${esc(group.label)} ${pct(group.value, 1)}</b>${rows.length ? `<table><tbody>${rows.slice(0, 5).map((r) => { const d = H.dayPct(r.broker); return `<tr><th scope="row">${esc(r.symbol)}</th><td>${pct(r.broker.weight, 1)}</td><td class="${dirClass(d)}">${d === null ? '--' : priv(pct(d, 2, false, true))}</td></tr>`; }).join('')}</tbody></table>${rows.length > 5 ? `<span>and ${rows.length - 5} more</span>` : ''}` : '<span>No holdings: cash</span>'}`;
        tip.hidden = false;
      } else if (tip) tip.hidden = true;
    }
    const targetOf = (el) => {
      if (!el || !el.closest) return null;
      const seg = el.closest('[data-seg], [data-seg-key]');
      if (seg) return { group: seg.dataset.seg || seg.dataset.segKey, fromDonut: true };
      const s = el.closest('[data-sym]');
      if (s) return { sym: s.dataset.sym };
      const sec = el.closest('[data-sector]');
      if (sec) return { sector: sec.dataset.sector };
      return null;
    };

    function bindOnce() {
      if (typeof document === 'undefined' || bindOnce.done) return;
      const view = document.getElementById('view-holdings');
      if (!view) return;
      bindOnce.done = true;
      const scopeOf = (e) => e.target.closest && e.target.closest('[data-mpo]');
      let last = null;
      const move = (e) => {
        const sc = scopeOf(e);
        if (!sc) return;
        const t = targetOf(e.target);
        const k = t ? JSON.stringify(t) : '';
        if (k === last) return;
        last = k;
        highlight(sc, t);
      };
      view.addEventListener('pointerover', move);
      view.addEventListener('focusin', move);
      view.addEventListener('pointerout', (e) => {
        const to = e.relatedTarget;
        if (!to || !to.closest || !to.closest('[data-mpo]')) { last = null; const sc = view.querySelector('[data-mpo]'); if (sc) highlight(sc, null); }
      });
      view.addEventListener('focusout', (e) => { if (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest('[data-mpo]')) { last = null; const sc = view.querySelector('[data-mpo]'); if (sc) highlight(sc, null); } });
      view.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && ins.ov.pin) { ins.ov.pin = null; X.paintSlot('alloc-overview'); H.announce('Allocation group closed.'); }
      });
    }

    Object.assign(X.actions, {
      'alloc-view'(k) {
        if (!ALLOC_VIEWS.some(([x]) => x === k)) return;
        ins.prefs.alloc = k; ins.ov.pin = null; X.savePrefs();
        X.paintSlot('alloc-overview');
        H.announce(k === 'treemap' ? 'Allocation as a treemap of holdings.' : `Allocation by ${k === 'asset_class' ? 'asset class' : k === 'geography' ? 'region' : 'sector'}.`);
      },
      alloc(key) {
        const x = (ins.allocSegs || []).find((g) => g.key === key);
        if (x && x.symbols.length) H.filterHoldings(x.label, x.symbols);
      },
      'alloc-pin'(key, t) {
        ins.ov.pin = !key || ins.ov.pin === key ? null : key;
        X.paintSlot('alloc-overview');
        const g = (ins.allocSegs || []).find((x) => x.key === ins.ov.pin);
        H.announce(g ? `${g.label}: ${g.symbols.length} ${plural(g.symbols.length, 'holding', 'holdings')} listed. Escape closes.` : 'Allocation group closed.');
        const back = document.querySelector(`[data-hp-x="alloc-pin:${CSS.escape(key || '')}"]`);
        if (back && t) back.focus({ preventScroll: true });
      },
      ovsort(k) {
        if (!OV_SORT[k]) return;
        if (ins.ov.sort === k) ins.ov.dir = (ins.ov.dir || OV_SORT[k].dir) === 'asc' ? 'desc' : 'asc';
        else { ins.ov.sort = k; ins.ov.dir = null; }
        X.paintSlot('hold-overview');
      },
      driver(i) {
        const d = (ins.ov.drivers || [])[Number(i)];
        if (!d || typeof document === 'undefined') return;
        const flash = (el) => { if (!el) return; el.scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' }); el.classList.remove('is-flash'); void el.offsetWidth; el.classList.add('is-flash'); };
        if (d.go === 'conc') flash(document.getElementById('mpo-conc'));
        else if (d.go === 'sector') {
          ins.prefs.alloc = 'sector'; ins.ov.pin = d.sector;
          X.paintSlot('alloc-overview');
          flash(document.querySelector('.mpo-alloc-c'));
        } else {
          H.setTab('review', { focus: true });
          setTimeout(() => flash(document.querySelector('#hp-panel-review .hp-exposure')), 0);
        }
      },
    });

    // Session and realized data the KPI strip needs, loaded with the Overview.
    const baseAfter = X.afterRender;
    X.afterRender = (pf) => {
      baseAfter(pf);
      if (!pf) return;
      const period = ACT_PERIOD[(pf.overview || {}).realized_pnl_period];
      if (period && X.loadActivity && (pf.overview || {}).realized_pnl != null) {
        const cur = ins.act && ins.act[period];
        if (!cur) Promise.resolve(X.loadActivity(period)).then(() => X.paintSlot('kpi-overview'));
      }
    };
    const baseReset = X.reset;
    X.reset = (keep) => { baseReset(keep); ins.ov.counted = false; ins.ov.pin = null; };

    Object.assign(X, { overview, onMount, slices, kpiInner, moversInner, allocInner, holdInner, contribInner, queueInner, concBar, driverTag });
    bindOnce();
    return X;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaOverview = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
