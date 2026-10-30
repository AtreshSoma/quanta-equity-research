/* Plan lab card: the three plain questions (your plan, does it fit the rules,
 * how often would it have worked), escaping of server strings, pre-fill from the
 * Research levels, live dollar summary, client validation, the CSRF header on POST,
 * and one mounted submit flow. Old payloads (no titles, no `plain`) must still
 * render. No network: fetch is replaced. */
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');

const lab = require('../../static/plan-lab.js');

const MARK = '"><img src=x data-xss onerror=alert(1)>';

// The server's answer, with the new fields: titles, a summary and a skipped group.
const checkRes = () => ({
  ticker: 'AAPL',
  overall: 'BLOCK',
  incomplete: true,
  fingerprint: 'abcdef0123456789',
  summary: { counts: { PASS: 1, WARN: 1, BLOCK: 1, SKIPPED: 3 }, headline: 'One rule blocks this plan: the stop is above the target.' },
  notes: [`Not advice ${MARK}`, 'No model here has a demonstrated edge.'],
  checks: [
    { id: 'rr', title: 'Net reward to risk', status: 'WARN', message: `Net R/R 1.54 ${MARK}`, next_step: `Enter at or below $99 ${MARK}` },
    { id: 'geometry', title: 'Stop and target order', status: 'BLOCK', message: `Out of order ${MARK}`, next_step: 'Move the stop below the buy price.' },
    { id: 'dollar_risk', title: 'Dollar risk', status: 'SKIPPED', message: 'Enter the account value.', next_step: '' },
    { id: 'position_size', title: 'Position size', status: 'SKIPPED', message: 'Enter the account value or cash.', next_step: '' },
    { id: 'concentration_name', title: 'Single-name', status: 'SKIPPED', message: 'No holdings.', next_step: '' },
    { id: 'concentration_sector', title: 'Sector', status: 'SKIPPED', message: 'No holdings.', next_step: '' },
    { id: 'fomc', title: 'Fed decision', status: 'PASS', message: 'No meeting in the horizon.', next_step: '' },
    { id: 'todays_move', title: 'Today’s move', status: 'PASS', message: 'Within a normal day.', next_step: '' },
    { id: 'material_filings', title: 'Recent SEC filings', status: 'PASS', message: 'None in 90 days.', next_step: '' },
    { id: 'decision', title: 'QUANTA’s call', status: 'PASS', message: 'Buy.', next_step: '' },
  ],
});

const simRes = () => ({
  ticker: 'AAPL',
  available: true,
  paths: 5000,
  horizon_sessions: 63,
  summary: `Summary ${MARK}`,
  notes: [`Note ${MARK}`],
  plan: { rr_net: 0.96 },
  simulation: {
    p_target: 0.38, p_stop: 0.61, p_timeout: 0.01, target_share_decided: 0.38,
    break_even_share: 0.51, expected_r: -0.25, r: { p5: -1, median: -1, p95: 0.95 },
    simulated_band: 'Below break-even',
  },
  historical: {
    sessions_tested: 683, target: 272, stop: 411, timeout: 0, target_share_decided: 0.4,
    wilson95_effective: [0.15, 0.65], effective_n: 11,
  },
  verdict: { label: 'Not distinguishable from break-even', history_read: 'not distinguishable' },
});

// The new `plain` block as the server is due to send it.
const simPlain = () => ({
  ...simRes(),
  plain: {
    per_100: { target: 40, stop: 60, timeout: 0 },
    breakeven_per_100: 51,
    history_per_100: 53,
    history_range_per_100: [28, 79],
    independent_samples: 11,
    verdict: 'Not distinguishable from break-even',
  },
  history: { vol_sample_ann: 0.301, vol_ewma_ann: 0.335, vol_used_ann: 0.32, vol_method: 'blend' },
});

// Only what the page shows, not what it hides in the collapsed section: no
// markup from a server string, no image or script.
function bad(html) {
  const dom = new JSDOM(`<body>${html}</body>`);
  return dom.window.document.querySelectorAll('[data-xss], img, script').length;
}

test('checklist leads with the headline and the overall chip', () => {
  const html = lab.renderChecks(checkRes());
  assert.equal(bad(html), 0);
  assert.ok(html.includes('One rule blocks this plan: the stop is above the target.'));
  assert.ok(html.includes('Blocked'), 'overall chip word');
});

