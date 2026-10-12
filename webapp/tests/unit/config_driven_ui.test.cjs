/**
 * Production audit (October 2026) A-02 / A-05 / A-16 / A-26 / A-32: the page
 * types no ticker, scan size, limit, score band or version of its own. Each
 * comes from config (engine/thresholds.py) or live data, and changes with it.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/config_driven_ui.test.cjs
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
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');
const T = globalThis.__QUANTA_THRESHOLDS__;

test('scan menu sizes and the large-cap floor come from UNIVERSE config', () => {
  app.applyThresholds(document);
  const opt = (v) => document.querySelector(`#sp-scan option[value="${v}"]`);
  assert.equal(opt('quick').label, `Quick (${T.UNIVERSE.US_SCAN_LIMITS.quick} names)`);
  assert.match(opt('standard').textContent, new RegExp(`^Standard: ${T.UNIVERSE.US_SCAN_LIMITS.standard} most liquid`));
  assert.match(opt('large_cap').textContent, new RegExp(app.fmtCompactMoney(T.UNIVERSE.SCAN_MIN_MARKET_CAP.large_cap).replace('$', '\\$')));
  assert.match(opt('india_mid').textContent, new RegExp(`${T.UNIVERSE.INDIA_SCAN_LIMITS.india_mid} most liquid`));
  // Change the config and the menu follows.
  const old = T.UNIVERSE.US_SCAN_LIMITS.quick;
  T.UNIVERSE.US_SCAN_LIMITS.quick = old + 7;
  try {
    app.applyThresholds(document);
    assert.equal(opt('quick').label, `Quick (${old + 7} names)`);
  } finally {
    T.UNIVERSE.US_SCAN_LIMITS.quick = old;
    app.applyThresholds(document);
  }
});

test('the markup itself states no scan size, so a missing config never shows a stale number', () => {
  const raw = new JSDOM(html).window.document;
  const stated = [...raw.querySelectorAll('#sp-scan option')].filter((o) => /\b\d+ most liquid|\(\d+ names\)/.test(`${o.label} ${o.textContent}`));
  assert.deepEqual(stated.map((o) => o.value), []);
});

test('the position count takes its default and ceiling from CAPITAL config', () => {
  const pos = document.querySelector('#sp-positions');
  pos.value = '';
  app.applyThresholds(document);
  assert.equal(pos.max, String(T.CAPITAL.MAX_POSITIONS_LIMIT));
  assert.equal(pos.value, String(T.CAPITAL.MAX_POSITIONS_DEFAULT));
});

test('score ranges follow LADDER.SCORE_BANDS', () => {
  const floors = T.LADDER.SCORE_BANDS.map(([f]) => f).sort((a, b) => b - a);
  assert.equal(app.scoreBandRange(floors[0] + 1), `${floors[0]}+`);
  assert.equal(app.scoreBandRange(floors[1]), `${floors[1]}–${floors[0]}`);
  assert.equal(app.scoreBandRange(floors[floors.length - 1] - 1), `below ${floors[floors.length - 1]}`);
  assert.equal(app.scoreBandRange(null), 'n/a');
});

test('research suggestions are recent searches then today’s movers, never a fixed list', () => {
  const intel = { movers_up: [{ symbol: 'AAA' }, { symbol: 'BBB' }], movers_down: [{ symbol: 'CCC' }] };
  const s = app.researchSuggestions(['BBB', 'ZZZ'], intel);
  assert.deepEqual(s.tickers, ['BBB', 'ZZZ', 'AAA', 'CCC']);
  assert.equal(s.recentCount, 2);
  assert.deepEqual(app.researchSuggestions([], {}).tickers, []);
});

test('the template carries no fixed ticker, freshness claim or typed version', () => {
  const raw = new JSDOM(html).window.document;
  assert.equal(raw.querySelectorAll('#an-quick-chips [data-ticker]').length, 0, 'no fixed suggestion chips');
  assert.ok(raw.querySelector('#an-quick-chips').hasAttribute('hidden'), 'the row hides until it has suggestions');
  for (const sel of ['#an-ticker', '#sp-custom-tickers']) {
    assert.doesNotMatch(raw.querySelector(sel).getAttribute('placeholder'), /\b[A-Z]{2,5}(\.NS)?\b,/, `${sel} placeholder lists tickers`);
  }
  assert.doesNotMatch(raw.querySelector('#freshness-time').textContent, /just now/);
  assert.doesNotMatch(raw.querySelector('.footer-disclaimer-text').textContent, /v\d+\.\d+/);
});

test('D3: scores read out of 100 through SCORE.TO_100, and follow it', () => {
  assert.equal(app.fmtScore100(6.87), String(Math.round(6.87 * T.SCORE.TO_100)));
  assert.equal(app.score100(12), 100, 'clamped to the scale');
  assert.equal(app.score100(null), null);
  assert.equal(app.fmtScore100(''), '');
  const old = T.SCORE.TO_100;
  T.SCORE.TO_100 = old * 2;
  try {
    assert.equal(app.fmtScore100(3), String(Math.round(3 * old * 2)));
  } finally {
    T.SCORE.TO_100 = old;
  }
  for (const f of ['static/app.js', 'static/elite-pro.js']) {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', f), 'utf8');
    // Risk points are a count, not a score; only scores move to the 100 scale.
    assert.doesNotMatch(src.replace(/of 10 risk points/g, ''), /of 10[`'" ]|\/10[`'"< ]/, `${f} shows no 10-point score`);
  }
});
