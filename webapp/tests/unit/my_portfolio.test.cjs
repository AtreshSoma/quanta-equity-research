// My Portfolio page: rendering contract against a fixture produced by the real
// build_view (tests/tools/make_my_portfolio_fixture.py). Synthetic holdings.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(root, 'templates', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const src = fs.readFileSync(path.join(root, 'static', 'my-portfolio.js'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'fixtures', 'my_portfolio_view.json'), 'utf8'));
const hp = require('../../static/my-portfolio.js');

const clone = (o) => JSON.parse(JSON.stringify(o));
const frag = (h) => new JSDOM(`<body>${h}</body>`).window.document;
// Visible words in reading order, one space between text nodes (markup puts none).
const words = (el) => {
  const walk = el.ownerDocument.createTreeWalker(el, 4);
  const out = [];
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = n.textContent.replace(/\s+/g, ' ').trim(); if (t) out.push(t); }
  return out.join(' ');
};

test('page is a first-class view with a tab, a route and its own script', () => {
  const doc = new JSDOM(html).window.document;
  const tab = doc.querySelector('.nav-tab[data-view="holdings"]');
  assert.ok(tab, 'nav tab exists');
  // Destination names stay unchanged across responsive breakpoints.
  assert.equal(tab.getAttribute('aria-label'), 'My Portfolio');
  assert.equal(tab.querySelector('.nav-label-long').textContent, 'My Portfolio');
  assert.equal(tab.querySelector('.nav-label-short').textContent, 'My Portfolio');
  assert.equal(doc.querySelector('.nav-tab[data-view="picks"]').textContent, 'Picks');
  assert.equal(tab.getAttribute('aria-controls'), 'view-holdings');
  assert.ok(doc.querySelector('section.view#view-holdings #hp-root'));
  assert.equal(doc.querySelector('#hp-status').getAttribute('aria-live'), 'polite');
  assert.match(appJs, /const VIEWS = \[[^\]]*'holdings'/);
  assert.match(html, /asset_url\('my-portfolio\.js'\)/);
});

test('the browser never talks to Robinhood and the core stores only the hide-amounts preference', () => {
  assert.doesNotMatch(src, /agent\.robinhood\.com|api\.robinhood\.com/);
  assert.doesNotMatch(src, /sessionStorage|indexedDB|document\.cookie/);
  // Browser storage holds one boolean, under one key, never account data.
  const uses = src.match(/localStorage\.\w+\([^)]*\)/g) || [];
  assert.deepEqual(uses, ["localStorage.getItem(HIDE_KEY)", "localStorage.setItem(HIDE_KEY, on ? '1' : '0')"]);
  assert.match(src, /'X-QUANTA-Request'\] = '1'/);
  // The only navigation to Robinhood is the sign-in URL the server returned, checked first.
  assert.match(src, /robinhood\\\.com\\\/\/i\.test\(res\.data\.authorize_url\)/);
});

test('disconnected state explains read-only scope and offers Connect', () => {
  const d = frag(hp.stateCardHtml({ state: 'disconnected' }));
  assert.ok(d.querySelector('[data-hp="connect"]'));
  const text = d.body.textContent;
  assert.match(text, /Read-only/);
  assert.match(text, /cannot place, review or cancel orders/);
  assert.match(text, /server memory only/);
  const head = frag(hp.headActionsHtml({ state: 'disconnected', state_label: 'Disconnected' }));
  assert.match(head.body.textContent, /Trading is off/);
});

test('each failure state is distinct and shows no portfolio', () => {
  const cases = {
    awaiting_auth: /Finish signing in/, auth_expired: /sign-in expired/, provider_error: /could not be read/,
  };
  for (const [st, re] of Object.entries(cases)) {
    const d = frag(hp.stateCardHtml({ state: st, last_error: { message: 'Robinhood MCP returned HTTP 503.' } }));
    assert.match(d.body.textContent, re, st);
    assert.equal(d.querySelector('table'), null, `${st} renders no holdings table`);
  }
  // One Reconnect, on the state card; the header does not repeat it (audit A-41).
  assert.ok(frag(hp.stateCardHtml({ state: 'auth_expired' })).querySelector('[data-hp="connect"]'));
  assert.equal(frag(hp.headActionsHtml({ state: 'auth_expired', state_label: 'Authentication expired' })).querySelector('[data-hp="connect"]'), null);
  assert.equal(frag(hp.headActionsHtml({ state: 'disconnected', state_label: 'Not connected' })).querySelector('[data-hp="connect"]'), null);
});

test('a bullish stock the portfolio is overweight in reads "Hold / No New Money", not Accumulate', () => {
  const d = frag(hp.portfolioHtml(fixture));
  const row = d.querySelector('[data-hp-row="AAPL"]');
  assert.match(row.textContent, /Bullish/);
  assert.match(row.textContent, /Hold, no new money/);
  assert.match(row.textContent, /single-name limit/);
  // A bullish name under its limits still gets no new money while the book's
  // stop-risk coverage is incomplete (BRK.B failed): the gap is stated, not hidden.
  const jnj = d.querySelector('[data-hp-row="JNJ"]').textContent;
  assert.match(jnj, /Hold, no new money/);
  assert.match(jnj, /stop-risk coverage is incomplete/);
  assert.doesNotMatch(jnj, /Accumulate/);
});

test('a holding whose analysis failed shows "No call", never an action', () => {
  const d = frag(hp.portfolioHtml(fixture));
  const row = d.querySelector('[data-hp-row="BRK.B"]');
  assert.match(row.textContent, /No call/);
  assert.doesNotMatch(row.textContent, /Accumulate|Trim|Exit|Hold/);
  assert.match(row.textContent, /No basis/);
  const r = fixture.portfolio.positions.find((p) => p.symbol === 'BRK.B');
  const det = frag(hp.detailHtml(r, fixture.portfolio));
  assert.match(det.body.textContent, /does not turn missing or failed data into Buy, Hold or Sell/);
});

test('position detail puts the portfolio call first and keeps company research one step away', () => {
  const r = fixture.portfolio.positions.find((p) => p.symbol === 'AAPL');
  const d = frag(hp.detailHtml(r, fixture.portfolio));
  const ch = [...d.querySelectorAll('.hp-ch-h h3')].map((e) => e.textContent);
  assert.deepEqual(ch, ['Position', 'Portfolio call', 'Portfolio Impact', 'Risks', 'Evidence', 'Thesis', 'Fundamentals', 'Valuation', 'Technicals', 'Catalysts']);
  const research = d.querySelector('details[data-hp-disclosure="research"]');
  assert.equal(research.open, false, 'company outlook is collapsed');
  assert.deepEqual([...research.querySelectorAll('.hp-ch-h h3')].map((e) => e.textContent), ['Thesis', 'Fundamentals', 'Valuation', 'Technicals', 'Catalysts']);
  const t = d.body.textContent;
  for (const part of ['Model stance', 'New money', 'Existing position', 'What supports it', 'What blocks a stronger action',
    'Principal risk', 'Strongest counterargument', 'What would change it', 'Room before the single-name limit', 'Model track record', 'Analysis run']) {
    assert.ok(t.includes(part), part);
  }
  assert.match(t, /research view, not a validated recommendation/);
  // The call names its action once, in words; no second chip beside the headline.
  assert.equal(d.querySelector('.hp-headline .hp-action'), null);
});