test('checklist shows only the problems, each with its title, message and what to do', () => {
  const dom = new JSDOM(`<body>${lab.renderChecks(checkRes())}</body>`);
  const rows = dom.window.document.querySelectorAll('.pl-problem');
  assert.equal(rows.length, 2, 'one BLOCK and one WARN, nothing else open');
  const text = dom.window.document.querySelector('.pl-problems').textContent;
  assert.ok(text.includes('Stop and target order'));
  assert.ok(text.includes('Net reward to risk'));
  assert.ok(text.includes('What to do'));
  assert.ok(text.includes('Move the stop below the buy price.'));
  assert.ok(!text.includes('Fed decision'), 'passed checks are not listed as problems');
});

test('passed checks collapse into one line, skipped checks into one line with a reason', () => {
  const dom = new JSDOM(`<body>${lab.renderChecks(checkRes())}</body>`);
  const doc = dom.window.document;
  const passed = doc.querySelector('.pl-more:not(.pl-skipped) > summary').textContent;
  assert.equal(passed.trim(), '✓ 4 checks passed');
  const skipped = doc.querySelector('.pl-skipped > summary').textContent;
  assert.match(skipped, /^4 couldn’t be checked: Dollar risk, Position size \(add account value\) · Single-name, Sector \(add account value or connect My Portfolio\)$/);
  // The lists stay in the DOM for keyboard and screen-reader users, behind a disclosure.
  assert.equal(doc.querySelectorAll('.pl-skipped details, .pl-skipped ul li').length, 4);
});

test('no raw check id is ever shown; the server title wins, the id map covers old payloads', () => {
  const html = lab.renderChecks({
    overall: 'WARN',
    checks: [
      { id: 'material_filings', status: 'WARN', message: 'An 8-K landed today.', next_step: 'Read it.' },
      { id: 'todays_move', title: 'Today: −12.8% move', status: 'WARN', message: 'Large.', next_step: '' },
      { id: 'unmapped_thing', status: 'WARN', message: 'Something.', next_step: '' },
    ],
  });
  assert.ok(html.includes('Recent SEC filings'), 'fallback title from the id map');
  assert.ok(html.includes('Today: −12.8% move'), 'server title used');
  assert.ok(html.includes('Plan check'), 'unknown id gets a generic title');
  assert.ok(!html.includes('material_filings'));
  assert.ok(!html.includes('unmapped_thing'));
  assert.ok(!html.includes('todays_move'));
});

test('an unknown status is shown as not checked, never as a pass', () => {
  const html = lab.renderChecks({ overall: 'WARN', checks: [{ id: 'x', status: 'OK?', message: 'm', next_step: '' }] });
  assert.ok(html.includes('couldn’t be checked'));
  assert.ok(!html.includes('checks passed'));
  assert.ok(!html.includes('OK?'), 'the raw unknown status is not echoed');
});

test('an old checklist payload (no titles, no summary) still renders with the id map', () => {
  const html = lab.renderChecks({
    overall: 'BLOCK',
    checks: [
      { id: 'rr', status: 'WARN', message: 'Net R/R 1.54', next_step: 'Enter lower.' },
      { id: 'geometry', status: 'BLOCK', message: 'Out of order', next_step: '' },
      { id: 'fomc', status: 'PASS', message: 'ok', next_step: '' },
    ],
  });
  assert.ok(html.includes('Net reward to risk'));
  assert.ok(html.includes('Stop and target order'));
  assert.ok(html.includes('1 check passed'));
  assert.ok(html.includes('Blocked'));
});

test('empty checklist asks for a run instead of showing a result', () => {
  assert.match(lab.renderChecks(null), /Run the check/);
});

test('the fingerprint and notes sit under About this checklist, escaped', () => {
  const html = lab.renderChecks(checkRes());
  assert.ok(html.includes('About this checklist'));
  assert.ok(html.includes('abcdef0123456789'));
  assert.equal(bad(html), 0);
});

test('simulation leads with the plain sentence, from the plain block', () => {
  const html = lab.renderSimulation(simPlain());
  assert.equal(bad(html), 0);
  assert.ok(html.includes('Out of 100 simulated paths, about 40 reached the target first and 60 hit the stop.'));
  assert.ok(html.includes('To break even with this reward/risk you need about 51.'));
});

