/**
 * Research tear sheet, presentation (2026-10-29): every % on Key levels is from
 * the entry; R/R is floored; a funding clear price below the stop says what the
 * stop and target would be re-set to; the levels' basis line; the provisional
 * banner and the live-preview callout for today's excluded bar; neutral-filled
 * score legs; the volume basis; and the same-day filing reaction (events.js).
 *
 * Run: node --require ./tests/helpers/thresholds_setup.cjs --test tests/unit/research_presentation.test.cjs
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'index.html'), 'utf8');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:5500/' });
global.document = dom.window.document;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.requestAnimationFrame = () => 0;
global.window = undefined;
const app = require('../../static/app.js');
const events = require('../../static/events.js');

const frag = (h) => { const el = document.createElement('div'); el.innerHTML = h; return el; };

test('fmtRR floors to the configured decimals and never rounds up', () => {
  assert.equal(app.fmtRR(1.999), '1.99:1');
  assert.equal(app.fmtRR(1.8), '1.80:1');
  assert.equal(app.fmtRR(1.5502), '1.55:1');
  assert.equal(app.fmtRR(null), '--');
});

test('a funding clear price below the stop says so, with the re-set stop and target', () => {
  const levels = {
    rr_hurdles: { research: { min: 1.5, passes: true }, funding: { min: 1.8, passes: false, clear_entry: 256.93 } },
    valuation_target: 300, target_horizon_days: 63, valuation_target_horizon_days: 126,
  };
  const el = frag(app.levelNotesHtml({ levels, entry: 282.32, stop: 260.13, target: 310, rr: 1.4, technical: {}, sym: '$' }));
  const funding = el.querySelector('[data-hurdle="funding"]');
  assert.ok(funding, 'funding hurdle shown');
  const text = funding.textContent;
  assert.match(text, /reached at or below \$256\.93\./);
  assert.match(text, /That is below today’s stop \(\$260\.13\)/);
  // stop keeps the entry-to-stop percentage: 256.93 x 260.13 / 282.32 = 236.74
  assert.match(text, /re-set the stop to about \$236\.74/);
  // target = clear + (63/126) * (300 - clear) = 256.93 + 21.535 = 278.465
  assert.match(text, /re-set the stop to about \$236\.74 and the target to about \$278\.4/);
});

test('without a valuation target the caveat names the stop only', () => {
  const levels = { rr_hurdles: { funding: { min: 1.8, passes: false, clear_entry: 256.93 } } };
  const el = frag(app.levelNotesHtml({ levels, entry: 282.32, stop: 260.13, target: 310, rr: 1.4, technical: {}, sym: '$' }));
  const text = el.querySelector('[data-hurdle="funding"]').textContent;
  assert.match(text, /re-set the stop to about \$236\.74\./);
  assert.ok(!/target to about/.test(text));
});

test('the buy-zone caveat is empty when the clear price sits above the stop', () => {
  const levels = { rr_hurdles: { funding: { min: 1.8, passes: false, clear_entry: 270 } } };
  assert.equal(app.buyZoneCaveat(levels, 282.32, 260.13, '$'), '');
  const below = { rr_hurdles: { funding: { min: 1.8, passes: false, clear_entry: 256.93 } } };
  assert.match(app.buyZoneCaveat(below, 282.32, 260.13, '$'), /^Below today’s stop \(\$260\.13\): at that price QUANTA would re-set the stop to about \$236\.74/);
});

test('the levels basis line names the set time, the reference price and the live price', () => {
  const el = frag(app.levelBasisHtml({ analysis_timestamp: '2026-10-29T14:05:00Z' }, { reference_price: 281.76 }, '$', 283.58));
  const line = el.querySelector('[data-levels-basis]');
  assert.ok(line, 'basis line rendered');
  assert.match(line.textContent, /Levels set at /);
  assert.match(line.textContent, /from \$281\.76; live price now \$283\.58/);
  assert.ok(el.querySelector('[data-levels-live]'), 'live figure is updatable by the header quote');
});

test('no reference price, no basis line', () => {
  assert.equal(app.levelBasisHtml({}, {}, '$', 283.58), '');
});

test('the provisional banner shows only when today’s large move is excluded', () => {
  const large = app.provisionalBannerHtml({ live_preview: { large_move: true, day_change_pct: -12.8 } });
  assert.match(large, /Provisional:/);
  assert.match(large, /today’s −12\.8% move is not in this call yet; it is re-scored after the close\./);
  assert.equal(app.provisionalBannerHtml({ live_preview: { large_move: false, day_change_pct: 1.2 } }), '');
  assert.equal(app.provisionalBannerHtml({}), '');
});

test('the live-preview callout names the move, its multiple and the RSI only when it exists', () => {
  const t = {
    indicator_basis: { scored_through: '2026-10-28' },
    live_preview: { day_change_pct: -12.8, daily_sigma_pct: 2.13, large_move: true, rsi: 22 },
  };
  const el = frag(app.livePreviewHtml(t, '$'));
  const text = el.textContent;
  assert.match(text, /Today: −12\.8% \(a large move, 6× a normal day\)\./);
  assert.match(text, /scored through the Oct 28 close and do not include it yet/);
  assert.match(text, /With today included, RSI would be 22 \(oversold\)\./);
  assert.match(text, /QUANTA re-scores after the close\./);

  const noRsi = frag(app.livePreviewHtml({ indicator_basis: { scored_through: '2026-10-28' }, live_preview: { day_change_pct: 0.4, large_move: false } }, '$')).textContent;
  assert.ok(!/RSI would be/.test(noRsi), 'no RSI sentence without an RSI');
  assert.ok(!/large move/.test(noRsi), 'no large-move wording unless it is one');
});

test('no live preview, no callout', () => {
  assert.equal(app.livePreviewHtml({}, '$'), '');
});

test('volume is labelled with its session when the basis is the last completed session', () => {
  const note = app.volumeNoteHtml({ volume_ratio_basis: 'last completed session', indicator_basis: { scored_through: '2026-10-28' } });
  assert.match(note, /Oct 28 session/);
  assert.equal(app.volumeNoteHtml({ volume_ratio_basis: 'intraday' }), '');
});

test('a neutral-filled leg reads n/m with its weight; Risk is sizing only', () => {
  const scores = { neutral_filled: ['valuation'], weights_used: { valuation: 0.17, technical: 0.3 } };
  const v = frag(app.scoreLeg(scores, 'Valuation', null, '')).textContent;
  assert.match(v, /n\/m/);
  assert.match(v, /counted as 50 · 17% weight/);
  const tech = frag(app.scoreLeg(scores, 'Technical', 61.2)).textContent;
  assert.match(tech, /61\.2/);
  assert.ok(!/counted as 50/.test(tech));
  const risk = frag(app.scoreLeg({}, 'Risk', 40)).textContent;
  assert.match(risk, /sizing only, not in the composite/);
});

test('a same-day filing shows the reaction so far against the typical move', () => {
  const ev = { reaction_session: '2026-10-29', items: [{ code: '8.01' }] };
  const study = { '8.01': { windows: { 5: { mean_abs_pct: 5.3, too_few: false } } } };
  const line = events.liveReactionLine(ev, study, { date: '2026-10-29', dayChangePct: -12.8 });
  assert.match(line, /Reaction so far today: <b>-12\.8%<\/b> \(typical move for this kind of filing ±5\.3%\)/);
  assert.equal(events.liveReactionLine(ev, study, { date: '2026-10-28', dayChangePct: -12.8 }), '', 'a past reaction is not "today"');
  assert.equal(events.liveReactionLine(ev, study, { date: '2026-10-29', dayChangePct: null }), '');
});

test('the reaction line escapes everything it shows', () => {
  const ev = { reaction_session: '2026-10-29', items: [{ code: '<img src=x>' }] };
  const line = events.liveReactionLine(ev, {}, { date: '2026-10-29', dayChangePct: 1 });
  assert.ok(!line.includes('<img'));
});
