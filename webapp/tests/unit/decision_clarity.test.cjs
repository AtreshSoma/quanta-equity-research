/**
 * Plain-language Decision on the tear sheet: the model's real call (never a
 * vague "CANDIDATE"), the unvalidated-model qualifier beside it, what the call
 * means, when to enter, and sizing figures formatted as what they are.
 *
 * Run: node --test tests/unit/decision_clarity.test.cjs
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

// Shaped on the NBIX payload that prompted this change: an unvalidated
// ACCUMULATE at risk grade D, with candle timing saying "not today".
function nbix(over = {}) {
  const decision = {
    action: 'ACCUMULATE', label: 'ACCUMULATE', qualifier: 'Unproven model', stance: 'BULLISH',
    sizing_basis: 'risk-budget-only',
    caveat: 'Unproven model: the scoring model has not been tested on past data yet, so treat this as a research idea, not a recommendation. Sizes shown are the most your risk budget allows, not advice to buy.',
    horizon: { key: 'position', label: 'Position (~3 months)', trading_days: 63 },
    evidence: { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false },
    freshness: { status: 'fresh', last_bar: '2026-09-25', lag_sessions: 0 },
    levels: { reference_price: 138.05, entry: 138.33, stop: 132.57, target: 180.06, reward_risk: 7.04 },
    reasons: ['Composite score 62.7 → candidate ACCUMULATE', 'Risk Grade D: hold at ACCUMULATE (reduced size)'],
    blocking: ['Preliminary analysis; full checks were skipped.'], conflicts: [], scenarios: [],
  };
  return Object.assign({
    ticker: 'NBIX', company_name: 'Neurocrine Biosciences, Inc.', sector: 'Healthcare', currency_symbol: '$',
    current_price: 138.05, day_change_pct: -0.64, market_cap: 14.07e9, market_regime: 'BEAR_TRENDING',
    final_action: 'ACCUMULATE', signal: 'ACCUMULATE', confidence_score: 52.7, confidence_level: 'MEDIUM',
    recommendation_label: 'ACCUMULATE', recommendation_qualifier: 'Unproven model',
    decision,
    scores: { composite_score: 62.7, technical_score: 35, fundamental_score: 83, valuation_score: 75, quality_score: 90, sentiment_score: 50, risk_score: 20 },
    technical: { rsi: 25, adx: 28.6, macd_signal: 'BEARISH', trend_direction: 'DOWN', trend_strength: 'MODERATE', volume_ratio: 0.35, support_level: 133.91, resistance_level: 156.56, week_52_low: 123.1, week_52_high: 185.5, candlestick_bias: 'BEARISH', candlestick_score: 37, prior_trend: 'DOWN', candlestick_patterns: [] },
    fundamental: { pe_ratio: 20.2, forward_pe: 11.6, peg_ratio: 0.65, ev_ebitda: 15.5, roe: 22.1, net_margin: 20.9, revenue_growth_yoy: 39.5, debt_to_equity: 0.13, piotroski_f_score: 9, altman_z_score: 7.79 },
    risk: { risk_grade: 'D', annualized_volatility: 0.349, beta: 0.63, sharpe_ratio: 0.32, sortino_ratio: 0.43, max_drawdown: -0.429, var_95_daily: -0.033 },
    trade_setup: { entry_price: 138.33, stop_loss: 132.57, target_base: 180.06, target_conservative: 162.06, target_aggressive: 207.07, risk_reward_ratio: 7.04, levels_available: true, is_actionable_entry: true, time_horizon: 'POSITION' },
    valuation: { target_base: 180.06, target_conservative: 162.06, target_aggressive: 207.07, valuation_label: 'UNDERVALUED', dcf_upside: 41.8 },
    market_intel: { buy_ratings: 0, hold_ratings: 0, sell_ratings: 0 },
    insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0 },
    monte_carlo: { median_price: 137.57, expected_return: -0.4, probability_profit: 0.487, var_95: -17.6 },
    price_chart: { period: '6mo', points: Array.from({ length: 30 }, (_, i) => ({ date: `2026-08-${String(i + 1).padStart(2, '0')}`, close: 130 + i / 3 })), session: { open: 138.94, day_high: 138.94, day_low: 137.02, prev_close: 138.94, ltp: 138.05, volume: 401595, range_low: 125.64, range_high: 186.12, range_bars: 120, date: '2026-09-25' } },
    position_sizing: { recommended_shares: 25, position_value: 3458.15, portfolio_weight: 0.0346, kelly_optimal: 10540.06, is_viable: true },
    account_context: { account_size: 100000, assumed: true, note: 'Sizing assumes a $100,000 account (default).' },
    beginner_guidance: { buy_now: 'NO', candlestick_timing: 'NO CLEAR EDGE', entry_quality: 'Weak', rr_label: '7.0:1', risk_grade: 'D', new_buyer: 'Wait', existing_holder: 'Hold with strict stop-loss', long_term_stop: 132.57, tactical_stop: 134.06, existing_holder_stop: 132.57, pullback_entry: 133.91, pullback_stop: 130.04, breakout_entry: 185.5, volume_requirement: '>= 1.5x avg', data_quality: 'WARN', warnings: [] },
    portfolio_outlook: { recommendation: 'ACCUMULATE', bottom_line: 'New buyers may build a small position gradually.' },
    signal_explanation: { primary_reason: 'Composite 62.7', supporting_factors: [], risk_factors: [], overrides_triggered: [] },
    action_plan_title: 'SCALE-IN PLAN',
    thesis: { summary: 'Accumulate', bull_case: ['Strong profitability'], bear_case: ['Drawdowns'], risks: [] },
    catalysts: { earnings_date: '', days_to_earnings: null }, sentiment: { sentiment_label: 'NEUTRAL', key_headlines: [] },
    price_reconciliation: { discrepancy: false },
    fast_mode: true, cache: { hit: false },
  }, over);
}

test('plainReason turns engine audit traces into sentences and keeps unknown lines', () => {
  assert.equal(app.plainReason('Composite score 62.7 → candidate ACCUMULATE'),
    'Overall score 62.7/100 is in the Accumulate range (55–65)');
  assert.equal(app.plainReason('Risk Grade D — hold at ACCUMULATE (reduced size)'),
    'Risk grade D (high risk): kept at Accumulate, with a smaller position');
  assert.equal(app.plainReason('Risk Grade D blocks fresh BUY — STRONG_BUY → ACCUMULATE (reduced size)'),
    'Risk grade D (high risk): lowered from Strong Buy to Accumulate, with a smaller position');
  assert.equal(app.plainReason('HOLD_EXISTING but no existing position → WATCHLIST'),
    'Neutral call and you do not hold it: Watchlist until the setup improves');
  assert.equal(app.plainReason('Composite score 80.0 → candidate STRONG_BUY'),
    'Overall score 80.0/100 is in the Strong Buy range (75+)');
  // Unknown shapes are never dropped; only the enum tokens become words.
  assert.equal(app.plainReason('Earnings in 2 days → WATCHLIST'), 'Earnings in 2 days → Watchlist');
});

test('entryTimingNote says when to enter a funded call and never proposes buying above the target', () => {
  const data = nbix();
  const note = app.entryTimingNote(data, 'ACCUMULATE', '$', 180.06);
  assert.equal(note.tone, 'warn');
  assert.match(note.text, /pullback near \$133\.91/);
  assert.doesNotMatch(note.text, /185\.50/, 'a breakout above the base target leaves no reward');
  assert.match(app.entryTimingNote(data, 'ACCUMULATE', '$', 200).text, /breakout above \$185\.50/);
  const yes = nbix({ beginner_guidance: { buy_now: 'YES' } });
  assert.equal(app.entryTimingNote(yes, 'BUY').tone, 'bull');
  assert.equal(app.entryTimingNote(data, 'WATCHLIST'), null, 'no entry timing for a call that funds nothing');
});

test('the hero shows the real call, its qualifier, its meaning and its timing', () => {
  app.renderAnalysis(nbix());
  const block = document.querySelector('#an-results .decision-action');
  assert.equal(block.querySelector('.signal').textContent, 'ACCUMULATE');
  assert.equal(block.querySelector('.model-flag').textContent, 'Model not yet validated');
  assert.equal(block.querySelector('.model-flag').getAttribute('href'), '#track', 'the flag opens the evidence page');
  assert.match(block.querySelector('.decision-meaning').textContent, /Buy gradually in small steps/);
  assert.match(block.querySelector('[data-entry-timing]').textContent, /Prefer a pullback near \$133\.91/);
  assert.doesNotMatch(block.textContent, /Track record/, 'the model flag carries the evidence status');
  assert.doesNotMatch(document.querySelector('#an-results').textContent, /CANDIDATE/);

  const why = document.querySelector('#an-results .decision-why').textContent;
  assert.match(why, /Overall score 62\.7\/100 is in the Accumulate range/);
  assert.match(why, /Fast preview: news, insider, analyst and catalyst checks were skipped/);
  assert.doesNotMatch(why, /→ candidate/);

  const levels = document.querySelector('#an-results .decision-levels').textContent;
  assert.match(levels, /Entry\s*\$138\.33/, 'R/R is measured from the entry, so the entry is shown');
});

test('sizing shows weight as a percent of the account and publishes no Kelly figure', () => {
  app.renderAnalysis(nbix());
  const text = document.querySelector('#ts-decision').textContent;
  assert.match(text, /Portfolio weight\s*3\.5%/);
  // No Kelly figure is published: it rested on an uncalibrated win probability.
  assert.doesNotMatch(text, /Kelly/i);
  assert.doesNotMatch(text, /10540\.1%|\+0\.0%/);
});

test('a funded call with weak timing is a scale-in plan, and the section asks the right question', () => {
  app.renderAnalysis(nbix());
  const text = document.querySelector('#ts-decision').textContent;
  assert.match(text, /Scale-in plan/); // sentence case since the styling pass
  assert.doesNotMatch(text, /Existing holder profit plan/i);
  assert.match(text, /Why only Accumulate\?/);
  // The same payload from an older server, without the title, still reads right.
  app.renderAnalysis(nbix({ action_plan_title: undefined }));
  assert.doesNotMatch(document.querySelector('#ts-decision').textContent, /Existing holder profit plan/i);
});

test('a bear case above today\'s price is flagged as a valuation target, not a downside', () => {
  app.renderAnalysis(nbix());
  const note = document.querySelector('#an-results [data-bear-above-price]');
  assert.ok(note, 'note missing');
  assert.match(note.textContent, /stop at \$132\.57/);
  app.renderAnalysis(nbix({ valuation: { target_base: 180.06, target_conservative: 120, target_aggressive: 207.07 } }));
  assert.equal(document.querySelector('#an-results [data-bear-above-price]'), null);
});

test('a validated call carries no qualifier', () => {
  const data = nbix();
  data.decision = { ...data.decision, action: 'BUY', label: 'BUY', qualifier: '', caveat: '',
    evidence: { status: 'VALIDATED', status_text: 'Validated out of sample', permits_recommendation: true } };
  data.recommendation_qualifier = '';
  app.renderAnalysis(data);
  const block = document.querySelector('#an-results .decision-action');
  assert.equal(block.querySelector('.signal').textContent, 'BUY');
  assert.equal(block.querySelector('.decision-qualifier'), null);
  assert.equal(block.querySelector('.model-flag'), null, 'a validated model carries no flag');
});

// A blocked bullish call: the contract says what it means for each reader, and
// the tear sheet renders the backend's own words, not a browser re-derivation.
function blockedNvda() {
  const data = nbix();
  data.final_action = 'WATCHLIST';
  data.signal = 'HOLD';
  data.decision = {
    ...data.decision, action: 'WATCHLIST', label: 'WATCHLIST', qualifier: '', caveat: '', stance: 'NEUTRAL',
    model_stance: 'BULLISH', new_money: 'WATCH', holder_action: 'HOLD', holdings_known: false,
    primary_blocker: { code: 'RISK_GRADE_F', category: 'risk', text: 'Risk grade F (very high volatility/drawdown) allows no new money', remedy: 'Risk grade improving to D or better' },
    blockers: [
      { code: 'RISK_GRADE_F', category: 'risk', text: 'Risk grade F (very high volatility/drawdown) allows no new money', remedy: 'Risk grade improving to D or better' },
      { code: 'LOW_REWARD_RISK', category: 'reward_risk', text: 'Reward/risk to the target is below the 1.5:1 minimum', remedy: 'Reward/risk of at least 1.5:1 (a better entry or a higher target)' },
    ],
    blocking: ['Risk Grade F blocks new-money BUY → WATCHLIST', 'R/R 0.40 below min 1.50 → WATCHLIST'],
    blocking_codes: ['RISK_GRADE_F', 'LOW_REWARD_RISK'],
    reasons: ['Composite score 67.7 → candidate BUY', 'Risk Grade F blocks new-money BUY → WATCHLIST'],
    sizing: { shares: 0, position_value: 0 },
  };
  return data;
}

test('the hero states model stance, new-money eligibility and the holder action as sent', () => {
  app.renderAnalysis(blockedNvda());
  const hero = document.querySelector('#an-results .decision-action');
  assert.equal(hero.querySelector('.signal').textContent, 'WATCHLIST');
  assert.match(hero.querySelector('[data-contract="model_stance"]').textContent, /Bullish score/);
  assert.match(hero.querySelector('[data-contract="new_money"]').textContent, /Not yet/);
  const holder = hero.querySelector('[data-contract="holder_action"]');
  assert.match(holder.textContent, /Hold, keep the stop/);
  assert.match(holder.getAttribute('title'), /does not know your holdings/);
});

test('the main blocker and its remedy come from the Decision, and the rules table lists every rule', () => {
  app.renderAnalysis(blockedNvda());
  const blocker = document.querySelector('#an-results [data-primary-blocker]');
  assert.equal(blocker.getAttribute('data-primary-blocker'), 'RISK_GRADE_F');
  assert.match(blocker.textContent, /Risk grade F .* allows no new money/);
  assert.match(blocker.textContent, /Would change with: Risk grade improving to D or better/);
  // The rules are a list now, not a table (Research second pass: intended change).
  const rules = [...document.querySelectorAll('#an-results [data-why-not-buy] .gate-name')].map((td) => td.getAttribute('data-gate'));
  assert.deepEqual(rules, ['RISK_GRADE_F', 'LOW_REWARD_RISK']);
  const why = document.querySelector('#an-results [data-why-not-buy]').textContent;
  assert.doesNotMatch(why, /Context check/, 'no browser-rebuilt checks when the engine sent its rules');
});

test('a bearish call reads AVOID with a trim instruction for holders', () => {
  const data = blockedNvda();
  data.final_action = 'AVOID';
  data.decision = { ...data.decision, action: 'AVOID', label: 'AVOID', stance: 'BEARISH', model_stance: 'BEARISH',
    new_money: 'AVOID', holder_action: 'TRIM', blockers: [], blocking: [], blocking_codes: [],
    primary_blocker: { code: 'SCORE_BEARISH', category: 'score', text: 'Overall score is below 45 (bearish)', remedy: 'Overall score recovering above 45' } };
  app.renderAnalysis(data);
  const hero = document.querySelector('#an-results .decision-action');
  assert.equal(hero.querySelector('.signal').textContent, 'AVOID');
  assert.match(hero.querySelector('[data-contract="new_money"]').textContent, /Avoid/);
  assert.match(hero.querySelector('[data-contract="holder_action"]').textContent, /Trim into strength/);
  assert.match(document.querySelector('#an-results .decision-meaning').textContent, /Bearish score: do not buy/);
});

test('an engine-stated missing valuation target is not relabelled as a data-quality failure', () => {
  const data = blockedNvda();
  data.decision = { ...data.decision, levels: { ...data.decision.levels, target: null, reward_risk: 0 },
    primary_blocker: { code: 'INVALID_TARGET', category: 'missing_data', text: 'No valuation target: no analyst consensus or valid DCF for this instrument', remedy: 'An analyst consensus target or a valid DCF' },
    blockers: [], blocking_codes: ['INVALID_TARGET'] };
  data.trade_setup = { ...data.trade_setup, target_base: 0, target_conservative: 0, target_aggressive: 0, risk_reward_ratio: 0 };
  data.valuation = { target_base: 0, target_conservative: 0, target_aggressive: 0 };
  app.renderAnalysis(data);
  const hero = document.querySelector('#an-results .decision-action');
  assert.equal(hero.querySelector('.signal').textContent, 'WATCHLIST');
  assert.doesNotMatch(document.querySelector('#an-results').textContent, /NOT CONFIRMED/);
  assert.match(document.querySelector('#an-results [data-primary-blocker]').textContent, /No valuation target/);
});

test('key levels show only what the Decision published, never the valuation blend in its place', () => {
  const data = blockedNvda();
  data.decision = { ...data.decision, action: 'AVOID', label: 'AVOID', new_money: 'AVOID', holder_action: 'TRIM',
    levels: { reference_price: 138.05, entry: null, stop: null, target: null, reward_risk: null } };
  app.renderAnalysis(data);                       // valuation.target_base (180.06) is still in the payload
  const levels = document.querySelector('#an-results .decision-levels').textContent;
  assert.doesNotMatch(levels, /180\.06/);
  assert.match(levels, /none published by this Decision/);
});
