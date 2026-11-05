/**
 * Research tear sheet, Earnings report card (discover_enrich.report_read): hidden
 * until a recent report arrives, then the result, the move, what changed since,
 * the next date and the post-earnings-drift lean, qualified by the stock's record.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/earnings_report.test.cjs
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

const frag = (h) => { const el = document.createElement('div'); el.innerHTML = h; return el.firstElementChild; };

const AMZN = {
  date: '2026-10-29', timing: 'amc', days_since: 6, eps_actual: 1.89, eps_estimate: 1.96, surprise_pct: -3.57, result: 'miss',
  beats: 7, reports_counted: 9, reaction: { date: '2026-10-30', move_pct: -1.41, typical_abs_pct: 6.23 }, since_pct: 9.12,
  estimates: { window_days: 7, '0q': -1.45, '0y': -0.88 }, revision_pct: -1.17,
  analysts: { raises: 3, cuts: 9, upgrades: 0, downgrades: 0, firms: 17 },
  drift: { sessions: 20, followed: 2, counted: 6 }, next: { date: '2027-02-04', eps_estimate: 2.41 },
  lean: { direction: 'down', reasons: ['EPS missed the estimate by 3.6%', 'the stock fell 1.4% on the report', 'estimates were cut 1.2% since'], history_agrees: false },
};

test('no recent report: an empty hidden slot the Research read can fill', () => {
  const el = frag(app.earningsReportHtml(null));
  assert.equal(el.hasAttribute('hidden'), true);
  assert.equal(el.hasAttribute('data-earnings-report'), true);
});

test('a report reads its result, move, revisions, next date and the lean', () => {
  const el = frag(app.earningsReportHtml(AMZN));
  const text = el.textContent.replace(/\s+/g, ' ');
  assert.equal(el.hasAttribute('hidden'), false);
  assert.match(text, /after the close, 6 days ago/);
  assert.match(text, /1\.89 vs 1\.96/);
  assert.match(text, /Missed 3\.6%, beat 7 of last 9/);
  assert.match(text, /typical ±6\.2%/);
  assert.match(text, /3 up \/ 9 down/);
  assert.match(text, /EPS est\. 2\.41/);
  assert.equal(el.querySelector('.er-dir').textContent, 'Leans down');
  assert.ok(el.querySelector('.er-dir').classList.contains('bear'));
  assert.match(text, /2 of 6 times, so the lean is weak here/);
  assert.match(text, /gone the other way \(\+9\.1% since\)/);
  assert.match(text, /not a forecast/);
});

test('disagreeing signals read as no clear lean, without a weak-lean or against-the-lean note', () => {
  const r = { ...AMZN, since_pct: -2, lean: { direction: 'mixed', reasons: ['EPS beat the estimate by 5.5%', 'the stock fell 1.9% on the report'], history_agrees: false } };
  const text = frag(app.earningsReportHtml(r)).textContent;
  assert.match(text, /No clear lean/);
  assert.match(text, /The signals disagree: EPS beat the estimate by 5\.5%; the stock fell 1\.9% on the report\./);
  assert.doesNotMatch(text, /lean is weak|other way/);
});

test('a report not yet traded says so instead of a move', () => {
  const r = { ...AMZN, reaction: null, since_pct: null, drift: null, lean: { direction: 'mixed', reasons: [], history_agrees: null } };
  const text = frag(app.earningsReportHtml(r)).textContent;
  assert.match(text, /Next session/);
  assert.match(text, /not traded yet/);
  assert.match(text, /has not traded on the report yet/);
});
