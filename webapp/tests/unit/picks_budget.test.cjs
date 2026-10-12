/**
 * A10: each market keeps its own capital budget across market switches, and a
 * safe-mode retry re-sends the interrupted scan's parameters (only safe_mode
 * changes) instead of silently switching to a Quick scan.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_budget.test.cjs
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
global.requestAnimationFrame = () => 0;
global.CSS = { escape: (s) => String(s) };
global.window = undefined;
const app = require('../../static/app.js');
const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));

const $ = (s) => document.querySelector(s);
const cap = () => $('#sp-capital');
function switchTo(m) { $('#sp-market').value = m; app.applyPicksMarketUI(m); }

test('a large valid US budget survives a round trip through India', () => {
  switchTo('US');
  const big = String(T.CAPITAL.MIN_INDIA * 5);          // above the India minimum, valid in USD
  cap().value = big;
  switchTo('IN');
  assert.equal(cap().value, String(T.CAPITAL.DEFAULT_INDIA));
  switchTo('US');
  assert.equal(cap().value, big);
});

test('each market remembers the budget typed for it', () => {
  switchTo('US');
  cap().value = '3000';
  switchTo('IN');
  cap().value = '500000';
  switchTo('US');
  assert.equal(cap().value, '3000');
  switchTo('IN');
  assert.equal(cap().value, '500000');
  switchTo('US');
});

test('safe-mode retry re-sends the interrupted scan unchanged except safe_mode', async () => {
  const bodies = [];
  global.fetch = async (url, init) => {
    if (String(url).includes('/api/stock-picks')) bodies.push(JSON.parse(init.body));
    return { status: 500, ok: false, json: async () => ({ error: 'synthetic failure' }) };
  };
  switchTo('US');
  cap().value = '4000';
  $('#sp-scan').value = 'standard';
  await app.runScan(null);
  cap().value = '9999';                                   // edited after the run: not the retried scan
  await app.retrySafeMode();
  assert.equal(bodies.length, 2);
  const [first, retry] = bodies;
  assert.equal(retry.safe_mode, true);
  assert.equal(first.safe_mode, false);
  for (const k of ['market', 'capital', 'scan_type', 'risk_profile', 'max_positions', 'diversify']) {
    assert.deepEqual(retry[k], first[k], k);
  }
  assert.equal(retry.scan_type, 'standard');
});

test('the throttle message describes what safe mode changes', () => {
  const msg = app.scanStatusMessage({ scan_status: 'provider_rate_limited', provider_rate_limited_count: 3,
    analysis_failed_count: 3, error_summary: { universe_size: 100 } });
  assert.match(msg.body, /same scan, lower concurrency/i);
  assert.doesNotMatch(msg.body, /smaller universe/i);
});

test('A12: before an India scan the page says no Buy or Accumulate call is possible, from config', () => {
  const notice = () => document.querySelector('#sp-market-notice');
  switchTo('IN');
  assert.equal(notice().hidden, false);
  const exchanges = Object.values(T.EVENTS.UNCOVERED_LISTINGS);
  for (const ex of exchanges) assert.match(notice().textContent, new RegExp(ex));
  // Styling pass: one line, the reason behind a "Why" disclosure.
  assert.match(notice().querySelector('.q-notice-t').textContent, /^Watchlist calls only for .* listings$/);
  assert.match(notice().querySelector('.q-notice-body').textContent, /^Before a name gets new money, QUANTA checks it for a pending merger or delisting, and that check has no .* source yet\.$/);
  switchTo('US');
  assert.equal(notice().hidden, true);
  const saved = globalThis.__QUANTA_THRESHOLDS__.EVENTS.UNCOVERED_LISTINGS;
  globalThis.__QUANTA_THRESHOLDS__.EVENTS.UNCOVERED_LISTINGS = {};
  try {
    switchTo('IN');
    assert.equal(notice().hidden, true, 'no uncovered listing in config, no notice');
  } finally {
    globalThis.__QUANTA_THRESHOLDS__.EVENTS.UNCOVERED_LISTINGS = saved;
    switchTo('US');
  }
});
