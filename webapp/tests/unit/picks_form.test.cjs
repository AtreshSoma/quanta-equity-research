/**
 * Portfolio Builder layout + honesty regressions (September 2026 UI review).
 *
 *  - Paired form fields share a row track, so a label that wraps in one column
 *    cannot push its own control out of line with the one beside it.
 *  - No control clips its own value: the mandate options are short and the
 *    sizing effect lives in a hint under the control.
 *  - Nothing funded means no deployed capital, no utilisation and no reward
 *    ratio; those read as absent rather than as a measured zero.
 *
 * Run: node --test tests/unit/picks_form.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const proJs = fs.readFileSync(path.join(root, 'static', 'elite-pro.js'), 'utf8');

test('paired form fields align on a shared row track', () => {
  // A flex column per field let each label set its own height, which put the
  // Market select 17px above Capital Budget whenever that label wrapped.
  assert.match(css, /@supports \(grid-template-rows: subgrid\)/, 'subgrid alignment is declared');
  assert.match(css, /\.form-row\.two > \.form-field \{ display: grid; grid-template-rows: subgrid; grid-row: span 3; \}/);
  assert.match(css, /\.form-row\.two \{ grid-template-rows: auto auto auto;/, 'the parent supplies the three tracks');
});

test('field constraints sit under the control, not inside the label', () => {
  // A wrapping label was the cause of the misalignment, so the hint moved out.
  assert.doesNotMatch(html, /<span>Capital budget <em/, 'the hint is no longer inside the label');
  assert.match(html, /<span>Capital budget<\/span>/, 'sentence case since the styling pass');
  assert.match(html, /<em class="field-hint" id="sp-capital-hint">/);
  assert.match(css, /\.field-hint \{/, 'the hint has a style of its own');
});

test('the mandate control cannot clip its own option text', () => {
  // Aggressive was retired in audit F14 (it sized exactly like Balanced).
  for (const label of ['Conservative', 'Balanced']) {
    assert.match(html, new RegExp(`>${label}</option>`), `${label} is short enough to fit`);
  }
  assert.doesNotMatch(html, /Balanced \(full risk budget\)/, 'the clipped long label is gone');
  // The information it carried is not lost, only relocated.
  assert.match(appJs, /const MANDATE_HINTS = /);
  assert.match(appJs, /'Full risk budget'/, 'one line under the control since the styling pass');
  assert.match(html, /id="sp-risk-hint"/);
  // Any future long value degrades to an ellipsis rather than a hard cut.
  assert.match(css, /\.form-field input, \.form-field select \{ text-overflow: ellipsis; \}/);
});

test('an unfunded book reports measured zero capital and leaves ratios unavailable', () => {
  // Portfolio header.
  assert.match(appJs, /const naCell = '<span class="pm-na">N\/A<\/span>'/);
  assert.match(appJs, /Planned loss at stops/);
  assert.match(appJs, /\$\{fmtMoney\(totalMaxLoss, sym, 0\)\}/);
  assert.match(appJs, /avgRR > 0 \? `\$\{fmtNumber\(avgRR, 2\)\}:1` : naCell/);
  assert.doesNotMatch(appJs, /pm-value bear">-\$\{fmtMoney\(totalMaxLoss/, 'no unconditional -$0');
  // Capital allocation table: a watchlist row holds no capital.
  assert.match(proJs, /const dash = '<span class="qpro-na">--<\/span>'/); // "--" like every other missing value since the styling pass
  assert.doesNotMatch(proJs, /<td class="mono bear">-\$\{fmtCur\(risk\$/, 'no unconditional -$0 per row');
});

test('the clear-cache control no longer floats over page content', () => {
  // Fixed to the bottom-right, it sat on top of whichever table was there.
  assert.doesNotMatch(html, /clear-cache-fab/, 'the floating pill is gone');
  assert.doesNotMatch(css, /\.clear-cache-fab \{ position: fixed/);
  // A maintenance action, not navigation: it lives in the data-status popover
  // with the feed ledger it relates to, labelled rather than icon-only.
  assert.match(html, /id="data-health-panel"[\s\S]*?id="clear-cache"[\s\S]*?Clear data cache/, 'it sits in the data-status popover');
});

test('the empty Position List previews what a scan returns', () => {
  assert.match(html, /Nothing scanned yet/);
  assert.match(html, /class="empty-preview"/);
  assert.match(html, /data-run-scan-now/);
  // One primary button per screen: the rail owns it, this one is secondary.
  assert.match(html, /class="btn-secondary empty-cta" data-run-scan-now/);
});

test('user-facing copy names no internal function', () => {
  for (const src of [appJs, proJs, html]) {
    assert.doesNotMatch(src, /analyze_stock/, 'analyze_stock is a function name, not a trader-facing term');
  }
});

test('glass is confined to floating chrome, never to data surfaces', () => {
  // Translucency behind a price is contrast you cannot afford in a trading
  // tool, so the effect is limited to chrome that floats over scrolling
  // content: the sticky topbar and the data-health popover. The "How QUANTA
  // works" guide uses none; its demos are opaque panels like every other.
  const bare = css.replace(/[/][*][^]*?[*][/]/g, '');   // comments hold selector-like text
  const found = [...bare.matchAll(/([.#][\w-]+)\s*\{[^{}]*backdrop-filter\s*:\s*blur\(/g)]
    .map((m) => m[1]);
  assert.deepEqual([...new Set(found)].sort(), ['.ovx-ctip', '.ovx-tipbox', '.q-health-panel', '.topbar']);
  // The Overview Glass Rule (DESIGN.md): frost on its raised reading surfaces (the
  // market mood, the lead story, the outlook card) and the chart's range tabs, over
  // static ambient light only, never over moving data.
  const frosted = [...bare.matchAll(/([.#][\w-]+)\s*\{[^{}]*backdrop-filter\s*:\s*var\(--ovx-frost\)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(frosted)].sort(), ['.ovx-lead', '.ovx-mood', '.ovx-outlook', '.ovx-rtabs']);

  // Every data surface stays opaque.
  for (const sel of ['.q-panel', '.q-table', '.pm-cell', '.card', '.q-board-table', '.qpro-card']) {
    assert.ok(!found.includes(sel), sel + ' must not be glass');
  }
  assert.doesNotMatch(html, /hw-glass/, 'no glass class in the markup');
});

test('glass degrades to the opaque surface it was contrast-tested against', () => {
  // The @supports guard means a browser without backdrop-filter keeps the
  // solid background rather than rendering a thin, unreadable tint.
  assert.match(css, /@supports \(\(backdrop-filter: blur\(1px\)\) or \(-webkit-backdrop-filter: blur\(1px\)\)\)/);
  const topbarBase = css.match(/\.topbar \{[^}]+\}/)[0];
  assert.match(topbarBase, /background: var\(--bg-elev-1\)/, 'opaque base retained');
  // Safari needs the prefixed property too. Count declarations only, so the
  // @supports condition itself is not counted.
  const prefixed = (css.match(/^\s*-webkit-backdrop-filter:\s*blur\(/gm) || []).length;
  const standard = (css.match(/^\s*backdrop-filter:\s*blur\(/gm) || []).length;
  assert.equal(standard, 2, 'exactly two glass surfaces: topbar and health popover');
  assert.equal(prefixed, standard, 'every backdrop-filter is paired with -webkit-');
});

test('content-bearing glass is thicker than bare chrome glass', () => {
  // Thin glass let the table underneath punch through the source ledger's own
  // list. Assert the relationship, not literal values, so retuning the effect
  // cannot silently make the popover unreadable again.
  const alpha = (name, block) => {
    const i = block.indexOf(name + ':');
    if (i === -1) return null;
    const seg = block.slice(i, block.indexOf(';', i));
    const nums = seg.slice(seg.indexOf('rgba(') + 5, seg.lastIndexOf(')')).split(',');
    return nums.length === 4 ? Number(nums[3].trim()) : null;
  };
  // Anchor on the rule itself: the token header comment mentions the
  // [data-theme="dark"] selector hundreds of lines earlier, without a brace.
  const darkAt = css.indexOf('[data-theme="dark"] {');
  const light = css.slice(css.indexOf(':root'), darkAt);
  const dark = css.slice(darkAt);
  for (const [theme, block] of [['light', light], ['dark', dark]]) {
    const chrome = alpha('--glass-tint', block);
    const panel = alpha('--glass-tint-panel', block);
    assert.ok(chrome !== null && panel !== null, theme + ' defines both tints');
    assert.ok(panel > chrome, theme + ': content panel retains the thicker tint');
    assert.ok(panel >= 0.9, theme + ': source text needs a near-opaque base');
    assert.ok(chrome >= 0.5 && chrome <= 0.65, theme + ': chrome transmits the backdrop; controls retain their solid surfaces');
  }
  // Glass is functional translucency only: no decorative sheen gradient or
  // specular rim on the chrome (September 2026 redesign).
  assert.doesNotMatch(css, /--glass-sheen/);
  assert.match(css, /\.topbar \{\s*background: var\(--glass-tint\);/);
});
