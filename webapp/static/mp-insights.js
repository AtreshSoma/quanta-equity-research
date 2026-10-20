/* My Portfolio insights: shared state, data loading, the performance chart, the exposure
 * card and the Overview composition. Later modules (mp-review, mp-plan, mp-news) add
 * their tabs to the same `host.ext`.
 *
 * Data comes from this server only (/api/portfolio/performance | exposure | events | ...),
 * each loaded independently: one section failing leaves the others standing, and a
 * section that cannot be built says why instead of drawing a guess.
 *
 * Two words are kept apart everywhere: "actual" (the account's own record) and
 * "simulation" (today's holdings replayed over past prices). The chart's label,
 * subtitle and table caption change with the kind; nothing here ever shows a
 * simulation as the account's performance.
 */
(function (root) {
  'use strict';

  const PREF_KEY = 'quanta.holdings.prefs';
  const RANGE_LABEL = { '1D': 'today', '1W': '1 week', '1M': '1 month', '3M': '3 months', '6M': '6 months', YTD: 'year to date', '1Y': '1 year', MAX: 'all available' };
  // The range buttons, in order. 6M stays a valid range for older links and the export.
  const RANGES = ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'MAX'];
  const RANGE_SHORT = { MAX: 'All' };
  // The server remembers a failed price read for 30 seconds; one retry follows it.
  const PERF_RETRY_MS = 35000;
  const perfUnusable = (d) => !d || d.available === false || !Object.values(d.simulation || {}).some((s) => s && s.available);
  const EXPO_VIEWS = [['sector', 'Sector'], ['asset_class', 'Asset class'], ['geography', 'Geography'], ['holdings', 'Holdings']];

  const readJson = (k, dflt) => { try { const v = JSON.parse(root.localStorage.getItem(k)); return v && typeof v === 'object' ? v : dflt; } catch (_) { return dflt; } };
  const writeJson = (k, v) => { try { root.localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* preference only */ } };

  function attach(host) {
    const H = host._h;
    const { esc, num, NA, priv, money, pct, signedPts, dirClass, plural } = H;
    const state = host._state;
    const Q = () => root.QuantaCharts;

    // The chart range is not remembered between visits: it opens on 1D while the market
    // is open and on 1M otherwise (see range()). Other view choices are.
    const saved = readJson(PREF_KEY, {});
    delete saved.range;
    const ins = {
      prefs: Object.assign({ range: null, bench: true, markers: false, expo: 'sector', alloc: 'sector' }, saved),
      perf: { s: 'idle' }, intra: { s: 'idle' }, expo: { s: 'idle' }, events: { s: 'idle' }, outcomes: { s: 'idle' }, records: { s: 'idle' },
      stamp: {}, animate: false, barsAnim: false,
    };
    state.ins = ins;
    const savePrefs = () => { const { range, ...rest } = ins.prefs; writeJson(PREF_KEY, rest); };
    // The range on screen: the viewer's pick, else 1D in a live session and 1M outside one.
    const range = () => {
      if (RANGE_LABEL[ins.prefs.range]) return ins.prefs.range;
      const pf = state.view && state.view.portfolio;
      return pf && H.sessionOf(pf).live ? '1D' : '1M';
    };

    // ------------------------------------------------------------ loading
    const LOADERS = {
      perf: () => H.api('GET', '/performance'),
      intra: () => H.api('GET', '/intraday'),
      expo: () => H.api('POST', '/exposure', { prefs: (host.ext.exposurePrefs && host.ext.exposurePrefs()) || {} }),
      events: () => H.api('GET', '/events'),
      outcomes: () => H.api('GET', '/outcomes'),
      records: () => H.api('GET', '/records'),
    };
    const SLOTS_FOR = {
      perf: ['perf-overview', 'perf-review', 'review-body', 'attrib', 'results', 'review-lessons', 'hold-overview', 'kpi-overview', 'movers-overview'],
      intra: ['perf-overview', 'perf-review'],
      expo: ['expo-overview', 'alloc-overview'],
      stale: ['banner'],
      events: ['events-line', 'news-body', 'news-alerts', 'review-lessons'],
      outcomes: ['outcomes', 'review-lessons'],
      records: ['records', 'act-tools', 'act-bystock', 'activity-body'],
    };

    async function load(key, force) {
      const cur = ins[key];
      if (cur.s === 'loading' || (cur.s === 'ok' && !force)) return;
      ins[key] = { s: 'loading', data: cur.data };
      paint(key);
      let res;
      try { res = await LOADERS[key](); } catch (_) { res = { ok: false, data: { error: 'The request failed.' } }; }
      if (res.status === 404) {
        // The server predates this route: say so once (the banner), not in every card.
        ins[key] = { s: 'missing', data: null };
        if (!ins.stale) { ins.stale = true; paintSlot('banner'); }
      } else {
        ins[key] = res.ok ? { s: 'ok', data: res.data, at: Date.now() } : { s: 'error', error: shortError(res), data: cur.data };
      }
      if (key === 'perf' || key === 'intra') ins.animate = true;
      if (key === 'expo') ins.barsAnim = true;
      paint(key);
      if (key === 'perf' && ins.perf.s === 'ok') {
        if (!perfUnusable(ins.perf.data)) ins.perfRetried = false;
        else if (!ins.perfRetried) {
          ins.perfRetried = true;
          root.setTimeout(() => { if (ins.perf.s === 'ok' && perfUnusable(ins.perf.data)) load('perf', true); }, PERF_RETRY_MS);
        }
      }
      if (key === 'events' && host.ext.onEvents) host.ext.onEvents();
    }

    // A card needs a line, not the transport's paragraph; the header status carries connection problems.
    function shortError(res) {
      if (!res.status) return 'The server did not answer';
      if (res.status >= 500) return 'The data source did not answer';
      return (res.data && res.data.error && String(res.data.error).length < 90 ? res.data.error : 'The request failed');
    }

    function reset(keepPrefs) {
      ['perf', 'intra', 'expo', 'events', 'outcomes', 'records'].forEach((k) => { ins[k] = { s: 'idle' }; });
      ins.stamp = {};
      ins.stale = false;
      if (host.ext.resetPlan) host.ext.resetPlan(keepPrefs);
    }

    // ------------------------------------------------------------ slots
    const slotRenderers = {};
    function paint(key) {
      if (typeof document === 'undefined') return;
      (SLOTS_FOR[key] || []).forEach(paintSlot);
    }
    function paintSlot(name) {
      const pf = state.view && state.view.portfolio;
      if (!pf || typeof document === 'undefined') return;
      const fn = slotRenderers[name];
      if (!fn) return;
      document.querySelectorAll(`[data-mp-slot="${name}"]`).forEach((el) => {
        // A slot is rebuilt in place: keep the focused control and open disclosures.
        const a = document.activeElement;
        const focusKey = a && el.contains(a) && a.dataset ? a.dataset.hpX : null;
        const open = [...el.querySelectorAll('details[data-hp-disclosure][open]')].map((d) => d.dataset.hpDisclosure);
        const sel = a && el.contains(a) && typeof a.selectionStart === 'number' ? [a.selectionStart, a.selectionEnd] : null;
        el.innerHTML = fn(pf);
        el.querySelectorAll('details[data-hp-disclosure]').forEach((d) => { d.open = open.includes(d.dataset.hpDisclosure); });
        mountCharts(el);
        if (focusKey) {
          const n = [...el.querySelectorAll('[data-hp-x]')].find((x) => x.dataset.hpX === focusKey);
          if (n) {
            n.focus({ preventScroll: true });
            if (sel) { try { n.setSelectionRange(sel[0], sel[1]); } catch (_) { /* no caret */ } }
          }
        }
      });
    }
    function paintAll() { Object.keys(slotRenderers).forEach(paintSlot); }

    function mountCharts(scope) {
      const Ch = Q();
      if (!Ch || !scope.querySelectorAll) return;
      scope.querySelectorAll('[data-mp-chart]').forEach((el) => {
        const spec = chartSpecFor(el.dataset.mpChart);
        if (!spec) return;
        Ch.mount(el, Object.assign(spec, { animate: ins.animate }));
      });
      ins.animate = false;
      if (host.ext && host.ext.onMount) host.ext.onMount(scope);
      // Exposure bars grow from zero once per load or view change (transform only).
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => {
          if (scope.querySelectorAll) scope.querySelectorAll('.mpx-list:not(.is-in)').forEach((l) => l.classList.add('is-in'));
          ins.barsAnim = false;
        });
      }
    }

    // ------------------------------------------------------------ performance
    // The Overview may show 1D (today's 5-minute bars); the Review works on daily closes,
    // so a 1D choice reads as 1M there.
    const rangeFor = (mode) => { const rk = range(); return mode === 'overview' || rk !== '1D' ? rk : '1M'; };
    const reviewRange = () => rangeFor('review');
    const rangeData = (mode = 'review') => {
      const rk = rangeFor(mode);
      if (rk === '1D') {
        const d = ins.intra.data;
        return d ? { kind: 'simulation', d, p: ins.perf.data || {}, intraday: true, rk } : null;
      }
      const p = ins.perf.data;
      if (!p || !p.available) return null;
      const act = p.actual && p.actual.available && p.actual.ranges && p.actual.ranges[rk];
      if (act && act.available) return { kind: 'ledger', d: act, p, rk };
      const sim = p.simulation && p.simulation[rk];
      return { kind: 'simulation', d: sim, p, rk };
    };

    const isStamp = (iso) => String(iso || '').length > 10;
    const nyText = (iso, opts) => {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      try { return d.toLocaleString('en-US', Object.assign({ timeZone: 'America/New_York' }, opts)); } catch (_) { return d.toLocaleString('en-US', opts); }
    };
    const dateText = (iso) => {
      if (isStamp(iso)) return `${nyText(iso, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} ET`;
      const d = new Date(`${iso}T12:00:00`);
      return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    };
    const tickFor = (rk) => (iso) => {
      if (isStamp(iso)) return nyText(iso, { hour: 'numeric', minute: '2-digit' }).replace(':00', '');
      const d = new Date(`${iso}T12:00:00`);
      if (Number.isNaN(d.getTime())) return iso;
      // Day ticks for short ranges; month names for a year or less; month and year beyond.
      if (['1W', '1M', '3M'].includes(rk)) return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      if (rk === 'MAX') return `${d.toLocaleDateString('en-US', { month: 'short' })} ’${String(d.getFullYear()).slice(2)}`;
      return d.toLocaleDateString('en-US', { month: 'short' });
    };
    const sp = (v, dec = 1) => (Number.isFinite(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(dec)}%` : '--');
    // Session boundaries of an intraday series, as New York clock times.
    const hhmm = (iso) => nyText(iso, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

    function chartSpecFor(name) {
      const mode = name === 'overview' ? 'overview' : 'review';
      const rd = rangeData(mode);
      if (!rd || !rd.d || !rd.d.available) return null;
      const d = rd.d;
      const review = mode !== 'overview';
      const bench = review ? ins.prefs.bench : true;
      const name0 = rd.kind === 'ledger' ? 'Equity sleeve' : 'Current holdings';
      const bName = (rd.p.benchmark || {}).name || 'S&P 500 (SPY)';
      const hid = () => !!state.hidden;
      // Indexed return from the start of the range (the previous close for 1D): both lines start at 0%.
      const pv = d.portfolio.map((v) => (v / 100 - 1) * 100);
      const bv = d.benchmark_series.map((v) => (v / 100 - 1) * 100);
      const values = Array.isArray(d.value) ? d.value : null;
      const series = [{ key: 'p', label: name0, values: pv, cls: 'p', area: true }];
      if (bench) series.push({ key: 'b', label: bName, values: bv, cls: 'b' });
      const last = d.dates.length - 1;
      // The Overview marks the account's own purchases (open lots) on the line; the Review adds
      // QUANTA's dated call changes behind its toggle. An intraday range has no dated markers.
      const marks = rd.intraday ? [] : (rd.p.markers || []).filter((m) => m.date >= d.dates[0] && m.date <= d.dates[last] && (review || m.kind === 'purchase'));
      const marksOn = (i) => marks.filter((m) => d.dates.findIndex((x) => x >= m.date) === i);
      const rowHtml = (cls, label, valHtml) => `<div class="mpc-tr"><span class="mpc-k mpc-k--${cls}" aria-hidden="true"></span><span>${esc(label)}</span><b>${valHtml}</b></div>`;
      let shade = null;
      let xticks = null;
      if (rd.intraday && Array.isArray(d.sessions)) {
        const a = d.sessions.indexOf('regular');
        const b = d.sessions.lastIndexOf('regular');
        shade = [];
        if (a > 0) shade.push({ from: 0, to: a, cls: 'pre', label: 'Pre-market' });
        if (b >= 0 && b < last) shade.push({ from: b, to: last, cls: 'post', label: 'After hours' });
        xticks = ['09:30', '12:00', '16:00'].map((t) => d.dates.findIndex((x) => hhmm(x) >= t)).filter((i) => i >= 0);
        if (a > 0) xticks.unshift(0);
      }
      const sayP = (i) => (hid() ? 'portfolio hidden' : `${name0} ${sp(pv[i])}`);
      return {
        id: `perf-${name}`, dates: d.dates, series, tone: '', zero: true, band: !review && bench, endLabels: !review,
        shade, xticks, showMarkers: review ? ins.prefs.markers : marks.length > 0, markersOnLine: !review, markers: marks,
        fmt: {
          value: (v) => sp(v, 0),
          end: (v, s) => (s.key === 'p' && hid() ? '••' : sp(v, 1)),
          axis: (t) => (Math.abs(t) < 1e-9 ? '0%' : hid() ? '' : sp(t, Math.abs(t) < 10 && t % 1 ? 1 : 0)),
          tick: tickFor(rd.rk), date: dateText, dd: (v) => sp(v * 100, 0),
        },
        drawdown: review && d.drawdown ? { label: 'Drop from peak', values: d.drawdown, bench: bench ? d.benchmark_drawdown : null } : null,
        ariaLabel: `${rd.kind === 'ledger' ? 'Equity sleeve return' : 'Current holdings simulation'} versus ${bName}, ${rd.intraday ? `today from the previous close, ${dateText(d.dates[0])} to ${dateText(d.dates[last])}` : `${dateText(d.dates[0])} to ${dateText(d.dates[last])}`}, percent change. ${hid() ? '' : `Ends at ${sp(pv[last])} against ${sp(bv[last])}. `}Focus the chart, then use the left and right arrow keys to read each ${rd.intraday ? 'bar' : 'day'}.`,
        unitLabel: '',
        tip: (i) => `<b class="mpc-td">${esc(dateText(d.dates[i]))}${rd.intraday && d.sessions && d.sessions[i] !== 'regular' ? ` · ${d.sessions[i] === 'pre' ? 'pre-market' : 'after hours'}` : ''}</b>
          ${rowHtml('p', name0, `${values ? `${money(values[i])} ` : ''}<i>${priv(esc(sp(pv[i])))}</i>`)}
          ${bench ? rowHtml('b', bName, esc(sp(bv[i]))) : ''}
          ${bench ? rowHtml('d', 'Difference', priv(esc(`${signedPts(pv[i] - bv[i], 1)} pts`))) : ''}
          ${review && d.drawdown ? rowHtml('dd', 'Below peak', priv(esc(sp(d.drawdown[i] * 100)))) : ''}
          ${marksOn(i).map((m) => `<div class="mpc-tm">${esc(m.label)}</div>`).join('')}`,
        speak: (i) => `${dateText(d.dates[i])}: ${sayP(i)}${bench ? `, ${bName} ${sp(bv[i])}` : ''}.`,
        table: {
          first: rd.intraday ? 'Time' : 'Date',
          caption: `${name0} versus ${bName}, percent change since ${rd.intraday ? 'the previous close' : dateText(d.dates[0])}`,
          columns: [{ label: name0, num: true }, ...(bench ? [{ label: bName, num: true }, { label: 'Difference', num: true }] : []), ...(values ? [{ label: 'Value', num: true }] : []), ...(review && d.drawdown ? [{ label: 'Below peak', num: true }] : [])],
          cells: (i) => [priv(esc(sp(pv[i]))), ...(bench ? [esc(sp(bv[i])), priv(esc(`${signedPts(pv[i] - bv[i], 1)} pts`))] : []), ...(values ? [money(values[i])] : []), ...(review && d.drawdown ? [priv(esc(sp(d.drawdown[i] * 100)))] : [])],
        },
      };
    }

    function rangeControls(mode) {
      const p = ins.perf.data;
      const sims = (p && p.simulation) || {};
      const cur = rangeFor(mode);
      const list = mode === 'overview' ? RANGES : RANGES.filter((rk) => rk !== '1D');
      return `<div class="mpc-seg" role="group" aria-label="Time range">${list.map((rk) => {
        const ok = rk === '1D' ? !(ins.intra.s === 'ok' && ins.intra.data && !ins.intra.data.available)
          : !p || !p.simulation || (sims[rk] && sims[rk].available) || (p.actual && p.actual.ranges && p.actual.ranges[rk] && p.actual.ranges[rk].available);
        const why = rk === '1D' ? ((ins.intra.data || {}).reason || 'Intraday prices are unavailable') : ((sims[rk] && sims[rk].reason) || 'Not enough history for this range');
        return `<button type="button" class="mpc-segb${cur === rk ? ' is-on' : ''}" data-hp-x="range:${rk}" aria-pressed="${cur === rk}" aria-label="${esc(RANGE_LABEL[rk])}"${ok ? '' : ` disabled title="${esc(why)}"`}>${RANGE_SHORT[rk] || rk}</button>`;
      }).join('')}</div>`;
    }
    const toggle = (key, label, on) => `<button type="button" class="mpc-toggle${on ? ' is-on' : ''}" data-hp-x="toggle:${key}" aria-pressed="${on}"><span class="mpc-toggle-box" aria-hidden="true"></span>${label}</button>`;

    function kindChip(rd) {
      if (!rd) return '';
      if (rd.kind === 'ledger') return '<span class="mp-kind mp-kind--actual">Equity sleeve from order history</span>';
      return '<span class="mp-kind mp-kind--sim" title="Today’s shares valued at past prices. Not what the account earned.">Current holdings simulation<span class="mp-kind-x"> · not account returns</span></span>';
    }
    function kindNote(rd) {
      if (!rd) return '';
      if (rd.kind === 'ledger') {
        return `<p class="mp-kindnote">Time-weighted return of the stocks and funds you held, from your order history. ${esc((rd.p.actual.missing || [])[0] || '')}</p>`;
      }
      return `<p class="mp-kindnote"><strong>Not your account's actual performance.</strong> This replays the shares you hold today over past prices. Purchases and sales made since, cash, dividends, fees and deposits are not reflected; an actual return needs daily account values and cash flows, which Robinhood does not provide here.</p>`;
    }

    // The readout: holdings and the benchmark over the range, and the gap between them in points.
    function heroLine(rd, review) {
      const s = rd.d.stats;
      const ahead = s.excess * 100;
      const cell = (k, v, cls = '') => `<div><dt>${k}</dt><dd class="${cls}">${v}</dd></div>`;
      const bShort = esc(((rd.p.benchmark || {}).name || 'S&P 500 (SPY)').replace(/ \(SPY\)$/, ''));
      return `<dl class="mp-hero">
        ${cell(rd.kind === 'ledger' ? 'Holdings (sleeve)' : 'Holdings', priv(pct(s.portfolio_return, 2, true, true)), dirClass(s.portfolio_return))}
        ${cell(bShort, pct(s.benchmark_return, 2, true, true), dirClass(s.benchmark_return))}
        ${cell('Difference', priv(`${signedPts(ahead, 1)} pts${review ? '' : ` vs ${bShort.replace(/^S&amp;P 500$/, 'S&amp;P')}`}`), dirClass(ahead))}
        ${review ? `${cell('Max drop', priv(pct((s.max_drawdown || {}).depth, 1, true)))}${cell('Volatility', s.volatility != null ? priv(pct(s.volatility, 0)) : NA)}` : ''}
        <div class="mp-hero-r"><dt class="sr-only">Range</dt><dd>${rd.intraday ? 'Today, from the previous close' : esc(RANGE_LABEL[rd.rk]).replace(/^./, (c) => c.toUpperCase())}</dd></div>
      </dl>`;
    }

    function perfCard(pf, mode) {
      const review = mode !== 'overview';
      const rk = rangeFor(mode);
      const key = rk === '1D' ? 'intra' : 'perf';
      const P = ins[key];
      const head = (inner) => `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-perf-${mode}-h">${mode === 'overview' ? 'Holdings vs S&amp;P 500' : 'Performance'}</h2>${inner || ''}</div><div class="q-head-r">${H.tag('broker')}${H.tag('market')}</div></header>`;
      const ctrls = (extra = '') => `<div class="mp-ctrls">${rangeControls(mode)}${extra}</div>`;
      if (P.s === 'idle' || (P.s === 'loading' && !P.data)) {
        return `${head()}<div class="q-body mp-perf-body"><div class="mp-skel mp-skel--chart" role="status" aria-live="polite"><i></i><i></i><i></i><span class="sr-only">Loading price history…</span></div>${review ? '' : ctrls()}</div>`;
      }
      if (P.s === 'missing') {
        return `${head(`<p class="q-head-sub">${esc(H.sessionOf(pf).label)} · % change</p>`)}<div class="q-body mp-perf-body">${sessionCompare(pf)}<p class="hp-fine">The history chart appears after a QUANTA restart.</p></div>`;
      }
      if (P.s === 'error' && !P.data) {
        return `${head()}<div class="q-body mp-perf-body"><p class="mp-unavail" role="status">Performance is unavailable: ${esc(P.error)}. <button type="button" class="q-btn q-btn--link" data-hp-x="retry:${key}">Try again</button></p>${sessionCompare(pf)}${review ? '' : ctrls()}</div>`;
      }
      const rd = rangeData(mode);
      if (!rd || !rd.d || !rd.d.available) {
        const reason = (rd && rd.d && rd.d.reason) || (P.data && P.data.reason) || 'Price history is unavailable.';
        const missing = ((rd && rd.d && rd.d.excluded) || []).map((e) => `${esc(e.symbol)} (${esc(String(e.reason).toLowerCase())})`);
        const sims = (P.data && P.data.simulation) || {};
        const alts = RANGES.filter((k) => k !== '1D' && k !== (rd && rd.rk) && sims[k] && sims[k].available);
        const readAt = P.at ? new Date(P.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
        return `${head()}<div class="q-body mp-perf-body">${ctrls()}<p class="mp-unavail" role="status" data-chart-unavailable>No chart for this range. ${esc(reason)}</p>
          ${missing.length ? `<p class="hp-fine mp-nochart" role="note">No usable prices for: ${missing.join(', ')}.</p>` : ''}
          <p class="hp-fine mp-nochart">${readAt ? `Price history last read at ${esc(readAt)}. ` : ''}It is read again automatically once; <button type="button" class="q-btn q-btn--link" data-hp-x="retry:${key}">reload it now</button>${alts.length ? `, or show ${alts.map((k) => `<button type="button" class="q-btn q-btn--link" data-hp-x="range:${k}">${esc(RANGE_LABEL[k])}</button>`).join(' or ')} instead` : ''}.</p></div>`;
      }
      const excl = (rd.d.excluded || []);
      const cov = rd.d.coverage;
      const limit = rd.kind === 'simulation' && cov != null && cov < 0.9995
        ? `<p class="mp-limit" role="note">Covers ${pct(cov, 0)} of invested value; not charted: ${excl.map((e) => `${esc(e.symbol)} (${esc(e.reason.toLowerCase())})`).join(', ')}.</p>` : '';
      const bName = esc(((rd.p.benchmark || {}).name || 'S&P 500 (SPY)').replace(/ \(SPY\)$/, ''));
      const marks = !rd.intraday && (rd.p.markers || []).some((m) => m.kind === 'purchase' && m.date >= rd.d.dates[0]);
      const about = `<details class="hp-more mp-about" data-hp-disclosure="perf-about-${mode}"><summary>About this chart and its table</summary>${kindNote(rd)}<p class="hp-fine">${esc(rd.d.method || '')} ${esc(rd.d.basis || '')}</p>${Q() ? Q().tableHtml(chartSpecFor(mode), 24) : ''}</details>`;
      if (!review) {
        const key2 = `<p class="mp-axisnote" aria-hidden="true"><span><i class="mpc-k mpc-k--p"></i>Current holdings</span><span><i class="mpc-k mpc-k--b"></i>${bName}</span><span><i class="mpc-k mpc-k--lead"></i>Ahead</span><span><i class="mpc-k mpc-k--lag"></i>Behind</span>${marks ? '<span><i class="mpc-k mpc-k--pin"></i>Your purchases</span>' : ''}${rd.intraday ? '<span><i class="mpc-k mpc-k--ext"></i>Extended hours</span>' : ''}</p>`;
        return `${head(`<p class="q-head-sub">${kindChip(rd)}</p>`)}
          <div class="q-body mp-perf-body">
            ${heroLine(rd, false)}
            <div class="mp-chart" data-mp-chart="overview"></div>
            <div class="mp-ctrls">${rangeControls(mode)}${key2}</div>
            ${limit}
            <div class="mp-perf-foot">${about}<button type="button" class="q-btn q-btn--link hp-inline-link mp-more-link" data-hp-tab-go="review">Attribution and drawdown</button></div>
          </div>`;
      }
      const legend = `<p class="mp-axisnote" aria-hidden="true"><span><i class="mpc-k mpc-k--p"></i>${rd.kind === 'ledger' ? 'Equity sleeve' : 'Current holdings'}</span>${ins.prefs.bench ? `<span><i class="mpc-k mpc-k--b"></i>${bName}</span>` : ''}<span class="mp-axisunit">% change · closing prices, no dividends</span></p>`;
      return `${head(`<p class="q-head-sub">${kindChip(rd)}</p>`)}
        <div class="q-body mp-perf-body">
          ${heroLine(rd, true)}
          ${legend}
          <div class="mp-chart" data-mp-chart="${mode}"></div>
          <div class="mp-ctrls">${rangeControls(mode)}<div class="mp-toggles">${toggle('bench', 'S&amp;P 500 line', ins.prefs.bench)}${(rd.p.markers || []).length ? toggle('markers', 'Purchases &amp; calls', ins.prefs.markers) : ''}</div></div>
          ${limit}
          <div class="mp-perf-foot">${about}</div>
        </div>`;
    }

    slotRenderers['perf-overview'] = (pf) => perfCard(pf, 'overview');
    slotRenderers['perf-review'] = (pf) => perfCard(pf, 'review');

    // ------------------------------------------------------------ exposure
    const expoView = () => {
      const E = ins.expo;
      const pf = state.view && state.view.portfolio;
      const data = E.data && E.data.available ? E.data : (pf && (E.s === 'missing' || E.s === 'error') ? fallbackExposure(pf) : null);
      if (!data) return null;
      return data.views[ins.prefs.expo] || data.views.sector;
    };

    // Ranked rows: name and weight on one line, a thin bar under them. The bar scale is the
    // largest item (or reference), so the leader fills the width; widths stay proportional.
    function expoBars(v, limit) {
      const items = v.items.slice(0, limit);
      const rest = v.items.slice(limit);
      const scale = Math.max(...v.items.map((b) => b.weight || 0), ...v.items.map((b) => b.limit || 0), 0.05);
      const row = (b, i) => {
        const w = b.weight || 0;
        const idx = v.items.indexOf(b);
        const lim = b.limit != null ? ` The ${b.limit_kind === 'target' ? 'target' : 'reference'} is ${pct(b.limit, 0)} (${b.limit_source}).` : '';
        return `<li class="mpx-row${b.over ? ' is-over' : ''}${b.classified ? '' : ' is-unclassified'}"><button type="button" class="mpx-b" data-hp-x="expo:${idx}" aria-label="${esc(`${b.name}: ${pct(w, 1)} of the account, ${b.symbols.length} ${plural(b.symbols.length, 'holding', 'holdings')}.${lim} Show ${plural(b.symbols.length, 'it', 'them')} in Holdings.`)}">
          <span class="mpx-name">${esc(b.name)}${v.kind !== 'holdings' ? `<span class="mpx-n">${b.symbols.length}</span>` : ''}</span>
          <span class="hp-num mpx-w">${pct(w, 1)}</span>
          <span class="mpx-track" aria-hidden="true"><i style="--s:${(w / scale).toFixed(4)};--d:${i * 35}ms"></i>${b.limit != null ? `<b class="${b.limit_kind === 'target' ? 'is-target' : ''}" style="left:${Math.min(100, b.limit / scale * 100).toFixed(2)}%"></b>` : ''}</span></button></li>`;
      };
      const cls = ins.barsAnim ? 'mpx-list' : 'mpx-list is-in';
      return `<ol class="${cls}">${items.map(row).join('')}</ol>${rest.length ? `<details class="hp-more" data-hp-disclosure="expo-more"><summary>${rest.length} more</summary><ol class="${cls}">${rest.map((b, i) => row(b, i + limit)).join('')}</ol></details>` : ''}`;
    }

    function expoSummary(E) {
      const c = E.concentration || {};
      const bits = [];
      if (c.largest) bits.push(`Largest <b class="hp-sym">${esc(c.largest.symbol)}</b> ${pct(c.largest.weight, 1)}`);
      if (c.top5) bits.push(`top five ${pct(c.top5.weight, 1)} of account value`);
      return bits.join(' · ');
    }

    function expoCard(pf) {
      const E = ins.expo;
      const head = (sub, seg = '') => `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-expo-h">Exposure</h2>
          <p class="q-head-sub">${sub}</p></div>${seg}</header>`;
      if (E.s === 'idle' || (E.s === 'loading' && !E.data)) return `${head('Share of the whole account')}<div class="q-body"><div class="mp-skel mp-skel--bars" role="status"><span class="sr-only">Loading exposure…</span></div></div>`;
      const data = (E.s === 'missing' || (E.s === 'error' && !E.data)) ? fallbackExposure(pf) : E.data;
      if (!data || !data.available) return `${head('Share of the whole account')}<div class="q-body"><p class="mp-unavail">${esc((data && data.reason) || 'Exposure is unavailable.')}</p></div>`;
      const v = data.views[ins.prefs.expo] || data.views.sector;
      const seg = `<div class="mpx-seg" role="group" aria-label="Exposure view">${EXPO_VIEWS.filter(([k]) => data.views[k]).map(([k, l]) => `<button type="button" class="mpx-segb${ins.prefs.expo === k ? ' is-on' : ''}" data-hp-x="expo-view:${k}" aria-pressed="${ins.prefs.expo === k}">${l}</button>`).join('')}</div>`;
      const uncl = v.unclassified != null && v.unclassified > 0.0005 && v.kind !== 'holdings'
        ? `<p class="mp-limit" role="note">${pct(v.unclassified, 1)} not classified in this view${v.kind === 'sector' || v.kind === 'geography' ? '; funds are not looked through' : ''}.</p>` : '';
      const srcs = [...new Set(v.items.filter((b) => b.limit != null).map((b) => b.limit_source))];
      const refs = [...new Set(v.items.filter((b) => b.limit != null).map((b) => pct(b.limit, 0)))];
      const refNote = srcs.length
        ? `Tick marks: ${refs.join(' / ')} ${srcs.map((x) => (x === 'QUANTA default' ? 'QUANTA default' : x.toLowerCase())).join(' and ')}, not a universal allocation rule.`
        : 'No limit is implied for this view.';
      const cor = (data.correlated || [])[0];
      const corLine = cor ? ` Moving together: <b class="hp-sym">${esc(cor.a)}</b> + <b class="hp-sym">${esc(cor.b)}</b>, ${pct(cor.combined_weight, 1)} combined (r ${Number(cor.correlation).toFixed(2)}).` : '';
      return `${head(`${expoSummary(data)} · ${H.tag('broker')} / ${H.tag('market')}`, seg)}
        <div class="q-body">
          ${expoBars(v, 9)}
          ${uncl}
          <p class="hp-fine mp-expo-foot">${esc(refNote)} Cash ${data.cash_weight != null ? `(${pct(data.cash_weight, 1)}) ` : ''}is not in any view.${corLine}</p>
          <details class="hp-more" data-hp-disclosure="expo-method"><summary>How this view is built</summary><p class="hp-fine">${esc(v.note || '')} ${esc(data.lookthrough.reason)} ${esc(data.profile_source || '')}</p></details>
        </div>`;
    }
    slotRenderers['expo-overview'] = expoCard;

    // ------------------------------------------------------------ old server
    // A server started before this update answers the page but not the new routes (404).
    // That is one fact, stated once at the top; each card then shows what it can from the
    // data the server does send, instead of repeating an error paragraph.
    function banner() {
      if (!ins.stale) return '<div data-mp-slot="banner"></div>';
      return `<div data-mp-slot="banner"><p class="mp-banner" role="status"><strong>Restart QUANTA to load the charts.</strong> This server was started before the update, so it sends your holdings but not price history, exposure detail or events. Run <code>start.ps1</code> again, then sign in to Robinhood: the connection is kept in memory only.</p></div>`;
    }
    slotRenderers.banner = () => banner().replace(/^<div data-mp-slot="banner">|<\/div>$/g, '');

    // Exposure from the view itself: sectors and holdings are in every server's response.
    function fallbackExposure(pf) {
      const m = pf.metrics || {};
      const total = num((pf.overview || {}).total_value);
      const pol = pf.policy || {};
      const rows = pf.positions || [];
      const sectors = (m.sectors || []).filter((s) => s.sector !== 'Cash').map((s) => ({
        name: s.sector === 'Unclassified' ? 'Sector not reported' : s.sector, weight: num(s.weight), symbols: s.symbols || [],
        classified: s.sector !== 'Unclassified', limit: s.sector !== 'Unclassified' ? num(pol.max_sector) : null,
        limit_source: 'QUANTA default', limit_kind: 'cap',
      })).map((b) => Object.assign(b, { over: b.limit != null && b.weight != null && b.weight >= b.limit }));
      const holds = rows.slice().sort((a, b) => (num(b.broker.weight) || 0) - (num(a.broker.weight) || 0)).map((r) => ({
        name: r.symbol, weight: num(r.broker.weight), symbols: [r.symbol], classified: true,
        limit: num(pol.max_single_position), limit_source: 'QUANTA default', limit_kind: 'cap',
        over: num(pol.max_single_position) != null && num(r.broker.weight) >= num(pol.max_single_position),
      }));
      const c = m.concentration || {};
      const uncl = sectors.filter((b) => !b.classified).reduce((t, b) => t + (b.weight || 0), 0);
      return {
        available: true, fallback: true, cash_weight: num(m.cash_weight),
        views: {
          sector: { kind: 'sector', title: 'Sector', items: sectors, unclassified: uncl || null, note: 'Stocks by reported sector; cash is not a sector.' },
          holdings: { kind: 'holdings', title: 'Holdings', items: holds, unclassified: null, note: 'Each position as a share of the whole account.' },
        },
        concentration: { largest: c.top ? { symbol: c.top.symbol, weight: c.top.weight } : null, top5: c.top5_weight != null ? { weight: c.top5_weight } : null },
        correlated: ((m.volatility || {}).correlated_pairs || []).slice(0, 1).map((p) => ({ ...p, combined_weight: (num((m.weights || {})[p.a]) || 0) + (num((m.weights || {})[p.b]) || 0) })),
        lookthrough: { reason: 'Asset class and geography load after a QUANTA restart.' }, profile_source: '',
        total,
      };
    }

    // Last session against the market, from the view and the index tape: the one comparison
    // that needs no price history. Diverging bars from zero, signed values beside them.
    function sessionCompare(pf) {
      const cmp = H.marketCompare(pf);
      const mv = H.moveParts(pf);
      const ses = H.sessionOf(pf);
      const rows = [];
      if (mv.pct !== null) rows.push({ label: 'Your holdings', v: mv.pct, mine: true });
      if (cmp.status === 'ok') cmp.idx.forEach((q) => rows.push({ label: q.label || q.symbol, v: num(q.change_pct) }));
      if (!rows.length) return '<p class="mp-unavail">No session figures to compare.</p>';
      const max = Math.max(...rows.map((r) => Math.abs(r.v)), 0.05);
      return `<ul class="mp-sess" aria-label="${esc(`${ses.label}: percent change`)}">${rows.map((r) => `<li class="${r.mine ? 'is-mine' : ''}"><span>${esc(r.label)}</span>
          <span class="mp-sess-bar" aria-hidden="true"><i class="${r.v < 0 ? 'is-down' : 'is-up'}" style="--s:${(Math.abs(r.v) / max / 2).toFixed(4)}"></i></span>
          <b class="hp-num ${dirClass(r.v)}">${r.mine ? priv(pct(r.v, 2, false, true)) : pct(r.v, 2, false, true)}</b></li>`).join('')}</ul>
        ${cmp.status !== 'ok' ? `<p class="hp-fine">${cmp.status === 'loading' ? 'Loading index quotes…' : 'Index quotes for the same session are unavailable.'}</p>` : ''}
        ${mv.rows.length ? `<h3 class="hp-h3 mp-sess-h">Moved the account most <span>${mv.rows.every((x) => x.pts !== null) ? 'points of account' : ''}</span></h3>
          <ul class="mp-sess-top">${mv.rows.slice(0, 4).map((x) => `<li><button type="button" class="mpa-row" data-hp-inspect="${esc(x.symbol)}" aria-label="${esc(`${x.symbol} details`)}"><span class="hp-sym">${esc(x.symbol)}</span><span class="hp-num ${dirClass(x.v)}">${H.signedMoney(x.v)}</span><span class="hp-num ${dirClass(x.v)}">${x.pts !== null ? `${signedPts(x.pts)} pts` : ''}</span></button></li>`).join('')}</ul>` : ''}`;
    }

    // A compact line of what is scheduled this week (scheduled facts first, headlines after).
    function weekLine() {
      const E = ins.events;
      if (E.s !== 'ok' || !E.data || !E.data.available) return '';
      const up = (E.data.upcoming || []).filter((i) => i.bucket === 'today' || i.bucket === 'week')
        .sort((a, b) => (a.kind === 'news') - (b.kind === 'news') || String(a.date).localeCompare(String(b.date)));
      const day = (i) => (i.bucket === 'today' ? 'today' : new Date(`${i.date}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' }));
      const shown = up.filter((i) => i.kind !== 'news').slice(0, 3);
      const body = shown.length
        ? shown.map((i) => `<b>${esc(i.title.replace(/ reports earnings$/, ' earnings').replace(/ goes ex-dividend$/, ' ex-dividend').replace(/ \((\w+) data\)$/, ''))}</b> <span class="hp-muted">${esc(day(i))}</span>`).join('<span aria-hidden="true"> · </span>')
        : 'Nothing scheduled for your holdings';
      return `<p class="mp-week"><span class="mp-week-k">This week</span> ${body} <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-tab-go="news">News &amp; Events</button></p>`;
    }
    slotRenderers['events-line'] = () => weekLine();

    // ------------------------------------------------------------ hooks called by my-portfolio.js
    function afterRender(pf) {
      if (!pf) return;
      const base = pf.retrieved_at || '';
      const analysis = ((pf.freshness || {}).analysis || {});
      const withAnalysis = `${base}:${analysis.state}:${analysis.done || 0}`;
      const need = (key, stamp, force) => { if (ins.stamp[key] !== stamp) { ins.stamp[key] = stamp; load(key, true); } else if (ins[key].s === 'idle' || force) load(key); };
      need('perf', base);
      if (range() === '1D') need('intra', base);
      need('expo', withAnalysis);
      need('events', base);
      if (state.tab === 'review') need('outcomes', base);
      if (state.tab === 'activity') need('records', base);
      // A deep link or a re-render lands on a tab that was never clicked: load what it needs.
      if (state.tab !== 'overview' && host.ext.onTab) host.ext.onTab(state.tab);
      // Charts live inside the just-rendered panels.
      const rootEl = document.getElementById('hp-root');
      if (rootEl) mountCharts(rootEl);
    }

    function onTabShown(key) {
      const pf = state.view && state.view.portfolio;
      if (!pf) return;
      if (key === 'review') load('outcomes');
      if (key === 'news') load('events');
      if (host.ext.onTab) host.ext.onTab(key);
    }

    // ------------------------------------------------------------ interactions
    const actions = {
      range(rk) {
        if (!RANGE_LABEL[rk]) return;
        ins.prefs.range = rk; ins.animate = true;
        if (rk === '1D' && ins.intra.s === 'idle') load('intra');
        paint('perf'); H.announce(`Chart range: ${RANGE_LABEL[rk]}.`);
      },
      toggle(k) {
        if (k !== 'bench' && k !== 'markers') return;
        ins.prefs[k] = !ins.prefs[k]; savePrefs();
        paint('perf'); H.announce(`${k === 'bench' ? 'Benchmark' : 'Event markers'} ${ins.prefs[k] ? 'shown' : 'hidden'}.`);
      },
      'expo-view'(k) {
        if (!EXPO_VIEWS.some(([x]) => x === k)) return;
        ins.prefs.expo = k; savePrefs(); ins.barsAnim = true;
        paint('expo'); H.announce(`Exposure by ${EXPO_VIEWS.find(([x]) => x === k)[1].toLowerCase()}.`);
      },
      expo(i) {
        const v = expoView();
        const b = v && v.items[Number(i)];
        if (b) H.filterHoldings(`${b.name}${v.kind === 'holdings' ? '' : ` (${v.title.toLowerCase()})`}`, b.symbols);
      },
      retry(k) { if (LOADERS[k]) { ins[k] = { s: 'idle' }; load(k, true); } },
      'clear-hold'() { H.filterHoldings(null, null); },
    };
    function onClick(e) {
      const t = e.target.closest && e.target.closest('[data-hp-x]');
      if (!t) return false;
      const raw = t.dataset.hpX;
      const cut = raw.indexOf(':');
      const name = cut < 0 ? raw : raw.slice(0, cut);
      const arg = cut < 0 ? '' : raw.slice(cut + 1);
      if (host.ext.actions && host.ext.actions[name]) { host.ext.actions[name](arg, t, e); return true; }
      return false;
    }

    function bindOnce() {
      if (typeof document === 'undefined' || bindOnce.done) return;
      const view = document.getElementById('view-holdings');
      if (!view) return;
      bindOnce.done = true;
      view.addEventListener('click', onClick);
      // Tab switches lazy-load the data that tab needs.
      view.addEventListener('click', (e) => {
        const tb = e.target.closest && (e.target.closest('[data-hp-tab]') || e.target.closest('[data-hp-tab-go]'));
        if (tb) setTimeout(() => onTabShown(state.tab), 0);
      });
      view.addEventListener('keydown', (e) => { if (e.target.closest && e.target.closest('[data-hp-tab]')) setTimeout(() => onTabShown(state.tab), 0); });
    }

    host.ext = Object.assign(host.ext || {}, {
      // Other modules and tests reach actions by name; later modules add their own.
      ins, load, paint, paintSlot, paintAll, mountCharts, slotRenderers, actions: Object.assign({}, actions, (host.ext && host.ext.actions) || {}),
      expoCard, banner, afterRender, reset, onTabShown, bindOnce, dateText, sp, RANGE_LABEL, RANGES, range, reviewRange, rangeData, chartSpecFor, fallbackExposure,
      weekLine, savePrefs, sessionCompare,
      counts(pf) {
        const E = ins.events;
        const c = E.s === 'ok' && E.data && E.data.counts ? E.data.counts : null;
        const out = {};
        if (c) out.news = (c.today || 0) + (c.week || 0);
        if (host.ext.alertCount) out.news = (out.news || 0) + host.ext.alertCount();
        return out;
      },
    });
    bindOnce();
    return host.ext;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaInsights = api;
    if (window.QuantaHoldings && !window.QuantaHoldings.ext) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
