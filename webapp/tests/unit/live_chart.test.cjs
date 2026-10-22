// Axis maths of the live price chart (static/live-chart.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const lc = require('../../static/live-chart.js');

test('price ticks are round, evenly spaced and cover every value', () => {
  const values = [331.59, 336.67, 333.1, 340.42];
  const y = lc.yModel(values);
  assert.ok(y.y0 <= Math.min(...values) && y.y1 >= Math.max(...values));
  const steps = y.ticks.slice(1).map((v, i) => +(v - y.ticks[i]).toFixed(6));
  assert.ok(steps.every((s) => s === steps[0]), 'even spacing');
  assert.ok([1, 2, 2.5, 5].some((m) => Math.abs(steps[0] / 10 ** Math.floor(Math.log10(steps[0])) - m) < 1e-9), `round step ${steps[0]}`);
});

test('the 1D axis spans the whole session, pre-market start to post-market end', () => {
  const pre0 = Date.parse('2026-10-09T08:00:00Z'); // 4:00 AM New York
  const post1 = Date.parse('2026-10-10T00:00:00Z'); // 8:00 PM New York
  const data = { range: '1d', points: [[Date.parse('2026-10-09T13:30:00Z'), 1, 0], [Date.parse('2026-10-09T14:00:00Z'), 2, 0]],
    session: { pre: [pre0, Date.parse('2026-10-09T13:30:00Z')], regular: [Date.parse('2026-10-09T13:30:00Z'), Date.parse('2026-10-09T20:00:00Z')], post: [Date.parse('2026-10-09T20:00:00Z'), post1] } };
  const xm = lc.xModel(data, lc.zoned('America/New_York'));
  assert.equal(Math.round(xm.xt(pre0)), 0);
  assert.equal(Math.round(xm.xt(post1)), 1000);
  assert.equal(xm.ticks[0].label, '4 AM');
  assert.equal(xm.ext.length, 2, 'pre- and post-market are shaded');
});

test('hour labels land on local whole hours in a zone offset by half an hour', () => {
  const t0 = Date.parse('2026-10-09T03:45:00Z'); // 9:15 AM India
  const t1 = Date.parse('2026-10-09T10:00:00Z'); // 3:30 PM India
  const data = { range: '1d', points: [[t0, 1, 0], [t1, 2, 0]], session: { regular: [t0, t1] } };
  const xm = lc.xModel(data, lc.zoned('Asia/Kolkata'));
  assert.deepEqual(xm.ticks.map((t) => t.label), ['10 AM', '11 AM', '12 PM', '1 PM', '2 PM', '3 PM']);
});

test('an index is a level, not a price, and volatility reads inversely', () => {
  assert.equal(lc.isLevel({ symbol: '^VIX', currency: 'USD' }), true, 'no "$" on the VIX');
  assert.equal(lc.isLevel({ symbol: 'X', instrument_type: 'INDEX' }), true);
  assert.equal(lc.isLevel({ symbol: 'SPY', currency: 'USD', instrument_type: 'ETF' }), false);
  assert.equal(lc.isInverse({ symbol: '^VIX' }), true);
  assert.equal(lc.isInverse({ symbol: '^INDIAVIX' }), true);
  assert.equal(lc.isInverse({ symbol: 'VIXY' }), false, 'a VIX ETF trades like any other price');
});

// ---- Studies, warm-up, compare and live patching -------------------------------

const { sma, ema, bollinger, rsi, macd, vwap } = lc.indicators;
const close = (v, d) => assert.ok(Math.abs(v - d) < 1e-9, `${v} vs ${d}`);

test('a simple average is empty until its window is full, then the plain mean', () => {
  const out = sma([1, 2, 3, 4, 5], 3);
  assert.deepEqual(out.slice(0, 2), [null, null]);
  close(out[2], 2); close(out[3], 3); close(out[4], 4);
});

test('an exponential average seeds on the simple mean and then weights 2 / (n + 1)', () => {
  const out = ema([2, 4, 6, 8], 3);
  assert.equal(out[1], null);
  close(out[2], 4);
  close(out[3], 8 * 0.5 + 4 * 0.5);
});

test('Bollinger bands sit two population deviations either side of the average', () => {
  const b = bollinger([1, 2, 3, 4, 5], 5, 2);
  close(b.mid[4], 3);
  close(b.upper[4] - b.mid[4], 2 * Math.sqrt(2));
  close(b.mid[4] - b.lower[4], 2 * Math.sqrt(2));
});

test('RSI is 100 on a run of gains and about 50 when gains and losses balance', () => {
  const up = rsi(Array.from({ length: 20 }, (_, i) => 10 + i), 14);
  assert.equal(up[13], null);
  close(up[14], 100);
  const flip = rsi(Array.from({ length: 40 }, (_, i) => 10 + (i % 2)), 14);
  assert.ok(Math.abs(flip[39] - 50) < 5, `balanced RSI ${flip[39]}`);
});

