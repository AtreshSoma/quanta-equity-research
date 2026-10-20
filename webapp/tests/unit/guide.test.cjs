/**
 * "How QUANTA works": a product explanation. It must describe the five real
 * pages, never claim a validation the registry does not record, run every
 * demonstration on the engine's own rules (read back from the engine source
 * below), label every example as an example, and link only to real views.
 *
 * Run: node --test tests/unit/guide.test.cjs
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
// The page fills every rule number from engine/thresholds.py at boot (F9).
app.applyThresholds(document);
const T = globalThis.__QUANTA_THRESHOLDS__;
const rrText = (v) => `${Number(v).toFixed(2)} : 1`;
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
// Visible text with a space between elements (textContent glues a <dt> to its <dd>).
const spaced = (el) => {
  const w = document.createTreeWalker(el, 4);
  const parts = [];
  while (w.nextNode()) parts.push(w.currentNode.nodeValue);
  return parts.join(' ').replace(/\s+/g, ' ').trim();
};
// Spans inside a sentence (thresholds filled from config) must not add a space
// next to the punctuation around them.
const guideText = () => spaced($('#view-guide')).replace(/\( /g, '(').replace(/ ([),.;%])/g, '$1');
const status = () => $('#guide-evidence');
const untested = { status: 'UNTESTED', status_text: 'Never validated', permits_recommendation: false };

// ---------------------------------------------------------------------------
// Live status
// ---------------------------------------------------------------------------
test('with no validated model, the live status says so and shows the tag', () => {
  app.renderGuideEvidence({
    benchmark: 'SPY', min_sample: 30, totals: { recorded: 10, resolved: 0, pending: 10 },
    engines: [{ engine: 'elite', label: 'Deep analysis', evidence: untested }],
  });
  assert.match(status().textContent, /No QUANTA model is proven yet/);
  assert.ok(status().querySelector('.hw-tag'));
  assert.match(status().textContent, /10 calls recorded, 0 measured so far against SPY \(results count after 30\)/);
});

test('a validated engine is named; an unreachable ledger is never shown as validated', () => {
  app.renderGuideEvidence({
    benchmark: 'SPY', min_sample: 30, totals: { recorded: 40, resolved: 32 },
    engines: [{ engine: 'elite', label: 'Deep analysis', evidence: { status: 'VALIDATED', permits_recommendation: true } },
      { engine: 'elite_pro', label: 'Portfolio scanner', evidence: { status: 'NO_EDGE', permits_recommendation: false } }],
  });
  assert.match(status().textContent, /Deep analysis is validated; the other models are not/);
  app.renderGuideEvidence({ error: 'unreachable', unreachable: true });
  assert.match(status().textContent, /could not be loaded/);
  assert.equal($('#guide-evidence-meta').dataset.state, 'error');
});

// ---------------------------------------------------------------------------
// Structure: the real navigation, real links, labelled examples
// ---------------------------------------------------------------------------
test('the path names exactly the five primary pages, in navigation order', () => {
  const nav = $$('.nav-tabs .nav-tab').map((b) => b.getAttribute('aria-label') || b.textContent.trim());
  const path_ = $$('#view-guide .hw-step-tab b').map((b) => b.textContent);
  assert.deepEqual(path_, nav);
  assert.deepEqual(path_, ['Overview', 'Discover', 'Research', 'Picks', 'My Portfolio']);
  // Each node jumps to a section that exists, and each page section links to its view.
  for (const b of $$('#view-guide [data-hw-jump]')) assert.ok(document.getElementById(b.dataset.hwJump), b.dataset.hwJump);
  const linked = new Set($$('#view-guide .hw-ch [data-view-link]').map((a) => a.dataset.viewLink));
  for (const v of ['dashboard', 'discover', 'analyze', 'picks', 'holdings', 'track']) assert.ok(linked.has(v), `section link to ${v}`);
  assert.doesNotMatch(guideText(), /Four pages/);
});

test('the step bar names the five pages in order and jumps to each step', () => {
  const btns = $$('#view-guide .hw-steps-bar .hw-step-tab');
  assert.deepEqual(btns.map((b) => b.dataset.hwJump), ['hw-overview', 'hw-discover', 'hw-research', 'hw-picks', 'hw-holdings']);
  assert.ok($('#view-guide .hw-steps-bar').getAttribute('aria-label'));
  for (const b of btns) {
    const step = document.getElementById(b.dataset.hwJump);
    assert.ok(step.classList.contains('hw-step'), step.id);
    assert.equal(step.getAttribute('aria-labelledby'), step.querySelector('h2').id, `${step.id} is named by its heading`);
    assert.ok(step.querySelector('.hw-step-foot [data-view-link]'), `${step.id} says where it lives in QUANTA`);
  }
  // A story, not tabs: no step is hidden and nothing claims a tab role.
  assert.equal($$('#view-guide [role="tab"], #view-guide [role="tabpanel"], #view-guide [role="tablist"]').length, 0);
  const raw = new JSDOM(html).window.document;
  assert.ok(raw.querySelector('#view-guide .hw-steps-bar').hasAttribute('hidden'), 'without JavaScript the bar stays hidden');
  for (const s of raw.querySelectorAll('#view-guide .hw-step')) assert.ok(!s.hasAttribute('hidden'), s.id);
  // The track record stands after the story.
  assert.ok(!$('#hw-trust').closest('.hw-steps'));
});

test('the guide is a secondary view with links only to real views', () => {
  app.switchView('how-it-works');
  assert.ok($('#view-guide').classList.contains('active'));
  assert.equal($$('.nav-tab[aria-selected="true"]').length, 0);
  app.switchView('dashboard');
  const known = new Set(['dashboard', 'discover', 'analyze', 'picks', 'holdings', 'track', 'guide']);
  const links = $$('#view-guide [data-view-link]');
  assert.ok(links.length >= 9);
  for (const a of links) assert.ok(known.has(a.dataset.viewLink), a.dataset.viewLink);
  for (const a of $$('#view-guide [data-focus]')) assert.ok($(a.dataset.focus), a.dataset.focus);
});

test('every example is labelled as one, and no screenshot or glass remains', () => {
  assert.match($('.hw-inst').textContent, /Illustrative stock, not live data/);
  assert.match($('.hw-chart').getAttribute('aria-label'), /^Illustrative price path/);
  assert.match($('.hw-funnel .hw-demo-note').textContent, /example names/);
  assert.match($('.hw-book .hw-demo-note').textContent, /Example account, not real holdings/);
  assert.match($('.hw-fine').textContent, /illustrations, not live data or recommendations/);
  assert.equal($$('#view-guide img').length, 0, 'demos are live components, not captures');
  assert.equal($$('#view-guide .hw-glass').length, 0);
});

test('the Research sections are listed in the order the tear sheet renders them', () => {
  const sections = $$('#view-guide .hw-chapters b').map((b) => b.textContent);
  // Research redesign: a summary band, then one tab per panel (intended change).
  assert.deepEqual(sections, ['Summary', 'Decision detail', 'Business', 'Valuation', 'Technicals', 'Risks & catalysts', 'Evidence & data']);
  // Read back from app.js: the section names live once in TS_SECTIONS (the
  // board cells and the panels share them; Research rebuild, intended change),
  // and renderAnalysis builds the panels in this order.
  const js = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
  const block = js.match(/const TS_SECTIONS = Object\.freeze\(\{([\s\S]*?)\}\);/);
  assert.ok(block, 'TS_SECTIONS is in app.js');
  const named = [...block[1].matchAll(/(\w+): '([^']+)'/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(named.map(([, t]) => t), sections.slice(1), 'the Guide names the sections as the board does');
  const order = named.map(([k]) => js.indexOf(`panel('${k}', TS_SECTIONS.${k}, chapter(`));
  assert.ok(order.every((i) => i > 0), 'every panel label is in app.js');
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'panels appear in this order in app.js');
});

test('Discover tabs are described as they behave, not as their names suggest', () => {
  const text = guideText();
  assert.match(text, /Extended.*Not extended-hours trading/);
  assert.match(text, /Watchlist Early-momentum names from the last scan/);
  assert.doesNotMatch(text, /your watchlist|extended hours/i);
  assert.match(text, /Street research candidates, each placed on its 52-week range/);
});

test('the guide never claims QUANTA has no brokerage link, or that it can trade', () => {
  const text = guideText();
  assert.doesNotMatch(text, /never connects to a brokerage/i);
  assert.match(text, /Read-only, enforced in code/);
  assert.match(text, /Can QUANTA trade in my account\? No\./);
});

test('the guide never overstates the India universe or the benchmark', () => {
  assert.doesNotMatch(guideText(), /500 Indian stocks|Indian stocks in the Nifty 500/);
  assert.match(guideText(), /Nifty 500 Growth Value Dividend Momentum Large cap Mid cap Small cap/);
  // Only NSE (.NS) tickers are measured against the Nifty 50.
  assert.match(guideText(), /Nifty 50 for NSE stocks/);
});

// ---------------------------------------------------------------------------
// Demonstrations: the engine's rules on example inputs
// ---------------------------------------------------------------------------
test('the decision lab follows the resolver: bands, overrides in order, ownership', () => {
  const d = app.guideDecide;
  assert.equal(d({ score: 80 }).call, 'strong');
  assert.equal(d({ score: 74 }).call, 'buy');
  assert.equal(d({ score: 64 }).call, 'accumulate');
  assert.equal(d({ score: 50 }).call, 'watchlist', 'hold band, not owned');
  assert.equal(d({ score: 50, owned: true }).call, 'hold');
  // A bearish score stays Avoid for a non-holder (engine/decision_engine.py).
  assert.equal(d({ score: 40 }).call, 'avoid');
  assert.equal(d({ score: 40, owned: true }).call, 'avoid');
  assert.equal(d({ score: 80, grade: 'D' }).call, 'accumulate');
  assert.equal(d({ score: 80, grade: 'F' }).call, 'watchlist');
  assert.equal(d({ score: 80, grade: 'F', owned: true }).call, 'hold');
  assert.equal(d({ score: 80, insider: true }).call, 'watchlist');
  assert.equal(d({ score: 80, flag: true }).call, 'accumulate');
  assert.equal(d({ score: 80, lowRr: true }).call, 'watchlist');
  // New money and the holder rule are published separately.
  assert.equal(d({ score: 68 }).newMoney, 'Eligible');
  assert.equal(d({ score: 40 }).newMoney, 'Avoid');
  assert.equal(d({ score: 50 }).newMoney, 'Watch');
  assert.equal(d({ score: 50, owned: true }).holder, 'Hold');
  assert.equal(d({ score: 40, owned: true }).holder, 'Trim review');
  assert.equal(d({ score: 30, owned: true }).holder, 'Trim review', 'a low score alone is never an exit');
  assert.equal(d({ score: 30, owned: true, insider: true }).holder, 'Exit review');
  assert.equal(d({ score: 70, grade: 'F', owned: true }).holder, 'Trim review');
});

test('the lab renders its call, reasons and highlighted glossary card', () => {
  app.bindGuide();
  $('#hw-score').value = '80';
  $('#view-guide input[name="hw-grade"][value="D"]').checked = true;
  const r = app.renderGuideDecision();
  assert.equal(r.call, 'accumulate');
  assert.equal($('#hw-verdict').textContent, 'Accumulate');
  assert.equal($('#hw-verdict').dataset.tone, 'bull');
  assert.match($('#hw-why').textContent, /Score 80 starts as Strong Buy\..*Risk grade D caps a fresh buy at Accumulate/);
  assert.ok($('.hw-calls [data-call="accumulate"]').classList.contains('is-active'));
  assert.ok($('.hw-bands [data-band="strong"]').classList.contains('is-active'));
  $('#view-guide input[name="hw-grade"][value="ok"]').checked = true;
  $('#hw-score').value = '68';
  app.renderGuideDecision();
  assert.equal($('#hw-verdict').textContent, 'Buy');
});

test('the funnel cuts each example name at its own gate and funds the rest', () => {
  const rows = $$('.hw-funnel .hw-row');
  assert.equal(rows.length, 12);
  assert.equal($$('.hw-funnel .hw-gate').length, 8);
  for (let g = 1; g <= 8; g += 1) assert.ok(rows.some((r) => Number(r.dataset.out) === g), `a name stops at gate ${g}`);
  assert.equal(app.renderGuideFunnel(0), 12);
  assert.equal($$('.hw-row.is-cut').length, 0);
  assert.equal(app.renderGuideFunnel(4), 6);
  assert.match(spaced($('#hw-funnel-count')), new RegExp(`6 of 12 left after Reward/risk ${rrText(T.RR.SCANNER_MIN)}`));
  assert.equal(app.renderGuideFunnel(8), 2);
  assert.equal($$('.hw-row.is-funded').length, 2);
  assert.match($('.hw-row[data-out="4"] .hw-row-s').textContent, new RegExp(`under ${rrText(T.RR.SCANNER_MIN)}`));
  // The static markup is the finished state.
  assert.match(html, /<b>2<\/b> of 12 picked after all eight gates/);
});

test('the calculator reproduces the worked example and the static receipt', () => {
  const r = app.guideSize({ capital: 25000, entry: 100, stop: 95.8, call: 'buy', score: 68, grade: 'A', mandate: 'balanced' });
  assert.equal(r.shares, 25);
  assert.equal(Math.round(r.atRisk), 105);
  assert.ok(r.binding);
  assert.equal(app.guideSize({ mandate: 'conservative' }).shares, 17);
  assert.equal(app.guideSize({ grade: 'C' }).shares, 20);
  assert.equal(app.guideSize({ capital: 2000, entry: 50, stop: 48 }).capPct.toFixed(2), '0.20', 'small accounts get a wider cap');
  assert.match(app.guideSize({ capital: 600, entry: 900, stop: 880 }).none, /needs more capital/);
  assert.ok(app.guideSize({ stop: 101 }).error);
  const values = $$('#hw-receipt .hw-rc-v').map((b) => b.textContent);
  assert.deepEqual(values, ['$120.00', '4.2%', '$2,857', '$2,500', '$2,500', '25 shares']);
  const before = $('#hw-receipt').innerHTML;
  app.renderGuideSize(false);
  assert.equal($('#hw-receipt').innerHTML.replace(/\s+/g, ''), before.replace(/\s+/g, ''), 'JS renders the same receipt as the markup');
  // The hero's example size is the same arithmetic.
  assert.match($('.hw-inst').textContent, /25 shares/);
});

test('the posture ladder shows each band, and low confidence or stale data replace it', () => {
  assert.equal(app.guidePosture('lean-long').text.includes('50-70% gross exposure'), true);
  assert.deepEqual(app.guidePosture('offensive', 'low').band, [30, 55]);
  assert.deepEqual(app.guidePosture('defensive', 'stale').band, [20, 45]);
  // A low-confidence cap never lifts a defensive band (it once read 30-55%).
  assert.deepEqual(app.guidePosture('defensive', 'low').band, [20, 50]);
  assert.equal(app.guidePosture('defensive', 'low').capped, false);
  for (const row of $$('.hw-posture-row')) {
    const p = app.GUIDE_POSTURES[row.dataset.posture];
    assert.ok(p, row.dataset.posture);
    assert.equal(row.querySelector('.hw-posture-band').textContent, `${p.band[0]}-${p.band[1]}%`);
    assert.equal(row.style.getPropertyValue('--lo'), String(p.band[0]));
  }
  assert.equal($$('.hw-posture-row').length, 7);
});

test('the example book: bars match their weights and each holding has its own panel', () => {
  const bars = $$('.hw-bar');
  let total = 17; // cash, stated under the chart
  for (const b of bars) {
    const w = Number(b.style.getPropertyValue('--w'));
    total += w;
    assert.equal(b.querySelector('.hw-bar-b em').textContent, `${w}%`);
    assert.ok($(`[data-hold-panel="${b.dataset.hold}"]`), b.dataset.hold);
  }
  assert.equal(total, 100, 'the example account adds up');
  // Portfolio actions use My Portfolio's own group labels.
  const labels = new Set(bars.map((b) => b.querySelector('.hw-bar-a').textContent));
  for (const l of labels) assert.ok(['Exit Review', 'Trim / Risk Review', 'Consider Adding', 'Do Not Add', 'Hold'].includes(l), l);
});

test('the hero chart draws the example levels where their prices are', () => {
  // y = 20 + (110 - price) * 260 / 18 on a 0-300 viewBox.
  const y = (p) => +(20 + (110 - p) * 260 / 18).toFixed(1);
  const lv = (cls) => Number($(`.hw-chart .hw-lv--${cls}`).getAttribute('d').match(/M20 ([\d.]+)H/)[1]);
  assert.equal(lv('target'), y(108.4)); assert.equal(lv('entry'), y(100)); assert.equal(lv('stop'), y(95.8));
  const last = $('.hw-ch-line').getAttribute('points').trim().split(/\s+/).pop().split(',').map(Number);
  assert.equal(last[1], y(100), 'the price path ends at the entry');
  assert.equal((108.4 - 100) / (100 - 95.8), 2, 'reward/risk 2.0 : 1');
  assert.match($('.hw-inst').textContent, /\+\$8\.40.*-\$4\.20/s);
});

test('each promise in "What it does for you" jumps to the part of the page that shows it', () => {
  const items = $$('#view-guide .hw-help-list > li');
  assert.equal(items.length, 6);
  const targets = items.map((li) => li.querySelector('[data-hw-jump]').dataset.hwJump);
  assert.deepEqual(targets, ['hw-research', 'hw-discover', 'hw-picks', 'hw-holdings', 'hw-trust', 'hw-guard']);
  for (const id of targets) {
    const sec = document.getElementById(id);
    assert.ok(sec && sec.querySelector('h2, h3'), `${id} has a heading to take focus`);
    assert.ok(!sec.closest('[data-detail]'), `${id} is never folded away`);
  }
  // Plain words, no claims: nothing about returns, beating or winning.
  const text = spaced($('#view-guide .hw-help'));
  assert.doesNotMatch(text, /\b(beat|beats|outperform|best|guarantee|returns?)\b/i);
  assert.match(text, /never trade/);
});

test('reference detail folds behind one button per chapter and stays in the page', () => {
  const buttons = $$('#view-guide .hw-more');
  assert.deepEqual(buttons.map((b) => b.closest('.hw-ch').id), ['hw-overview', 'hw-discover', 'hw-research', 'hw-picks', 'hw-holdings']);
  for (const b of buttons) {
    const body = document.getElementById(b.getAttribute('aria-controls'));
    assert.ok(body && body.hasAttribute('data-detail'), b.getAttribute('aria-controls'));
    assert.equal(body.previousElementSibling, b, 'the button sits right above what it opens');
    assert.ok(b.querySelector('.hw-more-s').textContent.trim(), 'the button says what it opens');
  }
  // The demos themselves are never folded.
  for (const sel of ['.hw-posture', '.hw-funnel', '.hw-lab', '.hw-calc', '.hw-book', '.hw-guard', '.hw-ledger', '.hw-trust']) {
    assert.ok(!$(`#view-guide ${sel}`).closest('[data-detail]'), sel);
  }
  // Every threshold the folds hold is still filled from config.
  for (const s of $$('#view-guide [data-detail] [data-threshold]')) assert.ok(s.textContent.trim(), s.dataset.threshold);
});

test('the ladder marks the start, the rules walked and the landing rung', () => {
  $('#hw-score').value = '80';
  $('#view-guide input[name="hw-grade"][value="F"]').checked = true;
  app.renderGuideDecision();
  const cls = (c) => $(`.hw-ladder [data-call="${c}"]`).classList;
  assert.ok(cls('strong').contains('is-start'));
  assert.ok(cls('watchlist').contains('is-active'));
  for (const c of ['strong', 'buy', 'accumulate', 'hold']) assert.ok(cls(c).contains('is-path'), c);
  assert.ok(!cls('watchlist').contains('is-path'));
  $('#view-guide input[name="hw-grade"][value="ok"]').checked = true;
  $('#hw-score').value = '68';
  app.renderGuideDecision();
  assert.ok(!$('.hw-ladder .is-start'), 'no start marker when nothing lowered the call');
});

test('risk geometry: the hatched area is the dollars at risk, the dashed one the budget', () => {
  const r = app.guideSize({});
  const g = app.guideGeometry(25000, r);
  const area = (w, h) => w * (25000 * 0.25) * h * 0.12;
  assert.ok(Math.abs(area(g.fillW, g.h) - r.atRisk) < 0.01);
  assert.ok(Math.abs(area(g.budgetW, g.h) - r.budget) < 0.01);
  assert.equal(g.cap, 0.4);
  // The static markup is the same picture.
  assert.equal($('#hw-geo-fill').style.getPropertyValue('--gw'), '0.400');
});

test('the score mix follows the regime weights', () => {
  const w = app.guideMix('bear');
  assert.ok(Math.abs(Object.values(w).reduce((a, b) => a + b, 0) - 1) < 1e-9);
  assert.ok(w.fundamental > w.technical, 'fundamentals lead in a bear regime');
  const side = app.guideMix('sideways');
  for (const s of $$('#hw-mix-bar [data-pillar]')) {
    assert.equal(s.querySelector('b').textContent, `${Math.round(side[s.dataset.pillar] * 100)}%`, s.dataset.pillar);
  }
});

// ---------------------------------------------------------------------------
// The reel: fifteen seconds on one timeline, every figure the page's own.
// ---------------------------------------------------------------------------
const reelText = (id) => $(`#${id}`).textContent.trim();

test('the reel is 15 seconds with ordered chapters, and its player is labelled', () => {
  assert.equal(app.REEL_MS, 15000);
  assert.equal(Number($('#hw-reel-seek').getAttribute('max')), app.REEL_MS);
  const times = app.REEL_CHAPTERS.map(([ms]) => ms);
  assert.deepEqual(times, [...times].sort((a, b) => a - b), 'chapters in order');
  assert.ok(times.every((ms) => ms >= 0 && ms < app.REEL_MS));
  assert.deepEqual($$('[data-reel-t]').map((b) => Number(b.dataset.reelT)), times, 'chapter buttons match the timeline');
  assert.equal(app.reelSceneAt(0), '01 OPEN');
  assert.equal(app.reelSceneAt(8000), '04 RESEARCH');
  assert.equal(app.reelSceneAt(15000), '07 END');
  for (const id of ['hw-reel-play', 'hw-reel-replay', 'hw-reel-full', 'hw-reel-cover']) assert.ok($(`#${id}`).getAttribute('aria-label'), id);
  assert.ok($('label[for="hw-reel-seek"]'));
  assert.match($('#hw-reel svg').getAttribute('aria-labelledby'), /hw-reel-desc/);
  assert.ok(reelText('hw-reel-desc').length > 200, 'a full text description of the reel');
});

test('the reel poster is the Research frame, with the call and its tag drawn', () => {
  assert.equal(app.reelSceneAt(app.REEL_POSTER_MS), '04 RESEARCH');
  // The stamp lands by 9.2 s and the tag by 9.6 s; the scene leaves at 9.65 s.
  assert.ok(app.REEL_POSTER_MS >= 9600 && app.REEL_POSTER_MS < 9650, String(app.REEL_POSTER_MS));
});

test('reel keyframes span the whole reel and hold at both ends', () => {
  const kf = app.reelKeyframes([[1000, { opacity: 0 }], [2000, { opacity: 1 }]]);
  assert.equal(kf[0].offset, 0); assert.equal(kf[kf.length - 1].offset, 1);
  assert.equal(kf[0].opacity, 0); assert.equal(kf[kf.length - 1].opacity, 1);
  assert.deepEqual(kf.map((k) => k.offset), [0, 1000 / 15000, 2000 / 15000, 1]);
});

test('the poster is the closing frame: every other scene starts hidden', () => {
  for (let i = 1; i <= 6; i += 1) assert.match($(`#rl-s${i}`).getAttribute('style') || '', /opacity:\s*0/, `scene ${i}`);
  assert.equal($('#rl-s7').getAttribute('style'), null);
  assert.equal($('#rl-tc').textContent, '00:15.00');
  assert.equal($('#hw-reel-seek').value, '15000');
});

test('every figure in the reel is one the page demonstrates', () => {
  // Overview: the posture list and the Lean long band.
  for (const [i, key] of ['offensive', 'selective-offensive', 'lean-long', 'neutral', 'lean-defensive', 'selective-defensive', 'defensive'].entries()) {
    const band = app.GUIDE_POSTURES[key].band;
    assert.equal(reelText(`rl-p${i + 1}`), app.GUIDE_POSTURES[key].name.replace(/^./, (c) => c.toUpperCase()));
    assert.equal(reelText(`rl-p${i + 1}v`), `${band[0]}-${band[1]}%`);
  }
  assert.equal(reelText('rl-dial-v'), '50-70%');
  // Discover: about 400 names, the page's eight gates in order.
  assert.equal(app.REEL_FIELD.cut.reduce((a, b) => a + b, 0), 400);
  assert.equal(app.REEL_FIELD.cols * app.REEL_FIELD.rows, 400);
  const gates = $$('#view-guide .hw-gate b').map((b) => b.textContent);
  for (let g = 1; g <= 8; g += 1) assert.equal(reelText(`rl-gl${g}`), `${g} ${gates[g - 1]}`);
  // Research: the hero's example call.
  assert.equal(reelText('rl-ring-v'), '68');
  assert.match($('#rl-card').textContent, /Risk grade\s*B.*Insider selling\s*Clear.*Reward\/risk\s*2\.0 : 1.*Price data\s*Current/s);
  assert.equal(app.guideDecide({ score: 68 }).label.toUpperCase(), $('#rl-stamp').textContent.trim());
  // Picks: the calculator's worked example.
  const r = app.guideSize({});
  assert.equal(reelText('rl-e1'), `$${r.value.toLocaleString('en-US')}`);
  assert.equal(reelText('rl-e2'), `x ${+(r.stopPct * 100).toFixed(1)}%`);
  assert.equal(reelText('rl-e3'), `= $${Math.round(r.atRisk)} at risk`);
  assert.equal(reelText('rl-e4'), `${r.shares} shares`);
  // The risk box is drawn to the same scale as the page's plot.
  const g = app.guideGeometry(25000, r);
  assert.equal(Number($('#rl-fill').getAttribute('width')), Math.round(g.fillW * 520));
  assert.equal(Number($('#rl-budget').getAttribute('width')), Math.round(g.budgetW * 520));
  // My Portfolio: the example book's weights against the 10% and 20% lines.
  const weights = $$('.hw-bar').map((b) => Number(b.style.getPropertyValue('--w')));
  const bars = [1, 2, 3, 4, 5, 6].map((i) => Number($(`#rl-b${i}`).getAttribute('width')));
  assert.deepEqual(bars, weights.map((w) => Math.round((w / 25) * 460)));
  assert.equal(Number($('#rl-l10').getAttribute('d').match(/M(\d+)/)[1]), 140 + (10 / 25) * 460);
  assert.equal(Number($('#rl-l20').getAttribute('d').match(/M(\d+)/)[1]), 140 + (20 / 25) * 460);
  assert.match($('#rl-order').textContent, /place_equity_order/);
  // End card: the five pages, as the navigation names them.
  assert.deepEqual([1, 2, 3, 4, 5].map((i) => reelText(`rl-pg${i}`)), $$('.nav-tabs .nav-tab').map((b) => b.getAttribute('aria-label') || b.textContent.trim()));
});

test('the downloadable reel exists and is a real WebM', () => {
  const href = $('.hw-reel-dl').getAttribute('href');
  assert.match(href, /img\/guide\/quanta-reel\.webm/);
  const file = path.join(root, 'static', 'img', 'guide', 'quanta-reel.webm');
  assert.ok(fs.existsSync(file), 'rendered by tests/tools/render_reel.cjs');
  const head = fs.readFileSync(file).subarray(0, 4);
  assert.deepEqual([...head], [0x1a, 0x45, 0xdf, 0xa3], 'EBML (WebM) header');
  assert.ok(fs.statSync(file).size < 6 * 1024 * 1024, 'under 6 MB');
});

// ---------------------------------------------------------------------------
// Accuracy: every figure the guide states is read back from the source.
// Skipped only when the engine (core/) is not beside the webapp.
// ---------------------------------------------------------------------------
const engineRoot = path.join(root, '..', 'core');
const haveEngine = fs.existsSync(path.join(engineRoot, 'SP_ELITE_PRO.py'));
const src = (rel) => fs.readFileSync(path.join(engineRoot, rel), 'utf8');
const appPy = fs.readFileSync(path.join(root, 'app.py'), 'utf8');
const num = (text, re, label) => {
  const m = text.match(re);
  assert.ok(m, `${label} not found in the source`);
  return Number(m[1].replace(/_/g, ''));
};
const pct = (v) => `${+(v * 100).toFixed(2)}%`;

// Every number below comes from engine/thresholds.py (the committed fixture that a
// Python test keeps equal to it). The engine reads the same module, so checking
// the page against T checks it against the rule (audit F9).
const band = (name) => T.LADDER.SCORE_BANDS.find(([, n]) => n === name)[0];

test('sizing constants in the calculator and the copy match the thresholds', () => {
  const S = T.SIZING;
  const G = app.GUIDE_SIZE;
  assert.equal(G.risk, S.TARGET_RISK_PER_TRADE);
  assert.equal(G.riskStrong, S.TARGET_RISK_STRONG_BUY);
  assert.equal(G.ceiling, S.MAX_RISK_PER_TRADE);
  assert.equal(G.ceilingStrong, S.MAX_RISK_STRONG_BUY);
  assert.equal(G.maxPos, S.MAX_POSITION_PCT);
  assert.equal(G.small, S.SMALL_ACCOUNT_THRESHOLD);
  assert.equal(G.smallMaxPos, S.SMALL_ACCOUNT_MAX_POSITION_PCT);
  assert.equal(G.minTrade, S.MIN_POSITION_SIZE);
  assert.deepEqual(G.callMult, { strong: S.SIGNAL_SIZE_MULT.STRONG_BUY, buy: S.SIGNAL_SIZE_MULT.BUY, accumulate: S.SIGNAL_SIZE_MULT.ACCUMULATE });
  assert.equal(G.scoreLo, S.SCORE_MULT_BASE); assert.equal(G.scoreSpan, S.SCORE_MULT_SPAN);
  assert.equal(G.grade.C, S.RISK_GRADE_SIZE_MULTIPLIERS.C);
  assert.deepEqual(G.mandate, S.MANDATE_SCALE);
  if (haveEngine) {
    // The scanner sizes from the same module: its constants are aliases of it.
    const pro = src('SP_ELITE_PRO.py');
    assert.match(pro, /TARGET_RISK_PER_TRADE: float = _T\.alias\(_T\.SIZING, "TARGET_RISK_PER_TRADE"\)/);
    assert.match(pro, /signal_mult = _T\.SIZING\.SIGNAL_SIZE_MULT\.get\(signal/);
    assert.match(pro, /FRACTIONAL_SHARES: bool = False/, 'whole shares');
  }
  const text = guideText();
  const small = G.small.toLocaleString('en-US');
  const smallSector = T.BOOK.SECTOR_CAP_PROFILE_PCT[T.BOOK.SECTOR_CAP_PROFILE];
  assert.match(text, new RegExp(`${pct(G.maxPos)} most in one stock, up to ${pct(G.smallMaxPos)} for accounts under \\$${small}`));
  assert.match(text, new RegExp(`${pct(T.BOOK.MAX_SECTOR_PCT)} most in one sector, or ${pct(smallSector)} for accounts under \\$${small}`));
  assert.match(text, new RegExp(`${pct(G.ceiling)} hard ceiling on capital at risk per trade \\(${pct(G.ceilingStrong)} for a Strong Buy\\)`));
  assert.match(text, new RegExp(`${pct(G.risk)} of capital \\(${pct(G.riskStrong)} for a Strong Buy\\)`));
  assert.match(text, new RegExp(`funds ${Math.round(G.mandate.conservative * 100)}% for Conservative`));
  // [I5] The minimum scales with capital (A06): a share of capital between the floor and the cap.
  assert.match(text, new RegExp(`minimum trade of ${pct(G.minPct)} of capital, at least \\$${G.minFloor} and at most \\$${G.minTrade}`));
});

test('the scan gates and reward/risk figures match the thresholds', () => {
  const text = guideText();
  const rrScan = rrText(T.RR.SCANNER_MIN);
  const rrResearch = rrText(T.RR.RESEARCH_MIN);
  assert.match(text, new RegExp(`Reward/risk ${rrScan}`));
  assert.match(text, new RegExp(`${rrScan} reward/risk for a Buy or Accumulate call`));
  assert.equal(app.GUIDE_MIN_RR, T.RR.RESEARCH_MIN);
  assert.match(text, new RegExp(`Reward/risk below ${rrResearch}`));
  assert.match(text, new RegExp(`reward/risk of ${rrScan} \\(Research needs ${rrResearch}\\)`));
  assert.match(text, new RegExp(`At least ${band('ACCUMULATE')}; it rises`));
  assert.match(text, new RegExp(`over ${T.LADDER.DCF_OVERVALUATION_CAP_PCT}% above (its )?DCF value`));
  if (haveEngine) {
    const pro = src('SP_ELITE_PRO.py');
    assert.match(pro, /MIN_RR = TradingConfig\.RISK_REWARD_MIN\r?\n/, 'gate 4 uses the flat minimum');
    assert.match(pro, /RISK_REWARD_MIN: float = _T\.alias\(_T\.RR, "SCANNER_MIN"\)/);
    assert.match(src('engine/types.py'), /min_rr_for_buy: float = field\(default_factory=lambda: RR\.RESEARCH_MIN\)/);
    assert.match(src('engine/risk_overrides.py'), /cap = LADDER\.DCF_OVERVALUATION_CAP_PCT/);
  }
  // Funnel labels are the scanner's own (static/elite-pro.js), costs from config.
  const funnel = fs.readFileSync(path.join(root, 'static', 'elite-pro.js'), 'utf8');
  for (const k of ["k: 'Actionable'", "k: 'Risk A\\u2013D'", "k: 'Cost-viable'"]) assert.ok(funnel.includes(k), k);
  assert.match(funnel, /proThr\('BOOK\.COST_VIABILITY_MULTIPLE'\)/);
  assert.match(text, new RegExp(`Expected return at least ${T.BOOK.COST_VIABILITY_MULTIPLE}\\s?x round-trip cost`));
});

test('regime weights match the Research engine', { skip: !haveEngine && 'engine source not present' }, () => {
  const ta = src('TradingAnalysis_ELITE.py');
  const block = ta.slice(ta.indexOf('REGIME_WEIGHTS = {'));
  for (const [key, name] of [['sideways', 'SIDEWAYS'], ['bull', 'BULL'], ['bear', 'BEAR'], ['high_vol', 'HIGH_VOL']]) {
    const m = block.match(new RegExp(`"${name}": \\{([^}]+)\\}`));
    assert.ok(m, name);
    const w = Object.fromEntries([...m[1].matchAll(/"(\w+)": ([\d.]+)/g)].map((x) => [x[1], Number(x[2])]));
    assert.deepEqual(w, app.GUIDE_REGIME_WEIGHTS[key], name);
  }
  assert.match(ta, /return \{key: value \/ total for key, value in raw\.items\(\)\}/, 'the engine normalises them');
  // The blocked names in the read-only map are refused by the MCP guard.
  const guard = src('brokers/mcp_http.py').match(/WRITE_TOOL = re\.compile\(([\s\S]*?)\)\s*\n/)[1];
  for (const word of ['place', 'review', 'cancel', 'submit', 'transfer', 'withdraw', 'deposit']) assert.ok(guard.includes(word), word);
});

test('score bands and the holder rule match the thresholds', () => {
  const floors = T.LADDER.SCORE_BANDS.map(([f]) => f);
  assert.deepEqual(floors.slice(0, 5), [band('STRONG_BUY'), band('BUY'), band('ACCUMULATE'), band('HOLD'), band('REDUCE')]);
  for (const name of ['STRONG_BUY', 'BUY', 'ACCUMULATE', 'HOLD']) {
    const floor = band(name);
    const tick = $$('#view-guide .hw-ticks span').find((s) => Number(s.textContent) === floor);
    assert.ok(tick, `tick ${floor}`);
    assert.equal(Number(tick.style.getPropertyValue('--x')), floor, `tick ${floor} sits at its value`);
  }
  assert.equal(app.GUIDE_EXIT_FLOOR, band('REDUCE'));
  if (haveEngine) {
    assert.match(src('engine/score_bands.py'), /SCORE_BANDS = LADDER\.SCORE_BANDS/);
    // Below the hold floor, a non-holder's call is Avoid, not Watchlist.
    assert.match(src('engine/decision_engine.py'), /A bearish \(AVOID\) call stays AVOID for a non-holder/);
    // Exit needs a corroborating flag; the lab's flags are in the engine's set.
    const block = src('engine/blockers.py').match(/_EXIT_CORROBORATION = \{([^}]+)\}/)[1];
    for (const code of ['HEAVY_INSIDER_SELLING', 'BEARISH_REVERSAL', 'DCF_OVERVALUATION']) assert.ok(block.includes(code), code);
  }
});

test('posture bands match the Overview playbook', () => {
  // One source: MARKET.EXPOSURE_BANDS feeds app.py's playbook and the Guide.
  const B = globalThis.__QUANTA_THRESHOLDS__.MARKET.EXPOSURE_BANDS;
  const key = (posture) => {
    const i = appPy.indexOf(posture);
    assert.ok(i > 0, posture);
    return appPy.slice(i).match(/"band": _band_text\("(\w+)"\)/)[1];
  };
  assert.deepEqual(app.GUIDE_POSTURES.offensive.band, B[key('if posture in {"Offensive", "Selective Offensive"}')]);
  assert.deepEqual(app.GUIDE_POSTURES['lean-long'].band, B[key('elif posture == "Lean Long"')]);
  assert.deepEqual(app.GUIDE_POSTURES.neutral.band, B[key('elif posture == "Neutral"')]);
  const defensive = appPy.slice(appPy.indexOf('elif posture == "Neutral"')).match(/else:[\s\S]*?"band": _band_text\("(\w+)"\)/)[1];
  assert.deepEqual(app.GUIDE_POSTURES.defensive.band, B[defensive]);
  assert.match(appPy, /cap_band = _band_text\("cap_stale" if stale else "cap_low_confidence"\)/);
  assert.deepEqual(app.GUIDE_POSTURE_CAP.stale, B.cap_stale);
  assert.deepEqual(app.GUIDE_POSTURE_CAP.low, B.cap_low_confidence);
  for (const label of ['Offensive', 'Selective Offensive', 'Lean Long', 'Neutral', 'Lean Defensive', 'Selective Defensive', 'Defensive']) {
    assert.ok(appPy.includes(`"${label}"`), `posture ${label}`);
  }
});

test('My Portfolio limits, read-only tools and the ledger match the thresholds and the source', { skip: !haveEngine && 'engine source not present' }, () => {
  const P = T.PORTFOLIO;
  const text = guideText();
  assert.match(src('portfolio/analytics.py'), /max_single_position: float = field\(default_factory=lambda: _PF\.MAX_SINGLE_POSITION\)/);
  assert.match(text, new RegExp(`${pct(P.MAX_SINGLE_POSITION)} no new money`));
  assert.match(text, new RegExp(`${pct(P.TRIM_REVIEW_POSITION)} trim review`));
  assert.match(text, new RegExp(`over the ${pct(P.MAX_SECTOR)} sector limit`));
  assert.match(text, new RegExp(`one stock reaches ${pct(P.MAX_SINGLE_POSITION)} of the account, when its sector or a group of holdings that move together reaches ${pct(P.MAX_SECTOR)}`));
  assert.match(text, new RegExp(`earnings report is due within ${P.EARNINGS_WINDOW_DAYS} days`));
  assert.match(text, /The guidance, grouped from Exit Review to Hold/);
  // Read-only: the allowlist is what the copy says, and execution is off in the source.
  const tools = src('brokers/robinhood.py').match(/READ_TOOLS: Dict\[str, str\] = \{([^}]+)\}/)[1];
  for (const t of ['get_accounts', 'get_equity_positions', 'get_equity_quotes', 'get_equity_tax_lots', 'get_equity_fundamentals', 'get_earnings_calendar', 'get_realized_pnl']) assert.ok(tools.includes(t), t);
  // Order *history* is a read; no tool that places, reviews, previews or cancels an order is listed.
  assert.doesNotMatch(tools, /(place|review|preview|cancel|replace)_\w*order/);
  assert.match(src('portfolio/execution.py'), /^EXECUTION_ENABLED = False$/m);
  assert.match(src('brokers/oauth.py'), /Tokens live only in this object's memory/);
  // Ledger: 30 calls on 10+ dates, SPY or ^NSEI for .NS only.
  const ledger = src('tracking/ledger.py');
  assert.match(ledger, /MIN_SAMPLE = _EVIDENCE\.MIN_SAMPLE/);
  assert.match(text, new RegExp(`Only after ${T.EVIDENCE.MIN_SAMPLE} measured calls on at least ${T.EVIDENCE.MIN_DATES} separate dates`));
  assert.match(ledger, /"\^NSEI" if decision\.ticker\.upper\(\)\.endswith\("\.NS"\)/);
  // Scan universes and the custom-list limit.
  // The engine takes its US preset sizes from UNIVERSE config, the one source the menu reads.
  const pro = src('SP_ELITE_PRO.py');
  assert.match(pro, /\*\*_T\.UNIVERSE\.US_SCAN_LIMITS/);
  for (const [k, label] of [['quick', 'Quick'], ['standard', 'Standard'], ['comprehensive', 'Comprehensive']]) {
    const n = T.UNIVERSE.US_SCAN_LIMITS[k];
    assert.match($('.hw-universes').textContent, new RegExp(`${label}, ${n}(?!\\d)`));
  }
  // [Picks revamp] Every Picks menu option that states a cap states the engine's cap,
  // filled from config when the page applies its thresholds.
  require('../../static/app.js').applyThresholds(document);
  const capOf = (k) => (k.startsWith('india_') ? T.UNIVERSE.INDIA_SCAN_LIMITS[k] : T.UNIVERSE.US_SCAN_LIMITS[k]);
  const capped = [...document.querySelectorAll('#sp-scan option')].filter((o) => /\d+ most liquid/.test(o.textContent));
  assert.ok(capped.length >= 12, 'the menu states its caps');
  for (const o of capped) assert.equal(Number(o.textContent.match(/(\d+) most liquid/)[1]), Number(capOf(o.value)), o.value);
  assert.match(appPy, /len\(custom_tickers\) > C\.MAX_CUSTOM_TICKERS/);
  assert.match(text, new RegExp(`Your own list, up to ${T.CAPITAL.MAX_CUSTOM_TICKERS}`));
  // Loopback only.
  assert.match(appPy, /non-loopback host\. Deploy behind authenticated access control\./);
});

// ---------------------------------------------------------------------------
// Design rules
// ---------------------------------------------------------------------------
test('the guide follows its design rules', () => {
  const css = fs.readFileSync(path.join(root, 'static', 'quanta.css'), 'utf8');
  const start = css.lastIndexOf('/*', css.indexOf('HOW QUANTA WORKS (#guide)'));
  const block = css.slice(start, css.indexOf('Off-scale chart levels', start));
  assert.doesNotMatch(block, /infinite/, 'nothing loops');
  assert.doesNotMatch(block, /--violet/);
  assert.doesNotMatch(block, /background-clip:\s*text/, 'no gradient-clipped text');
  assert.doesNotMatch(block.replace(/[/][*][^]*?[*][/]/g, ''), /\d+vh/, 'no viewport-height spacing');
  assert.doesNotMatch(block, /backdrop-filter/, 'no glass');
  assert.doesNotMatch(block, /hw-orb|hw-glow|hw-hero-bg|hw-end-bg/, 'no decorative glows');
  // The Floor Rule: no page text below 12px (the reel's SVG type scales with the stage).
  for (const line of block.replace(/[/][*][^]*?[*][/]/g, '').split('\n')) {
    if (/^\s*\.rl-/.test(line)) continue;
    const m = line.match(/font-size:\s*(\d+(?:\.\d+)?)px/);
    if (m) assert.ok(Number(m[1]) >= 12, `below the 12px floor: ${line.trim().slice(0, 80)}`);
  }
  assert.match(block, /@media \(prefers-reduced-motion: reduce\) \{\s*\.hw-progress \{ display: none !important; \}\s*\.hw \*, \.hw \*::before, \.hw \*::after \{ animation: none !important; transition: none !important; \}/);
  // Every keyframe animation is gated behind .hw-motion.
  const bare = block.replace(/[/][*][^]*?[*][/]/g, '');
  let gated = 0;
  for (const [, sel, body] of bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/animation(-name)?:\s*hw-/.test(body)) continue;
    for (const s of sel.split(',')) assert.match(s, /\.hw-motion/, `ungated animation: ${s.trim()}`);
    gated += 1;
  }
  assert.ok(gated >= 8, 'the motion rules were found');
  const bind = app.bindGuide.toString();
  assert.doesNotMatch(bind, /addEventListener\(\s*'scroll'/, 'no scroll listener');
  assert.doesNotMatch(bind, /requestAnimationFrame/);

  // Hero: headline, promise (20 words or fewer), actions; then the example.
  // No eyebrow above the headline (DESIGN.md).
  const copy = $('#view-guide .hw-hero-copy');
  assert.deepEqual([...copy.children].map((c) => c.className.split(' ')[0]), ['hw-title', 'hw-lede', 'hw-cta']);
  assert.ok($('#view-guide .hw-lede').textContent.trim().split(/\s+/).length <= 20);
  // No em or en dashes in visible copy; at most one middle dot per line.
  assert.doesNotMatch($('#view-guide').textContent, /[–—]/);
  for (const el of $$('#view-guide p, #view-guide li, #view-guide span, #view-guide dd')) {
    if (el.children.length) continue;
    assert.ok((el.textContent.match(/·/g) || []).length <= 1, `one middle dot at most: "${el.textContent}"`);
  }
  // One label per call-to-action intent.
  const labels = new Map();
  for (const a of $$('#view-guide .hw-btn[data-view-link]')) {
    const prev = labels.get(a.dataset.viewLink);
    if (prev) assert.equal(a.textContent, prev, `one label for ${a.dataset.viewLink}`);
    labels.set(a.dataset.viewLink, a.textContent);
  }
  // Every interactive control is a real control with a name.
  for (const b of $$('#view-guide button')) assert.ok(b.textContent.trim() || b.getAttribute('aria-label'), 'named button');
  for (const i of $$('#view-guide input, #view-guide select')) assert.ok(i.closest('label') || $(`label[for="${i.id}"]`), `labelled ${i.id || i.name}`);
});
