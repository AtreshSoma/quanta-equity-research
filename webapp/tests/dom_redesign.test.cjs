/**
 * DOM tests for the September 2026 redesign — information architecture and
 * completeness guarantees for the Deep-Dive tear sheet and the Portfolio
 * Builder position list (jsdom, no network).
 *
 * Run: node --test tests/dom_redesign.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// Score-bar animation + Pro-layer injection run in rAF; neither is under test
// here and the Pro layer needs a real window, so the frame never fires.
global.requestAnimationFrame = () => 0;
global.window = undefined;

const app = require('../static/app.js');

function analysisFixture(over = {}) {
  return Object.assign({
    ticker: 'AAPL', company_name: 'Apple Inc.', sector: 'Technology', currency_symbol: '$',
    current_price: 329.93, day_change_pct: 1.53, market_cap: 4.8e12, market_regime: 'SIDEWAYS',
    final_action: 'WATCHLIST', signal: 'HOLD', confidence_score: 42, confidence_level: 'LOW',
    recommendation_text: 'Maintain current position', recommendation_label: 'LOW-CONVICTION HOLD',
    scores: { composite_score: 57.6, technical_score: 60, fundamental_score: 76, valuation_score: 20, quality_score: 85, sentiment_score: 50, risk_score: 56 },
    technical: { rsi: 61, adx: 22, macd_signal: 'BULLISH', trend_direction: 'UP', trend_strength: 'MODERATE', volume_ratio: 1.1, support_level: 310, resistance_level: 339, week_52_low: 169, week_52_high: 339, candlestick_bias: 'BULLISH', candlestick_score: 63, prior_trend: 'UP', candlestick_patterns: [{ name: 'Bullish Engulfing', bias: 'BULLISH', type: 'REVERSAL', reliability: 'MEDIUM', context: 'Buyers overwhelmed sellers' }] },
    fundamental: { pe_ratio: 40, forward_pe: 32, peg_ratio: 3.1, ev_ebitda: 30, roe: 148.75, net_margin: 27.6, revenue_growth_yoy: 16.4, debt_to_equity: 1.5, piotroski_f_score: null, piotroski_components_available: 3, altman_z_score: null, altman_z_status: 'unavailable', altman_z_variant: '' },
    risk: { annualized_volatility: 0.28, beta: 1.1, sharpe_ratio: 0.69, sortino_ratio: 1.01, max_drawdown: -0.33, var_95_daily: -0.027, risk_grade: 'C' },
    monte_carlo: { median_price: 328.9, expected_return: -0.3, probability_profit: 0.49, var_95: -15.9, percentile_25: 310, percentile_75: 348 },
    trade_setup: { entry_price: 329.93, stop_loss: 311.95, target_base: 280.13, target_conservative: 0, target_aggressive: 0, risk_reward_ratio: 0, has_long_reward: false, levels_available: true, is_actionable_entry: false, setup_basis: 'blended 12-month valuation at or below spot — no long reward', time_horizon: 'POSITION' },
    valuation: { target_base: 280.13, target_conservative: 252.1, target_aggressive: 322.1, valuation_label: 'OVERVALUED', dcf_upside: -28 },
    market_intel: { analyst_rating: 'HOLD', buy_ratings: 0, hold_ratings: 0, sell_ratings: 0, target_mean: 0, implied_upside: 0 },
    insider: { insider_sentiment: 'NEUTRAL', recent_buys: 0, recent_sells: 0, net_flow: 0 },
    position_sizing: { recommended_shares: 0, position_value: 0, portfolio_weight: 0, kelly_optimal: 0, is_viable: false, recommendation: 'No new position recommended for this signal' },
    beginner_guidance: { buy_now: 'ONLY AFTER CONFIRMATION', candlestick_timing: 'WAIT', entry_quality: 'Moderate', rr_label: 'Unfavorable', risk_grade: 'C', new_buyer: 'Wait', existing_holder: 'Hold', aggressive_buyer: 'Add only above confirmation level', short_tf: 'Wait', mid_tf: 'Hold', long_tf: 'Conditional Accumulate after confirmation', long_term_stop: 311.95, tactical_stop: 322.56, pullback_entry: 319.07, pullback_stop: 311.95, breakout_entry: 339.79, volume_requirement: '>= 1.5x avg', data_quality: 'WARN', warnings: ['Analyst target freshness not verified.'], main_reason: 'Fundamentals are strong but timing is unclear; defer entry.' },
    thesis: { summary: 'Hold', bull_case: ['Strong profitability'], bear_case: ['Valuation stretched'], risks: ['Risk score 30'] },
    signal_explanation: { primary_reason: 'Base target -15.1% below current price', supporting_factors: ['Technical score 60'], risk_factors: ['Risk score 30'], overrides_triggered: [] },
    catalysts: { earnings_date: '', days_to_earnings: null, last_earnings_result: '' },
    sentiment: { sentiment_label: 'NEUTRAL', key_headlines: [] },
    portfolio_outlook: { recommendation: 'LOW-CONVICTION HOLD', bottom_line: 'Wait for confirmation.' },
    key_insights: ['HOLD signal with 42% confidence'], action_items: ['Maintain current position'], risk_warnings: ['Risk score 30 (elevated risk)'],
    price_chart: { period: '6mo', points: Array.from({ length: 30 }, (_, i) => ({ date: `2026-08-${String(i + 1).padStart(2, '0')}`, close: 300 + i })), session: { open: 326.95, day_high: 330.02, day_low: 324.11, prev_close: 324.96, ltp: 329.93, volume: 41300000, range_low: 195, range_high: 331, range_bars: 120, date: '2026-09-02' } },
    price_reconciliation: { discrepancy: false, note: 'Analysis price and session quote agree.' },
    cache: { hit: false }, fast_mode: true, data_quality_status: 'preliminary',
  }, over);
}

function kickers() {
  return Array.from(document.querySelectorAll('#an-results .section-kicker, #an-results .qpro-kicker'))
    .map((n) => (n.firstChild ? n.firstChild.textContent : n.textContent).trim());
}

test('Deep-Dive reads Summary → Decision detail → Business → … → Evidence, every section present', () => {
  app.renderAnalysis(analysisFixture());
  // Chapters follow the tab order (Research redesign: Decision detail leads), each tagged with its layer.
  const chapters = Array.from(document.querySelectorAll('#an-results .ts-chapter')).map((n) => n.dataset.ch);
  assert.deepEqual(chapters, ['decision', 'thesis', 'fundamentals', 'valuation', 'scenarios', 'technicals', 'risks', 'catalysts', 'evidence']);
  const layer = (k) => document.querySelector(`#ts-${k}`).dataset.layer;
  assert.equal(layer('decision'), 'Authoritative');
  assert.equal(layer('fundamentals'), 'Facts');
  assert.equal(layer('thesis'), 'Facts & interpretation');

  // Every core section still renders, inside the right chapter.
  const inChapter = (key) => Array.from(document.querySelectorAll(`#ts-${key} .section-kicker, #ts-${key} summary`))
    .map((n) => (n.firstChild ? n.firstChild.textContent : n.textContent).trim());
  // Sentence case since the styling pass (intended change).
  const want = {
    fundamentals: ['Fundamental quality'], valuation: ['Valuation multiples'],
    technicals: ['Candlestick patterns'], catalysts: ['News & catalysts'],
    risks: ['Risk profile'], scenarios: ['Price targets & simulation'],
    decision: ['Why not Buy?', 'Action plan', 'Position sizing', 'Plain-language guidance', 'Beginner guidance', 'Portfolio outlook'],
    evidence: ['Model evidence', 'Data confidence & validation', 'Data confidence', 'Source ledger'],
  };
  for (const [key, titles] of Object.entries(want)) {
    const got = inChapter(key);
    for (const t of titles) assert.ok(got.some((k) => k.startsWith(t)), `"${t}" missing from ${key}; got: ${got.join(' | ')}`);
  }
  // The price chart sits beside the Decision, above the tabs, not inside
  // Technicals (Research layout 2026-10-09: intended change). Without the live
  // chart module the same slot holds the static chart.
  assert.match(document.querySelector('#an-results #ts-chart').textContent, /Price chart/);
  // The at-a-glance grid beside the Decision holds the technical signals, the
  // session quote, the scores and performance vs the benchmark (2026-10-09:
  // intended change; Technicals keeps the candlestick detail).
  const glance = document.querySelector('#an-results .ts-glance');
  for (const sel of ['[data-signals]', '[data-session]', '.ts-scores']) assert.ok(glance.querySelector(sel), `${sel} missing from the glance grid`);
  // The duplicate Executive Summary is folded into the Decision block.
  assert.ok(!inChapter('decision').some((k) => k.startsWith('Executive Summary')));

  // Section tabs: one per panel, no chip cloud. The tabs are plain names; the
  // Decision rail beside them carries the call, so a tab holds no reading
  // (Research second pass: intended change).
  const navTabs = Array.from(document.querySelectorAll('#an-results .ts-nav [role="tab"]'));
  const nav = navTabs.map((a) => document.getElementById(a.getAttribute('aria-labelledby')).textContent);
  assert.deepEqual(nav, ['Decision detail', 'Business', 'Valuation', 'Technicals', 'Risks & catalysts', 'Evidence & data']);
  navTabs.forEach((a) => assert.equal(a.textContent.trim(), document.getElementById(a.getAttribute('aria-labelledby')).textContent, 'tab carries more than its name'));
  assert.equal(document.querySelectorAll('#an-results .ts-nav [aria-selected="true"]').length, 1);

  // The summary precedes everything; the session quote board opens Technicals.
  const hero = document.querySelector('#an-results .decision-hero');
  assert.ok(hero, 'hero missing');
  const session = document.querySelector('#an-results .ts-glance [data-session]');
  assert.ok(session && session.querySelector('.quote-board'), 'session quote board missing from the glance grid');
  assert.ok(session.querySelector('.range-meter'), 'range meter missing from the glance grid');
  // Validation/audit content lives at the end, not above the verdict.
  const validation = document.querySelector('#an-validation');
  assert.ok(validation, 'validation section missing');
  assert.ok(hero.compareDocumentPosition(validation) & 4, 'validation must follow the hero'); // FOLLOWING
});

test('Decision block renders only the authoritative Decision, with horizon, evidence and freshness', () => {
  const decision = {
    action: 'WATCHLIST', label: 'WATCHLIST', caveat: '', stance: 'NEUTRAL',
    horizon: { key: 'position', label: 'Position (~3 months)', trading_days: 63 },
    evidence: { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false, summary: 'No out-of-sample validation has been run.' },
    freshness: { status: 'fresh', last_bar: '2026-09-24', lag_sessions: 0 },
    levels: { reference_price: 329.93, entry: 329.93, stop: 311.95, target: 280.13, reward_risk: 0 },
    reasons: ['Composite score 57.6 → candidate ACCUMULATE', 'Target 280.13 <= entry 329.93 → WATCHLIST'],
    blocking: ['Preliminary analysis; full checks were skipped.'], conflicts: [], scenarios: [],
  };
  app.renderAnalysis(analysisFixture({ decision }));
  const block = document.querySelector('#an-results .decision-action');
  assert.equal(block.querySelector('.signal').textContent, 'WATCHLIST');
  const facts = block.textContent;
  assert.match(facts, /Position \(~3 months\)/);
  assert.match(facts, /Do not buy yet/);
  // Owner, 2026-10-08: the Track record and Checks facts are gone; the model
  // flag beside the call carries the evidence status, and fresh data is the
  // price's tooltip (a stale bar is stated as a caution row).
  assert.ok(block.querySelector('.model-flag'), 'the call carries the model flag');
  assert.doesNotMatch(block.textContent, /Track record|Checks/);
  assert.match(document.querySelector('#an-results .decision-price-value').getAttribute('title'), /Fresh · last bar 2026-09-24/);
  assert.ok(block.querySelector('a[data-view-link="track"]'), 'evidence links to Model Evidence');
  // Why-this-Decision lists the Decision's own reasons, not legacy commentary.
  const why = document.querySelector('#an-results .decision-why').textContent;
  assert.match(why, /Target 280\.13 is at or below the 329\.93 entry, so there is no upside: moved to Watchlist/);
  assert.match(why, /Fast preview: news, insider, analyst and catalyst checks were skipped/);
  assert.doesNotMatch(why, /→ candidate/);
  // Legacy lines naming another verdict never sit beside the Decision.
  const thesis = document.querySelector('#ts-thesis').textContent;
  assert.doesNotMatch(thesis, /HOLD signal with 42% confidence/);
  // The guidance keeps its detail but carries the Decision label.
  assert.match(document.querySelector('.beginner-card').textContent, /Recommendation\s*WATCHLIST/);
});

test('decisionConsistentLines drops only lines that name a different verdict', () => {
  const d = { action: 'WATCHLIST', label: 'WATCHLIST' };
  assert.deepEqual(app.decisionConsistentLines(['HOLD signal with 45% confidence', 'Strong ROE of 148%', 'Moved to WATCHLIST on timing', 'Buyers stepped in'], d),
    ['Strong ROE of 148%', 'Moved to WATCHLIST on timing', 'Buyers stepped in']);
  assert.deepEqual(app.decisionConsistentLines(['HOLD — keep stop'], { action: 'HOLD_EXISTING', label: 'HOLD EXISTING' }), ['HOLD — keep stop']);
  assert.deepEqual(app.decisionConsistentLines(['HOLD signal'], null), ['HOLD signal'], 'no Decision → nothing withheld');
});

test('fundamentals render in the percent units the engine reports, levels unsigned', () => {
  app.renderAnalysis(analysisFixture());
  const text = document.querySelector('#ts-fundamentals').textContent;
  assert.match(text, /Return on equity\s*148\.8%/);
  assert.match(text, /Net margin\s*27\.6%/);
  assert.match(text, /Revenue growth YoY\s*\+16\.4%/);
  assert.doesNotMatch(text, /14875|2760/);
  assert.match(document.querySelector('#ts-risks').textContent, /Volatility \(Ann\)\s*28\.0%/);
});

test('factor score bars are drawn on the 0-100 scale they are reported in', () => {
  const html = app.scoreBar('Valuation', 20);
  assert.match(html, /data-fill="20"/);
  assert.match(app.scoreBar('Quality', 85.3), /data-fill="85.3"/);
  assert.match(app.scoreBar('Risk', null), /is-na/);
});

test('Deep-Dive never prints a perpetual "--" for a field the payload carries', () => {
  app.renderAnalysis(analysisFixture());
  const text = document.querySelector('#an-results').textContent;
  // Fields carried by the fixture must render as values, not placeholders.
  for (const needle of ['$329.93', '57.6/100', 'Risk Grade', '$280.13', '$311.95', 'No long reward', 'N/A (3/9 inputs reported)', 'N/A (statements unavailable)']) {
    assert.ok(text.includes(needle), `expected "${needle}" in tear sheet`);
  }
  // Fast preview must say so where the optional modules were skipped.
  assert.match(text, /Fast preview/);
  assert.match(text, /News was not fetched in fast preview/);
});

test('Deep-Dive with no long reward shows the finding, not a recomputed R:R', () => {
  app.renderAnalysis(analysisFixture());
  const text = document.querySelector('#an-results').textContent;
  assert.match(text, /No long reward · 0:1/);
  assert.doesNotMatch(text, /recomputed locally/);
});

test('Deep-Dive fast preview no longer fails data quality on optional gaps', () => {
  app.renderAnalysis(analysisFixture());
  assert.equal(document.querySelector('#an-results .dq-fail-banner'), null, 'DQ fail banner must not fire on a WARN-only preview');
  assert.ok(document.querySelector('#an-results .confidence-card'), 'confidence summary card missing');
  assert.match(document.querySelector('#an-results .confidence-card').textContent, /Critical Checks\s*PASS/);
});

test('Deep-Dive surfaces the backend price reconciliation note when prices diverge', () => {
  app.renderAnalysis(analysisFixture({ price_reconciliation: { discrepancy: true, note: 'Analysis was priced at 320.00; the session quote shows 329.93 (+3.10%).' } }));
  assert.match(document.querySelector('#an-results .ds-recon').textContent, /priced at 320\.00/);
});

function picksFixture() {
  const rec = (over) => Object.assign({
    ticker: 'AAPL', company: 'Apple Inc.', sector: 'Technology', signal: 'BUY', final_action: 'BUY', display_action: 'BUY',
    overall_score: 7.4, conviction: 4, entry_price: 100, price_target: 120, stop_loss: 92, risk_reward_ratio: 2.3,
    allocation: 700, allocation_before: 1000, allocation_after: 700, shares: 7, position_pct: 28,
    is_funded: true, deep_mode: 'fast', scanner_action: 'BUY', deep_action: 'BUY', downgrade_flag: false,
    why: 'Passed new-money policy: score 7.4/10, upside 20.0%, R/R 2.3:1, risk B',
    final_action_reasons: [], investment_committee_notes: ['Passed new-money policy'], risk_grade: 'B', market_cap_tier: 'MEGA',
    // [F2] The engine publishes the one level set (and the entry zone) per pick.
    binding_levels: { source: 'research', label: 'Research levels (binding)', entry: 100, entry_zone_low: 97,
      entry_zone_high: 100, stop: 92, target: 120, risk_reward: 2.3, horizon_days: 63 },
  }, over);
  return {
    scan_status: 'success', total_scanned: 30, currency: '$', capital: 2500, max_positions: 5,
    regime: { type: 'BULL', vix: 14.6, volatility: 'NORMAL', exposure: 90 },
    risk_profile: { profile: 'conservative', multiplier: 0.7, adjusted: [{ ticker: 'AAPL', before: 1000, after: 700 }] },
    recommendations: [
      rec({}),
      rec({ ticker: 'MSFT', company: 'Microsoft', sector: 'Technology', final_action: 'WATCHLIST', display_action: 'WATCHLIST', signal: 'HOLD', allocation: 0, allocation_after: 0, allocation_before: 800, shares: 0, is_funded: false, downgrade_flag: true, deep_action: 'WATCHLIST', deep_confirmation_reason: 'Bearish reversal: wait for confirmation', why: 'Bearish reversal: wait for confirmation' }),
    ],
    deep_confirmation: { enabled: true, records: [
      { ticker: 'AAPL', scanner_action: 'BUY', deep_action: 'BUY', final_action: 'BUY', downgrade_flag: false, allocation_before: 1000, allocation_after: 700, reason: 'agreed — no downgrade', deep_mode: 'fast' },
      { ticker: 'MSFT', scanner_action: 'BUY', deep_action: 'WATCHLIST', final_action: 'WATCHLIST', downgrade_flag: true, allocation_before: 800, allocation_after: 0, reason: 'Bearish reversal: wait for confirmation', deep_mode: 'fast' },
    ], downgrades: [], not_confirmed: [], rejected: [] },
    chase_alerts: [{ ticker: 'AAPL', reason: 'gapped up 4%', rsi: 74, momentum_1m: 0.12, distance_from_20dma: 0.09 }],
    early_momentum: [], error_summary: { records: [] }, funnel: { universe_size: 30, opportunities: 5, final: 2, gate_stats: {} },
  };
}

test('Portfolio Builder position list shows every per-name field the review requires', () => {
  const capInput = document.querySelector('#sp-capital');
  if (capInput) capInput.value = '999999'; // must NOT be used — the result carries capital
  app.renderPicks(picksFixture());
  const root = document.querySelector('#picks-results');
  const cards = root.querySelectorAll('.pick-card');
  assert.equal(cards.length, 2);
  const funded = cards[0];
  assert.match(funded.querySelector('.signal-pill').textContent, /BUY/);
  assert.equal(funded.querySelector('[data-funded]').getAttribute('data-funded'), '1');
  assert.match(funded.querySelector('.pd-why').textContent, /Why\s*\S/);
  assert.match(funded.textContent, /\$700/);            // allocation post deep-confirm + mandate
  assert.match(funded.textContent, /\$1,000/);          // the pre-mandate figure is shown struck through
  assert.match(funded.textContent, /\$120\.00/);        // target
  assert.match(funded.textContent, /\$92\.00/);         // stop
  assert.match(funded.textContent, /2\.30:1/);          // R:R
  assert.ok(funded.querySelector('[data-chase-flag]'), 'chase-risk flag missing');
  assert.match(funded.textContent, /Technology/);
  assert.match(funded.textContent, /Of capital\s*28\.0%/);  // 700 / 2500 from the RESULT capital, not the form
  const watch = cards[1];
  assert.equal(watch.querySelector('[data-funded]').getAttribute('data-funded'), '0');
  assert.match(watch.textContent, /Additional risk-check downgrade \(fast checks\)/);
  // Portfolio construction impact + mandate line.
  assert.match(root.querySelector('.portfolio-mandate').textContent, /Conservative mandate/);
  assert.match(root.querySelector('.portfolio-metrics').textContent, /\$2,500/);
});

/* ---------------------------------------------------------------------------
   [A12] One decision row per pick (the quick-reference table repeated every
   value, so its contracts now hold on the row): action, binding reason, one
   level set with source and horizon, allocation and evidence.
   --------------------------------------------------------------------------- */
