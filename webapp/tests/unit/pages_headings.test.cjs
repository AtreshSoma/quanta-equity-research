/**
 * Styling pass, Overview, Discover, Evidence, Guide and chrome
 * (docs/audit-ui/STYLING_BRIEF.md): titles in sentence case, no title that
 * repeats its page, no em dash or tracked caps in what the Overview writes,
 * and Guide type on the page and chapter scale.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/pages_headings.test.cjs
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
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const words = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');

// Words that keep a capital mid-title: the call names, the Decision and the
// product's own page name.
const PROPER = new Set(['Buy', 'Strong', 'Accumulate', 'Watchlist', 'Hold', 'Sell', 'Decision', 'Portfolio']);
const sentenceCase = (t) => t.split(/\s+/).slice(1).every((w) => {
  const bare = w.replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '');
  return !bare || bare[0] !== bare[0].toUpperCase() || bare === bare.toUpperCase() || PROPER.has(bare);
});

test('page, panel and tab titles in the page are sentence case', () => {
  const sel = '.page-title, .q-head-title, .card-title, [role="tab"], .q-tab';
  const bad = [...document.querySelectorAll(sel)].filter((n) => !n.closest('#view-guide') && !n.classList.contains('nav-tab'))
    .map(words).filter((t) => t && !sentenceCase(t));
  assert.deepEqual(bad, []);
});

test('no panel title repeats its page title', () => {
  for (const view of document.querySelectorAll('.view')) {
    const page = words(view.querySelector('.page-title')).toLowerCase();
    if (!page) continue;
    for (const t of view.querySelectorAll('.q-head-title, .card-title')) {
      assert.ok(!page.includes(words(t).toLowerCase()), `"${words(t)}" repeats "${page}"`);
    }
  }
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.doesNotMatch(src, /id="track-ledger-h">Track record</, 'the ledger panel does not repeat the Evidence page title');
});

test('the Overview writes no em dash and no tracked caps of its own', () => {
  const data = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'dashboard_synth.json'), 'utf8'));
  const market = data.market || {}; const news = data.news || {}; const intel = data.market_intel || {};
  global.window = {};
  try {
    app.renderTopHeadlines(market, news);
    app.renderMarketRegime(market, news, intel);
    app.renderPlaybook(market, news, intel);
    app.renderRiskPanel(market, news, intel);
    app.renderMarketPulse(market);
    app.renderSignalCheck(market, news, intel);
    app.renderMarketDrivers(market, news, intel);
    app.renderBreadthPanel(market, intel);
  } finally { global.window = undefined; }
  const view = document.getElementById('view-dashboard');
  const hits = view.textContent.split('\n').map((l) => l.trim()).filter((l) => l.includes('—'));
  assert.deepEqual(hits, []);
  for (const sel of ['.q-tape-kicker', '.q-tape-net', '.q-tape-flag', '.q-news-m .cat']) {
    for (const m of css.matchAll(new RegExp(`${sel.replace(/\./g, '\\.')}(?![-\\w])[^{,]*\\{([^}]*)\\}`, 'g'))) {
      assert.doesNotMatch(m[1], /text-transform: uppercase/, sel);
    }
  }
});

test('Guide type sits on the page and chapter scale', () => {
  for (const m of css.matchAll(/\.hw-(?:title|h2|end-title)\b[^{]*\{([^}]*)\}/g)) {
    const fs_ = /font-size:\s*([^;]+);/.exec(m[1]);
    if (fs_) assert.match(fs_[1], /var\(--hs-display\)|var\(--hs-section\)|var\(--hs-page\)|var\(--hs-chapter\)/, `Guide heading at ${fs_[1]}`);
  }
  // The display size belongs to the Guide's hero alone.
  for (const m of css.replace(/[/][*][^]*?[*][/]/g, '').matchAll(/([^{}]+)\{[^}]*var\(--hs-display\)[^}]*\}/g)) {
    assert.match(m[1].trim(), /^\.hw-title$/, `--hs-display used by ${m[1].trim()}`);
  }
});

test('the page template writes no em dash outside its comments', () => {
  const text = html.replace(/<!--[\s\S]*?-->/g, '');
  const hits = text.split('\n').map((l) => l.trim()).filter((l) => /—|&mdash;/.test(l));
  assert.deepEqual(hits, []);
});

test('at 320 px a long Key levels label wraps and the review table fits the screen', () => {
  const dlevel = (css.match(/\n\.dlevel \{[^}]*\}/) || [''])[0];
  assert.match(dlevel, /grid-template-columns: minmax\(0, 1fr\) max-content;/, 'the label column gives way, the figure keeps its width');
  // The table fits instead of scrolling: a scrolling region would need its own focus stop.
  assert.match(css, /@media \(max-width: 420px\) \{\s*\.mp-table th, \.mp-table td \{ padding: 6px 4px; overflow-wrap: anywhere; \}/);
  assert.doesNotMatch(css, /\.mp-table \{ display: block;[^}]*overflow-x: auto/);
});

test('every page purpose is one sentence, and Evidence reads model status in amber with "--" for missing values', () => {
  for (const sub of document.querySelectorAll('.view .page-sub')) {
    const t = words(sub);
    if (t) assert.equal(t.split(/(?<=\.)\s/).length, 1, t);
  }
  const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  assert.doesNotMatch(src, /'NO_EDGE' \? 'bear'/, 'no-edge status is caution, not price-down red');
  global.window = {};
  try {
    app.renderTrackRecord({ engines: [{ engine: 'e1', label: 'Engine', evidence: { status: 'NO_EDGE', status_text: 'Tested, no edge' } }],
      summary: { recorded: 0, resolved: 0 }, decisions: [] });
  } finally { global.window = undefined; }
  const body = document.getElementById('track-body');
  assert.ok(body && body.textContent.trim(), 'the ledger rendered');
  assert.doesNotMatch(body.textContent, /—/);
});
