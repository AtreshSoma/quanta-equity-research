/**
 * Overview accuracy (September 2026 audit). Every rule here is a bug that was
 * on screen: two "high impact" counts that disagreed, a week-old Fed
 * statement shown as High, a risk-off chain "Confirmed 4/4" on a green tape,
 * a 10Y move called "steady" by the ribbon and "tightening" by the model, an
 * index wrap labelled a single-name catalyst, and target upside in market green.
 *
 * Run: node --test tests/unit/overview_accuracy.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  effectiveNewsImpact, highImpactNews, pulseInterp, newsMeaning, upsideCell, state,
} = require('../../static/app.js');

const DAY = 86400000;
const NOW = Date.parse('2026-09-25T22:00:00Z');

test('headline impact decays with age exactly like the engine (half-life 4 days)', () => {
  const at = (days) => new Date(NOW - days * DAY).toISOString();
  assert.equal(effectiveNewsImpact({ impact: 7, published: at(0) }, NOW), 7);
  assert.ok(Math.abs(effectiveNewsImpact({ impact: 8, published: at(4) }, NOW) - 4) < 1e-9);
  assert.ok(effectiveNewsImpact({ impact: 7, published: at(9) }, NOW) < 3, 'a 9-day-old FOMC statement is Low, not High');
  assert.equal(effectiveNewsImpact({ impact: 9 }, NOW), 9, 'undated items came from this fetch');
});

test('the High Impact list is recency-weighted and de-duplicated across groups', () => {
  const fresh = { title: 'Surging Treasury yields pose a problem for the Fed', impact: 18, published: new Date().toISOString(), url: 'a' };
  const stale = { title: 'Federal Reserve issues FOMC statement', impact: 7, published: new Date(Date.now() - 9 * DAY).toISOString(), url: 'b' };
  const list = highImpactNews({ big_news: [fresh, stale], macro: [fresh] });
  assert.deepEqual(list.map((i) => i.url), ['a']);
});

test('the High impact list is exactly the set the engine counted, so every count agrees', () => {
  // On 2026-10-18 the tab read 10, the risk card 10, the factor 3 and the
  // engine 7: four counts of one thing. With the engine's keys there is one.
  const a = { title: 'Hot PPI shows the Fed is not done', impact: 9, url: 'https://x.test/a', published: new Date().toISOString() };
  const b = { title: 'Stocks slide on chip rout', impact: 8, url: 'https://x.test/b', published: new Date().toISOString() };
  const essay = { title: 'I lost my home in the Great Recession', impact: 7, url: 'https://x.test/c', published: new Date().toISOString() };
  state.dashboard = { market: { market_regime_engine: { newsImpact: { highImpactCount: 2, keys: ['https://x.test/a', 'https://x.test/b'] } } } };
  try {
    const list = highImpactNews({ big_news: [a, b, essay], macro: [a] });
    assert.deepEqual(list.map((i) => i.url), ['https://x.test/a', 'https://x.test/b']);
  } finally {
    state.dashboard = null;
  }
});

test('an index wrap is not a single-name catalyst', () => {
  const wrap = { category: 'stocks', impact: 9, catalyst_label: 'Market Wrap' };
  assert.match(newsMeaning(wrap), /Index-level move/);
  assert.doesNotMatch(newsMeaning(wrap), /Single-name/);
  assert.match(newsMeaning({ category: 'stocks', impact: 5, catalyst_label: 'Stock / Sector Catalyst' }), /Single-name/);
});

test('target upside is labelled as an estimate', () => {
  const html = upsideCell(73.58, 'to Street target', 'Upside to the mean target');
  assert.match(html, /\+73\.6%/);
  assert.match(html, /<small>to Street target<\/small>/);
  assert.match(upsideCell(null, 'to target', 'x'), /N\/A/);
});