const row = (card) => card.querySelector('[data-decision-row]');

test('Portfolio Builder leads with a decision and allocation research book', () => {
  const fixture = picksFixture();
  fixture.recommendations[0].setup_type = 'GROWTH';
  fixture.recommendations[0].technical_indicators = { atr: 3 };
  app.renderPicks(fixture);
  const root = document.querySelector('#picks-results');
  assert.equal(root.querySelector('.quickref-table'), null, 'the repeated table is gone');
  const cards = root.querySelectorAll('.pick-card');
  assert.equal(cards.length, 2, 'every published pick has a decision row');
  const labels = [...row(cards[0]).querySelectorAll('dt')].map((dt) => dt.textContent.trim());
  assert.equal(labels[0], 'Decision');
  assert.equal(labels[1], 'Why');
  assert.equal(labels[3], 'Allocation');
  assert.equal(labels[4], 'Evidence');
  const first = row(cards[0]).textContent;
  assert.match(first, /BUY/);
  assert.match(first, /Passed new-money policy/);
  assert.match(first, /\$97\.00–\$100\.00/);   // entry zone as the engine published it
  assert.match(first, /\$92\.00/);
  assert.match(first, /\$120\.00/);
  assert.match(first, /2\.30:1/);
  assert.match(first, /\$700/);
  assert.match(root.querySelector('.portfolio-title').textContent, /proposed position/);
  assert.equal(root.querySelector('.portfolio-diagnostics').open, false);
});

