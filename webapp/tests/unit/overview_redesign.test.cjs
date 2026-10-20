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

test('the regime has one name: the mood card and the factor panel never use a second wording', () => {
  app.renderMarketRegime(market(), news, intel);
  app.renderSignalCheck(market(), news, intel);
  const pro = document.querySelector('#decision-brief .ovx-proline').textContent;
  const sig = document.querySelector('#signal-check-body').textContent;
  assert.match(pro, /^Mildly Constructive \(Lean Long\)/);
  assert.doesNotMatch(pro + sig, /Moderately Constructive/);
  assert.doesNotMatch(pro, /Low Confidence/, 'the thin-client suffix is not repeated');
});

test('direction, agreement and confidence are three labelled measures, none read as a probability', () => {
  app.renderMarketRegime(market(), news, intel);
  const el = document.querySelector('#decision-brief');
  // 3 support, 1 against of 6 factors: net +2 of a possible 12 -> 58 of 100.
  assert.match(el.querySelector('.ovx-dial').getAttribute('aria-label'), /^Mood score 58 of 100: mixed zone/);
  assert.match(el.querySelector('.ovx-proline').textContent, /Direction 58\/100[\s\S]*Factors: 3 support, 2 neutral, 1 against/);
  assert.match(el.querySelector('.ovx-sure').textContent, /Low[\s\S]*29\/100/);
  assert.doesNotMatch(el.textContent, /29%/);
  assert.match(el.textContent, /Low confidence: Signals conflict\./);
});

test('the factor tally reads support / neutral / against and sums to the factor count', () => {
  app.renderSignalCheck(market(), news, intel);
  const tally = [...document.querySelectorAll('#signal-check-body .q-sig-tally span')].map((s) => s.textContent);
  assert.deepEqual(tally, ['3support', '2neutral', '1against']);
});

test('the band is the engine’s, and a cap is named only with the engine’s own reason', () => {
  app.renderMarketRegime(market(), news, intel);
  assert.match(document.querySelector('#decision-brief .ovx-money').getAttribute('aria-label'), /Invest 30 to 55 percent/);
  assert.match(document.querySelector('#decision-brief .ovx-money-k').textContent, /Invest 30 to 55%/);
  assert.equal(document.querySelector('#decision-brief .ovx-cap'), null, 'no cap is inferred from a second band table');
  const guided = { band: '30-55% gross', cappedFrom: '50-70% gross', capReason: 'confidence is low', headline: 'x', headlineTone: 'warn', actions: [] };
  app.renderMarketRegime(market({ portfolioGuidance: guided }), news, intel);
  assert.match(document.querySelector('#decision-brief .ovx-cap').textContent, /Held under the usual 50-70% gross while confidence is low/);
});

test('the Watch list gives the event counts with their universe and the VIX in its band', () => {
  app.renderMarketRegime(market(), news, intel);
  const items = [...document.querySelectorAll('#decision-brief .ovx-watch li')].map((li) => li.textContent);
  assert.match(items[0], /^VIX 14\.84: under \d+, inside its calm band$/);
  assert.match(items[1], /^5 earnings reports ahead: next 7 days, tracked universe$/);
  assert.match(items[2], /^4 high-impact headlines/);
});

