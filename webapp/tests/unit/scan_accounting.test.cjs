/**
 * A04 on the page: the stage table reconciles every scanned name to one
 * outcome, keeps warnings apart from rejections, states unevaluated stages
 * with their configured baseline, and uses words instead of internal keys.
 * Synthetic counts only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/scan_accounting.test.cjs
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
global.filterWords = app.filterWords;
const pro = require('../../static/elite-pro.js');
const T = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'thresholds.json'), 'utf8'));

const stage = (key, entered, passed, threshold) => ({ key, entered, passed, evaluated: entered != null && entered > 0, ...(threshold ? { threshold } : {}) });

function accounting(over = {}) {
  return {
    version: 1, universe: 100,
    could_not_analyse: { count: 6, by_reason: { insufficient_data: 1, analysis_exception: 5 } },
    rule_rejected: { count: 76, by_reason: { score_below_min: 50, signal_not_actionable: 26 } },
    reached_book: 18, not_analysed: 0, not_analysed_detail: { not_reached: 0, other: 0 },
    warnings: { earnings_calendar_missing: 5 },
    book_stages: [
      stage('actionable', 18, 0),
      stage('event', 0, 0),
      stage('risk_grade', 0, 0, { configured: T.BOOK.BANNED_GRADES, value: null }),
      stage('score', 0, 0, { configured: T.SCORE.ACCUMULATE, value: null }),
      stage('risk_reward', 0, 0, { configured: T.RR.SCANNER_MIN, value: null }),
      stage('conviction', 0, 0, { configured: T.BOOK.CONVICTION_FLOOR_SMALL, value: null }),
      stage('cost', null, null, { configured: T.BOOK.COST_VIABILITY_MULTIPLE, value: null }),
      stage('selection', null, null),
      stage('final_review', null, null),
      stage('mandate', 0, 0),
    ],
    funded: 0, watch: 0, published: 0, watch_total: 0, watch_published: 0, max_watch_rows: T.BOOK.MAX_WATCH_ROWS,
    ...over,
  };
}

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

test('every outcome count reconciles to the universe', () => {
  const a = accounting();
  const out = a.could_not_analyse.count + a.rule_rejected.count + a.reached_book + a.not_analysed;
  assert.equal(out, a.universe);
  const t = text(pro.funnelCard({ accounting: a }));
  assert.match(t, /Each of the 100 scanned names has one outcome below; 0 names got a Buy or Accumulate call/);
  assert.match(t, /Could not be analysed 6/);
  assert.match(t, /Screened out 76/);
  assert.match(t, /Reached the book 18/);
  assert.match(t, /Buy or Accumulate call 0 of 18 18 held back/);
});

test('reasons read in words, never as internal keys', () => {
  const h = pro.funnelCard({ accounting: accounting() });
  assert.match(text(h), /score below the minimum 50 · Hold signal, no buy setup 26/);
  assert.doesNotMatch(h, /score_below_min|Score Below Min|signal_not_actionable|=\d/);
  assert.doesNotMatch(h, /action=0|risk grade=0/);
});

test('a warning is shown apart from rejections', () => {
  const h = pro.funnelCard({ accounting: accounting() });
  assert.match(text(h), /Warnings, counted separately: no earnings date available 5/);
  const rejectedRow = h.split('Screened out')[1].split('</tr>')[0];
  assert.doesNotMatch(rejectedRow, /earnings/);
});

test('an unevaluated stage states its configured baseline, never a placeholder', () => {
  const h = pro.funnelCard({ accounting: accounting() });
  const t = text(h);
  assert.match(t, new RegExp(`Score Not evaluated \\(no names reached this stage\\) At least ${T.SCORE.ACCUMULATE * T.SCORE.TO_100} of 100`));
  assert.match(t, new RegExp(`Covers trading costs Not evaluated \\(not reached\\) Expected return at least ${T.BOOK.COST_VIABILITY_MULTIPLE}× the round-trip cost`));
  assert.doesNotMatch(h, /--/);
});

test('the legacy funnel shows the configured minimum instead of "--"', () => {
  const h = pro.funnelCard({ recommendations: [], funnel: { universe_size: 100, opportunities: 0, final: 0, gate_stats: {
    gate1_actionable: 0, gate2_risk_safe: 0, gate3_score_qualified: 0, gate4_rr_qualified: 0, gate5_conviction: 0,
    dynamic_min_score: null, min_rr: null, min_conviction: null }, filter_stats: {} } });
  assert.doesNotMatch(h, /--/);
  assert.match(h, new RegExp(`Score \\(not evaluated; minimum ${T.SCORE.ACCUMULATE * T.SCORE.TO_100} of 100\\)`));
});

test('a capped watch list says how many rows it shows', () => {
  const h = pro.funnelCard({ accounting: accounting({ watch_total: 13, watch_published: 10, max_watch_rows: 10 }) });
  assert.match(text(h), /10 of 13 watch rows shown \(limit 10\)/);
});

test('the empty-scan verdict row carries the reconciled counts once', () => {
  const data = { accounting: accounting({ watch: 2 }), capital: 2500, currency: '$', scan_type: 'quick',
    max_positions: 5, risk_profile: { profile: 'balanced' }, timestamp: '2026-10-03T14:00:00Z' };
  const t = text(app.scanVerdictHtml(data));
  assert.match(t, /Buy or Accumulate 0 Watchlist 2 Could not be analysed 6 Scanned 100 Capital \$2,500/);
  assert.match(t, /Quick scan · up to 5 positions · Balanced mandate · run /);
  assert.equal(app.scanVerdictHtml({}), '');
});

test('the scan verdict in the header is announced as a status', () => {
  const meta = document.querySelector('#picks-meta');
  assert.equal(meta.getAttribute('role'), 'status');
  assert.equal(meta.getAttribute('aria-live'), 'polite');
});

test('a watch-only scan renders every watch row with a non-empty reason', () => {
  const watch = (t) => ({ ticker: t, company: `Synthetic ${t}`, sector: 'Technology', signal: 'ACCUMULATE',
    final_action: 'WATCHLIST', display_action: 'WATCHLIST', allocation: 0, allocation_after: 0, shares: 0, is_funded: false,
    entry_price: 100, stop_loss: 95, price_target: 115, risk_reward_ratio: 3, overall_score: 6.1,
    why: 'Heavy insider selling → WATCHLIST (no new money)', final_action_reasons: ['Heavy insider selling → WATCHLIST (no new money)'] });
  app.renderPicks({ scan_status: 'no_picks', total_scanned: 9, currency: '$', capital: 25000, max_positions: 5,
    regime: { type: 'BULL' }, risk_profile: {}, recommendations: [watch('SYNA'), watch('SYNB')],
    funded_count: 0, watch_count: 2, empty_scan_explanation: 'explained',
    accounting: accounting({ universe: 9, reached_book: 2, funded: 0, watch: 2 }),
    deep_confirmation: { enabled: true, records: [], rejected: [] }, chase_alerts: [], early_momentum: [], error_summary: { records: [] } });
  const root = document.querySelector('#picks-results');
  assert.match(root.querySelector('.portfolio-title').textContent, /No eligible positions/);
  const cards = root.querySelectorAll('.pick-card');
  assert.equal(cards.length, 2);
  cards.forEach((c) => assert.match(c.querySelector('.pd-why').textContent, /Why\s*Heavy insider selling/));
  assert.match(document.querySelector('#picks-meta').textContent, /No positions sized · 2 Watchlist/);
});

test('A12: closest to qualifying states each gap with the exact value needed', () => {
  const h = app.closestToQualifyingHtml({ currency: '$', closest_to_qualifying: [
    { ticker: 'NEAR', company: 'Near Co', gate: 'risk_reward', now: 1.62, needed: 1.8, unit: 'net R:R',
      entry: 100, stop: 95, target: 108.5, entry_needed: 98.76, target_needed: 109.4, shortfall: 0.1 },
    { ticker: 'LOW', company: 'Low Co', gate: 'score', now: 5.1, needed: 5.5, unit: 'score /10', shortfall: 0.07 },
    { ticker: 'WAT', company: 'Watch Co', gate: 'actionable', now: 4.9, needed: 5.5, unit: 'score /10', shortfall: 0.11 },
  ] });
  const t = text(h);
  assert.match(t, /Closest to qualifying/);
  assert.match(t, /NEAR Near Co Reward to risk 1\.62:1, needs 1\.80:1: entry at or below \$98\.76 with stop \$95\.00 and target \$108\.50, or target at or above \$109\.40/);
  assert.match(t, /LOW Low Co Score 51 of 100, needs 55/);
  assert.match(t, /WAT Watch Co Score 49 of 100; Accumulate needs 55/);
  assert.equal(app.closestToQualifyingHtml({ closest_to_qualifying: [] }), '');
});
