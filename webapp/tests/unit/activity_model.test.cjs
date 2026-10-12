// Activity model (static/mp-activity-model.js): the numbers behind the Activity tab.
// Fixtures: tests/fixtures/activity/*.json, synthetic payloads through the REAL server code
// (tests/tools/make_activity_fixtures.py).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const M = require('../../static/mp-activity-model.js');

const dir = path.join(__dirname, '..', 'fixtures', 'activity');
const load = (name) => JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), 'utf8'));
const SCENARIOS = fs.readdirSync(dir).filter((f) => f.endsWith('.json')).map((f) => f.replace(/\.json$/, ''));
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-6, `${msg}: ${a} vs ${b}`);

function ctx(fx, view, filters = {}) {
  const cfg = fx.records.config;
  const today = M.wallOf(new Date(fx.now), cfg.timezone).slice(0, 10);
  const range = filters.range || cfg.default_range;
  const span = typeof range === 'object' ? null : M.rangeOf(cfg, range).span;
  return { cfg, view, records: fx.records, today, series: span ? fx.activity[span] : null, filters: { range, ...filters } };
}

test('every fixture scenario exists', () => {
  for (const s of ['empty', 'one_trade', 'many', 'all_losses', 'all_gains', 'capped', 'missing_lot_dates', 'no_figure_bucket', 'short_window']) {
    assert.ok(SCENARIOS.includes(s), s);
  }
});

test('reconciliation: KPI total = chart buckets = ranking = table, for every view, range and filter', () => {
  let checked = 0;
  for (const name of SCENARIOS) {
    const fx = load(name);
    const cfg = fx.records.config;
    for (const view of cfg.views.map((v) => v.key)) {
      for (const range of cfg.ranges.map((r) => r.key)) {
        const base = M.build(ctx(fx, view, { range }));
        if (base.unavailable) continue;
        const syms = base.symbolsAll.slice(0, 2);
        const busy = base.periods.filter((p) => p.n > 0).map((p) => p.key);
        const variants = [{}, { symbols: syms.slice(0, 1) }, { symbols: syms }, ...(busy.length ? [view === 'lots' ? { age: busy[0] } : { period: busy[busy.length - 1] }] : [])];
        for (const v of variants) {
          const m = M.build(ctx(fx, view, { range, ...v }));
          const label = `${name}/${view}/${range}/${JSON.stringify(v)}`;
          const val = (r) => (view === 'sales' ? r.realized : view === 'buys' ? r.amount : r.gain);
          near(m.total, m.kpis[view === 'sales' ? 'realized' : view === 'buys' ? 'deployed' : 'unrealized'], `${label} KPI`);
          near(m.total, m.periodTotal, `${label} chart buckets`);
          near(m.total, m.rankingTotal, `${label} ranking`);
          near(m.total, m.rows.reduce((t, r) => t + val(r), 0), `${label} table`);
          assert.equal(m.rows.length, m.periods.filter((p) => p.active).reduce((t, p) => t + p.n, 0), `${label} counts`);
          if (view !== 'buys') near(m.kpis.grossGain + m.kpis.grossLoss, m.total, `${label} gross gain + gross loss`);
          checked += 1;
        }
      }
    }
  }
  assert.ok(checked > 300, `checked ${checked} combinations`);
});

test('return on closed cost is a fraction shown as a percent: about $21 on about $520 is about 4%, not 0.04%', () => {
  const fx = load('one_trade');
  const m = M.build(ctx(fx, 'sales', { range: 'all' }));
  const t = fx.records.closed_trades[0];
  const cost = t.quantity * t.price - t.realized_gain;
  near(m.kpis.closedCost, cost, 'closed cost = proceeds less the gain');
  near(m.kpis.ret, t.realized_gain / cost, 'realized / closed cost');
  assert.equal(M.pct(m.kpis.ret, fx.records.config), '+4.05%');
  assert.equal(M.pct(0.0404, fx.records.config), '+4.04%');
  assert.equal(M.pct(-0.0404, fx.records.config), '-4.04%');
  assert.equal(M.pct(0, fx.records.config), '0.00%');
  assert.equal(M.pct(null, fx.records.config), null);
  assert.ok(Math.abs(m.rows[0].entry * t.quantity - cost) < 1e-6, 'the implied buy price');
});

