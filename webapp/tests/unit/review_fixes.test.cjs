/**
 * Frontend fixes from the September 2026 full review (pure functions).
 *
 *  - Monte Carlo expected_return is PERCENT; small moves are not "inconsistent".
 *  - An exit-stance verdict publishes no long levels; that is not a missing stop.
 *  - When the engine says there is no long reward, the UI never recomputes a
 *    positive R:R from a different target.
 *
 * Run: node --test tests/unit/review_fixes.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const { buildValidationIssues, summarizeValidationIssues, decisionSessionHtml } = require('../../static/app.js');

test('a +1.2% Monte Carlo expected return on a +1.2% median is NOT flagged inconsistent', () => {
  const data = {
    current_price: 100, signal: 'BUY',
    trade_setup: { entry_price: 100, target_base: 120, stop_loss: 90, risk_reward_ratio: 2 },
    monte_carlo: { median_price: 101.2, expected_return: 1.2, probability_profit: 0.56 },
  };
  const issues = buildValidationIssues(data, {});
  assert.equal(issues.find((i) => i.code === 'monte_carlo_inconsistent'), undefined);
});

test('a -1.4% Monte Carlo expected return on a -1.4% median is NOT flagged inconsistent', () => {
  const data = {
    current_price: 100, signal: 'HOLD',
    trade_setup: { entry_price: 100, target_base: 110, stop_loss: 90, risk_reward_ratio: 1 },
    monte_carlo: { median_price: 98.6, expected_return: -1.4, probability_profit: 0.47 },
  };
  const issues = buildValidationIssues(data, {});
  assert.equal(issues.find((i) => i.code === 'monte_carlo_inconsistent'), undefined);
});

test('a SELL verdict with no long levels is not a critical stop-loss failure', () => {
  const data = {
    current_price: 100, signal: 'SELL', final_action: 'AVOID',
    trade_setup: { entry_price: 0, stop_loss: 0, target_base: 0, levels_available: false, is_actionable_entry: false },
  };
  const issues = buildValidationIssues(data, {});
  assert.equal(issues.find((i) => i.code === 'no_stop_loss'), undefined);
  assert.equal(summarizeValidationIssues(issues).criticalStatus, 'PASS');
});

test('a BUY with no stop anywhere is still a critical failure', () => {
  const data = { current_price: 100, signal: 'BUY', trade_setup: { entry_price: 100, target_base: 110 } };
  const issues = buildValidationIssues(data, {});
  assert.equal(issues.find((i) => i.code === 'no_stop_loss').severity, 'FAIL');
});

test('the engine long-term / tactical stop counts as a fallback stop', () => {
  const data = {
    current_price: 100, signal: 'BUY',
    trade_setup: { entry_price: 100, target_base: 110, stop_loss: 0 },
    beginner_guidance: { long_term_stop: 92 },
  };
  const issues = buildValidationIssues(data, {});
  const stop = issues.find((i) => i.code === 'stop_fallback_used');
  assert.ok(stop, 'fallback stop should be recognised');
  assert.equal(stop.severity, 'WARN');
});

test('has_long_reward=false is reported as a finding, not a recompute', () => {
  const data = {
    current_price: 100, signal: 'HOLD',
    trade_setup: { entry_price: 100, target_base: 97, stop_loss: 92, risk_reward_ratio: 0, has_long_reward: false },
  };
  const issues = buildValidationIssues(data, {});
  assert.ok(issues.find((i) => i.code === 'no_long_reward'));
  assert.equal(issues.find((i) => i.code === 'rr_recomputed'), undefined);
});

test('session block surfaces the backend price reconciliation note when prices disagree', () => {
  const data = {
    current_price: 100,
    price_chart: { session: { open: 101, day_high: 104, day_low: 100.5, prev_close: 100, ltp: 103, date: '2026-09-03' } },
    price_reconciliation: { discrepancy: true, note: 'Analysis was priced at 100.00; the session quote shows 103.00 (+3.00%).' },
  };
  const html = decisionSessionHtml(data, { sym: '$' });
  assert.match(html, /ds-recon/);
  assert.match(html, /priced at 100\.00/);
});

test('session block stays quiet when prices agree', () => {
  const data = {
    current_price: 100,
    price_chart: { session: { open: 101, day_high: 104, day_low: 100.5, prev_close: 100, ltp: 100.1 } },
    price_reconciliation: { discrepancy: false, note: 'Analysis price and session quote agree.' },
  };
  assert.doesNotMatch(decisionSessionHtml(data, { sym: '$' }), /ds-recon/);
});
