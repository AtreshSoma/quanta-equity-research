/**
 * Research page: failure diagnostics, listing resolution, and the research
 * facts the tear sheet now shows (target build-up, benchmark-relative
 * performance, risk-grade drivers, invalidation) — all rendered as published.
 *
 * Run: node --test tests/unit/research_page.test.cjs
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

const rvii = () => ({
  status: 'insufficient_data', category: 'insufficient_data', data_status: 'insufficient_history',
  ticker: 'RVII', provider: 'yfinance', title: 'RVII is too new to score',
  error_message: 'Only 31 daily bar(s) of history since 2026-08-13 for RVII; the model needs at least 50 to score trend, volatility and risk.',
  suggested_action: 'Research becomes available after 19 more trading session(s). The facts above are what the provider has so far.',
  retryable: false, attempts: 1, alternatives: [], can_switch_market: false,
  facts: { requested_symbol: 'RVII', resolved_symbol: 'RVII', symbol_note: 'No NSE or BSE listing found for RVII; analysed the US listing.',
    company_name: 'Robinhood Ventures Fund II', quote_type: 'EQUITY', exchange: 'NYQ', currency: 'USD', currency_symbol: '$',
    last_price: 22.11, market_cap: 176880000, history_bars: 31, first_bar_date: '2026-08-13', last_bar_date: '2026-09-25' },
});

test('a too-new listing shows what is known and when research becomes available', () => {
  document.getElementById('an-results').innerHTML = '';
  assert.equal(app.handleAnalyzeJobError({ error_detail: rvii() }), true);
  const card = document.querySelector('#an-results [data-diagnostic="insufficient_data"]');
  assert.ok(card, 'diagnostic card missing');
  const text = card.textContent;
  assert.match(text, /Too new to score/);
  assert.match(text, /Robinhood Ventures Fund II/);
  assert.match(text, /\$22\.11 · 2026-09-25/);
  assert.match(text, /31 trading sessions since 2026-08-13/);
  assert.match(text, /NYSE/, 'exchange code shown by name');
  assert.match(text, /No NSE or BSE listing found/);
  assert.match(text, /19 more trading session/);
  assert.match(text, /nothing on this page is a recommendation/);
  assert.equal(card.querySelector('[data-diag-action="retry"]'), null, 'no retry for a deterministic state');
  assert.doesNotMatch(text, /did not respond/);
});

test('an index offers its tracking ETF and a provider gap offers retry and the other market', () => {
  const idx = { ...rvii(), category: 'unsupported_instrument', title: '^GSPC is not a stock or fund', alternatives: ['SPY', 'VOO'] };
  app.renderResearchDiagnostic(idx);
  const btns = [...document.querySelectorAll('#an-results [data-diag-action="ticker"]')].map((b) => b.dataset.ticker);
  assert.deepEqual(btns, ['SPY', 'VOO']);
  app.renderResearchDiagnostic({ ...rvii(), category: 'provider_unavailable', retryable: true, can_switch_market: true });
  assert.ok(document.querySelector('#an-results [data-diag-action="retry"]'));
  assert.ok(document.querySelector('#an-results [data-diag-action="market"]'));
});

test('a scan error is never rendered into the Research panel', () => {
  assert.equal(app.handleAnalyzeJobError({ error_detail: { reason: 'cancelled' } }), false);
});

function research(over = {}) {
  return Object.assign({
    ticker: 'MSFT', requested_symbol: 'MSFT', company_name: 'Microsoft Corporation', sector: 'Technology',
    industry: 'Software - Infrastructure', country: 'United States', exchange: 'NMS', currency: 'USD', currency_symbol: '$',
    business_summary: 'Microsoft Corporation develops and supports software, services, devices and solutions.',
    current_price: 516.17, day_change_pct: 3.66, market_cap: 3.83e12, final_action: 'ACCUMULATE', signal: 'ACCUMULATE',
    confidence_score: 63, confidence_level: 'MEDIUM', data_status: 'ok',
    decision: {
      action: 'ACCUMULATE', label: 'ACCUMULATE', qualifier: 'Unproven model', stance: 'BULLISH', model_stance: 'BULLISH',
      new_money: 'ELIGIBLE', holder_action: 'HOLD', holdings_known: false, primary_blocker: null, blockers: [],
      invalidation: ['A daily close below the stop, $485.46', 'Risk grade deteriorating to F'],
      horizon: { key: 'position', label: 'Position (~3 months)', trading_days: 63 },
      evidence: { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false },
      freshness: { status: 'fresh', last_bar: '2026-09-25', lag_sessions: 0 },
      levels: { reference_price: 516.17, entry: 511.01, stop: 485.46, target: 560.27, reward_risk: 1.9, target_basis: '12-month valuation blend', target_horizon_days: 252 },
      reasons: ['Composite score 62.9 → candidate ACCUMULATE'], blocking: [], blocking_codes: [], conflicts: [], scenarios: [],
      sizing: { shares: 11, position_value: 5621 },
    },
    scores: { composite_score: 62.9, technical_score: 57, fundamental_score: 78, valuation_score: 50, quality_score: 85, sentiment_score: 55, risk_score: 30 },
    technical: { rsi: 63, macd_signal: 'BEARISH', trend_direction: 'UP', support_level: 487.23, resistance_level: 517.78, week_52_high: 553.72, week_52_low: 349.2, week_52_basis: 'provider-reported traded range' },
    fundamental: { pe_ratio: 28.7, forward_pe: 21.8, roe: 34, net_margin: 40.3, revenue_growth_yoy: 17.7, debt_to_equity: 0.29, dividend_yield: 0.0076, piotroski_f_score: 8 },
    valuation: { dcf_status: 'unavailable', dcf_upside: 0, valuation_label: 'UNAVAILABLE', target_base: 560.27, target_conservative: 517.78, target_aggressive: 577.26,
      target_components: [{ source: 'analyst_consensus', value: 577.26, weight: 0.7143 }, { source: 'technical_resistance', value: 517.78, weight: 0.2857 }] },
    market_intel: { analyst_count: 52, analyst_rating: 'STRONG_BUY', target_mean: 577.26, target_low: 440, target_high: 870, target_median: 575, buy_ratings: 0, hold_ratings: 0, sell_ratings: 0 },
    risk: { risk_grade: 'C', risk_points: 6, annualized_volatility: 0.29, beta: 0.97, beta_status: 'measured', beta_benchmark: 'SPY', beta_observations: 250, max_drawdown: -0.345,
      grade_drivers: [{ factor: 'volatility', label: 'Volatility', points: 1, detail: '29% annualised' }, { factor: 'beta', label: 'Beta', points: 0, detail: '0.97 vs its benchmark' }] },
    relative_performance: { benchmark: 'SPY', basis: 'total return, dividend-adjusted closes', rows: [
      { window: '3M', stock_pct: 38.66, benchmark_pct: 6.07, excess_pct: 32.59 }, { window: '1Y', stock_pct: 2.01, benchmark_pct: null, excess_pct: null }] },
    trade_setup: { entry_price: 511.01, stop_loss: 485.46, target_base: 560.27, risk_reward_ratio: 1.9, levels_available: true },
    beginner_guidance: { buy_now: 'NO', rr_label: '1.9:1', pullback_entry: 500.04, breakout_entry: 549.2, volume_requirement: '>= 1.5x avg', existing_holder_stop: 485.46 },
    monte_carlo: { median_price: 525.7, mean_price: 529, expected_return: 2.5, probability_profit: 0.57, var_95: -13.5, days_simulated: 30 },
    insider: { insider_sentiment: 'NEUTRAL', data_status: 'measured', recent_buys: 0, recent_sells: 0, net_flow: 0 },
    thesis: { bull_case: [], bear_case: [], risks: [] }, sentiment: {}, catalysts: {},
    signal_explanation: { supporting_factors: [], risk_factors: [], overrides_triggered: [] },
  }, over);
}

test('the tear sheet opens the business right after the Decision detail and shows the target build-up, not a low/high ladder', () => {
  app.renderAnalysis(research());
  const root = document.querySelector('#an-results');
  const chapters = [...root.querySelectorAll('.ts-chapter')].map((n) => n.dataset.ch);
  // Research redesign: Decision detail is the first tab, the business follows (intended change).
  assert.deepEqual(chapters.slice(0, 6), ['decision', 'thesis', 'fundamentals', 'valuation', 'scenarios', 'technicals']);
  assert.match(root.querySelector('[data-business]').textContent, /develops and supports software/);
  const build = root.querySelector('[data-target-build]').textContent;
  assert.match(build, /Analyst consensus \(52 analysts\)/);
  assert.match(build, /Technical resistance \(tactical level\)/);
  assert.match(build, /Weighted 12-month target/);
  assert.doesNotMatch(root.textContent, /Low method estimate/);
});

test('coverage comes from the analyst count, so a covered name is never "unavailable"', () => {
  app.renderAnalysis(research());
  const root = document.querySelector('#an-results');
  assert.doesNotMatch(root.textContent, /reported with 0 analysts/);
  assert.match(root.textContent, /52 analysts/);
  assert.equal(root.querySelector('.analyst-votes'), null, 'no 0/0/0 vote bars when buckets were not returned');
});

test('a DCF that was not measured is not shown as a 0% gap, and dividend yield is a percent', () => {
  app.renderAnalysis(research());
  const rows = [...document.querySelectorAll('#ts-valuation .fact-row')].map((r) => `${r.querySelector('dt').textContent} ${r.querySelector('dd').textContent}`.trim());
  assert.ok(rows.includes('DCF vs price Unavailable'), rows.join(' | '));
  assert.ok(rows.includes('Dividend yield 0.76%'), rows.join(' | '));
});

test('benchmark-relative performance shows n/a, never zero, for an uncovered window', () => {
  app.renderAnalysis(research());
  const card = document.querySelector('#an-results [data-relative-performance]');
  assert.match(card.textContent, /Performance vs SPY/);
  const rows = [...card.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()).join(' '));
  assert.deepEqual(rows, ['3M +38.7% +6.1% +32.6 pts', '1Y +2.0% n/a n/a']);
});

test('risk grade is explained by its drivers, and a funded call lists what would withdraw it', () => {
  app.renderAnalysis(research());
  const drivers = document.querySelector('#an-results [data-grade-drivers]').textContent;
  assert.match(drivers, /Why grade C: 6 of 10 risk points/);
  assert.match(drivers, /Volatility\s*\+1/);
  assert.match(drivers, /not a maximum-loss guarantee/);
  const inv = document.querySelector('#an-results [data-invalidation]').textContent;
  assert.match(inv, /daily close below the stop, \$485\.46/);
});

test('the plan shows only supplied levels, and a rule-free Accumulate says so without an empty table', () => {
  app.renderAnalysis(research());
  const decision = document.querySelector('#ts-decision').textContent;
  assert.doesNotMatch(decision, /not supplied by engine|Pullback Entry R\/R|Low method target/);
  // The valuation target is stated once, in the summary's key levels;
  // the plan below it no longer repeats it.
  assert.match(document.querySelector('#an-results .ts-summary .decision-levels').textContent, /12-month valuation target/);
  assert.doesNotMatch(decision, /12-month valuation target/);
  const why = document.querySelector('#an-results [data-why-not-buy]');
  assert.match(why.textContent, /Overall score 62\.9\/100 is in the Accumulate range/);
  assert.match(why.textContent, /No gate or cap changed this Decision/);
  assert.equal(why.querySelector('table'), null);
});

test('a resolved listing is named on the tear sheet', () => {
  app.renderAnalysis(research({ ticker: 'RELIANCE.NS', requested_symbol: 'RELIANCE', symbol_note: 'No US listing found for RELIANCE; analysed RELIANCE.NS (NSE).', currency_symbol: '₹' }));
  const note = document.querySelector('#an-results [data-symbol-note]');
  assert.ok(note);
  assert.match(note.textContent, /Listing analysed: RELIANCE\.NS/);
});

// [Audit A-22] A tear sheet that fails its own data check must not show an
// "Eligible" rail or a position size beside "Do not use this analysis".
test('a failed data check withholds new money and sizing on the Decision rail', () => {
  app.renderAnalysis(research()); // no portfolio_outlook: the required-section check fails
  const root = document.querySelector('#an-results');
  assert.ok(root.querySelector('.dq-fail-banner'), 'data-quality banner shown');
  const nm = root.querySelector('[data-contract="new_money"]');
  assert.equal(nm.dataset.withheld, 'data-quality');
  assert.match(nm.textContent, /Withheld until data checks pass/);
  assert.doesNotMatch(root.querySelector('.decision-action').textContent, /Eligible \(risk-budget size\)/);
  assert.ok(root.querySelector('[data-sizing-withheld]'), 'sizing withheld');
  assert.doesNotMatch(root.textContent, /\$5,621/);
});

test('a tear sheet that passes its data checks shows the engine’s new-money call and size', () => {
  app.renderAnalysis(research({ portfolio_outlook: { summary: 'Fits a balanced book.' } }));
  const root = document.querySelector('#an-results');
  assert.equal(root.querySelector('.dq-fail-banner'), null, 'this fixture passes every data check');
  const nm = root.querySelector('[data-contract="new_money"]');
  assert.equal(nm.dataset.withheld, undefined);
  assert.match(nm.textContent, /Eligible/);
  assert.equal(root.querySelector('[data-sizing-withheld]'), null);
});
