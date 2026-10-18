/**
 * Recent scans: reopen a scan from the last day without running it again.
 * The server side (store, expiry, endpoints) is tested in tests/test_saved_scans.py
 * and tests/test_recent_scans_api.py; this covers the switcher on the page.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/recent_scans.test.cjs
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
global.window = undefined;
const app = require('../../static/app.js');
const { payload } = require('../fixtures/picks_calls.cjs');

const T = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
const $ = (sel) => document.querySelector(sel);
const NOW = Date.parse('2026-10-15T15:00:00Z');
const ago = (min) => new Date(NOW - min * 60000).toISOString();

const scan = (id, scan_type, minutes, extra = {}) => ({
  id, saved_at: ago(minutes), market: 'US', scan_type, capital: 2500, risk_profile: 'balanced', max_positions: 5,
  diversify: true, has_filters: false, custom_count: 0, scanned: 250, buy_accumulate: 1, watchlist: 2, funded: 1,
  regime: 'BULL', ...extra,
});
const LIST = () => [scan('aaaaaaaaaaaa', 'standard', 5), scan('bbbbbbbbbbbb', 'focused_growth', 125), scan('cccccccccccc', 'focused_etf', 600)];

const reset = () => {
  global.fetch = undefined;
  $('#picks-results').innerHTML = '';
  const box = $('#pk-recent');
  box.innerHTML = ''; box.hidden = true;
  $('#btn-scan').disabled = false;
  const sectors = $('#sp-sectors');
  sectors.innerHTML = ['Energy', 'Technology'].map((s) => `<label><input type="checkbox" value="${s}"></label>`).join('');
  app.state && Object.assign(app.state, { recentScans: [], picksViewId: null, picks: null });
};
const render = (data) => { global.window = {}; try { app.renderPicks(data); } finally { global.window = undefined; } };
const respond = (routes) => {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(String(url));
    const hit = Object.entries(routes).find(([k]) => String(url).includes(k));
    const [status, body] = hit ? hit[1] : [404, { error: 'none' }];
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
  return calls;
};

test('ages read as just now, minutes, then hours', () => {
  assert.equal(app.savedScanAge(ago(0), NOW), 'just now');
  assert.equal(app.savedScanAge(ago(12), NOW), '12m ago');
  assert.equal(app.savedScanAge(ago(125), NOW), '2h ago');
  assert.equal(app.savedScanAge('not a date', NOW), '');
});

test('one saved scan leaves nothing to switch between, so nothing is drawn', () => {
  assert.equal(app.recentScansHtml([], null, NOW), '');
  assert.equal(app.recentScansHtml([scan('aaaaaaaaaaaa', 'standard', 5)], null, NOW), '');
  assert.equal(app.recentScansHtml(null, null, NOW), '');
});

test('each saved scan is a chip, newest first, with the one on screen marked', () => {
  reset();
  const box = $('#pk-recent');
  box.innerHTML = app.recentScansHtml(LIST(), 'bbbbbbbbbbbb', NOW);
  const chips = [...box.querySelectorAll('[data-pk-recent]')];
  assert.deepEqual(chips.map((c) => c.dataset.pkRecent), ['aaaaaaaaaaaa', 'bbbbbbbbbbbb', 'cccccccccccc']);
  assert.deepEqual(chips.map((c) => T(c)), ['Standard 5m ago', 'Growth Leaders 2h ago', 'ETF Sleeve 10h ago']);
  assert.deepEqual(chips.map((c) => c.getAttribute('aria-pressed')), ['false', 'true', 'false']);
  assert.match(chips[0].title, /Standard, \$2,500, Balanced mandate, up to 5 positions, 1 Buy or Accumulate, 2 Watchlist/);
  assert.equal(box.querySelector('nav').getAttribute('aria-label'), 'Recent scans');
});

test('two scans of one universe say how they differ', () => {
  const twins = [scan('aaaaaaaaaaaa', 'standard', 5), scan('bbbbbbbbbbbb', 'standard', 30, { capital: 10000, risk_profile: 'conservative' })];
  const box = $('#pk-recent');
  box.innerHTML = app.recentScansHtml(twins, null, NOW);
  assert.deepEqual([...box.querySelectorAll('[data-pk-recent]')].map((c) => T(c)),
    ['Standard $2,500, Balanced · 5m ago', 'Standard $10,000, Conservative · 30m ago']);
});

test('a scan from before the last close is marked, and a custom list shows its size', () => {
  const box = $('#pk-recent');
  box.innerHTML = app.recentScansHtml([scan('aaaaaaaaaaaa', 'custom', 5, { custom_count: 3 }),
    scan('bbbbbbbbbbbb', 'standard', 700, { closed_since: true, as_of_session: '2026-10-14' })], null, NOW);
  const [custom, old] = [...box.querySelectorAll('[data-pk-recent]')];
  assert.equal(T(custom.querySelector('b')), 'Custom (3)');
  assert.ok(old.classList.contains('is-stale'));
  assert.match(old.title, /prices from before the last market close/);
});

test('refreshRecentScans reads the list for the market and marks the newest when a scan just ended', async () => {
  reset();
  const calls = respond({ '/api/stock-picks/recent?market=US': [200, { retention_hours: 24, scans: LIST() }] });
  await app.refreshRecentScans('US', { selectNewest: true });
  assert.deepEqual(calls, ['/api/stock-picks/recent?market=US']);
  assert.equal($('#pk-recent').hidden, false);
  assert.equal($('#pk-recent [aria-pressed="true"]').dataset.pkRecent, 'aaaaaaaaaaaa');
});

test('a market that changed while the list was loading is ignored', async () => {
  reset();
  respond({ '/api/stock-picks/recent?market=IN': [200, { scans: LIST() }] });
  await app.refreshRecentScans('IN');                 // the form says US
  assert.equal($('#pk-recent').hidden, true);
});

test('opening a scan shows its report, restores its settings and says it is a snapshot', async () => {
  reset();
  const growth = payload({ scan_type: 'focused_growth', capital: 10000 });
  const calls = respond({
    '/api/stock-picks/recent/bbbbbbbbbbbb?market=US': [200, {
      id: 'bbbbbbbbbbbb', saved_at: ago(125), closed_since: true, as_of_session: '2026-10-14',
      params: { market: 'US', scan_type: 'focused_growth', capital: 10000, risk_profile: 'conservative', max_positions: 3,
        diversify: false, filters: { sectors: ['Energy'], min_market_cap: 2e9, min_price: 20 } },
      result: growth }],
    '/api/stock-picks/recent?market=US': [200, { scans: LIST() }],
  });
  await app.refreshRecentScans('US');
  assert.equal(await app.openRecentScan('bbbbbbbbbbbb'), true);
  assert.ok(calls.some((c) => c.includes('/recent/bbbbbbbbbbbb?market=US')));
  // The report is on screen, behind a snapshot note that names the close.
  assert.ok($('#picks-results [data-picks-v2]'));
  assert.match(T($('#picks-results .picks-restored')), /^Showing a saved scan from .+\. Its prices are from the .+ close and the market has closed since: run a new scan before acting on it\.$/);
  assert.match(T($('#picks-meta')), /^Saved scan · /);
  // The form describes this scan, so Run Picks scan refreshes exactly it.
  assert.equal($('#sp-scan').value, 'focused_growth');
  assert.equal($('#sp-capital').value, '10000');
  assert.equal($('#sp-risk').value, 'conservative');
  assert.equal($('#sp-positions').value, '3');
  assert.equal($('#sp-diversify').checked, false);
  assert.deepEqual([...document.querySelectorAll('#sp-sectors input')].map((i) => i.checked), [true, false]);
  assert.equal($('#sp-f-capmin').value, '2', 'raw dollars come back in the field\'s own unit ($B)');
  assert.equal($('#sp-f-pmin').value, '20');
  assert.match(T($('#sp-filters-count')), /3 set/);
  // And that chip is the one marked.
  assert.equal($('#pk-recent [aria-pressed="true"]').dataset.pkRecent, 'bbbbbbbbbbbb');
});

test('a saved scan from after the close gets the plain "may have moved" note', async () => {
  reset();
  respond({ '/api/stock-picks/recent/aaaaaaaaaaaa?market=US': [200, {
    id: 'aaaaaaaaaaaa', saved_at: ago(5), closed_since: false, as_of_session: '2026-10-15',
    params: { market: 'US', scan_type: 'standard', capital: 2500, risk_profile: 'balanced', max_positions: 5 }, result: payload() }] });
  await app.openRecentScan('aaaaaaaaaaaa');
  assert.match(T($('#picks-results .picks-restored')), /Prices and calls may have moved since: run a new scan before acting on it\.$/);
});

test('an expired scan says so and the list is re-read', async () => {
  reset();
  const calls = respond({
    '/api/stock-picks/recent/dddddddddddd': [404, { error: 'gone' }],
    '/api/stock-picks/recent?market=US': [200, { scans: LIST().slice(0, 2) }],
  });
  $('#pk-recent').innerHTML = app.recentScansHtml(LIST(), null, NOW);
  assert.equal(await app.openRecentScan('dddddddddddd'), false);
  assert.ok(calls.some((c) => c === '/api/stock-picks/recent?market=US'));
  assert.match(T($('#pk-recent [data-pk-recent-note]')), /^That scan has expired\. Run it again to bring it back\.$/);
  assert.equal($('#picks-results').innerHTML.trim(), '', 'the report on screen was left alone');
});

test('a scan that is running wins: switching waits, and nothing is fetched', async () => {
  reset();
  $('#pk-recent').innerHTML = app.recentScansHtml(LIST(), null, NOW);
  const calls = respond({});
  $('#btn-scan').disabled = true;
  assert.equal(await app.openRecentScan('aaaaaaaaaaaa'), false);
  assert.deepEqual(calls, []);
  assert.match(T($('#pk-recent [data-pk-recent-note]')), /^A scan is running\./);
});

test('a failed request leaves the page as it was', async () => {
  reset();
  $('#pk-recent').innerHTML = app.recentScansHtml(LIST(), null, NOW);
  global.fetch = async () => { throw new Error('offline'); };
  assert.equal(await app.openRecentScan('aaaaaaaaaaaa'), false);
  assert.match(T($('#pk-recent [data-pk-recent-note]')), /could not be opened/);
});

test('the switcher sits above the report and is hidden until there is something to switch', () => {
  const box = $('#pk-recent');
  assert.ok(box, 'the container exists');
  assert.equal(box.nextElementSibling.id, 'picks-results');
});

test('reopening the newest scan on page load also restores its settings', async () => {
  reset();
  $('#sp-scan').value = 'standard';
  respond({
    '/api/stock-picks/last?market=US': [200, { id: 'aaaaaaaaaaaa', saved_at: ago(5), closed_since: false,
      params: { market: 'US', scan_type: 'focused_etf', capital: 4000, risk_profile: 'balanced', max_positions: 4 },
      result: payload({ scan_type: 'focused_etf' }) }],
    '/api/stock-picks/recent?market=US': [200, { scans: LIST() }],
  });
  assert.equal(await app.restoreLastScan('US'), true);
  assert.equal($('#sp-scan').value, 'focused_etf', 'the form says what the report on screen is');
  assert.equal($('#sp-capital').value, '4000');
  assert.equal($('#sp-positions').value, '4');
});
