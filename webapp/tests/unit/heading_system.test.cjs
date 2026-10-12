/**
 * Styling pass (October 2026): one heading and spacing system for every page
 * (docs/audit-ui/STYLING_BRIEF.md). The tokens live in quanta.css; the page,
 * panel, label, hint, empty-state and notice rules read them, and the older
 * per-page heading overrides that fought each other are gone.
 *
 * Run: node --test tests/unit/heading_system.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const css = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'quanta.css'), 'utf8');
const section = css.slice(css.indexOf('Heading and spacing system'));
const rule = (sel) => {
  const i = section.indexOf(`${sel} {`);
  assert.ok(i >= 0, `${sel} is styled by the system`);
  return section.slice(i, section.indexOf('}', i));
};

test('the system tokens are declared once, in px on the 8px grid where they are spacing', () => {
  assert.ok(section.length < css.length, 'the system section exists');
  const tokens = { '--hs-page': '24px', '--hs-panel': '15px', '--hs-strip': '40px', '--hs-desc': '12px',
    '--hs-label': '12px', '--hs-empty': '17px', '--hs-gap': '16px', '--hs-sub': '13.5px' };
  for (const [k, v] of Object.entries(tokens)) assert.match(section, new RegExp(`${k}: ${v};`), k);
  assert.equal((css.match(/--hs-page:/g) || []).length, 1, 'declared once');
});

test('page head: serif title and a one-line purpose on one baseline', () => {
  assert.match(rule('.view .page-title'), /font-size: var\(--hs-page\)/);
  const head = rule('.view .page-head-l');
  assert.match(head, /flex-direction: row/);
  assert.match(head, /align-items: baseline/);
  const sub = rule('.view .page-sub');
  assert.match(sub, /white-space: nowrap/);
  assert.match(sub, /text-overflow: ellipsis/);
  assert.doesNotMatch(css, /:is\(#view-analyze, #view-picks\) \.page-title \{ font-size/, 'no per-page title size');
  assert.doesNotMatch(css, /#view-holdings \.page-title \{ font-size/, 'no per-page title size');
});

test('panel header strip: one title at the panel size, at most the strip height, description on the same line', () => {
  const strip = rule('.card-header');
  assert.match(strip, /min-height: var\(--hs-strip\)/);
  assert.match(rule('.card-title'), /font-size: var\(--hs-panel\)/);
  const desc = rule('.card-sub');
  assert.match(desc, /font-size: var\(--hs-desc\)/);
  assert.match(desc, /white-space: nowrap/);
  assert.match(rule('.qpro-kicker'), /font-size: var\(--hs-panel\)/);
});

test('form labels are sentence case at the label size, helper text is one muted line', () => {
  const label = rule('.form-field > span');
  assert.match(label, /font-size: var\(--hs-label\)/);
  assert.match(label, /text-transform: none/);
  assert.match(label, /letter-spacing: 0/);
  assert.doesNotMatch(css, /\.form-field > span \{[^}]*text-transform: uppercase/, 'no tracked all-caps labels');
  const hint = rule('.field-hint');
  assert.match(hint, /font-size: var\(--hs-desc\)/);
  assert.match(hint, /white-space: nowrap/);
});

test('empty states: a 17px title and a single-row list of what comes back, no numbered cards', () => {
  assert.match(rule('.empty-state.large h3'), /font-size: var\(--hs-empty\)/);
  const list = rule('.empty-preview');
  assert.match(list, /display: flex/);
  assert.match(rule('.empty-preview li::before'), /content: none/);
});

test('notices: one component, one line, details behind a disclosure', () => {
  assert.match(rule('.q-notice'), /font-size: var\(--hs-desc\)/);
  assert.match(rule('.q-notice-t'), /white-space: nowrap/);
  assert.match(rule('.q-notice-body'), /white-space: normal/);
});

test('a truncated one-line text shows its full text as a title on hover or focus', () => {
  const { JSDOM } = require('jsdom');
  const html = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
  global.document = dom.window.document;
  global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  global.requestAnimationFrame = () => 0;
  global.window = undefined;
  const app = require('../../static/app.js');
  const hint = document.getElementById('sp-risk-hint');
  hint.textContent = 'A long helper line that does not fit';
  Object.defineProperty(hint, 'scrollWidth', { value: 300, configurable: true });
  Object.defineProperty(hint, 'clientWidth', { value: 120, configurable: true });
  app.titleIfTruncated(hint);
  assert.equal(hint.getAttribute('title'), 'A long helper line that does not fit');
  Object.defineProperty(hint, 'scrollWidth', { value: 100, configurable: true });
  app.titleIfTruncated(hint);
  assert.equal(hint.getAttribute('title'), null, 'no title once it fits');
  assert.match(fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'app.js'), 'utf8'),
    /TRUNCATING_TEXT = '[^']*\.field-hint[^']*\.card-sub[^']*\.page-sub[^']*\.q-notice-t/);
});

test('the base type scale collapses to three text steps: 12, 13.5 and 15 (larger sizes only for page titles, empty titles and hero figures)', () => {
  const tok = (k) => (css.match(new RegExp(`${k}:\\s*([0-9.]+px)`)) || [])[1];
  assert.equal(tok('--fs-2xs'), '12px');
  assert.equal(tok('--fs-xs'), '12px');
  assert.equal(tok('--fs-sm'), '13.5px');
  assert.equal(tok('--fs-md'), '13.5px');
  assert.equal(tok('--fs-lg'), '15px');
  assert.equal(tok('--fs-section'), '15px');
  assert.equal(tok('--fs-xl'), '17px');
});

test('My Portfolio sub-heads use the sub-head size, and Allocation sits under its strip', () => {
  const rule = (sel) => (css.match(new RegExp(`\\n${sel.replace(/\./g, '\\.')} \\{[^}]*\\}`)) || [''])[0];
  assert.match(rule('.hp-h3'), /font-size: var\(--hs-sub\)/);
  assert.match(rule('.hp-h4'), /font-size: var\(--hs-sub\)/);
  assert.match(rule('.mpo-alloc'), /align-content: start/);
});

test('My Portfolio Overview panel titles use the panel-title size', () => {
  assert.match(css, /\n\.mpo-h h2 \{[^}]*font-size: var\(--hs-panel\)/);
  assert.match(css, /\n\.mpo-perf \.q-head-title \{ font-size: var\(--hs-panel\); \}/);
});
