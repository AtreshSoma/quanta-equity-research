/**
 * Research at a glance (2026-10-09): the technical signals read in one coloured
 * word from config bands, and the search examples come from UI.RESEARCH_SUGGESTIONS.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/research_signals.test.cjs
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

const card = (technical, price = 100) => {
  const el = document.createElement('div');
  el.innerHTML = app.technicalSignalsHtml(technical, price, '$');
  return el;
};
const tone = (el, key) => (el.querySelector(`[data-signal="${key}"] .sg-pill`) || {}).className || '';

test('direction reads green or red; RSI uses the configured overbought and oversold bands', () => {
  const ob = T.REGIME.RSI_OVERBOUGHT; const os = T.UI.TECH_RSI_OVERSOLD;
  const up = card({ trend_direction: 'UP', macd_signal: 'BULLISH', rsi: os - 1 });
  assert.match(tone(up, 'trend'), /sg--bull/);
  assert.match(tone(up, 'macd'), /sg--bull/);
  assert.match(tone(up, 'rsi'), /sg--bull/);
  assert.match(up.querySelector('[data-signal="rsi"]').textContent, /Oversold/);
  const down = card({ trend_direction: 'DOWN', macd_signal: 'BEARISH', rsi: ob + 1 });
  assert.match(tone(down, 'trend'), /sg--bear/);
  assert.match(tone(down, 'rsi'), /sg--bear/);
  assert.match(down.querySelector('.section-kicker-tail').textContent, /0 bullish, 3 bearish, 0 neutral/);
});

test('a price near support is amber, otherwise its distance is stated once', () => {
  const near = T.UI.TECH_NEAR_LEVEL_PCT;
  const el = card({ support_level: 100 - near / 2, resistance_level: 120 });
  assert.match(tone(el, 'support'), /sg--warn/);
  assert.match(el.querySelector('[data-signal="support"]').textContent, /Near support/);
  assert.match(el.querySelector('[data-signal="resistance"]').textContent, /20\.0% above/);
  assert.equal(card({}).innerHTML.includes('sg-row'), true, 'missing levels still say Not identified');
});

test('search examples per index come from config, for each market', () => {
  const us = app.researchIndexGroups('US');
  const india = app.researchIndexGroups('IN');
  assert.deepEqual(us, T.UI.RESEARCH_SUGGESTIONS.US);
  assert.deepEqual(india, T.UI.RESEARCH_SUGGESTIONS.IN);
  for (const [, items] of [...us, ...india]) {
    assert.ok(items.length >= 2 && items.length <= 3);
    items.forEach(([t, name]) => { assert.match(t, /^[A-Z0-9.&-]+$/); assert.ok(name, `${t} has a company name`); });
  }
});