test('sorting by action, weight, P&L, conviction and risk; missing values sort last', () => {
  const rows = fixture.portfolio.positions;
  assert.equal(hp.sortRows(rows, 'action')[0].symbol, 'RIVN');
  const byW = hp.sortRows(rows, 'weight').map((r) => r.broker.weight);
  assert.deepEqual(byW, byW.slice().sort((a, b) => b - a));
  const byP = hp.sortRows(rows, 'pnl');
  assert.equal(byP[byP.length - 1].symbol, 'BRK.B', 'no cost basis sorts last');
  const byC = hp.sortRows(rows, 'conviction');
  assert.equal(byC[0].symbol, 'NVDA');
  assert.equal(byC[byC.length - 1].symbol, 'BRK.B');
  assert.equal(hp.sortRows(rows, 'risk')[0].symbol, hp.sortRows(rows, 'risk', 'desc')[0].symbol);
  const asc = hp.sortRows(rows, 'weight', 'asc').map((r) => r.broker.weight);
  assert.deepEqual(asc, byW.slice().reverse());
});

test('broker strings are escaped', () => {
  const v = clone(fixture);
  v.portfolio.positions[0].broker.name = '<img src=x onerror=alert(1)>';
  v.portfolio.positions[0].guidance.key_reason = '<script>alert(2)</script>';
  const d = frag(hp.portfolioHtml(v));
  assert.equal(d.querySelector('.hp-table img'), null);
  assert.equal(d.querySelector('.hp-table script'), null);
});

test('no holdings renders an explicit empty state, not sample data', () => {
  const v = clone(fixture);
  v.portfolio.positions = [];
  const d = frag(hp.portfolioHtml(v));
  assert.match(d.body.textContent, /No stock or ETF positions/);
  assert.equal(d.querySelector('.hp-table'), null);
});

test('hide amounts masks every account figure, keeps weights, prices and research', () => {
  hp._state.filter = null; hp._state.sort = 'action'; hp._state.dir = null;
  const pf = clone(fixture);
  const first = pf.portfolio.positions[0];
  first.guidance.key_reason = 'Trim 12 shares ($1,234.50) to get back under the limit';
  const secrets = ['$90,576', '$9,650', '$3,540', '$4,950', '-$1,410', '$1,234.50', '12 shares'];
  try {
    const shown = hp.portfolioHtml(pf);
    for (const v of secrets.slice(0, 3)) assert.ok(shown.includes(v), `${v} visible when shown`);
    hp._state.hidden = true;
    const d = frag(hp.portfolioHtml(pf));
    const text = d.body.textContent;
    for (const v of secrets) assert.ok(!text.includes(v), `${v} is hidden`);
    assert.ok(d.querySelectorAll('.hp-mask').length > 10, 'figures replaced by a mask');
    // P&L %, session % and cost per share are masked too; weights and market prices stay.
    const row = d.querySelector(`[data-hp-row="${first.symbol}"]`);
    assert.doesNotMatch(row.textContent, /[+-]\d+\.\d+%/);
    assert.match(row.querySelector('.hp-col-weight').textContent, /^\d+\.\d%/);
    const panel = frag(hp.drawerHtml(first, pf.portfolio));
    assert.match(panel.body.textContent, /Last price\$11\.80/);
    for (const v of secrets) assert.ok(!panel.body.textContent.includes(v), `${v} hidden in the detail panel`);
    // The research is unchanged.
    assert.ok(d.querySelector('.hp-action'));
  } finally {
    hp._state.hidden = false;
  }
});

test('hide amounts is a quiet icon toggle in the connected header, with its state exposed', () => {
  hp._state.hidden = false;
  let d = frag(hp.headActionsHtml({ state: 'connected', state_label: 'Connected' }));
  const btn = d.querySelector('button[data-hp="privacy"]');
  assert.ok(btn && btn.classList.contains('icon-btn'));
  assert.equal(btn.getAttribute('aria-pressed'), 'false');
  assert.equal(btn.getAttribute('aria-label'), 'Hide amounts');
  assert.ok(btn.querySelector('svg[aria-hidden="true"]'));
  hp.setHidden(true);
  try {
    d = frag(hp.headActionsHtml({ state: 'connected', state_label: 'Connected' }));
    assert.equal(d.querySelector('[data-hp="privacy"]').getAttribute('aria-pressed'), 'true');
    assert.equal(d.querySelector('[data-hp="privacy"] .hp-tip').textContent, 'Show amounts');
  } finally {
    hp.setHidden(false);
  }
  // Nothing to hide before a portfolio can be read.
  assert.equal(frag(hp.headActionsHtml({ state: 'disconnected' })).querySelector('[data-hp="privacy"]'), null);
});

test('a sign-in left unfinished can be restarted without cancelling first', () => {
  const d = frag(hp.stateCardHtml({ state: 'awaiting_auth' }));
  assert.ok(d.querySelector('[data-hp="connect"]'));
});

test('no reason cites a score band the score is not in', () => {
  for (const r of fixture.portfolio.positions) {
    const g = r.guidance;
    if (/reduce band/.test(g.key_reason)) assert.ok(g.conviction >= 35 && g.conviction < 45, r.symbol);
    if (/exit|below 35/i.test(g.key_reason)) assert.ok(g.conviction < 35, r.symbol);
    assert.equal(g.key_reason, g.basis.text, r.symbol);
  }
});

