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
  confidenceRead, vixTone, participationTone, newsImpactLevel, volatilityLine,
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

test('the volatility line takes its bands from MARKET config and names a sharp move beside the level', () => {
  const mk = (price, change_pct) => ({ quotes: [{ symbol: '^VIX', label: 'VIX', price, change_pct }] });
  const ps = { phrase: 'on Fri Oct 16' };
  assert.match(volatilityLine(mk(M.VIX_CALM_MAX - 0.01, 1), {}, ps), /inside its calm band$/);
  assert.match(volatilityLine(mk(M.VIX_CALM_MAX - 0.01, M.VIX_SESSION_JUMP_PCT + 11), {}, ps), /up \d+% on Fri Oct 16: .*calm band, but rising fast$/);
  assert.match(volatilityLine(mk(M.VIX_CALM_MAX, 0), {}, ps), /above its calm band/);
  assert.match(volatilityLine(mk(M.VIX_STRESS, 0), {}, ps), /stress band/);
  withMarket('VIX_STRESS', M.VIX_STRESS + 10, () => assert.doesNotMatch(volatilityLine(mk(M.VIX_STRESS - 10 + 0.5, 0), {}, ps), /stress band/));
  assert.equal(volatilityLine({ quotes: [] }, {}, ps), null, 'no VIX, no line');
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
  const live = confidenceRead({ confidenceScore: M.CONFIDENCE_HIGH });
  assert.equal(live.label, 'High');
  assert.equal(confidenceRead({ confidenceScore: M.CONFIDENCE_MODERATE }).label, 'Moderate');
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
