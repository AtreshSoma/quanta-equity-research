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