test('header is one row of icon controls, each with a name, a tooltip and no visible status text', () => {
  const previous = { accounts: hp._state.accounts, busy: hp._state.busy };
  hp._state.accounts = [{ handle: 'a', label: 'Individual' }, { handle: 'b', label: 'IRA' }];
  hp._state.busy = false;
  try {
    const d = frag(hp.headActionsHtml({ state: 'connected', state_label: 'Connected (read-only)' }));
    const conn = d.querySelector('.hp-conn');
    assert.equal(conn.getAttribute('tabindex'), '0');
    const tip = d.getElementById(conn.getAttribute('aria-describedby'));
    assert.equal(tip.getAttribute('role'), 'tooltip');
    assert.match(tip.textContent, /^Connected · Read-only access/);
    assert.equal(conn.firstChild.nextSibling.textContent, 'Robinhood');
    assert.ok(d.querySelector('#hp-account'));
    for (const [act, name] of [['privacy', 'Hide amounts'], ['refresh', 'Refresh'], ['disconnect', 'Disconnect Robinhood']]) {
      const b = d.querySelector(`button[data-hp="${act}"]`);
      assert.equal(b.getAttribute('aria-label'), name, act);
      assert.ok(b.querySelector('svg[aria-hidden="true"]'), `${act} icon`);
      assert.ok(b.querySelector('.hp-tip[aria-hidden="true"]').textContent, `${act} tooltip`);
      assert.equal([...b.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).length, 0, `${act} is icon-only`);
    }
    assert.equal(d.querySelector('[data-hp="disconnect"]').closest('[role="menu"]'), null, 'Disconnect acts directly');
    assert.equal(d.querySelector('.q-btn'), null, 'no text buttons in the connected header');
  } finally {
    Object.assign(hp._state, previous);
  }
  assert.match(css, /\.hp-head-actions \.is-tip-off \.hp-tip \{ opacity: 0; visibility: hidden; \}/, 'Esc can dismiss tooltips');
  assert.match(css, /@media \(pointer: coarse\) \{\r?\n  \.hp-icon-btn \{ width: 44px; height: 44px; \}/);
  assert.match(src, /e\.key !== 'Escape'/);
});

test('each holding opens its detail panel from a native button; the table keeps value and the session move', () => {
  hp._state.filter = null; hp._state.sort = 'action'; hp._state.dir = null;
  const d = frag(hp.tableHtml(fixture.portfolio));
  const row = d.querySelector('[data-hp-row="RIVN"]');
  assert.equal(row.getAttribute('role'), null, 'table row keeps its table semantics');
  const trigger = row.querySelector('button[data-hp-toggle]');
  assert.equal(trigger.getAttribute('aria-haspopup'), 'dialog');
  assert.equal(trigger.getAttribute('aria-label'), 'RIVN details');
  assert.equal(d.querySelector('.hp-detail'), null, 'no inline expansion rows');
  assert.match(words(row.querySelector('.hp-col-weight')), /^3\.9% \$3,540$/);
  assert.match(words(row.querySelector('.hp-col-today')), /^-\$90 -2\.48%$/);
  assert.match(row.querySelector('.hp-col-basis').textContent, /^\$4,950.*\$16\.50 \/ share/);
  assert.match(row.querySelector('.hp-col-ev').textContent, /29\/100 score.*Model not yet validated/);
  const heads = [...d.querySelectorAll('thead th')].map((e) => (e.querySelector('.q-sortable') || e).textContent.replace(/[▲▼]/g, '').trim());
  assert.deepEqual(heads, ['Ticker', 'Weight', 'Last session', 'Cost basis', 'P&L', 'QUANTA stance', 'Portfolio action', 'Evidence', 'Key reason']);
  // Filters live with the table they filter.
  assert.deepEqual([...d.querySelectorAll('.hp-toolbar [data-hp-filter]')].map((b) => b.dataset.hpFilter), ['', ...fixture.portfolio.groups.filter((g) => g.count).map((g) => g.key)]);
});

test('the detail panel walks the table order and names its place in it', () => {
  hp._state.filter = 'do_not_add'; hp._state.sort = 'weight'; hp._state.dir = null;
  try {
    const pf = fixture.portfolio;
    const msft = pf.positions.find((p) => p.symbol === 'MSFT');
    const d = frag(hp.drawerHtml(msft, pf));
    assert.match(words(d.querySelector('.hp-drawer-s')), /Hold, no new money Model not yet validated 9\.5% of the account · 2 of 3 in Holdings/);
    assert.equal(d.querySelector('[data-hp-drawer="prev"]').getAttribute('aria-label'), 'Previous: AAPL');
    assert.equal(d.querySelector('[data-hp-drawer="next"]').getAttribute('aria-label'), 'Next: JNJ');
    assert.equal(d.querySelector('[data-hp-drawer="close"]').getAttribute('aria-label'), 'Close details');
    assert.equal(d.querySelector('#hp-drawer-title').getAttribute('tabindex'), '-1');
    const first = frag(hp.drawerHtml(pf.positions.find((p) => p.symbol === 'AAPL'), pf));
    assert.ok(first.querySelector('[data-hp-drawer="prev"]').disabled, 'no previous at the top');
  } finally {
    hp._state.filter = null; hp._state.sort = 'action';
  }
});

test('ticker and total-cost sorting preserve missing-last behavior', () => {
  const rows = fixture.portfolio.positions;
  assert.deepEqual(hp.sortRows(rows, 'ticker').map((r) => r.symbol), rows.map((r) => r.symbol).sort());
  const byBasis = hp.sortRows(rows, 'basis');
  assert.equal(byBasis[0].symbol, 'NVDA');
  assert.equal(byBasis.at(-1).symbol, 'BRK.B');
  assert.equal(hp.sortRows(rows, 'basis', 'asc').at(-1).symbol, 'BRK.B');
});

test('account selection and refresh are locked together while reading a portfolio', () => {
  const previous = { accounts: hp._state.accounts, busy: hp._state.busy };
  hp._state.accounts = [{ handle: 'first', label: 'Individual' }, { handle: 'second', label: 'IRA' }];
  hp._state.busy = true;
  try {
    const d = frag(hp.headActionsHtml({ state: 'connected' }));
    assert.ok(d.querySelector('#hp-account').disabled);
    assert.ok(d.querySelector('[data-hp="refresh"]').disabled);
    assert.ok(d.querySelector('[data-hp="disconnect"]').disabled);
  } finally {
    Object.assign(hp._state, previous);
  }
});

test('a tiny percentage cannot suppress a material cash mismatch, or a large relative mismatch', () => {
  for (const [difference, reported] of [[400, 1e9], [0.5, 100], [null, 100]]) {
    const pf = clone(fixture.portfolio);
    pf.issues = [];
    pf.reconciliation = { status: 'mismatch', difference, reported };
    const d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
    assert.match(d.querySelector('.hp-recon-note').textContent, /differ from Robinhood’s total/);
    assert.doesNotMatch(d.querySelector('.hp-recon-note').textContent, /\$0\.00/);
  }
});

test('without the insight modules, every tab still renders its core sections with source tags', () => {
  hp._state.filter = null; hp._state.sort = 'action'; hp._state.dir = null; hp._state.expanded.clear();
  const d = frag(hp.portfolioHtml(fixture));
  const titles = (id) => [...d.querySelectorAll(`#hp-panel-${id} .q-head-title`)].map((e) => words(e));
  assert.deepEqual(titles('overview'), ['Portfolio Overview', 'Priorities']);
  // Decisions have their own tab; Holdings is the table.
  assert.deepEqual(titles('holdings'), ['Holdings 8']);
  assert.deepEqual(titles('guidance'), ['Guidance']); // sentence case, no page-name repeat (styling pass)
  assert.deepEqual(titles('review'), ['Contribution', 'Risk exposure']);
  assert.equal(d.getElementById('hp-panel-plan'), null, 'no Plan tab');
  const tags = new Set([...d.querySelectorAll('.hp-src')].map((e) => e.textContent));
  for (const t of ['Account data', 'QUANTA analysis']) assert.ok(tags.has(t), t);
  assert.doesNotMatch(d.body.textContent, /Broker data/, 'no user-facing broker wording');
  assert.doesNotMatch(d.body.textContent, /\bQuotes\b/, 'no standalone Quotes field');
  assert.match(d.querySelector('.hp-issue-list').textContent, /Positions plus cash match Robinhood/);
  const heads = [...d.querySelectorAll('.hp-table thead th')].map((e) => (e.querySelector('.q-sortable') || e).textContent.replace(/[▲▼]/g, '').trim());
  assert.deepEqual(heads.slice(0, 9), ['Ticker', 'Weight', 'Last session', 'Cost basis', 'P&L', 'QUANTA stance', 'Portfolio action', 'Evidence', 'Key reason']);
});
test('the KPI row: value with the session move under it, return on cost, realized, cash and QUANTA risk', () => {
  const d = frag(hp.overviewHtml(fixture.portfolio));
  const cells = [...d.querySelectorAll('.hp-kpis .hp-kpi')];
  assert.deepEqual(cells.map((c) => c.querySelector('dt').textContent), ['Total value', 'Return on cost', 'Realized P&L', 'Cash', 'Risk']);
  assert.ok(cells[0].classList.contains('hp-kpi--lead'));
  // Quotes are from the last close, so the move reads "last session", not "today".
  assert.match(cells[0].querySelector('.hp-today').textContent, /^\+\$330\.60 \+0\.37% last session$/);
  assert.match(cells[0].textContent, /\$90,576\.00.*8 holdings · cash \$9,650/s);
  assert.match(cells[1].textContent, /\+52\.3%\s*\+\$25,874 unrealized · no cost basis for BRK\.B/);
  // Realized and unrealized stay separate; the broker's window is said in words.
  assert.match(cells[2].textContent, /\+\$2,314\.55\s*Year to date, closed positions/);
  assert.match(cells[3].textContent, /10\.7%\s*\$9,650\.00 · buying power \$9,650/);
  assert.match(cells[4].textContent, /High\s*4 drivers: Largest position is 35%/);
  assert.equal(d.querySelectorAll('.hp-dq').length, 1, 'one data-status line');
  const live = clone(fixture.portfolio);
  live.positions.forEach((p) => { p.broker.quote_freshness = { status: 'live' }; });
  assert.match(frag(hp.overviewHtml(live)).querySelector('.hp-today').textContent, /today$/);
  const down = clone(fixture.portfolio);
  down.overview.day_change = -120; down.overview.day_change_pct = -0.13;
  const t = frag(hp.overviewHtml(down)).querySelector('.hp-today');
  assert.ok(t.classList.contains('bear') && t.querySelector('path').getAttribute('d').startsWith('M5 8.5'), 'a down arrow for a loss');
  const none = clone(fixture.portfolio);
  none.overview.realized_pnl = null; none.overview.realized_pnl_period = '3month';
  assert.match(frag(hp.overviewHtml(none)).querySelectorAll('.hp-kpi')[2].textContent, /--\s*Not reported by Robinhood/);
  none.overview.realized_pnl = 5;
  assert.match(frag(hp.overviewHtml(none)).querySelectorAll('.hp-kpi')[2].textContent, /Past 3 months, closed positions/);
});
test('partial data and reconciliation mismatch are visible', () => {
  const v = clone(fixture);
  v.portfolio.data_state = 'partial';
  v.portfolio.reconciliation = { status: 'mismatch', difference: -412.5 };
  v.portfolio.issues.push({ code: 'QUOTE_STALE', message: 'KO: Robinhood quote is from 2026-09-24.', scope: 'KO', severity: 'error' });
  const d = frag(hp.portfolioHtml(v));
  const dq = d.querySelector('.hp-dq');
  assert.ok(dq.classList.contains('is-warn'));
  // The quote limitation is the lead, stated once; tickers wait in the details.
  assert.match(dq.querySelector('.hp-dq-lead').textContent, /^1 of 8 quotes are delayed or unverified, so QUANTA gives that holding no call until prices refresh\.$/);
  assert.match(dq.querySelector('.hp-dq-msg').textContent, /\d more notes?/);
  assert.equal((d.body.textContent.match(/delayed or unverified/g) || []).length, 1);
  assert.equal(d.querySelector('details.hp-issues').open, false, 'technical record stays collapsed');
  assert.match(d.querySelector('.hp-issue-list').textContent, /Affected KO/);
  assert.match(d.querySelector('.hp-recon').textContent, /differ from Robinhood’s total by \$412\.50/);
  assert.ok(d.querySelector('.hp-recon').classList.contains('is-warn'));
});

test('market closure is not staleness: a last-session close reads as current', () => {
  const d = frag(hp.overviewHtml(fixture.portfolio));
  assert.match(d.querySelector('.hp-dq-prices').textContent, /Prices as of .*latest close; market closed/);
  assert.equal(d.querySelector('.hp-dq.is-warn'), null);
  const pf = clone(fixture.portfolio);
  pf.positions[0].broker.quote_freshness = { status: 'stale', text: 'From 2026-09-20' };
  const t = frag(hp.overviewHtml(pf));
  assert.ok(t.querySelector('.hp-dq.is-warn'));
  assert.match(t.querySelector('.hp-dq-lead').textContent, /1 of 8 quotes are delayed or unverified/);
  assert.match(t.querySelector('.hp-issue-list').textContent, /Affected RIVN \(from 2026-09-20\)/);
  assert.doesNotMatch(t.querySelector('.hp-dq-prices').textContent, /market closed/, 'a mixed-age book is not labelled as a clean close');
});

test('guidance tones avoid alarm red and the stylesheet stays within its rules', () => {
  const section = css.slice(css.indexOf('MY PORTFOLIO (#holdings)'), css.indexOf('HOW QUANTA WORKS (#guide)'));
  assert.ok(section.length > 1000, 'section sits before the guide slice');
  // Red is P&L direction only: table/detail P&L, the KPI band, the inspector's
  // session move and the performance-driver bars.
  const pnlOnly = section
    .replace(/\.hp-(table|dl) \.bear[^}]*}/g, '').replace(/\.hp-kpi-v\.bear, \.hp-ov-sec \.bear[^}]*}/g, '')
    .replace(/\.hp-insp-dl \.bear[^}]*}/g, '').replace(/\.hp-mv-bar i\.is-down[^}]*}/g, '').replace(/\.hp-mv-v\.bear, \.hp-mv-p\.bear[^}]*}/g, '')
    .replace(/\.hp-mm-row \.bear[^}]*}/g, '')
    // Workspace: P&L cells, attribution bars and the drawdown area (a loss) only.
    .replace(/\.mp-table \.num\.bull[^}]*} \.mp-table \.num\.bear[^}]*}/g, '').replace(/\.mp-statline dd\.bull[^}]*} \.mp-statline dd\.bear[^}]*}/g, '')
    .replace(/\.mpa-row \.hp-num\.bull[^}]*} \.mpa-row \.hp-num\.bear[^}]*}/g, '').replace(/\.mpa-bar i\.is-down[^}]*}/g, '')
    .replace(/\.mpc-ddarea[^}]*}/g, '').replace(/\.mpc\.is-bear[^}]*}/g, '').replace(/\.hp-today\.bull b \{[^}]*} \.hp-today\.bear b[^}]*}/g, '').replace(/\.hp-today\.bull \.hp-tri[^}]*} \.hp-today\.bear \.hp-tri[^}]*}/g, '')
    .replace(/\.mp-sess-bar i\.is-down[^}]*}/g, '').replace(/\.mpo-n\.bull[^}]*} \.mpo-n\.bear[^}]*}/g, '').replace(/\.mpo-div i\.is-up[^}]*} \.mpo-div i\.is-down[^}]*}/g, '')
    .replace(/\.mps-spark\.is-bull polyline[^}]*} \.mps-spark\.is-bear polyline[^}]*}/g, '').replace(/\.mpa2-col\.is-down \.mpa2-bar[^}]*}/g, '').replace(/\.mpa2-read b\.bull[^}]*} \.mpa2-read b\.bear[^}]*}/g, '').replace(/\.mp-hero dd\.bull[^}]*} \.mp-hero dd\.bear[^}]*}/g, '')
    // Overview redesign: where the portfolio lags the benchmark, losing buckets and holdings, and
    // tiles and bars of a loss. The one non-P&L red is the risk gauge's severe band (High).
    .replace(/--mp-lag: [^;]*;/g, '').replace(/--mpt-down: [^;]*;/g, '').replace(/--risk-high: var\(--bear\);/g, '')
    .replace(/\.mpb i\.is-down::before[^}]*}/g, '').replace(/\.mpo-div i\.is-up[^}]*} \.mpo-div i\.is-down[^}]*}/g, '')
    .replace(/\.mpo-cbar i\.is-down[^}]*}/g, '').replace(/\.mpd-tip td\.bull[^}]*} \.mpd-tip td\.bear[^}]*}/g, '')
    .replace(/\.mpo-pin-t \.bull[^}]*} \.mpo-pin-t \.bear[^}]*}/g, '');
  assert.doesNotMatch(pnlOnly, /--bear/, 'only P&L direction uses red');
  assert.doesNotMatch(section, /backdrop-filter|violet|purple|!important|linear-gradient|radial-gradient/);
});

