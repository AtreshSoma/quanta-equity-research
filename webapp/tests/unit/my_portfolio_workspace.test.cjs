// My Portfolio workspace (mp-*.js): Overview, Guidance, Portfolio Review, Activity, News & Events.
// Rendered against fixtures built by the REAL server code over synthetic data:
//   my_portfolio_view.json      (build_view)          make_my_portfolio_fixture.py
//   my_portfolio_insights.json  (portfolio.insights)  make_insights_fixture.py
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const read = (f) => JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', f), 'utf8'));
const view = read('my_portfolio_view.json');
const I = read('my_portfolio_insights.json');
view.portfolio.journal = I.journal;

const hp = require('../../static/my-portfolio.js');
globalThis.QuantaCharts = require('../../static/mp-charts.js');
require('../../static/mp-insights.js').attach(hp);
require('../../static/mp-review.js').attach(hp);
require('../../static/mp-activity.js').attach(hp);
const news = require('../../static/mp-news.js');
news.attach(hp);
require('../../static/mp-overview.js').attach(hp);
require('../../static/mp-guidance.js').attach(hp);
const X = hp.ext;
const ins = X.ins;

const clone = (o) => JSON.parse(JSON.stringify(o));
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
// Visible words in reading order, one space between text nodes (markup puts none).
const words = (el) => {
  const doc = el.ownerDocument || el;
  const walk = doc.createTreeWalker(el.body || el, 4);
  const out = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = n.textContent.replace(/\s+/g, ' ').trim(); if (t) out.push(t); }
  return out.join(' ');
};
const pf = view.portfolio;

test('Overview and Review accept zoned realized bucket bounds without changing their render', () => {
  loaded();
  const before = ['overview', 'review'].map((tab) => panel(render(tab), tab).innerHTML);
  for (const slot of Object.values(ins.act)) {
    slot.data.buckets = slot.data.buckets.map((b, i) => ({ ...b,
      start: i % 2 ? '2026-07-01T04:00:00Z' : '2026-01-01T00:00:00-05:00',
      end: i % 2 ? '2026-07-02T04:00:00Z' : '2026-01-02T00:00:00-05:00' }));
  }
  assert.deepEqual(['overview', 'review'].map((tab) => panel(render(tab), tab).innerHTML), before);
});

function loaded() {
  ins.perf = { s: 'ok', data: clone(I.performance) };
  ins.intra = { s: 'ok', data: clone(I.intraday) };
  ins.expo = { s: 'ok', data: clone(I.exposure) };
  ins.events = { s: 'ok', data: clone(I.events) };
  ins.outcomes = { s: 'ok', data: clone(I.outcomes) };
  ins.records = { s: 'ok', data: clone(I.records) };
  ins.act = { month: { s: 'ok', data: clone(I.activity.month) }, ytd: { s: 'ok', data: clone(I.activity.ytd) } };
  Object.assign(X.activityState, { ready: false, symbols: [], period: null, age: null, gran: null, sort: null, today: null });
  ins.stale = false;
  Object.assign(ins.prefs, { range: '6M', bench: true, markers: false, expo: 'sector', newsBucket: 'soon', newsKind: 'all', actPeriod: 'month' });
  ins.watch = { rules: [], dismissed: {}, snoozed: {} };
  hp._state.hidden = false;
  hp._state.holdFilter = null;
  Object.assign(ins.ov, { sort: 'weight', dir: null, pin: null });
}
const render = (tab) => { hp._state.tab = tab; return frag(hp.portfolioHtml(view)); };
const panel = (d, tab) => d.getElementById(`hp-panel-${tab}`);
const titles = (el) => [...el.querySelectorAll('.q-head-title')].map(words);

