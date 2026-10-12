/**
 * Decision D5 (October 2026): Guide and the model evidence page are named in the
 * header, and stay reachable at phone width (the evidence page is a trust and
 * disclosure requirement, not a hidden extra).
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/entry_points.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const doc = new JSDOM(html).window.document;

test('the header names Guide and Evidence, and each name contains its visible label', () => {
  for (const [id, label, view] of [['guide-link', 'Guide', 'guide'], ['evidence-link', 'Evidence', 'track']]) {
    const a = doc.getElementById(id);
    assert.ok(a.closest('header'), `${id} is in the header`);
    assert.equal(a.dataset.viewLink, view);
    assert.equal(a.querySelector('.guide-link-label').textContent, label);
    assert.match(a.getAttribute('aria-label').toLowerCase(), new RegExp(label.toLowerCase()), 'label in name (WCAG 2.5.3)');
  }
});

test('the links are not hidden at phone width', () => {
  assert.doesNotMatch(css, /@media \(max-width: 4\d\dpx\) \{ \.guide-link \{ display: none; \} \}/);
});