// The reason a decision is shown with: the hero's line, or the title of the card it sits in.
const reasonOf = (d, sym) => {
  const item = [...d.querySelectorAll('.hp-gi')].find((el) => el.dataset.hpDisclosure === `gi-${sym}`);
  if (!item) return null;
  const hero = item.closest('.hp-hero');
  return hero ? hero.querySelector('.hp-hero-why').textContent : item.closest('.hp-gc').querySelector('.hp-gc-t').textContent;
};

test('guidance shows only decisions: the largest as the hero, the rest by severity, each reason once', () => {
  const before = clone(fixture);
  hp._state.gFilter = null;
  const d = frag(hp.guidanceHtml(fixture.portfolio));
  const items = [...d.querySelectorAll('.hp-gi')].map((el) => el.dataset.hpDisclosure);
  // The hero is the decision whose sized scenario moves the most money; the rest follow severity.
  assert.deepEqual(items, ['gi-NVDA', 'gi-RIVN', 'gi-XOM']);
  assert.match(d.querySelector('.q-head-sub').textContent, /^3 need a decision · Not yet tested, all 7 calls · Analysis /);
  const hero = d.querySelector('.hp-hero');
  assert.match(words(hero.querySelector('.hp-hero-h')), /^NVDA Trim Model not yet validated Position is 35\.0% of the portfolio, past the 20% review level$/);
  assert.match(words(hero.querySelector('.hp-hero-bars')), /Now 35\.0% After trim 10\.0% 10% limit 20% review/);
  assert.match(words(hero.querySelector('.hp-hero-cash')), /^Cash freed \$22,676 at today’s prices, not an order$/);
  assert.match(words(hero.querySelector('.hp-hero-fx')), /Largest position NVDA 35\.0% → to AAPL 15\.1% Technology 59\.7% → to 34\.7% Cash 10\.7% → to 35\.7%/);
  assert.match(hero.querySelector('.hp-hero-next').textContent, /^Position size · Consider trimming/);
  // The other decisions: a card per reason, the reason as its title and not repeated in the row.
  const cards = [...d.querySelectorAll('.hp-gc')];
  assert.deepEqual(cards.map((c) => c.querySelector('.hp-gc-t').textContent), [
    'QUANTA score 29 is below 35, with a measured red flag: 12-month valuation target is at or below the entry',
    'QUANTA score 41 is in the reduce band (35 to 45)']);
  for (const c of cards) assert.doesNotMatch(c.querySelector('.hp-gi-row').textContent, /QUANTA score/);
  for (const hold of ['AAPL', 'MSFT', 'JNJ', 'KO', 'BRK.B']) assert.ok(!items.includes(`gi-${hold}`), `${hold} is not a decision row`);
  // Every group is a filter chip, most severe first.
  assert.deepEqual([...d.querySelectorAll('[data-hp-gfilter]')].map((b) => b.dataset.hpGfilter), ['', 'exit_review', 'trim_review', 'do_not_add', 'hold', 'unavailable']);
  assert.deepEqual(fixture, before, 'presentation does not mutate portfolio outputs');
});

