/**
 * Styling pass, Research (docs/audit-ui/STYLING_BRIEF.md): tile heads in
 * sentence case on one line, a one-line purpose, the data-quality failure as
 * the shared notice, and no em dash or tracked caps in what the page writes.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/research_headings.test.cjs
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
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const words = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

// The engine's own text is data; this fixture keeps it free of em dashes so
// any em dash left on the sheet comes from the page.
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
    trade_setup: { entry_price: 329.93, stop_loss: 311.95, target_base: 280.13, target_conservative: 0, target_aggressive: 0, risk_reward_ratio: 0, has_long_reward: false, levels_available: true, is_actionable_entry: false, setup_basis: 'blended 12-month valuation at or below spot: no long reward', time_horizon: 'POSITION' },
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

const render = (over) => { app.renderAnalysis(analysisFixture(over)); return document.querySelector('#an-results'); };
// Words that keep a capital mid-title: the call names and the Decision.
const PROPER = new Set(['Buy', 'Strong', 'Accumulate', 'Watchlist', 'Hold', 'Sell', 'Decision']);
const sentenceCase = (t) => t.split(/\s+/).slice(1).every((w) => {
  const bare = w.replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '');
  return !bare || bare[0] !== bare[0].toUpperCase() || bare === bare.toUpperCase() || PROPER.has(bare);
});

test('the Research purpose is one sentence', () => {
  const sub = words(document.querySelector('#view-analyze .page-sub'));
  assert.equal(sub.split(/(?<=\.)\s/).length, 1, sub);
});

test('every tile head and chapter title is sentence case', () => {
  const box = render();
  const heads = [...box.querySelectorAll('.section-kicker, .qpro-kicker, .ts-chapter-head h2, .ts-nav a, .ts-nav [role="tab"]')]
    .map((n) => (n.firstChild ? n.firstChild.textContent : n.textContent).trim()).filter(Boolean);
  assert.ok(heads.length > 10, 'the sheet rendered its tiles');
  const bad = heads.filter((t) => !sentenceCase(t));
  assert.deepEqual(bad, [], 'Title Case heads left');
});

test('a tile head stays on one line: the tail truncates and carries its full text', () => {
  assert.match(css, /\.section-kicker-tail \{[^}]*white-space: nowrap;[^}]*text-overflow: ellipsis;/);
  assert.match(fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8'), /TRUNCATING_TEXT = '[^']*\.section-kicker-tail/);
});

test('the sheet writes no em dash of its own', () => {
  const box = render();
  const hits = box.textContent.split('\n').map((l) => l.trim()).filter((l) => l.includes('—'));
  assert.deepEqual(hits, []);
});

test('a data-quality failure is the shared notice: one line, the reason behind "Why"', () => {
  const holder = document.createElement('div');
  holder.innerHTML = app.dataQualityFailBannerHtml({ failed: true });
  const n = holder.firstElementChild;
  assert.ok(n.classList.contains('q-notice'), 'shared notice component');
  assert.equal(n.getAttribute('role'), 'alert');
  assert.equal(n.querySelectorAll('.q-notice-t').length, 1);
  assert.doesNotMatch(words(n.querySelector('.q-notice-t')), /—/);
  assert.equal(words(n.querySelector('.q-notice-more')), 'Why');
  assert.equal(n.querySelectorAll('h3').length, 0, 'no second heading inside the notice');
  assert.equal(app.dataQualityFailBannerHtml({ failed: false }), '');
});

test('status chips in the rail are sentence case, without tracking', () => {
  const rule = (css.match(/\.provisional-badge \{[^}]*\}/) || [''])[0];
  assert.doesNotMatch(rule, /text-transform: uppercase/);
  assert.doesNotMatch(rule, /letter-spacing: 0\.08em/);
});

test('in-tile labels and gate chips are not tracked caps, and a data failure is not painted in the price-down red', () => {
  for (const sel of ['.why-not-buy .gate-chip', '.qpro-conflict-verdict .label']) {
    const rule = (css.match(new RegExp(`${sel.replace(/\./g, '\\.')} \\{[^}]*\\}`)) || [''])[0];
    assert.ok(rule, `${sel} rule present`);
    assert.doesNotMatch(rule, /text-transform: uppercase/, sel);
  }
  for (const m of css.matchAll(/\.gate-chip\.(?:fail|partial) \{([^}]*)\}/g)) assert.doesNotMatch(m[1], /--bear/);
});

test('a data failure on the sheet reads in amber, and its answer line has no em dash', () => {
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  for (const sel of ['.decision-verdict', '.decision-verdict .answer']) {
    const rule = (css.match(new RegExp(`\\n${sel.replace(/\./g, '\\.')} \\{[^}]*\\}`)) || [''])[0];
    assert.ok(rule, `${sel} rule present`);
    assert.doesNotMatch(rule, /--bear/, sel);
  }
  assert.doesNotMatch(src, /\$\{escapeHtml\(dqGate\.buyNowAnswer\)\}<\/span> —/);
  assert.match(src, /<span class="tag warn" data-status-chip="dq">/);
  assert.doesNotMatch(src, /const (freshTone|checksTone) = [^;]*'bear'/);
});

test('a tile that falls back to "unavailable" keeps its sentence-case title', () => {
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  const titles = [...src.matchAll(/sectionUnavailableCard\('([^']+)'/g)].map((m) => m[1]);
  assert.ok(titles.length, 'fallback tiles found');
  assert.deepEqual(titles.filter((t) => !sentenceCase(t)), []);
});

test('the elite-pro tiles write no em dash of their own', () => {
  const src = fs.readFileSync(path.join(root, 'static', 'elite-pro.js'), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').filter((l) => !/^\s*\/\//.test(l));
  assert.deepEqual(code.filter((l) => l.includes('—')), []);
});
