/* Plan lab: the plan card on the Research page, in three plain questions.
 *
 * 1. Your plan: the ticker, shares, buy price, stop and target (pre-filled from
 *    the Research Decision on screen, read from `state.analysis`), each price
 *    with its percent from the buy price, and a one-line dollar summary.
 * 2. Does it fit the rules? The Plan Check: the headline, then only the checks
 *    that block or warn. Passed and skipped checks collapse to one line each.
 * 3. How often would this have worked? The Plan Simulator: the odds in plain
 *    words, a stacked bar with the break-even marked, and this stock's history.
 *    Everything technical sits under "How this is calculated".
 *
 * Every server string is escaped before it reaches the DOM. QUANTA never places
 * orders; the card only reports checks and simulated odds.
 *
 * Loads in the browser as window.QuantaPlanLab, and under node as a module
 * (tests/unit/plan_lab.test.cjs). */
(function (root) {
  'use strict';

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => ESC[c]);
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const MINUS = '−';
  const pct = (v, dp = 0) => (isNum(v) ? `${(v * 100).toFixed(dp)}%` : 'n/a');
  const rText = (v) => (isNum(v) ? `${v >= 0 ? '+' : '-'}${Math.abs(v).toFixed(2)}R` : 'n/a');
  const money = (v, dp = 2) => (isNum(v) ? `$${v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}` : 'n/a');
  // Ratios are floored, never rounded up, as the server floors them.
  const floor2 = (v) => (isNum(v) ? (Math.floor(v * 100 + 1e-9) / 100).toFixed(2) : 'n/a');
  const signedPct = (v) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? MINUS : ''}${Math.abs(v).toFixed(1)}%` : '');

  const TICKER_RE = /^[A-Z0-9^.=&-]{1,32}$/;
  const HORIZON_MIN = 5;
  const HORIZON_MAX = 126;
  const HORIZON_DEFAULT = 63;
  const POST_HEADERS = Object.freeze({ 'X-QUANTA-Request': '1' });

  // Plain titles for each check. The server's own `title` wins; these cover
  // payloads that predate it. An id never reaches the page as text.
  const CHECK_LABEL = {
    geometry: 'Stop and target order',
    rr: 'Net reward to risk',
    dollar_risk: 'Dollar risk per trade',
    position_size: 'Position size',
    concentration_name: 'Single-name limit',
    concentration_sector: 'Sector limit',
    earnings: 'Earnings date',
    fomc: 'Fed decision',
    decision: 'QUANTA’s call',
    todays_move: 'Today’s move',
    material_filings: 'Recent SEC filings',
  };
  // What a skipped check needs from the user; shown in brackets on the summary line.
  const SKIP_HINT = {
    dollar_risk: 'add account value',
    position_size: 'add account value',
    concentration_name: 'add account value or connect My Portfolio',
    concentration_sector: 'add account value or connect My Portfolio',
  };
  const STATUS_TONE = { PASS: 'tone-bull', WARN: 'tone-warn', BLOCK: 'tone-bear', SKIPPED: '' };
  const OVERALL_WORD = { PASS: 'Fits the rules', WARN: 'Needs a look', BLOCK: 'Blocked', SKIPPED: 'Not fully checked' };
  const VERDICT_TONE = {
    'Below break-even': 'tone-bear',
    'Above break-even': 'tone-bull',
    'Close to break-even': 'tone-warn',
    'Not distinguishable from break-even': 'tone-warn',
  };
  // The plain wording for each technical verdict; the technical label stays in a tooltip.
  const VERDICT_PLAIN = {
    'Below break-even': 'Below break-even',
    'Above break-even': 'Above break-even',
    'Close to break-even': 'Close to break-even',
    'Not distinguishable from break-even': 'Too close to call',
  };

  const statusKey = (s) => (Object.prototype.hasOwnProperty.call(STATUS_TONE, s) ? s : 'SKIPPED');
  const titleOf = (c) => (c && typeof c.title === 'string' && c.title.trim() ? c.title.trim() : (CHECK_LABEL[c && c.id] || 'Plan check'));

  function emptyState(text) {
    return `<p class="pl-empty">${esc(text)}</p>`;
  }

  function errorState(text) {
    return `<p class="pl-error" role="alert">${esc(text)}</p>`;
  }

  // The one-line answer for the checklist: the server's headline, else a count.
  function checkHeadline(res, counts) {
    const h = res.summary && typeof res.summary.headline === 'string' ? res.summary.headline.trim() : '';
    if (h) return h;
    if (counts.BLOCK) return `${counts.BLOCK} ${counts.BLOCK === 1 ? 'rule blocks' : 'rules block'} this plan.`;
    if (counts.WARN) return `${counts.WARN} ${counts.WARN === 1 ? 'check warns' : 'checks warn'}. Read ${counts.WARN === 1 ? 'it' : 'them'} before you buy.`;
    if (counts.SKIPPED) return 'Nothing blocks this plan, but some checks could not run.';
    return 'Every check passed.';
  }

  function countChecks(checks) {
    const counts = { PASS: 0, WARN: 0, BLOCK: 0, SKIPPED: 0 };
    checks.forEach((c) => { counts[statusKey(String(c.status || ''))] += 1; });
    return counts;
  }

  function renderChecks(res) {
    if (!res || !Array.isArray(res.checks)) return emptyState('Run the check to see the result here.');
    const checks = res.checks.filter((c) => c && typeof c === 'object');
    const counts = countChecks(checks);
    const overall = statusKey(String(res.overall || ''));
    const rank = { BLOCK: 0, WARN: 1 };
    const problems = checks
      .filter((c) => rank[statusKey(String(c.status || ''))] !== undefined)
      .sort((a, b) => rank[statusKey(String(a.status || ''))] - rank[statusKey(String(b.status || ''))]);
    const passes = checks.filter((c) => statusKey(String(c.status || '')) === 'PASS');
    const skipped = checks.filter((c) => statusKey(String(c.status || '')) === 'SKIPPED');

    const problemRows = problems.map((c) => {
      const st = statusKey(String(c.status || ''));
      const word = st === 'BLOCK' ? 'Blocks the plan' : 'Warning';
      const next = typeof c.next_step === 'string' ? c.next_step.trim() : '';
      return `<li class="pl-problem is-${st === 'BLOCK' ? 'block' : 'warn'}">
        <div class="pl-problem-head"><span class="chip ${STATUS_TONE[st]}">${esc(word)}</span><strong>${esc(titleOf(c))}</strong></div>
        ${c.message ? `<p class="pl-problem-msg">${esc(c.message)}</p>` : ''}
        ${next ? `<p class="pl-problem-next"><span class="pl-kicker">What to do</span> ${esc(next)}</p>` : ''}
      </li>`;
    }).join('');

    const passLine = passes.length
      ? `<details class="pl-more"><summary>✓ ${passes.length} ${passes.length === 1 ? 'check' : 'checks'} passed</summary>
          <ul class="pl-plain-list">${passes.map((c) => `<li>${esc(titleOf(c))}${c.message ? ` <span class="pl-muted">${esc(c.message)}</span>` : ''}</li>`).join('')}</ul></details>`
      : '';

    // Skipped checks group by what would let them run: "Dollar risk, Position size (add account value)".
    const groups = [];
    skipped.forEach((c) => {
      const hint = SKIP_HINT[c.id] || '';
      let g = groups.find((x) => x.hint === hint);
      if (!g) { g = { hint, titles: [] }; groups.push(g); }
      g.titles.push(titleOf(c));
    });
    const skipText = groups.map((g) => `${g.titles.join(', ')}${g.hint ? ` (${g.hint})` : ''}`).join(' · ');
    const skipLine = skipped.length
      ? `<details class="pl-more pl-skipped"><summary>${skipped.length} couldn’t be checked: ${esc(skipText)}</summary>
          <ul class="pl-plain-list">${skipped.map((c) => `<li>${esc(titleOf(c))}${c.message ? ` <span class="pl-muted">${esc(c.message)}</span>` : ''}</li>`).join('')}</ul></details>`
      : '';

    const sz = res.sizing && isNum(res.sizing.max_shares) ? res.sizing : null;
    const BINDING = { 'risk target': 'the risk-per-trade target', 'position cap': 'the position cap', cash: 'your cash' };
    const sizeLine = sz
      ? `<p class="pl-size">Largest size within your limits: <strong>${esc(sz.max_shares)} ${sz.max_shares === 1 ? 'share' : 'shares'}</strong>, set by ${esc(BINDING[sz.binding] || sz.binding)}.</p>`
      : '';
    const fp = res.fingerprint ? `<p class="pl-fp">Plan fingerprint <code>${esc(res.fingerprint)}</code></p>` : '';
    const notes = (Array.isArray(res.notes) ? res.notes : []).map((n) => `<li>${esc(n)}</li>`).join('');
    const calc = (fp || notes)
      ? `<details class="pl-calc"><summary>About this checklist</summary>${fp}${notes ? `<ul class="pl-notes">${notes}</ul>` : ''}</details>`
      : '';

    return `<div class="pl-verdict-row">
        <span class="chip ${STATUS_TONE[overall]}" title="Overall status: ${esc(overall)}">${esc(OVERALL_WORD[overall])}</span>
        <p class="pl-headline">${esc(checkHeadline(res, counts))}</p>
      </div>
      ${res.incomplete && !(res.summary && res.summary.headline) ? '<p class="pl-incomplete">Some checks could not run, so this is not a full pass.</p>' : ''}
      ${sizeLine}
      ${problems.length ? `<ul class="pl-problems">${problemRows}</ul>` : '<p class="pl-clear">Nothing blocks or warns.</p>'}
      ${passLine}${skipLine}${calc}`;
  }

  // The simulator's odds in whole counts per 100 paths. Uses the server's `plain`
  // block when present; otherwise derives the same counts from the shares.
  function plainOf(res) {
    const s = res.simulation || {};
    const h = res.historical || {};
    const pr = res.plain && typeof res.plain === 'object' ? res.plain : null;
    if (pr && pr.per_100 && typeof pr.per_100 === 'object') return pr;
    const per = (x) => (isNum(x) ? Math.round(x * 100) : null);
    const w = Array.isArray(h.wilson95_effective) && h.wilson95_effective.length === 2 && h.wilson95_effective.every(isNum)
      ? [per(h.wilson95_effective[0]), per(h.wilson95_effective[1])] : null;
    return {
      per_100: { target: per(s.p_target), stop: per(s.p_stop), timeout: per(s.p_timeout) },
      breakeven_per_100: per(s.break_even_share),
      history_per_100: per(h.target_share_decided),
      history_range_per_100: w,
      independent_samples: isNum(h.effective_n) ? h.effective_n : null,
      verdict: (res.verdict && res.verdict.label) || s.simulated_band || '',
    };
  }

  // The short technical label ("Not distinguishable from break-even"); the chip
  // shows its plain word. The server's one-sentence explanation (plain.verdict)
  // is shown under the chip, never inside it.
  function verdictText(p, res) {
    if (res && res.verdict && typeof res.verdict.label === 'string') return res.verdict.label;
    if (p.verdict && typeof p.verdict.label === 'string') return p.verdict.label;
    return '';
  }

  function verdictSentence(p) {
    return typeof p.verdict === 'string' ? p.verdict : '';
  }

  function leadSentence(p) {
    const pc = p.per_100 || {};
    if (!isNum(pc.target) || !isNum(pc.stop)) return 'The simulation did not return odds for this plan.';
    const to = isNum(pc.timeout) ? pc.timeout : 0;
    const base = `Out of 100 simulated paths, about ${pc.target} reached the target first and ${pc.stop} hit the stop${to > 0 ? `, and ${to} timed out` : ''}.`;
    return isNum(p.breakeven_per_100)
      ? `${base} To break even with this reward/risk you need about ${p.breakeven_per_100}.`
      : base;
  }

  function historyLine(p, ticker, sessions) {
    const h = p.history_per_100;
    if (!isNum(h)) return 'This stock’s own history is too short to say how plans like this would have done.';
    const yrs = isNum(sessions) && sessions >= 252 ? Math.round(sessions / 252) : null;
    const span = yrs ? `last ${yrs} year${yrs === 1 ? '' : 's'}` : (isNum(sessions) ? `last ${sessions} sessions` : 'past');
    const r = p.history_range_per_100;
    const samples = isNum(p.independent_samples) ? `, about ${Math.round(p.independent_samples)} independent samples` : '';
    const range = Array.isArray(r) && r.length === 2 && r.every(isNum) ? ` (could be anywhere from ${r[0]} to ${r[1]}${samples})` : '';
    return `On ${ticker || 'this stock'}’s own ${span}, plans shaped like this reached the target first about ${h} times in 100${range}.`;
  }

  // Stacked bar: target, stop, time-out as shares of the paths, the break-even
  // marked. The legend carries the numbers, so the bar is never colour alone.
  function oddsBar(p) {
    const pc = p.per_100 || {};
    const t = isNum(pc.target) ? pc.target : 0;
    const s = isNum(pc.stop) ? pc.stop : 0;
    const o = isNum(pc.timeout) ? pc.timeout : 0;
    const total = t + s + o;
    if (!(total > 0)) return '';
    const w = (x) => ((x / total) * 100).toFixed(1);
    const be = isNum(p.breakeven_per_100) ? clamp((p.breakeven_per_100 / total) * 100, 0, 100).toFixed(1) : null;
    return `<div class="pl-odds" aria-hidden="true">
        <div class="pl-bar">
          <span class="pl-seg pl-seg--target" style="width:${w(t)}%"></span><span class="pl-seg pl-seg--stop" style="width:${w(s)}%"></span><span class="pl-seg pl-seg--time" style="width:${w(o)}%"></span>
          ${be !== null ? `<span class="pl-be-mark" style="left:${be}%"></span>` : ''}
        </div>
        ${be !== null ? `<span class="pl-be-label" style="left:${be}%">Break-even ${esc(p.breakeven_per_100)}</span>` : ''}
      </div>
      <ul class="pl-legend">
        <li><i class="pl-sw pl-sw--target"></i>Reached target <b>${esc(t)}</b></li>
        <li><i class="pl-sw pl-sw--stop"></i>Hit stop <b>${esc(s)}</b></li>
        ${o > 0 ? `<li><i class="pl-sw pl-sw--time"></i>Timed out <b>${esc(o)}</b></li>` : ''}
      </ul>`;
  }

  function renderSimulation(res) {
    if (!res || res.available === false) return errorState(res && res.error ? res.error : 'The simulation is unavailable.');
    const s = res.simulation || {};
    const h = res.historical || {};
    const plan = res.plan || {};
    const p = plainOf(res);
    const verdict = verdictText(p, res);
    const plainVerdict = VERDICT_PLAIN[verdict] || verdict || 'No verdict';
    const tone = VERDICT_TONE[verdict] || '';
    const ticker = String(res.ticker || '').toUpperCase();
    const notes = (Array.isArray(res.notes) ? res.notes : []).map((n) => `<li>${esc(n)}</li>`).join('');
    const vol = res.history || {};
    const volPct = (x) => (isNum(x) ? `${(x * 100).toFixed(1)}%` : 'n/a');
    const volLine = isNum(vol.vol_used_ann)
      ? `${volPct(vol.vol_used_ann)} a year (sample ${volPct(vol.vol_sample_ann)}, EWMA ${volPct(vol.vol_ewma_ann)}${vol.vol_method ? `; method: ${vol.vol_method}` : ''})`
      : 'n/a';
    return `<p class="pl-lead">${esc(leadSentence(p))}</p>
      ${oddsBar(p)}
      <p class="pl-history">${esc(historyLine(p, ticker, h.sessions_tested))}</p>
      <p class="pl-verdict-row"><span class="pl-kicker">Verdict</span> <span class="chip ${tone}" title="${esc(verdict)}">${esc(plainVerdict)}</span></p>
      ${verdictSentence(p) ? `<p class="pl-verdict-why">${esc(verdictSentence(p))}</p>` : ''}
      <p class="pl-forecast">Simulation, not a forecast: it models random paths from this stock’s price history and does not predict what happens next.</p>
      <details class="pl-calc"><summary>How the simulation is calculated</summary>
        <dl class="pl-dl">
          <div><dt>Expected result per trade, net of costs</dt><dd>${esc(rText(s.expected_r))}</dd></div>
          <div><dt>Worst 5% / median / best 5%</dt><dd>${esc(rText(s.r && s.r.p5))} / ${esc(rText(s.r && s.r.median))} / ${esc(rText(s.r && s.r.p95))}</dd></div>
          <div><dt>Planned net reward to risk</dt><dd>${esc(isNum(plan.rr_net) ? floor2(plan.rr_net) : 'n/a')}</dd></div>
          <div><dt>Simulated: target / stop / timed out after ${esc(res.horizon_sessions)} sessions</dt><dd>${esc(pct(s.p_target))} / ${esc(pct(s.p_stop))} / ${esc(pct(s.p_timeout))}</dd></div>
          <div><dt>Volatility used</dt><dd>${esc(volLine)}</dd></div>
          <div><dt>Past sessions tested</dt><dd>${esc(h.sessions_tested ?? 'n/a')}</dd></div>
          <div><dt>History: target / stop / timed out</dt><dd>${esc(h.target ?? 'n/a')} / ${esc(h.stop ?? 'n/a')} / ${esc(h.timeout ?? 'n/a')}</dd></div>
          <div><dt>Independent samples (effective n)</dt><dd>${esc(h.effective_n ?? 'n/a')}</dd></div>
        </dl>
        <p class="pl-r-help">R is your risk per share: the buy price minus the stop. A result of +2R means you made twice what you risked; -1R means the stop was hit.</p>
        ${notes ? `<ul class="pl-notes">${notes}</ul>` : ''}
      </details>`;
  }

  function levelsFromAnalysis(data) {
    if (!data || typeof data !== 'object') return null;
    const ticker = String(data.ticker || '').trim().toUpperCase();
    if (!TICKER_RE.test(ticker)) return null;
    const lv = (data.decision && data.decision.levels) || {};
    const pick = (v) => (isNum(v) && v > 0 ? v : null);
    const hz = Number(lv.target_horizon_days);
    const horizon = Number.isInteger(hz) && hz >= HORIZON_MIN && hz <= HORIZON_MAX ? hz : HORIZON_DEFAULT;
    return { ticker, entry: pick(lv.entry), stop: pick(lv.stop), target: pick(lv.target), horizon };
  }

  function validatePlan(form) {
    const errs = [];
    if (!TICKER_RE.test(form.ticker)) errs.push('Enter a ticker, for example AAPL or RELIANCE.NS.');
    const [entry, stop, target] = [form.entry, form.stop, form.target];
    if (!isNum(entry) || entry <= 0) errs.push('Enter a buy price above zero.');
    if (!isNum(stop) || stop <= 0) errs.push('Enter a stop price above zero.');
    if (!isNum(target) || target <= 0) errs.push('Enter a target price above zero.');
    if (errs.length === 0 && !(stop < entry && entry < target)) {
      errs.push('The stop must be below the buy price and the target above it.');
    }
    if (form.shares !== null && form.shares !== undefined && !(isNum(form.shares) && form.shares > 0)) {
      errs.push('Shares must be above zero, or left blank to skip the checklist.');
    }
    if (form.account !== null && form.account !== undefined && !(isNum(form.account) && form.account > 0)) {
      errs.push('Account value must be above zero, or left blank.');
    }
    if (form.cash !== null && form.cash !== undefined && !(isNum(form.cash) && form.cash >= 0)) {
      errs.push('Cash cannot be negative, or leave it blank.');
    }
    if (form.horizon !== undefined && !(Number.isInteger(form.horizon) && form.horizon >= HORIZON_MIN && form.horizon <= HORIZON_MAX)) {
      errs.push(`The horizon must be ${HORIZON_MIN} to ${HORIZON_MAX} sessions.`);
    }
    return errs;
  }

  // Live arithmetic from the form as it is typed: each price's percent from the
  // buy price, and the one-line dollar summary. Pure; numbers only.
  function planMath(form) {
    const e = form.entry; const s = form.stop; const t = form.target;
    const priced = isNum(e) && e > 0;
    const pctOf = (v) => (priced && isNum(v) && v > 0 ? signedPct(((v / e) - 1) * 100) : '');
    return {
      stopPct: pctOf(s),
      targetPct: pctOf(t),
      ordered: priced && isNum(s) && isNum(t) && s > 0 && t > 0 && s < e && e < t,
      riskPerShare: priced && isNum(s) ? e - s : null,
      rewardPerShare: priced && isNum(t) ? t - e : null,
    };
  }

  function planSummary(form) {
    const m = planMath(form);
    if (!isNum(form.entry) || !(form.entry > 0) || !isNum(form.stop) || !isNum(form.target)) {
      return 'Enter the buy price, stop and target to see the plan in dollars.';
    }
    if (!m.ordered) return 'The stop must be below the buy price and the target above it.';
    const risk = m.riskPerShare;
    const reward = m.rewardPerShare;
    const rr = floor2(reward / risk);
    if (isNum(form.shares) && form.shares > 0) {
      const n = Number(form.shares.toFixed(4));
      return `Risking ${money(n * risk, 0)} (${n} × ${money(risk)}) to make ${money(n * reward, 0)}, ${rr} : 1 before costs.`;
    }
    return `Risking ${money(risk)} a share to make ${money(reward)}, ${rr} : 1 before costs. Enter shares to see the dollar amounts.`;
  }

  function checkBody(form) {
    const body = { ticker: form.ticker, shares: form.shares, entry: form.entry, stop: form.stop, target: form.target };
    if (isNum(form.account) && form.account > 0) body.account_value = form.account;
    if (isNum(form.cash) && form.cash >= 0) body.cash = form.cash;
    return body;
  }

  function simulateBody(form) {
    return { ticker: form.ticker, entry: form.entry, stop: form.stop, target: form.target, horizon_sessions: form.horizon };
  }

  async function postJson(url, body, fetchImpl) {
    const doFetch = fetchImpl || (typeof fetch === 'function' ? fetch : null);
    if (!doFetch) throw new Error('This browser cannot send the request.');
    const resp = await doFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...POST_HEADERS },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (!resp.ok) throw new Error((data && data.error) || `The request failed (${resp.status}).`);
    return data;
  }

  // Price alerts at the plan's own stop and target, in My list. The store
  // dedupes the same level and caps active alerts, so a second click is harmless.
  async function setPlanAlerts(form, fetchImpl) {
    const symbol = form.ticker;
    // Saving to the list is a convenience; the alerts do not depend on it (list may be full).
    try { await postJson('/api/my-list', { symbol }, fetchImpl); } catch (_) { /* keep going */ }
    await postJson('/api/alerts', { symbol, direction: 'below', level: form.stop, label: 'Plan stop', source: 'manual' }, fetchImpl);
    await postJson('/api/alerts', { symbol, direction: 'above', level: form.target, label: 'Plan target', source: 'manual' }, fetchImpl);
    if (root.QuantaMyList && typeof root.QuantaMyList.refresh === 'function') root.QuantaMyList.refresh();
    return `Alerts set for ${symbol}: at or below ${money(form.stop)} (stop) and at or above ${money(form.target)} (target). `
      + 'Checked every few minutes while the market is open.';
  }

  const WATCH_HTML = `<div class="pl-watch">
      <button type="button" class="q-btn" id="pl-watch">Alert me at the stop and target</button>
      <p class="pl-hint" id="pl-watch-status" role="status" aria-live="polite"></p>
    </div>`;

  const FORM_HTML = `
    <form id="pl-form" class="pl-form" novalidate>
      <section class="pl-step" aria-labelledby="pl-plan-title">
        <h3 class="pl-title" id="pl-plan-title"><span class="pl-num">1</span> Your plan</h3>
        <div class="pl-ticker-row">
          <div class="pl-field pl-field--ticker">
            <label for="pl-ticker">Ticker</label>
            <input id="pl-ticker" name="ticker" type="text" readonly autocomplete="off" spellcheck="false" autocapitalize="characters" />
          </div>
          <button type="button" class="pl-link" id="pl-ticker-edit" aria-controls="pl-ticker">Change</button>
        </div>
        <div class="pl-grid">
          <div class="pl-field">
            <label for="pl-shares">Shares</label>
            <input id="pl-shares" name="shares" type="number" inputmode="decimal" min="0" step="any" aria-describedby="pl-shares-hint" />
          </div>
          <div class="pl-field">
            <label for="pl-entry">Buy at</label>
            <input id="pl-entry" name="entry" type="number" inputmode="decimal" min="0" step="any" required />
          </div>
          <div class="pl-field">
            <div class="pl-field-head"><label for="pl-stop">Stop</label><span class="pl-pct" id="pl-stop-pct"></span></div>
            <input id="pl-stop" name="stop" type="number" inputmode="decimal" min="0" step="any" required aria-describedby="pl-stop-pct" />
          </div>
          <div class="pl-field">
            <div class="pl-field-head"><label for="pl-target">Target</label><span class="pl-pct" id="pl-target-pct"></span></div>
            <input id="pl-target" name="target" type="number" inputmode="decimal" min="0" step="any" required aria-describedby="pl-target-pct" />
          </div>
        </div>
        <p class="pl-summary-line" id="pl-summary" role="status" aria-live="polite"></p>
        <p class="pl-hint" id="pl-shares-hint">Shares are needed for the dollar amounts and the checklist. The simulation does not use them.</p>
        <details class="pl-more-opts" id="pl-more">
          <summary>More options</summary>
          <div class="pl-grid">
            <div class="pl-field">
              <label for="pl-horizon">Horizon (sessions)</label>
              <input id="pl-horizon" name="horizon" type="number" inputmode="numeric" min="${HORIZON_MIN}" max="${HORIZON_MAX}" step="1" value="${HORIZON_DEFAULT}" aria-describedby="pl-horizon-hint" />
              <p class="pl-hint" id="pl-horizon-hint">Default ${HORIZON_DEFAULT} sessions, about three months.</p>
            </div>
            <div class="pl-field">
              <label for="pl-account">Account value (optional)</label>
              <input id="pl-account" name="account" type="number" inputmode="decimal" min="0" step="any" aria-describedby="pl-account-hint" />
              <p class="pl-hint" id="pl-account-hint">Used to check dollar risk and position size, to see how much of the account the plan uses. Leave blank to skip those checks.</p>
            </div>
            <div class="pl-field">
              <label for="pl-cash">Cash (optional)</label>
              <input id="pl-cash" name="cash" type="number" inputmode="decimal" min="0" step="any" />
            </div>
          </div>
        </details>
        <p class="pl-prefill" id="pl-prefill"></p>
        <div class="pl-actions">
          <button type="submit" class="primary-btn" id="pl-run">Check my plan</button>
          <p class="pl-disclaimer">A checklist, not advice. Nothing here places an order.</p>
        </div>
      </section>
    </form>
    <div class="pl-errors" id="pl-errors" role="alert" hidden></div>
    <section class="pl-step pl-step--result" aria-labelledby="pl-checks-title">
      <h3 class="pl-title" id="pl-checks-title"><span class="pl-num">2</span> Does it fit the rules?</h3>
      <div id="pl-checks" aria-live="polite">${emptyState('Run the check to see the result here.')}</div>
    </section>
    <section class="pl-step pl-step--result" aria-labelledby="pl-sim-title">
      <h3 class="pl-title" id="pl-sim-title"><span class="pl-num">3</span> How often would this have worked?</h3>
      <div id="pl-sim" aria-live="polite">${emptyState('Run the check to see the odds here.')}</div>
    </section>`;

  function readForm(doc) {
    const val = (id) => doc.getElementById(id).value.trim();
    const num = (id) => {
      const raw = val(id);
      return raw === '' ? null : Number(raw);
    };
    return {
      ticker: val('pl-ticker').toUpperCase(),
      shares: num('pl-shares'),
      entry: num('pl-entry'),
      stop: num('pl-stop'),
      target: num('pl-target'),
      horizon: num('pl-horizon'),
      account: num('pl-account'),
      cash: num('pl-cash'),
    };
  }

  function mount(doc, currentAnalysis) {
    const body = doc.getElementById('plan-lab-body');
    if (!body) return null;
    body.innerHTML = FORM_HTML;
    const q = (id) => doc.getElementById(id);
    let run = 0;
    let lastSig = null;

    // Engine levels carry full float precision (91.91346336364747); a person
    // types prices in cents, so show them that way (4 dp below $1).
    const px = (v) => {
      if (!isNum(v)) return '';
      const f = Math.abs(v) < 1 ? 1e4 : 100;
      return String(Math.round(v * f) / f);
    };

    function updateLive() {
      const f = readForm(doc);
      const m = planMath(f);
      q('pl-stop-pct').textContent = m.stopPct ? `${m.stopPct} from buy` : '';
      q('pl-target-pct').textContent = m.targetPct ? `${m.targetPct} from buy` : '';
      q('pl-summary').textContent = planSummary(f);
    }

    function setValues(levels) {
      q('pl-ticker').value = levels.ticker;
      q('pl-entry').value = px(levels.entry);
      q('pl-stop').value = px(levels.stop);
      q('pl-target').value = px(levels.target);
      q('pl-horizon').value = String(levels.horizon);
      const complete = isNum(levels.entry) && isNum(levels.stop) && isNum(levels.target);
      q('pl-prefill').textContent = complete
        ? `Buy price, stop and target pre-filled from the Research levels for ${levels.ticker}. Edit any field.`
        : `Research has no usable levels for ${levels.ticker}. Enter the plan by hand.`;
      updateLive();
    }

    function syncFromAnalysis() {
      const levels = levelsFromAnalysis(currentAnalysis());
      const sig = levels ? JSON.stringify(levels) : null;
      if (sig === lastSig) return;
      lastSig = sig;
      if (!levels) {
        q('pl-prefill').textContent = 'Run Research on a ticker to pre-fill the plan, or enter it by hand.';
        updateLive();
        return;
      }
      // A new Research read replaces the old results; they belong to another plan.
      run += 1;
      setValues(levels);
      q('pl-checks').innerHTML = emptyState('Run the check to see the result here.');
      q('pl-sim').innerHTML = emptyState('Run the check to see the odds here.');
    }

    function showErrors(list) {
      const box = q('pl-errors');
      if (!list.length) { box.hidden = true; box.innerHTML = ''; return; }
      box.innerHTML = `<ul>${list.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>`;
      box.hidden = false;
    }

    async function onSubmit(ev) {
      ev.preventDefault();
      const form = readForm(doc);
      const needShares = form.shares !== null;
      const errs = validatePlan(form);
      showErrors(errs);
      if (errs.length) return;
      const current = ++run;
      const checksBox = q('pl-checks');
      const simBox = q('pl-sim');
      const runBtn = q('pl-run');
      runBtn.disabled = true;
      simBox.innerHTML = '<p class="pl-loading" role="status">Simulating 5,000 paths from this stock’s history...</p>';
      checksBox.innerHTML = needShares
        ? '<p class="pl-loading" role="status">Checking the plan against your limits and QUANTA’s call...</p>'
        : emptyState('Enter the number of shares to run the checklist.');
      const checkTask = needShares
        ? postJson('/api/plan/check', checkBody(form))
        : Promise.resolve(null);
      const simTask = postJson('/api/plan/simulate', simulateBody(form));
      const [c, s] = await Promise.allSettled([checkTask, simTask]);
      if (current !== run) return;
      runBtn.disabled = false;
      checksBox.innerHTML = (c.status === 'fulfilled'
        ? (c.value ? renderChecks(c.value) : emptyState('Enter the number of shares to run the checklist.'))
        : errorState(c.reason && c.reason.message ? c.reason.message : 'The checklist is unavailable.'))
        + WATCH_HTML;
      q('pl-watch').addEventListener('click', async () => {
        const btn = q('pl-watch');
        const status = q('pl-watch-status');
        btn.disabled = true;
        try {
          status.textContent = await setPlanAlerts(form);
        } catch (err) {
          status.textContent = err && err.message ? err.message : 'The alerts could not be set.';
          btn.disabled = false;
        }
      });
      simBox.innerHTML = s.status === 'fulfilled'
        ? renderSimulation(s.value)
        : errorState(s.reason && s.reason.message ? s.reason.message : 'The simulation is unavailable.');
    }

    q('pl-form').addEventListener('submit', onSubmit);
    q('pl-form').addEventListener('input', updateLive);
    q('pl-ticker-edit').addEventListener('click', () => {
      const t = q('pl-ticker');
      t.readOnly = false;
      t.focus();
      t.select();
    });
    syncFromAnalysis();
    updateLive();
    return { syncFromAnalysis, readForm, onSubmit, updateLive };
  }

  function start() {
    if (typeof document === 'undefined') return;
    const card = document.getElementById('plan-lab');
    if (!card) return;
    const analysis = () => {
      try {
        // app.js keeps the Research read in its global `state`; read it defensively.
        return typeof state !== 'undefined' && state ? state.analysis : null; // eslint-disable-line no-undef
      } catch (_) {
        return null;
      }
    };
    const api = mount(document, analysis);
    if (!api) return;
    const results = document.getElementById('an-results');
    if (results && typeof MutationObserver === 'function') {
      new MutationObserver(() => api.syncFromAnalysis()).observe(results, { childList: true });
    }
  }

  const api = {
    renderChecks, renderSimulation, levelsFromAnalysis, validatePlan, checkBody, simulateBody,
    planMath, planSummary, plainOf, leadSentence, historyLine, floor2,
    postJson, esc, mount, setPlanAlerts, CHECK_LABEL, STATUS_TONE, VERDICT_TONE, VERDICT_PLAIN,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaPlanLab = api;
    if (typeof document !== 'undefined') {
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
      else start();
    }
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
