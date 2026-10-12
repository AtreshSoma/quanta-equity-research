/**
 * Styling pass, Picks (docs/audit-ui/STYLING_BRIEF.md): headings that say
 * something once.
 *
 * - The results panel does not repeat the page title; neither panel header
 *   carries a sub-line that restates the page purpose.
 * - Form labels are sentence case; the footnote has no em dash.
 * - The empty state is one title, one sentence, one row of what comes back.
 * - Pre-scan notices are one line, with the steps ("Fix") or the reason
 *   ("Why") behind a disclosure.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/picks_headings.test.cjs
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
const T = globalThis.__QUANTA_THRESHOLDS__;
const view = () => document.getElementById('view-picks');
const words = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

test('no panel title repeats the page title, and no panel sub-line restates the page purpose', () => {
  const page = words(view().querySelector('.page-title'));
  const titles = [...view().querySelectorAll('.card-title')].map(words);
  assert.deepEqual(titles, ['Scan setup', 'Scan results']);
  for (const t of titles) assert.doesNotMatch(t, new RegExp(page, 'i'));
  for (const sub of view().querySelectorAll('.card-header .card-sub:not(.setup-summary)')) {
    assert.fail(`panel sub-line left in a header: ${words(sub)}`);
  }
});

test('form labels are sentence case; the footnote has no em dash', () => {
  const labels = [...view().querySelectorAll('.form-field > span')].map((s) => s.childNodes[0].textContent.trim()).filter(Boolean);
  for (const l of labels) {
    const rest = l.split(/\s+/).slice(1).filter((w) => !/^(S&P|NASDAQ|US|ETF)$/.test(w));
    assert.ok(rest.every((w) => w === w.toLowerCase() || /^[A-Z]{2,}$/.test(w)), `sentence case: ${l}`);
  }
  assert.ok(labels.includes('Capital budget') && labels.includes('Client mandate') && labels.includes('Max positions'));
  assert.doesNotMatch(view().innerHTML, /—|&mdash;/, 'no em dash anywhere in the Picks view');
});

test('the empty state is one title, one sentence, one button and one row of what comes back', () => {
  const empty = view().querySelector('#picks-results .empty-state');
  assert.equal(words(empty.querySelector('h3')), 'Nothing scanned yet');
  const ps = [...empty.querySelectorAll(':scope > p')];
  assert.equal(ps.length, 1);
  assert.equal(words(ps[0]).split(/(?<=\.)\s/).length, 1, 'one sentence');
  const items = [...empty.querySelectorAll('.empty-preview li')];
  assert.equal(items.length, 4);
  for (const li of items) assert.equal(li.querySelector('span'), null, 'a label, not a card with a paragraph');
  assert.equal(empty.querySelectorAll('button').length, 1);
});

test('pre-scan notices are one line, with the steps or the reason behind a disclosure', () => {
  for (const id of ['sp-market-notice', 'sp-sec-notice']) {
    const el = document.getElementById(id);
    assert.ok(el.classList.contains('q-notice') && el.tagName === 'DIV', id);
  }
  const contact = { state: 'missing', label: 'no SEC contact configured', fix: 'Set the contact variable and restart the server' };
  const box = document.createElement('div');
  box.innerHTML = app.noticeHtml(app.secContactNotice({ sec_contact: contact }));
  assert.match(words(box.querySelector('.q-notice-t')), /^Buy calls blocked: no SEC contact configured$/);
  assert.equal(words(box.querySelector('summary .q-notice-more')), 'Fix');
  assert.match(words(box.querySelector('.q-notice-body')), /Set the contact variable and restart the server/);
  assert.equal(app.secContactNotice({ sec_contact: { state: 'configured' } }), null);
  // India: no step for the user to take, so the disclosure gives the reason.
  const select = document.getElementById('sp-market');
  select.value = 'IN';
  select.dispatchEvent(new dom.window.Event('change'));
  const india = document.getElementById('sp-market-notice');
  if (!india.hidden) {
    const uncovered = Object.values(T.EVENTS.UNCOVERED_LISTINGS);
    assert.match(words(india.querySelector('.q-notice-t')), new RegExp(`^Watchlist calls only for ${uncovered.join(' and ')} listings$`));
    assert.equal(words(india.querySelector('.q-notice-more')), 'Why');
  }
});

test('the report header is one strip: the count, the run settings on one line, one Export menu', () => {
  const { payload } = require('../fixtures/picks_calls.cjs');
  global.window = {};
  try { app.renderPicks(payload()); } finally { global.window = undefined; }
  const head = document.querySelector('#picks-results .pk-head');
  assert.equal(head.querySelectorAll('h3').length, 1, 'one title');
  const meta = head.querySelector('.pk-meta');
  assert.equal(meta.getAttribute('title'), [...meta.querySelectorAll('li')].map(words).join(' \u00b7 '),
    'the settings line carries its full text, in case it truncates');
  const menus = head.querySelectorAll('details.pk-export');
  assert.equal(menus.length, 1, 'one Export menu');
  assert.equal(words(menus[0].querySelector('summary')), 'Export');
  assert.ok(menus[0].querySelector('[data-pk-csv]'), 'Download CSV sits in the menu');
  assert.equal(head.querySelectorAll(':scope > .pk-actions').length, 0, 'no row of export buttons beside the title');
});

test('a phone-width report stacks the analysis panels in one column, so no factor value clips', () => {
  // The report steps on its own width (container pkv). Every container rule
  // that sets the panel row applies to a phone report; the last one wins.
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const rules = [];
  const re = /@container pkv \(max-width: (\d+)px\) \{([\s\S]*?)\n\}/g;
  for (let m; (m = re.exec(css));) {
    const row = /\.pk-row3 \{ grid-template-columns: ([^;]+);/.exec(m[2]);
    if (row) rules.push({ max: Number(m[1]), cols: row[1].trim() });
  }
  assert.ok(rules.length, 'the panel row steps on the report width');
  const last = rules[rules.length - 1];
  assert.equal(last.cols, '1fr', 'the rule that applies last on a phone is one column');
  assert.equal(last.max, Math.min(...rules.map((r) => r.max)), 'the one-column rule is the narrowest');
});

test('a failed scan is one state in the results panel: title, the reason once, one button to run again', () => {
  const results = document.getElementById('picks-results');
  const reason = 'The provider did not answer.';
  results.innerHTML = app.scanErrorHtml({ error: reason });
  const box = results.firstElementChild;
  assert.equal(box.getAttribute('role'), 'alert');
  assert.ok(box.classList.contains('error-box'), 'smoke tests find the failure by .error-box');
  assert.equal(box.querySelectorAll('h3').length, 1);
  assert.deepEqual([...box.querySelectorAll('p')].map(words), [reason]);
  const buttons = box.querySelectorAll('button');
  assert.equal(buttons.length, 1);
  assert.ok(buttons[0].hasAttribute('data-run-scan-now'));
  results.innerHTML = app.scanErrorHtml({ error: reason, error_detail: { reason: 'cancelled' } });
  assert.notEqual(words(results.querySelector('h3')), words(box.querySelector('h3')), 'a cancelled scan is not called a failure');
  // Both job paths (stream and fallback poll) render it, and the sidebar
  // status does not repeat the reason the panel already shows.
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.equal((src.match(/results\.innerHTML = scanErrorHtml\(payload\)/g) || []).length, 2);
});

test('on a narrow report each compare box stays beside its own name', () => {
  // The narrow layout flattens the group lists into one wrapping row; the
  // list item itself must stay a box, or its checkbox wraps away from its name.
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  for (const m of css.matchAll(/([^{}]*\.pk-alist li[^{}]*)\{([^}]*)\}/g)) {
    assert.doesNotMatch(m[2], /display:\s*contents/, `flattened list item: ${m[1].trim()}`);
  }
});
