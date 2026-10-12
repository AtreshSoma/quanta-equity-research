/**
 * Decision D1 (October 2026): every call the model makes carries the
 * "Model not yet validated" flag until the evidence status reaches the
 * validated status named in config (LADDER.STRONG_BUY_EVIDENCE). The flag
 * links to the evidence page; a validated model shows no flag.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/model_flag.test.cjs
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
const hp = require('../../static/my-portfolio.js');
const T = globalThis.__QUANTA_THRESHOLDS__;
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'my_portfolio_view.json'), 'utf8'));
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
const VALID = () => ({ status: T.LADDER.STRONG_BUY_EVIDENCE, text: 'Validated', permits_recommendation: true });
const clone = (o) => JSON.parse(JSON.stringify(o));

test('the validated status comes from config, and anything else is flagged', () => {
  assert.equal(app.evidenceValidated(VALID()), true);
  for (const ev of [{ status: 'UNTESTED' }, { status: 'NO_EDGE' }, { status: 'STALE' }, {}, null, undefined]) {
    assert.equal(app.evidenceValidated(ev), false, JSON.stringify(ev));
    const a = frag(app.modelFlagHtml(ev)).querySelector('a.model-flag');
    assert.ok(a, 'flag shown');
    assert.equal(a.textContent, 'Model not yet validated');
    assert.equal(a.getAttribute('href'), '#track');
    assert.equal(a.dataset.viewLink, 'track');
  }
  assert.equal(app.modelFlagHtml(VALID()), '');
  const old = T.LADDER.STRONG_BUY_EVIDENCE;
  const was = VALID();
  T.LADDER.STRONG_BUY_EVIDENCE = 'SOMETHING_ELSE';
  try {
    assert.equal(app.evidenceValidated(was), false, 'the bar follows config');
  } finally {
    T.LADDER.STRONG_BUY_EVIDENCE = old;
  }
});

test('the reason travels in the flag title, escaped', () => {
  const a = frag(app.modelFlagHtml({ status: 'NO_EDGE', status_text: 'no edge <b>found</b>' })).querySelector('a');
  assert.match(a.title, /no edge <b>found<\/b>/);
  assert.equal(a.querySelector('b'), null);
});

test('My Portfolio puts the flag beside each call and drops it once the model is validated', () => {
  const pf = clone(fixture.portfolio);
  let d = frag(hp.guidanceHtml(pf));
  const hero = d.querySelector('.hp-hero');
  assert.ok(hero.querySelector('.model-flag'), 'hero call is flagged');
  for (const card of d.querySelectorAll('.hp-gc')) assert.ok(card.querySelector('.model-flag'), 'each guidance card is flagged');
  for (const p of pf.positions) if (p.guidance && p.guidance.evidence) p.guidance.evidence = VALID();
  d = frag(hp.guidanceHtml(pf));
  assert.equal(d.querySelector('.model-flag'), null, 'no flag on a validated model');
});

test('the Research rail, Overview brief and Pro watchlist use the same flag, not a second name', () => {
  const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  const pro = fs.readFileSync(path.join(root, 'static', 'elite-pro.js'), 'utf8');
  assert.match(appJs, /\$\{decision \|\| heroQualifier \? modelFlagHtml\(ev\) : ''\}/);
  assert.match(appJs, /am\.qualifier \? modelFlagHtml\(/);
  assert.match(pro, /class="qpro-qual"[^>]*>Model not yet validated</);
  for (const f of ['templates/index.html', 'static/app.js', 'static/my-portfolio.js', 'static/mp-overview.js']) {
    assert.doesNotMatch(fs.readFileSync(path.join(root, f), 'utf8'), /Unproven model/, f);
  }
  assert.match(html, /What does "Model not yet validated" mean\?/);
});
