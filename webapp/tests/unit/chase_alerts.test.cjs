/**
 * A07 on the page: every chase alert shows its company, trigger, the bar it
 * was measured on and its distance from the 20-day average, with no relative
 * "today" / "yesterday" wording. Synthetic names only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/chase_alerts.test.cjs
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

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

const gap = {
  ticker: 'SYNG', company: 'Synthetic Gap Co', trigger: 'gap', move_pct: 7.2, threshold_pct: 3,
  bar_date: '2026-10-01', from_date: null, session_date: '2026-10-02', distance_from_20dma: -0.081,
  momentum_1m: -0.41, rsi: 26.4, reason: 'Chase alert: SYNG opened with a 7.2% gap above the prior close on 2026-10-01 (threshold 3%).',
};
const run = {
  ticker: 'SYNR', company: 'Synthetic Run Co', trigger: 'run_5d', move_pct: 11.3, threshold_pct: 10,
  bar_date: '2026-10-02', from_date: '2026-09-25', session_date: '2026-10-02', distance_from_20dma: 0.094,
  momentum_1m: 0.18, rsi: 71.0,
};

test('each alert names company, trigger, bar dates and distance from the 20-day average', () => {
  const t = text(app.renderPicksBuckets([gap, run], []));
  assert.match(t, /SYNG Synthetic Gap Co Gap \+7\.2% at the open \(over 3%\) 2026-10-01 · latest bar 2026-10-02 -8\.1% -41\.0% 26\.4/);
  assert.match(t, /SYNR Synthetic Run Co 5-day run \+11\.3% \(over 10%\) 2026-09-25 to 2026-10-02 \+9\.4%/);
});

test('a gap inside a decline is labelled a gap, not "extended"', () => {
  const h = app.renderPicksBuckets([gap], []);
  assert.doesNotMatch(h, /extended/i);
  assert.match(text(h), /Chase alerts · recent gap or run-up/);
});

test('no relative day words, and missing fields say so', () => {
  const legacy = { ticker: 'SYNL', reason: 'Chase alert: SYNL rose 21.0% in 10 sessions', rsi: 60, momentum_1m: 0.2 };
  const h = app.renderPicksBuckets([gap, run, legacy], []);
  assert.doesNotMatch(h, /\btoday\b|\byesterday\b/i);
  const row = h.split('data-chase="SYNL"')[1].split('</tr>')[0];
  assert.match(text(row), /Not recorded .*rose 21\.0% in 10 sessions Not recorded Not recorded/);
});