test('shares show up to the configured decimals with trailing zeros trimmed; currency has its own decimals', () => {
  const cfg = load('one_trade').records.config;
  assert.equal(M.shares(0.123456, cfg), '0.1235');
  assert.equal(M.shares(4, cfg), '4');
  assert.equal(M.shares(2.5, cfg), '2.5');
  assert.equal(M.shares(1234.10001, cfg), '1,234.1');
  assert.equal(cfg.decimals.currency, 2);
});

test('date labels are unambiguous: month names carry the year at the start and at each new year', () => {
  const months = M.periodsOf('2025-11-03', '2026-02-10', 'month');
  const lab = M.axisLabels(months, 'month', 12);
  assert.deepEqual(lab.map((l) => `${l.main}${l.year ? ` ${l.year}` : ''}`), ['Nov 2025', 'Dec', 'Jan 2026', 'Feb']);
  assert.equal(M.periodLong(months[0], 'month'), 'November 2025');
  const weeks = M.periodsOf('2025-12-24', '2026-01-08', 'week');
  assert.equal(M.periodLong(weeks[1], 'week'), 'Week of Dec 29, 2025');
  const wl = M.axisLabels(weeks, 'week', 12);
  assert.deepEqual(wl.map((l) => `${l.pre} ${l.main}${l.year ? ` ${l.year}` : ''}`), ['Week of Dec 22 2025', 'Week of Dec 29', 'Week of Jan 5 2026']);
  assert.equal(M.periodLong({ start: '2026-09-03', end: '2026-09-03' }, 'day'), 'Thu, Sep 3, 2026');
  assert.equal(M.dateLong('2026-06-26'), 'Jun 26, 2026');
  // No label is ever a month and a two-digit number that could be a day or a year.
  const many = load('many');
  for (const range of ['3month', 'year', 'all']) {
    const m = M.build(ctx(many, 'sales', { range }));
    for (const p of m.periods) {
      if (!p.axis) continue;
      if (m.gran.key === 'month') assert.match(p.axis.main, /^[A-Z][a-z]{2}$/);
      if (p.axis.year) assert.match(p.axis.year, /^\d{4}$/);
    }
    assert.ok(m.periods.find((p) => p.axis).axis.year, 'the first label carries the year');
  }
});

test('granularity follows the span and the configured bucket limit, and an override must fit', () => {
  const fx = load('many');
  const cfg = fx.records.config;
  const g = (range, gran) => M.build(ctx(fx, 'sales', { range, gran })).gran;
  assert.equal(g('month').key, 'day');
  assert.equal(g('3month').key, 'week');
  assert.equal(g('year').key, 'month');
  assert.equal(g('all').key, 'month');
  assert.equal(g('3month', 'month').key, 'month', 'a coarser override that fits');
  assert.equal(g('year', 'day').key, 'month', 'an override over the limit is refused');
  for (const range of cfg.ranges.map((r) => r.key)) assert.ok(M.build(ctx(fx, 'sales', { range })).periods.length <= cfg.max_buckets, range);
});

test('sparse closings open the chart on the configured coarser granularity; an override still wins', () => {
  const fx = load('one_trade');
  const cfg = fx.records.config;
  const m = M.build(ctx(fx, 'sales', { range: 'month' }));
  assert.ok(m.rows.length / m.gran.options.find((o) => o.key === 'day').count < cfg.sparse.rows_per_bucket);
  assert.equal(m.gran.key, cfg.sparse.use);
  assert.equal(m.gran.sparse, true);
  assert.equal(M.build(ctx(fx, 'sales', { range: 'month', gran: 'day' })).gran.key, 'day');
  const busy = M.build(ctx(load('many'), 'sales', { range: 'month' }));
  assert.equal(busy.gran.sparse, false);
  assert.equal(busy.gran.key, 'day', 'dense closings keep the finest fitting granularity');
});

