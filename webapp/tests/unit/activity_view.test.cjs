// Activity tab (static/mp-activity.js) rendered against the activity fixtures
// (tests/fixtures/activity/*.json: synthetic payloads through the REAL server code).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const fixtures = path.join(root, 'tests', 'fixtures');
const view = JSON.parse(fs.readFileSync(path.join(fixtures, 'my_portfolio_view.json'), 'utf8'));
const load = (name) => JSON.parse(fs.readFileSync(path.join(fixtures, 'activity', `${name}.json`), 'utf8'));

const hp = require('../../static/my-portfolio.js');
globalThis.QuantaCharts = require('../../static/mp-charts.js');
globalThis.QuantaActivityModel = require('../../static/mp-activity-model.js');
require('../../static/mp-insights.js').attach(hp);
require('../../static/mp-activity.js').attach(hp);
const M = globalThis.QuantaActivityModel;
const X = hp.ext;
const ins = X.ins;
const A = X.activityState;
const pf = view.portfolio;

const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
const words = (el) => {
  const doc = el.ownerDocument || el;
  const walk = doc.createTreeWalker(el.body || el, 4);
  const out = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = n.textContent.replace(/\s+/g, ' ').trim(); if (t) out.push(t); }
  return out.join(' ');
};

// Load a scenario as the page would have it: records, and Robinhood's series per span.
function use(name, ui = {}) {
  const fx = load(name);
  ins.records = { s: 'ok', data: fx.records };
  ins.act = {};
  Object.entries(fx.activity).forEach(([span, data]) => { ins.act[span] = { s: 'ok', data }; });
  const cfg = fx.records.config;
  Object.assign(A, { ready: true, range: cfg.default_range, base: cfg.default_range, view: cfg.default_view, asset: 'all', symbols: [], period: null, age: null,
    gran: null, cum: true, shown: cfg.page_size, rankAll: false, q: '', sort: null, brush: null, today: M.wallOf(new Date(fx.now), cfg.timezone).slice(0, 10) }, ui);
  if (ui.range && typeof ui.range !== 'object') A.base = ui.range;
  hp._state.hidden = false;
  hp._state.tab = 'activity';
  return fx;
}
const render = () => frag(X.activity(pf));
const text = (d, sel) => { const el = d.querySelector(sel); return el ? words(el) : ''; };
// The realized/amount figure as rendered, e.g. "+$1,234.56".
const money = (s) => (s.match(/[+-]?\$[\d,]+\.\d{2}/) || [null])[0];

test('the layout, top to bottom: control row, five KPI tiles, chart beside ranking, table, one footer', () => {
  use('many', { range: 'all' });
  const d = render();
  const order = [...d.querySelectorAll('#mpa-root .mpa-ctl, #mpa-root .mpa-kpis, #mpa-root .mpa-main, #mpa-root .mpa-table, #mpa-root .mpa-foot')].map((e) => e.className.split(' ').find((c) => /^mpa-(ctl|kpis|main|table|foot)$/.test(c)));
  assert.deepEqual(order, ['mpa-ctl', 'mpa-kpis', 'mpa-main', 'mpa-table', 'mpa-foot']);
  assert.equal(d.querySelectorAll('.mpa-k').length, 5);
  assert.ok(d.querySelector('.mpa-main > .mpa-chart') && d.querySelector('.mpa-main > .mpa-rank'));
  assert.equal(d.querySelectorAll('.mpa-foot').length, 1, 'one footer note');
  assert.deepEqual([...d.querySelectorAll('[data-hp-x^="act-view:"]')].map(words), ['Closing trades', 'Purchases', 'Open lots']);
  assert.equal(d.querySelector('[data-hp-x="act-view:sales"]').getAttribute('aria-pressed'), 'true', 'closing trades by default');
  assert.deepEqual([...d.querySelectorAll('[data-hp-x^="act-range:"]')].map(words), load('many').records.config.ranges.map((r) => r.label));
  assert.deepEqual([...d.querySelectorAll('[data-hp-x^="act-asset:"]')].map(words), ['All', 'Equities', 'Crypto']);
  assert.equal(d.querySelector('[data-hp-x="act-asset:all"]').getAttribute('aria-pressed'), 'true');
  assert.ok(d.querySelector('.mpa-ms .mpa-ms-q[type="search"]'));
  assert.equal(d.querySelectorAll('.mpa-ms-o input').length, 75);
});