test('simulation without a plain block is derived from the shares, the same wording', () => {
  const html = lab.renderSimulation(simRes());
  assert.ok(html.includes('about 38 reached the target first and 61 hit the stop, and 1 timed out.'));
  assert.ok(html.includes('you need about 51.'));
});

test('history line states the range and the independent samples in plain words', () => {
  const html = lab.renderSimulation(simPlain());
  assert.ok(html.includes('reached the target first about 53 times in 100'));
  assert.ok(html.includes('could be anywhere from 28 to 79, about 11 independent samples'));
  assert.ok(html.includes('last 3 years'), '683 sessions reads as about three years');
});

test('the verdict is plain, with the technical label in its tooltip', () => {
  const dom = new JSDOM(`<body>${lab.renderSimulation(simPlain())}</body>`);
  const chip = dom.window.document.querySelector('.pl-verdict-row .chip');
  assert.equal(chip.textContent, 'Too close to call');
  assert.equal(chip.getAttribute('title'), 'Not distinguishable from break-even');
});

test('the bar is labelled with numbers and marks the break-even; it is never colour alone', () => {
  const html = lab.renderSimulation(simPlain());
  assert.match(html, /Reached target <b>40<\/b>/);
  assert.match(html, /Hit stop <b>60<\/b>/);
  assert.ok(html.includes('pl-be-mark'));
  assert.ok(html.includes('Break-even 51'));
});

test('simulation keeps the simulation-not-a-forecast line visible and the technical figures collapsed', () => {
  const dom = new JSDOM(`<body>${lab.renderSimulation(simPlain())}</body>`);
  const doc = dom.window.document;
  assert.match(doc.querySelector('.pl-forecast').textContent, /Simulation, not a forecast/);
  const calc = doc.querySelector('details.pl-calc');
  assert.ok(calc, 'technical section is a details element');
  assert.ok(calc.textContent.includes('Expected result per trade'));
  assert.ok(calc.textContent.includes('R is your risk per share'));
  assert.ok(calc.textContent.includes('EWMA 33.5%'));
  assert.ok(!doc.querySelector('.pl-lead').textContent.includes('R/R'), 'no jargon in the lead');
});

test('simulation refusal is shown as an error, with the server reason escaped', () => {
  const html = lab.renderSimulation({ available: false, error: `only 200 sessions ${MARK}` });
  assert.ok(html.includes('pl-error'));
  assert.equal(bad(html), 0);
});

test('levels are read from the Research decision, horizon falls back to 63', () => {
  const plan = lab.levelsFromAnalysis({
    ticker: 'aapl', decision: { levels: { entry: 100, stop: 95, target: 112, target_horizon_days: 63 } },
  });
  assert.deepEqual(plan, { ticker: 'AAPL', entry: 100, stop: 95, target: 112, horizon: 63 });
  const odd = lab.levelsFromAnalysis({ ticker: 'MSFT', decision: { levels: { entry: 1, stop: 0.5, target: 2, target_horizon_days: 900 } } });
  assert.equal(odd.horizon, 63);
  assert.equal(lab.levelsFromAnalysis({ ticker: '<img>', decision: {} }), null);
  assert.equal(lab.levelsFromAnalysis(null), null);
});

test('client validation catches bad plans before any request', () => {
  const ok = { ticker: 'AAPL', shares: 10, entry: 100, stop: 95, target: 112, horizon: 63, account: null, cash: null };
  assert.deepEqual(lab.validatePlan(ok), []);
  assert.ok(lab.validatePlan({ ...ok, stop: 101 }).some((e) => /stop must be below/i.test(e)));
  assert.ok(lab.validatePlan({ ...ok, shares: 0 }).some((e) => /Shares must be above zero/.test(e)));
  assert.ok(lab.validatePlan({ ...ok, horizon: 4 }).some((e) => /horizon/.test(e)));
  assert.ok(lab.validatePlan({ ...ok, ticker: 'A B' }).some((e) => /ticker/i.test(e)));
  assert.ok(lab.validatePlan({ ...ok, entry: null }).some((e) => /buy price/i.test(e)));
});

