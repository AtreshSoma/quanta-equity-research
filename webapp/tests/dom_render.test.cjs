/**
 * DOM render tests — jsdom-backed verification that every dashboard section
 * renders real content (not skeletons, not "undefined") from a canonical
 * backend payload, and that failure paths degrade honestly.
 *
 * Run: node --test tests/dom_render.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

// ---------------------------------------------------------------------------
// Boot a jsdom document from the real template, then load app.js against it.
// Scripts are NOT executed by jsdom (Jinja tags in the HTML are inert).
// ---------------------------------------------------------------------------
const html = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// NOTE: `window` is deliberately NOT set: app.js skips its DOMContentLoaded
// bootstrap when window is undefined, which is exactly what we want here.

const app = require('../static/app.js');

test('ticker rows with button semantics open from Enter and Space', () => {
  const row = document.createElement('div');
  row.className = 'ticker-action';
  row.setAttribute('role', 'button');
  row.dataset.ticker = 'AAPL';
  document.body.appendChild(row);
  let clicks = 0;
  row.addEventListener('click', () => { clicks += 1; });
  app.bindTickerKeyboardActions();
  for (const key of ['Enter', ' ']) {
    row.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true }));
  }
  assert.equal(clicks, 2);
  row.remove();
});

// Canonical engine payload (mirrors _build_market_regime_engine output shape).
function engineFixture(overrides = {}) {
  return Object.assign({
    marketRegime: 'Constructive — Selective',
    marketHeadline: 'Constructive — Selective. Trend and breadth lean supportive.',
    posture: 'Selective Offensive',
    modelSignal: 'Constructive but Selective',
    confidenceScore: 72,
    confidenceLabel: 'High',
    confidenceReasons: [],
    lowConfidence: false,
    actionBrief: 'Buy only after clean confirmation.',
    regimeScore: 5,
    colorTheme: { toneClass: 'bull' },
    factors: [
      { key: 'trend', label: 'Trend', score: 1, status: 'Confirmed', tone: 'bull', note: 'SPY above 200-DMA' },
      { key: 'volatility', label: 'Volatility', score: 1, status: 'Confirmed', tone: 'bull', note: 'VIX calm (14.2)' },
      { key: 'participation', label: 'Participation', score: 0, status: 'Neutral', tone: 'neutral', note: 'Mixed breadth' },
      { key: 'rates_credit', label: 'Rates / Credit', score: -1, status: 'Warning', tone: 'warn', note: 'Yields tightening' },
    ],
    primaryAction: {
      primary: 'Add risk only in confirmed leaders with volume support.',
      sizing: 'Normal size with staged adds.',
      avoid: 'Avoid extended entries.',
      risk: 'Keep stop discipline.',
    },
    portfolioGuidance: {
      band: '60-80% gross', headline: 'Build selectively', headlineTone: 'bull',
      actions: [{ tone: 'bull', text: 'Prioritize leaders' }],
    },
    riskWarnings: ['Yields tightening'],
    outlookTriggers: { strengthens: 'Breadth improves', weakens: 'Trend loses 50-DMA' },
    newsImpactSummary: '1 high-impact bullish headline leads the tape.',
    newsImpact: { netDirection: 'bullish', highImpactCount: 1, bullishHighImpact: 1, bearishHighImpact: 0 },
    marketDrivers: [{ title: 'Fed signals patience on rates', category: 'macro', direction: 'bullish', impactLevel: 'High', marketMeaning: 'Supportive for risk', source: 'WSJ', url: 'https://example.com/a' }],
    catalystRisks: [],
    sectorLeadership: { label: 'Confirmed', tone: 'bull', note: 'Leadership broad' },
    sourceHealth: { newsSourcesOk: 8, newsSourcesTotal: 10, quotesOk: 9, quotesTotal: 9, healthPct: 89.5, state: 'healthy' },
    overridesTriggered: [],
    shortSummary: 'Constructive: 2/4 supportive factors.',
    detailedReasoning: 'Trend: Confirmed.',
    dataFreshness: { state: 'healthy', coveragePct: 100, missingInputs: [], conflictCount: 1, stale: false, newsConflict: false },
    timeframe: 'Swing-to-position horizon (days to a few weeks)',
    debug: {},
  }, overrides);
}

function marketFixture(engineOverrides = {}) {
  return {
    market: 'US', label: 'United States', tone: 'Risk-on',
    vix_level: 14.2, ten_year_yield: 4.3, participation_pct: 62,
    quotes: [
      { symbol: 'SPY', label: 'S&P 500', price: 580.1, change_pct: 0.6 },
      { symbol: 'QQQ', label: 'Nasdaq 100', price: 500.2, change_pct: 0.9 },
      { symbol: '^VIX', label: 'VIX', price: 14.2, change_pct: -2.0 },
      { symbol: 'GLD', label: 'Gold', price: 210.0, change_pct: 0.2 },
    ],
    market_regime_engine: engineFixture(engineOverrides),
  };
}

const newsFixture = {
  groups: {
    big_news: [{ title: 'Fed signals patience on rates', impact: 8, direction: 'bullish', category: 'macro', source: 'WSJ', published: new Date().toISOString(), url: 'https://example.com/a' }],
    latest: [],
  },
  source_status: [{ name: 'WSJ', ok: true }],
};

const intelFixture = {
  movers_up: [{
    symbol: 'NVDA', name: 'NVIDIA', price: 140.2, change_pct: 4.2,
    market_cap: 3.4e12, volume: 2.1e8,
    open: 135.1, day_high: 141.9, day_low: 134.6, prev_close: 134.55, change_abs: 5.65,
  }],
  movers_down: [{ symbol: 'XYZ', name: 'XYZ Corp', price: 12.1, change_pct: -6.3, market_cap: 2.0e9, volume: 9.0e6 }],
  earnings_week: [{ symbol: 'AAPL', earnings_in_days: 3, sector: 'Technology' }],
  analyst_picks: [],
};

test('Market Regime renders the call, its direction and confidence, and the strategy', () => {
  app.renderMarketRegime(marketFixture(), newsFixture, intelFixture);
  const el = document.querySelector('#decision-brief');
  const text = el.textContent;
  assert.ok(el.querySelector('.q-regime-title'), 'regime title node present');
  assert.match(text, /Constructive — Selective/);
  assert.match(text, /Confidence/);
  assert.ok(!el.querySelector('.q-skel'), 'skeleton cleared');
  assert.ok(!/undefined|NaN|null/.test(text), `no raw undefined/NaN in: ${text.slice(0, 200)}`);
  // Two labelled measures: direction (the factor sum, 0-100) and confidence.
  // Index levels, VIX and participation live in the ribbon and their panel only.
  assert.match(el.querySelector('.q-measure-v').textContent, /^\d+\/100$/);
  assert.match(text, /Direction/);
  assert.match(el.querySelector('.q-measure-s').textContent, /\d+ support, \d+ against, \d+ neutral/);
  assert.ok(el.querySelector('.q-measure-note').textContent.length > 30, 'one sentence ties the two measures');
  assert.equal(el.querySelectorAll('.q-metrics').length, 0, 'no repeated price strip');
  assert.equal(el.querySelectorAll('h1').length, 0, 'the page has one h1; the regime is an h2');
  // The evidence lives once, in the factor table and "What would change the
  // call"; the card links to it instead of folding a copy behind "+".
  assert.equal(el.querySelectorAll('details.q-disclose').length, 0, 'no folded copies of other panels');
  assert.ok(el.querySelector('.q-textlink[data-jump="signal-check"]'), 'a link to the factor table');
  // De-dup: the regime panel must not restate Signal Check or Market Drivers.
  assert.ok(!el.querySelector('.q-fact'), 'no factor bars duplicated into the regime panel');
  assert.ok(!/Top Market Drivers/.test(text));
});

test('Market Regime surfaces backend risk caps as an inline flag', () => {
  app.renderMarketRegime(marketFixture({
    posture: 'Neutral',
    marketRegime: 'Mixed Market',
    colorTheme: { toneClass: 'neutral' },
    overridesTriggered: ['VIX 26.4 > 25 — risk-on/offensive blocked (capped at Neutral)'],
  }), newsFixture, intelFixture);
  const el = document.querySelector('#decision-brief');
  const note = Array.from(el.querySelectorAll('.q-flag')).map((n) => n.textContent).find((t) => /Risk caps active/.test(t));
  assert.ok(note, 'override flag rendered');
  assert.match(note, /VIX 26\.4/);
});

test('Market Regime shows the low-confidence reason even without backend reasons', () => {
  app.renderMarketRegime(marketFixture({
    lowConfidence: true, confidenceScore: 38, confidenceLabel: 'Low', confidenceReasons: [],
    colorTheme: { toneClass: 'neutral' }, marketRegime: 'Mixed Market — Low Confidence',
  }), newsFixture, intelFixture);
  const el = document.querySelector('#decision-brief');
  // The gauge owns the number and where it sits against the model thresholds;
  // the flag owns the reason. Each fact is stated once.
  assert.match(el.querySelector('.q-conf-num').textContent, /38%/);
  assert.match(el.querySelector('.q-conf-band').textContent, /moderate begins at 50/i);
  const fill = el.querySelector('.q-gauge-fill');
  assert.equal(fill.style.width, '38%', 'gauge reads the actual score');
  assert.deepEqual(Array.from(el.querySelectorAll('.q-gauge-tick')).map((t) => t.dataset.at), ['50', '70']);
  const flag = el.querySelector('.q-regime-why');
  assert.ok(flag && /Confidence is low/.test(flag.textContent), 'low-confidence reason rendered');
  assert.ok(flag.textContent.trim().length > 30, 'a reason is present, not a headless banner');
  // The engine suffixes the regime name for thin clients; the panel must not
  // repeat what the gauge and the flag already say.
  assert.ok(!/Low Confidence/.test(el.querySelector('.q-regime-title').textContent), 'title states the regime once');
});

test('Playbook carries stance, exposure band and the avoid rule without pills', () => {
  app.renderPlaybook(marketFixture(), newsFixture, intelFixture);
  const body = document.querySelector('#allocator-body');
  const text = body.textContent;
  assert.match(text, /Selective Offensive/);
  assert.doesNotMatch(text, /60-80% gross/, 'the band is stated once, in the head');
  assert.match(text, /Avoid/);
  assert.ok(body.querySelector('.q-dl'), 'playbook uses a label to value list');
  assert.equal(body.querySelectorAll('.badge, .tag, .chip').length, 0, 'no decorative pills');
  assert.match(document.querySelector('#playbook-meta').textContent, /60-80% gross/);
  assert.ok(!/undefined|NaN/.test(text));
});

test('Risk panel states a level, the numbers behind it, and both triggers', () => {
  app.renderRiskPanel(marketFixture(), newsFixture, intelFixture);
  const body = document.querySelector('#risk-body');
  const text = body.textContent;
  // The level is the head's figure; the body gives reasons, counts and the main risk.
  assert.doesNotMatch(text, /risk level/);
  assert.match(text, /Main risk/);
  assert.doesNotMatch(text, /Breaks the call/, 'the triggers panel owns what changes the call');
  // Event counts only (VIX and participation are stated elsewhere), each with its universe.
  assert.equal(body.querySelectorAll('.q-metrics .q-metric').length, 2);
  assert.match(text, /next 7 days, tracked universe/);
  assert.ok(!/undefined|NaN/.test(text));
  assert.match(document.querySelector('#risk-meta').textContent, /Contained|Moderate|Elevated|High/);
});

test('Risk level escalates with volatility and never reads as price direction', () => {
  const hot = marketFixture();
  hot.vix_level = 27.5;
  app.renderRiskPanel(hot, newsFixture, intelFixture);
  assert.match(document.querySelector('#risk-meta').textContent, /High/);
  assert.match(document.querySelector('#risk-body').textContent, /27\.5/);
});

test('Market Signal renders one diverging bar per factor and an honest 0-10 read', () => {
  app.renderSignalCheck(marketFixture(), newsFixture, intelFixture);
  const body = document.querySelector('#signal-check-body');
  assert.equal(body.querySelectorAll('.q-fact').length, 4);
  assert.equal(body.querySelectorAll('.q-dbar').length, 4, 'every factor gets a score bar');
  assert.match(body.textContent, /Trend/);
  const bullCount = body.querySelector('.q-sig-tally .bull b');
  assert.equal(bullCount.textContent, '2', 'confirmed count matches factors with score>0');
  // The score itself lives in the regime card; the table says how it is built.
  assert.match(body.querySelector('.q-sig-tally').getAttribute('title'), /rescaled/i);
  assert.ok(!body.querySelector('.q-sig-score b'), 'the direction score is not printed twice');
});

test('Market Drivers renders driver rows with meaning + response', () => {
  app.renderMarketDrivers(marketFixture(), newsFixture, intelFixture);
  const body = document.querySelector('#market-drivers-body');
  assert.match(body.textContent, /Fed signals patience/);
  assert.match(body.textContent, /Means/);
  assert.match(body.textContent, /Do/);
});

test('Market Ribbon renders one row of instruments plus breadth', () => {
  app.renderMarketPulse(marketFixture());
  const el = document.querySelector('#market-pulse');
  assert.match(el.textContent, /S&P 500/);
  assert.match(el.textContent, /VIX/);
  assert.ok(!/undefined/.test(el.textContent));
  // Prices only, in a single scrolling row: participation is not a price and
  // lives in the Participation & movers panel.
  assert.equal(el.querySelectorAll('.q-ribbon-scroll').length, 1, 'one ribbon, not a tile grid');
  assert.equal(el.querySelectorAll('.q-rb').length, 4);
  assert.equal(el.querySelector('.q-rb--breadth'), null, 'participation is not repeated in the ribbon');
});

test('The Security Monitor holds no second table of the same movers', () => {
  // It re-rendered movers_up/movers_down, which the board already shows, and
  // its Read/Next-step columns only restated the sign of change_pct. The id
  // survives as the workspace grouping the board with the veto queue — the
  // duplicated table, its bucket tabs and their renderers do not.
  assert.equal(document.querySelector('#movers-body'), null, 'mover table removed');
  assert.equal(document.querySelector('#monitor-tabs'), null, 'bucket tabs removed');
  assert.equal(typeof app.renderMovers, 'undefined', 'renderer removed, not left dormant');
  assert.equal(typeof app.showMonitorPanel, 'undefined', 'tab machinery removed');
  // Every tab in the workspace must point at a panel something actually
  // renders into; empty shells were what made the old monitor unreadable.
  const monitor = document.querySelector('#security-monitor');
  const tabs = [...monitor.querySelectorAll('[role="tab"]')].map((t) => t.getAttribute('aria-controls'));
  assert.deepEqual(tabs, ['top-movers', 'open-extremes', 'monitor-watchlist', 'monitor-extended', 'scanner-vetoes'],
    'no tab without a renderer behind it');
  app.renderSetupStatusCard(intelFixture);
  ['monitor-watchlist-body', 'monitor-extended-body'].forEach((id) => {
    assert.ok(document.getElementById(id).innerHTML.trim(), `${id} is filled, not an empty shell`);
  });
});

test('The scanner veto queue survives the removal in a panel of its own', () => {
  // The one thing the monitor held that nothing else showed: why a name was
  // rejected. It now sits beside Top Opportunities — the scan's other half.
  const panel = document.querySelector('#scanner-vetoes');
  assert.ok(panel, 'veto panel exists');
  assert.ok(panel.contains(document.querySelector('#setup-status-body')), 'veto body lives inside it');
  assert.match(panel.querySelector('.q-head-title').textContent, /Rejected/i);

  app.renderSetupStatusCard(intelFixture);
  const body = document.querySelector('#setup-status-body');
  assert.ok(!body.hidden, 'no longer hidden behind a bucket tab');
  assert.match(body.textContent, /No scanner vetoes on record/i, 'empty state is a clean read');
  assert.match(document.querySelector('#vetoes-meta').textContent, /No vetoes/);
});

test('Top movers says a timed-out feed timed out, not that the market was quiet', () => {
  app.renderTopMovers({ loading: false, movers_up: [], movers_down: [], feed_timeout: true });
  assert.match(document.querySelector('#top-movers-body').textContent, /timed out/i);
  assert.match(document.querySelector('#top-movers-meta').textContent, /Timed out/);
  app.renderTopMovers({ loading: false, movers_up: [], movers_down: [] });
  assert.doesNotMatch(document.querySelector('#top-movers-body').textContent, /timed out/i);
});

test('An extended move is flagged on the board rather than in a repeated column', () => {
  app.renderTopMovers({
    movers_up: [
      { symbol: 'HOT', name: 'Hot Co', price: 60, change_pct: 22.5, open: 50, day_high: 61, day_low: 49, prev_close: 49 },
      { symbol: 'CALM', name: 'Calm Co', price: 30, change_pct: 1.4, open: 29.6, day_high: 30.2, day_low: 29.5, prev_close: 29.6 },
    ],
    movers_down: [],
  });
  const body = document.querySelector('#top-movers-body');
  const flagged = Array.from(body.querySelectorAll('.q-board-row'))
    .filter((tr) => tr.querySelector('.q-board-flag'));
  assert.equal(flagged.length, 1, 'only the extended name carries the flag');
  assert.match(flagged[0].textContent, /HOT/);
  assert.match(body.querySelector('.q-board-flag').textContent, /Extended/);
});

test('Top Opportunities omits trade-plan columns it cannot fill, and says why', () => {
  app.renderTopOpportunities({
    universe_size: 100,
    analyst_picks: [{ symbol: 'NVDA', name: 'NVIDIA', price: 140.2, sector: 'Technology', recommendation: 'buy', target_mean: 170, analyst_count: 42, upside_pct: 21.3 }],
  });
  const body = document.querySelector('#portfolio-scan-body');
  const cols = Array.from(body.querySelectorAll('thead th')).map((n) => n.textContent.trim());
  // Entry/Stop were rendered as a hardcoded N/A on every row — two columns
  // restating one footnote. The footnote is the honest carrier of the gap.
  assert.ok(!cols.includes('Entry') && !cols.includes('Stop'), 'no columns of pure N/A');
  assert.match(body.textContent, /no Picks scan has run/i, 'the reason for the gap is stated');
  assert.ok(!/\$0\.00/.test(body.textContent), 'never a fabricated zero level');
  // Every body row still matches the header width.
  const headCount = body.querySelectorAll('thead th').length;
  body.querySelectorAll('tbody tr.q-tr').forEach((tr) => {
    assert.equal(tr.children.length, headCount, 'row cell count matches the header');
  });
  const detail = body.querySelector('tbody tr.q-detail td');
  assert.equal(Number(detail.getAttribute('colspan')), headCount, 'detail row spans the full table');
});

test('Top Opportunities empty state is compact and actionable', () => {
  app.renderTopOpportunities({ universe_size: 100, analyst_picks: [] });
  const body = document.querySelector('#portfolio-scan-body');
  assert.match(body.textContent, /No Picks scan yet/);
  assert.doesNotMatch(body.textContent, /100 names|do not meet QUANTA entry criteria/i);
  assert.ok(body.querySelector('[data-jump-view="picks"]'), 'offers the scan action');
  assert.equal(body.querySelectorAll('table').length, 0, 'no empty table shell');
});

test('Upcoming Catalysts renders earnings rows and a useful empty state', () => {
  app.renderCatalystCalendar(intelFixture, newsFixture);
  const body = document.querySelector('#event-risk-body');
  assert.match(body.textContent, /AAPL/);
  assert.ok(body.querySelector('table.q-table'), 'catalysts render as a table');
  const cols = Array.from(body.querySelectorAll('thead th')).map((n) => n.textContent.trim()).filter(Boolean);
  assert.deepEqual(cols, ['When', 'Event', 'Affected', 'Category', 'Impact']);
  // [DE-DUP] Catalysts is forward-looking only — it must NOT re-list live news
  // headlines (those live in Market Impact / Market Drivers).
  assert.ok(!/just-broken macro|Live ·/.test(body.textContent), 'no live-macro headlines duplicated in catalysts');
  assert.ok(!/Fed signals patience/.test(body.textContent), 'driver headline not duplicated into catalysts');

  // An empty feed means "nothing was returned", not "nothing is scheduled" —
  // the empty state must not read as an all-clear the data cannot support.
  app.renderCatalystCalendar({ earnings_week: [] }, { groups: {} });
  const emptyText = document.querySelector('#event-risk-body').textContent;
  assert.match(emptyText, /No earnings dates returned/);
  assert.match(emptyText, /does not rule out/i, 'the empty state is not read as an all-clear');
});

test('Top headlines bar renders drivers or an honest quiet state', () => {
  app.renderTopHeadlines(marketFixture(), newsFixture);
  assert.match(document.querySelector('#top-headlines').textContent, /Fed signals patience/);

  app.renderTopHeadlines({ market_regime_engine: engineFixture({ marketDrivers: [] }) }, { groups: {} });
  assert.match(document.querySelector('#top-headlines').textContent, /Headline tape is quiet/);
});

test('Dashboard error state is friendly, not a raw browser error', () => {
  app.renderDashboardError(new TypeError('Failed to fetch'));
  const hero = document.querySelector('#decision-brief');
  assert.match(hero.textContent, /Dashboard temporarily unreachable/);
  assert.match(hero.textContent, /Cannot reach the QUANTA backend/);
  assert.ok(!/Failed to fetch/.test(hero.textContent), 'raw browser error hidden');
  const news = document.querySelector('#news-body');
  assert.match(news.textContent, /Offline/);
});

// ---------------------------------------------------------------------------
// Deep Dive — session quote inside the decision hero
// ---------------------------------------------------------------------------
test('Deep Dive hero carries the session quote board and both range meters', () => {
  const html = app.decisionSessionHtml({
    current_price: 416.8,
    day_change_pct: 3.78,
    price_chart: {
      session: {
        open: 408.2, day_high: 417.75, day_low: 406.5, prev_close: 401.6, ltp: 416.8,
        change_abs: 15.2, change_pct: 3.78, volume: 12_400_000,
        range_low: 340.1, range_high: 470.0, range_bars: 120, date: '2026-09-02',
      },
    },
  }, { sym: '₹' });

  assert.match(html, /Session quote/); // sentence case (Research third pass)
  for (const label of ['Open', 'High', 'Low', 'Prev Close', 'Last']) {
    assert.ok(html.includes(`>${label}<`), `missing the ${label} column`);
  }
  assert.match(html, /₹401\.60/, 'previous close rendered uncompacted');
  assert.match(html, /Day Range/);
  // 120 bars is a 6-month slice — it must not be mislabelled as a 52-week range.
  assert.match(html, /6M Range/);
  assert.ok(!/52W Range/.test(html), 'never claims a year of history it lacks');
  assert.match(html, /Volume 12\.40M/);
  assert.ok(!/undefined|NaN/.test(html));
});

test('Deep Dive session block labels the trailing band by the bars it holds', () => {
  const build = (bars) => app.decisionSessionHtml({
    price_chart: { session: { open: 10, day_high: 12, day_low: 9, prev_close: 10, ltp: 11, range_low: 5, range_high: 15, range_bars: bars } },
  }, { sym: '$' });
  assert.match(build(260), /52W Range/);
  assert.match(build(120), /6M Range/);
  assert.match(build(30), /30-Bar Range/);
});

test('Deep Dive session block falls back to top-level price when session is absent', () => {
  const html = app.decisionSessionHtml({ current_price: 250, day_change_pct: -2, price_chart: {} }, { sym: '$' });
  // No OHLC and no band => nothing worth drawing, rather than a row of dashes.
  assert.equal(html, '');
});

test('Deep Dive session block is silent on a fully empty payload', () => {
  assert.equal(app.decisionSessionHtml({}, { sym: '$' }), '');
  assert.equal(app.decisionSessionHtml({ price_chart: null }, { sym: '$' }), '');
});

// ---------------------------------------------------------------------------
// Branding
// ---------------------------------------------------------------------------
test('The maker is credited once, in the footer colophon', () => {
  const footerSig = document.querySelector('.app-footer .signature');
  assert.ok(footerSig, 'footer colophon present');
  assert.match(footerSig.textContent, /Atresh Somayajula/);
  assert.match(footerSig.textContent, /Designed & built by/); // sentence case since the styling pass

  // Exactly one attribution in the shell — the top bar carries controls only.
  assert.equal(document.querySelectorAll('.brand-signature').length, 0, 'no topbar byline');
  const mentions = Array.from(document.querySelectorAll('body *'))
    .filter((n) => n.children.length === 0 && /Atresh Somayajula/.test(n.textContent));
  assert.equal(mentions.length, 1, `attribution appears once, found ${mentions.length}`);

  // It is a mark, not a heading: it must not take a heading slot or a tab stop.
  assert.equal(footerSig.querySelector('h1,h2,h3,h4'), null);
  assert.equal(footerSig.getAttribute('tabindex'), null);
  // The disclaimer keeps its own node, below the colophon, and carries the build.
  const disc = document.querySelector('.footer-disclaimer-text');
  assert.match(disc.textContent, /Not investment advice/);
  assert.match(disc.textContent, /\{\{ app_build \}\}/, 'build label filled by the server (no version typed in the page)');
  assert.ok(footerSig.compareDocumentPosition(disc) & 4, 'disclaimer closes the page');
});

test('Top Gainers & Losers renders the session board; rows open Research, the chart is a secondary link (D4)', () => {
  app.renderTopMovers(intelFixture);
  const body = document.querySelector('#top-movers-body');
  const cols = Array.from(body.querySelectorAll('.q-board-col')).length;
  assert.equal(cols, 2, 'gainers and losers boards');
  const head = Array.from(body.querySelectorAll('.q-board-col:first-child .q-board-table thead th'))
    .map((n) => n.textContent.replace(/[⇅▲▼]/g, '').trim());
  assert.deepEqual(head, ['Symbol', 'Open', 'High', 'Low', 'Prev. Close', 'Last / Change']);
  assert.match(body.textContent, /NVDA/);
  // Session OHLC is shown as reported, uncompacted.
  assert.match(body.textContent, /\$141\.90/, 'session high');
  assert.match(body.textContent, /\$134\.55/, 'previous close');
  // The row's own control opens the in-app Research tear sheet.
  const row = body.querySelector('.q-board-row');
  const open = row.querySelector('button.q-board-open[data-ticker-open]');
  assert.ok(open, 'a native button opens Research');
  assert.equal(open.dataset.tickerOpen, row.dataset.boardTicker);
  assert.equal(row.getAttribute('role'), null, 'the row keeps its table semantics');
  assert.equal(row.querySelector('[data-tv]'), null);
  // TradingView is a secondary link, exchange-qualified when the feed says where.
  const chart = row.querySelector('a.q-board-chart');
  assert.equal(chart.textContent, 'Open chart');
  assert.equal(chart.getAttribute('target'), '_blank');
  assert.match(chart.getAttribute('rel'), /noopener/);
  assert.match(chart.href, /^https:\/\/www\.tradingview\.com\/chart\/\?symbol=/);
  assert.ok(!/\/chart\/[A-Za-z0-9]+\//.test(chart.href),
    'no private saved-layout id in the chart URL (those 403 for every other visitor)');
  assert.match(body.textContent, /Select a row to open its Research tear sheet/);
  assert.ok(!/undefined|NaN/.test(body.textContent), 'no leaked bad values');
});

test('TradingView symbols are exchange-qualified only when the venue is known', () => {
  assert.match(app.tradingViewUrl('AAPL', 'NMS'), /symbol=NASDAQ%3AAAPL$/);
  assert.match(app.tradingViewUrl('GE', 'NYQ'), /symbol=NYSE%3AGE$/);
  // Indian listings map from the Yahoo suffix, not from a guess.
  assert.match(app.tradingViewUrl('RELIANCE.NS'), /symbol=NSE%3ARELIANCE$/);
  // Unknown venue falls back to the bare ticker rather than inventing one.
  assert.match(app.tradingViewUrl('XYZ', 'WEIRD'), /symbol=XYZ$/);
  assert.equal(app.tradingViewUrl(''), null);
  // Every URL uses the public chart endpoint, never an account-private layout.
  for (const u of [app.tradingViewUrl('AAPL', 'NMS'), app.tradingViewUrl('RELIANCE.NS')]) {
    assert.ok(u.startsWith('https://www.tradingview.com/chart/?symbol='), u);
  }
});

test('TradingView symbols translate index, crypto and share-class tickers', () => {
  // Yahoo carets do not exist on TradingView; the mapped ones resolve…
  assert.match(app.tradingViewUrl('^NSEI'), /symbol=NSE%3ANIFTY$/);
  assert.match(app.tradingViewUrl('^BSESN'), /symbol=BSE%3ASENSEX$/);
  assert.match(app.tradingViewUrl('^VIX'), /symbol=TVC%3AVIX$/);
  // …and an unmapped one links nowhere rather than to a dead chart.
  assert.equal(app.tradingViewUrl('^MADEUP'), null);
  // Share classes: Yahoo BRK-B is TradingView BRK.B.
  assert.match(app.tradingViewUrl('BRK-B', 'NYQ'), /symbol=NYSE%3ABRK\.B$/);
  assert.match(app.tradingViewUrl('BTC-USD'), /symbol=CRYPTO%3ABTCUSD$/);
});

test('Top Gainers & Losers states an empty feed instead of an empty board', () => {
  app.renderTopMovers({ loading: false, movers_up: [], movers_down: [] });
  const body = document.querySelector('#top-movers-body');
  assert.match(body.textContent, /No ranked moves/i);
  assert.equal(body.querySelectorAll('table').length, 0, 'no empty table shell');
});

test('Source ledger renders feed health from the news payload', () => {
  app.renderSourceLedger(newsFixture);
  const body = document.querySelector('#source-ledger-body');
  assert.ok(body.textContent.trim().length > 0, 'source ledger has content');
  assert.ok(!/undefined/.test(body.textContent));
});

test('Market ribbon names the ETF behind an index label; real indices carry no tag', () => {
  app.renderMarketPulse({ quotes: [
    { symbol: 'SPY', label: 'S&P 500', price: 767.18, change_pct: -0.08 },
    { symbol: '^VIX', label: 'VIX', price: 15.7, change_pct: 3.2 },
    { symbol: 'BTC-USD', label: 'Bitcoin', price: 84133, change_pct: -0.3 },
  ] });
  const tags = Array.from(document.querySelectorAll('#market-pulse .q-rb-proxy')).map((n) => n.textContent);
  assert.deepEqual(tags, ['SPY'], 'only the ETF proxy is tagged');
});

test('Consensus candidates label the Street rating as the Street\'s, not QUANTA\'s', () => {
  app.renderTopOpportunities({ universe_size: 100, analyst_picks: [{ symbol: 'NVDA', name: 'NVIDIA', price: 140, sector: 'Technology', recommendation: 'buy', target_mean: 170, analyst_count: 42, upside_pct: 21 }] });
  const cols = Array.from(document.querySelectorAll('#portfolio-scan-body thead th')).map((n) => n.textContent.trim());
  assert.ok(cols.includes('Street rating'));
  assert.ok(!cols.includes('Rating'));
});

test('Track Record renders evidence per engine and never hides a thin sample', () => {
  app.renderTrackRecord({
    benchmark: 'SPY', min_sample: 30, method: 'Entry at the next open.',
    totals: { recorded: 3, resolved: 1, pending: 2, legacy_excluded: 0 },
    engines: [{ engine: 'elite', label: 'Deep analysis', recorded: 3,
      evidence: { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false, summary: 'No OOS run.' } }],
    groups: [{ key: { engine: 'elite', horizon_days: 63, stance: 'BULLISH' }, n_resolved: 1, n_pending: 2,
      hit_rate: 1, mean_excess_pct: 2.5, ci95_excess_pct: null, sufficient: false, verdict: 'Insufficient sample (1 resolved calls over 1 dates; need 30+)' }],
    recent: [{ as_of_date: '2026-09-20', ticker: 'AAPL', engine: 'elite', label: 'ACCUMULATE', horizon_days: 63, return_pct: 3.1, excess_return_pct: 2.5, outcome: 'WIN' }],
  });
  const body = document.querySelector('#track-body');
  const text = body.textContent;
  assert.match(text, /Never validated/);
  assert.match(text, /1 resolved calls so far; statistics below 30/);
  assert.match(text, /Insufficient sample/);
  assert.ok(body.querySelector('tr.is-thin'), 'thin groups are visually de-emphasised');
  assert.match(text, /ACCUMULATE/);
  assert.ok(!/undefined|NaN/.test(text));
  assert.match(document.querySelector('#track-meta').textContent, /3 recorded · 1 resolved/);

  app.renderTrackRecord({ error: 'Track record is unavailable' });
  assert.match(document.querySelector('#track-body').textContent, /Track record unavailable/);
});

test('News ledger lists the feeds actually polled, with the backend state and age', () => {
  app.renderSourceLedger({
    fetched_count: 42,
    // Display links are ignored: joining them to feeds by name hid live feeds.
    sources: [{ name: 'MarketWatch', url: 'https://www.marketwatch.com/' }],
    source_status: [
      { name: 'MarketWatch Top Stories', state: 'live', ok: true, items: 10, age_hours: 0.8 },
      { name: 'Yahoo Finance', state: 'live', ok: true, items: 10, age_hours: 55.5 },
      { name: 'Old Wire', state: 'stale', ok: false, items: 10, age_hours: 9600 },
      { name: 'Gone Wire', state: 'error', ok: false, items: 0, error: 'HTTP 404' },
    ],
  });
  const body = document.querySelector('#source-ledger-body');
  const text = body.textContent;
  assert.match(text, /News feeds 50%/);
  assert.match(text, /2\/4 live · 42 recent articles/);
  assert.match(text, /MarketWatch Top Stories/);
  assert.match(text, /Live · 48m ago/);
  assert.match(text, /Stale · 400d ago/);
  assert.match(text, /Unavailable · HTTP 404/);
  assert.match(text, /Prices are unaffected/);
  assert.equal(document.querySelector('#data-health-value').textContent, '50%');
  assert.ok(!/undefined|NaN/.test(text));
});
