/**
 * Regression tests for the UI audit: result identity, data truthfulness,
 * rejection remedies, chart gaps, benchmark labelling, scan states, keyboard
 * access and quote freshness. jsdom, no network.
 *
 * Run: node --test tests/unit/audit_fixes.test.cjs
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
// A minimal window: timers plus the navigation surface switchView touches.
const windowStub = {
  setTimeout, clearTimeout, setInterval, clearInterval,
  location: { hash: '' }, history: { pushState() {}, replaceState() {} }, scrollY: 0, scrollTo() {},
};
// Loaded with no window (skips the DOMContentLoaded bootstrap), then given one.
global.window = undefined;
const app = require('../../static/app.js');
global.window = windowStub;
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const $ = (sel) => document.querySelector(sel);
const tick = async (n = 6) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setTimeout(r, 0)); };

// --- controllable transport --------------------------------------------------
class FakeEventSource {
  constructor(url) { this.url = url; this.closed = false; FakeEventSource.all.push(this); }
  close() { this.closed = true; }
  emit(obj) { if (this.onmessage) return this.onmessage({ data: JSON.stringify(obj) }); return undefined; }
}
FakeEventSource.all = [];
global.EventSource = FakeEventSource;
const results = {};
global.fetch = async (url, opts) => {
  const u = String(url);
  const job = u.match(/\/api\/jobs\/([^/]+)$/);
  if (job) return { ok: true, status: 200, json: async () => ({ result: results[job[1]] }) };
  if (u === '/api/analyze') return { ok: true, status: 200, json: async () => ({ job_id: `an-${JSON.parse(opts.body).ticker}` }) };
  if (u === '/api/stock-picks') return { ok: true, status: 200, json: async () => ({ job_id: `scan-${JSON.parse(opts.body).market}` }) };
  throw new Error(`unexpected fetch ${u}`);
};
const streamFor = (jobId) => FakeEventSource.all.find((es) => es.url.includes(`/jobs/${jobId}/`));

// ---------------------------------------------------------------------------
// 1. Result identity
// ---------------------------------------------------------------------------
test('a superseded analysis response never replaces a newer one', async () => {
  $('#an-ticker').value = 'AAPL';
  await app.runAnalysis();
  $('#btn-analyze').disabled = false;            // the newer request can be issued
  $('#an-ticker').value = 'NVDA';
  await app.runAnalysis();
  results['an-NVDA'] = { ticker: 'NVDA', is_invalid: true, recommendation_text: 'nvda result' };
  results['an-AAPL'] = { ticker: 'AAPL', is_invalid: true, recommendation_text: 'aapl result' };
  await streamFor('an-NVDA').emit({ status: 'complete', progress: 100 });
  await tick();
  assert.match($('#an-results').textContent, /NVDA/);
  // The older request finishes last and must be discarded.
  await streamFor('an-AAPL').emit({ status: 'complete', progress: 100 });
  await tick();
  assert.match($('#an-results').textContent, /NVDA/);
  assert.doesNotMatch($('#an-results').textContent, /AAPL/);
});

test('a scan for the previous market is discarded after the market changes', async () => {
  $('#sp-market').value = 'US';
  await app.runScan(null, {});
  assert.equal($('#btn-scan').disabled, true);
  app.clearMarketDependentPanels('IN');
  assert.equal($('#btn-scan').disabled, false, 'the form is released');
  assert.equal($('#picks-meta').textContent, 'No scan yet');
  results['scan-US'] = { scan_status: 'no_universe' };
  await streamFor('scan-US')?.emit({ status: 'complete', progress: 100 });
  await tick();
  assert.doesNotMatch($('#picks-results').textContent, /Scan failed|empty universe/,
    'a US result must not render under India');
  assert.equal(app.state.picks, null);
});

// ---------------------------------------------------------------------------
// 2. Missing analyst data is not a recommendation
// ---------------------------------------------------------------------------
test('missing rating and null upside read as unavailable, not Buy / 0.0%', () => {
  assert.equal(app.numOrNull(null), null);
  assert.equal(app.numOrNull(''), null);
  assert.equal(app.numOrNull(0), 0);
  assert.equal(app.upsidePct(null, 100), null, 'a missing target is not a 100% drop');
  assert.equal(app.streetRating(undefined), 'Rating unavailable');
  assert.equal(app.streetRating('none'), 'Rating unavailable');
  assert.equal(app.streetRating('strong_buy'), 'Strong Buy');

  app.renderTopOpportunities({ analyst_picks: [{ symbol: 'ZZZ', name: 'Zed', price: 10, sector: 'Tech', target_mean: null, analyst_count: null, upside_pct: null }] });
  const text = $('#portfolio-scan-body').textContent;
  assert.match(text, /Rating unavailable/);
  assert.match(text, /Upside unavailable/);
  assert.doesNotMatch(text, /\bBuy\b/);
  assert.doesNotMatch(text, /0\.0%/);
});

// ---------------------------------------------------------------------------
// 3. Skipped insider analysis is not measured inactivity
// ---------------------------------------------------------------------------
function analysis(over = {}) {
  return Object.assign({
    ticker: 'AAPL', company_name: 'Apple Inc.', currency_symbol: '$', current_price: 100,
    final_action: 'WATCHLIST', signal: 'HOLD', confidence_score: 42,
    scores: { composite_score: 57 }, technical: {}, fundamental: {}, risk: { risk_grade: 'C' },
    trade_setup: {}, valuation: {}, monte_carlo: {},
    market_intel: { analyst_rating: 'HOLD', buy_ratings: 0, hold_ratings: 0, sell_ratings: 0, target_mean: 0 },
    insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0 },
    position_sizing: {}, thesis: {}, signal_explanation: {}, catalysts: {}, sentiment: {}, price_chart: { points: [] },
  }, over);
}
test('fast preview says insider activity was not fetched', () => {
  app.renderAnalysis(analysis({ fast_mode: true, insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0, data_status: 'not_fetched' } }));
  const box = $('#an-results .insider-box').textContent;
  assert.match(box, /Not fetched in fast preview/);
  assert.doesNotMatch(box, /0 buys/);
});
test('an insider outage says unavailable; a measured zero still reads as zero', () => {
  app.renderAnalysis(analysis({ insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0, data_status: 'unavailable' } }));
  assert.match($('#an-results .insider-box').textContent, /Unavailable.*not zero/);
  app.renderAnalysis(analysis({ insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0, data_status: 'measured' } }));
  assert.match($('#an-results .insider-box').textContent, /0 buys \/ 0 sells/);
});
test('an older payload without a status is judged by fast_mode', () => {
  app.renderAnalysis(analysis({ fast_mode: true }));
  assert.match($('#an-results .insider-box').textContent, /Not fetched in fast preview/);
});

// ---------------------------------------------------------------------------
// 4. Rejection remedies follow the rule, not the words
// ---------------------------------------------------------------------------
test('the remedy for a rejection is chosen by its rule code', () => {
  const grade = app.vetoRevalidation(['RISK_GRADE_F']);
  assert.match(grade, /risk grade improves/i);
  assert.doesNotMatch(grade, /reward\/risk/);
  // [F2] The R:R remedy names the binding levels; the exact prices come from
  // binding_levels.remedy (tests/unit/binding_levels.test.cjs).
  assert.match(app.vetoRevalidation(['LOW_REWARD_RISK']), /reward\/risk on the binding levels/);
  const outage = app.vetoRevalidation(['PROVIDER_DATA_UNAVAILABLE']);
  assert.match(outage, /Not a verdict on the setup/);
  assert.doesNotMatch(outage, /clean base/i);
  // No code: say so rather than invent a remedy from prose.
  assert.match(app.vetoRevalidation([]), /did not report which rule/);
});

test('the Avoid queue shows the remedy of the rule that fired', () => {
  app.state.picks = { deep_confirmation: { rejected: [
    { ticker: 'RISKY', reason: 'Risk Grade F blocks new-money BUY → WATCHLIST', reason_codes: ['RISK_GRADE_F'] },
  ] } };
  app.renderSetupStatusCard({});
  const text = $('#setup-status-body').textContent;
  assert.match(text, /risk grade improves/i);
  assert.doesNotMatch(text, /tighter stop/);
  app.state.picks = null;
});

// ---------------------------------------------------------------------------
// 5 + 14. Chart
// ---------------------------------------------------------------------------
test('a null close is a gap, never a price of zero', () => {
  const points = Array.from({ length: 6 }, (_, i) => ({ date: `2026-03-0${i + 1}`, close: 100 + i }));
  points[2].close = null;
  const out = app.renderPriceChartCard({ points }, { ticker: 'X', currentPrice: 105, target: 0, stop: 0, sym: '$' });
  const lines = [...out.matchAll(/class="chart-line[^"]*" points="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(lines.length, 2, 'the line is split at the gap');
  const series = JSON.parse(out.match(/data-series='([^']+)'/)[1].replace(/&quot;/g, '"'));
  assert.equal(series.length, 5);
  assert.ok(series.every(([, close]) => close >= 100), 'no zero close was plotted');
  assert.match(out, /Gaps: no close reported/);
});

test('a chart with too few real closes degrades instead of drawing zeros', () => {
  const out = app.renderPriceChartCard({ points: [{ date: 'a', close: 100 }, { date: 'b', close: null }, { date: 'c', close: '' }] },
    { ticker: 'X', currentPrice: 100, sym: '$' });
  assert.match(out, /Price chart unavailable/);
});

test('an off-scale target is marked, and the title uses the returned period', () => {
  const points = Array.from({ length: 5 }, (_, i) => ({ date: `2026-0${i + 1}-15`, close: 100 + i }));
  const out = app.renderPriceChartCard({ points }, { ticker: 'X', currentPrice: 104, target: 400, stop: 1, sym: '$' });
  assert.match(out, /Target \$400\.00<tspan class="chart-offscale"> ▲ off scale/);
  assert.match(out, /Stop \$1\.00<tspan class="chart-offscale"> ▼ off scale/);
  assert.match(out, /3-month price action|4-month price action/);
  assert.deepEqual(app.chartPeriodLabel('2026-01-01', '2026-01-21'), { long: '20-day', short: '20D' });
  assert.equal(app.chartPeriodLabel('2025-09-01', '2026-09-01').short, '1Y');
});

// ---------------------------------------------------------------------------
// 6 + 15. Track record
// ---------------------------------------------------------------------------
test('a mixed-market ledger names each benchmark instead of one global SPY', () => {
  app.renderTrackRecord({
    benchmark: null, benchmarks: ['SPY', '^NSEI'], benchmark_mixed: true, min_sample: 30, method: 'Entry at the next open.',
    totals: { recorded: 2, resolved: 1, pending: 1, awaiting_maturity: 1, awaiting_resolution: 0, legacy_excluded: 0 },
    engines: [],
    groups: [{ key: { engine: 'elite', horizon_days: 21, stance: 'BULLISH' }, n_resolved: 1, n_pending: 1, benchmarks: ['SPY', '^NSEI'], hit_rate: 1, mean_excess_pct: 1, ci95_excess_pct: null, sufficient: false, verdict: 'Insufficient sample' }],
    recent: [
      { as_of_date: '2026-09-01', ticker: 'RELIANCE.NS', engine: 'elite', label: 'BUY', horizon_days: 21, return_pct: 3, excess_return_pct: 1, outcome: 'WIN', benchmark: '^NSEI' },
    ],
  });
  const text = $('#track-body').textContent;
  assert.match(text, /Nifty 50 \(\^NSEI\)/);
  assert.match(text, /mixed markets/);
  assert.doesNotMatch(text, /vs SPY/);
});

test('waiting states separate "inside horizon" from "past horizon, unresolved"', () => {
  const base = { benchmark: 'SPY', benchmarks: ['SPY'], min_sample: 30, method: 'x', engines: [], groups: [], recent: [] };
  app.renderTrackRecord({ ...base, totals: { recorded: 3, resolved: 0, pending: 3, awaiting_maturity: 1, awaiting_resolution: 2, earliest_expected_exit: '2026-10-05', legacy_excluded: 1 } });
  const text = $('#track-body').textContent;
  assert.match(text, /1 is still inside its horizon/);
  assert.match(text, /2 have passed their horizon without a recorded outcome/);
  assert.match(text, /1 earlier as-of-close result is excluded/);
  assert.match(text, /Oct 5, 2026/);
  assert.doesNotMatch(text, /all still inside their horizon/);
});

// ---------------------------------------------------------------------------
// 8. Scan states
// ---------------------------------------------------------------------------
test('a completed empty scan replaces "Scanning…" in the header', () => {
  app.renderPicks({ recommendations: [], total_scanned: 40, scan_status: 'no_picks' });
  assert.equal($('#picks-meta').textContent, 'Scan complete · 40 scanned · no Buy or Accumulate call');
  app.renderPicks({ scan_status: 'failed', recommendations: [] });
  assert.equal($('#picks-meta').textContent, 'Scan failed');
  app.renderPicks({ scan_status: 'provider_rate_limited', recommendations: [] });
  assert.match($('#picks-meta').textContent, /rate-limited/);
  app.state.picks = null;
});

test('Discover does not claim no scan ran after an empty one', () => {
  app.state.picks = { recommendations: [], total_scanned: 40 };
  app.renderTopOpportunities({ analyst_picks: [{ symbol: 'ZZZ', name: 'Zed', price: 10, target_mean: 12, upside_pct: 20, recommendation: 'buy', analyst_count: 5 }] });
  const text = $('#portfolio-scan-body').textContent;
  assert.doesNotMatch(text, /no Picks scan has run/i);
  assert.match(text, /last Picks scan did not fund/);
  app.state.picks = null;
  app.renderTopOpportunities({ analyst_picks: [{ symbol: 'ZZZ', name: 'Zed', price: 10, target_mean: 12, upside_pct: 20, recommendation: 'buy', analyst_count: 5 }] });
  assert.match($('#portfolio-scan-body').textContent, /no Picks scan has run this session/);
});

// ---------------------------------------------------------------------------
// 9. Badges follow the theme
// ---------------------------------------------------------------------------
test('portfolio decision badges use theme tokens, not inline colours', () => {
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.doesNotMatch(src, /badgeStyle/);
  assert.doesNotMatch(src, /class="ds-badge" style=/);
  assert.match(css, /\.signal-pill\.bull \{[^}]*var\(--bull-bright\)/);
});

// ---------------------------------------------------------------------------
// 10. Keyboard
// ---------------------------------------------------------------------------
test('an Avoid row opens its ticker from Enter and Space', () => {
  app.state.picks = { deep_confirmation: { rejected: [{ ticker: 'KEYB', reason: 'x', reason_codes: ['LOW_SCORE'] }] } };
  app.renderSetupStatusCard({});
  const row = $('#setup-status-body tr[data-ticker-open="KEYB"]');
  assert.ok(row, 'row rendered');
  $('#an-ticker').value = '';
  let opened = 0;
  row.addEventListener('click', () => { opened += 1; });
  const ev = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  row.dispatchEvent(ev);
  assert.equal(ev.defaultPrevented, true, 'Enter is handled');
  assert.equal($('#an-ticker').value, 'KEYB', 'the ticker was opened');
  app.state.picks = null;
});

// ---------------------------------------------------------------------------
// 12. Reduced motion
// ---------------------------------------------------------------------------
test('reduced motion overrides component animations, not just the universal selector', () => {
  const block = css.match(/@media \(prefers-reduced-motion: reduce\) \{\s*html \{[^}]*\}[\s\S]*?\n\}/)[0];
  assert.match(block, /animation-duration: 0\.01ms !important/);
  assert.match(block, /animation-delay: 0s !important/);
  assert.match(block, /transition-duration: 0\.01ms !important/);
});

// ---------------------------------------------------------------------------
// 13. Quote freshness
// ---------------------------------------------------------------------------
test('quote session time is reported apart from retrieval time', () => {
  app.state.market = 'US';
  // Wednesday 2026-09-23, 11:00 New York (15:00 UTC): regular session.
  const midday = new Date('2026-09-23T15:00:00Z');
  assert.equal(app.exchangeSession('US', midday).phase, 'regular');
  const live = app.quoteFreshness({ quote_date: '2026-09-23', price_basis: 'daily_bar', as_of: '2026-09-23T15:00:00Z' }, midday);
  assert.match(live.session, /In session Sep 23 · delayed/);
  const prev = app.quoteFreshness({ quote_date: '2026-09-22', as_of: '2026-09-23T15:00:00Z', cached: true }, midday);
  assert.match(prev.session, /Previous close Sep 22/);
  assert.deepEqual(prev.flags, ['cached']);
  assert.match(prev.retrieved, /retrieved 11:00 ET/);
  const unknown = app.quoteFreshness({ price_basis: 'last_price', stale: true }, midday);
  assert.match(unknown.session, /session time not reported/);
  assert.deepEqual(unknown.flags, ['stale']);
  // After the close the same date is a close, not a live session.
  const late = new Date('2026-09-23T21:00:00Z');
  assert.match(app.quoteFreshness({ quote_date: '2026-09-23' }, late).session, /^Close Sep 23/);
});

test('the ribbon says its prices are delayed and separates session from retrieval', () => {
  app.state.market = 'US';
  app.renderMarketPulse({ quotes: [{ symbol: 'SPY', label: 'S&P 500', price: 500, change_pct: 0.4, quote_date: '2026-09-22', as_of: '2026-09-23T15:00:00Z' }] });
  const el = $('#market-pulse');
  assert.match(el.textContent, /can be delayed/);
  assert.match(el.querySelector('.q-rb-time').textContent, /Previous close|In session|Close/);
});

// ---------------------------------------------------------------------------
// 11. Browser history
// ---------------------------------------------------------------------------
test('changing view adds a history entry; the initial load replaces one', () => {
  const win = new JSDOM('<!doctype html><body></body>', { url: 'http://127.0.0.1:5500/#dashboard' }).window;
  global.window = win;
  try {
    const before = win.history.length;
    app.switchView('discover');
    assert.equal(win.location.hash, '#discover');
    assert.equal(win.history.length, before + 1, 'navigating pushes');
    app.switchView('picks');
    assert.equal(win.history.length, before + 2);
    app.switchView('picks');
    assert.equal(win.history.length, before + 2, 'no duplicate entry for the same view');
    app.switchView('analyze', { replace: true });
    assert.equal(win.history.length, before + 2, 'replace rewrites the current entry');
    assert.equal(win.location.hash, '#analyze');
  } finally {
    global.window = windowStub;
  }
});