test('the view switch drives KPI tiles, chart, ranking and table together', () => {
  const seen = {};
  for (const v of ['sales', 'buys', 'lots']) {
    use('many', { range: 'all', view: v });
    const d = render();
    seen[v] = {
      kpis: [...d.querySelectorAll('.mpa-k dt')].map(words),
      chart: text(d, '#mpa-ch-t'),
      rank: d.querySelector('.mpa-r-bar.is-flow') ? 'flow' : 'diverging',
      caption: text(d, '.mpa-t caption'),
      pressed: d.querySelector(`[data-hp-x="act-view:${v}"]`).getAttribute('aria-pressed'),
    };
  }
  assert.deepEqual(seen.sales.kpis, ['Realized P&L', 'Closing trades', 'Average win / loss', 'Best / worst trade', 'Positive months']);
  assert.deepEqual(seen.buys.kpis, ['Capital deployed', 'Purchases', 'Assets bought', 'Average purchase', 'Largest purchase']);
  assert.deepEqual(seen.lots.kpis, ['Unrealized P&L', 'Open lots', 'Cost basis', 'Value', 'Lots by age']);
  assert.match(seen.sales.chart, /^Realized P&L per month$/);
  assert.match(seen.buys.chart, /^Capital deployed per /);
  assert.match(seen.lots.chart, /^Unrealized P&L by lot age$/);
  assert.equal(seen.buys.rank, 'flow');
  assert.equal(seen.lots.rank, 'diverging');
  assert.deepEqual([seen.sales.caption, seen.buys.caption, seen.lots.caption], ['Closing trades', 'Purchases', 'Open lots']);
  assert.ok(Object.values(seen).every((s) => s.pressed === 'true'));
});

test('reconciliation as rendered: KPI = ranking total = table total, and the chart sums to it', () => {
  const cases = [['many', { range: 'all' }], ['many', { range: 'year', symbols: ['SAA', 'SAB', 'SAC'] }], ['many', { range: 'all', view: 'buys' }],
    ['one_trade', { range: 'all' }], ['all_losses', { range: 'all' }], ['no_figure_bucket', { range: '3month' }], ['many', { range: 'all', view: 'lots' }]];
  for (const [name, ui] of cases) {
    use(name, ui);
    const d = render();
    const kpi = money(text(d, '.mpa-k:first-child .mpa-k-v'));
    const rank = money(text(d, '.mpa-rt:not(.mpa-rt--diff) b'));
    const table = money(text(d, '.mpa-sum-t dd'));
    assert.ok(kpi, `${name} ${JSON.stringify(ui)} has a total`);
    assert.equal(rank, kpi, `${name} ${JSON.stringify(ui)} ranking`);
    assert.equal(table, kpi, `${name} ${JSON.stringify(ui)} table`);
    const m = X.activityModel();
    assert.ok(Math.abs(m.periodTotal - m.total) < 1e-6, 'chart buckets');
  }
});

test('defect 2: return on closed cost reads about 4% for about $21 on about $520, and is defined', () => {
  use('one_trade', { range: 'all' });
  const d = render();
  const sub = d.querySelector('.mpa-k:first-child .mpa-k-s span');
  assert.equal(words(sub), '+4.05% on closed cost');
  assert.equal(sub.getAttribute('title'), 'Realized P&L divided by the cost basis of the closed lots');
  assert.match(text(d, '.mpa-foot'), /Return on closed cost = realized P&L ÷ cost basis of the closed lots/);
  assert.match(text(d, '.mpa-t tbody tr'), /\+\$21\.04 \+4\.05%/);
});

