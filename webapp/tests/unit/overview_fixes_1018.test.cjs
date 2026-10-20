/**
 * Overview review fixes (2026-10-18): a refresh never blanks good data, fund
 * prices say they are fund prices, a rounded zero has no sign, the size key is
 * drawn at the chart's scale, glossary terms open a real tooltip, and story
 * times carry their day.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/overview_fixes_1018.test.cjs
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

test('a context refresh that came back without data keeps the last good values', () => {
  const prev = { available: true, series: { SPY: { closes: [1, 2, 3] } }, levels: { symbol: 'SPY', sma50: 767.4, last: 770 },
    sectors: [{ symbol: 'XLK', change_pct: -3, above_sma50: false }], futures: [{ symbol: 'ES=F', price: 7748 }] };
  const next = { available: true, series: {}, levels: { symbol: 'SPY' }, sectors: [{ symbol: 'XLK', change_pct: null, above_sma50: null }],
    futures: [{ symbol: 'ES=F', price: null }], missing: ['series', 'levels'] };
  const out = app.mergeContext(prev, next);
  assert.deepEqual(out.series, prev.series);
  assert.equal(out.levels.sma50, 767.4);
  assert.equal(out.sectors[0].change_pct, -3);
  assert.equal(out.sectors[0].above_sma50, false);
  assert.equal(out.futures[0].price, 7748);
  // A complete refresh replaces the old values outright.
  const fresh = { available: true, series: { SPY: { closes: [9] } }, levels: { sma50: 770 }, sectors: [{ symbol: 'XLK', change_pct: 1 }] };
  assert.deepEqual(app.mergeContext(prev, fresh).series, fresh.series);
  assert.equal(app.mergeContext(prev, fresh).sectors[0].change_pct, 1);
});

test('a percentage that rounds to zero carries no sign', () => {
  assert.equal(app.fmtPct(-0.004, 2, false), '0.00%');
  assert.equal(app.fmtPct(0.004, 2, false), '0.00%');
  assert.equal(app.fmtPct(-0.006, 2, false), '-0.01%');
  assert.equal(app.fmtPct(1.234, 1, false), '+1.2%');
});

test('a quote carried by a fund is named as the fund', () => {
  assert.equal(app.isFundProxy('SPY', 'S&P 500'), true);
  assert.equal(app.isFundProxy('USO', 'Oil'), true);
  assert.equal(app.isFundProxy('^VIX', 'VIX'), false);
  assert.equal(app.isFundProxy('BTC-USD', 'Bitcoin'), false);
  assert.equal(app.isFundProxy('ES=F', 'S&P 500 futures'), false);
  app.renderMarketPulse({ quotes: [{ symbol: 'USO', label: 'Oil', price: 161.76, change_pct: 2.8 }] });
  const cell = document.querySelector('#market-pulse .ovx-pu');
  assert.match(cell.querySelector('.ovx-pu-sym').textContent, /^USO fund$/);
  assert.match(cell.querySelector('.tip').dataset.tip, /fund's share price/);
});

test('price-axis ticks are round numbers inside the plotted range', () => {
  const t = app.niceTicks(755.2, 781.9, 4);
  assert.ok(t.length >= 2 && t.length <= 5, t.join(','));
  t.forEach((v) => { assert.ok(v > 755.2 && v < 781.9); assert.equal(v % 5, 0); });
  assert.deepEqual(app.niceTicks(5, 5), []);
});

test('company names drop legal suffixes and share classes', () => {
  assert.equal(app.shortName('Meta Platforms, Inc.'), 'Meta Platforms');
  assert.equal(app.shortName('Tesla, Inc.'), 'Tesla');
  assert.equal(app.shortName('PayPay Corporation - American Depositary Shares'), 'PayPay');
  assert.equal(app.shortName('Bending Spoons S.p.A.'), 'Bending Spoons');
});

test('a story from an earlier day shows its day beside the time', () => {
  const tz = 'America/New_York';
  const now = app.newsTime(new Date().toISOString(), tz);
  assert.match(now, /^\d{1,2}:\d{2} [AP]M$/);
  const old = app.newsTime(new Date(Date.now() - 3 * 864e5).toISOString(), tz);
  assert.match(old, /^[A-Z][a-z]{2} \d{1,2}:\d{2} [AP]M$/);
});

test('the busiest reporting day sets the bar scale, and every name is a 30px target', () => {
  const today = app.exchangeSession().today;
  const d1 = new Date(Date.parse(`${today}T12:00:00Z`) + 1 * 864e5).toISOString().slice(0, 10);
  const d2 = new Date(Date.parse(`${today}T12:00:00Z`) + 2 * 864e5).toISOString().slice(0, 10);
  const row = (symbol, cap, timing, date) => ({ symbol, name: `${symbol} Inc.`, market_cap: cap, earnings_date: date, earnings_timing: timing });
  const cal = [row('BIG', 2e12, 'amc', d2), row('MID', 1e12, 'bmo', d1)];
  app.state.dashboard = { market: {}, news: {}, market_intel: { earnings_week: cal, earnings_calendar: cal, earnings_scope: 'US companies over $10B' } };
  app.state.book = { s: 'off' };
  app.state.market = 'US';
  app.state.marketContext = { US: { calendar: [] } };
  app.renderWeekCalendar();
  const h = (sym) => parseFloat(document.querySelector(`#week-body .ovx-wkn[data-ticker="${sym}"]`).closest('.ovx-wkd').querySelector('.ovx-wkstack').style.height);
  assert.equal(h('BIG'), 100);
  assert.equal(h('MID'), 50);
  const cols = app.weekColumns([{ date: d1, items: [{ kind: 'earnings', symbol: 'A', cap: 3e11, timing: 'am' }, { kind: 'earnings', symbol: 'B', cap: 1e11, timing: 'pm' }] }]);
  assert.equal(cols[0].am, 3e11);
  assert.equal(cols[0].pm, 1e11);
  app.state.dashboard = null;
  app.state.marketContext = {};
});

test('the news rail names what is coming up instead of repeating a trending row', () => {
  const today = app.exchangeSession().today;
  const day1 = new Date(Date.parse(`${today}T12:00:00Z`) + 864e5).toISOString().slice(0, 10);
  app.state.market = 'US';
  app.state.marketContext = { US: { calendar: [{ kind: 'cpi', macroKind: 'cpi', title: 'Consumer Price Index', date: day1, time: '08:30' }] } };
  const out = app.comingUp({ earnings_calendar: [{ symbol: 'META', name: 'Meta Platforms, Inc.', market_cap: 1.6e12, earnings_date: day1, earnings_timing: 'amc' }] }, 3);
  assert.equal(out[0].what, 'CPI');
  assert.match(out[0].when, /08:30 ET/);
  assert.equal(out[1].what, 'Meta Platforms reports earnings');
  assert.match(out[1].when, /after close/);
  app.state.marketContext = {};
});

test('the hero chart offers 1D, 1W, 1M, YTD and 1Y, and each range draws against its own reference', () => {
  const day = (h, m) => Date.UTC(2026, 9, 16, h, m);
  const pts = Array.from({ length: 40 }, (_, i) => [day(14, 0) + i * 864e5 / 40, 700 + i]);
  const y = { points: pts, timezone: 'America/New_York' };
  const ytd = app.heroSeries('ytd', null, [y], { price: 739 }, { sma50: 720, sma200: 690 });
  assert.equal(ytd.ref, 700);
  assert.deepEqual(ytd.lines.map((l) => l.cls), ['is-prior', 'is-50', 'is-200']);
  const html = app.heroChartHtml('ytd', ytd, 'S&P 500');
  const doc = new JSDOM(`<body>${html}</body>`).window.document;
  assert.deepEqual([...doc.querySelectorAll('.ovx-rtab')].map((b) => b.textContent), ['1D', '1W', '1M', 'YTD', '1Y']);
  assert.equal(doc.querySelector('.ovx-rtab[aria-selected="true"]').textContent, 'YTD');
  assert.match(doc.querySelector('.ovx-cread').textContent, /739\.00\+5\.57%year to date/);
  assert.ok(doc.querySelector('.ovx-plot[tabindex="0"]'), 'the plot takes keyboard focus for the crosshair');
  assert.ok(doc.querySelector('.ovx-cx [class~="ovx-ctip"]'), 'a crosshair tooltip is ready');
  // 1D before the open: the last session, then today's pre-market (dashed), against Friday's close.
  const fri = { points: Array.from({ length: 78 }, (_, i) => [Date.UTC(2026, 9, 16, 13, 30) + i * 3e5, 760 + (i % 5)]), timezone: 'America/New_York' };
  const pre0 = Date.UTC(2026, 9, 19, 8, 0);
  const d1 = { points: Array.from({ length: 50 }, (_, i) => [pre0 + i * 6e4, 767]), previous_close: 763.25, timezone: 'America/New_York',
    session: { pre: [pre0, Date.UTC(2026, 9, 19, 13, 30)], regular: [Date.UTC(2026, 9, 19, 13, 30), Date.UTC(2026, 9, 19, 20, 0)] } };
  const one = app.heroSeries('1d', null, [d1, fri], {}, {});
  assert.equal(one.ref, 763.25);
  assert.equal(one.refLabel, 'Fri close');
  assert.equal(one.ext.filter(Boolean).length, 11, 'pre-market thinned to the five-minute pace, plus the latest bar');
  assert.equal(one.ext.slice(0, 78).some(Boolean), false);
  const oneHtml = new JSDOM(`<body>${app.heroChartHtml('1d', one, 'S&P 500')}</body>`).window.document;
  assert.ok(oneHtml.querySelector('.ovx-line.is-ext'));
  assert.match(oneHtml.querySelector('.ovx-cread').textContent, /pre-market, vs Fri close/);
});

test('one story from two outlets shows once, and old stories give way to new ones', () => {
  const now = Date.parse('2026-10-19T13:00:00Z');
  const iso = (h) => new Date(now - h * 36e5).toISOString();
  const items = [
    { title: "China's third-quarter economic growth quickens to 4.5% as exports surge, beating estimates", published: iso(3), source: 'CNBC' },
    { title: "China's economic growth picks up as AI boom boosts exports", published: iso(2), source: 'BBC' },
    { title: 'Fed signals patience on rates', published: iso(1) },
  ];
  const out = app.dedupeSimilar(items);
  assert.equal(out.length, 2);
  const china = out.find((a) => /China/.test(a.title));
  assert.equal(china.source, 'BBC', 'the newer telling is kept');
  assert.equal(china.alsoIn, 1);
  const many = Array.from({ length: 12 }, (_, i) => ({ title: `Story ${i}`, published: iso(i) })).concat([{ title: 'Old', published: iso(90) }]);
  assert.ok(!app.freshNews(many, 48, 10, now).some((a) => a.title === 'Old'));
  assert.equal(app.freshNews(many.slice(0, 3).concat([{ title: 'Old', published: iso(90) }]), 48, 10, now).length, 4, 'kept when too few are fresh');
});
