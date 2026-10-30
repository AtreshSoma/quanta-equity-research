/**
 * Research tear sheet, trader review (2026-10-23): the reward/risk hurdles and
 * where they clear, the break-even hit rate, the peer fund beside the
 * benchmark, the Street strip, call history, the mild-stance and risk-grade
 * trim words, a placeholder valuation score, and the chart's bar labels.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/research_1023.test.cjs
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
const lc = require('../../static/live-chart.js');
const T = globalThis.__QUANTA_THRESHOLDS__;

const frag = (h) => { const el = document.createElement('div'); el.innerHTML = h; return el; };

test('a failing funding hurdle names the price that clears it, and the hit rate the levels need', () => {
  const levels = { rr_hurdles: { research: { min: 1.5, passes: true }, funding: { min: 1.8, passes: false, clear_entry: 206.43 } } };
  const el = frag(app.levelNotesHtml({ levels, entry: 211.98, target: 241.22, rr: 1.72, technical: { resistance_level: 227.67 }, sym: '$' }));
  const funding = el.querySelector('[data-hurdle="funding"]');
  assert.ok(funding, 'the funding hurdle is stated');
  assert.match(funding.textContent, /1\.80:1/);
  assert.match(funding.textContent, /\$206\.43/);
  assert.match(funding.querySelector('[data-hurdle-why]').textContent, /Research judges the call at 1\.50:1; Picks funds only at 1\.80:1/,
    'the two hurdles are explained where a call passes one and not the other');
  assert.match(el.querySelector('[data-breakeven]').textContent, /37%/, '1 / (1 + 1.72) = 37%');
  assert.ok(el.querySelector('[data-hurdle-resistance]'), 'resistance between entry and target is named');
  assert.equal(app.buyZonePrice(levels), 206.43);
  // A passing hurdle, or resistance beyond the target, adds nothing.
  const quiet = frag(app.levelNotesHtml({ levels: { rr_hurdles: { research: { min: 1.5, passes: true }, funding: { min: 1.8, passes: true } } },
    entry: 100, target: 110, rr: 2, technical: { resistance_level: 120 }, sym: '$' }));
  assert.equal(quiet.querySelector('[data-hurdle]'), null);
  assert.equal(quiet.querySelector('[data-hurdle-resistance]'), null);
  assert.equal(app.buyZonePrice({ rr_hurdles: { funding: { min: 1.8, passes: true } } }), null);
});

test('the benchmark table gains the peer fund columns once the Research read arrives', () => {
  const rel = { benchmark: 'SPY', basis: 'total return', rows: [{ window: '1M', stock_pct: -5.6, benchmark_pct: -0.8, excess_pct: -4.8 }, { window: '1Y', stock_pct: 17.9, benchmark_pct: 15.2, excess_pct: 2.7 }] };
  const plain = frag(app.relativeCardHtml(rel, 'NVDA', null));
  assert.equal(plain.querySelectorAll('thead th').length, 4);
  const peer = { fund: 'SMH', basis: 'industry', rows: [{ window: '1M', fund_pct: -12.1 }, { window: '1Y', fund_pct: 56.5 }] };
  const el = frag(app.relativeCardHtml(rel, 'NVDA', peer));
  assert.deepEqual([...el.querySelectorAll('thead th')].map((t) => t.textContent), ['Window', 'NVDA', 'SPY', 'SMH', 'vs SPY', 'vs SMH']);
  const yr = [...el.querySelectorAll('tbody tr')][1].textContent;
  assert.match(yr, /-38\.6 pts/, 'vs the fund is the stock\'s own return minus the fund\'s');
  assert.match(el.querySelector('.section-kicker').textContent, /industry fund/);
});

test('the Street strip reads "Reading" until the read arrives, then the revisions', () => {
  const data = { current_price: 211.98, currency_symbol: '$', market_intel: { analyst_rating: 'STRONG_BUY', analyst_count: 58, target_mean: 328.95 }, fundamental: { forward_pe: 13.3, pe_ratio: 26.8, peg_ratio: 0.21 } };
  assert.match(frag(app.streetStripHtml(data, undefined)).textContent, /Reading/);
  const done = frag(app.streetStripHtml(data, { revisions: { raises: 12, cuts: 0, firms: 21 }, eps_revision_pct: 0.06 })).textContent;
  assert.match(done, /12 up/);
  assert.match(done, /0 down/);
  assert.match(done, /58 analysts/);
  assert.doesNotMatch(done, /Reading/);
});

test('call history marks the call that changed', () => {
  const el = frag(app.callHistoryHtml([
    { date: '2026-10-22', action: 'AVOID', stance: 'BEARISH', score: 42.6, price: 212.21, target: null, stop: null, changed: true },
    { date: '2026-10-21', action: 'HOLD_EXISTING', stance: 'NEUTRAL', score: 59.4, price: 221.97, target: 248.66, stop: 205.75, changed: false },
  ], '$'));
  const rows = el.querySelectorAll('tbody tr');
  assert.equal(rows.length, 2);
  assert.ok(rows[0].classList.contains('is-changed'));
  assert.match(rows[0].textContent, /Oct 22, 2026/);
  assert.equal(app.callHistoryHtml([], '$'), '');
});

test('a bullish score in the Accumulate band reads mild; a grade-F trim names the grade', () => {
  const acc = Number(T.LADDER.SCORE_BANDS.find(([, n]) => n === 'ACCUMULATE')[0]);
  const buy = Number(T.LADDER.SCORE_BANDS.find(([, n]) => n === 'BUY')[0]);
  const base = { new_money: 'WATCH', model_stance: 'BULLISH', holder_action: 'TRIM', blocking_codes: ['RISK_GRADE_F'] };
  const mild = app.decisionContract({ ...base, score: (acc + buy) / 2 });
  assert.equal(mild.stanceText, 'Bullish score (mild)');
  assert.equal(mild.holderText, 'Trim into strength (risk grade F)');
  assert.equal(app.decisionContract({ ...base, score: buy + 1 }).stanceText, 'Bullish score');
  assert.equal(app.decisionContract({ ...base, score: (acc + buy) / 2, blocking_codes: [] }).holderText, 'Trim into strength');
});

test('a placeholder valuation score reads n/m, not 50', () => {
  const el = frag(app.scoreBar('Valuation', 50, 'No DCF or valuation multiple was measured'));
  assert.equal(el.querySelector('.score-value').textContent, 'n/m');
  assert.ok(el.querySelector('[data-not-measured]'));
  assert.equal(frag(app.scoreBar('Valuation', 50)).querySelector('.score-value').textContent, '50.0');
});

test('a zoomed 1D window keeps the extended hours narrow (timeScale)', () => {
  const open = Date.parse('2026-10-09T13:30:00Z');
  const close = Date.parse('2026-10-09T20:00:00Z');
  const lo = Date.parse('2026-10-09T10:00:00Z'); // 6 AM New York
  const hi = Date.parse('2026-10-09T15:00:00Z'); // 11 AM
  const sc = lc.timeScale(lo, hi, [open, close], lc.zoned('America/New_York'));
  // 3.5 pre-market hours against 1.5 regular hours: the regular session is wider.
  assert.ok(sc.x(hi) - sc.x(open) > sc.x(open) - sc.x(lo));
  assert.equal(Math.round(sc.x(lo)), 0);
  assert.equal(Math.round(sc.x(hi)), 1000);
});

test('an average on intraday or weekly bars names its bar', () => {
  assert.equal(lc.studyLabel('SMA 200', '1m'), 'SMA 200 (1-min)');
  assert.equal(lc.studyLabel('SMA 50', '1wk'), 'SMA 50 (weekly)');
  assert.equal(lc.studyLabel('SMA 200', '1d'), 'SMA 200');
});

test('signals scored from the last completed bar say so; a settled bar adds nothing', () => {
  const live = { indicator_basis: { scored_through: '2026-10-28', live_bar_excluded: true, reason: 'still trading' } };
  const note = frag(app.indicatorBasisHtml(live)).querySelector('[data-indicator-basis]');
  assert.ok(note, 'the note is shown while today\'s bar is excluded');
  assert.match(note.textContent, /Scored through the Oct 28, 2026 close/);
  assert.match(note.textContent, /prices and levels on this page are live/);
  assert.equal(app.indicatorBasisHtml({ indicator_basis: { scored_through: '2026-10-28', live_bar_excluded: false } }), '');
  assert.equal(app.indicatorBasisHtml({}), '');
  const card = frag(app.technicalSignalsHtml({ ...live, rsi: 55, macd_signal: 'BULLISH', trend_direction: 'UP' }, 100, '$'));
  assert.ok(card.querySelector('[data-indicator-basis]'), 'the Technical signals card carries the note');
});