test('defect 3: axis labels and best/worst dates are unambiguous', () => {
  use('many', { range: 'all' });
  const d = render();
  const xs = [...d.querySelectorAll('.mpa-x')];
  assert.ok(xs.length > 2);
  xs.forEach((x) => assert.match(words(x), /^[A-Z][a-z]{2}( \d{4})?$/, 'a month, with a four-digit year where it starts or changes'));
  assert.ok(xs[0].querySelector('b'), 'the first label carries the year');
  for (const s of d.querySelectorAll('.mpa-k:nth-child(4) .mpa-k-s')) assert.match(words(s), /^S[A-Z]{2} [A-Z][a-z]{2} \d{1,2}, \d{4}$/, 'ticker and full date');
  const bar = d.querySelector('.mpa-b');
  assert.match(bar.getAttribute('aria-label'), /^[A-Z][a-z]+ \d{4}: /, 'the bar names its month and year');
});

test('defect 4: All states its coverage; a capped source says so with its real cap and dates', () => {
  const fx = use('capped', { range: 'all', view: 'buys' });
  const d = render();
  const c = fx.records.coverage.fills;
  const foot = text(d, '.mpa-foot');
  const from = M.dateLong(M.wallOf(new Date(c.first), fx.records.config.timezone).slice(0, 10));
  const to = M.dateLong(M.wallOf(new Date(c.last), fx.records.config.timezone).slice(0, 10));
  assert.ok(foot.includes(`${from} to ${to}`), 'the dates of what was loaded');
  assert.ok(foot.includes(`only the newest ${c.cap} of ${c.read} filled orders`), foot);
  A.view = 'sales';
  const t = fx.records.coverage.trades;
  assert.ok(text(render(), '.mpa-foot').includes(`the read stopped after ${t.pages} pages`));
  // The range itself names its dates.
  assert.match(text(render(), '.mpa-sum-f'), /^All \([A-Z][a-z]{2} \d{1,2}, \d{4} to [A-Z][a-z]{2} \d{1,2}, \d{4}\)/);
  use('many', { range: 'all' });
  assert.doesNotMatch(text(render(), '.mpa-foot'), /Capped|Incomplete/, 'a complete history is not flagged');
});

test('defect 5: no share total summed across different stocks', () => {
  for (const v of ['sales', 'buys', 'lots']) {
    use('many', { range: 'all', view: v });
    const d = render();
    assert.doesNotMatch(text(d, '.mpa-tbl-h') + text(d, '.mpa-kpis') + text(d, '.mpa-gross'), /shares/i, v);
  }
});

test('defect 6: one scroll mechanism, the page; "Show more" only past the configured page size', () => {
  const fx = use('many', { range: 'all' });
  const cfg = fx.records.config;
  const d = render();
  assert.equal(d.querySelectorAll('.mpa-t tbody tr').length, cfg.page_size);
  assert.equal(d.querySelectorAll('[data-hp-x="act-more"]').length, 1);
  assert.equal(d.querySelector('.q-tablewrap, .mpa-tw[style*="overflow"]'), null, 'no inner scroll wrapper');
  X.actions['act-more']();
  assert.equal(render().querySelectorAll('.mpa-t tbody tr').length, Math.min(2 * cfg.page_size, 1000));
  use('one_trade', { range: 'all' });
  assert.equal(render().querySelectorAll('[data-hp-x="act-more"]').length, 0);
  // The stylesheet: the table wrapper does not scroll on desktop widths.
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const block = css.slice(css.indexOf('/* ---- Activity: control row'), css.indexOf('@media (max-width: 1279px)', css.indexOf('/* ---- Activity: control row')));
  assert.doesNotMatch(block.match(/\.mpa-tw \{[^}]*}/)[0], /overflow|max-height/);
  assert.doesNotMatch(block.match(/\.mpa-table \{[^}]*}/)[0], /overflow|max-height/);
});

