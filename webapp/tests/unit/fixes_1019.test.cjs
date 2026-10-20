/**
 * Bug fixes from the 2026-10-19 review.
 *
 *  - The saved market is read through a guard: blocked storage starts the page on US.
 *  - The capital default follows the market (an INR budget for India).
 *  - A market switch cancels the running scan on the server.
 *  - An empty custom-ticker list is an error under its field, not an alert().
 *  - A retry repeats the request's market and preview mode.
 *  - Only the latest evidence-page read renders.
 *  - Copy keeps a short button label; the CSV link outlives the download start.
 *  - Clearing the data cache takes a second click.
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/fixes_1019.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
let store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');
const T = globalThis.__QUANTA_THRESHOLDS__;

test('the saved market is read through a guard and only US or IN is accepted', () => {
  store = { quanta_market: 'IN' };
  assert.equal(app.savedMarket(), 'IN');
  store = { quanta_market: '<script>' };
  assert.equal(app.savedMarket(), 'US');
  const real = global.localStorage;
  global.localStorage = { getItem: () => { throw new Error('SecurityError'); } };
  try {
    assert.equal(app.savedMarket(), 'US', 'blocked storage still starts the page');
  } finally {
    global.localStorage = real;
  }
  assert.match(appJs, /^ {2}market: savedMarket\(\),$/m);
});

test('the capital default follows the market', () => {
  assert.equal(app.defaultCapital('IN'), T.CAPITAL.DEFAULT_INDIA);
  assert.equal(app.defaultCapital('US'), T.CAPITAL.DEFAULT_US);
  assert.doesNotMatch(appJs, /\|\| thr\('CAPITAL\.DEFAULT_US'\)\)/, 'no fallback reads the US figure for every market');
});

test('a market switch cancels the running scan on the server', () => {
  const clear = appJs.slice(appJs.indexOf('function clearMarketDependentPanels'));
  assert.match(clear.slice(0, 900), /if \(state\.scanJobId\) \{\s*fetch\(`\/api\/jobs\/\$\{encodeURIComponent\(state\.scanJobId\)\}\/cancel`/);
  assert.match(appJs, /state\.scanJobId = payload\.job_id;\s*pollJob\(/);
});

test('an empty custom list is an error under its field, never an alert', () => {
  assert.doesNotMatch(appJs, /\balert\(/);
  const field = document.getElementById('sp-custom-error');
  assert.ok(field, 'the error slot exists');
  assert.equal(field.getAttribute('role'), 'alert');
  assert.equal(document.getElementById('sp-custom-tickers').getAttribute('aria-describedby'), 'sp-custom-error');
});

test('a Research retry repeats the market and preview mode of the request it retries', () => {
  assert.match(appJs, /state\.lastAnalysisOpts = \{ market: options\.market, fastMode: options\.fastMode \};/);
  assert.match(appJs, /runAnalysis\(null, \{ \.\.\.\(state\.lastAnalysisOpts \|\| \{\}\), safeMode: true \}\)/);
  assert.match(appJs, /runAnalysis\(null, act === 'market' \? \{ fastMode: last\.fastMode \} : last\);/);
});

test('only the latest evidence-page read renders', () => {
  const fn = appJs.slice(appJs.indexOf('async function loadTrackRecord'), appJs.indexOf('async function resolveTrackRecord'));
  assert.match(fn, /nextJobToken\('track'\)/);
  assert.equal((fn.match(/if \(current\(\)\) renderTrackRecord/g) || []).length, 2);
});

test('copy keeps a short label, and the CSV link outlives the download start', () => {
  assert.doesNotMatch(appJs, /ev\.target\.textContent = text/);
  assert.match(appJs, /btn\.textContent = 'Copy blocked'/);
  assert.doesNotMatch(appJs, /revokeObjectURL\(a\.href\), 0\)/);
});

test('clearing the data cache takes a second click', () => {
  const fn = appJs.slice(appJs.indexOf('async function clearDataCache'));
  assert.ok(fn.indexOf("'Click again to clear'") > 0);
  assert.ok(fn.indexOf("'Click again to clear'") < fn.indexOf("fetch('/api/cache/clear'"), 'the first click only arms');
});

test('Discover names the configured funding hurdle, not a hard-coded 2:1', () => {
  assert.doesNotMatch(appJs, /below 2:1/);
  assert.match(appJs, /\['Poor reward-to-risk', `below \$\{fmtRR\(Math\.max\(Number\(thr\('RR\.SCANNER_MIN'\)\)/);
});

test('the Evidence page leads its statistics with a plain sentence', () => {
  assert.match(app.trackPlainLine({ status: 'NO_EDGE' }), /higher scores were not followed by better returns/);
  assert.equal(app.trackPlainLine({ status: 'UNTESTED' }), '');
  assert.doesNotMatch(appJs, /'warn' : 'warn'/, 'no ternary with identical branches');
});

test('the theme button carries an action label and no pressed state', () => {
  assert.equal(document.getElementById('theme-toggle').hasAttribute('aria-pressed'), false);
  assert.match(appJs, /themeBtn\.removeAttribute\('aria-pressed'\)/);
  assert.match(appJs, /getPropertyValue\('--bg'\)/, 'the browser chrome colour follows the token');
});

test('the session high and low carry no gain or loss colour', () => {
  const qb = fs.readFileSync(path.join(root, 'static', 'quote-board.css'), 'utf8');
  assert.match(qb, /\.qb-cell\.is-high \.qb-value \{ color: var\(--text-primary\); \}/);
  assert.match(qb, /\.qb-cell\.is-low {2}\.qb-value \{ color: var\(--text-primary\); \}/);
});

test('My Portfolio states the risk scale from one constant', () => {
  const g = fs.readFileSync(path.join(root, 'static', 'mp-guidance.js'), 'utf8');
  assert.doesNotMatch(g, /of 9 points/);
  assert.match(fs.readFileSync(path.join(root, 'static', 'mp-overview.js'), 'utf8'), /X\.RISK_MAX = RISK_MAX;/);
});

// Engine reasons dropped their em dashes (2026-10-19). Saved scans on disk
// still carry the old dashed text, so plainReason must rewrite both forms.
test('plainReason rewrites the Risk Grade D reasons in both the old dashed and the new form', () => {
  const DASH = String.fromCharCode(0x2014);
  const kept = 'Risk grade D (high risk): kept at Accumulate, with a smaller position';
  const lowered = 'Risk grade D (high risk): lowered from Strong Buy to Accumulate, with a smaller position';
  assert.equal(app.plainReason(`Risk Grade D ${DASH} hold at ACCUMULATE (reduced size)`), kept);
  assert.equal(app.plainReason('Risk Grade D: hold at ACCUMULATE (reduced size)'), kept);
  assert.equal(app.plainReason(`Risk Grade D blocks fresh BUY ${DASH} STRONG_BUY → ACCUMULATE (reduced size)`), lowered);
  assert.equal(app.plainReason('Risk Grade D blocks fresh BUY: STRONG_BUY → ACCUMULATE (reduced size)'), lowered);
  // The patterns themselves carry no literal em dash (an escape matches old text).
  assert.doesNotMatch(appJs.slice(appJs.indexOf('const PLAIN_REASON_RULES'), appJs.indexOf('function plainReason')), new RegExp(DASH));
});

test('phone-width tables keep their decision columns, and every call carries the not-advice line', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  for (const cls of ['q-table--analyst', 'q-table--evidence', 'q-table--ledger']) {
    assert.match(appJs, new RegExp(cls), `${cls} is set on its table`);
    assert.ok(css.includes(`.${cls} :is(th, td)`), `${cls} has phone rules`);
  }
  assert.equal((appJs.match(/\$\{NOT_ADVICE_HTML\}/g) || []).length, 2, 'Picks report and Research decision');
  assert.match(appJs, /absolute scale, not ranked against its sector/);
  assert.doesNotMatch(appJs, /upside \$\{analystUp >= 0 \? 'bull' : 'bear'\}/, 'Street upside carries no gain/loss colour');
  assert.match(appJs, /thr\('MARKET\.ANALYST_PICK_MIN_ANALYSTS'\)/);
});

test('no category colour in the allocation donut is green or red', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  for (const hex of ['#1baf7a', '#008300', '#199e70']) assert.ok(!css.includes(hex), hex);
});

test('every stylesheet is well-formed: balanced braces, and no rule head with an open bracket', () => {
  // A mangled selector list (say a comma split inside :is()) makes the browser stop
  // reading the sheet from that rule on, and the page loses its styles after it.
  for (const f of ['quanta.css', 'quote-board.css', 'signature.css']) {
    const css = fs.readFileSync(path.join(root, 'static', f), 'utf8').replace(/\/\*[^*]*\*+(?:[^/*][^*]*\*+)*\//g, '');
    let depth = 0;
    let head = '';
    for (const ch of css) {
      if (ch === '{') {
        assert.equal((head.match(/\(/g) || []).length, (head.match(/\)/g) || []).length, `${f}: unbalanced parentheses in ${head.trim().slice(0, 80)}`);
        assert.equal((head.match(/\[/g) || []).length, (head.match(/\]/g) || []).length, `${f}: unbalanced brackets in ${head.trim().slice(0, 80)}`);
        assert.ok(!/^\s*(?::is|:where|:not)\(\s*[^)]*$/.test(head), `${f}: a functional selector is cut off: ${head.trim().slice(0, 80)}`);
        depth += 1;
        head = '';
      } else if (ch === '}') {
        depth -= 1;
        assert.ok(depth >= 0, `${f}: a closing brace with no rule open`);
        head = '';
      } else if (ch === ';') {
        head = '';
      } else {
        head += ch;
      }
    }
    assert.equal(depth, 0, `${f}: braces balance`);
  }
});

// ---- My Portfolio review (2026-10-19) ----------------------------------------------------
const mp = (f) => fs.readFileSync(path.join(root, 'static', f), 'utf8');

test('My Portfolio: partial risk, honest labels and a chart empty state that helps', () => {
  const ov = mp('mp-overview.js');
  assert.match(ov, /Partial risk assessment: \$\{esc\(\(risk\.unmeasured/);
  assert.match(ov, /<h2 id="mpo-con-h">Unrealized gains by holding<\/h2>/);
  assert.doesNotMatch(ov, /What drove return/);
  assert.match(ov, /Price history could not be loaded/, 'a missing 30-day trend says why');
  const ins = mp('mp-insights.js');
  assert.match(ins, /data-chart-unavailable/);
  assert.match(ins, /No usable prices for:/, 'the excluded holdings are named');
  assert.match(ins, /PERF_RETRY_MS/, 'one automatic retry');
  assert.match(ins, /reload it now/);
});

test('My Portfolio: a grade-F trim reads as a risk review, a concentration trim stays a trim', () => {
  const src = mp('my-portfolio.js');
  assert.match(src, /basis\.code === 'RISK_GRADE_F' \? 'Review risk'/);
  const guidance = fs.readFileSync(path.join(root, '..', 'core', 'portfolio', 'guidance.py'), 'utf8');
  assert.match(guidance, /QUANTA defaults, not a target set/, 'the 10% and 20% levels are named as defaults');
});

test('My Portfolio: Review and Activity state their limits where the reader looks', () => {
  const rv = mp('mp-review.js');
  assert.match(rv, /Unable to assess: price history for this range is not available/);
  assert.match(rv, /data-attrib-total/);
  assert.match(rv, /other \$\{plural\(others\.length/, 'the holdings not listed are summed');
  const act = mp('mp-activity.js');
  assert.match(act, /data-activity-limits/);
  assert.match(act, /Unclassified: \$\{unres\}/);
  assert.match(act, /only the newest \$\{fl\.cap\} of \$\{fl\.read\} fills are loaded/);
  assert.match(act, /'Assets bought'/);
  assert.match(mp('mp-news.js'), /General market item, not about one holding/);
});
