/**
 * Market read (October 2026 production audit, A-08 / A-09 / A-10 / A-11 / A-12).
 * Every band the Overview colours or labels comes from MARKET.* config, an
 * impact chain is only "Confirmed" by quotes that were measured, a read built
 * from missing inputs shows no confidence number, a missing news read is not
 * "Quiet tape", and no headline carries a fixed trading instruction.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/market_read_config.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {
  impactChain, confidenceRead, confidenceBand, vixTone, participationTone, newsImpactLevel, newsTiltRead,
} = require('../../static/app.js');

const QT = globalThis.__QUANTA_THRESHOLDS__;
const M = QT.MARKET;
const quotes = (o) => Object.entries(o).map(([symbol, change_pct]) => ({ symbol, change_pct }));
// Run fn with MARKET.<key> temporarily set to value.
function withMarket(key, value, fn) {
  const old = M[key];
  M[key] = value;
  try { return fn(); } finally { M[key] = old; }
}

test('the thresholds fixture carries the MARKET group the page reads', () => {
  for (const k of ['VIX_CALM_MAX', 'VIX_STRESS', 'PARTICIPATION_BROAD_PCT', 'PARTICIPATION_WEAK_PCT',
    'CONFIDENCE_HIGH', 'CONFIDENCE_MODERATE', 'NEWS_IMPACT_HIGH', 'NEWS_IMPACT_MARKET_MOVING',
    'CHAIN_CONFIRM_SHARE', 'CHAIN_LEADERSHIP_SPREAD_PCT']) {
    assert.equal(typeof M[k], 'number', `MARKET.${k}`);
  }
});

test('a mixed tape with no quotes confirms nothing (no unconditional links)', () => {
  const chain = impactChain({ quotes: [] }, {}, {});
  assert.equal(chain.label, 'Mixed tape');
  assert.equal(chain.confirmed, 0);
  assert.equal(chain.verified, false);
  assert.ok(chain.links.every((l) => l.check === false));
});

test('a mixed tape is confirmed only by the quotes each link names', () => {
  const spread = M.CHAIN_LEADERSHIP_SPREAD_PCT;
  // Indices split, Nasdaq and S&P within the spread, small caps the other way.
  const split = impactChain({ quotes: quotes({ SPY: 0.1, QQQ: 0.1 + spread / 2, IWM: -0.6 }) }, {}, {});
  assert.equal(split.confirmed, split.total);
  assert.equal(split.verified, true);
  // Every index up together is a broad advance, never "Mixed tape: indices split".
  const together = impactChain({ quotes: quotes({ SPY: 0.5, QQQ: 0.5, IWM: 0.5 }) }, {}, {});
  assert.equal(together.label, 'Broad advance');
  assert.equal(together.verified, true);
  assert.equal(impactChain({ quotes: quotes({ SPY: -0.5, QQQ: -0.6, IWM: -0.4 }) }, {}, {}).label, 'Broad decline');
});

test('an energy shock does not count a missing 10Y quote as rate fear', () => {
  const chain = impactChain({ quotes: quotes({ USO: M.CHAIN_OIL_SHOCK_PCT + 1, SPY: 0.2, QQQ: 0.1 }) }, {}, {});
  assert.equal(chain.label, 'Energy shock');
  assert.equal(chain.links.find((l) => l.step === 'Inflation/rate fear').check, false);
});

test('chain triggers follow config, not page constants', () => {
  const oil = M.CHAIN_OIL_SHOCK_PCT + 0.5;
  assert.equal(impactChain({ quotes: quotes({ USO: oil }) }, {}, {}).label, 'Energy shock');
  withMarket('CHAIN_OIL_SHOCK_PCT', oil + 1, () => {
    assert.notEqual(impactChain({ quotes: quotes({ USO: oil }) }, {}, {}).label, 'Energy shock');
  });
});

test('VIX and participation tones follow MARKET bands', () => {
  assert.equal(vixTone(null), '');
  assert.equal(vixTone(M.VIX_CALM_MAX - 0.1), 'bull');
  assert.equal(vixTone(M.VIX_CALM_MAX), 'warn');
  assert.equal(vixTone(M.VIX_STRESS), 'bear');
  withMarket('VIX_STRESS', M.VIX_STRESS + 10, () => assert.equal(vixTone(M.VIX_STRESS - 10), 'warn'));
  assert.equal(participationTone(M.PARTICIPATION_BROAD_PCT), 'bull');
  assert.equal(participationTone(M.PARTICIPATION_WEAK_PCT), 'bear');
  assert.equal(participationTone((M.PARTICIPATION_BROAD_PCT + M.PARTICIPATION_WEAK_PCT) / 2), 'warn');
  assert.equal(newsImpactLevel(M.NEWS_IMPACT_MARKET_MOVING), 'high');
  assert.equal(newsImpactLevel(M.NEWS_IMPACT_MEDIUM), 'med');
  assert.equal(newsImpactLevel(0), 'low');
});

test('a read with guidance withheld shows no confidence number', () => {
  const read = confidenceRead({ confidenceScore: 12, confidenceLabel: 'Low', guidanceWithheld: true });
  assert.equal(read.score, null);
  assert.equal(read.withheld, true);
  assert.match(confidenceBand(read.score, read.label, read.withheld), /No read/);
  const live = confidenceRead({ confidenceScore: M.CONFIDENCE_HIGH });
  assert.equal(live.label, 'High');
  assert.match(confidenceBand(live.score, live.label), new RegExp(`${M.CONFIDENCE_HIGH} threshold`));
});

test('a missing news read is unknown, never "Quiet tape"', () => {
  assert.equal(newsTiltRead({}).label, 'News read unavailable');
  assert.equal(newsTiltRead({ newsImpact: { netDirection: 'neutral' }, newsFeedsLive: false }).known, false);
  assert.equal(newsTiltRead({ newsImpact: { netDirection: 'neutral' } }).label, 'Quiet tape');
  assert.equal(newsTiltRead({ newsImpact: { netDirection: 'bearish' } }).tone, 'bear');
});

test('the Overview code restates no market band and gives no per-headline trading instruction', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8');
  const literal = /\b(vix|breadth|part|impact|price)\b\s*(>=|<=|>|<)\s*(?!0(?![.\d]))\d/g; // `> 0` is a validity check, not a band
  const hits = [];
  src.split('\n').forEach((line, i) => {
    if (/\b(vix|breadth|part|impact)\b/.test(line) && literal.test(line) && !/^\s*\/\//.test(line)) hits.push(`${i + 1}: ${line.trim()}`);
    literal.lastIndex = 0;
  });
  // price >= N is allowed outside the VIX note; filter to market-read contexts.
  const marketHits = hits.filter((h) => !/\bprice\b/.test(h) || /vix/i.test(h));
  assert.deepEqual(marketHits, [], marketHits.join('\n'));
  assert.doesNotMatch(src, /function newsResponse/);
  assert.doesNotMatch(src, /<i>Do<\/i>\$\{escapeHtml\(newsResponse/);
});