test('each value appears once per pick', () => {
  app.renderPicks(picksFixture());
  const card = document.querySelector('#picks-results .pick-card');
  const count = (re) => (card.textContent.match(re) || []).length;
  assert.equal(count(/\$120\.00/g), 1, 'target once');
  assert.equal(count(/\$92\.00/g), 1, 'stop once');
  assert.equal(count(/2\.30:1/g), 1, 'R:R once');
  assert.equal(card.querySelector('.qpro-action-strip'), null);
  assert.equal(card.querySelector('.qpro-rr-ladder'), null);
});

test('watchlist targets are references and scanner strength cannot override the Decision', () => {
  const fixture = picksFixture();
  fixture.recommendations[0].decision = { action: 'BUY', label: 'BUY', qualifier: 'Unproven model', evidence: { status: 'UNTESTED' }, horizon: { label: '63 trading sessions' } };
  fixture.recommendations[1].decision = Object.assign({}, fixture.recommendations[1].decision, {
    risk_grade: 'F', risk_grade_lookback_days: 504,
    risk_grade_drivers: [{ factor: 'volatility', label: 'Volatility', points: 3, detail: '55% annualised (points above 25% / 35% / 50%)' }],
  });
  app.renderPicks(fixture);
  const root = document.querySelector('#picks-results');
  assert.match(row(root.querySelector('.pick-funded')).textContent, /BUY[\s\S]*Not yet tested/);
  const watch = root.querySelector('.pick-watch');
  assert.match(row(watch).textContent, /Reference levels \(watch only\)/);
  assert.doesNotMatch(watch.textContent, /Trigger/);
  assert.match(watch.textContent, /Risk grade F: Volatility 55% annualised \(\+3\)/);
  assert.doesNotMatch(watch.textContent, /restrictive result controls/);
  assert.match(watch.textContent, /Scanner strength/);
  assert.equal(watch.querySelector('.pick-details').open, false);
});

