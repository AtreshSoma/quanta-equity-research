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
  effectiveNewsImpact, highImpactNews, pulseInterp, newsMeaning, upsideCell, impactChain,
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

test('a bearish news tilt on a green tape is read as the tape: a broad advance, never a risk-off story', () => {
  // The 2026-09-25 tape: every index up, VIX down 5%, bitcoin slightly red.
  const market = {
    vix_level: 14.87,
    quotes: [
      { symbol: 'SPY', change_pct: 0.54 }, { symbol: 'QQQ', change_pct: 0.46 },
      { symbol: 'IWM', change_pct: 0.11 }, { symbol: 'GLD', change_pct: 0.44 },
      { symbol: '^TNX', change_pct: 0.43 }, { symbol: 'BTC-USD', change_pct: -0.53 },
    ],
    market_regime_engine: { colorTheme: { toneClass: 'neutral' }, newsImpact: { netDirection: 'bearish' } },
  };
  const chain = impactChain(market, {}, {});
  assert.equal(chain.label, 'Broad advance');
  assert.notEqual(chain.label, 'Risk-off rotation');
  // A bearish tilt on a red tape is still the risk-off story.
  const red = { ...market, quotes: market.quotes.map((q) => (['SPY', 'QQQ', 'IWM'].includes(q.symbol) ? { ...q, change_pct: -0.4 } : q)) };
  red.quotes.find((q) => q.symbol === 'QQQ').change_pct = -0.6;
  assert.equal(impactChain(red, {}, {}).label, 'Risk-off rotation');
});

test('the ribbon reads the 10Y with the model\'s own thresholds', () => {
  const read = (ch) => pulseInterp('^TNX', '10Y Yield', ch, 5.18);
  assert.equal(read(0.43).text, 'rising, rate pressure', '+0.43% is what the Rates factor scores as tightening');
  assert.equal(read(0.2).text, 'steady');
  assert.equal(read(0.9).tone, 'bear');
  assert.equal(read(-0.5).text, 'easing, relief'); // no em dash since the styling pass
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