test('the live summary turns the plan into dollars, ratio floored', () => {
  const form = { ticker: 'AAPL', shares: 5, entry: 100, stop: 77.81, target: 134.4, horizon: 63, account: null, cash: null };
  assert.equal(lab.planSummary(form), 'Risking $111 (5 × $22.19) to make $172, 1.55 : 1 before costs.');
  const noShares = lab.planSummary({ ...form, shares: null });
  assert.match(noShares, /Risking \$22\.19 a share to make \$34\.40, 1\.55 : 1 before costs\./);
  assert.match(noShares, /Enter shares/);
  assert.equal(lab.planSummary({ ...form, stop: 101 }), 'The stop must be below the buy price and the target above it.');
  assert.match(lab.planSummary({ ...form, entry: null }), /Enter the buy price/);
});

test('ratios are floored, never rounded up', () => {
  assert.equal(lab.floor2(1.999), '1.99');
  assert.equal(lab.floor2(1.55), '1.55');
  assert.equal(lab.floor2(1.5502), '1.55');
});

test('each price shows its percent from the buy price', () => {
  const m = lab.planMath({ entry: 282.32, stop: 260.13, target: 310 });
  assert.equal(m.stopPct, '−7.9%');
  assert.equal(m.targetPct, '+9.8%');
  assert.equal(m.ordered, true);
  assert.equal(lab.planMath({ entry: null, stop: 1, target: 2 }).stopPct, '');
});

test('request bodies omit blank optional fields', () => {
  const form = { ticker: 'AAPL', shares: 10, entry: 100, stop: 95, target: 112, horizon: 63, account: null, cash: 0 };
  const check = lab.checkBody(form);
  assert.equal('account_value' in check, false);
  assert.equal(check.cash, 0);
  assert.deepEqual(lab.simulateBody(form), { ticker: 'AAPL', entry: 100, stop: 95, target: 112, horizon_sessions: 63 });
});

test('POST carries the CSRF header and surfaces the server error', async () => {
  const seen = [];
  const fetchImpl = async (url, opts) => {
    seen.push({ url, opts });
    return { ok: false, status: 400, json: async () => ({ error: 'ticker contains unsupported characters' }) };
  };
  await assert.rejects(lab.postJson('/api/plan/check', { ticker: 'X' }, fetchImpl),
    /unsupported characters/);
  assert.equal(seen[0].opts.method, 'POST');
  assert.equal(seen[0].opts.headers['X-QUANTA-Request'], '1');
  assert.equal(seen[0].opts.headers['Content-Type'], 'application/json');
});

test('the mounted card has the three steps, a read-only ticker and one primary button', () => {
  const dom = new JSDOM('<body><div id="an-results"></div><div id="plan-lab-body"></div></body>');
  const doc = dom.window.document;
  lab.mount(doc, () => null);
  assert.match(doc.getElementById('pl-plan-title').textContent, /Your plan/);
  assert.match(doc.getElementById('pl-checks-title').textContent, /Does it fit the rules\?/);
  assert.match(doc.getElementById('pl-sim-title').textContent, /How often would this have worked\?/);
  assert.equal(doc.getElementById('pl-ticker').readOnly, true);
  assert.equal(doc.querySelectorAll('button[type="submit"]').length, 1);
  assert.equal(doc.getElementById('pl-run').textContent.trim(), 'Check my plan');
  assert.ok(doc.querySelector('#pl-more'), 'horizon, account and cash sit under More options');
  assert.ok(doc.getElementById('pl-more').textContent.includes('Account value (optional)'));
});

