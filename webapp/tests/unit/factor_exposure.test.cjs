/**
 * Factor exposure (2026-11-06, roadmap P1-4 / E3): the Risk exposure panel shows
 * the book's Fama-French loadings with their intervals, mutes loadings that are
 * not distinguishable from zero, and says why when nothing was measured.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/factor_exposure.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');

const hp = require('../../static/my-portfolio.js');
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
const words = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const FE = {
  available: true, days: 252, end: '2026-09-30', r2: 0.93, coverage: 0.8, excluded_non_us: ['RELIANCE.NS'],
  source: 'Kenneth R. French data library', factors_as_of: '2026-09-30', factors_stale: false,
  factors: [
    { factor: 'Mkt-RF', label: 'US market', beta: 1.14, ci95: [1.07, 1.21] },
    { factor: 'HML', label: 'High minus low book-to-market (value)', beta: -0.04, ci95: [-0.14, 0.06] },
  ],
};

test('loadings show with their intervals; one spanning zero is muted and says so', () => {
  const d = frag(hp.factorExposureHtml(FE));
  const mkt = d.querySelector('[data-factor="Mkt-RF"]');
  const cells = [...mkt.children].map(words);
  assert.deepEqual(cells, ['US market', '+1.14', '95% interval 1.07 to 1.21']);
  assert.ok(!mkt.classList.contains('is-muted'));
  const hml = d.querySelector('[data-factor="HML"]');
  assert.ok(hml.classList.contains('is-muted'));
  assert.match(words(hml), /not distinguishable from zero/);
});

test('the note states the fit, what was left out, coverage, and that it is not a forecast', () => {
  const t = words(frag(hp.factorExposureHtml(FE)).body);
  assert.match(t, /252 days to/);
  assert.match(t, /explain 93% of the daily moves/);
  assert.match(t, /RELIANCE\.NS left out/);
  assert.match(t, /Covers 80% of invested value/);
  assert.match(t, /not a forecast/);
});

test('unavailable says why; nothing sent renders nothing', () => {
  const t = words(frag(hp.factorExposureHtml({ available: false, reason: 'Factor data unavailable.' })).body);
  assert.match(t, /Factor exposure .*Factor data unavailable\./);
  assert.equal(hp.factorExposureHtml(undefined), '');
});
