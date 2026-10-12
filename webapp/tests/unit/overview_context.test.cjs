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
  assert.equal(s['The benchmark above its 50-day average'], true);
  assert.equal(s['Confidence 50 or more'], false);
  // With the fund count and the index name, the conditions read as counts and names.
  const named = app.regimeTriggers(model, { last: 778.5, sma50: 765.5, sma200: 719.2 }, { above: 2, measured: 4 }, 'S&P 500');
  const labels = named.strengthen.map((r) => r.label).concat(named.weaken.map((r) => r.label));
  assert.ok(labels.includes('3 of 4 index funds in uptrends or more'), labels.join(' | '));
  assert.ok(labels.includes('1 of 4 index funds in uptrends or fewer'));
  assert.ok(labels.includes('S&P 500 above its 50-day average'));
  assert.equal(named.strengthen.find((r) => /index funds/.test(r.label)).now, '2 of 4 now');
  assert.ok(t.weaken.every((r) => r.met === false), 'nothing on the weaker side is true');
  const bare = app.regimeTriggers({ factors: [] }, {});
  assert.ok(bare.strengthen.every((r) => !/VIX|50-day|participation/i.test(r.label)), 'unmeasured inputs are left out');
});

test('history cells colour the engine posture, and older 4-factor rows are marked', () => {
  assert.deepEqual(app.historyCell({ date: '2026-10-09', posture: 'Lean Long', direction: 58 }).tone, 'bull-1');
  const legacy = app.historyCell({ date: '2026-09-28', tone: 'Neutral' });
  assert.equal(legacy.legacy, true);
  assert.match(legacy.label, /4-factor read/);
});