test('profit factor is gross profit over gross loss, and only exists with a loss', () => {
  const m = M.build(ctx(load('many'), 'sales', { range: 'all' }));
  const g = m.rows.filter((r) => r.realized > 0).reduce((t, r) => t + r.realized, 0);
  const l = m.rows.filter((r) => r.realized < 0).reduce((t, r) => t + r.realized, 0);
  near(m.kpis.grossGain, g, 'gross profit');
  near(m.kpis.grossLoss, l, 'gross loss');
  near(m.kpis.profitFactor, g / -l, 'profit factor');
  assert.equal(M.build(ctx(load('all_gains'), 'sales', { range: 'all' })).kpis.profitFactor, null);
  assert.equal(M.build(ctx(load('all_losses'), 'sales', { range: 'all' })).kpis.profitFactor, 0);
});

test('a Robinhood bucket with closing trades but no figure flags its period; only trades without a figure are left out', () => {
  // Audit A1: dropping every trade inside a null bucket made the same trades sum
  // differently per range (coarser ranges have coarser buckets).
  const fx = load('no_figure_bucket');
  const m = M.build(ctx(fx, 'sales', { range: '3month' }));
  const B = M.brokerSeries(fx.activity['3month'], fx.records.config.timezone);
  assert.equal(B.noFigure.length, 1);
  // Expected counts come from the fixture itself, so a regenerated fixture (any seed) still checks the rule.
  const tz = fx.records.config.timezone;
  const inWindow = fx.records.closed_trades.map((x) => ({ x, w: M.wallOf(M.instant(x.at || x.closed_at), tz) }))
    .filter(({ w }) => w.slice(0, 10) >= m.window.from && w.slice(0, 10) <= m.window.to);
  const inNull = inWindow.filter(({ w }) => B.noFigure.some((b) => w >= b.start && (!b.end || w < b.end)));
  assert.ok(inNull.some(({ x }) => x.realized_gain != null), 'the fixture puts a trade with its own figure in the null bucket');
  assert.equal(m.excluded.length, inWindow.filter(({ x }) => x.realized_gain == null).length, 'only trades without their own figure');
  assert.ok(m.excluded.every((e) => e.row.realized === null));
  assert.equal(m.inNoFigure, inNull.filter(({ x }) => x.realized_gain != null).length, 'trades with their own figure are counted and flagged');
  assert.ok(!m.rows.some((r) => m.excluded.some((e) => e.row.id === r.id)));
  assert.equal(m.periods.filter((p) => p.noFigure).length, 1, 'one hatched period');
  // A bucket with no trades and no figure is an empty bucket, not a missing figure.
  assert.ok(fx.activity['3month'].buckets.some((b) => b.gain === null && b.trades === 0));
  assert.ok(m.check && m.check.matches, 'outside the null bucket the records reconcile with Robinhood');
});

test('the same trades give the same total in every preset range that covers them (audit A1)', () => {
  const fx = load('no_figure_bucket');
  const ymds = M.build(ctx(fx, 'sales', { range: 'all' })).rows.map((r) => r.ymd).sort();
  const covering = fx.records.config.ranges.filter((r) => {
    const w = M.windowOf(fx.records.config, r.key, M.wallOf(new Date(fx.now), fx.records.config.timezone).slice(0, 10), ymds[0]);
    return w.from <= ymds[0] && ymds[ymds.length - 1] <= w.to;
  });
  assert.ok(covering.length >= 2, 'at least two ranges cover every trade');
  const totals = covering.map((r) => { const k = M.build(ctx(fx, 'sales', { range: r.key })).kpis; return `${k.count}:${k.realized.toFixed(2)}`; });
  assert.equal(new Set(totals).size, 1, `${covering.map((r) => r.key).join(', ')} -> ${totals.join(' | ')}`);
});