test('the pulse quotes yields in basis points, crypto over 24 hours, and colours the fear index inversely', () => {
  app.renderMarketPulse(market());
  const cells = [...document.querySelectorAll('#market-pulse .ovx-pu')];
  const cell = (name) => cells.find((c) => c.querySelector('.ovx-pu-n').textContent.startsWith(name));
  assert.match(cell('10-year yield').querySelector('.ovx-pu-c b').textContent, /^\+1\.3 bp$/);
  assert.match(cell('Bitcoin').querySelector('.ovx-pu-c').textContent, /24h/);
  const vix = cell('Fear index').querySelector('.ovx-pu-c b');
  assert.match(vix.textContent, /^-3\.7/, 'the sign keeps the real direction');
  assert.ok(vix.classList.contains('up'), 'a falling fear index takes the supportive colour');
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

test('feed headlines lose a doubled possessive', () => {
  assert.equal(app.cleanHeadline("Treasury sanctions 17 tankers linked to Iran's 's shadow fleet"), "Treasury sanctions 17 tankers linked to Iran's shadow fleet");
  assert.equal(app.cleanHeadline('Plain title'), 'Plain title');
});

test('participation reads as uptrend counts: indices and sectors apart, then the movers', () => {
  const m = market();
  m.participation = { above: 2, measured: 4, missing: [], members: [
    { symbol: 'SPY', label: 'S&P 500', above: true }, { symbol: 'QQQ', label: 'Nasdaq 100', above: true },
    { symbol: 'DIA', label: 'Dow', above: false }, { symbol: 'IWM', label: 'Russell 2000', above: false }] };
  app.state.market = 'US';
  app.state.marketContext = { US: { available: true, sectors: [
    { symbol: 'XLK', label: 'Technology', change_pct: 0.5, above_sma50: true, weight: 33 }, { symbol: 'XLE', label: 'Energy', change_pct: -1, above_sma50: false, weight: 3 }] } };
  app.state.dashboard = { market: m, news, market_intel: {} };
  app.state.ctxSig = null;
  app.renderMarketContext();
  const rings = [...document.querySelectorAll('#sector-body .ovx-ring')].map((r) => r.getAttribute('aria-label'));
  assert.deepEqual(rings, ['1 of 2 sectors in an uptrend', '2 of 4 major indexes in an uptrend']);
  assert.doesNotMatch(document.querySelector('#sector-body').textContent, /of 6/, 'indices and sectors are never summed into one share');
  // The movers are their own column: largest gain first, no up/down share.
  app.renderMoversRail({ movers_up: [{ symbol: 'A', change_pct: 3 }, { symbol: 'C', change_pct: 5 }], movers_down: [{ symbol: 'B', change_pct: -2 }] });
  const rail = document.querySelector('#movers-body');
  assert.deepEqual([...rail.querySelectorAll('.ovx-mvs > div:first-child .ovx-mv')].map((b) => b.dataset.ticker), ['C', 'A'], 'largest gain first');
  assert.match(rail.textContent, /a sample of the tape, not market breadth/);
  app.state.marketContext = {};
  app.state.dashboard = null;
});

test('without a scan the ideas ask for QUANTA’s own scan and never list the Street’s targets', () => {
  app.state.picks = null;
  app.state.market = 'US';
  app.state.marketContext = { US: { available: true, sectors: [
    { symbol: 'XLK', label: 'Technology', change_pct: -3, above_sma50: false }, { symbol: 'XLE', label: 'Energy', change_pct: 2.5, above_sma50: true }] } };
  app.state.dashboard = { market: market(), news, market_intel: {} };
  app.renderOpportunityBrief({ analyst_picks: [{ symbol: 'ORCL', name: 'Oracle', recommendation: 'buy', upside_pct: 68.3, price: 150, target_mean: 252.45, analyst_count: 38 }] });
  const body = document.querySelector('#opportunity-brief-body');
  assert.doesNotMatch(body.textContent, /ORCL|Oracle/);
  assert.equal(body.querySelector('[data-view-link="picks"]').textContent, 'Run a Picks scan');
  assert.match(body.textContent, /Sectors in an uptrend right now: Energy\. Ideas in these sectors are listed first\./);
  assert.match(body.textContent, new RegExp(`at least ${Number(globalThis.__QUANTA_THRESHOLDS__.RR.SCANNER_MIN).toFixed(1)} times the potential loss`));
  app.state.marketContext = {};
  app.state.dashboard = null;
});

test('buy calls from the last scan lead with names whose sector is in an uptrend, each with its plan', () => {
  app.state.market = 'US';
  app.state.marketContext = { US: { available: true, sectors: [
    { symbol: 'XLK', label: 'Technology', change_pct: -3, above_sma50: false }, { symbol: 'XLE', label: 'Energy', change_pct: 2.5, above_sma50: true }] } };
  app.state.dashboard = { market: market({ posture: 'Defensive', colorTheme: { toneClass: 'bear' } }), news, market_intel: {} };
  const rec = (ticker, sector) => ({ ticker, sector, final_action: 'BUY', allocation: 1200, position_value: 1200, entry_price: 100, price_target: 120, stop_loss: 92, risk_reward_ratio: 2.5 });
  app.state.picksSavedAt = null;
  app.state.picks = { currency: '$', recommendations: [rec('LRCX', 'Technology'), rec('XOM', 'Energy')] };
  app.renderOpportunityBrief({});
  const cards = [...document.querySelectorAll('#opportunity-brief-body .ovx-idea')];
  assert.deepEqual(cards.map((c) => c.querySelector('.ovx-idea-t').textContent), ['XOM', 'LRCX']);
  assert.match(cards[0].textContent.replace(/\s+/g, ' '), /Stop 92\.00\s*Buy 100\.00\s*Target 120\.00/);
  assert.match(cards[0].textContent, /Sector trendUp/);
  assert.match(cards[1].textContent, /Sector trendDown/);
  app.state.picks = null;
  app.state.marketContext = {};
  app.state.dashboard = null;
});

test('the page has one h1', () => {
  app.renderMarketRegime(market(), news, intel);
  const view = document.querySelector('#view-dashboard');
  assert.equal(view.querySelectorAll('h1').length, 1);
});
