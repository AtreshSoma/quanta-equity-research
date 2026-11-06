/**
 * Evidence clock (2026-11-06, roadmap P1-6): the Evidence page says how far the
 * model version running now is from its pre-registered sample, using only the
 * numbers the API sends. It states when a verdict becomes possible, never what
 * the verdict is.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/evidence_clock.test.cjs
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
const text = (h) => new JSDOM(`<body>${h}</body>`).window.document.body.textContent.replace(/\s+/g, ' ').trim();

const CLOCK = { model_fingerprint: 'abc123', horizon_days: 63, recorded: 40, resolved: 12,
  independent_dates: 3, min_calls: 30, min_independent_dates: 10, ready: false };

test('the clock reports resolved calls and independent dates against the API minimums', () => {
  const t = text(app.trackClockNote(CLOCK));
  assert.match(t, /this model version: 12 resolved calls on 3 independent dates at 63 sessions/);
  assert.match(t, /needs 30 on 10/);
  assert.doesNotMatch(t, /edge|validated|works/i, 'the clock never states a verdict');
});

test('a sample that reaches the minimum says only that a verdict is possible', () => {
  const t = text(app.trackClockNote({ ...CLOCK, resolved: 31, independent_dates: 10, ready: true }));
  assert.match(t, /enough for a verdict/);
});

test('singular wording, and nothing when the API sent no clock', () => {
  assert.match(text(app.trackClockNote({ ...CLOCK, resolved: 1, independent_dates: 1 })), /1 resolved call on 1 independent date /);
  assert.equal(app.trackClockNote(null), '');
  assert.equal(app.trackClockNote({}), '');
});

test('the per-engine freeze note carries the clock', () => {
  const t = text(app.trackFreezeNote({ recorded: 40, recorded_on_current_code: 10,
    code_fingerprint: 'deadbeefcafe', evidence_clock: CLOCK }));
  assert.match(t, /code deadbeef/);
  assert.match(t, /this model version: 12 resolved calls/);
});

test('a model freeze is stated, and a broken one is flagged', () => {
  assert.equal(app.trackModelFreezeNote(null), '');
  assert.match(text(app.trackModelFreezeNote({ until: '2027-02-05', active: true, intact: true })), /scoring code frozen until Feb 5, 2027/);
  assert.match(text(app.trackModelFreezeNote({ until: '2027-02-05', active: true, intact: false })), /changed during its freeze/);
  assert.match(text(app.trackModelFreezeNote({ until: '2026-10-01', active: false, intact: true })), /freeze ended/);
});