test('the decision row shows the entry alone when the engine published no zone', () => {
  const fixture = picksFixture();
  fixture.recommendations[0].binding_levels = null;
  app.renderPicks(fixture);
  const r = row(document.querySelector('#picks-results .pick-card')).textContent;
  assert.doesNotMatch(r, /\$98\.00–\$100\.00/);
  assert.match(r, /\$100\.00/);
});

test('picks that carry no capital are marked as watch rows', () => {
  app.renderPicks(picksFixture());
  const cards = document.querySelectorAll('#picks-results .pick-card');
  assert.ok(cards[0].classList.contains('pick-funded'));
  assert.ok(cards[1].classList.contains('pick-watch'));
});

test('the summary carries the concentration warning', () => {
  const fixture = picksFixture();
  fixture.recommendations[1].final_action = 'BUY';
  fixture.recommendations[1].display_action = 'BUY';
  fixture.recommendations[1].allocation_after = 500;
  fixture.recommendations[1].is_funded = true;
  app.renderPicks(fixture);
  assert.match(document.querySelector('#picks-results .portfolio-summary').textContent, /Concentration:\s*Technology/);
});

test('the decision row never manufactures an R:R the engine did not publish', () => {
  const fixture = picksFixture();
  fixture.recommendations[0].trade_setup = { has_long_reward: false };
  fixture.recommendations[0].risk_reward_ratio = 0;
  app.renderPicks(fixture);
  assert.match(row(document.querySelector('#picks-results .pick-card')).textContent, /0:1/);
});