test('defect 1: a trade history shorter than the range is a named difference, never "no closing trades"', () => {
  const fx = load('short_window');
  assert.equal(fx.records.coverage.trades.capped_by, 'span');
  const m = M.build(ctx(fx, 'sales', { range: 'month' }));
  assert.equal(m.rows.length, 0);
  assert.ok(m.check, 'Robinhood\'s window total is compared');
  assert.equal(m.check.matches, false);
  assert.ok(m.check.brokerTrades > 0);
  near(m.check.diff, m.check.broker, 'the whole broker total is missing from the records');
});

test('empty account: zero everywhere, nothing invented', () => {
  const fx = load('empty');
  for (const view of ['sales', 'buys', 'lots']) {
    const m = M.build(ctx(fx, view, { range: 'all' }));
    assert.equal(m.rows.length, 0);
    assert.equal(m.total, 0);
    assert.deepEqual(m.ranking, []);
  }
  assert.equal(M.build(ctx(fx, 'sales', { range: 'all' })).kpis.best, null);
});

test('all losses and all gains: averages exist only for the side that happened', () => {
  const L = M.build(ctx(load('all_losses'), 'sales', { range: 'all' })).kpis;
  assert.equal(L.avgWin, null); assert.ok(L.avgLoss < 0); assert.equal(L.winRate, 0); assert.equal(L.periodsPositive, 0);
  const G = M.build(ctx(load('all_gains'), 'sales', { range: 'all' })).kpis;
  assert.equal(G.avgLoss, null); assert.ok(G.avgWin > 0); assert.equal(G.winRate, 1); assert.equal(G.periodsPositive, G.periodsWith);
});

test('1,000 trades in 75 stocks build quickly and keep the ranking complete', () => {
  const fx = load('many');
  const t0 = Date.now();
  const m = M.build(ctx(fx, 'sales', { range: 'all' }));
  assert.ok(Date.now() - t0 < 1500, 'fast enough to rebuild on every filter');
  assert.equal(m.rows.length, 1000);
  assert.equal(m.symbolsAll.length, 75);
  assert.equal(m.ranking.length, 75);
});

test('open lots: undated lots appear only under All; days held needs lot dates', () => {
  const fx = load('missing_lot_dates');
  const all = M.build(ctx(fx, 'lots', { range: 'all' }));
  assert.equal(all.rows.length, 3);
  assert.equal(all.rows.filter((r) => r.ageKey === null).length, 2);
  const year = M.build(ctx(fx, 'lots', { range: 'year' }));
  assert.equal(year.rows.length, 1);
  assert.equal(year.undated, 2);
  const sales = M.build(ctx(fx, 'sales', { range: 'all' }));
  assert.ok(sales.rows.every((r) => r.held === null), 'Robinhood trade records carry no open date');
});

test('a brushed range is a custom window and is not cross-checked against a preset span', () => {
  const fx = load('many');
  const m = M.build(ctx(fx, 'sales', { range: { from: '2026-03-01', to: '2026-03-31' } }));
  assert.ok(m.window.custom);
  assert.ok(m.rows.every((r) => r.ymd >= '2026-03-01' && r.ymd <= '2026-03-31'));
  assert.equal(m.check, undefined);
});

test('bucket bounds in UTC (as Robinhood sends them) become New York days', () => {
  assert.equal(M.wallFrom('2026-01-01T05:00:00Z', 'America/New_York'), '2026-01-01T00:00:00');
  assert.equal(M.wallFrom('2026-07-01T04:00:00Z', 'America/New_York'), '2026-07-01T00:00:00');
  // A trade at 00:30 New York time belongs to that day's bucket, not the previous one.
  const B = M.brokerSeries({ available: true, total: 5, buckets: [
    { start: '2026-01-01T05:00:00Z', end: '2026-01-02T05:00:00Z', gain: null, trades: 1 },
    { start: '2026-01-02T05:00:00Z', end: '2026-01-03T05:00:00Z', gain: 5, trades: 1 }] }, 'America/New_York');
  assert.deepEqual([B.from, B.to], ['2026-01-01T00:00:00', '2026-01-03T00:00:00']);
  assert.equal(B.noFigure[0].end, '2026-01-02T00:00:00');
});

