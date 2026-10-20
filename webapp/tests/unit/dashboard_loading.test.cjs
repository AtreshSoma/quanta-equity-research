const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '../..');

function boot(fetch) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8'));
  const timers = new Map();
  let timerId = 0;
  const ctx = vm.createContext({
    __QUANTA_THRESHOLDS__: globalThis.__QUANTA_THRESHOLDS__,
    document: dom.window.document, console, URLSearchParams, AbortController, fetch,
    localStorage: { getItem: () => null, setItem() {} },
    setTimeout(fn, ms) {
      if (ms < 2000) { queueMicrotask(fn); return ++timerId; }
      timers.set(++timerId, { fn, ms }); return timerId;
    },
    clearTimeout(id) { timers.delete(id); }, setInterval() {}, clearInterval() {},
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app.js'), 'utf8') +
    '\n globalThis.api = {state, loadDashboard, loadMarketIntel, fetchJSON, clearMarketDependentPanels, renderMarketPulse};', ctx);
  return { ...ctx.api, document: dom.window.document, timers, dom };
}
const response = data => ({ ok: true, json: async () => data });
const intel = { loading: false, universe_size: 1, movers_up: [{ symbol: 'AAPL', price: 100, change_pct: 2 }], movers_down: [], analyst_picks: [], earnings_week: [] };
const dashboard = market => ({ selected_market: market, generated_at: '2026-09-07T12:00:00Z', market: { quotes: [] }, news: {}, market_intel: intel });

test('exhausted nonblocking polling clears every loading panel', async () => {
  const urls = [];
  const app = boot(async url => { urls.push(url); return response({ loading: true }); });
  app.state.dashboard = dashboard('US');
  await app.loadMarketIntel();
  for (let i = 0; i < 10; i++) {
    const entry = [...app.timers].find(([, timer]) => timer.ms === 4000);
    assert.ok(entry);
    app.timers.delete(entry[0]);
    await entry[1].fn();
  }
  assert.ok(urls.every(url => url.includes('blocking=0')));
  assert.equal(app.document.querySelectorAll('#portfolio-scan-body .skel-line, #top-movers-body .skel-line, #open-extremes-body .skel-line').length, 0);
  assert.equal(app.state.dashboard.market_intel.loading, false);
  assert.match(app.document.querySelector('#top-movers-body').textContent, /Retry/);
});

test('concurrent feed retries share one request and preserve prior quotes on failure', async () => {
  let reject;
  let calls = 0;
  const app = boot(() => { calls++; return new Promise((_, r) => { reject = r; }); });
  app.state.dashboard = dashboard('US');
  app.state.dashboard.market_intel.as_of = '2026-09-07T12:00:00Z';
  const first = app.loadMarketIntel();
  const second = app.loadMarketIntel({ resetAttempts: true });
  assert.equal(calls, 1);
  reject(new Error('offline'));
  await Promise.all([first, second]);
  assert.match(app.document.querySelector('#top-movers-body').textContent, /AAPL/);
  assert.match(app.document.querySelector('#top-movers-body').textContent, /last available quotes/);
});

test('a failed dashboard refresh preserves displayed opportunities', async () => {
  const app = boot(async () => { throw new Error('offline'); });
  app.state.dashboard = dashboard('US');
  const body = app.document.querySelector('#portfolio-scan-body');
  body.innerHTML = '<p>Existing qualified scan</p>';
  await app.loadDashboard(true);
  assert.equal(body.textContent, 'Existing qualified scan');
  assert.match(app.document.querySelector('#freshness-time').textContent, /refresh failed/);
});

test('late US dashboard and intel responses cannot replace the selected India market', async () => {
  const pending = [];
  const app = boot(url => new Promise(resolve => pending.push({ url, resolve })));
  const us = app.loadDashboard();
  app.state.market = 'IN';
  const india = app.loadDashboard();
  pending[1].resolve(response(dashboard('IN')));
  await india;
  pending[0].resolve(response(dashboard('US')));
  await us;
  assert.equal(app.state.dashboard.selected_market, 'IN');
  app.state.market = 'US';
  const oldIntel = app.loadMarketIntel();
  app.state.market = 'IN';
  pending[2].resolve(response({ ...intel, market: 'US' }));
  await oldIntel;
  assert.equal(app.state.dashboard.selected_market, 'IN');
  assert.notEqual(app.state.dashboard.market_intel.market, 'US');
});

test('request timeout stays active until the JSON response body completes', async () => {
  const app = boot(async (_url, options) => ({ ok: true, json: () => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('body timeout')));
  }) }));
  const result = app.fetchJSON('/slow', { retries: 0, timeoutMs: 8000 });
  await new Promise(setImmediate);
  const timer = [...app.timers.values()].find(t => t.ms === 8000);
  assert.ok(timer);
  timer.fn();
  await assert.rejects(result, /body timeout/);
});

test('malformed successful JSON is a request failure, not an empty dashboard', async () => {
  const app = boot(async () => ({ ok: true, json: async () => { throw new SyntaxError('invalid JSON'); } }));
  await assert.rejects(app.fetchJSON('/invalid', { retries: 0 }), /invalid JSON/);
});

test('switching markets clears stored scan results as well as the displayed panels', () => {
  const app = boot(async () => response({}));
  app.state.dashboard = dashboard('US');
  app.state.picks = { recommendations: [{ ticker: 'AAPL' }], total_scanned: 100 };
  app.clearMarketDependentPanels('IN');
  assert.equal(app.state.picks, null);
  assert.equal(app.state.dashboard, null);
  assert.match(app.document.querySelector('#portfolio-scan-body').textContent, /India/);
});

test('tape marks stale quotes and shows N/A for an unknown daily change', () => {
  const app = boot(async () => response({}));
  app.renderMarketPulse({ quotes: [{ symbol: 'SPY', price: 100, change_pct: null, stale: true }] });
  const tape = app.document.querySelector('#market-pulse');
  assert.match(tape.textContent, /Stale quote/);
  assert.equal(tape.querySelector('.ovx-pu-c b').textContent.trim(), 'N/A');
});
