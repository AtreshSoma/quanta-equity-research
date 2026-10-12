/**
 * Audit F3: one risk grade, and the card says what drove it.
 *
 * The card used to print "Risk grade: scanner B · additional checks F. The
 * restrictive result controls." Both engines now measure the grade the same
 * way, and the Decision carries the drivers; the card lists the factors that
 * added points and the window they were read on.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/risk_drivers.test.cjs
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

const drivers = [
  { factor: 'volatility', label: 'Volatility', points: 2, detail: '45% annualised (points above 25% / 35% / 50%)' },
  { factor: 'beta', label: 'Beta', points: 2, detail: '1.92 vs its benchmark (points above 1.2 / 1.5)' },
  { factor: 'drawdown', label: 'Max drawdown', points: 1, detail: '-37% peak to trough (points beyond -25% / -40%)' },
  { factor: 'tail', label: 'Tail risk', points: 0, detail: '50/100 from skew and fat tails (points above 50 / 70)' },
  { factor: 'liquidity', label: 'Liquidity', points: 0, detail: '10/10 dollar-volume score (a point below 5)' },
];

function pick(extra = {}) {
  return Object.assign({
    ticker: 'SYN', company: 'Synthetic', sector: 'Technology', signal: 'HOLD', final_action: 'WATCHLIST',
    display_action: 'WATCHLIST', overall_score: 7.5, conviction: 3, entry_price: 50, stop_loss: 46, price_target: 60,
    risk_reward_ratio: 2.1, allocation: 0, shares: 0, is_funded: false, risk_grade: 'F',
    decision: { risk_grade: 'F', risk_grade_drivers: drivers, risk_grade_lookback_days: 504 },
    deep_confirmation: { scanner_risk_grade: 'B', deep_risk_grade: 'F', reason_codes: [] },
  }, extra);
}

function scan(recs) {
  return { scan_status: 'no_picks', total_scanned: 250, currency: '$', capital: 2500, max_positions: 5,
    regime: { type: 'BULL', vix: 15, volatility: 'NORMAL', exposure: 100 }, risk_profile: {},
    recommendations: recs, deep_confirmation: { enabled: true, records: [], downgrades: [], not_confirmed: [], rejected: [] },
    chase_alerts: [], early_momentum: [], error_summary: { records: [] }, funnel: { universe_size: 250, gate_stats: {} } };
}

test('the card lists what drove the grade, never "the restrictive result controls"', () => {
  app.renderPicks(scan([pick()]));
  const card = document.querySelector('#picks-results .pick-card');
  const line = card.querySelector('[data-risk-drivers]');
  assert.ok(line, 'risk drivers line present');
  assert.match(line.textContent, /Risk grade F/);
  assert.match(line.textContent, /Volatility 45% annualised \(\+2\)/);
  assert.match(line.textContent, /Beta 1\.92 vs its benchmark \(\+2\)/);
  assert.match(line.textContent, /504 sessions/);
  assert.doesNotMatch(line.textContent, /Tail risk/, 'factors that add nothing are not listed');
  assert.doesNotMatch(card.textContent, /restrictive result controls/);
});

test('a grade with no adding factor says so', () => {
  const calm = drivers.map((d) => Object.assign({}, d, { points: 0 }));
  app.renderPicks(scan([pick({ risk_grade: 'A', decision: { risk_grade: 'A', risk_grade_drivers: calm, risk_grade_lookback_days: 504 } })]));
  const line = document.querySelector('#picks-results .pick-card [data-risk-drivers]');
  assert.match(line.textContent, /Risk grade A: no factor adds risk points/);
});

test('the line adds the base points and says when the total is capped (audit R1)', () => {
  const heavy = [
    { factor: 'volatility', label: 'Volatility', points: 3, detail: '55% annualised' },
    { factor: 'beta', label: 'Beta', points: 2, detail: '1.62 vs its benchmark' },
    { factor: 'drawdown', label: 'Max drawdown', points: 2, detail: '-46% peak to trough' },
    { factor: 'tail', label: 'Tail risk', points: 1, detail: '60/100' },
    { factor: 'liquidity', label: 'Liquidity', points: 0, detail: '10/10' },
  ];
  app.renderPicks(scan([pick({ decision: { risk_grade: 'F', risk_grade_drivers: heavy, risk_grade_lookback_days: 504 } })]));
  const line = document.querySelector('#picks-results .pick-card [data-risk-drivers]').textContent;
  assert.match(line, /3 base points, 11 capped at 10 of 10/);
  const calm = drivers.map((d) => Object.assign({}, d, { points: 0 }));
  app.renderPicks(scan([pick({ risk_grade: 'A', decision: { risk_grade: 'A', risk_grade_drivers: calm, risk_grade_lookback_days: 504 } })]));
  assert.match(document.querySelector('#picks-results .pick-card [data-risk-drivers]').textContent, /3 base points, 3 of 10/);
});
