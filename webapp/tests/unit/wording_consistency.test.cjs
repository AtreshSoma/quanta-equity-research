/**
 * Production audit (October 2026) A-15 / A-18 / A-19 / A-39: one word per
 * concept across pages, plain labels, and grammatical summaries.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/wording_consistency.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const { withArticle } = require('../../static/app.js');

test('summaries take the right article', () => {
  assert.equal(withArticle('insufficient data'), 'an insufficient data');
  assert.equal(withArticle('bullish'), 'a bullish');
});

test('My Portfolio shows one label for Hold / No New Money on every tab', () => {
  const mp = read('static/my-portfolio.js');
  assert.match(mp, /'Hold \/ No New Money': 'Hold, no new money'/);
  assert.match(mp, /const actionChip = \(a(, ev)?(, g)?\) => [^\n]*actionLabel\(a(, g)?\)/);
  for (const f of ['static/my-portfolio.js', 'static/mp-overview.js', 'static/mp-guidance.js']) {
    assert.doesNotMatch(read(f), /Hold, no adds/, f);
  }
});

test('the scan is named after the Picks tab everywhere the reader sees it', () => {
  for (const f of ['static/app.js', 'templates/index.html']) {
    assert.doesNotMatch(read(f), /\b[Pp]ortfolio [Ss]can\b/, f);
  }
});

test('market labels are plain: no "LTP" or "Open = L/H" shown', () => {
  const html = read('templates/index.html');
  assert.doesNotMatch(html, />Open = L\/H</);
  assert.match(html, />Opened at high\/low</);
  assert.doesNotMatch(read('static/app.js'), />LTP[ <]/);
});