test('sector heat sorts strongest first and caps the intensity', () => {
  const h = app.sectorHeat([{ label: 'A', change_pct: -0.5 }, { label: 'B', change_pct: 3 }, { label: 'C', change_pct: null }]);
  assert.deepEqual(h.map((s) => s.label), ['B', 'A']);
  assert.equal(h[0].heat, 1);
  assert.ok(h[1].heat > 0 && h[1].heat < 1);
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

test('What changed leads with the story, lists the changes and draws the recorded sessions', () => {
  app.renderWhatChanged(market, {}, {});
  const body = document.querySelector('#changed-body');
  const secs = [...body.querySelectorAll('.q-wc-sec')];
  assert.equal(secs.length, 3, 'prices, model changes, the record');
  assert.match(secs[0].querySelector('.q-wc-tag').textContent, /Broad advance/);
  assert.equal(secs[0].querySelectorAll('.q-move-l').length, 0, 'the ribbon carries the index moves');
  assert.match(secs[0].textContent, /3 of 3 checks agree with prices/);
  const rows = [...secs[1].querySelectorAll('tbody tr')].map((r) => [...r.children].map((c) => c.textContent.replace(/\s+/g, ' ').trim()).join(' | '));
  assert.equal(rows.length, 3);
  assert.equal(rows[0], 'Direction score | 52 of 100 | →48 of 100 | ▼ Weaker');
  assert.equal(rows[1], 'Rates | Supportive | →Neutral | ▼ Weaker');
  assert.match(rows[2], /News Impact 0 → -2/, 'a row without its parts keeps its sentence');
  assert.equal(body.querySelectorAll('.q-hist-strip i').length, 2);
  assert.ok(body.querySelector('.q-hist-strip i.is-legacy'), 'the 4-factor row is marked');
  assert.ok(body.querySelector('.q-hist-strip i.is-last'), 'the latest session is outlined');
  assert.equal(body.querySelectorAll('.q-hist-axis span').length, 2, 'dated axis');
  assert.match(secs[2].textContent, /Latest: Neutral\./);
  assert.match(document.querySelector('#changed-meta').textContent, /3 model changes/);
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

test('sectors, levels, cross-asset and triggers render from the market context', () => {
  app.state.market = 'US';
  app.state.marketContext = { US: ctx };
  app.renderMarketContext();
  const rows = [...document.querySelectorAll('#sector-body .q-sbar-row')];
  assert.deepEqual(rows.map((t) => t.querySelector('.q-sbar-n').textContent), ['Technology', 'Energy'], 'ranked, strongest first');
  assert.match(rows[0].getAttribute('aria-label'), /above its 50-day average/);
  assert.ok(rows[0].classList.contains('up') && rows[1].classList.contains('dn'), 'each move drawn from one zero line');
  assert.equal(rows[1].querySelector('.q-sbar-t i').style.getPropertyValue('--w'), '1.000', 'the largest move fills its half');
  assert.match(document.querySelector('#sector-meta').textContent, /1 of 2 above 50-DMA/);
  const lv = document.querySelector('#levels-body');
  assert.equal(lv.querySelectorAll('.q-ruler-row').length, 3);
  assert.match(lv.textContent, /±7\.28 \(±0\.93%\)/);
  assert.match(lv.textContent, /\+1\.7%/);
  assert.match(document.querySelector('#cross-body').textContent, /\+118 bp/);
  assert.match(document.querySelector('#cross-body').textContent, /Not inverted/);
  assert.match(document.querySelector('#triggers-body').textContent, /above its 50-day average/);
  assert.equal(document.querySelectorAll('#triggers-body .q-trig-m').length, 2, 'a meter per side');
});

test('earnings timing reads in words from either feed', () => {
  assert.equal(app.earningsTimingWords('am'), 'before the open');
  assert.equal(app.earningsTimingWords('AMC'), 'after the close');
  assert.equal(app.earningsTimingWords('unknown'), '');
});

test('the week has seven columns and names a weekend day as closed', () => {
  app.state.dashboard = { market: {}, news: {}, market_intel: { earnings_week: [] } };
  app.state.book = { s: 'off' };
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const days = [...document.querySelectorAll('#week-body .q-wk > li')];
  assert.equal(days.length, 7);
  assert.ok(days.some((d) => d.classList.contains('is-weekend') && /Closed/.test(d.textContent)), 'seven consecutive days always include a weekend');
  assert.ok(days[0].classList.contains('is-today'));
  assert.match(days[0].getAttribute('aria-label'), /^Today, /);
});

test('the week names the next event, its time and how far away it is', () => {
  app.state.marketContext = { US: { calendar: [{ kind: 'cpi', title: 'Consumer Price Index (September data)', date: '2026-10-14', time: '08:30' }] } };
  const days = app.weekEvents('2026-10-12', [{ kind: 'cpi', title: 'Consumer Price Index (September data)', date: '2026-10-14', time: '08:30' }], [{ symbol: 'JPM', earnings_date_pretty: '2026-10-13' }], []);
  const next = app.nextWeekEvent(days, '10:00');
  assert.equal(next.symbol, 'JPM');
  assert.equal(next.inDays, 1);
  const today = app.weekEvents('2026-10-14', [{ kind: 'cpi', title: 'CPI', date: '2026-10-14', time: '08:30' }], [], []);
  assert.equal(app.nextWeekEvent(today, '09:00'), null, 'a release already out today is not next');
  assert.equal(app.nextWeekEvent(today, '08:00').time, '08:30');
});

test('a context panel without a read says why: a restart, or a failed read', () => {
  assert.match(app.contextNotice({ available: false, reason: 'restart' }), /Restart QUANTA/);
  assert.match(app.contextNotice({ available: false, error: 'HTTP 502' }), /could not be read \(HTTP 502\)/);
  assert.equal(app.contextNotice({ available: true }), null);
  app.state.marketContext = { US: { available: false, reason: 'restart' } };
  app.renderMarketContext();
  for (const id of ['#sector-body', '#levels-body', '#cross-body']) assert.match(document.querySelector(id).textContent, /Restart QUANTA/, id);
});

test('the week lists the largest reports per day with timing and estimate, and the rest on demand', () => {
  const today = app.exchangeSession().today;
  const day2 = new Date(Date.parse(`${today}T12:00:00Z`) + 2 * 864e5).toISOString().slice(0, 10);
  const row = (symbol, cap, timing, eps) => ({ symbol, name: `${symbol} Inc.`, market_cap: cap, earnings_date: day2, earnings_timing: timing, eps_estimate: eps });
  const cal = [row('SMALL', 11e9, 'amc', 0.5), row('BIG', 900e9, 'bmo', 4.92), row('MID', 50e9, null, null), row('MID2', 40e9, 'amc', 1), row('TINY', 10e9, 'bmo', 0.1)];
  app.state.dashboard = { market: {}, news: {}, market_intel: { earnings_week: cal.slice(0, 1), earnings_calendar: cal, earnings_scope: 'US companies over $10B and the tracked universe' } };
  app.state.book = { s: 'off' };
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const col = document.querySelectorAll('#week-body .q-wk > li')[2];
  const shown = [...col.querySelectorAll('.ev--earn .ev-sym')].map((e) => e.textContent);
  assert.deepEqual(shown, ['BIG', 'MID', 'MID2', 'SMALL', 'TINY'], 'every report, largest first');
  assert.match(col.querySelector('.ev--earn').textContent, /Before open/);
  assert.match(col.querySelector('.ev--earn').textContent, /EPS est\. \$4\.92/);
  assert.equal(col.querySelector('details'), null, 'nothing is folded away');
  assert.match(col.querySelector('.q-wk-cnt').textContent, /5 reports/);
  assert.match(document.querySelector('#week-meta').textContent, /5 earnings reports/);
  assert.match(document.querySelector('#week-body .q-wk-src').textContent, /US companies over \$10B/);
});

test('after the close, nothing left today is next up', () => {
  const days = [{ date: '2026-10-09', items: [{ kind: 'earnings', symbol: 'DAL' }] }, { date: '2026-10-10', items: [] }, { date: '2026-10-11', items: [{ kind: 'earnings', symbol: 'FDX', timing: 'amc' }] }];
  assert.equal(app.nextWeekEvent(days, '21:36').symbol, 'FDX');
  assert.equal(app.nextWeekEvent(days, '11:00').symbol, 'DAL');
});

test('India reads its own indices: an all-green day is a broad advance, led by the strongest', () => {
  const india = { quotes: [
    { symbol: '^NSEI', label: 'Nifty 50', change_pct: 1.3 }, { symbol: '^BSESN', label: 'Sensex', change_pct: 1.23 },
    { symbol: '^NSEBANK', label: 'Bank Nifty', change_pct: 1.36 }, { symbol: '^CNXIT', label: 'Nifty IT', change_pct: 3.02 },
    { symbol: '^INDIAVIX', label: 'India VIX', change_pct: -5.09 }, { symbol: 'USDINR=X', label: 'USD/INR', change_pct: -0.1 },
    { symbol: 'GLD', label: 'Gold', change_pct: 1.57 }] };
  assert.deepEqual(app.indexQuotes(india).map((q) => q.label), ['Nifty 50', 'Sensex', 'Bank Nifty', 'Nifty IT'], 'currency and gold are not indices');
  const c = app.impactChain(india, {}, {});
  assert.equal(c.label, 'Broad advance, led by Nifty IT');
  assert.deepEqual(c.links.map((l) => [l.step, l.check]), [['Indices rise together', true], ['No clear leadership', false], ['Volatility eases', true]]);
  const split = app.indexChain({ quotes: [{ symbol: '^NSEI', label: 'Nifty 50', change_pct: 0.4 }, { symbol: '^CNXIT', label: 'Nifty IT', change_pct: -0.6 }] });
  assert.equal(split.label, 'Mixed tape');
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
  app.renderWhatChanged({ ...market, regime_changes: [{ text: 'Momentum -2 → -1', tone: 'bull', kind: 'factor', label: 'Momentum', from: -2, to: -1 }] }, {}, {});
  const cells = [...document.querySelectorAll('#changed-body tbody tr td')].map((c) => c.textContent.trim());
  assert.equal(cells[0], 'Headwind (-2)');
  assert.match(cells[1], /Headwind \(-1\)/);
});

test('the direction zone marks the score against the bearish and bullish cut-offs', () => {
  const html = app.dirZone({ shown: '50', score10: 5 });
  assert.match(html, /left:50%/);
  assert.match(html, /35 or less reads bearish, 65 or more bullish/);
  assert.equal(app.dirZone({ shown: '--', score10: 5 }), '', 'no zone without a score');
});

test('the regime says how long its posture has held', () => {
  const h = [{ date: '2026-10-06', posture: 'Lean Defensive' }, { date: '2026-10-07', posture: 'Neutral' }, { date: '2026-10-08', posture: 'Neutral' }, { date: '2026-10-09', posture: 'Neutral' }];
  assert.deepEqual(app.postureAge(h, 'Neutral'), { sessions: 3, since: '2026-10-07', prev: 'Lean Defensive', all: false });
  assert.equal(app.postureAgeText(app.postureAge(h, 'Neutral'), 'Neutral'), 'Neutral since Oct 7, 3 sessions');
  assert.equal(app.postureAgeText(app.postureAge(h.slice(0, 2), 'Neutral'), 'Neutral'), 'Neutral since today, from Lean Defensive');
  assert.equal(app.postureAge(h, 'Offensive'), null, 'a posture not yet recorded has no age');
});

test('the countdown and its time zone are exact', () => {
  // 16:00 in New York on 12 Oct 2026 (EDT, UTC-4) is 20:00 UTC.
  assert.equal(new Date(app.zonedEpoch('2026-10-12', '16:00', 'America/New_York')).toISOString(), '2026-10-12T20:00:00.000Z');
  assert.equal(new Date(app.zonedEpoch('2026-01-12', '09:30', 'America/New_York')).toISOString(), '2026-01-12T14:30:00.000Z', 'EST in winter');
  assert.equal(app.countdownText('2026-10-12', null, null, 'America/New_York', 2), 'in 2 days', 'no time: whole days');
  const soon = new Date(Date.now() + 90 * 60000);
  const p = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(soon).reduce((o, x) => ({ ...o, [x.type]: x.value }), {});
  assert.match(app.countdownText(`${p.year}-${p.month}-${p.day}`, `${p.hour}:${p.minute}`, null, 'America/New_York', 0), /^in 1 h (29|30) m$/);
});

test('the dial and the posture scale mark the reading', () => {
  const svg = app.directionDial({ shown: '50', score10: 5 });
  assert.match(svg, /data-at="50"/);
  assert.match(svg, /rotate\(90\.0deg\)/, '50 points straight up');
  assert.equal(app.directionDial({ shown: '--', score10: 5 }), '');
  const ladder = app.postureScale('Neutral', '30-55% gross');
  assert.match(ladder, /step 4 of 7/);
  assert.equal((ladder.match(/is-on/g) || []).length, 1);
  assert.equal(app.postureScale('Unknown posture', 'x'), '');
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
