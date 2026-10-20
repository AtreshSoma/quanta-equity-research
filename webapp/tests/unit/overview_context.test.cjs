/**
 * Overview context (October 2026): your book against the call, what changed,
 * what would change the call, sectors, key levels, cross-asset, the week and
 * futures. Pure helpers first, then each panel renders from its payload.
 *
 * Run: node --test tests/unit/overview_context.test.cjs
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

const pf = {
  overview: { total_value: 40000, invested_value: 37600, day_change: 120.5, day_change_pct: 0.3 },
  positions: [
    { symbol: 'MSFT', broker: { weight: 0.107, earnings_date: '2026-10-12', earnings_timing: 'AMC' } },
    { symbol: 'LRCX', broker: { weight: 0.032, earnings_date: '2026-10-21', earnings_timing: 'AMC' } },
    { symbol: 'NVDA', broker: { weight: 0.08 } },
  ],
};
const intel = { movers_up: [{ symbol: 'NVDA', change_pct: 4.2 }], movers_down: [], earnings_week: [{ symbol: 'JPM', earnings_date_pretty: '2026-10-13' }, { symbol: 'MSFT', earnings_date_pretty: '2026-10-12' }] };

test('the exposure band parses from the engine wording', () => {
  assert.deepEqual(app.parseBand('30-55% gross'), [30, 55]);
  assert.deepEqual(app.parseBand('50–70% gross'), [50, 70]);
  assert.equal(app.parseBand('No band'), null);
});

test('the book sits above, inside or below the band, with holdings that report or move', () => {
  const r = app.bookVsCall(pf, '30-55% gross', intel, '2026-10-09');
  assert.equal(Math.round(r.invested), 94);
  assert.equal(r.position, 'above');
  assert.equal(Math.round(r.gap), 39);
  assert.deepEqual(r.reporting.map((x) => [x.symbol, x.days, x.timing]), [['MSFT', 3, 'AMC']], 'LRCX is outside the week');
  assert.deepEqual(r.movers.map((m) => m.symbol), ['NVDA']);
  assert.equal(app.bookVsCall({ ...pf, overview: { ...pf.overview, invested_value: 16000 } }, '30-55% gross', intel, '2026-10-09').position, 'inside');
  assert.equal(app.bookVsCall({}, '30-55% gross'), null);
});

test('triggers compare live inputs with the model thresholds and skip what is not measured', () => {
  const model = { vix: 14.8, breadth: 50, confidenceScore: 18, net: 2, factors: Array.from({ length: 10 }, () => ({ score: 0 })) };
  const t = app.regimeTriggers(model, { last: 778.5, sma50: 765.5, sma200: 719.2 });
  const s = Object.fromEntries(t.strengthen.map((r) => [r.label, r.met]));
  assert.equal(s['VIX below 16'], true);
  assert.equal(s['Index participation 70% or more'], false);
  assert.equal(s['The benchmark back above its 50-day average (765.50)'], true);
  // Only market inputs: the model's own confidence and direction are outputs.
  assert.ok(t.strengthen.every((r) => !/Confidence|Direction score/.test(r.label)), 'no circular conditions');
  // With the fund count and the index name, the conditions read as counts and names.
  const named = app.regimeTriggers(model, { last: 778.5, sma50: 765.5, sma200: 719.2 }, { above: 2, measured: 4 }, 'S&P 500');
  const labels = named.strengthen.map((r) => r.label).concat(named.weaken.map((r) => r.label));
  assert.ok(labels.includes('3 of 4 index funds in uptrends or more'), labels.join(' | '));
  assert.ok(labels.includes('1 of 4 index funds in uptrends or fewer'));
  assert.ok(labels.includes('S&P 500 back above its 50-day average (765.50)'), labels.join(' | '));
  assert.ok(labels.includes('S&P 500 below its 200-day average (719.20)'));
  assert.equal(named.strengthen.find((r) => /index funds/.test(r.label)).now, '2 of 4 now');
  assert.ok(t.weaken.every((r) => r.met === false), 'nothing on the weaker side is true');
  const bare = app.regimeTriggers({ factors: [] }, {});
  assert.ok(bare.strengthen.every((r) => !/VIX|50-day|participation/i.test(r.label)), 'unmeasured inputs are left out');
});

test('the week places macro times, tracked earnings and the holdings own reports on their days', () => {
  const held = app.bookVsCall(pf, null, intel, '2026-10-09').reporting;
  const days = app.weekEvents('2026-10-09', [{ title: 'Consumer Price Index', date: '2026-10-14', time: '08:30' }], intel.earnings_week, held);
  assert.equal(days.length, 7);
  assert.equal(days[0].date, '2026-10-09');
  const mon = days.find((d) => d.date === '2026-10-12');
  assert.deepEqual(mon.items.map((i) => [i.symbol, i.held, i.timing]), [['MSFT', true, 'AMC']], 'a holding is listed once, with its timing');
  assert.equal(days.find((d) => d.date === '2026-10-13').items[0].symbol, 'JPM');
  assert.equal(days.find((d) => d.date === '2026-10-14').items[0].time, '08:30');
});

// ---- renders -------------------------------------------------------------
const market = {
  vix_level: 14.84, participation_pct: 50, participation: { above: 2, measured: 4 },
  quotes: [{ symbol: 'SPY', change_pct: 0.6 }, { symbol: 'QQQ', change_pct: 0.5 }, { symbol: 'IWM', change_pct: 0.5 }],
  regime_baseline: false,
  regime_changes: [{ text: 'Direction 52 → 48 of 100', tone: 'bear', kind: 'direction', label: 'Direction score', from: 52, to: 48 },
    { text: 'Rates turned neutral', tone: 'bear', kind: 'factor', label: 'Rates', from: 'supportive', to: 'neutral' },
    { text: 'News Impact 0 → -2', tone: 'bear' }],
  regime_history: [{ date: '2026-10-08', tone: 'Neutral' }, { date: '2026-10-09', posture: 'Neutral', direction: 48, confidence: 18 }],
  market_regime_engine: { posture: 'Neutral', marketRegimeBase: 'Mixed Market', colorTheme: { toneClass: 'neutral' }, confidenceScore: 18, lowConfidence: true,
    factors: [{ key: 'trend', label: 'Trend', score: 1 }, { key: 'news', label: 'News Impact', score: -2 }],
    portfolioGuidance: { band: '30-55% gross', headline: 'x', headlineTone: 'warn', actions: [] } },
};

test('the mood card says, for pros, how long its posture has held and what moved since the prior session', () => {
  app.renderMarketRegime(market, {}, {});
  const line = document.querySelector('#decision-brief .ovx-history');
  assert.ok(line.classList.contains('pro-only'), 'the posture name is pro detail');
  // Sessions are named by their dates, never "since records began".
  assert.match(line.textContent, /Neutral in the first recorded session, Fri Oct 9/);
  assert.match(line.textContent, /Since Thu Oct 8: Direction 52 → 48 of 100; Rates turned neutral\./);
  assert.doesNotMatch(line.textContent, /records began|since today/);
});

test('the impact chain is no longer repeated in Market intelligence', () => {
  assert.equal(document.querySelectorAll('#news-body .q-chain').length, 0);
});

test('the book panel is retired from the Overview; its data only marks holdings on the calendar', () => {
  assert.equal(document.querySelector('#book-vs-call'), null);
  assert.doesNotThrow(() => app.renderBookPanel());
});

const ctx = {
  available: true, market: 'US',
  sectors: [{ symbol: 'XLK', label: 'Technology', change_pct: 0.5, above_sma50: true }, { symbol: 'XLE', label: 'Energy', change_pct: -1.2, above_sma50: false }],
  sector_participation: { above: 1, measured: 2, requested: 2 },
  levels: { symbol: 'SPY', last: 778.53, sma50: 765.51, sma200: 719.16, above_sma50: true, above_sma200: true, atr: 6.81, atr_sessions: 14,
    session: { low: 775.14, high: 779.42 }, prior: { low: 770.44, high: 777.09, close: 773.93 },
    expected_move: { vol_symbol: '^VIX', vol_level: 14.84, points: 7.28, pct: 0.93, low: 771.25, high: 785.81 } },
  cross_asset: { quotes: [{ symbol: 'DX-Y.NYB', label: 'Dollar index', price: 101.2, change_pct: -0.2 }],
    reads: [{ key: 'curve', label: '10Y less 3-month', value: 118, unit: 'bp', change: -0.1, change_unit: 'bp', read: 'not inverted' }] },
  futures: [{ symbol: 'ES=F', label: 'S&P 500', price: 7866, change_pct: 0.64 }],
  calendar: [{ title: 'Consumer Price Index', date: '2026-10-14', time: '08:30' }],
};

test('the sector heatmap, levels, cross-asset and triggers render from the market context', () => {
  app.state.market = 'US';
  app.state.marketContext = { US: { ...ctx, sectors: ctx.sectors.map((s, i) => ({ ...s, weight: [33, 3][i] })) } };
  app.renderMarketContext();
  const tiles = [...document.querySelectorAll('#sector-body .ovx-tile')];
  assert.deepEqual(tiles.map((t) => t.querySelector('.ovx-tile-n').textContent), ['Technology', 'Energy'], 'largest sector first');
  assert.match(tiles[0].getAttribute('aria-label'), /Technology: \+0\.50% .*, in an uptrend/);
  assert.ok(tiles[0].classList.contains('up') && tiles[1].classList.contains('down'), 'colour is the move');
  const area = (t) => parseFloat(t.style.width) * parseFloat(t.style.height);
  assert.ok(area(tiles[0]) > 5 * area(tiles[1]), 'tile size follows the sector weight');
  const total = tiles.reduce((s, t) => s + area(t), 0);
  assert.ok(Math.abs(total - 10000) < 1, 'the tiles fill the map with no gap');
  assert.equal(tiles[0].dataset.chartSymbol, 'XLK', 'a tile charts its sector fund');
  // Trend breadth: each count with its own denominator, never summed into one share.
  assert.equal(document.querySelector('#sector-body .ovx-ring').getAttribute('aria-label'), '1 of 2 sectors in an uptrend');
  const lv = document.querySelector('#levels-body');
  assert.equal(lv.querySelectorAll('.q-ruler-row').length, 3);
  assert.match(lv.textContent, /±7\.28 \(±0\.93%\)/);
  assert.match(lv.textContent, /\+1\.7%/);
  assert.match(document.querySelector('#cross-body').textContent, /\+118 bp/);
  assert.match(document.querySelector('#cross-body').textContent, /Not inverted/);
  assert.match(document.querySelector('#triggers-body').textContent, /above its 50-day average/);
  assert.equal(document.querySelectorAll('#triggers-body .q-trig-side').length, 2, 'a stronger and a weaker side');
  assert.match(document.querySelector('#cross-meta').textContent, /change/i, 'cross-asset says its reads are one session');
  // The next session's likely range, from the same expected move.
  assert.match(document.querySelector('#ov-outlook .ovx-range').getAttribute('aria-label'), /range 771\.25 to 785\.81/);
});

test('earnings timing reads in words from either feed', () => {
  assert.equal(app.earningsTimingWords('am'), 'before the open');
  assert.equal(app.earningsTimingWords('AMC'), 'after the close');
  assert.equal(app.earningsTimingWords('unknown'), '');
});

test('the week shows the trading days; an empty weekend is not a column', () => {
  app.state.dashboard = { market: {}, news: {}, market_intel: { earnings_week: [] } };
  app.state.book = { s: 'off' };
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const days = [...document.querySelectorAll('#week-body .ovx-wkd')];
  assert.equal(days.length, 5, 'seven calendar days hold five trading days');
  const today = app.exchangeSession().today;
  const wd = new Date(`${today}T12:00:00Z`).getUTCDay();
  if (wd !== 0 && wd !== 6) assert.ok(days[0].classList.contains('is-today'));
  assert.match(document.querySelector('#week-body').textContent, /No Fed meeting or major jobs or inflation report found this week/);
});

test('while the earnings calendar is still loading the week says so, not "No dated events"', () => {
  app.state.dashboard = { market: {}, news: {}, market_intel: { loading: true } };
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const body = document.querySelector('#week-body').textContent;
  assert.match(body, /Loading this week/);
  assert.doesNotMatch(body, /No dated events/);
});

test('a context panel without a read says why: a restart, or a failed read', () => {
  assert.match(app.contextNotice({ available: false, reason: 'restart' }), /Restart QUANTA/);
  assert.match(app.contextNotice({ available: false, error: 'HTTP 502' }), /could not be read \(HTTP 502\)/);
  assert.equal(app.contextNotice({ available: true }), null);
  app.state.marketContext = { US: { available: false, reason: 'restart' } };
  app.renderMarketContext();
  for (const id of ['#sector-body', '#levels-body', '#cross-body']) assert.match(document.querySelector(id).textContent, /Restart QUANTA/, id);
});

test('each day shows the market value reporting before and after the bell, and its largest names', () => {
  const today = app.exchangeSession().today;
  const day2 = new Date(Date.parse(`${today}T12:00:00Z`) + 2 * 864e5).toISOString().slice(0, 10);
  const row = (symbol, cap, timing, eps) => ({ symbol, name: `${symbol} Inc.`, market_cap: cap, earnings_date: day2, earnings_timing: timing, eps_estimate: eps });
  const cal = [row('SMALL', 11e9, 'amc', 0.5), row('BIG', 900e9, 'bmo', 4.92), row('MID', 50e9, null, null), row('MID2', 40e9, 'amc', 1), row('TINY', 10e9, 'bmo', 0.1)];
  app.state.dashboard = { market: {}, news: {}, market_intel: { earnings_week: cal.slice(0, 1), earnings_calendar: cal, earnings_scope: 'US companies over $10B and the tracked universe' } };
  app.state.book = { s: 'off' };
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const col = [...document.querySelectorAll('#week-body .ovx-wkd')].find((d) => /5 reports/.test(d.textContent));
  assert.ok(col, 'the reporting day');
  const names = [...col.querySelectorAll('.ovx-wkn')].map((b) => b.dataset.ticker);
  assert.deepEqual(names, ['BIG', 'MID', 'MID2', 'SMALL'], 'largest first, top four');
  assert.match(col.querySelector('.ovx-wkmore').textContent, /\+1 more/);
  assert.match(col.querySelector('.ovx-wkn[data-ticker="BIG"]').textContent, /Before open/);
  assert.match(col.querySelector('.ovx-wkn[data-ticker="SMALL"]').textContent, /After close/);
  assert.match(col.querySelector('.ovx-wkbar').getAttribute('aria-label'), /\$1T of market value reports, \$910B before the open, \$51B after the close/);
  assert.ok(col.querySelector('.ovx-wkstack .am') && col.querySelector('.ovx-wkstack .pm'), 'both halves of the bar');
  assert.match(document.querySelector('#week-meta').textContent, /5 earnings reports/);
  assert.match(document.querySelector('#week-body > .ovx-fine').textContent, /US companies over \$10B/);
  assert.match(document.querySelector('#week-key').textContent, /Before the open[\s\S]*After the close/);
});

test('display symbols drop the exchange suffix', () => {
  assert.equal(app.shownSymbol('APOLLOHOSP.NS'), 'APOLLOHOSP');
  assert.equal(app.shownSymbol('RELIANCE.BO'), 'RELIANCE');
  assert.equal(app.shownSymbol('BRK-B'), 'BRK-B');
});

test('the week says why its earnings list is short', () => {
  assert.match(app.earningsCalendarNote({ earnings_week: [{ symbol: 'JPM' }] }, 'US'), /Restart QUANTA/, 'a server that predates the calendar');
  assert.match(app.earningsCalendarNote({ earnings_scope: 'the tracked universe', earnings_calendar_status: 'unavailable' }, 'US'), /did not answer/);
  assert.equal(app.earningsCalendarNote({ earnings_scope: 'x', earnings_calendar_status: 'ok' }, 'US'), null);
  assert.equal(app.earningsCalendarNote({ earnings_week: [] }, 'IN'), null, 'India has no Yahoo calendar to miss');
  assert.equal(app.earningsCalendarNote({}, 'US'), null, 'nothing loaded yet');
});

test('company names read in normal case; acronyms stay', () => {
  assert.equal(app.companyName('HCL TECHNOLOGIES LTD'), 'HCL Technologies Ltd');
  assert.equal(app.companyName('MAHINDRA & MAHINDRA LTD'), 'Mahindra & Mahindra Ltd');
  assert.equal(app.companyName('STATE BANK OF INDIA'), 'State Bank of India');
  assert.equal(app.companyName('JPMorgan Chase & Co.'), 'JPMorgan Chase & Co.', 'mixed case is left alone');
});

test('India triggers count indices, and a factor change that keeps its word shows the score', () => {
  const model = { vix: 14.4, breadth: 0, confidenceScore: 16, net: -1, factors: Array.from({ length: 10 }, () => ({ score: 0 })) };
  const t = app.regimeTriggers(model, {}, { above: 0, measured: 4, members: [{ symbol: '^NSEI' }, { symbol: '^BSESN' }, { symbol: '^NSEBANK' }, { symbol: '^CNXIT' }] }, 'Nifty 50');
  assert.ok(t.strengthen.some((r) => r.label === '3 of 4 indices in uptrends or more'));
  assert.equal(app.factorWord(-2), 'Headwind');
});

test('the regime says how long its posture has held', () => {
  const h = [{ date: '2026-10-06', posture: 'Lean Defensive' }, { date: '2026-10-07', posture: 'Neutral' }, { date: '2026-10-08', posture: 'Neutral' }, { date: '2026-10-09', posture: 'Neutral' }];
  assert.deepEqual(app.postureAge(h, 'Neutral'), { sessions: 3, since: '2026-10-07', prev: 'Lean Defensive', all: false });
  assert.equal(app.postureAgeText(app.postureAge(h, 'Neutral'), 'Neutral'), 'Neutral for 3 sessions, since Wed Oct 7');
  assert.equal(app.postureAgeText(app.postureAge(h.slice(0, 2), 'Neutral'), 'Neutral'), 'Neutral as of Wed Oct 7, from Lean Defensive');
  assert.equal(app.postureAgeText(app.postureAge(h.slice(1, 2), 'Neutral'), 'Neutral'), 'Neutral in the first recorded session, Wed Oct 7');
  assert.equal(app.postureAge(h, 'Offensive'), null, 'a posture not yet recorded has no age');
});


test('context refresh follows changed quote values even when source timestamps are unchanged', () => {
  app.state.market = 'US';
  app.state.marketContext = { US: ctx };
  app.state.ctxSig = null;
  app.state.dashboard = { market: { ...market, quotes: [{ symbol: 'SPY', price: 778.53, change_pct: 0.6 }] }, market_intel: {} };
  app.renderMarketContext();
  const before = document.querySelector('#levels-body').textContent;
  const sector = document.querySelector('#sector-body').firstElementChild;
  app.renderMarketContext();
  assert.equal(document.querySelector('#sector-body').firstElementChild, sector, 'identical inputs skip repaint');
  app.state.dashboard.market.quotes[0].price = 790.25;
  app.renderMarketContext();
  assert.notEqual(document.querySelector('#levels-body').textContent, before, 'new ribbon prices must update the levels ruler');
  assert.match(document.querySelector('#levels-body').textContent, /790\.25/);
});