test('the card ticker opens the tear sheet and is keyboard reachable', () => {
  app.renderPicks(picksFixture());
  const btn = document.querySelector('#picks-results .pick-card .ticker-action');
  assert.ok(btn, 'ticker control missing');
  assert.equal(btn.tagName, 'BUTTON');
  assert.equal(btn.dataset.ticker, 'AAPL');
});

test('Research validates the ticker before any request and shows the error under the field', async () => {
  const input = document.querySelector('#an-ticker');
  const err = document.querySelector('#an-ticker-error');
  let fetched = 0;
  global.fetch = async () => { fetched += 1; return { ok: true, json: async () => ({}) }; };
  input.value = '';
  await app.runAnalysis();
  assert.equal(input.getAttribute('aria-invalid'), 'true');
  // The error is linked; the market-routing hint may be linked beside it.
  assert.ok(input.getAttribute('aria-describedby').split(/\s+/).includes('an-ticker-error'));
  assert.equal(err.hidden, false);
  assert.match(err.textContent, /Enter a ticker symbol/);
  input.value = '$$$';
  await app.runAnalysis();
  assert.match(err.textContent, /is not a ticker symbol/);
  assert.equal(fetched, 0, 'no request for an invalid symbol');
  app.setFieldError('#an-ticker', '#an-ticker-error', '');
  assert.equal(err.hidden, true);
  assert.equal(input.getAttribute('aria-invalid'), null);
  delete global.fetch;
});

