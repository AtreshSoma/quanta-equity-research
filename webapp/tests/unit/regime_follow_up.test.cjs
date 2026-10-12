/**
 * Owner report (2026-10-08): the live Overview read "Data Incomplete" because
 * index histories and the earnings screen arrived after the first read. The
 * page now re-reads the dashboard once, after the server's retry time, when the
 * first read had missing inputs; a complete read, or a second try, schedules
 * nothing more.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/regime_follow_up.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');
global.document = new JSDOM(html, { url: 'http://127.0.0.1:5500/' }).window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');

const read = (missing, retry = 15) => ({
  retry_seconds: retry,
  market: { market_regime_engine: { dataFreshness: { missingInputs: missing } } },
});

test('one follow-up read after the server retry time, only when inputs were missing', () => {
  const timers = [];
  const real = global.setTimeout;
  global.setTimeout = (fn, ms) => { timers.push(ms); return { unref() {} }; };
  try {
    app.scheduleRegimeFollowUp(read([]), 'US');
    assert.deepEqual(timers, [], 'a complete read schedules nothing');
    app.scheduleRegimeFollowUp(read(['participation', 'earnings_calendar']), 'US');
    assert.deepEqual(timers, [16000], 'retry_seconds + 1, from the server');
    app.scheduleRegimeFollowUp(read(['participation']), 'US');
    assert.equal(timers.length, 1, 'a second incomplete read does not loop');
    app.scheduleRegimeFollowUp(read(['participation'], 0), 'IN');
    assert.equal(timers.length, 1, 'no retry time from the server, no follow-up');
  } finally {
    global.setTimeout = real;
  }
});
