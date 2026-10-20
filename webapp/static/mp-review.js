/* Portfolio Review — "How am I doing?"
 *
 * Every sentence here is assembled from a figure the server published; nothing adds a
 * judgement the data does not carry. Three separations are enforced in the markup:
 *   actual results  vs  a simulation of today's holdings  vs  retrospective what-ifs;
 *   realized  vs  unrealized  vs  dividends, fees and deposits (each with its own status);
 *   trade outcomes (what the account did)  vs  recommendation outcomes (what the model said).
 * A record with too little history says "Insufficient history" and lists what is missing.
 */
(function (root) {
  'use strict';

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const H = host._h;
    const { esc, num, NA, priv, money, signedMoney, pct, signedPts, dirClass, plural } = H;
    const ins = X.ins;
    const state = host._state;

    const asPct = (v, dec = 1) => (v == null ? NA : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(dec)}%`);
    const rk = () => X.RANGE_LABEL[X.reviewRange()];
    // PORTFOLIO.* config (engine/thresholds.py), through app.js thr() or the page's thresholds block.
    const pThr = (key) => {
      if (typeof thr === 'function') return Number(thr(`PORTFOLIO.${key}`));
      const g = typeof globalThis !== 'undefined' ? globalThis : {};
      return Number(((g.__QUANTA_THRESHOLDS__ || {}).PORTFOLIO || {})[key]);
    };

    // ------------------------------------------------------------ attribution
    // Prefer the account's own open lots (actual purchase dates and prices); fall back to the
    // simulation's per-holding contributions, and say which one is on screen.
    function attribution() {
      const p = ins.perf.data;
      if (!p || !p.available) return null;
      const k = X.reviewRange();
      const lots = p.attribution && p.attribution[k];
      if (lots && lots.available) {
        return { kind: 'lots', rows: lots.rows.map((r) => ({ symbol: r.symbol, dollars: r.dollars, points: r.points, ret: r.return_pct })),
          total: lots.total_dollars, totalPts: lots.total_points, note: lots.note, unknown: lots.unknown || [], base: 'of capital in the lots still held' };
      }
      const sim = p.simulation && p.simulation[k];
      if (sim && sim.available) {
        return { kind: 'simulation', rows: sim.contributions.map((r) => ({ symbol: r.symbol, dollars: r.dollars, points: r.points, ret: r.return_pct })),
          total: sim.contributions.reduce((t, r) => t + r.dollars, 0), totalPts: sim.contribution_total_points,
          note: 'Simulation: today’s shares over past prices. Not the account’s own history.', unknown: (sim.excluded || []).map((e) => ({ symbol: e.symbol, reason: e.reason })), base: 'of the simulated start value' };
      }
      return null;
    }

    function attribSlot() {
      const A = attribution();
      const head = `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-at-h">What moved the results</h2>
        <p class="q-head-sub">${A ? (A.kind === 'lots' ? 'Open lots since purchase or the start of the range, in dollars and points' : 'Simulation contributions in dollars and points') : 'Not available'} · ${esc(rk())}</p></div></header>`;
      if (ins.perf.s === 'loading' && !ins.perf.data) return `${head}<div class="q-body"><div class="mp-skel mp-skel--bars"></div></div>`;
      if (!A) return `${head}<div class="q-body"><p class="mp-unavail">Attribution needs price history${ins.perf.error ? `: ${esc(ins.perf.error)}` : ''}.</p></div>`;
      const max = Math.max(...A.rows.map((r) => Math.abs(r.points || 0)), 0.01);
      const li = (r) => `<li><button type="button" class="mpa-row" data-hp-inspect="${esc(r.symbol)}" aria-label="${esc(`${r.symbol}: ${signedPts(r.points, 2)} points, details`)}">
        <span class="hp-sym">${esc(r.symbol)}</span>
        <span class="mpa-bar" aria-hidden="true"><i class="${r.dollars < 0 ? 'is-down' : 'is-up'}" style="--s:${(Math.abs(r.points || 0) / max).toFixed(3)}"></i></span>
        <span class="hp-num ${dirClass(r.dollars)}">${signedMoney(r.dollars)}</span>
        <span class="hp-num ${dirClass(r.dollars)}">${r.points == null ? NA : `${signedPts(r.points, 2)} pts`}</span></button></li>`;
      const helped = A.rows.filter((r) => r.dollars > 0).sort((a, b) => b.dollars - a.dollars).slice(0, 4);
      const hurt = A.rows.filter((r) => r.dollars < 0).sort((a, b) => a.dollars - b.dollars).slice(0, 4);
      // Only the four largest on each side are listed; the rest are summed so the lines reconcile.
      const listed = new Set([...helped, ...hurt].map((r) => r.symbol));
      const others = A.rows.filter((r) => !listed.has(r.symbol));
      const listedSum = [...helped, ...hurt].reduce((t, r) => t + r.dollars, 0);
      const otherSum = others.reduce((t, r) => t + r.dollars, 0);
      const otherPts = others.reduce((t, r) => t + (r.points || 0), 0);
      const otherLine = others.length
        ? `the ${listed.size} holdings listed add to ${signedMoney(listedSum)}; ${others.length} other ${plural(others.length, 'holding adds', 'holdings add')} ${signedMoney(otherSum)} (${signedPts(otherPts, 2)} pts); all together ${signedMoney(A.total)} (${signedPts(A.totalPts, 2)} pts).`
        : `together the holdings shown add to ${signedMoney(A.total)} (${signedPts(A.totalPts, 2)} pts).`;
      const unknown = A.unknown.length ? `<p class="mp-limit" role="note">Not attributed: ${A.unknown.map((u) => `${esc(u.symbol)} (${esc(String(u.reason).toLowerCase())})`).join(', ')}.</p>` : '';
      return `${head}<div class="q-body">
        <div class="mp-grid2">
          <div><h3 class="hp-h3">Helped <span>${signedMoney(helped.reduce((t, r) => t + r.dollars, 0))}</span></h3>${helped.length ? `<ul class="mpa-list">${helped.map(li).join('')}</ul>` : '<p class="hp-empty">No holding added to the result in this range.</p>'}</div>
          <div><h3 class="hp-h3">Hurt <span>${signedMoney(hurt.reduce((t, r) => t + r.dollars, 0))}</span></h3>${hurt.length ? `<ul class="mpa-list">${hurt.map(li).join('')}</ul>` : '<p class="hp-empty">No holding subtracted from the result in this range.</p>'}</div>
        </div>
        <p class="hp-fine" data-attrib-total>Points are a share ${esc(A.base)}; ${otherLine} ${esc(A.note)}</p>${unknown}
        ${retroHtml()}
      </div>`;
    }
    X.slotRenderers.attrib = attribSlot;

    // ------------------------------------------------------------ results by source + what-ifs
    function resultsSlot(pf) {
      const o = pf.overview || {};
      const u = o.unrealized_pnl || {};
      const row = (k, v, src, status, cls = '') => `<tr><th scope="row">${k}</th><td class="num ${cls}">${v}</td><td>${src}</td><td><span class="mp-status mp-status--${status === 'Reported' ? 'ok' : 'na'}">${status}</span></td></tr>`;
      const noBasis = (pf.positions || []).filter((r) => r.broker.cost_basis == null).map((r) => r.symbol);
      const table = `<table class="mp-table"><caption class="sr-only">Results by source</caption>
        <thead><tr><th scope="col">Result</th><th scope="col" class="num">Amount</th><th scope="col">Where it comes from</th><th scope="col">Status</th></tr></thead><tbody>
        ${row('Realized', o.realized_pnl == null ? NA : signedMoney(o.realized_pnl, 2), `Robinhood realized P&amp;L${o.realized_pnl_period ? `, ${esc(o.realized_pnl_period)}` : ''}`, o.realized_pnl == null ? 'Not reported' : 'Reported', dirClass(o.realized_pnl))}
        ${row('Unrealized', u.total == null ? NA : signedMoney(u.total, 2), `Robinhood cost basis and live quotes${noBasis.length ? `; no cost basis for ${esc(noBasis.join(', '))}` : ''}`, u.total == null ? 'Not reported' : 'Reported', dirClass(u.total))}
        ${row('Dividends', NA, 'Not part of the data QUANTA reads from Robinhood', 'Not reported')}
        ${row('Fees', NA, 'Not part of the data QUANTA reads from Robinhood', 'Not reported')}
        ${row('Deposits and withdrawals', NA, 'Not part of the data QUANTA reads from Robinhood', 'Not reported')}
        </tbody></table>`;
      return `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-rs-h">Results by source</h2><p class="q-head-sub">Kept separate on purpose</p></div></header>
        <div class="q-body">${table}</div>`;
    }
    X.slotRenderers.results = resultsSlot;

    // Retrospective what-ifs, shown under attribution and labelled as not your results.
    function retroHtml() {
      const p = ins.perf.data;
      const sim = p && p.simulation && p.simulation[X.reviewRange()];
      let whatif = '';
      if (sim && sim.available) {
        const s = sim.stats;
        whatif = `<h3 class="hp-h3 mp-retro-h">Retrospective scenarios <span>not your results · ${esc(rk())}</span></h3>
          <table class="mp-table"><caption class="sr-only">Retrospective scenarios over the same period</caption><thead><tr><th scope="col">Scenario</th><th scope="col" class="num">Return</th><th scope="col">Rule and assumptions</th></tr></thead><tbody>
            <tr><th scope="row">Current holdings replayed</th><td class="num">${priv(asPct(s.portfolio_return))}</td><td>Today’s shares at past prices.</td></tr>
            <tr><th scope="row">Same names, equal-weighted</th><td class="num">${s.equal_weight_return == null ? NA : priv(asPct(s.equal_weight_return))}</td><td>Equal money in each name on day one, never rebalanced. Names are today’s holdings, so survivorship flatters it.</td></tr>
            <tr><th scope="row">${esc(p.benchmark.name)}</th><td class="num">${asPct(s.benchmark_return)}</td><td>Price return of the benchmark over the same dates.</td></tr>
          </tbody></table>
          <p class="hp-fine">All three ignore fees, taxes and dividends and use only information available on the first day. They show what a simple rule would have done, not what you should have done.</p>`;
      }
      return whatif;
    }



    // ------------------------------------------------------------ assessment
    function assessmentSlot(pf) {
      const p = ins.perf.data;
      const rd = X.rangeData('review');
      const o = pf.overview || {};
      const lines = [];
      if (rd && rd.d && rd.d.available) {
        const s = rd.d.stats;
        const ahead = s.excess * 100;
        const who = rd.kind === 'ledger' ? 'your equity holdings' : 'the stocks and funds you hold today';
        lines.push(['Return', `Over ${esc(rk())}, ${who} ${s.portfolio_return >= 0 ? 'gained' : 'lost'} ${priv(pct(Math.abs(s.portfolio_return), 1))} against ${asPct(s.benchmark_return)} for ${esc(p.benchmark.name)}${Math.abs(ahead) < 0.05 ? ', in line with it' : `, ${priv(`${Math.abs(ahead).toFixed(1)} pts`)} ${ahead > 0 ? 'ahead of' : 'behind'} it`}. ${rd.kind === 'ledger' ? 'Cash, dividends and deposits are outside that figure.' : '<strong>This replays today’s shares over past prices, so it describes the current book, not what the account earned.</strong>'}`]);
        const dd = (s.max_drawdown || {}).depth;
        const bd = (s.benchmark_max_drawdown || {}).depth;
        if (dd != null) lines.push(['Drawdown', `The deepest fall was ${priv(pct(dd, 1))}${s.max_drawdown.peak_date ? ` (${esc(X.dateText(s.max_drawdown.peak_date))} to ${esc(X.dateText(s.max_drawdown.trough_date))})` : ''}${bd != null ? `, against ${pct(bd, 1)} for the benchmark` : ''}${s.volatility != null ? `; annualized volatility ${priv(pct(s.volatility, 0))}${s.benchmark_volatility != null ? ` against ${pct(s.benchmark_volatility, 0)}` : ''}` : ''}.`]);
        const A = attribution();
        if (A && A.rows.length) {
          const rows = A.rows.slice().sort((a, b) => b.dollars - a.dollars);
          const top = rows[0];
          const bot = rows[rows.length - 1];
          const parts = [];
          if (top.dollars > 0) parts.push(`<span class="hp-sym">${esc(top.symbol)}</span> added the most (${signedMoney(top.dollars)}, ${signedPts(top.points, 1)} pts)`);
          if (bot.dollars < 0) parts.push(`<span class="hp-sym">${esc(bot.symbol)}</span> took the most away (${signedMoney(bot.dollars)}, ${signedPts(bot.points, 1)} pts)`);
          if (parts.length) lines.push(['Drivers', `${parts.join('; ')}${A.kind === 'lots' ? ', counting only lots you still hold' : ' in the simulation'}.`]);
        }
        const drift = (rd.d.weight_drift || [])[0];
        if (drift && Math.abs(drift.end - drift.start) >= 0.03) lines.push(['Drift', `Weights drifted most in <span class="hp-sym">${esc(drift.symbol)}</span>: ${pct(drift.start, 0)} of the covered book at the start of the range, ${pct(drift.end, 0)} now.`]);
      } else if (ins.perf.s === 'loading' || ins.perf.s === 'idle') {
        lines.push(['Return', 'Loading price history…']);
      } else {
        lines.push(['Return', `Performance cannot be assessed right now: ${esc((rd && rd.d && rd.d.reason) || (p && p.reason) || ins.perf.error || 'price history is unavailable')}.`]);
      }
      const u = o.unrealized_pnl || {};
      if (u.total != null) lines.push(['On cost', `Across holdings with a cost basis you are ${u.total >= 0 ? 'up' : 'down'} ${signedMoney(Math.abs(u.total)).replace('+', '')} on cost${o.realized_pnl != null ? `, and Robinhood reports ${signedMoney(o.realized_pnl)} realized${o.realized_pnl_period ? ` (${esc(o.realized_pnl_period)})` : ''}` : ''}.`]);
      return `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-as-h">How am I doing?</h2>
        <p class="q-head-sub">A plain reading of the figures below, ${rd && rd.kind === 'ledger' ? 'from your order history' : 'with the data limits stated beside them'}</p></div></header>
        <div class="q-body"><dl class="mp-read">${lines.map(([k, l]) => `<div><dt>${k}</dt><dd>${l}</dd></div>`).join('')}</dl></div>`;
    }
    X.slotRenderers['review-body'] = assessmentSlot;

    // ------------------------------------------------------------ outcome rates
    function rateCard(o, kind) {
      const titles = { trades: 'Your closed trades', recommendations: 'QUANTA’s recorded calls' };
      const lead = { trades: 'How often a completed trade made money', recommendations: 'How often a dated call beat the benchmark 63 trading days later' };
      const def = o.definition || {};
      const defs = ['unit', 'success', 'horizon', 'denominator', 'open_positions', 'not'].filter((k) => def[k]).map((k) => `<li><strong>${{ unit: 'Counted', success: 'Success', horizon: 'Horizon', denominator: 'Denominator', open_positions: 'Open positions', not: 'Not' }[k]}:</strong> ${esc(def[k])}</li>`).join('');
      const head = `<h3 class="hp-h3">${titles[kind]} <span>${lead[kind]}</span></h3>`;
      if (o.status !== 'ok') {
        const n = o.n || 0;
        return `<div class="mp-rate">${head}
          <div class="mp-insuff" role="status"><strong>Insufficient history</strong>
            <div class="mp-meter" role="img" aria-label="${n} of ${o.minimum} outcomes needed"><i style="--s:${Math.min(1, n / o.minimum).toFixed(3)}"></i></div>
            <span class="hp-num">${n} of ${o.minimum} outcomes</span></div>
          <ul class="mp-missing">${(o.missing || []).map((m) => `<li>${esc(m)}</li>`).join('')}</ul>
          <details class="hp-more" data-hp-disclosure="rate-${kind}"><summary>How this is defined</summary><ul class="mp-defs">${defs}</ul></details></div>`;
      }
      const lo = o.interval ? o.interval[0] : null;
      const hi = o.interval ? o.interval[1] : null;
      const Ch = root.QuantaCharts;
      const gauge = Ch ? Ch.gaugeHtml({ rate: o.rate, lo, hi, text: `${Math.round(o.rate * 100)}%`, sub: `${o.successes} of ${o.n}`,
        ariaLabel: `${titles[kind]}: ${Math.round(o.rate * 100)} percent, ${o.successes} of ${o.n}. 95 percent interval ${Math.round(lo * 100)} to ${Math.round(hi * 100)} percent. A record of past outcomes, not a forecast.` }) : '';
      const pay = kind === 'trades'
        ? `<div><dt>Average win</dt><dd>${o.average_win == null ? NA : signedMoney(o.average_win)}</dd></div><div><dt>Average loss</dt><dd>${o.average_loss == null ? NA : signedMoney(o.average_loss)}</dd></div>
           <div><dt>Payoff ratio</dt><dd>${o.payoff_ratio == null ? NA : `${o.payoff_ratio.toFixed(2)}×`}</dd></div><div><dt>Expectancy per trade</dt><dd>${o.expectancy == null ? NA : signedMoney(o.expectancy)}</dd></div>`
        : `<div><dt>Average win</dt><dd>${o.average_win == null ? NA : `${asPct(o.average_win)} vs benchmark`}</dd></div><div><dt>Average loss</dt><dd>${o.average_loss == null ? NA : `${asPct(o.average_loss)} vs benchmark`}</dd></div>
           <div><dt>Expectancy per call</dt><dd>${o.expectancy == null ? NA : `${asPct(o.expectancy)} vs benchmark`}</dd></div><div><dt>Still pending</dt><dd>${o.pending || 0}</dd></div>`;
      return `<div class="mp-rate">${head}
        <div class="mp-rate-body">${gauge}
          <dl class="mp-payoff">${pay}<div><dt>Sample</dt><dd>${o.n} ${kind === 'trades' ? 'trades' : 'calls'}</dd></div>
          <div><dt>95% interval</dt><dd>${Math.round(lo * 100)}–${Math.round(hi * 100)}%</dd></div></dl></div>
        <p class="hp-fine">A record of past outcomes. It is not the probability that the next ${kind === 'trades' ? 'trade' : 'call'} works${kind === 'recommendations' ? '; “win” means beating the S&amp;P 500 on price, and a rate above 50% with wins smaller than losses can still lose money' : ''}.</p>
        <details class="hp-more" data-hp-disclosure="rate-${kind}"><summary>How this is defined</summary><ul class="mp-defs">${defs}</ul></details></div>`;
    }

    function outcomesSlot() {
      const O = ins.outcomes;
      const head = `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-oc-h">Historical outcome rate</h2>
        <p class="q-head-sub">Two records, never combined: what the account did, and what the model said</p></div></header>`;
      if (O.s === 'idle' || (O.s === 'loading' && !O.data)) return `${head}<div class="q-body"><div class="mp-skel mp-skel--bars"></div></div>`;
      if (O.s === 'error' && !O.data) return `${head}<div class="q-body"><p class="mp-unavail">Outcome records are unavailable: ${esc(O.error)}. <button type="button" class="q-btn q-btn--link" data-hp-x="retry:outcomes">Try again</button></p></div>`;
      if (O.s === 'missing') return `${head}<div class="q-body"><p class="mp-unavail">Available after a QUANTA restart.</p></div>`;
      const d = O.data;
      return `${head}<div class="q-body"><div class="mp-grid2 mp-rates">${rateCard(d.trades, 'trades')}${rateCard(d.recommendations, 'recommendations')}</div>
        <p class="hp-fine">${esc(d.separation)}</p></div>`;
    }
    X.slotRenderers.outcomes = outcomesSlot;

    // ------------------------------------------------------------ lessons
    function lessons(pf) {
      const helped = [];
      const hurt = [];
      const improve = [];
      const o = pf.overview || {};
      const pol = pf.policy || {};
      const rows = pf.positions || [];
      const A = attribution();
      const src = A ? (A.kind === 'lots' ? 'open lots' : 'simulation') : '';
      if (A) {
        const sorted = A.rows.slice().sort((a, b) => b.dollars - a.dollars);
        sorted.filter((r) => r.dollars > 0).slice(0, 2).forEach((r) => helped.push({ text: `<span class="hp-sym">${esc(r.symbol)}</span> added ${signedMoney(r.dollars)} (${signedPts(r.points, 1)} pts) over ${esc(rk())}.`, ev: src }));
        sorted.filter((r) => r.dollars < 0).slice(-2).reverse().forEach((r) => hurt.push({ text: `<span class="hp-sym">${esc(r.symbol)}</span> took away ${signedMoney(Math.abs(r.dollars)).replace('+', '')} (${signedPts(r.points, 1)} pts) over ${esc(rk())}.`, ev: src }));
        const gross = A.rows.reduce((t, r) => t + Math.abs(r.dollars), 0);
        const big = A.rows.slice().sort((a, b) => Math.abs(b.dollars) - Math.abs(a.dollars))[0];
        const w = big && num((rows.find((r) => r.symbol === big.symbol) || { broker: {} }).broker.weight);
        if (big && gross > 0 && Math.abs(big.dollars) / gross >= 0.4 && rows.length > 2) {
          (big.dollars >= 0 ? helped : hurt).push({ text: `One holding, <span class="hp-sym">${esc(big.symbol)}</span>${w != null ? ` (${pct(w, 0)} of the account)` : ''}, accounts for ${pct(Math.abs(big.dollars) / gross, 0)} of all the movement in this range. Results this dependent on one name are less diversified than the holding count suggests.`, ev: src });
        }
      }
      if (o.realized_pnl != null && o.realized_pnl > 0) helped.push({ text: `Robinhood reports ${signedMoney(o.realized_pnl)} realized${o.realized_pnl_period ? ` (${esc(o.realized_pnl_period)})` : ''}.`, ev: 'broker' });
      if (o.realized_pnl != null && o.realized_pnl < 0) hurt.push({ text: `Robinhood reports ${signedMoney(o.realized_pnl)} realized${o.realized_pnl_period ? ` (${esc(o.realized_pnl_period)})` : ''}.`, ev: 'broker' });
      const rd = X.rangeData('review');
      if (rd && rd.d && rd.d.available) {
        const s = rd.d.stats;
        const dd = (s.max_drawdown || {}).depth;
        const bd = (s.benchmark_max_drawdown || {}).depth;
        if (dd != null && bd != null && dd < bd - pThr('DRAWDOWN_COMPARE_MARGIN')) hurt.push({ text: `The deepest fall, ${priv(pct(dd, 1))}, was deeper than the benchmark’s ${pct(bd, 1)}: the book was more volatile than the market over this range.`, ev: 'simulation' });
        if (dd != null && bd != null && dd > bd + pThr('DRAWDOWN_COMPARE_MARGIN')) helped.push({ text: `The deepest fall, ${priv(pct(dd, 1))}, was shallower than the benchmark’s ${pct(bd, 1)}.`, ev: 'simulation' });
      }
      const changes = ((pf.journal || {}).changes || []).filter((c) => (Date.now() - new Date(`${c.date}T12:00:00`).getTime()) / 864e5 <= pThr('JOURNAL_RECENT_DAYS'));
      changes.slice(0, 2).forEach((c) => hurt.push({ text: `<span class="hp-sym">${esc(c.symbol)}</span>: QUANTA’s call moved from ${esc(c.from_action || 'a prior call')} to ${esc(c.action)} on ${esc(X.dateText(c.date))} (${esc(c.basis_text || 'rule not recorded')}).`, ev: 'journal' }));

      const posLimit = num(pol.max_single_position);
      const secLimit = num(pol.max_sector);
      rows.filter((r) => posLimit != null && num(r.broker.weight) != null && r.broker.weight >= posLimit).slice().sort((a, b) => b.broker.weight - a.broker.weight).slice(0, 2)
        .forEach((r) => improve.push({ text: `<span class="hp-sym">${esc(r.symbol)}</span> is ${pct(r.broker.weight, 1)} of the account, above the ${pct(posLimit, 0)} single-name reference. The trim decision and its size preview are in Holdings.`, go: 'holdings', label: 'See the decision' }));
      ((pf.metrics || {}).sectors || []).filter((s) => H.limitedSector(s.sector) && secLimit != null && num(s.weight) != null && s.weight >= secLimit).slice(0, 1)
        .forEach((s) => improve.push({ text: `${esc(s.sector)} is ${pct(s.weight, 1)} of the account against a ${pct(secLimit, 0)} reference. New money into it is on hold; diversifying additions would go elsewhere.`, go: 'holdings', label: 'See the holdings' }));
      const noBasis = rows.filter((r) => r.broker.cost_basis == null).map((r) => r.symbol);
      if (noBasis.length) improve.push({ text: `No cost basis is reported for ${esc(noBasis.join(', '))}, so its gain or loss is missing from every total. Check the position in Robinhood.`, ev: 'broker' });
      const noCall = rows.filter((r) => r.no_call);
      if (noCall.length) {
        const causes = [...new Set(noCall.map((r) => r.no_call.code))];
        const wait = noCall.every((r) => r.no_call.remedy === 'wait');
        improve.push({ text: `${noCall.length} ${plural(noCall.length, 'holding has', 'holdings have')} no call (${esc(noCall.slice(0, 3).map((r) => r.symbol).join(', '))}${noCall.length > 3 ? '…' : ''}): ${esc(noCall[0].no_call.text)} ${causes.length > 1 ? 'Causes differ; see Data details.' : ''} ${wait ? 'This clears on its own.' : 'Refresh may clear it; reconnecting does not affect analysis.'}` });
      }
      const soon = rows.filter((r) => num(r.broker.weight) >= 0.1 && (r.market || {}).earnings_date).map((r) => ({ r, days: Math.round((new Date(`${r.market.earnings_date}T12:00:00`) - Date.now()) / 864e5) })).filter((x) => x.days >= 0 && x.days <= 7);
      soon.slice(0, 1).forEach(({ r, days }) => improve.push({ text: `<span class="hp-sym">${esc(r.symbol)}</span> (${pct(r.broker.weight, 0)} of the account) reports in ${days <= 0 ? 'less than a day' : `${days} ${plural(days, 'day', 'days')}`}. Decide the size you are comfortable holding through it before the date.`, go: 'news', label: 'See the event' }));
      const j = pf.journal || {};
      const need = pThr('OUTCOME_MIN_SAMPLE');
      const horizon = pThr('OUTCOME_HORIZON_DAYS');
      if (j.enabled && need > 0 && (j.entries || 0) < need) improve.push({ text: `The decision journal has ${j.entries || 0} ${plural(j.entries || 0, 'entry', 'entries')}${j.started ? ` since ${esc(X.dateText(j.started))}` : ' and starts with your next refresh'}. Outcome rates need ${need} completed calls, each measured ${horizon} trading days after it; keep using it.` });
      return { helped, hurt, improve, assessed: !!(rd && rd.d && rd.d.available) };
    }

    function lessonsSlot(pf) {
      const L = lessons(pf);
      const col = (title, items, empty, cls) => `<div class="mp-lesson mp-lesson--${cls}"><h3 class="hp-h3">${title}</h3>${items.length ? `<ul>${items.map((i) => `<li>${i.text}${i.go ? ` <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-tab-go="${i.go}">${esc(i.label || 'Open')}</button>` : ''}${i.ev ? `<span class="mp-ev">${esc(i.ev)}</span>` : ''}</li>`).join('')}</ul>` : `<p class="hp-empty">${empty}</p>`}</div>`;
      return `<header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-ls-h">Helped, hurt, and what to improve</h2><p class="q-head-sub">Each line is tied to a record. Nothing here uses hindsight about when to buy or sell.</p></div></header>
        <div class="q-body"><div class="mp-grid3">${col('What helped', L.helped, L.assessed ? 'Nothing in the available records clearly helped in this range.' : 'Unable to assess: price history for this range is not available.', 'ok')}${col('What hurt', L.hurt, L.assessed ? 'Nothing in the available records clearly hurt in this range.' : 'Unable to assess: price history for this range is not available.', 'bad')}${col('What to improve', L.improve, 'No issue in the records calls for action.', 'act')}</div></div>`;
    }
    X.slotRenderers['review-lessons'] = lessonsSlot;

    // The records slot (the trade ledger) lives in mp-activity.js.

    // ------------------------------------------------------------ journal
    function journalBlock(pf) {
      const j = pf.journal || {};
      if (!j.enabled) return '';
      const ch = (j.changes || []).slice(0, 6);
      return `<section class="q-panel hp-panel mp-journal" aria-labelledby="mp-jn-h"><header class="q-head mp-head"><div class="q-head-l"><h2 class="q-head-title" id="mp-jn-h">Decision journal</h2>
        <p class="q-head-sub">${j.started ? `Recording since ${esc(X.dateText(j.started))} · ${j.entries} ${plural(j.entries, 'entry', 'entries')} for ${j.symbols} ${plural(j.symbols, 'holding', 'holdings')}` : 'Starts with your next refresh'}</p></div></header>
        <div class="q-body"><p class="hp-fine">Each dated call QUANTA shows is saved here so that, months from now, its outcome can be measured against what was known that day. Nothing is backfilled. ${esc(j.stored)}</p>
        ${ch.length ? `<h3 class="hp-h3">Call changes <span>thesis tracking</span></h3><ul class="hp-rows">${ch.map((c) => `<li><span><span class="hp-sym">${esc(c.symbol)}</span> ${esc(c.from_action || '')} → ${esc(c.action)}</span><span class="hp-muted">${esc(X.dateText(c.date))} · ${esc(c.basis_text || '')}</span></li>`).join('')}</ul>` : '<p class="hp-empty">No call has changed since the journal began.</p>'}</div></section>`;
    }

    // ------------------------------------------------------------ export
    const plain = (html) => {
      const raw = String(html == null ? '' : html);
      if (typeof document !== 'undefined') {
        const d = document.createElement('div');
        d.innerHTML = raw;
        d.querySelectorAll('.sr-only').forEach((n) => n.remove());
        return d.textContent.replace(/\s+/g, ' ').trim();
      }
      return raw.replace(/<span class="sr-only">.*?<\/span>/g, '').replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
    };
    function exportMarkdown(pf) {
      const o = pf.overview || {};
      const rd = X.rangeData('review');
      const L = lessons(pf);
      const A = attribution();
      const out = [];
      out.push(`# My Portfolio review`, '', `Prepared ${new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })} from QUANTA. Research, not investment advice.${state.hidden ? ' Amounts are hidden in this export.' : ''}`, '');
      out.push('## Snapshot', `- Value: ${plain(money(o.total_value, 2))}`, `- Cash: ${plain(money(o.cash, 2))}`, `- Unrealized P&L: ${plain(signedMoney((o.unrealized_pnl || {}).total, 2))}`, `- Realized P&L${o.realized_pnl_period ? ` (${o.realized_pnl_period})` : ''}: ${o.realized_pnl == null ? 'not reported' : plain(signedMoney(o.realized_pnl, 2))}`, '');
      out.push('## Performance');
      if (rd && rd.d && rd.d.available) {
        const s = rd.d.stats;
        out.push(`**${rd.kind === 'ledger' ? 'Equity sleeve return (order ledger)' : 'Current holdings simulation, not the account’s actual return'}**, ${rk()}: ${plain(priv(asPct(s.portfolio_return)))} vs ${rd.p.benchmark.name} ${asPct(s.benchmark_return)}. Max drop ${plain(priv(asPct((s.max_drawdown || {}).depth)))}.`, `Method: ${rd.d.method}`, `Basis: ${rd.d.basis}`, '');
      } else out.push('Not available: price history could not be loaded.', '');
      if (A) { out.push(`## Contributors (${A.kind === 'lots' ? 'open lots' : 'simulation'})`); A.rows.slice(0, 6).forEach((r) => out.push(`- ${r.symbol}: ${plain(signedMoney(r.dollars))} (${signedPts(r.points, 2)} pts)`)); out.push('', A.note, ''); }
      const ex = ins.expo.data;
      if (ex && ex.available) { out.push('## Exposure (share of account)'); ex.views.sector.items.slice(0, 5).forEach((b) => out.push(`- ${b.name}: ${pct(b.weight, 1)}${b.limit != null ? ` (${b.limit_source} ${pct(b.limit, 0)})` : ''}`)); out.push(''); }
      out.push('## What helped', ...(L.helped.length ? L.helped.map((i) => `- ${plain(i.text)}`) : ['- Nothing clearly identified.']), '', '## What hurt', ...(L.hurt.length ? L.hurt.map((i) => `- ${plain(i.text)}`) : ['- Nothing clearly identified.']), '', '## What to improve', ...(L.improve.length ? L.improve.map((i) => `- ${plain(i.text)}`) : ['- No action indicated.']), '');
      const oc = ins.outcomes.data;
      if (oc) {
        out.push('## Historical outcome records');
        [['Closed trades', oc.trades], ['QUANTA calls', oc.recommendations]].forEach(([n, r]) => out.push(r.status === 'ok' ? `- ${n}: ${Math.round(r.rate * 100)}% (${r.successes} of ${r.n}; 95% interval ${Math.round(r.interval[0] * 100)}–${Math.round(r.interval[1] * 100)}%). A past record, not a forecast.` : `- ${n}: Insufficient history (${r.n} of ${r.minimum}).`));
        out.push('');
      }
      const ev = ins.events.data;
      if (ev && ev.available) { out.push('## Coming up'); (ev.upcoming || []).filter((i) => i.bucket !== 'later').slice(0, 8).forEach((i) => out.push(`- ${i.date}: ${i.title} (${i.status})`)); out.push(''); }
      out.push('## Data limits', '- Dividends, fees and deposits are not part of the data QUANTA reads.', '- A simulation replays current holdings over past prices and is not account performance.', '');
      return out.join('\n');
    }

    // ------------------------------------------------------------ tab
    function review(pf, legacy) {
      const sec = (name, inner, extra = '') => `<section class="q-panel hp-panel${extra}" data-mp-slot="${name}">${inner}</section>`;
      return `<div class="mp-grid2 mp-split mp-split--top">${sec('review-body', assessmentSlot(pf), ' mp-assess')}${sec('results', resultsSlot(pf))}</div>
        ${sec('perf-review', X.slotRenderers['perf-review'](pf), ' mp-perf')}
        <div class="mp-grid2 mp-split">${sec('attrib', attribSlot(pf))}${sec('outcomes', outcomesSlot(pf))}</div>
        ${sec('review-lessons', lessonsSlot(pf))}
        <div class="mp-legacy">${(legacy && legacy.contribution) || ''}${(legacy && legacy.risk) || ''}</div>
        <section class="q-panel hp-panel mp-expo" data-mp-slot="expo-overview" aria-labelledby="mp-expo-h">${X.expoCard ? X.expoCard(pf) : ''}</section>
        ${journalBlock(pf)}
        <section class="q-panel hp-panel mp-export" aria-labelledby="mp-ex-h"><div class="q-body mp-export-row"><div><h2 class="q-head-title" id="mp-ex-h">Take this review with you</h2><p class="hp-fine">A short Markdown summary of this tab: the same figures, definitions and limits, ${state.hidden ? 'with amounts hidden as they are on screen' : 'amounts included'}.</p></div>
          <div class="mp-export-actions"><button type="button" class="q-btn" data-hp-x="export:copy">Copy summary</button><button type="button" class="q-btn" data-hp-x="export:download">Download .md</button></div></div></section>`;
    }

    X.actions['rec-sym'] = () => {};
    X.actions.export = (mode) => {
      const pf = state.view && state.view.portfolio;
      if (!pf) return;
      const md = exportMarkdown(pf);
      if (mode === 'copy') {
        const done = () => H.announce('Review summary copied.');
        if (root.navigator && root.navigator.clipboard) root.navigator.clipboard.writeText(md).then(done, () => H.announce('Copy was blocked; use Download instead.'));
        else H.announce('Copy is not available in this browser; use Download.');
      } else {
        const blob = new Blob([md], { type: 'text/markdown' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `portfolio-review-${new Date().toISOString().slice(0, 10)}.md`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        H.announce('Review summary downloaded.');
      }
    };

    Object.assign(X, { review, lessons, attribution, exportMarkdown, rateCard, plain });
    return X;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaReview = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
