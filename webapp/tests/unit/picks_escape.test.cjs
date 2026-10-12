/* Every server string on the Picks page is escaped: the report, the legacy
 * path and the Pro cards are rendered from a payload whose every string leaf,
 * the currency symbol and every link carry markup. No element or javascript:
 * link may come out of it. */
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
global.filterWords = app.filterWords;
const pro = require('../../static/elite-pro.js');
const { payload, CASES } = require('../fixtures/picks_calls.cjs');

const MARK = '"><img src=x data-xss>\'';
// Keys the page matches on (enums, tickers used as lookups) keep their value
// and gain the markup as a suffix, so the logic still runs on real rows.
const poison = (v, k) => {
  if (typeof v === 'string') return /url$/i.test(k || '') ? 'javascript:alert(1)' : v + MARK;
  if (Array.isArray(v)) return v.map((x) => poison(x, k));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([kk, x]) => [kk, poison(x, kk)]));
  return v;
};
const KEEP = new Set(['call', 'status', 'kind', 'key', 'state', 'market']);
const poisonRow = (v, k) => {
  if (typeof v === 'string' && KEEP.has(k)) return v;
  if (typeof v === 'string') return poison(v, k);
  if (Array.isArray(v)) return v.map((x) => poisonRow(x, k));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([kk, x]) => [kk, poisonRow(x, kk)]));
  return v;
};

const render = (data) => {
  global.window = {};
  try { app.renderPicks(data); } finally { global.window = undefined; }
  return document.querySelector('#picks-results');
};
const bad = (box) => [...box.querySelectorAll('[data-xss], img[src="x"], a[href^="javascript"]')].map((n) => n.outerHTML.slice(0, 80));

for (const [name, make] of Object.entries({ full: () => payload(), ...CASES })) {
  test(`server strings are escaped: ${name}`, () => {
    const data = { ...poisonRow(make()), currency: `$${MARK}` };
    const box = render(data);
    globalThis.QuantaPro.injectPicksExtras(data);
    assert.ok(box.textContent.length > 100, 'the report rendered');
    assert.deepEqual(bad(box), []);
    for (const card of [pro.stageTableCard(data), pro.funnelCard(data)]) {
      const frag = JSDOM.fragment(String(card || ''));
      assert.equal(frag.querySelectorAll('[data-xss]').length, 0);
    }
    const { stock_picks: _drop, ...legacy } = data;
    assert.deepEqual(bad(render(legacy)), [], 'legacy path');
  });
}
