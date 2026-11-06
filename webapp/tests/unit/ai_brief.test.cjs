/**
 * AI brief panel (2026-11-06, roadmap P1-5 / E2): the panel offers to write a
 * brief, shows only what the server returns as checked, escapes model text,
 * and says plainly when a brief was withheld.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/ai_brief.test.cjs
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
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');
const doc = (h) => new JSDOM(`<body>${h}</body>`).window.document;

test('before writing: one button and what the brief will do', () => {
  const d = doc(app.aiBriefHtml(null));
  assert.ok(d.querySelector('[data-ai-brief-go]'));
  assert.match(d.body.textContent, /does not make a call/);
  assert.match(d.body.textContent, /checked against it before it is shown/);
});

test('a checked brief shows its three lists with citations, and escapes model text', () => {
  const res = { status: 'ok', model: 'claude-opus-5-5',
    brief: [{ text: 'QUANTA’s call on AAPL is WATCHLIST. <img src=x onerror=alert(1)>', cites: ['label'] }],
    bull: [{ text: 'Quality scores 71.0.', cites: ['factor_scores.quality'] }],
    bear: [{ text: 'The technical leg is weak at 44.0.', cites: ['factor_scores.technical'] }] };
  const d = doc(app.aiBriefHtml(res));
  assert.equal(d.querySelector('img'), null, 'model text is escaped');
  assert.equal(d.querySelectorAll('.ai-brief-list li').length, 3);
  assert.match(d.body.textContent, /The case for/);
  assert.match(d.body.textContent, /The case against/);
  assert.match(d.querySelector('.ai-cites').textContent, /label/);
  assert.match(d.body.textContent, /not investment advice/);
});

test('a withheld or unavailable brief says why and shows no model text', () => {
  const rejected = doc(app.aiBriefHtml({ status: 'rejected', findings: [{}, {}] })).body.textContent;
  assert.match(rejected, /Withheld: 2 statements could not be traced/);
  assert.match(doc(app.aiBriefHtml({ status: 'refused' })).body.textContent, /declined/);
  assert.match(doc(app.aiBriefHtml({ status: 'unavailable', reason: 'Off.' })).body.textContent, /Off\./);
});
