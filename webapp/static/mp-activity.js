/* Activity: closing trades, purchases and open lots, one view at a time.
 *
 * Top to bottom: one sticky control row (range, stocks, view, active filters), five
 * KPI tiles, the period chart beside the ranking by stock, the table, and one footer
 * for coverage and definitions. The view switch drives all of them together.
 *
 * Figures come from static/mp-activity-model.js: every section sums the same filtered
 * rows, so the KPI total, the chart buckets, the ranking and the table always agree.
 * A difference from Robinhood's own window total is shown as a named line.
 *
 * Limits, labels and windows come from the server's Activity config (records.config);
 * the range chip, stocks and view live in the URL (#holdings/activity/...).
 */
(function (root) {
  'use strict';

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const M = root.QuantaActivityModel || (typeof require === 'function' ? require('./mp-activity-model.js') : null);
    if (!M) return null;
    const H = host._h;
    const { esc, NA, priv, dirClass, plural } = H;
    const ins = X.ins;
    const state = host._state;
    ins.act = {};
    // UI state. range/view/symbols mirror the URL; the rest is per visit.
    // today: null is the real New York date; previews and tests may pin one.
    const A = (ins.actUi = { ready: false, range: null, view: null, asset: 'all', symbols: [], period: null, age: null, gran: null, cum: true,
      shown: null, rankAll: false, q: '', base: null, brush: null, sort: null, today: null });

    // Robinhood's realized P&L for one span (Overview reads this too).
    async function load(period, force) {
      const cur = ins.act[period];
      if (cur && (cur.s === 'loading' || (cur.s === 'ok' && !force))) return;
      ins.act[period] = { s: 'loading', data: cur && cur.data };
      X.paintSlot('activity-body');
      const res = await H.api('GET', `/activity?period=${encodeURIComponent(period)}`);
      if (res.status === 404) {
        ins.act[period] = { s: 'missing' };
        if (!ins.stale) { ins.stale = true; X.paintSlot('banner'); }
      } else {
        ins.act[period] = res.ok ? { s: 'ok', data: res.data } : { s: 'error', error: 'Realized P&L could not be read' };
      }
      X.paintSlot('activity-body');
    }

    // ------------------------------------------------------------ state
    // Reloads retain usable rows; reset/account changes clear them in mp-insights.
    const recs = () => { const R = ins.records || {}; return ['ok', 'loading', 'error'].includes(R.s) && R.data && R.data.available ? R.data : null; };
    const cfgOf = () => { const d = recs(); return d && d.config ? d.config : null; };
    function ready(cfg) {
      if (A.ready) return;
      const h = root.location ? String(root.location.hash || '') : '';
      Object.assign(A, M.parseHash(/^#holdings\/activity/.test(h) ? h : '', cfg));
      A.base = typeof A.range === 'object' ? cfg.default_range : A.range;
      A.shown = cfg.page_size;
      A.ready = true;
    }
    const today = (cfg) => A.today || M.wallOf(new Date(), cfg.timezone).slice(0, 10);
    const spanOf = (cfg) => { const r = M.rangeOf(cfg, typeof A.range === 'object' ? A.base : A.range); return r ? r.span : null; };
    function series(cfg) {
      const s = spanOf(cfg);
      const a = s && ins.act[s];
      return a && a.s === 'ok' && a.data && a.data.available ? a.data : null;
    }
    // The control row and the body both read the model on every repaint; it is built
    // once per distinct state (inputs compared by identity, filters by value).
    let memo = null;
    function model() {
      const cfg = cfgOf();
      if (!cfg) return null;
      ready(cfg);
      const filters = { range: A.range, asset: A.asset, symbols: A.symbols, period: A.period, age: A.age, gran: A.gran };
      const key = JSON.stringify([A.view, today(cfg), filters]);
      const records = recs();
      const ser = series(cfg);
      if (memo && memo.key === key && memo.records === records && memo.series === ser && memo.cfg === cfg) return memo.m;
      const m = M.build({ cfg, view: A.view, records, today: today(cfg), series: ser, filters });
      memo = { key, records, series: ser, cfg, m };
      return m;
    }
    function writeHash() {
      if (!A.ready || state.tab !== 'activity' || !root.history || !root.location) return;
      const want = `#holdings/activity/${M.hashOf(A)}`;
      try { if (root.location.hash !== want) root.history.replaceState({ view: 'holdings' }, '', want); } catch (_) { /* sandboxed frames */ }
    }
    // Before the config loads, a deep link keeps its own state (the tab bar writes first).
    X.tabHash = (key) => {
      if (key !== 'activity') return '';
      if (A.ready) return `/${M.hashOf(A)}`;
      const h = root.location ? String(root.location.hash || '') : '';
      const m = h.match(/^#holdings\/activity(\/.*)$/);
      return m ? m[1] : '';
    };

    // ------------------------------------------------------------ formatting
    const curDec = (cfg) => cfg.decimals.currency;
    const $ = (v, cfg) => H.money(v, curDec(cfg));
    const $s = (v, cfg) => H.signedMoney(v, curDec(cfg));
    const pc = (v, cfg, signed = true) => { const t = M.pct(v, cfg, signed); return t === null ? NA : t; };
    const sh = (v, cfg) => { const t = M.shares(v, cfg); return t === null ? NA : priv(t); };
    const say = (v, cfg) => (state.hidden ? 'amount hidden' : `${v < 0 ? 'minus ' : v > 0 ? 'plus ' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: curDec(cfg), maximumFractionDigits: curDec(cfg) })}`);
    const noun = (cfg, view, n) => { const v = cfg.views.find((x) => x.key === view); return v.noun[n === 1 ? 0 : 1]; };
    const gnoun = (g, n) => g.noun[n === 1 ? 0 : 1];
    const day = (ymd) => M.dateLong(ymd) || NA;
    const winText = (m) => (m.window.from === m.window.to ? M.dateLong(m.window.from) : `${M.dateLong(m.window.from)} to ${M.dateLong(m.window.to)}`);
    const rangeLabel = (cfg) => (typeof A.range === 'object' ? 'Custom range' : M.rangeOf(cfg, A.range).label);

    // ------------------------------------------------------------ control row
    function tools() {
      const cfg = cfgOf();
      const R = ins.records || { s: 'idle' };
      if (!cfg) return R.s === 'idle' || R.s === 'loading'
        ? '<p class="hp-fine" role="status">Loading activity filters…</p>' : '';
      ready(cfg);
      const custom = typeof A.range === 'object';
      const chips = `<div class="mpa-seg" role="group" aria-label="Range">${cfg.ranges.map((r) => `<button type="button" class="mpa-segb${!custom && A.range === r.key ? ' is-on' : ''}" data-hp-x="act-range:${esc(r.key)}" aria-pressed="${!custom && A.range === r.key}" aria-label="${esc(r.long)}">${esc(r.label)}</button>`).join('')}</div>`;
      const assets = `<div class="mpa-seg" role="group" aria-label="Asset type">${Object.entries(M.ASSETS).map(([key, label]) => `<button type="button" class="mpa-segb${A.asset === key ? ' is-on' : ''}" data-hp-x="act-asset:${key}" aria-pressed="${A.asset === key}">${label}</button>`).join('')}</div>`;
      const all = M.symbolsFor(recs(), A.asset);
      const picked = new Set(A.symbols);
      const q = A.q.trim().toUpperCase();
      const ms = `<details class="mpa-ms" data-hp-disclosure="act-assets">
        <summary class="mpa-ms-b" aria-label="${esc(`Assets: ${A.symbols.length ? A.symbols.join(', ') : 'all'}`)}">${A.symbols.length ? `${A.symbols.length} of ${all.length} assets` : `All assets <span class="mpa-ms-n">${all.length}</span>`}</summary>
        <div class="mpa-ms-pop">
          <input type="search" class="mpa-ms-q" id="mpa-ms-q" data-hp-x="act-q" placeholder="Search ${all.length} assets" aria-label="Search assets" value="${esc(A.q)}" autocomplete="off" spellcheck="false">
          <div class="mpa-ms-list" role="group" aria-label="Assets">${all.map((s) => `<label class="mpa-ms-o"${q && !s.includes(q) ? ' hidden' : ''}><input type="checkbox" data-hp-x="act-pick:${esc(s)}" value="${esc(s)}"${picked.has(s) ? ' checked' : ''}><span>${esc(s)}</span></label>`).join('')}</div>
          ${A.symbols.length ? '<button type="button" class="q-btn q-btn--link mpa-ms-clear" data-hp-x="act-syms:">All assets</button>' : ''}
        </div></details>`;
      const views = `<div class="mpa-seg mpa-views" role="group" aria-label="View">${cfg.views.map((v) => `<button type="button" class="mpa-segb${A.view === v.key ? ' is-on' : ''}" data-hp-x="act-view:${esc(v.key)}" aria-pressed="${A.view === v.key}">${esc(v.label)}</button>`).join('')}</div>`;
      const m = model();
      const active = [];
      if (custom) active.push([`${M.dateLong(A.range.from)} to ${M.dateLong(A.range.to)}`, 'act-clear:range']);
      if (m && m.selected && m.view !== 'lots') { const p = m.periods.find((x) => x.key === m.selected); active.push([p.long, 'act-clear:period']); }
      if (m && m.selected && m.view === 'lots') { const p = m.periods.find((x) => x.key === m.selected); active.push([p.label, 'act-clear:age']); }
      A.symbols.forEach((s) => active.push([s, `act-pick:${s}`]));
      const chipRow = active.length ? `<div class="mpa-active" role="group" aria-label="Active filters">${active.map(([t, x]) => `<button type="button" class="mpa-chip" data-hp-x="${esc(x)}" aria-label="${esc(`Remove filter ${t}`)}">${esc(t)}<span aria-hidden="true">×</span></button>`).join('')}<button type="button" class="q-btn q-btn--link mpa-clearall" data-hp-x="act-clear:all">Clear all</button></div>` : '';
      const status = R.s === 'loading' ? '<p class="hp-fine" role="status">Refreshing activity… Showing the last loaded records.</p>'
        : R.s === 'error' ? '<p class="hp-fine" role="status">Activity refresh failed. Showing the last loaded records. <button type="button" class="q-btn q-btn--link" data-hp-x="retry:records">Try again</button></p>' : '';
      return `<div class="mpa-ctl-row">${chips}${assets}${ms}<span class="mpa-gap"></span>${views}</div>${chipRow}${status}`;
    }
    X.slotRenderers['act-tools'] = tools;

    // ------------------------------------------------------------ KPI tiles
    function tile(label, value, subs = [], cls = '', title = '') {
      return `<div class="mpa-k"${title ? ` title="${esc(title)}"` : ''}><dt>${label}</dt><dd class="mpa-k-v ${cls}">${value}</dd>${subs.filter(Boolean).map((s) => `<dd class="mpa-k-s">${s}</dd>`).join('')}</div>`;
    }
    const tradeRef = (r) => `<span class="mpa-ref"><b>${esc(r.symbol)}</b> ${esc(M.dateLong(r.ymd))}</span>`;
    function kpiHtml(m, cfg) {
      const k = m.kpis;
      const none = !m.rows.length;
      let tiles;
      if (m.view === 'sales') {
        const g = m.gran;
        const gn = g.options.find((o) => o.key === g.key);
        const avg = [k.avgWin !== null ? `<span class="bull">${$s(k.avgWin, cfg)}</span>` : null, k.avgLoss !== null ? `<span class="bear">${$s(k.avgLoss, cfg)}</span>` : null].filter(Boolean);
        tiles = [
          tile(m.rows.some((r) => r.estimated) ? 'Realized P&amp;L (est.)' : 'Realized P&amp;L', $s(k.realized, cfg), [k.ret !== null ? `<span title="Realized P&amp;L divided by the cost basis of the closed lots">${pc(k.ret, cfg)} on closed cost</span>` : null], dirClass(k.realized)),
          tile('Closing trades', String(k.count), [k.winRate !== null ? `${pc(k.winRate, cfg, false)} win rate` : null]),
          tile(k.avgWin !== null && k.avgLoss === null ? 'Average win' : k.avgLoss !== null && k.avgWin === null ? 'Average loss' : 'Average win / loss', avg.length ? avg.join(' <span class="mpa-sl">/</span> ') : NA),
          tile('Best / worst trade', none ? NA : `<span class="${dirClass(k.best.realized)}">${$s(k.best.realized, cfg)}</span> <span class="mpa-sl">/</span> <span class="${dirClass(k.worst.realized)}">${$s(k.worst.realized, cfg)}</span>`,
            none ? [] : [tradeRef(k.best), k.worst !== k.best ? tradeRef(k.worst) : null]),
          tile(`Positive ${esc(gnoun(gn, 2))}`, k.periodsWith ? `${k.periodsPositive} of ${k.periodsWith}` : NA, [k.periodsWith ? `${esc(gnoun(gn, k.periodsWith))} with closings` : null]),
        ];
      } else if (m.view === 'buys') {
        tiles = [
          tile('Capital deployed', none ? NA : $(k.deployed, cfg)),
          tile('Purchases', String(k.count)),
          tile('Stocks bought', String(k.stocks)),
          tile('Average purchase', k.avg === null ? NA : $(k.avg, cfg)),
          tile('Largest purchase', k.largest ? $(k.largest.amount, cfg) : NA, k.largest ? [tradeRef(k.largest)] : []),
        ];
      } else {
        const ages = k.ages.filter((a) => a.n > 0);
        tiles = [
          tile('Unrealized P&amp;L', none ? NA : $s(k.unrealized, cfg), [k.ret !== null ? `<span title="Unrealized P&amp;L divided by the cost basis of the open lots">${pc(k.ret, cfg)} on cost</span>` : null], dirClass(k.unrealized)),
          tile('Open lots', String(k.count), [k.count ? `in ${k.stocks} ${plural(k.stocks, 'stock', 'stocks')}` : null]),
          tile('Cost basis', k.cost === null ? NA : $(k.cost, cfg)),
          tile('Value', k.value === null ? NA : $(k.value, cfg)),
          tile('Lots by age', ages.length ? `<span class="mpa-agebar" role="img" aria-label="${esc(ages.map((a) => `${a.label}: ${a.n}`).join(', '))}">${ages.map((a) => `<i style="--w:${(a.n / k.count).toFixed(4)}"></i>`).join('')}</span>` : NA,
            ages.length ? [`<span class="mpa-ages">${ages.map((a) => `${esc(a.label)} <b>${a.n}</b>`).join(' · ')}</span>`] : []),
        ];
      }
      return `<dl class="mpa-kpis">${tiles.join('')}</dl>`;
    }

    // ------------------------------------------------------------ the period chart
    const CHART_TITLE = { sales: 'Realized P&amp;L', buys: 'Capital deployed', lots: 'Unrealized P&amp;L' };
    function chartHtml(m, cfg) {
      const P = m.periods;
      const time = m.view !== 'lots';
      const g = m.gran;
      const gn = time ? g.options.find((o) => o.key === g.key) : null;
      const cumOn = time && A.cum;
      const sc = M.scale([...P.map((p) => p.value), ...(cumOn ? P.map((p) => p.cum) : [])], cfg.y_ticks);
      const zero = sc.at(0);
      const anyNoFig = P.some((p) => p.noFigure);
      const diverging = m.view !== 'buys';
      const head = `<header class="mpa-ch-h">
        <h2 id="mpa-ch-t">${CHART_TITLE[m.view]} <span>${time ? `per ${esc(gnoun(gn, 1))}` : 'by lot age'}</span></h2>
        <div class="mpa-ch-tools">
          ${time ? `<div class="mpa-seg mpa-seg--sm" role="group" aria-label="Granularity">${g.options.map((o) => `<button type="button" class="mpa-segb${g.key === o.key ? ' is-on' : ''}" data-hp-x="act-gran:${esc(o.key)}" aria-pressed="${g.key === o.key}"${o.fits ? '' : ` disabled title="${esc(`${o.count} ${gnoun(o, o.count)}: more than ${cfg.max_buckets} bars`)}"`}>${esc(o.label)}</button>`).join('')}</div>
          <button type="button" class="mpa-tog${cumOn ? ' is-on' : ''}" data-hp-x="act-cum" aria-pressed="${cumOn}"><i class="mpa-key mpa-key--cum" aria-hidden="true"></i>Cumulative</button>` : ''}
          ${anyNoFig ? '<span class="mpa-legend"><i class="mpa-key mpa-key--nofig" aria-hidden="true"></i>No broker figure</span>' : ''}
        </div>
      </header>`;
      const n = P.length;
      const bars = P.map((p, i) => {
        const v = p.value;
        const top = sc.at(Math.max(v, 0));
        const h = Math.abs(sc.at(v) - zero);
        const cls = [diverging ? (v < 0 ? 'is-down' : v > 0 ? 'is-up' : 'is-zero') : (v > 0 ? 'is-flow' : 'is-zero'), p.active ? '' : 'is-dim', m.selected === p.key ? 'is-sel' : '', p.noFigure ? 'is-nofig' : ''].filter(Boolean).join(' ');
        const label = `${p.long}: ${p.n ? `${m.view === 'buys' ? '' : v >= 0 ? 'gain ' : 'loss '}${say(v, cfg)}, ${p.n} ${noun(cfg, m.view, p.n)}` : `no ${noun(cfg, m.view, 2)}`}${p.noFigure ? ', part of it has no broker figure' : ''}`;
        return `<button type="button" class="mpa-b ${cls}" data-k="${esc(p.key)}" data-i="${i}" data-hp-x="act-bar:${i}" tabindex="${(m.selected ? m.selected === p.key : i === n - 1) ? 0 : -1}" aria-pressed="${m.selected === p.key}" aria-label="${esc(label)}">
          ${p.noFigure ? `<span class="mpa-nf" style="top:${(zero - Math.min(zero, 18)).toFixed(3)}%;height:${Math.min(zero, 18).toFixed(3)}%" aria-hidden="true"></span>` : ''}
          ${h > 0 ? `<span class="mpa-bf" style="top:${top.toFixed(3)}%;height:${h.toFixed(3)}%" aria-hidden="true"></span>` : ''}
        </button>`;
      }).join('');
      const xs = P.map((p, i) => (time ? p.axis : { main: p.label, year: null }) && `<span class="mpa-x" style="--i:${i}"><span class="mpa-x-m">${time && p.axis.pre ? `<span class="mpa-x-pre">${esc(p.axis.pre)} </span>` : ''}${esc(time ? p.axis.main : p.label)}</span>${time && p.axis.year ? `<b>${esc(p.axis.year)}</b>` : ''}</span>`).filter(Boolean).join('');
      const tickDec = Number.isInteger(sc.step) ? 0 : curDec(cfg);
      // With no rows there is no scale to state: only the zero line is drawn (audit A4).
      const ticks = m.windowRows.length ? sc.ticks : sc.ticks.filter((t) => t === 0);
      const ys = ticks.map((t) => `<span class="mpa-y${t === 0 ? ' is-zero' : ''}" style="top:${sc.at(t).toFixed(3)}%">${t === 0 ? '0' : priv(H.money(t, tickDec))}</span>`).join('');
      const grid = ticks.map((t) => `<i class="mpa-gl${t === 0 ? ' is-zero' : ''}" style="top:${sc.at(t).toFixed(3)}%"></i>`).join('');
      const cum = cumOn && n ? `<svg class="mpa-cum" viewBox="0 0 ${n} 100" preserveAspectRatio="none" aria-hidden="true"><polyline points="${P.map((p, i) => `${(i + 0.5).toFixed(2)},${sc.at(p.cum).toFixed(3)}`).join(' ')}" vector-effect="non-scaling-stroke"/></svg>` : '';
      const empty = !m.windowRows.length ? `<p class="mpa-ch-empty">${esc(emptyLine(m, cfg))}</p>` : '';
      return `${head}
        <div class="mpa-plot${time ? ' is-time' : ''}" style="--n:${n}">
          <div class="mpa-yaxis" aria-hidden="true">${ys}</div>
          <div class="mpa-area">
            <div class="mpa-grid" aria-hidden="true">${grid}</div>
            <div class="mpa-bars" role="group" aria-label="${esc(`${CHART_TITLE[m.view].replace('&amp;', 'and')} ${time ? `per ${gnoun(gn, 1)}` : 'by lot age'}. ${time ? 'Arrow keys move, Shift with an arrow selects a range, Enter applies it.' : ''}`)}">${bars}</div>
            ${cum}${empty}
            <div class="mpa-tip" role="status" aria-live="polite" hidden></div>
          </div>
          <div class="mpa-xaxis" aria-hidden="true">${xs}</div>
        </div>
        <p class="sr-only">${esc(summaryText(m, cfg))}</p>`;
    }
    function summaryText(m, cfg) {
      const k = m.kpis;
      if (!m.rows.length) return emptyLine(m, cfg);
      if (m.view === 'sales') {
        const g = m.gran.options.find((o) => o.key === m.gran.key);
        return `Realized ${say(k.realized, cfg)} from ${k.count} ${noun(cfg, 'sales', k.count)} in ${k.stocks} ${plural(k.stocks, 'stock', 'stocks')}, ${winText(m)}. ${k.periodsPositive} of ${k.periodsWith} ${gnoun(g, k.periodsWith)} with closings ended positive.${m.excluded.length ? ` ${m.excluded.length} ${noun(cfg, 'sales', m.excluded.length)} without a broker figure ${plural(m.excluded.length, 'is', 'are')} left out.` : ''}`;
      }
      if (m.view === 'buys') return `${say(k.deployed, cfg)} deployed in ${k.count} ${noun(cfg, 'buys', k.count)} of ${k.stocks} ${plural(k.stocks, 'stock', 'stocks')}, ${winText(m)}.`;
      return `${k.count} ${noun(cfg, 'lots', k.count)} with ${say(k.unrealized, cfg)} unrealized: ${k.ages.filter((a) => a.n).map((a) => `${a.label} ${a.n}`).join(', ')}.`;
    }
    function emptyLine(m, cfg) {
      const what = noun(cfg, m.view, 2);
      if (m.view === 'sales' && m.check && m.check.brokerTrades > 0) return `No ${what} in the loaded trade records for this range, though Robinhood counts ${m.check.brokerTrades}`;
      if (A.symbols.length) return `No ${what} for ${A.symbols.join(', ')} in this range`;
      return `No ${what} in this range`;
    }

    // Tooltip text for one bar.
    function tipHtml(m, p, cfg) {
      const v = (r) => (m.view === 'sales' ? r.realized : m.view === 'buys' ? r.amount : r.gain);
      const rows = [`<b class="mpa-tip-t">${esc(p.long)}</b>`];
      if (!p.n) rows.push(`<span>No ${esc(noun(cfg, m.view, 2))}</span>`);
      else {
        rows.push(`<span class="mpa-tip-v ${m.view === 'buys' ? '' : dirClass(p.value)}">${m.view === 'buys' ? $(p.value, cfg) : $s(p.value, cfg)}</span>`);
        rows.push(`<span>${p.n} ${esc(noun(cfg, m.view, p.n))}${m.view !== 'buys' ? ` · ${p.wins} up, ${p.losses} down` : ''}</span>`);
        if (p.best) rows.push(`<span>${m.view === 'buys' ? 'Largest' : 'Best'} <b class="hp-sym">${esc(p.best.symbol)}</b> ${m.view === 'buys' ? $(v(p.best), cfg) : $s(v(p.best), cfg)}</span>`);
        if (p.worst && m.view !== 'buys' && p.worst !== p.best) rows.push(`<span>Worst <b class="hp-sym">${esc(p.worst.symbol)}</b> ${$s(v(p.worst), cfg)}</span>`);
      }
      if (p.noFigure) rows.push('<span class="mpa-tip-n">Robinhood reports no figure for part of this period</span>');
      return rows.join('');
    }

    // ------------------------------------------------------------ ranking by stock
    function rankHtml(m, cfg) {
      const R = m.ranking;
      const ends = cfg.ranking_ends;
      const diverging = m.view !== 'buys';
      const max = Math.max(...R.map((e) => Math.abs(e.value)), 0) || 1;
      const unit = m.view === 'sales' ? 'realized' : m.view === 'buys' ? 'bought' : 'unrealized';
      const row = (e) => {
        const on = A.symbols.length === 1 && A.symbols[0] === e.symbol;
        return `<li><button type="button" class="mpa-r${on ? ' is-on' : ''}" data-sym="${esc(e.symbol)}" data-hp-x="act-sym:${esc(e.symbol)}" aria-pressed="${on}" aria-label="${esc(`${e.symbol}: ${say(e.value, cfg)} ${unit}, ${e.n} ${noun(cfg, m.view, e.n)}. ${on ? 'Clear the filter' : 'Filter to it'}.`)}">
          <span class="hp-sym">${esc(e.symbol)}</span>
          <span class="mpa-r-bar${diverging ? '' : ' is-flow'}" aria-hidden="true"><i class="${e.value < 0 ? 'is-down' : 'is-up'}" style="--s:${(Math.abs(e.value) / max / (diverging ? 2 : 1)).toFixed(4)}"></i></span>
          <span class="mpa-r-v ${diverging ? dirClass(e.value) : ''}">${diverging ? $s(e.value, cfg) : $(e.value, cfg)}</span>
          <span class="mpa-r-n">${e.n}</span></button></li>`;
      };
      let list;
      const fold = !A.rankAll && R.length > 2 * ends + 1;
      if (!R.length) list = `<p class="mpa-empty">${esc(emptyLine(m, cfg))}</p>`;
      else if (fold) {
        const top = diverging ? R.slice(0, ends) : R.slice(0, 2 * ends);
        const bottom = diverging ? R.slice(-ends) : [];
        const mid = R.slice(top.length, R.length - bottom.length);
        const midSum = mid.reduce((t, e) => t + e.value, 0);
        list = `<ul class="mpa-rl">${top.map(row).join('')}
          <li><button type="button" class="mpa-r mpa-r--fold" data-hp-x="act-rankall" aria-label="${esc(`${mid.length} more stocks, ${say(midSum, cfg)} together. Show all.`)}"><span>${mid.length} more</span><span class="mpa-r-bar" aria-hidden="true"></span><span class="mpa-r-v ${diverging ? dirClass(midSum) : ''}">${diverging ? $s(midSum, cfg) : $(midSum, cfg)}</span><span class="mpa-r-n">${mid.reduce((t, e) => t + e.n, 0)}</span></button></li>
          ${bottom.map(row).join('')}</ul>`;
      } else list = `<ul class="mpa-rl">${R.map(row).join('')}</ul>`;
      const toggle = R.length > 2 * ends + 1 ? `<button type="button" class="q-btn q-btn--link" data-hp-x="act-rankall" aria-expanded="${A.rankAll}">${A.rankAll ? 'Show top and bottom' : `Show all ${R.length}`}</button>` : '';
      const vcfg = cfg.views.find((v) => v.key === m.view);
      const colHead = R.length ? `<div class="mpa-rh" aria-hidden="true"><span>Stock</span><span></span><span>${esc(vcfg.value)}</span><span>${esc(vcfg.count)}</span></div>` : '';
      const ok = m.check && m.check.matches ? `<p class="mpa-rt-ok" role="note">Matches Robinhood's total for ${esc(M.rangeOf(cfg, A.range).long)}</p>` : '';
      const ck = m.check && !m.check.matches ? `<div class="mpa-rt mpa-rt--diff" role="note"><span>Robinhood's total for ${esc(M.rangeOf(cfg, A.range).long)}</span><b class="${dirClass(m.check.broker)}">${$s(m.check.broker, cfg)}</b></div>
        <div class="mpa-rt mpa-rt--diff"><span>Not in the trade records</span><b class="${dirClass(m.check.diff)}">${$s(m.check.diff, cfg)}</b></div>` : '';
      return `<header class="mpa-ch-h"><h2 id="mpa-rk-t">By stock <span>${R.length} ${plural(R.length, 'stock', 'stocks')}</span></h2>${toggle}</header>
        ${colHead}${list}
        <div class="mpa-rt"><span>Total</span><b class="${diverging ? dirClass(m.rankingTotal) : ''}">${diverging ? $s(m.rankingTotal, cfg) : $(m.rankingTotal, cfg)}</b></div>${ck}${ok}`;
    }

    // ------------------------------------------------------------ the table
    const COLS = {
      sales: (m) => [['date', 'Closed'], ['symbol', 'Stock'], ['qty', 'Quantity', 'num is-minor'], ['', 'Cost → sell', 'num is-minor', "Average cost per share of the closed lots, implied by Robinhood's realized gain (sale less gain). It can differ from any one order's price."], ['value', 'Realized', 'num'],
        ...(m.rows.some((r) => r.held !== null) ? [['held', 'Days held', 'num']] : [])],
      buys: () => [['date', 'Date'], ['symbol', 'Stock'], ['qty', 'Quantity at price', 'num'], ['value', 'Amount', 'num']],
      lots: (m) => [['symbol', 'Stock'], ['date', m.rows.some((r) => r.term === 'long') ? 'Acquired · term' : 'Acquired'], ['qty', 'Quantity', 'num is-minor'], ['cost', 'Cost', 'num is-minor'], ['valueNow', 'Value', 'num'], ['value', 'Unrealized', 'num']],
    };
    const SORT = { date: (r) => r.wall || r.ymd || '', symbol: (r) => r.symbol, qty: (r) => r.qty, value: (r) => r.realized ?? r.amount ?? r.gain, held: (r) => r.held, cost: (r) => r.cost, valueNow: (r) => r.value };
    function sortRows(rows, view) {
      const s = A.sort || { key: view === 'lots' ? 'value' : 'date', dir: 'desc' };
      const get = SORT[s.key] || SORT.date;
      const k = s.dir === 'asc' ? 1 : -1;
      return rows.slice().sort((a, b) => {
        const x = get(a);
        const y = get(b);
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * k;
      });
    }
    function tableHtml(m, cfg) {
      const cols = COLS[m.view](m);
      const s = A.sort || { key: m.view === 'lots' ? 'value' : 'date', dir: 'desc' };
      const head = `<thead><tr>${cols.map(([key, label, cls = '', tip = '']) => {
        if (!key) return `<th scope="col" class="${cls}"${tip ? ` title="${esc(tip)}"` : ''}>${label}</th>`;
        const on = s.key === key;
        return `<th scope="col" class="${cls}" aria-sort="${on ? (s.dir === 'asc' ? 'ascending' : 'descending') : 'none'}"><button type="button" class="q-sortable" data-hp-x="act-sort:${key}">${label}<span class="q-sort${on ? ' is-on' : ''}" aria-hidden="true">${on && s.dir === 'asc' ? '▲' : '▼'}</span></button></th>`;
      }).join('')}</tr></thead>`;
      const rows = sortRows(m.rows, m.view);
      const page = rows.slice(0, A.shown);
      const symCell = (r) => `<th scope="row"><button type="button" class="mpa-symb" data-sym="${esc(r.symbol)}" data-hp-x="act-sym:${esc(r.symbol)}" aria-label="${esc(`Filter to ${r.symbol}`)}"><span class="hp-sym">${esc(r.symbol)}</span></button></th>`;
      const when = (r) => `<td><span class="mpa-when" title="${esc(r.t ? M.clock(r.t, cfg.timezone) : '')}">${esc(M.dateLong(r.ymd))}</span></td>`;
      const ret = (v) => (v === null ? '' : ` <span class="mpa-ret">${pc(v, cfg)}</span>`);
      let body;
      if (m.view === 'sales') {
        const held = cols.some(([k]) => k === 'held');
        body = page.map((r) => `<tr data-k="${esc(r.pk)}" data-sym="${esc(r.symbol)}">${when(r)}${symCell(r)}<td class="num is-minor">${sh(r.qty, cfg)}</td>
          <td class="num mpa-px is-minor">${r.entry === null ? NA : priv(H.price(r.entry))} <span aria-hidden="true">→</span><span class="sr-only">to</span> ${r.price === null ? NA : priv(H.price(r.price))}</td>
          <td class="num ${dirClass(r.realized)}">${$s(r.realized, cfg)}${ret(r.ret)}</td>${held ? `<td class="num">${r.held === null ? NA : r.held}</td>` : ''}</tr>`).join('');
      } else if (m.view === 'buys') {
        body = page.map((r) => `<tr data-k="${esc(r.pk)}" data-sym="${esc(r.symbol)}">${when(r)}${symCell(r)}<td class="num">${sh(r.qty, cfg)} <span class="mpa-at">at</span> ${priv(H.price(r.price))}</td><td class="num">${$(r.amount, cfg)}</td></tr>`).join('');
      } else {
        const term = cols[1][1] !== 'Acquired';
        body = page.map((r) => `<tr data-k="${esc(r.pk)}" data-sym="${esc(r.symbol)}">${symCell(r)}<td>${r.ymd ? esc(M.dateLong(r.ymd)) : NA}${term && r.term ? ` <span class="mpa-term">${esc(r.term)}</span>` : ''}</td><td class="num is-minor">${sh(r.qty, cfg)}</td><td class="num is-minor">${r.cost === null ? NA : $(r.cost, cfg)}</td><td class="num">${r.value === null ? NA : $(r.value, cfg)}</td><td class="num ${dirClass(r.gain)}">${$s(r.gain, cfg)}${ret(r.ret)}</td></tr>`).join('');
      }
      const filters = [rangeLabel(cfg) === 'Custom range' ? `${M.dateLong(A.range.from)} to ${M.dateLong(A.range.to)}` : `${rangeLabel(cfg)} (${winText(m)})`,
        ...(m.selected ? [m.periods.find((p) => p.key === m.selected)[m.view === 'lots' ? 'label' : 'long']] : []), ...A.symbols];
      const more = rows.length - page.length;
      const val = (r) => (m.view === 'sales' ? r.realized : m.view === 'buys' ? r.amount : r.gain);
      const total = rows.reduce((t, r) => t + val(r), 0);
      const header = `<header class="mpa-tbl-h">
          <p class="mpa-sum">Showing <b>${page.length}</b> of <b>${rows.length}</b> ${esc(noun(cfg, m.view, rows.length))}</p>
          <ul class="mpa-sum-f">${filters.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>
          ${rows.length ? `<dl class="mpa-sum-t"><dt>Table total</dt><dd class="${m.view === 'buys' ? '' : dirClass(total)}">${m.view === 'buys' ? $(total, cfg) : $s(total, cfg)}</dd><dt>Stocks</dt><dd>${new Set(rows.map((r) => r.symbol)).size}</dd></dl>` : ''}
        </header>`;
      const side = `<section class="q-panel hp-panel mpa-card mpa-gross" aria-labelledby="mpa-gr-t">${grossHtml(m, cfg)}</section>`;
      if (!rows.length) return `<section class="q-panel hp-panel mpa-card mpa-tw">${header}<p class="mpa-empty">${esc(emptyLine(m, cfg))}</p></section>${side}`;
      return `<section class="q-panel hp-panel mpa-card mpa-tw">${header}
          <table class="mpa-t"><caption class="sr-only">${esc(cfg.views.find((v) => v.key === m.view).label)}</caption>${head}<tbody>${body}</tbody></table>
          ${more > 0 ? `<div class="mpa-more"><button type="button" class="q-btn" data-hp-x="act-more">Show ${Math.min(cfg.page_size, more)} more</button></div>` : ''}
        </section>${side}`;
    }

    // Gross gain against gross loss over the same rows (they add up to the total), or,
    // for purchases, the spread of purchase sizes.
    function grossHtml(m, cfg) {
      const k = m.kpis;
      const none = !m.rows.length;
      if (m.view === 'buys') {
        return `<h2 class="mpa-gr-t" id="mpa-gr-t">Purchase sizes</h2>
          ${none ? `<p class="mpa-empty">${esc(emptyLine(m, cfg))}</p>` : `<dl class="mpa-sum-t mpa-gr-dl"><dt>Smallest</dt><dd>${$(k.smallest, cfg)}</dd><dt>Median</dt><dd>${$(k.median, cfg)}</dd><dt>Average</dt><dd>${$(k.avg, cfg)}</dd><dt>Largest</dt><dd>${$(k.largest.amount, cfg)}</dd></dl>`}`;
      }
      const sales = m.view === 'sales';
      const title = sales ? 'Gross profit and gross loss' : 'Unrealized gains and losses';
      if (none) return `<h2 class="mpa-gr-t" id="mpa-gr-t">${title}</h2><p class="mpa-empty">${esc(emptyLine(m, cfg))}</p>`;
      const max = Math.max(k.grossGain, -k.grossLoss) || 1;
      const bar = (v, cls, label) => `<div class="mpa-gr-row"><span>${label}</span><span class="mpa-gr-bar" aria-hidden="true"><i class="${cls}" style="--s:${(Math.abs(v) / max).toFixed(4)}"></i></span><b class="${dirClass(v)}">${$s(v, cfg)}</b></div>`;
      const pfLabel = sales ? 'Profit factor' : 'Gain to loss';
      const pf = k.profitFactor === null ? `<p class="mpa-gr-pf"><span>${pfLabel}</span><b>${esc(k.grossLoss === 0 ? (sales ? 'no losing trades' : 'no lots at a loss') : '--')}</b></p>`
        : `<p class="mpa-gr-pf" title="${esc(sales ? 'Gross profit divided by gross loss' : 'Unrealized gains divided by unrealized losses')}"><span>${pfLabel}</span><b>${k.profitFactor.toFixed(cfg.decimals.percent)}</b></p>`;
      return `<h2 class="mpa-gr-t" id="mpa-gr-t">${title}</h2>
        ${bar(k.grossGain, 'is-up', sales ? 'Gross profit' : 'Gains')}
        ${bar(k.grossLoss, 'is-down', sales ? 'Gross loss' : 'Losses')}
        <div class="mpa-rt"><span>Net</span><b class="${dirClass(m.total)}">${$s(m.total, cfg)}</b></div>
        ${pf}`;
    }

    // ------------------------------------------------------------ footer: coverage and definitions
    function coverageText(m, cfg) {
      const d = recs();
      const cov = d.coverage || {};
      const ymdOf = (iso) => M.wallOf(new Date(iso), cfg.timezone).slice(0, 10);
      // The server's coverage, else the loaded rows' own dates.
      const span = (c) => {
        const a = c ? (c.first && ymdOf(c.first)) : m.loaded.from;
        const z = c ? (c.last && ymdOf(c.last)) : m.loaded.to;
        return a ? `${M.dateLong(a)} to ${M.dateLong(z)}` : 'no dated records';
      };
      const cut = (c, what) => {
        if (c && c.complete) return '';
        if (!c) return ' The server did not report whether this history is complete; restart QUANTA to check.';
        if (c.capped_by === 'rows') return ` Capped: only the newest ${c.cap} of ${c.read} ${what} are loaded, so earlier ones are missing.`;
        if (c.capped_by === 'pages') return ` Capped: the read stopped after ${c.pages} pages, so ${what} before that are missing.`;
        if (c.capped_by === 'span') return ` Incomplete: Robinhood returned its "${c.span}" window only; restart QUANTA to read all history.`;
        return ' The server did not report whether this history is complete; restart QUANTA to check.';
      };
      const parts = [];
      if (A.asset !== 'equity' && d.crypto) {
        if (!d.crypto.available) parts.push(d.crypto.reason);
        else if (m.view === 'sales' && (d.crypto.fills || []).length) parts.push("Crypto realized P&L is a first-in, first-out estimate from recorded executions using effective fill prices. Transfers and missing earlier buys cannot be reconstructed; unmatched sales are excluded.");
        if (d.crypto.available && m.view !== 'lots') {
          parts.push('Crypto executions: ' + span(d.crypto.coverage) + '.' + cut(d.crypto.coverage, 'crypto executions'));
          if (d.crypto.rejected) parts.push(d.crypto.rejected + ' crypto executions or filled orders could not be read.');
        }
        if (m.view === 'lots') parts.push('Crypto tax lots are not provided through this connection.');
      }
      if (m.view === 'sales') {
        const c = cov.trades;
        if (m.source === 'robinhood') parts.push(`Closing trades: Robinhood's trade records, ${span(c)} (${c ? c.passed : m.loaded.count} ${noun(cfg, 'sales', 2)}).${cut(c, noun(cfg, 'sales', 2))}`);
        else parts.push(`Robinhood's trade records are not available: realized gains are QUANTA's estimate, matched first-in, first-out against the buys in the order history (${span(cov.fills)}). The estimate cannot see stock splits; a sale of shares bought before the history begins is left out.${cut(cov.fills, 'filled orders')}`);
        parts.push('Return on closed cost = realized P&L ÷ cost basis of the closed lots (proceeds less the realized gain).');
        const ex = m.excluded;
        if (ex.length) {
          const days = [...new Set(ex.map((e) => e.row.ymd))].sort().map((x) => M.dateLong(x));
          const inB = ex.filter((e) => e.reason === 'bucket');
          const nb = new Set(inB.map((e) => e.bucket.start)).size;
          const own = ex.length - inB.length;
          const why = [inB.length ? `${inB.length} in ${plural(nb, 'a period', `${nb} periods`)} Robinhood reports without one, usually transfers` : null,
            own ? `${own} with no figure on the trade itself` : null].filter(Boolean).join('; ');
          parts.push(`Left out everywhere: ${ex.length} ${noun(cfg, 'sales', ex.length)} without a realized figure (${why}): ${days.join(', ')}.`);
        }
        if (m.inNoFigure) {
          parts.push(`${m.inNoFigure} ${noun(cfg, 'sales', m.inNoFigure)} ${plural(m.inNoFigure, 'falls', 'fall')} in periods Robinhood reports without a total (usually transfers): ${plural(m.inNoFigure, 'it is', 'they are')} counted from ${plural(m.inNoFigure, 'its', 'their')} own figures and left out of the comparison with Robinhood's total.`);
        }
        if (m.source === 'robinhood' && !series(cfg) && spanOf(cfg)) {
          const a = ins.act[spanOf(cfg)];
          if (a && (a.s === 'error' || a.s === 'missing' || (a.s === 'ok' && !a.data.available))) parts.push("Robinhood's window total could not be read, so it is not compared.");
        }
      } else if (m.view === 'buys') {
        parts.push(`Purchases: filled buy orders from Robinhood's order history, ${span(cov.fills)}.${cut(cov.fills, 'filled orders')}`);
      } else {
        parts.push('Open lots as Robinhood reports them now; return = unrealized P&L ÷ cost basis.');
        if (m.undated && !m.window.all) parts.push(`${m.undated} ${noun(cfg, 'lots', m.undated)} without an acquisition date ${plural(m.undated, 'appears', 'appear')} only when the range covers all history.`);
        const nog = m.excluded.length;
        if (nog) parts.push(`${nog} ${noun(cfg, 'lots', nog)} without a price or cost ${plural(nog, 'is', 'are')} left out.`);
      }
      if (m.unclassified) parts.push(`${m.unclassified} loaded records have no verified asset type and appear only under All.`);
      // One zone name for every row: today's daylight-time abbreviation was wrong for trades
      // made under standard time (audit A3). Each row's tooltip still shows its own.
      parts.push(`Times are ${String(cfg.timezone).split('/').pop().replace(/_/g, ' ')} time.`);
      return parts.join(' ');
    }

    // ------------------------------------------------------------ body
    function skeleton() {
      return '<div class="mpa-skel" role="status"><span class="sr-only">Loading activity…</span><i></i><i></i><i></i></div>';
    }
    function body() {
      const R = ins.records || { s: 'idle' };
      if (R.s === 'idle' || (R.s === 'loading' && !R.data)) return skeleton();
      if (R.s === 'missing') return '<p class="mp-unavail">Activity appears after a QUANTA restart.</p>';
      if (R.s === 'error' && !R.data) return `<p class="mp-unavail" role="status">Activity is unavailable: ${esc(R.error)}. <button type="button" class="q-btn q-btn--link" data-hp-x="retry:records">Try again</button></p>`;
      const d = R.data;
      if (!d.available) return `<p class="mp-unavail">${esc(d.reason)}</p>`;
      const cfg = d.config;
      if (!cfg) return '<p class="mp-unavail" role="status">This QUANTA server predates the Activity redesign. Restart QUANTA to load it.</p>';
      ready(cfg);
      // Robinhood's window total is only a cross-check: the records draw now and the
      // comparison fills in when it arrives (load repaints this slot).
      const span = spanOf(cfg);
      if (A.view === 'sales' && d.closed_trades != null && span && !ins.act[span]) Promise.resolve().then(() => load(span));
      const m = model();
      if (m.unavailable && m.reason) return `<p class="mp-limit" role="note">${esc(m.reason)}</p>`;
      if (m.unavailable) {
        return `<p class="mp-limit" role="note">Robinhood provided neither trade records nor order history, so ${esc(noun(cfg, m.view, 2))} cannot be listed. ${esc(cfg.views.find((v) => v.key === 'lots').label)} are still shown.</p>`;
      }
      ins.actModel = m;
      return `${kpiHtml(m, cfg)}
        <div class="mpa-main">
          <section class="q-panel hp-panel mpa-card mpa-chart" aria-labelledby="mpa-ch-t">${chartHtml(m, cfg)}</section>
          <section class="q-panel hp-panel mpa-card mpa-rank" aria-labelledby="mpa-rk-t">${rankHtml(m, cfg)}</section>
        </div>
        <div class="mpa-table" role="region" aria-label="${esc(cfg.views.find((v) => v.key === m.view).label)}">${tableHtml(m, cfg)}</div>
        <p class="mpa-foot">${esc(coverageText(m, cfg))}</p>`;
    }
    X.slotRenderers['activity-body'] = body;

    function activity() {
      return `<div class="mpa" id="mpa-root">
        <div class="mpa-ctl" data-mp-slot="act-tools">${tools()}</div>
        <div class="mpa-body" data-mp-slot="activity-body">${body()}</div>
      </div>`;
    }

    // ------------------------------------------------------------ actions
    const repaint = () => { X.paintSlot('act-tools'); X.paintSlot('activity-body'); writeHash(); measure(); };
    const resetPage = () => { const cfg = cfgOf(); A.shown = cfg ? cfg.page_size : A.shown; };
    const act = X.actions;
    act['act-range'] = (k) => {
      const cfg = cfgOf();
      if (!cfg || !M.rangeOf(cfg, k)) return;
      Object.assign(A, { range: k, base: k, period: null, gran: null });
      resetPage();
      repaint();
      load(M.rangeOf(cfg, k).span);
      H.announce(`Activity for ${M.rangeOf(cfg, k).long}.`);
    };
    act['act-view'] = (k) => {
      const cfg = cfgOf();
      if (!cfg || !cfg.views.some((v) => v.key === k)) return;
      Object.assign(A, { view: k, period: null, age: null, sort: null, rankAll: false });
      resetPage();
      repaint();
      H.announce(`${cfg.views.find((v) => v.key === k).label}.`);
    };
    act['act-asset'] = (key) => {
      if (!Object.hasOwn(M.ASSETS, key)) return;
      const available = new Set(M.symbolsFor(recs(), key));
      A.symbols = A.symbols.filter((symbol) => available.has(symbol));
      A.q = '';
      Object.assign(A, { asset: key, period: null, age: null });
      resetPage();
      repaint();
      H.announce(`Activity: ${M.ASSETS[key]}.`);
    };
    act['act-gran'] = (k) => { A.gran = k; A.period = null; repaint(); };
    act['act-cum'] = () => { A.cum = !A.cum; repaint(); };
    act['act-rankall'] = () => { A.rankAll = !A.rankAll; X.paintSlot('activity-body'); };
    act['act-more'] = () => { const cfg = cfgOf(); A.shown += cfg.page_size; X.paintSlot('activity-body'); };
    act['act-sort'] = (key) => {
      const s = A.sort || { key: A.view === 'lots' ? 'value' : 'date', dir: 'desc' };
      A.sort = s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'symbol' ? 'asc' : 'desc' };
      X.paintSlot('activity-body');
    };
    // One stock from the ranking or the table: filter to it, or clear it when it is the filter.
    act['act-sym'] = (s) => {
      A.symbols = A.symbols.length === 1 && A.symbols[0] === s ? [] : [s];
      resetPage();
      repaint();
      H.announce(A.symbols.length ? `Showing ${s}.` : 'Showing all stocks.');
    };
    // The multi-select: toggle one stock.
    act['act-pick'] = (s) => {
      A.symbols = A.symbols.includes(s) ? A.symbols.filter((x) => x !== s) : [...A.symbols, s].sort();
      resetPage();
      repaint();
      H.announce(A.symbols.length ? `Showing ${A.symbols.join(', ')}.` : 'Showing all stocks.');
    };
    act['act-syms'] = () => { A.symbols = []; resetPage(); repaint(); H.announce('Showing all stocks.'); };
    act['act-clear'] = (what) => {
      const cfg = cfgOf();
      if (what === 'range' || what === 'all') { A.range = A.base || (cfg && cfg.default_range); A.gran = null; }
      if (what === 'period' || what === 'all') A.period = null;
      if (what === 'age' || what === 'all') A.age = null;
      if (what === 'all') { A.symbols = []; A.asset = 'all'; }
      resetPage();
      repaint();
      H.announce(what === 'all' ? 'Filters cleared.' : 'Filter removed.');
    };
    act['act-q'] = () => {};
    // A bar: a click toggles that period (or age) as a filter, unless it ended a brush.
    act['act-bar'] = (i) => {
      if (A.brushed) { A.brushed = false; return; }
      const m = ins.actModel;
      const p = m && m.periods[Number(i)];
      if (!p) return;
      if (m.view === 'lots') A.age = A.age === p.key ? null : p.key;
      else A.period = A.period === p.key ? null : p.key;
      resetPage();
      repaint();
      const again = typeof document !== 'undefined' && document.querySelector(`#mpa-root .mpa-b[data-k="${cssq(p.key)}"]`);
      if (again) again.focus({ preventScroll: true });
      H.announce(A.period || A.age ? `Filtered to ${m.view === 'lots' ? p.label : p.long}.` : 'Period filter cleared.');
    };
    function applyBrush(a, b) {
      const m = ins.actModel;
      if (!m || m.view === 'lots') return;
      const [i, j] = a < b ? [a, b] : [b, a];
      const from = m.periods[i].start < m.window.from ? m.window.from : m.periods[i].start;
      const to = m.periods[j].end > m.window.to ? m.window.to : m.periods[j].end;
      if (typeof A.range !== 'object') A.base = A.range;
      Object.assign(A, { range: { from, to }, period: null, gran: null, brush: null });
      resetPage();
      repaint();
      H.announce(`Range set to ${M.dateLong(from)} to ${M.dateLong(to)}.`);
    }

    // ------------------------------------------------------------ hover, brush, keyboard
    const cssq = (v) => String(v).replace(/["\\]/g, '\\$&');
    function hot(key, sym) {
      const rootEl = document.getElementById('mpa-root');
      if (!rootEl) return;
      rootEl.querySelectorAll('.is-hot').forEach((el) => el.classList.remove('is-hot'));
      if (key != null) rootEl.querySelectorAll(`.mpa-b[data-k="${cssq(key)}"], .mpa-t tr[data-k="${cssq(key)}"]`).forEach((el) => el.classList.add('is-hot'));
      if (sym) rootEl.querySelectorAll(`.mpa-r[data-sym="${cssq(sym)}"]`).forEach((el) => el.classList.add('is-hot'));
    }
    function showTip(bar) {
      const m = ins.actModel;
      const cfg = cfgOf();
      const area = bar.closest('.mpa-area');
      const tip = area && area.querySelector('.mpa-tip');
      if (!m || !tip) return;
      const p = m.periods[Number(bar.dataset.i)];
      if (!p) return;
      tip.innerHTML = tipHtml(m, p, cfg);
      tip.hidden = false;
      const frac = (Number(bar.dataset.i) + 0.5) / m.periods.length;
      tip.style.setProperty('--x', frac.toFixed(4));
      tip.classList.toggle('is-left', frac > 0.6);
      hot(p.key, null);
    }
    function hideTip() {
      document.querySelectorAll('#mpa-root .mpa-tip').forEach((t) => { t.hidden = true; });
      hot(null, null);
    }
    function markBrush() {
      const bars = [...document.querySelectorAll('#mpa-root .mpa-b')];
      bars.forEach((b) => b.classList.remove('is-brush'));
      if (!A.brush) return;
      const [i, j] = A.brush.a < A.brush.b ? [A.brush.a, A.brush.b] : [A.brush.b, A.brush.a];
      bars.slice(i, j + 1).forEach((b) => b.classList.add('is-brush'));
    }
    // The sticky control row's height, for the offsets below it. Observed, not polled:
    // reading offsetHeight right after a repaint forced a synchronous layout each time.
    let ctlRo = null;
    function measure() {
      if (typeof document === 'undefined') return;
      const ctl = document.querySelector('#mpa-root .mpa-ctl');
      if (!ctl) return;
      if (typeof ResizeObserver === 'undefined') {
        const rootEl = document.getElementById('mpa-root');
        if (rootEl) rootEl.style.setProperty('--mpa-ctl-h', `${ctl.offsetHeight}px`);
        return;
      }
      if (ctlRo && ctlRo.el === ctl) return;
      if (ctlRo) ctlRo.ro.disconnect();
      const ro = new ResizeObserver((entries) => {
        const e = entries[entries.length - 1];
        const box = e.borderBoxSize && e.borderBoxSize[0];
        const h = box ? box.blockSize : e.target.offsetHeight;
        const rootEl = e.target.closest('#mpa-root');
        if (rootEl) rootEl.style.setProperty('--mpa-ctl-h', `${Math.round(h)}px`);
      });
      ro.observe(ctl);
      ctlRo = { ro, el: ctl };
    }
    function bind() {
      if (typeof document === 'undefined') return;
      const view = document.getElementById('view-holdings');
      if (!view || view._mpaBound) return;
      view._mpaBound = true;
      view.addEventListener('mouseover', (e) => {
        const bar = e.target.closest && e.target.closest('#mpa-root .mpa-b');
        if (bar) { showTip(bar); return; }
        const tr = e.target.closest && e.target.closest('#mpa-root .mpa-t tbody tr');
        if (tr) hot(tr.dataset.k, tr.dataset.sym);
      });
      view.addEventListener('mouseout', (e) => {
        const from = e.target.closest && e.target.closest('#mpa-root .mpa-b, #mpa-root .mpa-t tbody tr');
        const to = e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('#mpa-root .mpa-b, #mpa-root .mpa-t tbody tr');
        if (from && !to) hideTip();
      });
      view.addEventListener('focusin', (e) => { const bar = e.target.closest && e.target.closest('#mpa-root .mpa-b'); if (bar) showTip(bar); });
      view.addEventListener('focusout', (e) => { if (e.target.closest && e.target.closest('#mpa-root .mpa-b')) hideTip(); });
      view.addEventListener('pointerdown', (e) => {
        const bar = e.target.closest && e.target.closest('#mpa-root .mpa-plot.is-time .mpa-b');
        if (!bar || e.button !== 0) return;
        A.brush = { a: Number(bar.dataset.i), b: Number(bar.dataset.i), pointer: true };
      });
      // The bar under the pointer, also while a touch keeps the first bar captured.
      document.addEventListener('pointermove', (e) => {
        if (!A.brush || !A.brush.pointer) return;
        const el = document.elementFromPoint(e.clientX, e.clientY);
        const over = el && el.closest && el.closest('#mpa-root .mpa-plot.is-time .mpa-b');
        if (over && Number(over.dataset.i) !== A.brush.b) { A.brush.b = Number(over.dataset.i); markBrush(); showTip(over); }
      });
      document.addEventListener('pointerup', () => {
        if (!A.brush || !A.brush.pointer) return;
        const { a, b } = A.brush;
        A.brush = null;
        markBrush();
        if (a !== b) { A.brushed = true; setTimeout(() => { A.brushed = false; }, 0); applyBrush(a, b); }
      });
      view.addEventListener('keydown', (e) => {
        const bar = e.target.closest && e.target.closest('#mpa-root .mpa-b');
        if (!bar) return;
        const bars = [...document.querySelectorAll('#mpa-root .mpa-b')];
        const i = Number(bar.dataset.i);
        const time = !!bar.closest('.mpa-plot.is-time');
        const go = (j) => { const t = bars[Math.max(0, Math.min(bars.length - 1, j))]; bars.forEach((b) => { b.tabIndex = -1; }); t.tabIndex = 0; t.focus(); return Number(t.dataset.i); };
        const step = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[e.key];
        if (step !== undefined) {
          e.preventDefault();
          const j = go(Number.isFinite(step) ? i + step : step < 0 ? 0 : bars.length - 1);
          if (e.shiftKey && time) { A.brush = A.brush || { a: i, b: i }; A.brush.b = j; markBrush(); } else if (A.brush) { A.brush = null; markBrush(); }
          return;
        }
        if (e.key === 'Enter' && A.brush && A.brush.a !== A.brush.b) { e.preventDefault(); const { a, b } = A.brush; A.brush = null; applyBrush(a, b); return; }
        if (e.key === 'Escape') { A.brush = null; markBrush(); hideTip(); }
      });
      // Search inside the stock list: hide what does not match, without a repaint.
      view.addEventListener('input', (e) => {
        if (!e.target || e.target.id !== 'mpa-ms-q') return;
        A.q = e.target.value;
        const q = A.q.trim().toUpperCase();
        e.target.closest('.mpa-ms').querySelectorAll('.mpa-ms-o').forEach((o) => { o.hidden = !!q && !o.querySelector('input').value.includes(q); });
      });
      if (typeof ResizeObserver !== 'undefined') {
        // The Activity markup is rebuilt when the tab is drawn: re-attach to its control row.
        new ResizeObserver(() => measure()).observe(view);
      }
    }

    const prevTab = X.onTab;
    X.onTab = (key) => {
      if (prevTab) prevTab(key);
      if (key !== 'activity') return;
      bind();
      X.load('records');
      const cfg = cfgOf();
      if (cfg) { ready(cfg); const s = spanOf(cfg); if (s) load(s); writeHash(); }
      measure();
    };
    const prevReset = X.resetPlan;
    X.resetPlan = (keep) => {
      ins.act = {};
      Object.assign(A, { ready: false, asset: 'all', symbols: [], period: null, age: null, gran: null, brush: null, q: '' });
      if (prevReset) prevReset(keep);
    };

    Object.assign(X, { activity, activityBody: body, activityTools: tools, loadActivity: load, activityModel: model, activityTip: tipHtml, activityState: A });
    return X;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaActivity = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
