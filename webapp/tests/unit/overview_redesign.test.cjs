/**
 * Overview redesign (October 2026). Each test is a contradiction or unit error
 * from the trader review that was on screen: two names for one regime, an
 * "Elevated" risk with no stated reason beside a calm VIX, "Lean Long" with a
 * defensive-looking band, a yield change in percent, a crypto "regular
 * session", "$" on the VIX, a mixed-tape story on an all-green tape, and every
 * headline marked High.
 *
 * Run: node --test tests/unit/overview_redesign.test.cjs
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
const app = require('../../static/app.js');

const factors = [
  { key: 'trend', label: 'Trend', score: 1, note: 'S&P 500 above 200-DMA' },
  { key: 'volatility', label: 'Volatility', score: 1, note: 'Volatility is calm (14.8)' },
  { key: 'sector', label: 'Sector Leadership', score: 1, note: 'Leadership is broad' },
  { key: 'catalyst_risk', label: 'Catalyst Risk', score: -1, note: 'Heavy earnings week' },
  { key: 'participation', label: 'Participation', score: 0, note: 'Participation mixed' },
  { key: 'rates_credit', label: 'Rates / Credit', score: 0, note: '10Y Yield stable (+1.3 bp)' },
];
// The state the owner reviewed: a Lean Long posture read with low confidence.
function market(engine = {}) {
  return {
    market: 'US', vix_level: 14.84, participation_pct: 50,
    participation: { above: 2, measured: 4, missing: [] },
    quotes: [
      { symbol: 'SPY', label: 'S&P 500', price: 778.57, change_pct: 0.6, quote_date: '2026-10-09' },
      { symbol: 'QQQ', label: 'Nasdaq 100', price: 751.27, change_pct: 0.49, quote_date: '2026-10-09' },
      { symbol: 'IWM', label: 'Russell 2000', price: 278.94, change_pct: 0.49, quote_date: '2026-10-09' },
      { symbol: '^VIX', label: 'VIX', price: 14.84, change_pct: -3.7, quote_date: '2026-10-09' },
      { symbol: '^TNX', label: '10Y Yield', price: 5.24, change_pct: 0.25, quote_date: '2026-10-09' },
      { symbol: 'BTC-USD', label: 'Bitcoin', price: 82486, change_pct: 0.99, quote_date: '2026-10-09' },
    ],
    market_regime_engine: Object.assign({
      marketRegime: 'Mildly Constructive — Low Confidence', marketRegimeBase: 'Mildly Constructive',
      posture: 'Lean Long', modelSignal: 'Moderately Constructive', colorTheme: { toneClass: 'warn' },
      confidenceScore: 29, confidenceLabel: 'Low', lowConfidence: true, confidenceReasons: ['Signals conflict.'],
      regimeScore: 2, factors,
      primaryAction: { primary: 'Trade smaller.', sizing: 'Moderate starter size.', avoid: 'Avoid chasing.', risk: 'Clean stops.' },
      portfolioGuidance: { band: '30-55% gross', headline: 'Stay cautious while data confidence is low', headlineTone: 'warn', actions: [] },
      earningsCalendarKnown: true, newsFeedsLive: true,
    }, engine),
  };
}
const news = { groups: { big_news: ['Fed signals patience on rates as inflation cools', 'Treasury yields jump after strong jobs report', 'Stocks rally as CPI comes in below forecasts', 'Oil prices slide on OPEC output increase']
  .map((title, i) => ({ title, impact: 9, category: 'macro', published: new Date().toISOString(), url: `https://example.com/${i}` })) } };
const intel = { earnings_week: [1, 2, 3, 4, 5].map((i) => ({ symbol: `E${i}`, earnings_in_days: i })) };

test('the regime has one name: the factor panel uses the regime title, not a second wording', () => {
  app.renderMarketRegime(market(), news, intel);
  app.renderSignalCheck(market(), news, intel);
  const title = document.querySelector('#decision-brief .q-regime-title').textContent;
  const sig = document.querySelector('#signal-check-body').textContent;
  assert.equal(title, 'Mildly Constructive');
  assert.doesNotMatch(sig, /Moderately Constructive/);
});

test('direction and confidence are two labelled measures with one sentence tying them', () => {
  app.renderMarketRegime(market(), news, intel);
  const el = document.querySelector('#decision-brief');
  // 3 support, 1 against of 6 factors: net +2 of a possible 12 -> 58/100.
  assert.equal(el.querySelector('.q-measure-v').textContent, '58/100');
  assert.match(el.querySelector('.q-measure-s').textContent, /3 support, 1 against, 2 neutral/);
  assert.match(el.querySelector('.q-conf-num').textContent, /29%/);
  assert.match(el.querySelector('.q-measure-note').textContent, /leans positive, but the inputs disagree/);
});

test('the factor tally reads support / neutral / against and sums to the factor count', () => {
  app.renderSignalCheck(market(), news, intel);
  const tally = [...document.querySelectorAll('#signal-check-body .q-sig-tally span')].map((s) => s.textContent);
  assert.deepEqual(tally, ['3support', '2neutral', '1against']);
});

test('a capped exposure band names the band it replaced and why', () => {
  app.renderPlaybook(market(), news, intel);
  const text = document.querySelector('#allocator-body').textContent;
  assert.match(document.querySelector('#playbook-meta').textContent, /30-55% gross/);
  assert.match(text, /Capped from 50-70% gross while confidence is low/);
});

test('an Elevated risk level lists every reason and names a calm VIX as calm', () => {
  app.renderRiskPanel(market(), news, intel);
  const why = document.querySelector('#risk-body .q-note-block').textContent;
  assert.match(document.querySelector('#risk-meta').textContent, /Elevated/);
  assert.match(why, /^Model confidence is low \(29%\)/, 'the lede names the level; the sentence gives the reasons');
  assert.match(why, /4 high-impact headlines are live/);
  assert.match(why, /Volatility itself is calm \(VIX 14\.8\)/);
  const metrics = [...document.querySelectorAll('#risk-body .q-metric')].map((m) => m.textContent);
  assert.equal(metrics.length, 2, 'VIX and participation are not repeated here');
  assert.match(metrics[0], /Earnings\s*5\s*next 7 days, tracked universe/);
});

test('the ribbon quotes yields in basis points, crypto without a session, and VIX read inversely', () => {
  app.renderMarketPulse(market());
  const cells = [...document.querySelectorAll('#market-pulse .q-rb')];
  const cell = (label) => cells.find((c) => c.querySelector('.q-rb-k').textContent.startsWith(label));
  assert.match(cell('10Y Yield').querySelector('.q-rb-c').textContent, /▲ \+1\.3 bp/);
  assert.match(cell('Bitcoin').querySelector('.q-rb-time').textContent, /^24-hour market/);
  const vix = cell('VIX').querySelector('.q-rb-c');
  assert.match(vix.textContent, /▼/, 'the glyph keeps the real direction');
  assert.ok(vix.classList.contains('bull'), 'a falling VIX takes the supportive colour');
  assert.equal(document.querySelector('#market-pulse .q-rb--breadth'), null);
});

test('yield basis points come from the level and the percent change', () => {
  assert.ok(Math.abs(app.yieldChangeBp(5.24, 0.25) - 1.307) < 0.01);
  assert.ok(app.yieldChangeBp(4.0, -1) < 0);
  assert.equal(app.yieldChangeBp(NaN, 1), null);
  assert.ok(app.isYield('^TNX', '') && app.isYield('X', '10Y Yield') && !app.isYield('SPY', 'S&P 500'));
  assert.ok(app.isCryptoSymbol('BTC-USD', '') && !app.isCryptoSymbol('SPY', 'S&P 500'));
  assert.ok(app.isVolIndex('^VIX') && !app.isVolIndex('VIXY'));
});

test('an all-green tape is a broad advance, not "Mixed tape: indices split"', () => {
  const chain = app.impactChain(market({ colorTheme: { toneClass: 'neutral' } }), {}, {});
  assert.equal(chain.label, 'Broad advance');
  assert.equal(chain.links[0].step, 'Indices rise together');
});

test('feed headlines lose a doubled possessive', () => {
  assert.equal(app.cleanHeadline("Treasury sanctions 17 tankers linked to Iran's 's shadow fleet"), "Treasury sanctions 17 tankers linked to Iran's shadow fleet");
  assert.equal(app.cleanHeadline('Plain title'), 'Plain title');
});

test('the tape names the kind of story instead of repeating "High" on every item', () => {
  const m = market({ marketDrivers: [{ title: 'Inflation fears rise', category: 'macro', direction: 'bearish', impactLevel: 'High' }, { title: 'Chip rally', category: 'stocks', direction: 'bullish', impactLevel: 'Medium' }] });
  app.renderTopHeadlines(m, news);
  const flags = [...document.querySelectorAll('#top-headlines .q-tape-flag')].map((f) => f.textContent);
  assert.deepEqual(flags, ['Macro', 'Stocks, medium']);
});

test('participation reads as uptrend counts: indices by name, sectors, then the movers', () => {
  const m = market();
  m.participation = { above: 2, measured: 4, missing: [], members: [
    { symbol: 'SPY', label: 'S&P 500', above: true }, { symbol: 'QQQ', label: 'Nasdaq 100', above: true },
    { symbol: 'DIA', label: 'Dow', above: false }, { symbol: 'IWM', label: 'Russell 2000', above: false }] };
  app.state.market = 'US';
  app.state.marketContext = { US: { available: true, sectors: [
    { symbol: 'XLK', label: 'Technology', change_pct: 0.5, above_sma50: true }, { symbol: 'XLE', label: 'Energy', change_pct: -1, above_sma50: false }] } };
  app.renderBreadthPanel(m, { movers_up: [{ symbol: 'A', change_pct: 3 }, { symbol: 'C', change_pct: 5 }], movers_down: [{ symbol: 'B', change_pct: -2 }] });
  const body = document.querySelector('#breadth-body');
  const idx = [...body.querySelectorAll('.q-pt-idx li')];
  assert.deepEqual(idx.map((li) => li.querySelector('b').textContent), ['S&P 500', 'Nasdaq 100', 'Dow', 'Russell 2000']);
  assert.deepEqual(idx.map((li) => li.classList.contains('is-up')), [true, true, false, false]);
  assert.match(body.textContent, /2 of 4 in uptrends/);
  assert.equal(body.querySelector('.q-pt-meter'), null, 'the Sectors map owns the sector trend');
  assert.match(body.textContent, /2 up, 1 down/);
  assert.deepEqual([...body.querySelectorAll('.q-pt-movers > div')[0].querySelectorAll('b')].map((b) => b.textContent), ['C', 'A'], 'largest gain first');
  assert.match(body.textContent, /a sample, not the whole market/);
  assert.doesNotMatch(body.textContent, /tagged among the largest movers/, 'the single-sector sentence is gone');
  assert.match(document.querySelector('#breadth-meta').textContent, /^Mixed: 3 of 6 in uptrends$/);
  app.state.marketContext = {};
});

test('Street candidates show price, target and analyst count; a very wide gap says check', () => {
  app.renderOpportunityBrief({ analyst_picks: [
    { symbol: 'ORCL', name: 'Oracle', recommendation: 'buy', upside_pct: 68.3, price: 150, target_mean: 252.45, analyst_count: 38 },
    { symbol: 'MSFT', name: 'Microsoft', recommendation: 'buy', upside_pct: 20, price: 400, target_mean: 480, analyst_count: 50 },
  ] });
  const rows = [...document.querySelectorAll('#opportunity-brief-body .q-oppbrief-row')].map((r) => r.textContent.replace(/\s+/g, ' '));
  // A plausible target ranks ahead of a very wide gap (often a stale target).
  assert.match(rows[0], /MSFT/);
  assert.match(rows[0], /to Street target/);
  assert.match(rows[1], /150\.00 → \$?252\.45\s*38 analysts/);
  assert.match(rows[1], /check: wide gap/);
});

test('the page has one h1', () => {
  app.renderMarketRegime(market(), news, intel);
  const view = document.querySelector('#view-dashboard');
  assert.equal(view.querySelectorAll('h1').length, 1);
});
