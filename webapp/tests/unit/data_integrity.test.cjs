/**
 * QUANTA Elite — pure-function unit coverage (node --test).
 * Targets the strict data-integrity + honesty guarantees from
 * QUANTA_E2E_TEST_SUITE.md (DATA-003/004, DD-005/006, SEC, regression).
 *
 * Run:  node --test webapp/tests/unit/data_integrity.test.cjs
 * (or from webapp/:  node --test tests/unit/data_integrity.test.cjs)
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const {
  fmtPct,
  upsidePct,
  actionMeta,
  finalAllocation,
  isFundedPick,
  classifyFinalAction,
  dataQualityGate,
  friendlyFetchError,
  strategistBrief,
  assetTypeMeta,
} = require('../../static/app.js');

// ---- DATA-003 · numeric formatting never leaks NaN/undefined/Infinity ----
test('fmtPct guards NaN / undefined / null / Infinity', () => {
  for (const bad of [NaN, undefined, null, '', Infinity, -Infinity, 'abc']) {
    assert.equal(fmtPct(bad), '--', `fmtPct(${String(bad)}) must be "--"`);
  }
  assert.equal(fmtPct(0.572, 1, true), '+57.2%');
  assert.equal(fmtPct(57.2, 1, false), '+57.2%');
  assert.equal(fmtPct(-0.05, 1, true), '-5.0%');
  assert.equal(fmtPct(0, 1, true), '0.0%'); // zero has no '+' sign
});

test('upsidePct(target, base) returns percentage points (target - base)/base * 100', () => {
  assert.ok(Math.abs(upsidePct(110, 100) - 10) < 1e-9, `expected 10, got ${upsidePct(110, 100)}`);
  assert.ok(Math.abs(upsidePct(308.82, 310.59) - -0.5699) < 0.01); // below base => negative
  assert.equal(upsidePct(100, 0), null); // base 0 guarded
  assert.equal(upsidePct('x', 100), null); // non-finite guarded
});

// ---- DATA-004 / DD-005 · action metadata is the single source of truth ----
test('actionMeta maps every canonical action and funded flag is consistent', () => {
  // Funded (new capital) actions
  for (const a of ['STRONG_BUY', 'BUY', 'ACCUMULATE']) {
    assert.equal(actionMeta({ final_action: a }).funded, true, `${a} should be funded`);
  }
  // Non-funded / defensive actions must never be funded
  for (const a of ['WATCHLIST', 'HOLD', 'HOLD_EXISTING', 'AVOID', 'INVALID', 'REDUCE', 'SELL']) {
    assert.equal(actionMeta({ final_action: a }).funded, false, `${a} must NOT be funded`);
  }
});

test('actionMeta prefers final_action over raw signal (no scanner-signal leak)', () => {
  // Engine downgraded BUY -> WATCHLIST; display must follow final_action.
  const meta = actionMeta({ signal: 'BUY', final_action: 'WATCHLIST' });
  assert.equal(meta.key, 'watchlist');
  assert.equal(meta.funded, false);
});

test('HOLD and HOLD_EXISTING normalize to the same non-funded display', () => {
  assert.equal(actionMeta({ final_action: 'HOLD' }).key, actionMeta({ final_action: 'HOLD_EXISTING' }).key);
});

// ---- PB-006/007 · allocation trust + phantom-position guard ----
test('finalAllocation trusts post-deep-confirm value', () => {
  assert.equal(finalAllocation({ allocation: 500, allocation_after: 0 }), 0); // downgrade zeroed it
  assert.equal(finalAllocation({ allocation: 250 }), 250);
  assert.equal(finalAllocation(null), 0);
});

test('isFundedPick rejects 0-share / non-funded names', () => {
  assert.equal(isFundedPick({ final_action: 'BUY', shares: 0 }), false);
  assert.equal(isFundedPick({ final_action: 'WATCHLIST', shares: 5 }), false);
  assert.equal(isFundedPick({ final_action: 'BUY', shares: 3, allocation_after: 600 }), true);
});

// ---- DD-006 · data-quality FAIL gate forces an honest, non-bullish verdict ----
test('dataQualityGate: fail + low confidence => provisional / NOT CONFIRMED, never BUY', () => {
  const g = dataQualityGate({ dataQualityFailed: true, confidenceScore: 39, riskGrade: 'F' });
  assert.equal(g.failed, true);
  assert.equal(g.lowConfidence, true);
  assert.equal(g.provisionalHold, true);
  assert.equal(g.buyNowAnswer, 'NO');
  assert.match(g.overrideRecoLabel, /NOT CONFIRMED/i);
  assert.ok(!/\bBUY\b/.test(g.overrideRecoLabel), 'must not surface BUY on a DQ-fail');
});

test('dataQualityGate: passing data does not fabricate a hold', () => {
  const g = dataQualityGate({ dataQualityFailed: false, confidenceScore: 72, riskGrade: 'B' });
  assert.equal(g.failed, false);
  assert.equal(g.provisionalHold, false);
  assert.equal(g.overrideRecoLabel, null);
  assert.equal(g.buyNowAnswer, null);
});

// ---- DD-007 · risk-grade + R/R gates fire correctly ----
test('classifyFinalAction surfaces grade-F / grade-D / weak-RR gates', () => {
  const f = classifyFinalAction({ final_action: 'WATCHLIST', risk: { risk_grade: 'F' } }, null);
  assert.ok(f.gates.some((g) => g.name === 'risk_grade_F'));

  const d = classifyFinalAction({ final_action: 'ACCUMULATE', risk: { risk_grade: 'D' } }, null);
  assert.ok(d.gates.some((g) => g.name === 'risk_grade_D'));

  const rr = classifyFinalAction({ final_action: 'BUY', trade_setup: { risk_reward_ratio: 1.1 } }, null);
  assert.ok(rr.gates.some((g) => g.name === 'risk_reward_below_min'));

  const neg = classifyFinalAction({ final_action: 'HOLD', target_upside_pct: -8 }, null);
  assert.ok(neg.gates.some((g) => g.name === 'negative_target_upside'));
});

// ---- DD-010/011/012 · asset-type classification + caveats ----
test('assetTypeMeta classifies etf/adr/financial and high-vol overlay', () => {
  // isEtf derives from is_etf flag / quote_type (NOT asset_type alone).
  assert.equal(assetTypeMeta({ is_etf: true }).isEtf, true);
  assert.equal(assetTypeMeta({ quote_type: 'ETF' }).isEtf, true);
  assert.equal(assetTypeMeta({ is_etf: true }).assetType, 'etf');
  assert.equal(assetTypeMeta({ asset_type: 'adr' }).isAdr, true);
  assert.equal(assetTypeMeta({ asset_type: 'financial' }).isFinancial, true);
  // high-vol is an independent overlay, not exclusive
  const hv = assetTypeMeta({ asset_type: 'common_stock', is_high_volatility: true });
  assert.equal(hv.isHighVol, true);
});

// ---- Reliability · friendly fetch error maps network failures ----
test('friendlyFetchError maps network failures to a backend-unreachable hint', () => {
  assert.match(friendlyFetchError(new Error('Failed to fetch')), /Cannot reach the QUANTA backend/i);
  assert.equal(friendlyFetchError(new Error('Something specific')), 'Something specific');
});

// ---- DATA-010 · strategist brief stays coherent + never fabricates on empty model ----
test('strategistBrief is null-safe and posture-consistent', () => {
  const empty = strategistBrief({});
  assert.ok(empty && empty.marketType, 'must not throw on empty model');

  const bear = strategistBrief({ postureTone: 'bear', alignment: { bull: 0, bear: 3 }, count: 4 });
  assert.match(bear.marketType.label, /Defensive/i);

  const bull = strategistBrief({ postureTone: 'bull', breadth: 78, vix: 14, alignment: { bull: 3 }, count: 4 });
  assert.match(bull.marketType.label, /Investor's tape/i);
});