test('Overview: KPI strip, performance beside allocation, holdings beside what drove return, then the action queue with catalysts beside movers', () => {
  loaded();
  const ov = panel(render('overview'), 'overview');
  assert.deepEqual(titles(ov), ['Portfolio Overview', 'Holdings vs S&P 500']);
  assert.deepEqual([...ov.querySelectorAll('.mpo-h h2')].map(words), ['Allocation', 'Holdings 8', 'Unrealized gains by holding', 'Action queue 3', 'Movers Latest session · Fri, Sep 25']);
  // Catalysts: earnings, ex-dividend dates and filings for held names, nearest first.
  const cat = ov.querySelector('.mpo-queue [data-mp-slot="events-line"]');
  assert.match(words(cat.querySelector('.mpo-cat-h')), /^Catalysts next 30 days$/);
  const items = [...cat.querySelectorAll('.mpo-cat')];
  const held = new Set(pf.positions.map((r) => r.symbol));
  assert.ok(items.every((li) => held.has(li.dataset.sym)), 'only names you hold');
  const days = items.map((li) => words(li.querySelector('.mpo-cat-d span')));
  assert.ok(days.every((t) => /^(Today|Tomorrow|In \d+ days)$/.test(t)));
  assert.deepEqual([...ov.querySelectorAll('.hp-kpis .hp-kpi dt')].map(words), ['Total value', 'Return on cost', 'Realized P&L', 'Cash', 'Risk']);
  // The session's move sits under the value, with an arrow and a sign (not colour alone).
  const today = ov.querySelector('.hp-kpi--lead .hp-today');
  assert.ok(today.classList.contains('bull') && today.querySelector('svg.hp-tri'));
  assert.match(words(today), /^\+\$330\.60 \+0\.37% last session$/);
  // The 30-day line under the value is today's shares over the past month, and says so.
  assert.ok(ov.querySelector('.hp-kpi--lead svg.mps-area'));
  assert.match(ov.querySelector('.hp-kpi--lead .mpk-g').getAttribute('title'), /a simulation, not account history/);
  // Return on cost: market value against cost basis as a bullet; cash has no target to draw against.
  assert.match(ov.querySelector('.mpk-bullet').getAttribute('aria-label'), /^Market value \$\d[\d,]* against cost basis \$\d[\d,]*$/);
  assert.equal(words(ov.querySelectorAll('.hp-kpi')[3].querySelector('.mpk-empty')), 'No target cash range set');
  // Risk is QUANTA's audited rule: a zoned gauge at its points, and every driver as a tag.
  const risk = ov.querySelectorAll('.hp-kpi')[4];
  assert.match(words(risk), /^Risk High 6 of 9 points Largest position 35% Technology 60% Beta 1\.30 Volatility 22%$/);
  assert.equal(risk.querySelectorAll('.mprg-z').length, 4);
  assert.ok(risk.querySelector('.mprg-z--high.is-on'));
  assert.match(risk.querySelector('.mprg').getAttribute('aria-label'), /6 of 9 points/);
  const tags = new Set([...ov.querySelectorAll('.hp-src')].map((e) => e.textContent));
  for (const t of ['Account data', 'Market data']) assert.ok(tags.has(t), t);
  assert.match(words(ov.querySelector('.mp-week')), /^This week KO ex-dividend Wed · Employment Situation Fri News & Events$/);
});

test('movers: best and worst three by session change, each opening its holding', () => {
  loaded();
  const m = frag(X.slotRenderers['movers-overview'](pf));
  const cols = [...m.querySelectorAll('.mpo-mv-col')].map((c) => [...c.querySelectorAll('.mpo-mv')].map((b) => b.dataset.hpInspect));
  assert.deepEqual(cols, [['NVDA', 'AAPL', 'JNJ'], ['RIVN', 'XOM', 'MSFT']]);
  assert.match(m.querySelector('[data-hp-inspect="RIVN"]').getAttribute('aria-label'), /^RIVN -2\.48% last session\. Details\.$/);
  assert.ok(m.querySelector('.mpo-mv svg.mps-spark'), 'the past month beside each');
  assert.equal(m.querySelector('.mpo-mk'), null, 'no market comparison without same-session index quotes');
});

test('allocation donut: separate views and a treemap, colour follows the sector, cash is its own slice, a slice pins its holdings', () => {
  loaded();
  const a = frag(X.slotRenderers['alloc-overview'](pf));
  assert.deepEqual([...a.querySelectorAll('.mpo-seg button')].map(words), ['Sector', 'Asset', 'Region', 'Treemap']);
  const legend = [...a.querySelectorAll('.mpd-row')].map(words);
  assert.deepEqual(legend, ['Technology Over 30% 59.7%', 'Energy 8.5%', 'Consumer Defensive 8.4%', 'Consumer Cyclical 3.9%', 'Healthcare 2.7%', 'Sector not reported 6.2%', 'Cash 10.7%']);
  // Slices are shares of the whole account: they add to 100%.
  const total = X.ins.allocSegs.reduce((t, x) => t + x.value, 0);
  assert.ok(Math.abs(total - 1) < 1e-9);
  assert.equal(a.querySelectorAll('.mpd-seg').length, legend.length);
  // Over the sector limit: an amber ring from the limit to the slice's end, and a tick at the limit.
  assert.ok(a.querySelector('.mpd-over[data-seg="Technology"]') && a.querySelector('.mpd-lim'));
  assert.equal(a.querySelectorAll('.mpd-over').length, 1);
  assert.ok(a.querySelector('[data-seg="Technology"]').classList.contains('mpd-s-1'));
  assert.ok(a.querySelector('[data-seg="Healthcare"]').classList.contains('mpd-s-3'));
  assert.ok(a.querySelector('[data-seg="cash"]').classList.contains('mpd-s-cash'));
  assert.equal(a.querySelector('[data-seg-key="cash"]').tagName, 'SPAN', 'cash has no holdings to show');
  assert.match(a.querySelector('[data-hp-x="alloc-pin:Technology"]').getAttribute('aria-label'), /over the 30% QUANTA default\. Show 3 holdings\./);
  // Pinned: every holding in the group, largest first, with a way into Holdings.
  X.ins.ov.pin = 'Technology';
  const p = frag(X.slotRenderers['alloc-overview'](pf));
  assert.deepEqual([...p.querySelectorAll('.mpo-pin tbody tr')].map((r) => r.dataset.sym), ['NVDA', 'AAPL', 'MSFT']);
  assert.match(words(p.querySelector('.mpo-pin tbody tr')), /^NVDA 35\.0% \$31,734 \+188\.1% Trim$/);
  X.ins.ov.pin = null;
  X.actions.alloc('Technology');
  assert.deepEqual(hp._state.holdFilter, { label: 'Technology', symbols: ['NVDA', 'AAPL', 'MSFT'] });
  hp._state.holdFilter = null;
  X.actions['alloc-view']('asset_class');
  assert.deepEqual([...frag(X.slotRenderers['alloc-overview'](pf)).querySelectorAll('.mpd-row')].map(words), ['Individual stocks 89.3%', 'Cash 10.7%']);
  // Treemap: one tile per holding sized by weight, coloured by the session, plus cash.
  X.actions['alloc-view']('treemap');
  const t = frag(X.slotRenderers['alloc-overview'](pf));
  const tiles = [...t.querySelectorAll('button.mpt-tile')];
  assert.equal(tiles.length, pf.positions.length);
  assert.ok(t.querySelector('.mpt-tile.is-cash'));
  const area = (el) => parseFloat(el.style.width) * parseFloat(el.style.height);
  const nv = tiles.find((x) => x.dataset.sym === 'NVDA');
  const jnj = tiles.find((x) => x.dataset.sym === 'JNJ');
  const w = (s) => pf.positions.find((r) => r.symbol === s).broker.weight;
  assert.ok(Math.abs(area(nv) / area(jnj) - w('NVDA') / w('JNJ')) < 0.05, 'area is proportional to weight');
  assert.ok(nv.classList.contains('is-up') && tiles.find((x) => x.dataset.sym === 'RIVN').classList.contains('is-down'));
  X.ins.prefs.alloc = 'sector';
});