test('decisions that share a reason become one card: the reason and next step once, a row per holding', () => {
  const pf = clone(fixture.portfolio);
  const F = { code: 'RISK_GRADE_F', kind: 'risk', text: 'Risk grade F triggers a trim review at any score' };
  for (const s of ['AAPL', 'MSFT']) {
    Object.assign(pf.positions.find((p) => p.symbol === s).guidance, { action: 'Trim', group: 'trim_review', priority: 1, basis: F, key_reason: F.text, next_step: 'Check the position size against how large a swing you can accept.' });
  }
  const d = frag(hp.guidanceHtml(pf));
  const card = [...d.querySelectorAll('.hp-gc')].find((c) => c.querySelector('.hp-gc-t').textContent === F.text);
  assert.deepEqual([...card.querySelectorAll('.hp-gi')].map((el) => el.dataset.hpDisclosure), ['gi-AAPL', 'gi-MSFT']);
  assert.equal(words(card.querySelector('.hp-gc-n')), '2 holdings');
  assert.equal(words(card.querySelector('.hp-gc-s')), 'Risk · Check the position size against how large a swing you can accept.');
  assert.equal((words(d.body).match(/Check the position size against/g) || []).length, 1, 'the shared next step is written once');
  assert.equal((words(card).match(/Risk grade F triggers/g) || []).length, 1, 'the shared reason is written once');
  assert.ok(card.querySelector('.hp-wbar em'), 'each row reads against the single-name limit');
});

test('new money on hold is grouped by reason; a shared data limitation is counted, not repeated', () => {
  const d = frag(hp.guidanceHtml(fixture.portfolio));
  const t = d.querySelector('.hp-holdt');
  assert.match(words(t.querySelector('.hp-gc-h')), /^New money on hold 3 holdings$/);
  assert.deepEqual([...t.querySelectorAll('tr')].map(words), ['At the 10% single-name limit AAPL', 'Sector at the 30% limit MSFT']);
  // The full reason travels with the ticker (tooltip and name), from the server's text.
  assert.equal(t.querySelector('[data-hp-inspect="AAPL"]').getAttribute('title'), 'Already 15.1% of the portfolio, at or over the 10% single-name limit');
  // JNJ waits on a shared data limitation, which is stated once in the data status.
  assert.doesNotMatch(t.textContent, /JNJ|stop-risk coverage/);
  assert.match(words(t.querySelector('.hp-fine')), /^1 more waits on the account data noted in the data status\.$/);
  // Only the scenarios QUANTA sized carry a size: XOM's trim has none.
  assert.doesNotMatch(d.querySelector('.hp-hero').textContent, /XOM/);
  hp._state.gFilter = 'do_not_add';
  try {
    const full = frag(hp.guidanceHtml(fixture.portfolio));
    assert.equal(full.querySelector('.hp-hero'), null);
    assert.match(words(full.querySelector('.hp-hold-full')), /AAPL Already 15\.1% of the portfolio/);
  } finally { hp._state.gFilter = null; }
});

test('Guidance lists every decision; Overview shows the first three and points to the rest', () => {
  const pf = clone(fixture.portfolio);
  for (const s of ['AAPL', 'MSFT']) Object.assign(pf.positions.find((p) => p.symbol === s).guidance, { action: 'Trim', group: 'trim_review', priority: 1 });
  const g = frag(hp.guidanceHtml(pf));
  assert.equal(g.querySelectorAll('.hp-gi').length, 5);
  const pri = frag(hp.prioritiesHtml(pf));
  assert.equal(pri.querySelectorAll('.hp-pri').length, 3);
  assert.match(words(pri.body), /2 more in Guidance/);
  assert.equal(pri.querySelector('[data-hp-tab-go="guidance"]').textContent, 'Guidance');
});
test('Overview priorities and Guidance state the same action and reason', () => {
  const pf = fixture.portfolio;
  const brief = frag(hp.prioritiesHtml(pf));
  const g = frag(hp.guidanceHtml(pf));
  const pri = [...brief.querySelectorAll('.hp-pri')];
  assert.deepEqual(pri.map((li) => li.querySelector('.hp-sym').textContent), ['RIVN', 'NVDA', 'XOM']);
  for (const li of pri) {
    const sym = li.querySelector('.hp-sym').textContent;
    const item = [...g.querySelectorAll('.hp-gi')].find((el) => el.dataset.hpDisclosure === `gi-${sym}`);
    const shown = item.closest('.hp-hero') || item;
    assert.equal(li.querySelector('.hp-action').textContent, shown.querySelector('.hp-action').textContent, sym);
    assert.equal(li.querySelector('.hp-gi-reason').textContent, reasonOf(g, sym), sym);
    assert.ok(li.querySelector(`[data-hp-review="${sym}"]`), `${sym} links to its Guidance entry`);
    assert.ok(li.querySelector(`[data-hp-inspect="${sym}"]`), `${sym} opens its research`);
  }
});

