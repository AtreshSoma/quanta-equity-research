/**
 * "Opened at the Session Extreme" board + market-switch hygiene.
 *
 *  - The tab renders two boards in the Market Moves format, from the lists the
 *    backend selected. The frontend must not re-derive the match itself: it is
 *    handed 8 rows per side and the backend scanned hundreds.
 *  - An empty board says what was scanned, so a quiet tape does not read as a
 *    broken panel.
 *  - Switching country blanks EVERY country-specific panel. Leaving US
 *    headlines above an Indian position list is not a slow update; it is the
 *    wrong country's data presented as this one's.
 *
 * Run: node --test tests/unit/open_extremes.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const app = require('../../static/app.js');

const AT_LOW = [
  { symbol: 'BLTE', name: 'Belite Bio, Inc', price: 193.61, change_pct: 13.16, change_abs: 22.52,
    open: 172.77, day_high: 193.88, day_low: 172.77, prev_close: 171.09 },
  { symbol: 'ALAB', name: 'Astera Labs, Inc.', price: 310.40, change_pct: 9.75, change_abs: 27.58,
    open: 285.84, day_high: 321.65, day_low: 285.84, prev_close: 282.82 },
];
const AT_HIGH = [
  { symbol: 'FADE', name: 'Fade Corp', price: 51.20, change_pct: -8.4, change_abs: -4.7,
    open: 60.00, day_high: 60.00, day_low: 51.20, prev_close: 55.90 },
];

test('both boards render in the Market Moves column format', () => {
  app.renderOpenExtremes({ open_at_low: AT_LOW, open_at_high: AT_HIGH, open_extremes_scanned: 175 });
  const body = document.querySelector('#open-extremes-body');
  const tables = body.querySelectorAll('table.q-board-table');
  assert.equal(tables.length, 2, 'one board per condition');

  const cols = Array.from(tables[0].querySelectorAll('thead th')).map((n) => n.textContent.trim().split(/\s+/)[0]);
  assert.deepEqual(cols, ['Symbol', 'Open', 'High', 'Low', 'Prev.', 'Last'],
    'same columns as the movers board in the reference screenshot');

  assert.match(body.textContent, /BLTE/);
  assert.match(body.textContent, /ALAB/);
  assert.match(body.textContent, /FADE/);
});

test('each board is captioned for its own condition', () => {
  app.renderOpenExtremes({ open_at_low: AT_LOW, open_at_high: AT_HIGH, open_extremes_scanned: 175 });
  const caps = Array.from(document.querySelectorAll('#open-extremes-body .q-board-cap'))
    .map((n) => n.textContent.trim());
  assert.match(caps[0], /Opened at the low/);
  assert.match(caps[1], /Opened at the high/);
  // The old renderer captioned anything that was not "gainers" as "Top losers".
  assert.ok(!caps.some((c) => /Top losers|Top gainers/.test(c)), 'no mislabelled board');
});

test('the row shows the two prices that made it qualify', () => {
  app.renderOpenExtremes({ open_at_low: AT_LOW, open_at_high: [], open_extremes_scanned: 175 });
  const row = document.querySelector('#open-extremes-body tbody tr');
  const cells = Array.from(row.querySelectorAll('td')).map((n) => n.textContent.trim());
  assert.match(cells[1], /172\.77/, 'open');
  assert.match(cells[3], /172\.77/, 'low equals the open, visibly');
});

test('the meta line reports both counts and the pool scanned', () => {
  app.renderOpenExtremes({ open_at_low: AT_LOW, open_at_high: AT_HIGH, open_extremes_scanned: 175 });
  const meta = document.querySelector('#open-extremes-meta').textContent;
  assert.match(meta, /2 at low/);
  assert.match(meta, /1 at high/);
  assert.match(meta, /175 scanned/);
});

test('all matches remain available beyond the old eight-row limit', () => {
  const rows = Array.from({ length: 15 }, (_, i) => ({ ...AT_LOW[0], symbol: `MATCH${i}`, change_pct: i }));
  app.renderOpenExtremes({ open_at_low: rows, open_at_high: [], open_extremes_scanned: 49,
    open_extremes_coverage: { comparable: 45, missing_ohlc: 2, invalid_ohlc: 1, flat: 1, tracked_universe: 51 } });
  const body = document.querySelector('#open-extremes-body');
  assert.equal(body.querySelectorAll('.q-board-row').length, 15);
  assert.match(body.querySelector('.q-coverage').textContent, /15 exact matches from 49 fetched quotes/);
  assert.match(body.textContent, /not the whole exchange/);
  assert.match(body.textContent, /Comparable OHLC45/);
  assert.match(body.textContent, /No matching quotes in this snapshot/);
  assert.match(body.querySelector('.q-board-row').textContent, /MATCH14/);
  const sort = body.querySelector('[data-board-sort="symbol"]');
  sort.click();
  assert.equal(body.querySelectorAll('.q-board-row').length, 15, 'sorting cannot reintroduce the cap');
  assert.equal(body.querySelector('th').getAttribute('aria-sort'), 'descending');
});

test('an empty board states what was examined, not a bare "no data"', () => {
  app.renderOpenExtremes({ open_at_low: [], open_at_high: [], open_extremes_scanned: 175 });
  const body = document.querySelector('#open-extremes-body');
  assert.match(body.textContent, /No stock opened exactly at its session extreme/);
  assert.match(body.textContent, /Checked 175 quotes/, 'the pool size is stated');
  assert.equal(body.querySelectorAll('table').length, 0, 'no empty table shell');
});

test('a feed with no OHLC says so rather than blaming the market', () => {
  app.renderOpenExtremes({ open_at_low: [], open_at_high: [], open_extremes_scanned: 0 });
  assert.match(document.querySelector('#open-extremes-body').textContent,
    /no quotes carried a session open, high and low/i);
});

test('the loading state is a skeleton, not an empty verdict', () => {
  app.renderOpenExtremes({ loading: true });
  const body = document.querySelector('#open-extremes-body');
  assert.ok(body.querySelector('.q-skelrows'), 'skeleton while warming up');
  assert.doesNotMatch(body.textContent, /No stock opened/, 'never a verdict before the data lands');
});

test('the tab is wired to the panel it names', () => {
  const tab = document.querySelector('#tab-open-extremes');
  assert.ok(tab, 'the tab exists');
  assert.equal(tab.getAttribute('aria-controls'), 'open-extremes');
  assert.ok(document.querySelector('#open-extremes'), 'the panel exists');
  assert.ok(document.querySelector('#open-extremes #open-extremes-body'), 'body lives inside it');
});

test('switching country blanks every country-specific panel', () => {
  // Seed each panel with content that belongs to the market being left.
  const ids = ['top-movers-body', 'open-extremes-body', 'event-risk-body', 'news-body',
    'top-headlines', 'signal-check-body', 'market-drivers-body', 'breadth-body',
    'portfolio-scan-body', 'setup-status-body'];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    assert.ok(el, `${id} exists in the template`);
    el.innerHTML = '<b>Fed holds rates steady — S&P 500 hits record</b>';
  });

  app.clearMarketDependentPanels('IN');

  ids.forEach((id) => {
    const text = document.getElementById(id).textContent;
    assert.doesNotMatch(text, /Fed holds rates|S&P 500/, `${id} still shows the previous market`);
    assert.match(text, /Loading India data/, `${id} says which market is loading`);
  });
});

test('the loading notice names the market being switched to', () => {
  app.clearMarketDependentPanels('US');
  assert.match(document.getElementById('news-body').textContent, /Loading United States data/);
});
