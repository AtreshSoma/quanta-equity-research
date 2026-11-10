/**
 * The Research header's "today" change is measured from the right close.
 *
 * MSFT, 2026-11-09 after the close: the quote was $545.18 (Nov 9) and the daily
 * bars ended Nov 6 at $530.61. The page took the bar before the last (Nov 5,
 * $535.93) as the previous close and printed +1.73% for a +2.75% session.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/live_day_change.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');

const DAY = 86400000;
const NOV4 = Date.UTC(2026, 10, 4, 5);          // daily bars are stamped at session midnight, New York
const bars = [[NOV4, 555.8], [NOV4 + DAY, 535.93], [NOV4 + 2 * DAY, 530.61]];   // Nov 4, 5, 6 (Fri)
const NOV9_CLOSE = Date.UTC(2026, 10, 9, 21);   // Monday 16:00 New York

test('daily bars a session short of the quote: the last bar is the previous close', () => {
  const prev = app.livePreviousClose({ interval: '1d', points: bars, regular_price: 545.18, regular_time: NOV9_CLOSE });
  assert.equal(prev, 530.61);
  assert.equal((((545.18 - prev) / prev) * 100).toFixed(2), '2.75');
});

test('the last bar is the quote\'s own session: the bar before it is the previous close', () => {
  const withToday = [...bars, [NOV4 + 5 * DAY, 545.18]];                          // Nov 9 bar present
  assert.equal(app.livePreviousClose({ interval: '1d', points: withToday, regular_time: NOV9_CLOSE }), 530.61);
  // During the session too (10:00 New York on the bar's own day).
  assert.equal(app.livePreviousClose({ interval: '1d', points: withToday, regular_time: NOV4 + 5 * DAY + 10 * 3600000 }), 530.61);
});

test('a previous close the server states is used as given', () => {
  assert.equal(app.livePreviousClose({ interval: '30m', previous_close: 529.5, points: bars, regular_time: NOV9_CLOSE }), 529.5);
});

test('no figure is invented without a usable reference', () => {
  assert.ok(Number.isNaN(app.livePreviousClose({ interval: '30m', points: bars })), 'intraday bars with no stated close');
  assert.ok(Number.isNaN(app.livePreviousClose({ interval: '1d', points: [] })));
  assert.ok(Number.isNaN(app.livePreviousClose({ interval: '1d', points: [[NOV4, 555.8]], regular_time: NOV4 + 3600000 })), 'one bar, same session');
  assert.ok(Number.isNaN(app.livePreviousClose(null)));
});

test('an unknown quote time keeps the earlier reading (the bar before the last)', () => {
  assert.equal(app.livePreviousClose({ interval: '1d', points: bars }), 535.93);
});