test('progress text is announced politely and hints are linked to their inputs', () => {
  assert.equal(document.querySelector('#an-status').getAttribute('aria-live'), 'polite');
  assert.equal(document.querySelector('#sp-status').getAttribute('aria-live'), 'polite');
  for (const [id, hint] of [['sp-capital', 'sp-capital-hint'], ['sp-positions', 'sp-positions-hint'], ['sp-risk', 'sp-risk-hint'], ['sp-market', 'sp-market-hint']]) {
    assert.equal(document.querySelector(`#${id}`).getAttribute('aria-describedby'), hint);
    assert.ok(document.querySelector(`#${hint}`), `${hint} exists`);
  }
  // The age chip re-renders every 30s, so it must not be a live region.
  assert.equal(document.querySelector('#freshness-chip').getAttribute('aria-live'), null);
});

test('Deep-Dive with no valuation target says "no target", never "no long reward" (live 2026-09-27)', () => {
  const fx = analysisFixture();
  fx.trade_setup = { ...fx.trade_setup, target_base: 0, has_long_reward: false, risk_reward_ratio: 0 };
  if (fx.beginner_guidance) fx.beginner_guidance = { ...fx.beginner_guidance, rr_label: 'N/A' };
  app.renderAnalysis(fx);
  const text = document.querySelector('#an-results').textContent;
  assert.match(text, /Not available \(no target\)/);
  assert.doesNotMatch(text, /No long reward/);
});