test('URL state round-trips: range chip, brushed range, view and stocks', () => {
  const cfg = load('one_trade').records.config;
  const st = { range: '3month', view: 'buys', asset: 'crypto', symbols: ['SAA', 'SAB'] };
  assert.deepEqual(M.parseHash(`#holdings/activity/${M.hashOf(st)}`, cfg), st);
  const br = { range: { from: '2026-03-01', to: '2026-03-31' }, view: 'sales', asset: 'all', symbols: [] };
  assert.deepEqual(M.parseHash(`#holdings/activity/${M.hashOf(br)}`, cfg), br);
  assert.deepEqual(M.parseHash('#holdings/activity', cfg), { range: cfg.default_range, view: cfg.default_view, asset: 'all', symbols: [] });
  assert.deepEqual(M.parseHash('#holdings/activity/r=bogus&v=nope&s=<x>', cfg), { range: cfg.default_range, view: cfg.default_view, asset: 'all', symbols: [] });
});

test('the chart scale holds zero and steps on round numbers', () => {
  const s = M.scale([-37.2, 120.4, 88], 4);
  assert.ok(s.lo <= -37.2 && s.hi >= 120.4);
  assert.ok(s.ticks.includes(0));
  assert.ok([1, 2, 2.5, 5].some((m) => Math.abs(s.step / 10 ** Math.floor(Math.log10(s.step)) - m) < 1e-9));
  assert.equal(s.at(s.hi), 0);
  assert.equal(s.at(s.lo), 100);
  // All-positive figures (purchases): the axis starts at zero, no empty negative band.
  const up = M.scale([0, 120, 380000], 4);
  assert.equal(up.lo, 0);
  const down = M.scale([-5, -80], 4);
  assert.equal(down.hi, 0);
});


test('asset classes compose with every range and view, with one consistent aggregate dataset', () => {
  const fx = load('many');
  for (const key of ['closed_trades', 'fills', 'lots']) {
    fx.records[key].forEach((r, i) => { r.instrument_type = ['equity', 'etf', 'crypto', 'unknown'][i % 4]; });
  }
  for (const view of ['sales', 'buys', 'lots']) {
    for (const { key: range } of fx.records.config.ranges) {
      const all = M.build(ctx(fx, view, { range }));
      for (const asset of ['all', 'equity', 'crypto']) {
        const m = M.build(ctx(fx, view, { range, asset }));
        const wanted = all.rows.filter((r) => asset === 'all' || (asset === 'equity' ? ['equity', 'etf'].includes(r.instrument_type) : r.instrument_type === 'crypto'));
        assert.deepEqual(m.rows.map((r) => r.id), wanted.map((r) => r.id));
        const value = (r) => view === 'sales' ? r.realized : view === 'buys' ? r.amount : r.gain;
        near(m.total, wanted.reduce((n, r) => n + value(r), 0), 'filtered total');
        near(m.total, m.periods.reduce((n, p) => n + p.value, 0), 'chart');
        near(m.total, m.ranking.reduce((n, p) => n + p.value, 0), 'ranking');
        if (asset !== 'all') assert.equal(m.check, undefined);
        if (view === 'sales' && wanted.length) {
          const wins = wanted.filter((r) => r.realized > 0);
          near(m.kpis.winRate, wins.length / wanted.length, 'win rate');
          assert.equal(m.kpis.count, wanted.length);
          assert.equal(m.kpis.best.realized, Math.max(...wanted.map((r) => r.realized)));
          assert.equal(m.kpis.worst.realized, Math.min(...wanted.map((r) => r.realized)));
          const periods = new Map();
          wanted.forEach((r) => {
            const key = m.gran.key === 'month' ? r.ymd.slice(0, 7) : m.gran.key === 'week' ? M.weekStart(r.ymd) : r.ymd;
            periods.set(key, (periods.get(key) || 0) + r.realized);
          });
          assert.equal(m.kpis.periodsPositive, [...periods.values()].filter((v) => v > 0).length);
          if (wins.length) near(m.kpis.avgWin, wins.reduce((n, r) => n + r.realized, 0) / wins.length, 'average win');
        }
      }
    }
  }
});