test('MACD is fast minus slow, its histogram the line minus the signal', () => {
  const vals = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 4) * 5 + i * 0.2);
  const m = macd(vals);
  const f = ema(vals, 12); const s = ema(vals, 26);
  close(m.line[40], f[40] - s[40]);
  close(m.hist[59], m.line[59] - m.signal[59]);
  assert.equal(m.signal[30], null, 'the signal needs nine MACD values');
});

test('VWAP restarts with each session day', () => {
  const day = (t) => new Date(t).toISOString().slice(0, 10);
  const t0 = Date.parse('2026-10-20T14:00:00Z');
  const bars = [[t0, 10, 100, 10, 10, 10], [t0 + 60000, 20, 100, 20, 20, 20], [t0 + 86400000, 50, 10, 50, 50, 50]];
  const out = vwap(bars, day);
  close(out[1], 15);
  close(out[2], 50);
});

test('warm-up bars make a 200-bar average right from the first plotted bar', () => {
  const t0 = Date.parse('2025-01-02T05:00:00Z');
  const bar = (i) => [t0 + i * 86400000, 100 + i, 1000, 100 + i, 101 + i, 99 + i];
  const lead = Array.from({ length: 250 }, (_, i) => bar(i));
  const points = Array.from({ length: 30 }, (_, i) => bar(250 + i));
  const s = lc.computeSeries({ range: '1y', lead, points }, lc.zoned('America/New_York'));
  assert.equal(s.sma200.length, 30);
  // Mean of closes 100+51 .. 100+250 = 100 + 150.5
  close(s.sma200[0], 250.5);
  const cold = lc.computeSeries({ range: '1y', lead: [], points }, lc.zoned('America/New_York'));
  assert.equal(cold.sma200[0], null, 'without warm-up the average would start empty');
});

test('the benchmark aligns to each bar at or before it, and only Indian listings use the Nifty', () => {
  const h = 3600000;
  // Feeds stamp the same minute a few seconds apart, so up to a minute counts as the same bar.
  const pts = [[0, 1], [2 * h, 1], [4 * h, 1]];
  const bench = { points: [[-h, 50], [3 * h, 55]] };
  assert.deepEqual(lc.alignBenchmark(pts, bench), [50, 50, 55]);
  assert.equal(lc.benchmarkFor('AAPL'), 'SPY');
  assert.equal(lc.benchmarkFor('RELIANCE.NS'), '^NSEI');
  assert.equal(lc.benchmarkFor('TCS.BO'), '^NSEI');
  assert.equal(lc.benchmarkFor('SPY'), null);
  assert.equal(lc.benchmarkFor('^VIX'), null);
});

test('a live quote updates today\'s daily bar, or opens the next one', () => {
  const day1 = Date.parse('2026-10-21T04:00:00Z');
  const data = { interval: '1d', timezone: 'America/New_York', points: [[day1, 100, 10, 98, 101, 97]] };
  const sameDay = { regular_price: 103, regular_time: Date.parse('2026-10-21T18:00:00Z'), market_open: true, points: [] };
  assert.equal(lc.patchLastBar(data, sameDay), true);
  assert.deepEqual(data.points[0].slice(1), [103, 10, 98, 103, 97]);
  const nextDay = {
    regular_price: 104, regular_time: Date.parse('2026-10-22T15:00:00Z'), market_open: true,
    session: { regular: [Date.parse('2026-10-22T13:30:00Z'), Date.parse('2026-10-22T20:00:00Z')] },
    points: [[Date.parse('2026-10-22T13:30:00Z'), 102, 5, 101.5, 102.5, 101], [Date.parse('2026-10-22T14:59:00Z'), 104, 7, 103, 105, 102.8]],
  };
  assert.equal(lc.patchLastBar(data, nextDay), true);
  assert.equal(data.points.length, 2);
  assert.deepEqual(data.points[1].slice(1), [104, 12, 101.5, 105, 101]);
  assert.equal(lc.patchLastBar(data, { ...nextDay, regular_price: 104 }), false, 'no change, no redraw');
});

test('a one-minute print far above the session\'s volume is flagged and left out of VWAP', () => {
  const day = () => 'd';
  const t0 = Date.parse('2026-10-22T14:00:00Z');
  const bars = Array.from({ length: 30 }, (_, i) => [t0 + i * 60000, 100, 50000, 100, 100, 100]);
  bars[10] = [t0 + 600000, 120, 15000000, 120, 120, 120];
  const odd = lc.indicators.volumeOutliers(bars);
  assert.deepEqual([...odd], [10]);
  close(lc.indicators.vwap(bars, day, odd)[29], 100);
  assert.ok(lc.indicators.vwap(bars, day)[29] > 115, 'unfiltered, one print drags VWAP to its price');
});