test('holdings: every holding, sortable, weight, session, return on cost, 30-day path and a coloured call', () => {
  loaded();
  const h = frag(X.slotRenderers['hold-overview'](pf));
  const rows = [...h.querySelectorAll('tbody tr')];
  assert.deepEqual(rows.map((r) => r.dataset.mpoRow), ['NVDA', 'AAPL', 'MSFT', 'XOM', 'KO', 'BRK.B', 'RIVN', 'JNJ']);
  assert.match(words(rows[0]), /^NVDA NVIDIA 35\.0% \+1\.26% \+188\.1% \+7\.3% Trim$/);
  assert.ok(rows[0].querySelector('.mpo-bar i.is-over'), 'over the single-name default');
  assert.ok(rows[0].querySelector('svg.mps-spark'));
  assert.ok(rows[0].querySelector('.mpo-call--trim'));
  assert.match(words(rows.find((r) => r.dataset.mpoRow === 'BRK.B')), /-- \+2\.2% No call$/);
  assert.deepEqual([...h.querySelectorAll('thead th')].map((th) => words(th).replace(/[▲▼]/g, '').trim()), ['Symbol', 'Weight', 'Session', 'Return on cost', '30 days', 'Call']);
  assert.ok(h.querySelector('thead th[aria-sort="descending"] [data-hp-x="ovsort:weight"]'));
  // A header click sorts; a second click reverses. Missing values sort last either way.
  X.actions.ovsort('ret');
  let order = [...frag(X.slotRenderers['hold-overview'](pf)).querySelectorAll('tbody tr')].map((r) => r.dataset.mpoRow);
  assert.deepEqual(order.slice(0, 2), ['NVDA', 'AAPL']);
  assert.equal(order[order.length - 1], 'BRK.B');
  X.actions.ovsort('ret');
  order = [...frag(X.slotRenderers['hold-overview'](pf)).querySelectorAll('tbody tr')].map((r) => r.dataset.mpoRow);
  assert.equal(order[0], 'RIVN');
  assert.equal(order[order.length - 1], 'BRK.B');
  Object.assign(X.ins.ov, { sort: 'weight', dir: null });
});

test('concentration bar and what drove return add back to the published figures', () => {
  loaded();
  const h = frag(X.slotRenderers['hold-overview'](pf));
  const cb = h.querySelector('.mpo-cb');
  assert.match(words(cb), /^Top five 76\.6% NVDA 35% AAPL 15% MSFT 10% XOM 8% KO 8% Other 13% Cash 11% 10% limit 20% review$/);
  const widths = [...cb.querySelectorAll('.mpo-cb-s')].map((s) => parseFloat(s.style.flexBasis));
  assert.ok(Math.abs(widths.reduce((t, x) => t + x, 0) - 100) < 0.05, 'the bar is the whole account');
  assert.ok(cb.querySelector('.mpo-cb-s.is-0.is-over'), 'the largest holding is over the single-name limit');
  assert.deepEqual([...cb.querySelectorAll('em')].map((e) => parseFloat(e.style.left)), [10, 20]);
  const c = frag(X.slotRenderers['contrib-overview'](pf));
  const rows = [...c.querySelectorAll('.mpo-crl li')].map(words);
  assert.deepEqual(rows, ['NVDA +$20,718', 'AAPL +$5,178', 'KO +$1,188', 'MSFT +$590', 'JNJ +$198', 'XOM -$588', 'RIVN -$1,410', 'Total +$25,874']);
  const sum = pf.positions.reduce((t, r) => t + (r.broker.unrealized_pnl || 0), 0);
  assert.ok(Math.abs(sum - pf.overview.unrealized_pnl.total) < 0.01, 'the bars add to the total');
  assert.match(words(c.querySelector('.mpo-foot')), /^No cost basis: BRK\.B\.$/);
});

