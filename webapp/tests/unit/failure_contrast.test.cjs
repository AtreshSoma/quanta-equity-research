/**
 * A11: failure and throttle states take their colours from theme tokens, and
 * the heading contrast is at least 4.5:1 over its tinted panel in both themes.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/failure_contrast.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');

function block(selector) {
  const i = css.indexOf(`${selector} {`);
  assert.ok(i >= 0, selector);
  return css.slice(i, css.indexOf('}', i));
}
function tokens(selector) {
  const out = {};
  for (const m of block(selector).matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}
function rgba(v) {
  const hex = v.match(/^#([0-9a-f]{6})$/i);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)).concat(1);
  const m = v.match(/rgba?\(([^)]+)\)/);
  const p = m[1].split(',').map((x) => Number(x.trim()));
  return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
}
const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
const lum = (c) => {
  const [r, g, b] = c.slice(0, 3).map((x) => { const s = x / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

function headingContrast(theme, tone) {
  const t = { ...tokens(':root'), ...(theme === 'dark' ? tokens('[data-theme="dark"]') : {}) };
  const panel = over(rgba(t[`${tone}-dim`]), rgba(t['bg-elev-2']));
  return ratio(rgba(t[`${tone}-bright`]), panel);
}

for (const theme of ['light', 'dark']) {
  for (const tone of ['bear', 'warn']) {
    test(`${tone} failure heading meets 4.5:1 in the ${theme} theme`, () => {
      assert.ok(headingContrast(theme, tone) >= 4.5, `${headingContrast(theme, tone).toFixed(2)}:1`);
    });
  }
}

test('the failure states use token classes, not inline colours', () => {
  assert.match(block('.scan-fail h3'), /color: var\(--bear-bright\)/);
  assert.match(block('.scan-fail.is-throttle h3'), /color: var\(--warn-bright\)/);
  const results = document.querySelector('#picks-results');
  for (const status of ['failed', 'provider_rate_limited']) {
    app.renderPicks({ scan_status: status, error_summary: { records: [], universe_size: 10 }, total_scanned: 10 });
    const panel = results.querySelector('.scan-fail');
    assert.ok(panel, status);
    assert.equal(panel.classList.contains('is-throttle'), status === 'provider_rate_limited');
    assert.doesNotMatch(results.innerHTML, /#ff8a8a|#ffb86b|rgba\(255,\s*(90|180)/);
  }
});

test('the error-details table keeps token colours (axe: 2.96:1 header under inline opacity)', () => {
  const h = app.errorDetailsCardHtml({ error_summary: { records: [{ ticker: 'SYNE', error_type: 'analysis_exception', error_message: 'x', stage: 'analyze_stock' }], by_type: { analysis_exception: 1 } } });
  assert.ok(h.includes('error-details-table'));
  assert.doesNotMatch(h, /opacity:\s*0\.\d|rgba\(255/);
});

test('the watchlist export box is keyboard reachable when it scrolls (axe scrollable-region-focusable)', () => {
  const { tradingViewExportCard } = require('../../static/elite-pro.js');
  const h = tradingViewExportCard({ recommendations: Array.from({ length: 25 }, (_, i) => ({ ticker: `S${i}` })) });
  assert.match(h, /id="qpro-tv-string" tabindex="0" role="region" aria-label="[^"]+"/);
});