test('A12: one shared evidence and disclaimer note near the results, no repeats', () => {
  const fixture = picksFixture();
  fixture.recommendations[0].signal_strength_caveat = 'Heuristic confidence tier, NOT a validated win probability.';
  fixture.recommendations.forEach((r) => { r.decision = Object.assign({}, r.decision, { evidence: { status: 'UNTESTED' } }); });
  app.renderPicks(fixture);
  const root = document.querySelector('#picks-results');
  const notes = root.querySelectorAll('[data-shared-note]');
  assert.equal(notes.length, 1);
  const t = notes[0].textContent.replace(/\s+/g, ' ');
  assert.match(t, /Model evidence: Not yet tested/);
  assert.match(t, /NOT a validated win probability/);
  assert.match(t, /paper-trade before risking capital/);
  assert.equal(root.querySelectorAll('.picks-disclaimer').length, 0);
  const all = root.textContent;
  assert.equal((all.match(/research risk budget/g) || []).length, 1);
  assert.equal((all.match(/assumes fills at the stop/g) || []).length, 1);
  root.querySelectorAll('.pick-card').forEach((c) => assert.doesNotMatch(c.textContent, /paper-trade|research risk budget/));
  // near the results: directly after the summary, before the first card
  assert.ok(root.querySelector('.portfolio-summary').nextElementSibling.matches('[data-shared-note]'));
});