test('mounted card pre-fills from Research, updates the live summary, submits, and renders escaped results', async () => {
  const dom = new JSDOM(`<body>
    <div id="an-results"></div>
    <details id="plan-lab"><summary>Plan lab</summary><div id="plan-lab-body"></div></details>
  </body>`);
  const doc = dom.window.document;
  const analysis = { ticker: 'AAPL', decision: { levels: { entry: 100, stop: 95, target: 112, target_horizon_days: 63 } } };
  const api = lab.mount(doc, () => analysis);
  assert.ok(api, 'mounted');
  assert.equal(doc.getElementById('pl-ticker').value, 'AAPL');
  assert.equal(doc.getElementById('pl-entry').value, '100');
  assert.equal(doc.getElementById('pl-stop').value, '95');
  assert.equal(doc.getElementById('pl-target').value, '112');
  assert.match(doc.getElementById('pl-prefill').textContent, /pre-filled from the Research levels/);
  assert.equal(doc.getElementById('pl-stop-pct').textContent, '−5.0% from buy');
  assert.match(doc.getElementById('pl-summary').textContent, /1 share|Enter shares/);

  // Typing updates the percent and the summary at once.
  doc.getElementById('pl-stop').value = '90';
  doc.getElementById('pl-form').dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  assert.equal(doc.getElementById('pl-stop-pct').textContent, '−10.0% from buy');

  doc.getElementById('pl-stop').value = '95';
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    const data = url.endsWith('/check') ? checkRes() : simPlain();
    return { ok: true, status: 200, json: async () => data };
  };
  doc.getElementById('pl-shares').value = '10';
  doc.getElementById('pl-form').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 20));
  delete globalThis.fetch;

  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.opts.headers['X-QUANTA-Request'] === '1'));
  assert.ok(calls.some((c) => c.url === '/api/plan/simulate'));
  const checks = doc.getElementById('pl-checks').innerHTML;
  const sim = doc.getElementById('pl-sim').innerHTML;
  assert.match(checks, /Net reward to risk/);
  assert.match(sim, /Too close to call/);
  assert.match(sim, /Out of 100 simulated paths/);
  assert.equal(doc.querySelectorAll('[data-xss]').length, 0);
  assert.equal(doc.querySelectorAll('#pl-checks img, #pl-sim img').length, 0);
});

test('the ticker is editable only after Change is pressed', () => {
  const dom = new JSDOM('<body><div id="an-results"></div><div id="plan-lab-body"></div></body>');
  const doc = dom.window.document;
  lab.mount(doc, () => null);
  assert.equal(doc.getElementById('pl-ticker').readOnly, true);
  doc.getElementById('pl-ticker-edit').dispatchEvent(new dom.window.Event('click'));
  assert.equal(doc.getElementById('pl-ticker').readOnly, false);
});

test('mounted card without shares runs only the simulation and says why no checklist ran', async () => {
  const dom = new JSDOM('<body><div id="an-results"></div><div id="plan-lab-body"></div></body>');
  const doc = dom.window.document;
  lab.mount(doc, () => null);
  doc.getElementById('pl-ticker').value = 'AAPL';
  doc.getElementById('pl-entry').value = '100';
  doc.getElementById('pl-stop').value = '95';
  doc.getElementById('pl-target').value = '110';
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return { ok: true, status: 200, json: async () => simRes() };
  };
  doc.getElementById('pl-form').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 20));
  delete globalThis.fetch;
  assert.deepEqual(calls, ['/api/plan/simulate']);
  assert.match(doc.getElementById('pl-checks').textContent, /Enter the number of shares/);
});

test('the verdict chip is the short plain label; the sentence sits under it', () => {
  const res = simPlain();
  res.verdict = { label: 'Not distinguishable from break-even' };
  res.plain = Object.assign({}, res.plain || {}, {
    verdict: "The simulation puts the target share near break-even. The stock's own history cannot tell this plan from break-even." });
  const dom = new JSDOM(`<body>${lab.renderSimulation(res)}</body>`);
  const chip = dom.window.document.querySelector('.pl-verdict-row .chip');
  assert.equal(chip.textContent.trim(), 'Too close to call');
  assert.equal(chip.getAttribute('title'), 'Not distinguishable from break-even');
  assert.match(dom.window.document.querySelector('.pl-verdict-why').textContent, /cannot tell this plan from break-even/);
  assert.ok(lab.renderSimulation(res).includes('How the simulation is calculated'));
});

test('with a server headline, the old "not a full pass" line is not repeated', () => {
  const res = checkRes();
  res.incomplete = true;
  res.summary = { headline: '1 warning: Reward to risk. 4 checks could not run.', counts: { PASS: 4, WARN: 1, BLOCK: 0, SKIPPED: 4 } };
  assert.ok(!lab.renderChecks(res).includes('not a full pass'));
  delete res.summary;
  assert.ok(lab.renderChecks(res).includes('not a full pass'), 'old payloads keep the line');
});
