/**
 * Audit F2: the page shows the ONE level set that bound the decision.
 *
 *  - HST's table read "2.3", its chip "2.25" and its binding reason "1.23".
 *    R:R now prints at the configured 2 dp everywhere, from the binding set.
 *  - The entry zone comes from the engine (binding_levels.entry_zone_low);
 *    the page no longer invents one from ATR or a 2%-of-price fallback.
 *  - The "valid again" remedy is the one the engine built for the gate that
 *    fired (Research 1.50 vs scanner 1.80), with its price triggers.
 *  - The scanner's own levels appear only as a labelled reference.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/binding_levels.test.cjs
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
const pro = require('../../static/elite-pro.js');

const research = {
  source: 'research', label: 'Research levels (binding)', entry: 22.56, entry_zone_low: 22.13, entry_zone_high: 22.56,
  stop: 21.86, target: 23.47, risk_reward: 1.23, rr_basis: 'net of costs at the top of the entry zone',
  horizon_days: 63, target_basis: '12-month valuation target $26.21 pro-rated to 63 sessions',
  triggers: { min_rr: 1.5, entry_needed: 22.48, target_needed: 23.67 },
  remedy: 'Research R:R 1.23 needs 1.50: reached at a spot at or below $22.48 with the stop unchanged, or at a 63-session target at or above $23.67.',
};
const scanner = { source: 'scanner', label: 'Scanner reference levels', entry: 22.51, entry_zone_low: 22.08, entry_zone_high: 22.51,
  stop: 21.33, target: 25.39, risk_reward: 2.25 };

function hst() {
  return {
    ticker: 'HST', company: 'Host Hotels', sector: 'Real Estate', signal: 'HOLD', final_action: 'WATCHLIST', display_action: 'WATCHLIST',
    overall_score: 8.0, conviction: 4, entry_price: 22.56, stop_loss: 21.86, price_target: 23.47, risk_reward_ratio: 1.23,
    allocation: 0, shares: 0, is_funded: false, risk_grade: 'A',
    binding_levels: research, scanner_levels: scanner,
    technical_indicators: { atr: 0.43 },
    final_action_reasons: ['R/R 1.23 below min 1.50 → WATCHLIST', 'Capped at the Research Decision (WATCHLIST) for this name'],
    deep_confirmation: { reason_codes: ['LOW_REWARD_RISK', 'RESEARCH_DECISION_CAP'], scanner_action: 'ACCUMULATE', deep_action: 'WATCHLIST', downgrade_flag: true },
  };
}

function scan(recs) {
  return { scan_status: 'no_picks', total_scanned: 250, currency: '$', capital: 2500, max_positions: 5,
    regime: { type: 'BULL', vix: 15, volatility: 'NORMAL', exposure: 100 }, risk_profile: {},
    recommendations: recs, deep_confirmation: { enabled: true, records: [], downgrades: [], not_confirmed: [], rejected: [] },
    chase_alerts: [], early_momentum: [], error_summary: { records: [] }, funnel: { universe_size: 250, gate_stats: {} } };
}

test('test_rr_rounding_consistent: table, card and chip print the same 2 dp R:R', () => {
  app.renderPicks(scan([hst()]));
  const rootEl = document.querySelector('#picks-results');
  const table = rootEl.querySelector('[data-decision-row]').textContent;
  assert.match(table, /1\.23:1/);
  assert.doesNotMatch(table, /\b1\.2\b(?!3)/, 'no 1-dp rounding of the same ratio');
  // The scanner's ratio survives only inside its labelled reference block.
  const outside = rootEl.cloneNode(true);
  outside.querySelectorAll('[data-scanner-levels]').forEach((el) => el.remove());
  assert.doesNotMatch(outside.textContent, /2\.25:1|2\.3:1/, 'the scanner ratio is not shown as the pick\'s R:R');
  // The same helper prints the table, the card and (through elite-pro) the chip.
  assert.equal(app.fmtRR(1.2345), '1.23:1');
  assert.equal(app.fmtRR(2.25), '2.25:1');
  const proSrc = fs.readFileSync(path.join(root, 'static', 'elite-pro.js'), 'utf8');
  assert.doesNotMatch(proSrc, /qpro-action-strip'/, 'the chip strip that repeated the levels is gone (A12)');
  void pro;
});

test('the entry zone comes from the engine, not from page arithmetic', () => {
  const rec = hst();
  rec.technical_indicators = { atr: 5 };                  // would give a very different zone if used
  app.renderPicks(scan([rec]));
  const table = document.querySelector('#picks-results [data-decision-row]').textContent;
  assert.match(table, /\$22\.13.\$22\.56/);
  assert.equal(typeof app.entryZoneLow, 'undefined', 'the page no longer computes a zone');
  // No binding set: the zone is the entry alone, never a fabricated band.
  const bare = Object.assign(hst(), { binding_levels: null });
  app.renderPicks(scan([bare]));
  assert.doesNotMatch(document.querySelector('#picks-results [data-decision-row]').textContent, /\$\d+\.\d\d.\$22\.56/);
});

test('the remedy is the engine\'s, keyed to the gate that fired', () => {
  app.renderPicks(scan([hst()]));
  const card = document.querySelector('#picks-results .pick-card');
  assert.match(card.textContent, /Research R:R 1\.23 needs 1\.50/);
  assert.match(card.textContent, /\$22\.48/);
  assert.doesNotMatch(card.textContent, /lifts reward-to-risk above the minimum/, 'the generic line is gone');
});

test('the scanner levels appear only as a labelled reference', () => {
  app.renderPicks(scan([hst()]));
  const card = document.querySelector('#picks-results .pick-card');
  const ref = card.querySelector('[data-scanner-levels]');
  assert.ok(ref, 'scanner reference block present');
  assert.match(ref.textContent, /Scanner reference levels/);
  assert.match(ref.textContent, /not binding/i);
  assert.match(card.querySelector('[data-levels-source]').textContent, /Research levels \(binding\)/);
});

test('A16: the risk-check audit names the binding level set and horizon, not "scanner levels remain the basis"', () => {
  const data = scan([hst()]);
  data.deep_confirmation.records = [{ ticker: 'HST', scanner_action: 'ACCUMULATE', deep_action: 'WATCHLIST', final_action: 'WATCHLIST',
    downgrade_flag: true, allocation_before: 0, allocation_after: 0, reason: 'R/R below the Research minimum', deep_mode: 'fast' }];
  app.renderPicks(data);
  const audit = document.querySelector('#picks-results .deep-confirm-summary');
  assert.ok(audit, 'audit section rendered');
  assert.doesNotMatch(audit.textContent, /remain the basis/);
  assert.match(audit.textContent, /Levels used: Research levels \(binding\), 63-session horizon: 1 row/);
  const bare = Object.assign(hst(), { binding_levels: null });
  const d2 = scan([bare]);
  d2.deep_confirmation.records = data.deep_confirmation.records;
  app.renderPicks(d2);
  assert.match(document.querySelector('#picks-results .deep-confirm-summary').textContent, /Levels used: no binding level set published: 1 row/);
});
