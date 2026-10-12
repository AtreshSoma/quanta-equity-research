/**
 * Frontend regressions from the second review round (September 2026).
 *
 *  - "See today's movers" in the Top Opportunities empty state was inert: the
 *    empty-state branch only wired the dashboard action handlers, while the
 *    [data-scroll-to] listener lived in wireExpandableRows.
 *  - The mandate hint must describe the selected mandate. Since audit F14 the
 *    Aggressive mandate (sized exactly like Balanced) is no longer offered.
 *
 * Run: node --test tests/unit/review_round2.test.cjs
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
const app = require('../../static/app.js');

test("the empty state's movers button actually scrolls to the board", () => {
  app.renderTopOpportunities({ universe_size: 100, analyst_picks: [] });
  const btn = document.querySelector('#portfolio-scan-body [data-scroll-to="top-movers"]');
  assert.ok(btn, 'the empty state offers the movers shortcut');

  const target = document.querySelector('#top-movers');
  let scrolledTo = null;
  target.scrollIntoView = () => { scrolledTo = 'top-movers'; };
  btn.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
  assert.equal(scrolledTo, 'top-movers', 'the button is wired, not decorative');
});

test('the mandate hint follows the selected mandate and quotes the configured scale', () => {
  const risk = document.querySelector('#sp-risk');
  risk.value = 'conservative';
  app.applyMandateHint();
  const hint = document.querySelector('#sp-risk-hint');
  const pct = `${Math.round(globalThis.__QUANTA_THRESHOLDS__.SIZING.MANDATE_SCALE.conservative * 100)}%`;
  assert.ok(hint.textContent.includes(pct), `${hint.textContent} quotes ${pct}`);
  risk.value = 'balanced';
  app.applyMandateHint();
  assert.equal(hint.textContent, app.MANDATE_HINTS.balanced);
});

test('Aggressive is not offered anywhere: it sized exactly like Balanced (audit F14)', () => {
  // The scan form and the guide calculator offer only mandates that change sizing.
  const offered = [...document.querySelectorAll('#sp-risk option, #hw-c-mandate option')].map((o) => o.value);
  assert.ok(offered.length >= 4, 'both selects are present');
  assert.ok(!offered.includes('aggressive'), `offered: ${offered}`);
  assert.deepEqual(Object.keys(app.MANDATE_HINTS).sort(),
    Object.keys(globalThis.__QUANTA_THRESHOLDS__.SIZING.MANDATE_SCALE).sort());
  // A scan shortcut opens Picks; it never changes the mandate behind the user's back.
  const risk = document.querySelector('#sp-risk');
  risk.value = 'conservative';
  const container = document.createElement('div');
  container.innerHTML = '<button type="button" data-run-scan>Go</button>';
  document.body.append(container);
  app.attachDashboardActionHandlers(container);
  container.querySelector('button').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
  assert.equal(risk.value, 'conservative');
});

test('a failed scan clears the "Scanning…" header rather than leaving it', () => {
  const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.match(appJs, /setPicksMeta\('Scan failed to start'\)/, 'the request-failure path resets it');
  assert.match(appJs, /'Scan cancelled' : 'Scan failed'/, 'the job-failure path resets it (cancelled or failed)');
  assert.match(appJs, /setPicksMeta\('Scan blocked/, 'the 409 path resets it');
});

test('a 409 offers to cancel the scan holding the slot', () => {
  const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.match(appJs, /res\.status === 409/, 'the busy response is handled, not thrown');
  assert.match(appJs, /\/cancel`,\s*\n?\s*\{ method: 'POST', headers: QUANTA_POST_HEADERS \}/,
    'the cancel endpoint is reachable from the UI');
});