test('A12: a long result list renders compact rows first and mounts a card on demand', () => {
  const T = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, 'fixtures', 'thresholds.json'), 'utf8'));
  const n = T.UI.COMPACT_LIST_AFTER + 5;
  const fixture = picksFixture();
  const base = fixture.recommendations[0];
  fixture.recommendations = Array.from({ length: n }, (_, i) => Object.assign({}, base, { ticker: `S${i}`, company: `Synthetic ${i}` }));
  fixture.capital = n * 1000;
  app.renderPicks(fixture);
  const root = document.querySelector('#picks-results');
  const lazy = root.querySelectorAll('.pick-lazy');
  assert.equal(lazy.length, n);
  assert.equal(root.querySelectorAll('.pick-card').length, 0, 'no full card until asked');
  assert.match(lazy[3].querySelector('summary').textContent, /S3[\s\S]*BUY[\s\S]*\$700/);
  lazy[3].open = true;
  lazy[3].dispatchEvent(new dom.window.Event('toggle'));
  assert.equal(root.querySelectorAll('.pick-card').length, 1);
  assert.ok(lazy[3].querySelector('.pick-card [data-decision-row]'));
  const short = picksFixture();
  app.renderPicks(short);
  assert.equal(root.querySelectorAll('.pick-lazy').length, 0, 'short lists render full cards');
});

test('A12: compact rows do not bring back the duplicate watchlist card', () => {
  const T = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, 'fixtures', 'thresholds.json'), 'utf8'));
  const fixture = picksFixture();
  const base = fixture.recommendations[1];
  fixture.recommendations = Array.from({ length: T.UI.COMPACT_LIST_AFTER + 1 }, (_, i) => Object.assign({}, base, { ticker: `W${i}` }));
  fixture.analyzer_disagreement_watchlist = [{ ticker: 'W0', reason: 'x' }];
  app.renderPicks(fixture);
  require('../static/elite-pro.js');
  globalThis.QuantaPro.injectAnalyzerWatchlist(fixture);
  const stream = document.querySelector('#picks-results .picks-stream');
  assert.ok(stream.querySelector('.pick-lazy'));
  assert.equal(stream.querySelector('.qpro-watchlist-card'), null);
});

test('A12: the Pro cards follow the shared note, so the note stays beside the summary', () => {
  app.renderPicks(picksFixture());
  require('../static/elite-pro.js');
  globalThis.QuantaPro.injectBuilderExtras(picksFixture());
  const root = document.querySelector('#picks-results');
  assert.ok(root.querySelector('.portfolio-summary').nextElementSibling.matches('[data-shared-note]'));
  assert.ok(root.querySelector('[data-shared-note]').nextElementSibling.matches('.qpro-risk-card, .qpro-caveats-card, .qpro-tvexport-card'));
});