test('defect 7: shares to four decimals with zeros trimmed, currency to two', () => {
  use('many', { range: 'all' });
  const d = render();
  const qtys = [...d.querySelectorAll('.mpa-t tbody tr td:nth-child(3)')].map(words);
  assert.ok(qtys.length);
  qtys.forEach((q) => assert.match(q, /^[\d,]+(\.\d{1,4})?$/));
  assert.ok(qtys.every((q) => !/\.\d*0$/.test(q)), 'no trailing zeros');
  [...d.querySelectorAll('.mpa-t tbody td.num')].map(words).filter((t) => t.includes('$')).forEach((t) => {
    (t.match(/\$[\d,]+(\.\d+)?/g) || []).forEach((v) => assert.match(v, /\.\d{2}$/, t));
  });
});

test('defect 1: a trade history shorter than the range shows the difference, never a silent "no closing trades"', () => {
  use('short_window', { range: 'month' });
  const d = render();
  assert.match(text(d, '.mpa-rank'), /Robinhood's total for the past month [+-]\$[\d,.]+ Not in the trade records [+-]\$[\d,.]+/);
  assert.match(text(d, '.mpa-ch-empty'), /^No closing trades in the loaded trade records for this range, though Robinhood counts \d+$/);
  assert.match(text(d, '.mpa-foot'), /Incomplete: Robinhood returned its "week" window only; restart QUANTA to read all history\./);
});

test('a Robinhood bucket with no figure: hatched, one legend entry, listed once in the footer', () => {
  use('no_figure_bucket', { range: '3month' });
  const d = render();
  assert.equal(d.querySelectorAll('.mpa-legend .mpa-key--nofig').length, 1);
  assert.equal(d.querySelectorAll('.mpa-b.is-nofig').length, 1);
  assert.equal((text(d, '.mpa-foot').match(/Left out everywhere/g) || []).length, 1);
  assert.match(text(d, '.mpa-foot'), /Left out everywhere: 1 closing trade without a realized figure \(1 in a period Robinhood reports without one, usually transfers\): [A-Z][a-z]{2} \d{1,2}, \d{4}\./);
  assert.match(text(d, '.mpa-foot'), /1 closing trade falls in periods Robinhood reports without a total \(usually transfers\): it is counted from its own figures/);
  assert.equal(d.querySelector('.mpa-rt--diff'), null, 'what remains reconciles, so no difference line');
});

test('filter chips: stocks, a chosen period and a brushed range each show a chip, and one Clear all removes them', () => {
  use('many', { range: 'year' });
  X.actions['act-pick']('SAB');
  X.actions['act-pick']('SAA');
  let d = frag(X.activityTools());
  assert.deepEqual([...d.querySelectorAll('.mpa-chip')].map(words), ['SAA ×', 'SAB ×']);
  const m = X.activityModel();
  const busy = m.periods.filter((p) => p.n).pop();
  ins.actModel = m;
  X.actions['act-bar'](String(m.periods.indexOf(busy)));
  d = frag(X.activityTools());
  assert.ok([...d.querySelectorAll('.mpa-chip')].map(words).includes(`${busy.long} ×`));
  const all = frag(X.activity(pf));
  assert.ok([...all.querySelectorAll('.mpa-t tbody tr')].every((r) => r.dataset.k === busy.key && ['SAA', 'SAB'].includes(r.dataset.sym)), 'the table follows the chip');
  assert.equal(all.querySelectorAll('.mpa-b.is-sel').length, 1);
  assert.ok(all.querySelectorAll('.mpa-b.is-dim').length > 0, 'other periods dim, still drawn');
  X.actions['act-clear']('all');
  assert.equal(frag(X.activityTools()).querySelectorAll('.mpa-chip').length, 0);
  assert.deepEqual([A.symbols, A.period], [[], null]);
  // A brushed range is a chip too, and clearing it returns to the range chip it came from.
  A.range = { from: '2026-03-01', to: '2026-03-31' };
  d = frag(X.activityTools());
  assert.deepEqual([...d.querySelectorAll('.mpa-chip')].map(words), ['Mar 1, 2026 to Mar 31, 2026 ×']);
  assert.equal(d.querySelectorAll('[data-hp-x^="act-range:"][aria-pressed="true"]').length, 0);
  X.actions['act-clear']('range');
  assert.equal(A.range, 'year');
});

test('clicking a stock in the ranking or the table filters to it; again clears it', () => {
  use('many', { range: 'all' });
  const d = render();
  const sym = d.querySelector('.mpa-r[data-sym]').dataset.sym;
  assert.ok(d.querySelector(`.mpa-symb[data-hp-x="act-sym:${d.querySelector('.mpa-symb').dataset.sym}"]`));
  X.actions['act-sym'](sym);
  const f = render();
  assert.ok([...f.querySelectorAll('.mpa-t tbody tr')].every((r) => r.dataset.sym === sym));
  assert.deepEqual([...f.querySelectorAll('.mpa-r[data-sym]')].map((r) => r.dataset.sym), [sym]);
  X.actions['act-sym'](sym);
  assert.deepEqual(A.symbols, []);
});

test('column limits per view; days held only with lot dates; the tax term only when a lot is long-term', () => {
  const heads = (d) => [...d.querySelectorAll('.mpa-t thead th')].map((th) => words(th).replace(/ [▲▼]$/, ''));
  use('many', { range: 'all' });
  const sales = heads(render());
  assert.ok(sales.length <= 6);
  assert.deepEqual(sales, ['Closed', 'Stock', 'Quantity', 'Cost → sell', 'Realized'], 'Robinhood trade records carry no open date');
  use('many', { range: 'all', view: 'buys' });
  assert.deepEqual(heads(render()), ['Date', 'Stock', 'Quantity at price', 'Amount']);
  use('many', { range: 'all', view: 'lots' });
  const lots = heads(render());
  assert.equal(lots.length, 6);
  assert.equal(lots[1], 'Acquired · term', 'a long-term lot exists in this fixture');
  use('missing_lot_dates', { range: 'all', view: 'lots' });
  assert.equal(heads(render())[1], 'Acquired', 'no long-term lot: no term');
  // The FIFO fallback knows when each lot was bought: days held appears.
  const fx = use('many', { range: 'all' });
  ins.records = { s: 'ok', data: { ...fx.records, closed_trades: null, fills: [
    { symbol: 'SAA', side: 'buy', quantity: 10, price: 100, at: '2026-01-05T15:00:00Z' },
    { symbol: 'SAA', side: 'sell', quantity: 4, price: 130, at: '2026-09-21T18:45:00Z' }] } };
  const fifo = render();
  assert.deepEqual(heads(fifo), ['Closed', 'Stock', 'Quantity', 'Cost → sell', 'Realized', 'Days held']);
  assert.match(text(fifo, '.mpa-foot'), /QUANTA's estimate.*cannot see stock splits/);
});

test('empty account: one line per section says what is missing, and the layout stays', () => {
  for (const v of ['sales', 'buys', 'lots']) {
    use('empty', { range: 'all', view: v });
    const d = render();
    const what = { sales: 'closing trades', buys: 'purchases', lots: 'open lots' }[v];
    assert.equal(text(d, '.mpa-ch-empty'), `No ${what} in this range`);
    assert.equal(text(d, '.mpa-rank .mpa-empty'), `No ${what} in this range`);
    assert.equal(text(d, '.mpa-table .mpa-empty'), `No ${what} in this range`);
    assert.equal(d.querySelectorAll('.mpa-k').length, 5, 'tiles stay');
    assert.ok(d.querySelector('.mpa-plot'), 'the chart frame stays');
    const ys = [...d.querySelectorAll('.mpa-y')].map(words);
    assert.equal(new Set(ys).size, ys.length, `distinct axis labels: ${ys.join(' ')}`);
  }
});

test('tooltips name the period, the figure, counts, wins and losses, and best and worst', () => {
  use('many', { range: 'all' });
  const m = X.activityModel();
  const p = m.periods.find((x) => x.n > 2);
  const t = frag(X.activityTip(m, p, load('many').records.config));
  assert.match(words(t), new RegExp(`^${p.long} [+-]\\$[\\d,.]+ \\d+ closing trades · \\d+ up, \\d+ down Best S[A-Z]{2} [+-]\\$[\\d,.]+ Worst S[A-Z]{2} [+-]\\$[\\d,.]+$`));
});

test('bars are keyboard reachable: one tab stop, each a labelled button', () => {
  use('many', { range: 'all' });
  const bars = [...render().querySelectorAll('.mpa-b')];
  assert.ok(bars.length > 1);
  assert.equal(bars.filter((b) => b.getAttribute('tabindex') === '0').length, 1);
  assert.ok(bars.every((b) => b.tagName === 'BUTTON' && b.getAttribute('aria-label')));
  assert.ok(render().querySelector('.mpa-chart .sr-only').textContent.startsWith('Realized '), 'a generated text summary');
});

test('the URL keeps the range, the view and the stocks', () => {
  use('many', { range: '3month', view: 'buys', symbols: ['SAB'] });
  assert.equal(X.tabHash('activity'), '/r=3month&v=buys&s=SAB');
  assert.equal(X.tabHash('overview'), '');
  const st = M.parseHash(`#holdings/activity${X.tabHash('activity')}`, load('many').records.config);
  assert.deepEqual(st, { range: '3month', view: 'buys', asset: 'all', symbols: ['SAB'] });
});

test('hide amounts: no account dollars or share counts anywhere in the tab', () => {
  use('many', { range: 'all' });
  hp._state.hidden = true;
  try {
    const t = words(render());
    assert.doesNotMatch(t, /\$\d/);
    assert.doesNotMatch(t, /plus \$|minus \$/);
  } finally { hp._state.hidden = false; }
});

test('records without coverage (an older provider) fall back to the loaded dates and are not called complete', () => {
  const fx = use('many', { range: 'all' });
  const { coverage, ...rest } = fx.records;
  ins.records = { s: 'ok', data: { ...rest, coverage: {} } };
  const foot = text(render(), '.mpa-foot');
  assert.match(foot, /^Closing trades: Robinhood's trade records, [A-Z][a-z]{2} \d{1,2}, \d{4} to [A-Z][a-z]{2} \d{1,2}, \d{4} \(1000 closing trades\)\. The server did not report whether this history is complete/);
});

test('a server that predates the redesign: one line, no invented figures', () => {
  const fx = use('many');
  const { config, coverage, ...old } = fx.records;
  ins.records = { s: 'ok', data: old };
  assert.match(text(render(), '.mp-unavail'), /predates the Activity redesign\. Restart QUANTA/);
});

test('the table summary sits in the table header; the lower right holds gross profit and gross loss from the same rows', () => {
  use('many', { range: 'all' });
  const d = render();
  assert.equal(d.querySelector('.mpa-aside'), null, 'no separate summary card');
  assert.match(text(d, '.mpa-tw > .mpa-tbl-h'), /^Showing 50 of 1000 closing trades All \(.+\) Table total [+-]\$[\d,.]+ Stocks 75$/);
  const card = d.querySelector('.mpa-table > .mpa-gross');
  assert.ok(card, 'the card sits beside the table');
  assert.match(text(card, '.mpa-gr-t'), /^Gross profit and gross loss$/);
  const m = X.activityModel();
  assert.equal(money(text(card, '.mpa-rt b')), money(text(d, '.mpa-k:first-child .mpa-k-v')), 'net = the KPI total');
  assert.match(text(card, '.mpa-gr-pf'), new RegExp(`^Profit factor ${m.kpis.profitFactor.toFixed(2).replace('.', '\.')}$`));
  assert.equal(card.querySelector('.mpa-gr-pf').getAttribute('title'), 'Gross profit divided by gross loss');
  use('all_gains', { range: 'all' });
  assert.match(text(render(), '.mpa-gr-pf'), /^Profit factor no losing trades$/);
  use('many', { range: 'all', view: 'buys' });
  assert.match(text(render(), '.mpa-gross'), /^Purchase sizes Smallest \$[\d,.]+ Median \$[\d,.]+ Average \$[\d,.]+ Largest \$[\d,.]+$/);
});

test('the ranking labels its columns from the config, and says when Robinhood\'s total matches', () => {
  const fx = use('no_figure_bucket', { range: '3month' });
  const d = render();
  const v = fx.records.config.views.find((x) => x.key === 'sales');
  assert.equal(text(d, '.mpa-rh'), `Stock ${v.value} ${v.count}`);
  assert.equal(v.count, 'Trades');
  assert.equal(text(d, '.mpa-rt-ok'), "Matches Robinhood's total for the past 3 months");
  use('short_window', { range: 'month' });
  assert.equal(render().querySelector('.mpa-rt-ok'), null, 'a difference is shown instead');
});

test('week buckets read "Week of" a date; the cost column says it is implied by the realized gain', () => {
  use('many', { range: '3month' });
  const d = render();
  assert.equal(X.activityModel().gran.key, 'week');
  [...d.querySelectorAll('.mpa-x .mpa-x-m')].forEach((x) => assert.match(words(x), /^Week of [A-Z][a-z]{2} \d{1,2}$/));
  assert.match(d.querySelector('.mpa-b').getAttribute('aria-label'), /^Week of [A-Z][a-z]{2} \d{1,2}, \d{4}: /);
  const th = [...d.querySelectorAll('.mpa-t thead th')].find((x) => /Cost → sell/.test(words(x)));
  assert.match(th.getAttribute('title'), /implied by Robinhood's realized gain/);
});

// ---------------------------------------------------------------- guards
test('guard: no ticker, amount or date literals in the Activity source', () => {
  for (const f of ['mp-activity.js', 'mp-activity-model.js']) {
    const src = fs.readFileSync(path.join(root, 'static', f), 'utf8');
    // String and template literal bodies (comments included in the scan: no examples there either).
    const literals = src.match(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g) || [];
    for (const lit of literals) {
      // HTTP verbs and the UTC zone name are protocol words, not tickers.
      const body = lit.slice(1, -1).replace(/\$\{[^}]*\}/g, ' ').replace(/^(GET|POST|UTC)$/, '');
      assert.doesNotMatch(body, /(^|[^A-Za-z&#-])[A-Z]{2,5}(\.[A-Z])?(?![A-Za-z;])/, `${f}: ticker-like literal ${lit}`);
      assert.doesNotMatch(body, /\$\s?\d/, `${f}: amount literal ${lit}`);
    }
    const hit = (re) => { const m = src.match(re); return m ? m[0] : null; };
    assert.equal(hit(/\b(19|20)\d{2}-\d{2}-\d{2}\b/), null, `${f}: an ISO date`);
    assert.equal(hit(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}\b/), null, `${f}: a written date`);
    assert.equal(hit(/\b20\d{2}\b/), null, `${f}: a year`);
    assert.equal(hit(/\$\s?\d/), null, `${f}: an amount`);
  }
});

test('guard: the Activity stylesheet uses tokens, the spacing scale, and green/red only for gain and loss', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const start = css.indexOf('/* ---- Activity: control row');
  const block = css.slice(start, css.indexOf('/* ---- Guidance: the research rating gauge', start));
  assert.ok(block.length > 2000);
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,8}\b/, 'no literal colours');
  // Red and green appear only on gain/loss selectors.
  const rules = block.match(/[^{}]+\{[^}]*\}/g);
  for (const r of rules) {
    if (!/var\(--(bull|bear)\)/.test(r)) continue;
    const sel = r.slice(0, r.indexOf('{'));
    assert.match(sel, /is-up|is-down|\.bull|\.bear/, `colour on a non-P&L selector: ${sel.trim()}`);
  }
  // Paddings and gaps on the 4/8/12/16/24 scale (tokens --sp-1..4 and --sp-6).
  for (const m of block.matchAll(/(?:padding|gap|margin)[^:]*:\s*([^;]+);/g)) {
    assert.doesNotMatch(m[1], /var\(--sp-(5|7|8|9|10)\)/, `off-scale spacing ${m[0]}`);
  }
  assert.match(block, /\.mpa-t th, \.mpa-t td \{[^}]*height: 32px/, 'rows 32px');
  assert.match(block, /font-variant-numeric: tabular-nums/);
});


test('asset actions update all views and preserve range and view selection', () => {
  use('many', { range: 'month' });
  for (const view of ['sales', 'buys', 'lots']) {
    X.actions['act-view'](view);
    X.actions['act-asset']('crypto');
    const d = render();
    assert.equal(A.range, 'month');
    assert.equal(A.view, view);
    assert.equal(d.querySelector('[data-hp-x="act-asset:crypto"]').getAttribute('aria-pressed'), 'true');
    assert.match(text(d, '.mpa-ch-empty'), /^No /);
    assert.equal(d.querySelectorAll('.mpa-r[data-sym]').length, 0);
    X.actions['act-asset']('all');
  }
});

test('unclassified records are explained and excluded from Equities', () => {
  use('one_trade', { range: 'all' });
  assert.match(text(render(), '.mpa-foot'), /loaded records have no verified asset type and appear only under All/);
  X.actions['act-asset']('equity');
  const d = render();
  assert.equal(d.querySelectorAll('.mpa-ms-o').length, 0);
  assert.equal(d.querySelectorAll('.mpa-t tbody tr[data-sym]').length, 0);
});

// A refreshed portfolio timestamp forces a records reload with cached rows.
test('records refresh keeps every filter and the Activity body while pending and after failure', async () => {
  use('one_trade', { range: 'all', symbols: ['SAA'] });
  const dom = new JSDOM('<body><div data-mp-slot="act-tools"></div><div data-mp-slot="activity-body"></div></body>');
  const oldDocument = globalThis.document;
  const oldApi = hp._h.api;
  const oldView = hp._state.view;
  let finish;
  let requested = false;
  hp._h.api = () => { requested = true; return new Promise((resolve) => { finish = resolve; }); };
  hp._state.view = view;
  globalThis.document = dom.window.document;
  try {
    X.paint('records');
    const tools = () => dom.window.document.querySelector('[data-mp-slot="act-tools"]');
    const groups = () => [...tools().querySelectorAll('[role="group"]')].map((g) => g.getAttribute('aria-label'));
    const expected = groups();
    const pending = X.load('records', true);
    // Attach immediately so a renderer rejection cannot go unhandled.
    const settled = pending.then(() => null, (error) => error);
    assert.equal(requested, true, 'rendering cached rows must allow the reload request to start');
    assert.deepEqual(groups(), expected, 'range, asset type, assets, view and active filters survive loading');
    assert.equal(tools().querySelector('[data-hp-x="act-range:all"]').getAttribute('aria-pressed'), 'true');
    assert.equal(dom.window.document.querySelectorAll('.mpa-k').length, 5);
    finish({ ok: false, status: 503, data: {} });
    assert.equal(await settled, null, 'the renderer must not reject the refresh');
    assert.deepEqual(groups(), expected, 'a failed refresh retains the usable filters');
    assert.equal(dom.window.document.querySelectorAll('.mpa-k').length, 5);
    assert.ok(tools().querySelector('[data-hp-x="retry:records"]'), 'cached data still needs a retry action');
    hp._h.api = async () => ({ ok: true, status: 200, data: load('one_trade').records });
    await X.load('records', true);
    assert.deepEqual(groups(), expected, 'a successful retry restores the same controls');
    assert.equal(tools().querySelector('[data-hp-x="retry:records"]'), null);
  } finally {
    hp._h.api = oldApi;
    hp._state.view = oldView;
    globalThis.document = oldDocument;
    dom.window.close();
  }
});

test('filters show a loading status until the initial server config arrives', () => {
  use('one_trade');
  ins.records = { s: 'loading' };
  const d = frag(X.activityTools());
  assert.match(text(d, '[role="status"]'), /Loading activity filters/);
});


test('account reset clears cached Activity records and filters', () => {
  use('one_trade', { range: 'all', symbols: ['SAA'] });
  X.reset(true);
  assert.equal(ins.records.data, undefined);
  assert.equal(X.activityModel(), null);
  assert.equal(frag(X.activityTools()).querySelector('[data-hp-x^="act-range:"]'), null);
  assert.deepEqual(A.symbols, []);
});
