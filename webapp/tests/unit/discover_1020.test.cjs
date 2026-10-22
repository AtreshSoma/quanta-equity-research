/**
 * Discover rework (2026-10-20): the Street ideas read their odds, revisions and
 * lagging targets as cards (or a compact list) and open a side panel; the tape
 * names each move's cause and how its session traded, gainers and losers in
 * their own rows; a lede under the title says what the ideas show; the header's
 * freshness follows the open view; a loss is never flagged "Extended".
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/discover_1020.test.cjs
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
let store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');
const $ = (s) => document.querySelector(s);

const pick = (symbol, up, extra = {}) => ({
  symbol, name: `${symbol} Inc.`, price: 100, sector: 'Technology', recommendation: 'buy', target_mean: 100 + up,
  target_low: 90, target_high: 160, analyst_count: 20, upside_pct: up, week52_low: 95, week52_high: 180, range_pos: 0.06, ...extra,
});
const read = (p, raises, cuts, extra = {}) => ({
  odds: { p, sigma: 0.4, source: 'options', expiry: '2027-09-17', rate: 0.041, rate_source: '3-month T-bill', horizon_days: 365 },
  revisions: { raises, cuts, upgrades: 0, downgrades: 0, firms: raises + cuts, days: 90 }, eps_revision_pct: -0.5, vol_1y: 0.38, ...extra,
});
const mover = (symbol, change, extra = {}) => ({
  symbol, name: `${symbol} Corp`, price: 50, change_pct: change, change_abs: change / 2, open: 50, day_high: 52, day_low: 48, prev_close: 48,
  tape: { gap_pct: 4, held_share: 0.9, clv: 0.8, range_pct: 8, tag: 'gap_held', rvol: 3.1 }, ...extra,
});
const setEnrich = (data) => { app.discoverEnrichStore[app.state.market] = { pending: 0, ideas: {}, movers: {}, ...data }; };

test.beforeEach(() => { for (const k of Object.keys(app.discoverEnrichStore)) delete app.discoverEnrichStore[k]; app.state.book = null; });

test('a loss is never flagged Extended; a large gain still is', () => {
  const ext = app.setupQualityForMover({ change_pct: 22 });
  assert.notEqual(app.setupQualityForMover({ change_pct: -22 }), ext, 'a 22% drop is not a chase');
  assert.equal(app.setupQualityForMover({ change_pct: Number(app.thr('MARKET.EXTENDED_MOVE_PCT')) }), ext);
});

test('Street list: odds and revisions wait quietly, then read; the list ranks by odds once all are in', () => {
  app.setIdeasView('list');
  const intel = { analyst_picks: [pick('AAA', 40), pick('BBB', 30), pick('CCC', 20, { range_pos: 0.6 })], consensus_stats: { median_upside_pct: 15, n: 60, names: 70, sectors: { Technology: 21 } } };
  app.renderTopOpportunities(intel);
  const body = $('#portfolio-scan-body');
  const head = [...body.querySelectorAll('thead th')].map((n) => n.textContent.replace(/[⇅▲▼]/g, '').trim());
  assert.deepEqual(head.slice(1, 7), ['Ticker', 'Price', 'Upside', 'Target odds', 'Revisions', 'Street rating']);
  assert.ok($('#portfolio-scan-body tr.q-detail .q-rt[role="img"]'), 'the targets bar lives in the row detail');
  assert.ok(body.querySelector('.q-odds-cell .q-wait'), 'a placeholder, not N/A, while the read is pending');
  const syms = () => [...body.querySelectorAll('tr.q-tr')].map((r) => r.dataset.sym);
  assert.deepEqual(syms(), ['AAA', 'BBB', 'CCC'], 'upside order before the odds arrive');

  setEnrich({ ideas: { AAA: read(0.12, 1, 6), BBB: read(0.31, 5, 1), CCC: read(0.22, 2, 2) } });
  app.renderTopOpportunities(intel);
  assert.deepEqual(syms(), ['BBB', 'CCC', 'AAA'], 'likeliest target first');
  assert.equal($('#portfolio-scan-body th [data-opp-sort="odds"]').getAttribute('aria-pressed'), 'true');
  assert.equal($('#portfolio-scan-body th [data-opp-sort="odds"]').closest('th').getAttribute('aria-sort'), 'descending');
  const first = body.querySelector('tr.q-tr');
  assert.match(first.querySelector('.q-odds b').textContent, /^31%$/);
  assert.match(first.querySelector('.q-odds').getAttribute('aria-label'), /not a forecast/);
  assert.match(first.querySelector('.q-rev-n').textContent, /5 up\s*1 down/);
});

test('a target that may lag a falling price is flagged, in the row and in the read', () => {
  const lagged = pick('NFLX', 33);
  assert.equal(app.targetLagged(lagged, read(0.2, 3, 9)), true);
  assert.equal(app.targetLagged(lagged, read(0.2, 9, 3)), false, 'raises outrunning cuts is no lag');
  assert.equal(app.targetLagged({ ...lagged, range_pos: 0.7 }, read(0.2, 3, 9)), false, 'not near the low');
  setEnrich({ ideas: { NFLX: read(0.2, 3, 9), ANET: read(0.25, 12, 1) } });
  app.renderTopOpportunities({ analyst_picks: [lagged, pick('ANET', 30, { range_pos: 0.6 })] });
  const row = $('#portfolio-scan-body tr.q-tr[data-sym="NFLX"]');
  assert.match(row.querySelector('.q-flags').textContent, /Target may lag/);
  app.setIdeasView('cards');
  app.renderTopOpportunities({ analyst_picks: [lagged, pick('ANET', 30, { range_pos: 0.6 })] });
  assert.match($('#portfolio-scan-body .idea-card[data-sym="NFLX"] .ic-note').textContent, /Target may lag · near 52-week low, 9 cuts/);
  const readLine = $('#discover-pulse');
  assert.match(readLine.textContent, /Best target odds\s*ANET\s*25%\s*to \$\d+\.\d\d, from option prices/);
  assert.match(readLine.textContent, /Target may lag\s*NFLX\s*near 52-week low, more cuts than raises/);
  assert.ok(readLine.querySelector('button.dsc-tk[data-pulse-jump="NFLX"]'), 'tickers in the brief find their row');
});

test('movers board: each move shows its cause, how the session traded and its volume', () => {
  const intel = {
    movers_up: [mover('BRZE', 51.4, { reason: { key: 'deal', label: 'Likely deal', detail: 'Pinned.' } }), mover('KVYO', 13)],
    movers_down: [mover('TSEM', -9.7, { tape: { gap_pct: -3.8, held_share: 0.3, clv: 0.1, tag: 'gap_faded', rvol: 1.2 } })],
    extreme_hidden: 2, extreme_move_pct: 15, screener_used: true,
  };
  app.renderTopMovers(intel);
  const body = $('#top-movers-body');
  const card = (s) => body.querySelector(`.mv-card[data-board-ticker="${s}"]`);
  assert.deepEqual([...body.querySelectorAll('.mv-label')].map((n) => n.textContent.replace(/\s+/g, ' ').trim()), ['Gainers 2', 'Losers 1']);
  assert.match(card('BRZE').querySelector('.mv-why').textContent, /Likely deal/);
  assert.equal(card('BRZE').querySelector('.mv-why').getAttribute('title'), 'Pinned.');
  assert.match(card('BRZE').querySelector('.mv-ses').textContent, /Gap held · 3\.1x vol/);
  assert.ok(card('BRZE').querySelector('.mv-heavy'), 'volume over the heavy line is inked');
  assert.match(card('TSEM').querySelector('.mv-ses .warn').textContent, /Gap faded/);
  assert.equal(card('TSEM').querySelector('.mv-heavy'), null);
  assert.match(card('KVYO').querySelector('.mv-why.is-wait').textContent, /Checking cause/, 'a pending cause says so, never "none"');
  assert.ok(card('BRZE').querySelector('a.mv-tk[href*="tradingview"]'), 'the ticker opens its chart');
  assert.equal(card('BRZE').querySelector('[data-ticker-open]').dataset.tickerOpen, 'BRZE');
  assert.match(body.textContent, /2 more moves of 15% or more ranked past these lists/);
});

test('the cause legend filters the board, and the read names deals and fades', () => {
  setEnrich({ movers: {
    KVYO: { reason: { key: 'none', label: 'No clear cause' } },
    TSEM: { reason: { key: 'analyst', label: 'Target cut', detail: 'X cut it.' } },
  } });
  const intel = {
    movers_up: [mover('BRZE', 51.4, { reason: { key: 'deal', label: 'Likely deal' } }), mover('KVYO', 13)],
    movers_down: [mover('TSEM', -9.7, { tape: { gap_pct: -3.8, held_share: 0.2, clv: 0.1, tag: 'gap_faded', rvol: 1.2 } })],
  };
  app.renderTopMovers(intel);
  assert.match($('#tape-sub').textContent, /BRZE looks like a deal; TSEM is giving back its opening gap/);
  assert.ok($('#tape-sub button.dsc-tk[data-pulse-board="BRZE"]'), 'tickers in the tape read find their card');
  const segs = [...$('#top-movers-body').querySelectorAll('[data-fk^="mv:"]')].map((b) => b.textContent.replace(/\d+$/, ''));
  assert.deepEqual(segs, ['All', 'Likely deal', 'Analyst action', 'No clear cause']);
  $('#top-movers-body [data-move-filter="analyst"]').click();
  const cards = () => [...$('#top-movers-body').querySelectorAll('.mv-card')].map((r) => r.dataset.boardTicker);
  assert.deepEqual(cards(), ['TSEM']);
  assert.equal($('#top-movers-body [data-fk="mv:analyst"]').getAttribute('aria-pressed'), 'true');
  assert.match($('#top-movers-body .q-boardbar-note').textContent, /Showing 1 analyst action move/);
  $('#top-movers-body [data-move-filter=""]').click();
  assert.equal(cards().length, 3);
});

test('Extended tab lists gains from the feed with what the feed measured, never [object Object]', () => {
  app.renderSecurityQueues({ movers_up: [mover('BRZE', 51.4, { reason: { key: 'deal', label: 'Likely deal', detail: 'Pinned.' } })], movers_down: [mover('DROP', -25)] });
  const body = $('#monitor-extended-body');
  assert.match($('#tab-monitor-extended').textContent, /Extended\s*1$/, 'the 25% loser is not on it');
  const head = [...body.querySelectorAll('thead th')].map((n) => n.textContent.trim());
  assert.deepEqual(head, ['Ticker / Company', 'Price', 'Change', 'Session', 'Rel. volume', 'Why it moved']);
  assert.doesNotMatch(body.textContent, /object Object|undefined|NaN/);
  assert.match(body.textContent, /Likely deal/);
});

test('your portfolio: holdings are marked and the detail reads the sector weight', () => {
  app.state.book = { s: 'ok', pf: { overview: { total_value: 10000 }, positions: [
    { symbol: 'AAA', broker: { quantity: 10, weight: 0.3, sector: 'Technology', market_value: 3000 } },
    { symbol: 'XOM', broker: { quantity: 5, weight: 0.1, sector: 'Energy', market_value: 1000 } },
  ] } };
  const bi = app.bookIndex();
  assert.equal(bi.sectors.Technology, 0.3);
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 30), pick('BBB', 20)] });
  assert.match($('#portfolio-scan-body .idea-card[data-sym="AAA"] .q-own').textContent, /You own/, 'the card marks a holding');
  assert.equal($('#portfolio-scan-body .idea-card[data-sym="BBB"] .q-own'), null);
  app.setIdeasView('list');
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 30), pick('BBB', 20)] });
  assert.match($('#portfolio-scan-body tr.q-tr[data-sym="AAA"] .q-own').textContent, /You own/);
  assert.equal($('#portfolio-scan-body tr.q-tr[data-sym="BBB"] .q-own'), null);
  assert.match($('#q-detail-cons-1').textContent, /Technology is already 30% of your book/);
  app.renderTopMovers({ movers_up: [mover('XOM', 4)], movers_down: [] });
  assert.match($('#top-movers-body .mv-card[data-board-ticker="XOM"] .q-own').getAttribute('title'), /\+\$10\.00 today on 5 shares/);
  app.state.book = null;
  app.setIdeasView('cards');
});

test('earnings: the chip names the timing, and days count on the exchange calendar', () => {
  const now = Date.parse('2026-10-20T15:00:00Z'); // 11:00 in New York
  assert.equal(app.marketTodayIso(now), '2026-10-20');
  assert.equal(app.exchangeDaysUntil('2026-10-21', now), 1);
  assert.match(app.earningsChipHtml('2026-10-20', now, 'amc'), /Earnings today · after the close/);
  const late = Date.parse('2026-10-21T02:00:00Z'); // 22:00 the evening before in New York
  assert.equal(app.marketTodayIso(late), '2026-10-20', 'still the 20th on the exchange');
});

test('status line: quote age and coverage, with a retry when the feed is delayed', () => {
  setEnrich({});
  app.renderDiscoverStatus({ as_of: new Date(Date.now() - 4 * 60000).toISOString(), coverage: { tracked: 140, quoted: 120, carried: 12 } });
  const el = $('#discover-status');
  assert.equal(el.hidden, false);
  assert.match(el.textContent, /Quotes \d\d:\d\d ET · 4 min ago · 132 of 140 tracked names read/);
  assert.equal(el.querySelector('[data-retry]'), null);
  app.renderDiscoverStatus({ as_of: new Date().toISOString(), stale: true, coverage: {} });
  assert.equal(el.dataset.staleness, 'stale');
  assert.ok(el.querySelector('[data-retry="market-intel"]'));
});

test('the header freshness chip reads Discover\'s own quotes while Discover is open', () => {
  app.state.lastUpdated = new Date().toISOString();
  app.state.dashboard = { market_intel: { as_of: '2026-10-20T13:37:00Z', stale: true } };
  $('#view-discover').classList.add('active');
  const src = app.freshnessSource();
  assert.equal(src.iso, '2026-10-20T13:37:00Z');
  assert.equal(src.stale, true);
  assert.equal(src.staleText, 'quotes delayed');
  $('#view-discover').classList.remove('active');
  assert.equal(app.freshnessSource().iso, app.state.lastUpdated);
  app.state.dashboard = null;
});

test('the lede says what the ideas show; the tape keeps its own read under its head', () => {
  setEnrich({ ideas: { AAA: read(0.3, 4, 1) }, movers: { BRZE: { reason: { key: 'deal', label: 'Likely deal' } } } });
  app.renderTopMovers({ analyst_picks: [pick('AAA', 30, { range_pos: 0.6 })], movers_up: [mover('BRZE', 51, { reason: { key: 'deal', label: 'Likely deal' } })], movers_down: [] });
  assert.equal($('#discover-pulse').hidden, false);
  assert.match($('#discover-pulse').textContent, /^Best target odds\s*AAA\s*30%/);
  assert.match($('#tape-sub').textContent, /^BRZE looks like a deal\./);
});

test('the lede places the listed median upside among every tracked name', () => {
  const upsides = Array.from({ length: 40 }, (_, i) => i - 5); // -5 .. 34
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 33), pick('BBB', 31)], consensus_stats: { median_upside_pct: 14.5, n: 40, names: 40, upsides, sectors: {} } });
  assert.match($('#discover-pulse').textContent, /Median upside\s*\+?32\.0%\s*higher than 93% of tracked names/);
});

test('the brief never credits options for odds priced from price history', () => {
  setEnrich({ ideas: { AAA: read(0.3, 4, 1, { odds: { p: 0.3, sigma: 0.5, source: 'history', rate: 0.041, horizon_days: 365 } }) } });
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 30, { range_pos: 0.6 })] });
  const brief = $('#discover-pulse').textContent;
  assert.match(brief, /Best target odds\s*AAA\s*30%\s*to .*from price history/);
  assert.doesNotMatch(brief, /option/i);
});

test('Opened at high/low is a tape view: its tab carries the count, the movers filter stays with the movers', () => {
  const big = 50e9; const small = 3e9;
  const ox = (s, cap) => ({ symbol: s, name: s, price: 10, open: 10, day_high: 11, day_low: 10, prev_close: 9.5, change_pct: 5, market_cap: cap });
  const earn = { reason: { key: 'earnings', label: 'Earnings' } };
  const intel = { movers_up: [mover('UPA', 8, { market_cap: big, ...earn })], movers_down: [mover('DNA', -6, { market_cap: small, ...earn })],
    open_at_low: [ox('LOWA', big), ox('LOWB', small)], open_at_high: [ox('HIGA', small)], open_extremes_scanned: 50 };
  app.renderTopMovers(intel); app.renderOpenExtremes(intel);
  assert.match($('#tab-open-extremes').textContent, /Opened at high\/low\s*3/, 'the tab counts every match');
  assert.equal($('#top-movers-body [data-fk="mv:extremes"]'), null, 'no band chip among the causes');
  $('#top-movers-body [data-fk="size:large"]').click();
  assert.equal($('#top-movers-body').querySelectorAll('.mv-card').length, 1, 'size narrows the movers');
  assert.match($('#top-movers-body [data-fk="mv:all"]').textContent, /All\s*1/, 'counts follow the size');
  assert.equal($('#open-extremes-body').querySelectorAll('.q-board-row').length, 3, 'the boards keep every match');
  $('#top-movers-body [data-fk="size:"]').click();
});

test('compare: tick up to three names to set them side by side; the stronger figure is bold', () => {
  setEnrich({ ideas: { AAA: read(0.12, 1, 6), BBB: read(0.31, 5, 1), CCC: read(0.22, 2, 2), DDD: read(0.2, 1, 1) } });
  const intel = { analyst_picks: [pick('AAA', 40), pick('BBB', 30), pick('CCC', 20), pick('DDD', 10)] };
  app.setIdeasView('list');
  app.renderTopOpportunities(intel);
  const tick = (s) => $(`#portfolio-scan-body input.q-cmp[data-compare="${s}"]`).click();
  tick('AAA'); tick('BBB');
  const tray = $('#portfolio-scan-body .q-compare');
  assert.ok(tray, 'the tray appears with the first tick');
  assert.deepEqual([...tray.querySelectorAll('thead th b')].map((n) => n.textContent), ['AAA', 'BBB'], 'columns follow the Street order');
  const oddsRow = [...tray.querySelectorAll('tbody tr')].find((tr) => /Target odds/.test(tr.textContent));
  assert.equal(oddsRow.querySelector('td.is-best').textContent, '31%');
  // The summary names what each one leads on, from the same bold marks.
  const leadBBB = [...tray.querySelectorAll('.q-compare-lead li')].find((li) => li.querySelector('b').textContent === 'BBB');
  assert.match(leadBBB.textContent, /target odds, target raises less cuts/);
  assert.equal($('#portfolio-scan-body tr.q-tr[data-sym="AAA"]').classList.contains('is-compared'), true);
  assert.equal($('#portfolio-scan-body tr.q-detail[data-detail]:not([hidden])'), null, 'ticking does not expand the row');
  tick('CCC');
  assert.equal($('#portfolio-scan-body input.q-cmp[data-compare="DDD"]').disabled, true, 'a fourth is refused');
  $('#portfolio-scan-body [data-compare-clear]').click();
  assert.equal($('#portfolio-scan-body .q-compare'), null);
  app.setIdeasView('cards');
});

test('movers board: size filter and the sector baseline under each change', () => {
  setEnrich({ movers: { BIG: { fund: 'XLK', sector: 'Technology', fund_change_pct: -0.3, excess_pct: -9.4, reason: { key: 'none', label: 'No clear cause' } } } });
  const intel = { movers_up: [mover('SMALL', 12, { market_cap: 3e9 })], movers_down: [mover('BIG', -9.7, { market_cap: 40e9 })] };
  app.renderTopMovers(intel);
  const body = $('#top-movers-body');
  // The sector baseline rides in the change's title, with the dollar move.
  assert.match(body.querySelector('.mv-card[data-board-ticker="BIG"] .mv-chg').getAttribute('title'), /-\$4\.85 today\. Sector fund XLK -0\.3% today, so -9\.4 points of this move are the stock's own/);
  body.querySelector('[data-move-size="large"]').click();
  assert.deepEqual([...$('#top-movers-body').querySelectorAll('.mv-card')].map((r) => r.dataset.boardTicker), ['BIG']);
  assert.match($('#top-movers-body .mv-label.bull').nextElementSibling.textContent, /No matching moves/);
  $('#top-movers-body [data-move-size=""]').click();
  assert.equal($('#top-movers-body').querySelectorAll('.mv-card').length, 2);
});

test('monitor tabs say what an empty scan list needs instead of a bare zero', () => {
  app.renderSecurityQueues({ movers_up: [], movers_down: [] });
  const tab = $('#tab-monitor-watchlist');
  assert.ok(tab.classList.contains('is-idle'));
  assert.match(tab.textContent, /Watchlist\s*needs a scan/);
  app.renderOpenExtremes({ open_at_low: [mover('LOW', 2)], open_at_high: [], open_extremes_scanned: 40 });
  assert.match($('#open-extremes-meta').textContent, /^1 at low · 0 at high/);
});

test('when the odds-and-causes read does not load, nothing missing is shown as a finding', () => {
  app.discoverEnrichStore[app.state.market] = { ideas: {}, movers: {}, pending: 0, failed: true };
  const intel = { analyst_picks: [pick('AAA', 30), pick('BBB', 20)], movers_up: [mover('UP1', 12, { tape: null }), mover('UP2', 9, { tape: null })], movers_down: [], as_of: new Date().toISOString() };
  app.renderTopOpportunities(intel);
  app.renderTopMovers(intel);
  assert.ok($('#portfolio-scan-body .ic-stats.is-solo'), 'the card keeps upside alone');
  assert.equal($('#portfolio-scan-body .ic-ring'), null, 'no empty odds ring');
  assert.match($('#portfolio-scan-body .ic-note').textContent, /odds not loaded/);
  app.setIdeasView('list');
  app.renderTopOpportunities(intel);
  assert.ok($('#portfolio-scan-body table.q-table--noreads'), 'odds and revisions columns step out');
  app.setIdeasView('cards');
  assert.equal($('#opps-filters [data-opp-sort="odds"]'), null, 'no sort on a figure that is not there');
  assert.equal($('#opps-filters [data-opp-sort="revisions"]'), null);
  const tape = $('#top-movers-body');
  assert.doesNotMatch(`${tape.textContent} ${$('#tape-sub').textContent}`, /no clear cause/i, 'an unread cause is never reported as none');
  assert.deepEqual([...tape.querySelectorAll('.mv-why')].map((n) => n.textContent), ['Cause not loaded', 'Cause not loaded']);
  assert.ok([...tape.querySelectorAll('.mv-ses')].every((n) => !n.textContent.trim()), 'no session line without session reads');
  app.renderDiscoverStatus(intel);
  assert.match($('#discover-status').textContent, /odds and causes not loaded/);
});

test('the tape shows every move, gainers and losers each in their own row', () => {
  const ups = Array.from({ length: 7 }, (_, i) => mover(`U${i}`, 20 - i));
  const dns = Array.from({ length: 6 }, (_, i) => mover(`D${i}`, -(10 - i)));
  app.renderTopMovers({ movers_up: ups, movers_down: dns });
  const groups = [...$('#top-movers-body').querySelectorAll('.mv-group')];
  assert.equal(groups.length, 2);
  assert.deepEqual([...groups[0].querySelectorAll('.mv-card')].map((r) => r.dataset.boardTicker), ups.map((m) => m.symbol), 'biggest gain first');
  assert.deepEqual([...groups[1].querySelectorAll('.mv-card')].map((r) => r.dataset.boardTicker), dns.map((m) => m.symbol), 'biggest loss first');
  assert.equal($('#top-movers-body [data-tape-all]'), null, 'nothing hides behind a show-all');
});

test('cards: the note leads with a report due soon, then a lagging target', () => {
  const today = app.marketTodayIso();
  const plus = (d) => new Date(Date.parse(`${today}T12:00:00Z`) + d * 864e5).toISOString().slice(0, 10);
  setEnrich({ ideas: { AAA: read(0.3, 4, 1, { earnings: { implied_move_pct: 9.3 } }) } });
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 40, { earnings_date: plus(0), earnings_timing: 'amc', range_pos: 0.6 }), pick('BBB', 30, { range_pos: 0.6 }), pick('CCC', 20, { earnings_date: plus(30), range_pos: 0.6 })] });
  const note = (s) => $(`#portfolio-scan-body .idea-card[data-sym="${s}"] .ic-note`);
  assert.equal(note('AAA').textContent, 'Reports today after close · ±9.3% priced');
  assert.ok(note('AAA').classList.contains('warn'));
  assert.match(note('BBB').textContent, /No report due, no target flag/);
  assert.ok(note('BBB').classList.contains('calm'));
});

test('cards: one per idea, with its chart, odds ring and revisions; the card opens the side panel', () => {
  const spark = Array.from({ length: 64 }, (_, i) => 100 + Math.sin(i / 5) * 4 + i / 8);
  setEnrich({ ideas: { AAA: read(0.12, 1, 6, { spark, return_3m: 8.2 }), BBB: read(0.31, 5, 1, { spark, return_3m: 8.2 }) } });
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 40, { range_pos: 0.6 }), pick('BBB', 30, { range_pos: 0.6 })] });
  const body = $('#portfolio-scan-body');
  const cards = [...body.querySelectorAll('.idea-grid[role="list"] > [role="listitem"] > .idea-card')];
  assert.deepEqual(cards.map((c) => c.dataset.sym), ['BBB', 'AAA'], 'likeliest first once every odds is in');
  const bbb = cards[0];
  assert.ok(bbb.querySelector('svg.ic-area.up polyline'), 'a rising price draws in the gain colour');
  assert.equal(bbb.querySelector('.ic-ring b').textContent, '31%');
  assert.match(bbb.querySelector('.ic-stats').textContent, /5\s*up\s*1\s*down/);
  assert.match(bbb.querySelector('.ic-px-c').textContent, /\+8\.2%3M/);
  const open = bbb.querySelector('button.ic-open');
  assert.equal(open.getAttribute('aria-haspopup'), 'dialog');
  assert.match(open.getAttribute('aria-label'), /BBB, .*upside \+?30\.0%, odds 31%\. Open its details/);

  open.click();
  const drawer = $('#dsc-drawer');
  assert.equal(drawer.hidden, false);
  assert.equal(drawer.getAttribute('role'), 'dialog');
  assert.equal($('#dsc-drawer-title').textContent, 'BBB');
  assert.ok(drawer.querySelector('.dd-chart svg.ic-area'));
  assert.match(drawer.textContent, /A 31% chance BBB trades at or above/);
  // Compare from the panel marks the card and keeps the panel open on the same name.
  drawer.querySelector('[data-compare="BBB"]').click();
  assert.ok($('#portfolio-scan-body .idea-card[data-sym="BBB"]').classList.contains('is-compared'));
  assert.equal($('#dsc-drawer-title').textContent, 'BBB');
  assert.equal($('#dsc-drawer [data-compare="BBB"]').getAttribute('aria-pressed'), 'true');
  $('#portfolio-scan-body [data-compare-clear]')?.click();
  drawer.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(drawer.classList.contains('is-open'), false, 'Escape closes the panel');
});

test('a panel opened before the odds land fills in when they arrive', () => {
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 40, { range_pos: 0.6 })] });
  app.openIdeaDrawer('AAA');
  assert.match($('#dsc-drawer').textContent, /Reading options and price history/);
  setEnrich({ ideas: { AAA: read(0.27, 2, 1) } });
  app.refreshIdeaDrawer();
  assert.match($('#dsc-drawer').textContent, /A 27% chance AAA trades at or above/);
  app.closeIdeaDrawer();
  setEnrich({ ideas: { AAA: read(0.41, 2, 1) } });
  app.refreshIdeaDrawer();
  assert.match($('#dsc-drawer').textContent, /27% chance/, 'a closed panel is left alone');
});

test('the cards and list toggle is remembered', () => {
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 40), pick('BBB', 30)] });
  $('#opps-filters [data-ideas-view="list"]').click();
  assert.ok($('#portfolio-scan-body table.q-table--ideas'));
  assert.equal($('#portfolio-scan-body .idea-grid'), null);
  assert.equal(store['quanta.discover.view'], 'list');
  assert.equal($('#opps-filters [data-ideas-view="list"]').getAttribute('aria-pressed'), 'true');
  $('#opps-filters [data-ideas-view="cards"]').click();
  assert.ok($('#portfolio-scan-body .idea-grid'));
  assert.equal(store['quanta.discover.view'], 'cards');
});

test('while the side panel is open the rest of the page is inert, and released on close', () => {
  app.renderTopOpportunities({ analyst_picks: [pick('AAA', 40)] });
  app.openIdeaDrawer('AAA');
  const drawer = $('#dsc-drawer');
  assert.ok($('.topbar').hasAttribute('inert'), 'the top bar is out of reach');
  assert.ok($('#portfolio-scan-body').closest('[inert]'), 'the cards behind the panel are out of reach');
  assert.equal(drawer.closest('[inert]'), null, 'the panel itself is not');
  assert.equal($('#dsc-drawer-scrim').hasAttribute('inert'), false, 'the scrim still closes it');
  app.closeIdeaDrawer();
  assert.equal(document.querySelectorAll('[inert]').length, 0);
});
