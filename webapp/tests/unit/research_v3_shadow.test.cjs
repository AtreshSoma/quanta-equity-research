/**
 * Research v3 shadow (2026-10-29): the shadow model's one muted line on the
 * Research tear sheet, and the pre-registered successor row in the Evidence
 * page's lineage table. The shadow is never the call, and every field from the
 * payload is escaped.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/research_v3_shadow.test.cjs
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
const windowStub = {
  setTimeout, clearTimeout, setInterval, clearInterval,
  location: { hash: '' }, history: { pushState() {}, replaceState() {} }, scrollY: 0, scrollTo() {},
};
global.window = undefined;
const app = require('../../static/app.js');
global.window = windowStub;
const $ = (sel) => document.querySelector(sel);
const frag = (h) => { const el = document.createElement('div'); el.innerHTML = h; return el; };

const v3 = {
  key: 'elite.research.v3', version: 'v3', score: 67.0, band_action: 'BUY',
  weights: { technical: 0.1, fundamental: 0.4, valuation: 0.1, quality: 0.3, sentiment: 0.1 },
  status: 'UNTESTED', note: 'Pre-registered shadow model. Not used for this call.',
};

test('the shadow line states the version, status and score, and that it is not the call', () => {
  const el = frag(app.shadowModelLineHtml({ shadow_model: v3 }));
  const line = el.querySelector('[data-shadow-model]');
  assert.ok(line, 'the line renders');
  assert.equal(line.textContent, 'Shadow model v3 (untested): score 67 — not used for this call.');
  assert.ok(line.classList.contains('muted'), 'a muted line, not a call');
});

test('no shadow payload, or a shadow without a score, renders nothing', () => {
  assert.equal(app.shadowModelLineHtml({}), '');
  assert.equal(app.shadowModelLineHtml(null), '');
  assert.equal(app.shadowModelLineHtml({ shadow_model: { version: 'v3', score: null, status: 'UNTESTED' } }), '');
});

test('every shadow field is escaped before it reaches the page', () => {
  const hostile = { shadow_model: { version: '<img src=x onerror=alert(1)>', score: 50, status: '<b>x</b>' } };
  const out = app.shadowModelLineHtml(hostile);
  assert.doesNotMatch(out, /<img/);
  assert.doesNotMatch(out, /<b>/);
  assert.match(out, /&lt;img/);
});

function evidenceEntry(extraLineage) {
  return {
    permits_recommendation: false, status: 'UNTESTED', status_text: 'Never validated', summary: 'Not tested.',
    tested_through: null, lineage: extraLineage,
  };
}

test('the Evidence lineage table shows the pre-registered v3 row as a successor, running in shadow', () => {
  app.renderTrackRecord({
    benchmark: 'SPY', benchmarks: ['SPY'], min_sample: 30, method: 'x', groups: [], recent: [],
    totals: { recorded: 0, resolved: 0, pending: 0, awaiting_maturity: 0, awaiting_resolution: 0, legacy_excluded: 0 },
    engines: [{
      engine: 'elite', label: 'Deep analysis', recorded: 0, evidence: evidenceEntry([
        { strategy: 'elite.research.v3', relation: 'successor', status: 'UNTESTED', status_text: 'Never validated',
          change: 'pre-registered 2026-10-29; running in shadow', summary: 'Never tested.', tested_through: null,
          doc: 'docs/preregistration/elite_research_v3.md' },
        { strategy: 'elite.alpha_only.v1', relation: 'earlier', status: 'NO_EDGE', status_text: 'Tested out of sample: no demonstrated edge',
          change: 'risk leg removed from the ranking composite', summary: 'old', tested_through: '2026-08-25' },
      ]),
    }],
  });
  const row = $('#track-body tr[data-successor]');
  assert.ok(row, 'the successor row is present');
  assert.equal(row.getAttribute('data-lineage'), 'elite.research.v3');
  assert.match(row.textContent, /pre-registered successor/);
  assert.match(row.textContent, /running in shadow/);
  assert.match(row.textContent, /Never validated/);
  assert.match(row.textContent, /docs\/preregistration\/elite_research_v3\.md/);
  const earlier = $('#track-body tr[data-lineage="elite.alpha_only.v1"]');
  assert.ok(earlier && !earlier.hasAttribute('data-successor'), 'earlier versions keep their own label');
  assert.match(earlier.textContent, /earlier version/);
});