test('untyped history is never silently classified as equity, including ETH', () => {
  const fx = load('one_trade');
  for (const key of ['closed_trades', 'fills', 'lots']) {
    fx.records[key].forEach((r) => { r.symbol = 'ETH'; delete r.instrument_type; });
  }
  assert.deepEqual(M.symbolsFor(fx.records, 'equity'), []);
  assert.deepEqual(M.symbolsFor(fx.records, 'crypto'), []);
  assert.deepEqual(M.symbolsFor(fx.records, 'all'), ['ETH']);
  for (const view of ['sales', 'buys', 'lots']) {
    const all = M.build(ctx(fx, view, { range: 'all' }));
    assert.ok(all.rows.length);
    assert.equal(all.unclassified, all.rows.length);
    assert.deepEqual(M.build(ctx(fx, view, { range: 'all', asset: 'equity' })).rows, []);
    assert.deepEqual(M.build(ctx(fx, view, { range: 'all', asset: 'crypto' })).rows, []);
  }
});

test('legacy equity history has an empty Crypto state in each view', () => {
  for (const view of ['sales', 'buys', 'lots']) {
    const fx = load('many');
    const m = M.build(ctx(fx, view, { range: 'all', asset: 'crypto' }));
    assert.equal(m.total, 0);
    assert.deepEqual(m.rows, []);
    assert.deepEqual(m.ranking, []);
  }
});

test('FIFO keeps instrument types and never matches same-symbol crypto to equity buys', () => {
  const fx = load('empty');
  fx.records.closed_trades = null;
  fx.records.fills = [
    { symbol: 'SAME', instrument_type: 'equity', side: 'buy', quantity: 1, price: 10, at: '2026-01-01T15:00:00Z' },
    { symbol: 'SAME', instrument_type: 'crypto', side: 'buy', quantity: 1, price: 20, at: '2026-01-02T15:00:00Z' },
    { symbol: 'SAME', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 30, at: '2026-01-03T15:00:00Z' },
  ];
  const m = M.build(ctx(fx, 'sales', { range: 'all', asset: 'crypto' }));
  assert.equal(m.total, 10);
  assert.equal(m.rows.length, 1);
  assert.equal(M.build(ctx(fx, 'sales', { range: 'all', asset: 'equity' })).rows.length, 0);
});


test('separate crypto executions join Activity without replacing broker equity closings', () => {
  const fx = load('one_trade');
  for (const key of ['closed_trades', 'fills', 'lots']) fx.records[key].forEach((r) => { r.instrument_type = 'equity'; });
  const before = M.build(ctx(fx, 'sales', { range: 'all' }));
  fx.records.crypto = { available: true, fills: [
    {symbol:'COIN', instrument_type:'crypto', side:'buy', quantity:2, price:10, at:'2026-09-01T15:00:00Z'},
    {symbol:'COIN', instrument_type:'crypto', side:'sell', quantity:1, price:15, at:'2026-09-20T15:00:00Z'},
    {symbol:'UNMATCHED', instrument_type:'crypto', side:'sell', quantity:1, price:100, at:'2026-09-20T15:00:00Z'},
  ]};
  const all = M.build(ctx(fx, 'sales', { range:'all' }));
  near(all.total, before.total + 5, 'all includes both sources');
  assert.equal(all.excluded.length, before.excluded.length + 1);
  const crypto = M.build(ctx(fx, 'sales', {range:'all', asset:'crypto'}));
  assert.equal(crypto.total, 5);
  assert.equal(crypto.rows[0].estimated, true);
  assert.equal(crypto.check, undefined);
  assert.equal(M.build(ctx(fx, 'sales', {range:'all', asset:'equity'})).total, before.total);
  assert.deepEqual(M.symbolsFor(fx.records, 'crypto'), ['COIN','UNMATCHED']);
  assert.ok(!M.symbolsFor(fx.records,'equity').includes('COIN'));
  const purchases = M.build(ctx(fx,'buys',{range:'all', asset:'crypto',symbols:['COIN']}));
  assert.equal(purchases.total,20);
  assert.equal(purchases.rows.length,1);
  assert.equal(M.build(ctx(fx,'lots',{range:'all', asset:'crypto'})).unavailable,true);
  fx.records.crypto = { available:false, reason:'Crypto history unavailable' };
  assert.equal(M.build(ctx(fx,'sales',{range:'all', asset:'crypto'})).reason,'Crypto history unavailable');
});

