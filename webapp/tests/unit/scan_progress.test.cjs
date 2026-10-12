/**
 * A13 on the page: staged progress with counts, the three data clocks kept
 * apart (bar session, provider quote, scan completion), and the setup panel
 * collapsing on phones after a run. Synthetic values only.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/scan_progress.test.cjs
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

const text = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

test('the stage list marks finished stages and counts the current one', () => {
  app.renderScanStages({ key: 'analysing', label: 'Analysing names', done: 37, total: 100, elapsed_s: 71.6 });
  const list = document.querySelector('#sp-stages');
  const items = [...list.querySelectorAll('li')].map((li) => [li.dataset.state, li.textContent.replace(/\s+/g, ' ').trim()]);
  assert.deepEqual(items[0], ['done', 'Choosing the universe']);
  assert.deepEqual(items[1], ['current', 'Analysing names: 37 of 100 · 72 s']);
  assert.equal(items[2][0], 'pending');
  assert.equal(items.length, 4);
});

test('the data line keeps bar session, quote time and scan time apart', () => {
  const t = text(app.dataDatesLineHtml({ data_dates: {
    bar_session: '2026-10-02', bar_sessions: { '2026-10-02': 98, '2026-10-01': 2 },
    quote_time: '2026-10-02T20:00:00+00:00', scan_completed: '2026-10-04T19:03:00+00:00' } }));
  assert.match(t, /Daily bars through 2026-10-02 \(98 names; 2 on an earlier session\)/);
  assert.match(t, /provider quote 2026-10-02 \d\d:\d\d/);
  assert.match(t, /scan completed 2026-10-04 \d\d:\d\d/);
  assert.doesNotMatch(t, /today|yesterday/i);
  assert.equal(app.dataDatesLineHtml({}), '');
});

test('a missing quote time is stated, not invented', () => {
  const t = text(app.dataDatesLineHtml({ data_dates: { bar_session: '2026-10-02', bar_sessions: { '2026-10-02': 5 },
    quote_time: null, scan_completed: '2026-10-04T19:03:00+00:00' } }));
  assert.match(t, /no provider quote time/);
});

test('after a run the setup stays beside the report on a wide screen, and folds where the layout stacks', () => {
  const card = document.querySelector('.form-card');
  const toggle = document.querySelector('#sp-setup-toggle');
  const summary = document.querySelector('#sp-setup-summary');
  document.querySelector('#sp-capital').value = '2500';
  app.applyThresholds(document); // the page fills the position default from CAPITAL config
  const positions = globalThis.__QUANTA_THRESHOLDS__.CAPITAL.MAX_POSITIONS_DEFAULT;
  const had = global.window;
  global.window = {};
  try {
    // Wide: the sidebar stays open and there is nothing to toggle.
    window.matchMedia = () => ({ matches: false, addEventListener() {} });
    app.collapseSetupAfterRun();
    assert.ok(!card.classList.contains('is-collapsed'));
    assert.equal(toggle.hidden, true);
    assert.equal(summary.hidden, true);
    // Stacked: the form folds to a one-line summary and can be reopened.
    window.matchMedia = () => ({ matches: true, addEventListener() {} });
    app.collapseSetupAfterRun();
    assert.equal(summary.hidden, false);
    assert.match(summary.textContent, new RegExp(`\\$2,500, Balanced mandate, up to ${positions} positions, sector caps on$`));
    assert.ok(card.classList.contains('is-collapsed'));
    assert.equal(toggle.hidden, false);
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    toggle.click();
    assert.ok(!card.classList.contains('is-collapsed'));
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(summary.hidden, true, 'the full form replaces the summary');
    assert.equal(toggle.textContent, 'Hide scan setup');
    app.collapseSetupAfterRun();                       // every run folds it again where the layout stacks
    assert.ok(card.classList.contains('is-collapsed'));
  } finally {
    global.window = had;
  }
});

test('A12: focus moves to the result heading only after a user-run scan', () => {
  const data = { scan_status: 'no_picks', total_scanned: 6, recommendations: [], chase_alerts: [], early_momentum: [],
    error_summary: { records: [] }, accounting: { version: 1, universe: 6, could_not_analyse: { count: 1, by_reason: {} },
      rule_rejected: { count: 5, by_reason: {} }, reached_book: 0, not_analysed: 0, warnings: {}, book_stages: [], funded: 0 } };
  global.window = {};                                     // the empty state consults window.QuantaPro
  const before = document.querySelector('#sp-capital');
  before.focus();
  app.renderPicks(data);                                   // a re-render (restore, market switch) keeps focus
  assert.equal(document.activeElement, before);
  app.onUserScanComplete(data, false);
  const heading = document.activeElement;
  assert.ok(heading.matches('[data-result-heading]'), heading.outerHTML.slice(0, 80));
  assert.equal(heading.getAttribute('tabindex'), '-1');
  assert.match(heading.textContent, /No positions sized/);
});

test('scan menu: every option shows a short label, and a stated name count matches its full text', () => {
  const opts = [...document.querySelectorAll('#sp-scan option')];
  assert.ok(opts.length >= 25);
  for (const o of opts) {
    const label = o.getAttribute('label');
    assert.ok(label && label.length <= 40, `${o.value} has a short label`);
    const n = label.match(/(\d+) names/);
    if (n) assert.equal(n[1], (o.textContent.match(/(\d+) most liquid/) || [])[1], o.value);
  }
});
