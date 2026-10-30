/* Material filings card: rendering of the 8-K list, escaping of SEC text, the loading, empty,
 * not-covered and error states, the sec.gov link rule, the past-reaction sentence, and the mounted
 * fetch flow. No network: fetch is replaced. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');

const ev = require('../../static/events.js');

const MARK = '"><img src=x data-xss onerror=alert(1)>';
const SEC_URL = 'https://www.sec.gov/Archives/edgar/data/320193/000032019326000042/0000320193-26-000042-index.htm';

const eventRes = () => ({
  ticker: 'AAPL',
  covered: true,
  status: 'ok',
  days: 90,
  available: true,
  as_of: '2026-10-28',
  notes: [`Note ${MARK}`],
  recent: [{
    date: '2026-10-20',
    form: '8-K',
    max_severity: 'HIGH',
    primary_item: '4.02',
    url: SEC_URL,
    reaction_session: '2026-10-21',
    reaction_note: `Filed after the close ${MARK}`,
    items: [
      { code: '4.02', title: `Non-Reliance ${MARK}`, severity: 'HIGH', why: `Restatement ${MARK}` },
      { code: '9.01', title: 'Financial Statements and Exhibits', severity: 'LOW', why: 'Exhibits' },
    ],
  }],
  study: {
    '4.02': {
      code: '4.02',
      title: 'Non-Reliance',
      severity: 'HIGH',
      filings: 7,
      windows: {
        '1': { n: 7, too_few: false, median_pct: -1.2, mean_abs_pct: 3.4 },
        '5': { n: 7, too_few: false, median_pct: -2.345, mean_abs_pct: 3.4, min_n: 5 },
      },
    },
  },
  study_status: 'ok',
});

function bad(html) {
  const dom = new JSDOM(`<body>${html}</body>`);
  return dom.window.document.querySelectorAll('[data-xss], img').length;
}

test('server strings are escaped: no injected element reaches the DOM', () => {
  const html = ev.renderCard(eventRes(), 90);
  assert.equal(bad(html), 0);
  assert.ok(html.includes('&quot;&gt;&lt;img'), 'the mark is shown as text');
});

test('each filing shows its date, severity word, item code and title', () => {
  const html = ev.renderCard(eventRes(), 90);
  assert.ok(html.includes('2026-10-20'));
  assert.ok(html.includes('>HIGH<'), 'severity is written as text, not colour alone');
  assert.ok(html.includes('Item 4.02'));
  assert.ok(html.includes('Item 9.01'));
});

test('the filing link opens on sec.gov with noopener noreferrer', () => {
  const html = ev.renderCard(eventRes(), 90);
  assert.match(html, /<a class="mf-link" href="https:\/\/www\.sec\.gov\/[^"]+" target="_blank" rel="noopener noreferrer"/);
});

test('a link that is not a sec.gov archive address is never rendered as a link', () => {
  for (const u of ['javascript:alert(1)', 'http://www.sec.gov/Archives/x', 'https://evil.example/x', '']) {
    assert.equal(ev.safeUrl(u), '', u);
  }
  assert.equal(ev.safeUrl(SEC_URL), SEC_URL);
  const res = eventRes();
  res.recent[0].url = 'javascript:alert(1)';
  const html = ev.renderCard(res, 90);
  assert.ok(!html.includes('<a class="mf-link"'));
  assert.ok(html.includes('No SEC link was given'));
});

test('an item with history shows the past-reaction sentence with its n and the 5-session median', () => {
  const html = ev.renderCard(eventRes(), 90);
  assert.ok(html.includes("This stock's past reaction: median -2.3% vs SPY over 5 sessions; typical move ±3.4% (n=7)."));
});

test('fewer than five past filings says too few to say and shows no median', () => {
  const res = eventRes();
  res.study['4.02'].windows['5'] = { n: 3, min_n: 5, too_few: true, median_pct: null, mean_abs_pct: null };
  const html = ev.renderCard(res, 90);
  assert.ok(html.includes('too few past filings to say (n=3; QUANTA needs 5)'));
  assert.ok(!html.includes('median'));
});

test('an item with no study entry shows no reaction line', () => {
  const res = eventRes();
  res.study = {};
  const html = ev.renderCard(res, 90);
  assert.ok(!html.includes('past reaction'));
});

test('every answer carries the not-a-forecast caveat', () => {
  assert.ok(ev.renderCard(eventRes(), 90).includes('not a forecast'));
  assert.ok(ev.renderCard({ status: 'ok', recent: [], days: 90 }, 90).includes('not a forecast'));
});

test('an empty window says no 8-K filings, plainly', () => {
  const html = ev.renderCard({ status: 'ok', recent: [], days: 90 }, 90);
  assert.ok(html.includes('No 8-K filings in the last 90 days.'));
});

test('an India ticker shows a not-covered state, not an empty result', () => {
  const html = ev.renderCard({ status: 'not_covered', covered: false,
    message: 'Not covered: SEC filings exist only for US-registered issuers. QUANTA could not check RELIANCE.NS for 8-K filings.' }, 90);
  assert.ok(html.includes('Not covered'));
  assert.ok(html.includes('SEC filings exist only for US-registered issuers'));
  assert.ok(!html.includes('No 8-K filings'));
});

test('an SEC error is an alert with the reason and a retry control', () => {
  const html = ev.renderCard({ available: false, status: 'unavailable', error: `SEC ${MARK}`, fix: 'Try later' }, 90);
  assert.equal(bad(html), 0);
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes('data-mf-retry'));
});

test('an unknown severity is shown as NOT RATED, never as a pass', () => {
  const res = eventRes();
  res.recent[0].items = [{ code: '6.05', title: 'Item 6.05', severity: '<b>x</b>', why: '' }];
  const html = ev.renderCard(res, 90);
  assert.ok(html.includes('NOT RATED'));
  assert.ok(!html.includes('<b>x</b>'));
});

test('a study that could not read prices says so and keeps the filings', () => {
  const res = eventRes();
  res.study = {};
  res.study_status = 'unavailable';
  res.study_message = `Price history could not be read ${MARK}`;
  const html = ev.renderCard(res, 90);
  assert.equal(bad(html), 0);
  assert.ok(html.includes('Item 4.02'));
  assert.ok(html.includes('Price history could not be read'));
});

test('the ticker must be a symbol before anything is fetched', () => {
  const fetched = [];
  globalThis.fetch = async (url) => { fetched.push(url); return { ok: true, json: async () => eventRes() }; };
  const dom = new JSDOM('<body><div id="material-filings"></div></body>');
  const api = ev.mount(dom.window.document, () => ({ ticker: '<img src=x>' }), 90);
  assert.ok(api);
  delete globalThis.fetch;
  assert.equal(fetched.length, 0);
});

const flush = () => new Promise((r) => setImmediate(r));

test('mounted card fetches the ticker once, renders the answer, and escapes SEC text', async () => {
  const dom = new JSDOM('<body><div id="an-results"></div><div id="material-filings"></div></body>');
  const doc = dom.window.document;
  const fetched = [];
  globalThis.fetch = async (url) => {
    fetched.push(url);
    return { ok: true, json: async () => eventRes() };
  };
  try {
    const analysis = { ticker: null };
    const api = ev.mount(doc, () => analysis, 90);
    assert.equal(fetched.length, 0, 'no ticker on screen, nothing fetched');
    analysis.ticker = 'AAPL';
    api.syncFromAnalysis();
    api.syncFromAnalysis();
    await flush();
    await flush();
    assert.equal(fetched.length, 1, 'the same ticker is not fetched twice');
    assert.ok(fetched[0].startsWith('/api/events?symbol=AAPL&days=90'));
    const body = doc.getElementById('mf-body');
    assert.ok(body.textContent.includes('Item 4.02'));
    assert.equal(body.querySelectorAll('[data-xss], img').length, 0);
  } finally {
    delete globalThis.fetch;
  }
});

test('a failed request shows the plain error with a retry, and retry fetches again', async () => {
  const dom = new JSDOM('<body><div id="material-filings"></div></body>');
  const doc = dom.window.document;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) return { ok: false, json: async () => ({ error: 'SEC EDGAR is unavailable right now.', fix: 'Try again later.' }) };
    return { ok: true, json: async () => eventRes() };
  };
  try {
    ev.mount(doc, () => ({ ticker: 'AAPL' }), 90);
    await flush();
    await flush();
    const body = doc.getElementById('mf-body');
    assert.ok(body.querySelector('[role="alert"]'));
    assert.ok(body.textContent.includes('SEC EDGAR is unavailable right now.'));
    body.querySelector('[data-mf-retry]').dispatchEvent(new dom.window.Event('click', { bubbles: true }));
    await flush();
    await flush();
    assert.equal(calls, 2);
    assert.ok(body.textContent.includes('Item 4.02'));
  } finally {
    delete globalThis.fetch;
  }
});

test('the live API shape (study.items) renders the past-reaction line', () => {
  // /api/events nests per-item stats under study.items (event_study.build_report).
  // A live check found the card silently dropping them when only the flat shape was read.
  const res = eventRes();
  res.study = { items: res.study, notes: [] };
  const html = ev.renderCard(res, 90);
  assert.match(html, /This stock&#39;s past reaction|This stock's past reaction/);
  assert.match(html, /n=7/);
});