// Regression: once the server classified a few broker trades as crypto, every
// other crypto sale (estimated from the crypto executions) vanished from Activity.
test('a broker crypto record replaces only its own sale; every other crypto sale is still estimated', () => {
  const fx = load('one_trade');
  for (const key of ['closed_trades', 'fills', 'lots']) fx.records[key].forEach((r) => { r.instrument_type = 'equity'; });
  fx.records.config.evidence_window_s = 60;
  fx.records.crypto = { available: true, fills: [
    { symbol: 'COIN', instrument_type: 'crypto', side: 'buy', quantity: 4, price: 10, at: '2026-08-01T15:00:00Z' },
    { symbol: 'COIN', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 15, at: '2026-08-20T15:00:00Z' },
    { symbol: 'COIN', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 18, at: '2026-09-02T15:00:00Z' },
    { symbol: 'TOKN', instrument_type: 'crypto', side: 'buy', quantity: 2, price: 5, at: '2026-08-01T16:00:00Z' },
    { symbol: 'TOKN', instrument_type: 'crypto', side: 'sell', quantity: 2, price: 4, at: '2026-09-10T16:00:00Z' },
  ] };
  // Robinhood reports the first COIN sale itself, stamped 20 seconds after the execution.
  fx.records.closed_trades.push({ symbol: 'COIN', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 15, realized_gain: 4.9, at: '2026-08-20T15:00:20Z' });
  const m = M.build(ctx(fx, 'sales', { range: 'all', asset: 'crypto' }));
  assert.equal(m.rows.length, 3, 'one broker record and two estimates, nothing counted twice');
  assert.equal(m.rows.filter((r) => r.estimated).length, 2);
  near(m.total, 4.9 + 8 + -2, "the broker's figure for its sale, FIFO for the rest");
  assert.deepEqual([...new Set(m.rows.map((r) => r.symbol))].sort(), ['COIN', 'TOKN']);
  // Outside the evidence window the broker record is a different sale.
  fx.records.config.evidence_window_s = 10;
  assert.equal(M.build(ctx(fx, 'sales', { range: 'all', asset: 'crypto' })).rows.length, 4);
});

test("Robinhood's total is compared with its own records only, even when crypto is estimated", () => {
  const fx = load('one_trade');
  const before = M.build(ctx(fx, 'sales'));
  assert.ok(before.check, 'the fixture is cross-checked before crypto is added');
  fx.records.crypto = { available: true, fills: [
    { symbol: 'COIN', instrument_type: 'crypto', side: 'buy', quantity: 1, price: 10, at: `${before.window.from}T15:00:00Z` },
    { symbol: 'COIN', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 30, at: `${before.window.to}T15:00:00Z` },
  ] };
  const after = M.build(ctx(fx, 'sales'));
  assert.ok(after.rows.some((r) => r.estimated));
  assert.ok(after.check, 'still cross-checked');
  near(after.check.records, before.check.records, 'the estimate stays out of the comparison');
  assert.equal(after.check.matches, before.check.matches);
});