test('the assessment restates published figures and adds no judgement of its own', () => {
  hp._state.market = null;
  const d = frag(hp.assessmentHtml(fixture.portfolio));
  const rows = [...d.querySelectorAll('.hp-assess > div')].map((x) => [x.querySelector('dt').textContent, words(x.querySelector('dd'))]);
  assert.deepEqual(rows.map((r) => r[0]), ['Concentration', 'Risk', 'Last session']);
  assert.equal(rows[0][1], 'Concentrated: NVDA is 35.0% of the account, and 1 more holding is over the 10% single-name reference; Technology is 59.7% (reference 30%).');
  assert.equal(rows[1][1], 'QUANTA rates portfolio risk High : account beta 1.16 against the market’s 1.00; hitting every model stop would cost 5.8% of the account (budget 6%).');
  assert.equal(rows[2][1], 'Up 0.37%; NVDA moved the account most (+0.44 pts).');
  const calm = clone(fixture.portfolio);
  calm.policy.max_single_position = 0.5; calm.policy.max_sector = 0.9;
  assert.match(words(frag(hp.assessmentHtml(calm)).body), /Concentrated: 4\.6 effective positions across 8 holdings; nothing is over QUANTA’s references\./);
});

test('six tabs, Guidance with its decision count, one selected, each controlling a labelled panel', () => {
  hp._state.tab = 'overview';
  const d = frag(hp.portfolioHtml(fixture));
  const tabs = [...d.querySelectorAll('[role="tablist"] [role="tab"]')];
  assert.deepEqual(tabs.map(words), ['Overview', 'Holdings 8', 'Guidance 3', 'Activity', 'Review', 'News & events']);
  assert.deepEqual(tabs.map((t) => t.getAttribute('aria-selected')), ['true', 'false', 'false', 'false', 'false', 'false']);
  assert.deepEqual(tabs.map((t) => t.getAttribute('tabindex')), ['0', '-1', '-1', '-1', '-1', '-1']);
  for (const t of tabs) {
    const p = d.getElementById(t.getAttribute('aria-controls'));
    assert.equal(p.getAttribute('role'), 'tabpanel');
    assert.equal(p.getAttribute('aria-labelledby'), t.id);
    assert.equal(p.hidden, t.getAttribute('aria-selected') !== 'true');
  }
  try {
    hp._state.tab = 'activity';
    const p = frag(hp.portfolioHtml(fixture));
    assert.equal(p.getElementById('hp-panel-activity').hidden, false);
    assert.equal(p.getElementById('hp-panel-overview').hidden, true);
  } finally {
    hp._state.tab = 'overview';
  }
});
test('with nothing to decide, guidance collapses to one status row', () => {
  const pf = clone(fixture.portfolio);
  pf.positions.forEach((p) => { if (p.guidance.status === 'ok') Object.assign(p.guidance, { action: 'Hold', group: 'hold', priority: 4 }); });
  pf.groups = [{ key: 'hold', title: 'Hold', count: 7, symbols: [] }, { key: 'unavailable', title: 'Analysis Unavailable', count: 1, symbols: [] }];
  let d = frag(hp.guidanceHtml(pf));
  assert.ok(d.querySelector('.hp-guidance.is-quiet .hp-gd-bar'));
  assert.equal(d.querySelectorAll('.hp-gi').length, 0);
  // With nothing to decide, the body holds only the research rating, never empty state cards.
  const qb = d.querySelector('.q-body');
  assert.ok(!qb || [...qb.children].every((c) => c.classList.contains('hp-rate')), 'no empty panel body');
  assert.match(d.querySelector('.hp-gd-msg').textContent, /^No position changes indicated\. No portfolio changes require attention among the current calls\./);
  assert.deepEqual([...d.querySelectorAll('[data-hp-gfilter]')].map(words), ['All 8', 'No change 7', 'No call 1']);
  // Every call paused: say so once, name no tickers, offer no one-option filter.
  pf.positions.forEach((p) => { p.guidance.status = 'unavailable'; p.guidance.action = 'Unavailable'; p.guidance.group = 'unavailable'; });
  pf.groups = [{ key: 'unavailable', title: 'Analysis Unavailable', count: 8, symbols: [] }];
  d = frag(hp.guidanceHtml(pf));
  assert.match(d.querySelector('.hp-gd-msg').textContent, /^Analysis paused\. Current research is unavailable; refresh data to retry\./);
  assert.ok(d.querySelector('[data-hp="refresh"]'));
  assert.equal(d.querySelector('[data-hp-disclosure="guidance-state"]').open, false);
  for (const r of pf.positions) assert.doesNotMatch(d.body.textContent, new RegExp(`\\b${r.symbol.replace('.', '\\.')}\\b`));
  assert.equal(d.querySelectorAll('[data-hp-gfilter]').length, 0);
});

test('a limitation is stated once, in the data status, and nowhere else above Holdings', () => {
  const d = frag(hp.portfolioHtml(fixture));
  const above = ['.hp-overview', '.hp-exposure', '.hp-guidance', '.hp-priorities'].map((s) => d.querySelector(s).textContent).join(' ');
  assert.equal((above.match(/stop-risk coverage is incomplete/gi) || []).length, 1);
  assert.equal((above.match(/No analysis for BRK\.B/g) || []).length, 1);
  assert.match(d.querySelector('.hp-dq-lead').textContent, /No analysis for BRK\.B, so no call\. A missing analysis is not a view on the stock\./);
  assert.match(d.querySelector('.hp-dq-msg').textContent, /1 more note/);
  assert.equal(d.querySelector('.hp-limits'), null);
});

test('an expanded recommendation separates outlook from action and states every part', () => {
  const d = frag(hp.guidanceHtml(fixture.portfolio));
  const nvda = [...d.querySelectorAll('.hp-gi')].find((el) => el.dataset.hpDisclosure === 'gi-NVDA');
  const t = nvda.querySelector('.hp-gi-body').textContent;
  for (const part of ['Investment outlook', 'Portfolio action', 'Weight now', 'Conditional target', 'Horizon',
    'Evidence confidence', 'Facts', 'Principal risk', 'Strongest counterargument', 'Review when', 'Concentration and funding']) {
    assert.ok(t.includes(part), part);
  }
  assert.match(t, /Bullish\s*score 77\/100/);
  assert.match(t, /About 10\.0% if trimmed to the 10% single-name limit.*not a personal target/s);
  assert.match(t, /QUANTA analysis Sep \d+/);
  const xom = [...d.querySelectorAll('.hp-gi')].find((el) => el.dataset.hpDisclosure === 'gi-XOM');
  assert.match(xom.textContent, /No target size: QUANTA does not size reductions/);
  assert.ok(nvda.querySelector('[data-hp-inspect="NVDA"]'));
});