test('the action queue: the three most urgent decisions, the impact of acting, and a way to all of them', () => {
  loaded();
  const q = panel(render('overview'), 'overview').querySelector('.mpo-queue');
  const items = [...q.querySelectorAll('.mpo-q')].map(words);
  assert.deepEqual(items, [
    "High Review whether to keep RIVN QUANTA score 29 is below 35, with a measured red flag: QUANTA's 12-month valuation target is below the price 3.9% Review",
    'Medium Trim NVDA 35.0% of the account, past the 20% review level 35.0% → 10.0% Review',
    'Medium Trim XOM QUANTA score 41 is in the reduce band (35 to 45) 8.5% Review']);
  assert.equal(words(q.querySelector('[data-hp-tab-go="guidance"]')), 'See all 3');
  // The same reason Guidance gives.
  const g = frag(hp.guidanceHtml(pf));
  assert.equal(g.querySelector('.hp-hero-why').textContent, '35.0% of the account, past the 20% review level');
});

test('guidance what-if: only the server-defined scenarios, previewed with the server’s own risk rule', () => {
  loaded();
  assert.deepEqual(X.whatifOptions(pf).map((x) => `${x.r.symbol}:${x.opts.map((o) => o.k)}`), ['RIVN:keep,all', 'NVDA:keep,trim,all', 'XOM:keep,all']);
  const ui = frag(X.whatifPanel(pf));
  assert.deepEqual([...ui.querySelectorAll('[data-hp-x^="wi:NVDA:"]')].map(words), ['Keep', 'To 10%', 'Sell all']);
  assert.match(words(ui.querySelector('.hp-fine')), /QUANTA sizes a trim only above the 10% single-name limit; for the other 2, selling in full is the outer bound/);
  const nv = pf.positions.find((r) => r.symbol === 'NVDA').guidance.impact.trim_to_cap.delta_value;
  const key = X.whatifKey([{ symbol: 'NVDA', delta_value: nv }]);
  assert.ok(I.whatif[key], 'the fixture recorded this combination');
  Object.assign(ins.wi, { res: { [key]: clone(I.whatif[key]) }, key, s: 'ok', choice: { NVDA: 'trim' } });
  const prev = frag(X.slotRenderers['gd-wi-prev'](pf));
  assert.match(words(prev.body), /High → to Moderate Cash \$9,650 \(10\.7%\) → to \$32,326 \(35\.7%\) Top position NVDA 35\.0% → to AAPL 15\.1% Technology 59\.7% → to 34\.7%/);
  assert.match(words(prev.body), /Frees \$22,676 to cash/);
  // The hero's effect on risk is the same computation.
  assert.match(words(frag(X.slotRenderers['gd-hero-risk'](pf)).body), /^Risk High → to Moderate$/);
  hp._state.hidden = true;
  try { assert.doesNotMatch(words(frag(X.slotRenderers['gd-wi-prev'](pf)).body), /\$\d/); } finally { hp._state.hidden = false; }
  ins.wi = { choice: {}, res: {}, s: 'idle', key: '', stamp: null, hero: null };
});
test('a simulation is labelled as one, everywhere, and never as account performance', () => {
  loaded();
  const d = render('overview');
  const perf = d.querySelector('.mp-perf');
  assert.equal(words(perf.querySelector('.mp-kind')), 'Current holdings simulation · not account returns');
  assert.match(words(perf.querySelector('.mp-kindnote')), /^Not your account's actual performance\. This replays the shares you hold today/);
  assert.match(words(perf.querySelector('.mp-hero')), /^Holdings \+14\.34% S&P 500 \+6\.77% Difference \+7\.6 pts/);
  assert.equal(perf.querySelector('.mp-kind--actual'), null);
  const spec = X.chartSpecFor('overview');
  assert.match(spec.ariaLabel, /^Current holdings simulation versus S&P 500 \(SPY\)/);
  assert.match(spec.table.caption, /^Current holdings versus S&P 500/);
  assert.match(X.exportMarkdown(pf), /Current holdings simulation, not the account’s actual return/);
  // Only a reconciled order ledger may be called actual; then the label changes with it.
  const ledger = clone(I.performance);
  ledger.actual = { available: true, kind: 'ledger', missing: ['Cash balances are not available.'], ranges: { '6M': clone(I.performance.simulation['6M']) } };
  ins.perf = { s: 'ok', data: ledger };
  const a = frag(hp.portfolioHtml(view)).querySelector('.mp-perf');
  assert.equal(words(a.querySelector('.mp-kind')), 'Equity sleeve from order history');
  assert.match(words(a.querySelector('.mp-hero')), /^Holdings \(sleeve\)/);
  assert.doesNotMatch(words(a), /Current holdings simulation/);
});

test('the chart has ranges, a benchmark toggle, a keyboard-readable plot and a table alternative', () => {
  loaded();
  ins.prefs.range = '1M';
  const d = render('review');
  const perf = d.querySelector('#hp-panel-review .mp-perf');
  const ranges = [...perf.querySelectorAll('[data-hp-x^="range:"]')];
  // The Review works on daily closes: every range but 1D.
  assert.deepEqual(ranges.map(words), ['1W', '1M', '3M', 'YTD', '1Y', 'All']);
  assert.equal(perf.querySelector('[data-hp-x="range:1M"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual([...d.querySelectorAll('#hp-panel-overview [data-hp-x^="range:"]')].map(words), ['1D', '1W', '1M', '3M', 'YTD', '1Y', 'All']);
  ins.prefs.range = '6M';
  assert.equal(perf.querySelector('[data-hp-x="toggle:bench"]').getAttribute('aria-pressed'), 'true');
  assert.equal(words(perf.querySelector('[data-hp-x="toggle:bench"]')), 'S&P 500 line');
  const spec = X.chartSpecFor('review');
  assert.equal(spec.series.length, 2);
  for (const s of spec.series) assert.equal(s.values.length, spec.dates.length);
  const chart = frag(globalThis.QuantaCharts.render(spec));
  const area = chart.querySelector('.mpc-area');
  assert.equal(area.getAttribute('tabindex'), '0');
  assert.match(area.getAttribute('aria-label'), /use the left and right arrow keys/);
  assert.ok(chart.querySelector('.mpc-live[aria-live="polite"]'));
  assert.ok(chart.querySelector('.mpc-dd'), 'drawdown panel under the main chart');
  assert.equal(spec.zero, true, 'the Review chart is a percent return from zero');
  const tip = words(frag(spec.tip(10)).body);
  assert.match(tip, /^\w{3} \d+, 2026 Current holdings \$[\d,]+ [+−]\d+\.\d% S&P 500 \(SPY\) [+−]\d+\.\d% Difference [+−]\d+\.\d pts Below peak/);
  // The Overview plots indexed return from the start of the range: both lines start at 0%,
  // the gap between them is shaded, and each ends with its label. Blue is the portfolio always.
  const ov = X.chartSpecFor('overview');
  assert.equal(ov.zero, true);
  assert.equal(ov.series[0].values[0], 0);
  assert.equal(ov.series[1].values[0], 0);
  assert.ok(ov.band && ov.endLabels);
  assert.equal(ov.tone, '');
  const drawn = frag(globalThis.QuantaCharts.render(ov));
  assert.ok(drawn.querySelector('.mpc-band'));
  assert.deepEqual([...drawn.querySelectorAll('.mpc-endl')].map((e) => e.className.split(' ')[1]).sort(), ['mpc-endl--b', 'mpc-endl--p']);
  hp._state.hidden = true;
  assert.equal(frag(globalThis.QuantaCharts.render(X.chartSpecFor('overview'))).querySelector('.mpc-endl--p').textContent, '••', 'hidden amounts hide the portfolio return');
  hp._state.hidden = false;
  // 1D: today's 5-minute bars from the previous close, with pre-market and after hours shaded.
  ins.prefs.range = '1D';
  const day = X.chartSpecFor('overview');
  assert.equal(day.dates.length, I.intraday.dates.length);
  assert.deepEqual(day.shade.map((x) => x.label), ['Pre-market', 'After hours']);
  assert.match(frag(day.tip(100)).body.textContent.replace(/\s+/g, ' '), /^ ?Sep 29, \d+:\d\d [AP]M ET/);
  assert.match(words(frag(X.slotRenderers['perf-overview'](pf)).body), /Today, from the previous close/);
  ins.prefs.range = '6M';
  const table = frag(globalThis.QuantaCharts.tableHtml(spec, 24));
  assert.equal(table.querySelectorAll('tbody tr').length, 24);
  assert.deepEqual([...table.querySelectorAll('thead th')].map(words), ['Date', 'Current holdings', 'S&P 500 (SPY)', 'Difference', 'Value', 'Below peak']);
  // Benchmark off: one series, no difference column.
  ins.prefs.bench = false;
  assert.equal(X.chartSpecFor('review').series.length, 1);
});

test('holdings without history for the range are excluded and named, never held flat', () => {
  loaded();
  ins.prefs.range = 'MAX';
  const perf = render('overview').querySelector('.mp-perf');
  assert.match(words(perf.querySelector('.mp-limit')), /^Covers 96% of invested value; not charted: RIVN \(no price on the first day of this range\)\.$/);
  const sim = I.performance.simulation.MAX;
  assert.ok(!sim.contributions.some((c) => c.symbol === 'RIVN'));
});

test('exposure views are separate classifications; cash is never a sector; limits say whose they are', () => {
  loaded();
  const ex = render('review').querySelector('.mp-expo');
  assert.deepEqual([...ex.querySelectorAll('[data-hp-x^="expo-view:"]')].map(words), ['Sector', 'Asset class', 'Geography', 'Holdings']);
  const names = [...ex.querySelectorAll('.mpx-name')].map((n) => n.firstChild.textContent);
  assert.ok(!names.includes('Cash'));
  assert.equal(names[0], 'Technology');
  assert.ok(ex.querySelector('.mpx-row.is-over'), 'a sector over its reference is marked');
  const foot = words(ex.querySelector('.mp-expo-foot'));
  assert.match(foot, /^Tick marks: 30% QUANTA default, not a universal allocation rule\. Cash \(10\.7%\) is not in any view\./);
  assert.match(foot, /Moving together: AAPL \+ MSFT , 24\.7% combined \(r 0\.98\)/);
  assert.match(words(ex.querySelector('.mp-limit')), /^6\.2% not classified in this view; funds are not looked through\.$/);
  assert.match(words(ex.querySelector('.q-head-sub')), /^Largest NVDA 35\.0% · top five 76\.6% of account value/);
  // Bars are shares of the whole account: sector items plus cash add back to 100%.
  const v = I.exposure.views.sector;
  const total = v.items.reduce((t, b) => t + b.weight, 0) + I.exposure.cash_weight;
  assert.ok(Math.abs(total - 1) < 1e-9);
  // Asset class and geography carry no limit at all.
  ins.prefs.expo = 'asset_class';
  const ac = frag(X.slotRenderers['expo-overview'](pf));
  assert.equal(ac.querySelectorAll('.mpx-track b').length, 0);
  assert.match(words(ac), /No limit is implied for this view\./);
});

test('an exposure bar opens Holdings filtered to the holdings behind it', () => {
  loaded();
  X.actions.expo('0');
  assert.deepEqual(hp._state.holdFilter, { label: 'Technology (sector)', symbols: ['NVDA', 'AAPL', 'MSFT'] });
  const d = frag(hp.tableHtml(pf));
  assert.equal(d.querySelectorAll('tbody tr').length, 3);
  assert.match(words(d.querySelector('.hp-holdchip')), /Showing Technology \(sector\) 3 Show all/);
  hp._state.holdFilter = null;
});

test('Portfolio Review reads in order and keeps actual, simulated and retrospective results apart', () => {
  loaded();
  const rv = panel(render('review'), 'review');
  assert.deepEqual(titles(rv), ['How am I doing?', 'Results by source', 'Performance', 'What moved the results', 'Historical outcome rate',
    'Helped, hurt, and what to improve', 'Contribution', 'Risk exposure', 'Exposure', 'Decision journal', 'Take this review with you']);
  assert.deepEqual([...rv.querySelectorAll('.mp-read dt')].map(words), ['Return', 'Drawdown', 'Drivers', 'Drift', 'On cost']);
  const read = words(rv.querySelector('.mp-read dd'));
  assert.match(read, /^Over 6 months, the stocks and funds you hold today gained 14\.3% against \+6\.8% for S&P 500 \(SPY\), 7\.6 pts ahead of it\./);
  assert.match(read, /This replays today’s shares over past prices, so it describes the current book, not what the account earned\./);
  const src = [...rv.querySelectorAll('.mp-table')][0];
  const rows = [...src.querySelectorAll('tbody tr')].map((r) => [words(r.querySelector('th')), words(r.lastElementChild)]);
  assert.deepEqual(rows, [['Realized', 'Reported'], ['Unrealized', 'Reported'], ['Dividends', 'Not reported'], ['Fees', 'Not reported'], ['Deposits and withdrawals', 'Not reported']]);
  assert.match(words(rv.querySelector('.mp-retro-h')), /Retrospective scenarios not your results/);
});

test('attribution uses the lots actually held, in dollars and points, and names what it cannot attribute', () => {
  loaded();
  const at = frag(X.slotRenderers.attrib(pf));
  assert.match(words(at.querySelector('.q-head-sub')), /^Open lots since purchase or the start of the range/);
  const helped = [...at.querySelectorAll('.mp-grid2 > div')[0].querySelectorAll('.mpa-row')].map(words);
  assert.match(helped[0], /^NVDA \+\$9,354 \+19\.76 pts$/);
  assert.match(words(at.querySelector('.mp-limit')), /Not attributed: MSFT \(no tax lots\), KO \(no tax lots\), BRK\.B \(no tax lots\)\./);
  // The points shown add to the stated total.
  const rowsA = I.performance.attribution['6M'].rows;
  assert.ok(Math.abs(rowsA.reduce((t, r) => t + r.points, 0) - I.performance.attribution['6M'].total_points) < 1e-9);
});

test('outcome rates: two separate records, "Insufficient history" with what is missing', () => {
  loaded();
  const oc = frag(X.slotRenderers.outcomes(pf));
  const cards = [...oc.querySelectorAll('.mp-rate')];
  assert.equal(cards.length, 2);
  assert.match(words(cards[0].querySelector('h3')), /^Your closed trades/);
  assert.match(words(cards[1].querySelector('h3')), /^QUANTA’s recorded calls/);
  for (const c of cards) {
    assert.match(words(c.querySelector('.mp-insuff')), /^Insufficient history 0 of 20 outcomes$/);
    assert.equal(c.querySelector('.mpg'), null, 'no gauge without a sample');
  }
  assert.match(words(cards[1].querySelector('.mp-missing')), /3 still pending; the journal began 2026-09-15/);
  assert.match(words(oc), /never combined/);
});

test('with enough completed outcomes, the gauge pairs the rate with its sample, interval and payoff', () => {
  loaded();
  const trades = { status: 'ok', kind: 'trades', n: 25, successes: 15, minimum: 20, rate: 0.6, interval: [0.407, 0.766], average_win: 120, average_loss: -80,
    payoff_ratio: 1.5, expectancy: 40, definition: { success: 'Realized gain greater than zero', not: 'Not a probability' } };
  const html = X.rateCard(trades, 'trades');
  const d = frag(html);
  assert.match(d.querySelector('.mpg').getAttribute('aria-label'), /60 percent, 15 of 25\. 95 percent interval 41 to 77 percent\. A record of past outcomes, not a forecast\./);
  assert.match(words(d.querySelector('.mp-payoff')), /Average win \+\$120 Average loss -\$80 Payoff ratio 1\.50× Expectancy per trade \+\$40 Sample 25 trades 95% interval 41–77%/);
  assert.match(words(d), /It is not the probability that the next trade works/);
});

test('lessons are tied to records; the no-call line says reconnecting does not fix analysis', () => {
  loaded();
  const L = X.lessons(pf);
  assert.match(X.plain(L.helped[0].text), /^NVDA added \+\$9,354 \(\+19\.8 pts\) over 6 months\.$/);
  assert.ok(L.hurt.some((i) => /XOM: QUANTA’s call moved from Hold to Trim/.test(X.plain(i.text))));
  const improve = L.improve.map((i) => X.plain(i.text));
  assert.ok(improve.some((t) => /^NVDA is 35\.0% of the account, above the 10% single-name reference/.test(t)));
  assert.ok(improve.some((t) => /no call \(BRK\.B\).*reconnecting does not affect analysis/.test(t)));
  assert.ok(L.improve.some((i) => i.go === 'holdings'));
  assert.ok(!L.improve.some((i) => i.go === 'plan'), 'no link to a tab that no longer exists');
});

test('the export repeats the same figures and respects hidden amounts', () => {
  loaded();
  const md = X.exportMarkdown(pf);
  assert.match(md, /- NVDA: \+\$9,354 \(\+19\.76 pts\)/);
  assert.match(md, /QUANTA calls: Insufficient history \(0 of 20\)/);
  assert.match(md, /Dividends, fees and deposits are not part of the data QUANTA reads/);
  hp._state.hidden = true;
  try {
    const hidden = X.exportMarkdown(pf);
    assert.doesNotMatch(hidden, /\$\d/);
    assert.match(hidden, /Amounts are hidden in this export/);
  } finally { hp._state.hidden = false; }
});

test('News & Events: today and this week, each item says what, why, exposure and what to watch, with its source and status', () => {
  loaded();
  const nw = panel(render('news'), 'news');
  assert.deepEqual([...nw.querySelectorAll('.mpn-buckets .mpc-segb')].map(words), ['Today & this week 4', 'Later, to 31 days 5', 'Recent filings & news 3']);
  assert.deepEqual([...nw.querySelectorAll('.mpn-group')].map(words), ['Today 2', 'This week 2']);
  const items = [...nw.querySelectorAll('.mpn-item')];
  for (const it of items) {
    assert.deepEqual([...it.querySelectorAll('.mpn-4 dt')].map(words), ['Why it matters', 'Exposure', 'What to watch']);
    assert.ok(it.querySelector('time[datetime]'));
    assert.match(words(it.querySelector('.mpn-src')), /^Source: /);
  }
  const jobs = items.find((it) => /Employment Situation/.test(words(it)));
  assert.equal(words(jobs.querySelector('.mpn-status')), 'Confirmed');
  assert.equal(jobs.querySelector('.mpn-src a').getAttribute('href'), 'https://www.bls.gov/schedule/news_release/empsit.htm');
  assert.match(words(jobs.querySelector('.mpn-time')), /Oct 2, 2026, 08:30 ET/);
  const nvda = items.find((it) => /NVIDIA/.test(words(it)));
  assert.equal(words(nvda.querySelector('.mpn-status')), 'Reported');
  assert.match(words(nvda.querySelector('.mpn-src')), /QUANTA has not verified it and does not say it moved a price/);
  // Two outlets, one story.
  assert.equal(items.filter((it) => /NVIDIA Corp expands data/.test(words(it))).length, 1);
  // Later: a tentative date is an estimate, never presented as confirmed.
  ins.prefs.newsBucket = 'later';
  const later = frag(X.feedSlot(pf));
  const xom = [...later.querySelectorAll('.mpn-item')].find((it) => /XOM reports earnings/.test(words(it)));
  assert.equal(words(xom.querySelector('.mpn-status')), 'Estimate');
  assert.match(words(xom), /Tentative date: the company has not confirmed it/);
});

test('alerts are the viewer’s conditions, separate from the calendar, with dismiss and snooze, and say where they appear', (t) => {
  // Keep the rolling alert window anchored to the fixture's date.
  const now = Date.parse('2026-09-29T15:00:00Z');
  t.mock.method(Date, 'now', () => now);
  loaded();
  const events = I.events;
  const rules = [{ id: 'r1', type: 'weight_above', value: 15 }, { id: 'r2', type: 'call_changed', value: 30 }, { id: 'r3', type: 'cash_below', value: 5 }];
  const found = news.evaluateAlerts(pf, events, rules, now);
  assert.deepEqual(found.map((a) => a.title), ['NVDA is 35.0% of the account', 'AAPL is 15.1% of the account', 'XOM: Hold → Trim']);
  ins.watch = { rules, dismissed: { [found[0].id]: Date.now() }, snoozed: { [found[1].id]: Date.now() + 864e5 } };
  assert.deepEqual(X.activeAlerts(pf).map((a) => a.title).filter((t) => !/XOM/.test(t) || true).slice(0, 1).length, 1);
  assert.ok(!X.activeAlerts(pf).some((a) => a.id === found[0].id || a.id === found[1].id));
  const al = frag(X.alertsSlot(pf));
  assert.match(words(al.querySelector('.mpn-delivery')), /only on this page, when it is open\..*no background monitoring, e-?mail or push notification/i);
  assert.equal(al.querySelectorAll('.mpn-alert').length, X.activeAlerts(pf).length);
  assert.ok(al.querySelector('[data-hp-x^="dismiss:"]') && al.querySelector('[data-hp-x^="snooze:"]'));
});

test('hide amounts reaches every tab: overview, review, activity and events show no account dollars', () => {
  loaded();
  hp._state.hidden = true;
  try {
    const text = ['overview', 'review', 'activity', 'news'].map((t) => words(panel(render(t), t))).join(' ');
    for (const v of ['$9,354', '$699.12', '$25,874', '$330.60', '$572']) assert.ok(!text.includes(v), `${v} hidden`);
    assert.match(text, /35\.0%/, 'weights stay visible');
  } finally { hp._state.hidden = false; }
});

test('a failed section says so and offers a retry while the rest of the page stands', () => {
  loaded();
  ins.perf = { s: 'error', error: 'Synthetic provider failure' };
  ins.events = { s: 'error', error: 'Synthetic provider failure' };
  const d = render('overview');
  assert.match(words(d.querySelector('.mp-perf .mp-unavail')), /^Performance is unavailable: Synthetic provider failure\. Try again$/);
  assert.ok(d.querySelector('[data-hp-x="retry:perf"]'));
  assert.equal(d.querySelectorAll('.mpo-q').length, 3, 'the action queue still renders');
  assert.ok(d.querySelector('.mpo-perf .mp-ctrls [data-hp-x="range:1M"]'), 'a range with data can still be chosen');
  assert.ok(d.querySelector('.mpo-alloc .mpd'), 'allocation still renders');
  assert.equal(d.querySelector('.mp-week'), null, 'no invented week line');
});

test('Activity lives in the workspace: without trade records or order history it says so, and open lots still show', () => {
  loaded();
  const a = panel(render('activity'), 'activity');
  assert.ok(a.querySelector('#mpa-root [data-mp-slot="act-tools"]') && a.querySelector('#mpa-root [data-mp-slot="activity-body"]'));
  assert.match(words(a.querySelector('.mp-limit')), /^Robinhood provided neither trade records nor order history, so closing trades cannot be listed\. Open lots are still shown\.$/);
  X.actions['act-view']('lots');
  X.activityState.range = 'all';
  const lots = frag(X.activity(pf));
  assert.equal(lots.querySelectorAll('.mpa-t tbody tr').length, Math.min(I.records.config.page_size, I.records.lots.length));
  X.actions['act-view']('sales');
});
test('a server started before the update: one banner, and every card shows what the view itself supports', () => {
  loaded();
  for (const k of ['perf', 'expo', 'events', 'outcomes', 'records']) ins[k] = { s: 'missing', data: null };
  ins.act = { month: { s: 'missing' } };
  ins.stale = true;
  const d = frag(hp.portfolioHtml(view));
  assert.match(words(d.querySelector('.mp-banner')), /^Restart QUANTA to load the charts\./);
  assert.equal(d.querySelectorAll('.mp-banner').length, 1);
  // Performance falls back to last session against the market; exposure to the view's sectors.
  hp._state.market = { at: Date.now(), quotes: [{ symbol: 'SPY', label: 'S&P 500', change_pct: -0.42, quote_date: '2026-09-25' }] };
  const perf = frag(X.slotRenderers['perf-overview'](pf));
  assert.deepEqual([...perf.querySelectorAll('.mp-sess li')].map(words), ['Your holdings +0.37%', 'S&P 500 -0.42%']);
  assert.ok(perf.querySelector('.mp-sess-top [data-hp-inspect="NVDA"]'));
  hp._state.market = null;
  const ex = frag(X.slotRenderers['expo-overview'](pf));
  assert.deepEqual([...ex.querySelectorAll('.mpx-segb')].map(words), ['Sector', 'Holdings']);
  assert.deepEqual([...frag(X.slotRenderers['alloc-overview'](pf)).querySelectorAll('.mpo-seg button')].map(words), ['Sector', 'Treemap'], 'sectors and the treemap (from the view) without the exposure route');
  assert.equal(ex.querySelector('.mpx-name').firstChild.textContent, 'Technology');
  assert.ok(!words(ex).includes('unavailable'));
  // No card repeats the transport error.
  assert.doesNotMatch(words(d.body), /has no My Portfolio|older copy of QUANTA/);
  for (const t of ['activity', 'review', 'news']) {
    hp._state.tab = t;
    assert.doesNotThrow(() => frag(hp.portfolioHtml(view)), t);
  }
  assert.equal(words(frag(X.activityBody(pf))), 'Activity appears after a QUANTA restart.');
});