test('Guidance and Holdings show the same action and reason for every decision', () => {
  const d = frag(hp.portfolioHtml(fixture));
  const items = [...d.querySelectorAll('.hp-gi')];
  assert.ok(items.length);
  for (const item of items) {
    const symbol = item.dataset.hpDisclosure.slice(3);
    const r = fixture.portfolio.positions.find((p) => p.symbol === symbol);
    const row = d.querySelector(`[data-hp-row="${symbol}"]`);
    const shown = item.closest('.hp-hero') || item;
    assert.equal(shown.querySelector('.hp-action').textContent, row.querySelector('.hp-action').textContent, symbol);
    assert.equal(row.querySelector('.hp-col-reason').textContent, r.guidance.key_reason, symbol);
    assert.equal(reasonOf(d, symbol), r.guidance.key_reason, symbol);
  }
});

test('the one header timestamp is "Updated"; analysis time sits with Guidance', () => {
  const doc = new JSDOM(html).window.document;
  assert.ok(doc.querySelector('#view-holdings .page-head-l #hp-updated'));
  assert.match(frag(hp.updatedHtml(fixture.portfolio)).body.textContent, /^Updated [A-Z][a-z]{2} \d+, \d+:\d{2} [AP]M$/);
  assert.match(frag(hp.guidanceHtml(fixture.portfolio)).querySelector('.q-head-sub').textContent, /Analysis [A-Z][a-z]{2} \d+/);
  assert.doesNotMatch(hp.portfolioHtml(fixture), /hp-fresh/);
});

test('missing risk data remains unavailable and never becomes a measured zero', () => {
  const pf = clone(fixture.portfolio);
  pf.metrics = {};
  pf.overview.risk = {};
  const d = frag(hp.exposureHtml(pf));
  assert.match(d.body.textContent, /Risk drivers not reported/s);
  assert.equal(d.querySelectorAll('.hp-measures .q-na').length, 3);
  assert.equal(d.querySelectorAll('.hp-gauge').length, 0, 'no reference gauge without a measurement');
  assert.doesNotMatch(d.body.textContent, /0\.0%|\$0/);
});

test('risk rows carry a reading, real references only, and the holdings that carry each risk', () => {
  const d = frag(hp.exposureHtml(fixture.portfolio));
  const m = [...d.querySelectorAll('.hp-rk')];
  assert.deepEqual(m.map((x) => x.querySelector('dt').textContent), ['Market sensitivity', 'Loss at model stops', 'Annualized volatility']);
  // Account beta (cash at zero) is the headline; the invested-only beta is stated beside it.
  assert.equal(m[0].querySelector('.hp-rk-v').textContent, '1.16');
  assert.match(m[0].textContent, /16% more sensitive than the market: ≈11\.6% per 10% market move\. Holdings alone 1\.30/);
  assert.match(m[0].querySelector('.hp-stack-ref').textContent, /Market 1\.00/);
  assert.match(m[0].querySelector('.hp-rk-parts').textContent, /NVDA 0\.60 · AAPL 0\.18 · MSFT 0\.09 · not measured 0\.08 \(BRK\.B\)/);
  assert.match(m[1].textContent, /97% of the 6% risk budget/);
  assert.match(m[1].textContent, /30\.2% has no stop/);
  assert.match(m[1].querySelector('.hp-stack-ref').textContent, /Budget 6%/);
  assert.match(m[1].querySelector('.hp-rk-parts').textContent, /NVDA 62% · AAPL 22% · MSFT 13% · no model stop: RIVN, XOM, KO, BRK\.B/);
  assert.equal(m[2].querySelectorAll('.hp-stack-ref').length, 0, 'volatility has no valid reference, so no reference line');
  assert.match(m[2].textContent, /Historical variability, not a loss limit/);
  assert.match(d.querySelector('.hp-stop-note').textContent, /not orders at Robinhood, and not a maximum loss: a gap or slippage/);
  assert.match(d.querySelector('.hp-risk-lists').textContent, /Move together.*0\.70\+ correlation · 250 days.*AAPL\s*\+\s*MSFT\s*0\.98\s*24\.7% of account together/s);
  assert.equal(d.querySelector('[data-hp-disclosure="risk"]').open, false);
  for (const band of [/\blow risk\b/i, /\bmedium risk\b/i, /\bhigh risk\b/i]) assert.doesNotMatch(d.body.textContent, band);
});

test('each per-holding split adds back up to the figure the server published', () => {
  const pf = fixture.portfolio;
  const { moveParts, betaParts, stopParts } = hp._parts;
  const mv = moveParts(pf);
  assert.ok(Math.abs(mv.rows.reduce((t, x) => t + x.v, 0) - pf.overview.day_change) < 1e-6);
  assert.ok(Math.abs(mv.rows.reduce((t, x) => t + x.pts, 0) - pf.overview.day_change_pct) < 1e-9);
  const b = betaParts(pf);
  assert.ok(Math.abs(b.measured + b.unmeasured - pf.metrics.beta.portfolio_beta) < 1e-9);
  assert.deepEqual(b.missing, ['BRK.B']);
  const s = stopParts(pf);
  assert.ok(Math.abs(s.parts.reduce((t, x) => t + x.loss, 0) - pf.metrics.stops.loss) < 1e-6);
  assert.deepEqual(s.parts.map((x) => x.symbol), ['NVDA', 'AAPL', 'MSFT', 'JNJ']);
  // A split that does not reconcile with the published loss is not drawn.
  const off = clone(pf);
  off.metrics.stops.loss = 9000;
  assert.equal(stopParts(off).parts, null);
  assert.equal(frag(hp.exposureHtml(off)).querySelectorAll('[data-risk="stops"] .hp-seg').length, 1, 'one unsplit bar');
});

test('small reconciliation differences are secondary without altering the server status', () => {
  const pf = clone(fixture.portfolio);
  pf.issues = [{ code: 'TOTALS_MISMATCH', scope: 'portfolio', severity: 'error', message: 'Rounded totals differ.' }];
  pf.reconciliation = { status: 'mismatch', difference: 0.12, reported: 90576 };
  pf.data_state = 'partial';
  const before = clone(pf);
  const d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
  assert.equal(d.querySelector('.hp-recon-note'), null, 'no visible mismatch note');
  assert.equal(d.querySelector('.hp-dq.is-warn'), null);
  assert.equal(d.querySelector('.hp-issues > summary').textContent, 'Data details');
  assert.match(d.querySelector('.hp-recon').textContent, /\$0\.12.*original figures retained/);
  assert.deepEqual(pf, before);
});

test('quiet diagnostics retain unavailable tax lots; missing values and basis remain material', () => {
  const pf = clone(fixture.portfolio);
  pf.issues = [{ code: 'TAX_LOTS_UNAVAILABLE', scope: 'portfolio', severity: 'info', message: 'Tax lots unavailable.' }];
  let d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
  assert.equal(d.querySelector('.hp-dq.is-warn'), null, 'an info note stays in the record');
  assert.match(d.querySelector('.hp-issue-list').textContent, /Tax lots unavailable/);
  pf.reconciliation.status = 'unverifiable';
  d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
  assert.match(d.querySelector('.hp-dq-lead.hp-recon-note').textContent, /could not be cross-checked/);
  pf.issues.push({ code: 'COST_BASIS_MISSING', scope: 'BRK.B', severity: 'warning', message: 'Cost basis unavailable.' });
  d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
  assert.match(d.querySelector('.hp-overview .hp-kpis').textContent, /no cost basis for BRK\.B/);
  assert.match(d.querySelector('.hp-issue-list').textContent, /Cost basis unavailable/);
  pf.issues.push({ code: 'SOMETHING_NEW', scope: 'portfolio', severity: 'error', message: 'Unplaced error.' });
  d = frag(hp.portfolioHtml({ ...fixture, portfolio: pf }));
  assert.match(d.querySelector('.hp-issue-list').textContent, /Unplaced error\./, 'an error with no home is still recorded');
  assert.ok(d.querySelector('.hp-dq.is-warn'));
});

test('performance drivers split the session move by holding and compare it with the market only within one session', () => {
  const pf = fixture.portfolio;
  hp._state.market = null;
  let d = frag(hp.todayHtml(pf));
  assert.match(d.querySelector('.q-head-sub').textContent, /^Actual account change · Latest session · Fri, Sep 25 · \+\$330\.60 \(\+0\.37%\)$/);
  assert.match(d.querySelector('.hp-td-r .hp-fine').textContent, /change since the previous close as reported by Robinhood, divided by the prior-close account value/);
  const rows = [...d.querySelectorAll('.hp-td-r .hp-mv')].map(words);
  assert.deepEqual(rows.slice(0, 2), ['NVDA +$396 +0.44', 'XOM -$91 -0.10']);
  assert.match(rows.at(-1), /^2 others \+\$34 \+0\.04$/);
  assert.match(d.querySelector('.hp-td-l h3').textContent, /By sector/, 'without index quotes: by sector, no comparison');
  assert.match(d.querySelector('.hp-td-l').textContent, /Loading index quotes/);
  try {
    // Index quotes from another session are never compared.
    hp._state.market = { at: Date.now(), quotes: [{ symbol: 'SPY', label: 'S&P 500', change_pct: -0.74, quote_date: '2026-09-28' }] };
    d = frag(hp.todayHtml(pf));
    assert.match(d.querySelector('.hp-td-l').textContent, /No market comparison: index quotes are from Mon, Sep 28, holdings from Fri, Sep 25/);
    assert.doesNotMatch(frag(hp.overviewHtml(pf)).body.textContent, /S&P 500/);
    // Same session: portfolio vs indices, with the one-factor beta estimate labelled as such.
    hp._state.market = { at: Date.now(), quotes: [{ symbol: 'SPY', label: 'S&P 500', change_pct: -0.42, quote_date: '2026-09-25' }, { symbol: 'QQQ', label: 'Nasdaq 100', change_pct: -0.61, quote_date: '2026-09-25' }] };
    d = frag(hp.todayHtml(pf));
    const mk = [...d.querySelectorAll('.hp-td-l .hp-mv')].map(words);
    assert.deepEqual(mk, ['Your portfolio +0.37%', 'Beta-implied -0.49%', 'S&P 500 -0.42%', 'Nasdaq 100 -0.61%']);
    assert.match(d.querySelector('.hp-td-read').textContent, /gained 0\.37% while the S&P 500 fell 0\.42%: 0\.79 pts ahead of the market\. NVDA added \+0\.44 pts/);
    assert.match(d.querySelector('.hp-td-l').textContent, /A one-factor estimate, not attribution/);
    assert.match(frag(hp.overviewHtml(pf)).querySelector('.hp-kpi-vs').textContent, /S&P 500 -0\.42%/);
  } finally {
    hp._state.market = null;
  }
});

test('hide amounts masks dollar figures in the drivers and rail too', () => {
  hp._state.hidden = true;
  try {
    const pf = fixture.portfolio;
    const text = [hp.todayHtml(pf), hp.guidanceHtml(pf), hp.overviewHtml(pf)].map((h) => frag(h).body.textContent).join(' ');
    for (const v of ['$396', '$22,676', '$3,540', '$9,650']) assert.ok(!text.includes(v), `${v} hidden`);
    assert.match(text, /35\.0%/, 'weights stay visible');
  } finally {
    hp._state.hidden = false;
  }
});
test('paused and loading guidance offer truthful recovery without repeating unavailable holdings', () => {
  const pf = clone(fixture.portfolio);
  pf.positions.forEach((r) => { Object.assign(r.guidance, { status: 'unavailable', action: 'Unavailable' }); r.broker.quote_freshness = { status: 'stale' }; });
  let d = frag(hp.guidanceHtml(pf));
  assert.match(d.querySelector('.hp-gd-msg').textContent, /Fresh quotes are required/);
  assert.equal(d.querySelectorAll('.hp-gi').length, 0);
  pf.freshness.analysis.state = 'running';
  d = frag(hp.guidanceHtml(pf));
  assert.match(d.querySelector('.hp-gd-msg').textContent, /Analysis in progress/);
  assert.equal(d.querySelector('[data-hp="refresh"]').disabled, true);
});

test('risk visuals do not draw negative widths or imply a yearly loss bound', () => {
  const pf = clone(fixture.portfolio);
  pf.metrics.beta.equity_beta = -0.5;
  pf.policy.stop_risk_budget = 0;
  const d = frag(hp.exposureHtml(pf));
  assert.equal(d.querySelectorAll('.hp-gauge').length, 0);
  assert.match(d.body.textContent, /Historical variability, not a loss limit/);
  assert.doesNotMatch(d.body.textContent, /Typical yearly swing/);
});

test('a grade-F trim on a high score renders as risk, not as a score band', () => {
  const pf = clone(fixture.portfolio);
  const r = pf.positions.find((p) => p.symbol === 'XOM');
  Object.assign(r.guidance, { conviction: 70, basis: { code: 'RISK_GRADE_F', kind: 'risk', text: 'Risk grade F triggers a trim review at any score' },
    key_reason: 'Risk grade F triggers a trim review at any score' });
  const item = [...frag(hp.guidanceHtml(pf)).querySelectorAll('.hp-gi')].find((el) => el.dataset.hpDisclosure === 'gi-XOM');
  const card = item.closest('.hp-gc');
  assert.equal(card.querySelector('.hp-gc-t').textContent, 'Risk grade F triggers a trim review at any score');
  assert.match(card.querySelector('.hp-gc-s').textContent, /^Risk ·/);
  assert.doesNotMatch(item.textContent, /reduce band/);
});

test('connection failures say which server failed and how, never a bare HTTP code', async () => {
  const realFetch = global.fetch;
  try {
    // No process answering (server stopped while the page stayed open).
    global.fetch = async () => { throw new TypeError('Failed to fetch'); };
    let res = await hp._api('POST', '/connect');
    assert.equal(res.ok, false);
    assert.equal(res.status, 0);
    assert.match(res.data.error, /Cannot reach the QUANTA server at /);
    assert.match(res.data.error, /start\.ps1/);
    // Something answers, but it is a QUANTA without My Portfolio (an old checkout).
    global.fetch = async () => ({ ok: false, status: 404, json: async () => { throw new SyntaxError('HTML'); } });
    res = await hp._api('POST', '/connect');
    assert.equal(res.ok, false);
    assert.match(res.data.error, /has no My Portfolio/);
    assert.match(res.data.error, /older copy of QUANTA/);
    // A real JSON error from this server is passed through unchanged.
    global.fetch = async () => ({ ok: false, status: 502, json: async () => ({ error: 'Robinhood metadata request failed (HTTP 503).' }) });
    res = await hp._api('POST', '/connect');
    assert.equal(res.data.error, 'Robinhood metadata request failed (HTTP 503).');
    global.fetch = async () => ({ ok: false, status: 500, json: async () => { throw new SyntaxError('HTML'); } });
    res = await hp._api('GET', '/status');
    assert.match(res.data.error, /unexpected response \(HTTP 500\)/);
  } finally {
    global.fetch = realFetch;
  }
});
