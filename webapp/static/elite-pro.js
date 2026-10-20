/* ============================================================================
   QUANTA Elite Pro — UI upgrade JS layer
   Adds: Bull/Bear Investment Thesis, Signal Conflict Resolution, Upgrade
   Triggers, Insider Activity (with transactions), Why HOLD / Key Risks
   side-by-side panel, and pick-card decoration (R:R sparkline + action chips).
   Exposes window.QuantaPro for the main app.js to call after render.
   ============================================================================ */
(function () {
  'use strict';

  /* [F2] Same R:R format as app.js fmtRR (configured decimals). */
  function proRR(v) {
    if (typeof fmtRR === 'function') return fmtRR(v);
    const d = proThr('UI.RR_DECIMALS');
    return `${Number(v).toFixed(d == null ? 2 : d)}:1`;
  }

  /* [F9] Thresholds come from app.js's thr() (engine/thresholds.py via the page). */
  function proThr(path) {
    if (typeof thr === 'function') return thr(path);
    const g = typeof globalThis !== 'undefined' ? globalThis : {};
    return String(path).split('.').reduce((o, k) => (o == null ? undefined : o[k]), g.__QUANTA_THRESHOLDS__ || {});
  }

  const rThr = (key) => Number(proThr(`RESEARCH.${key}`));
  // D3: scanner scores (0-10) are shown out of 100, the one scale on the page.
  const s100 = (v) => String(Math.round(Math.max(0, Math.min(100, Number(v) * Number(proThr('SCORE.TO_100'))))));

  // ----- Tiny helpers (kept local; main app.js helpers may not be on window) --
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const num = (v) => {
    if (v == null || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const fmtN = (v, d = 2) => (isNum(v) ? v.toFixed(d) : '--');
  const fmtCur = (v, sym = '$', d = 2) => {
    if (!isNum(v)) return '--';
    sym = String(sym ?? '').replace(/[<>&"'`]/g, '');
    // The sign leads the symbol: -$959,950,681, never $-959,950,681.
    return `${v < 0 ? '-' : ''}${sym}${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
  };
  // The reconciled Decision is the only verdict; the legacy signal is a fallback
  // for payloads that predate it.
  const decisionAction = (data) => String((data && data.decision && data.decision.action)
    || (data && (data.final_action || data.signal)) || '').toUpperCase();
  const decisionLabel = (data) => String((data && data.decision && data.decision.label) || decisionAction(data) || 'HOLD');
  const consistentLines = (items, data) => (typeof window !== 'undefined' && typeof window.decisionConsistentLines === 'function')
    ? window.decisionConsistentLines(items, data && data.decision)
    : (items || []).filter(Boolean);
  const sigKey = (s) => String(s || 'HOLD').toLowerCase().replace(/[^a-z]+/g, '-');
  const titleCase = (s) => String(s || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  // ----- 1. Bull / Bear Investment Thesis ------------------------------------
  function thesisCard(data) {
    const t = data && data.thesis;
    if (!t) return '';
    const bull = Array.isArray(t.bull_case) ? t.bull_case.filter(Boolean) : [];
    const bear = Array.isArray(t.bear_case) ? t.bear_case.filter(Boolean) : [];
    if (!bull.length && !bear.length) return '';
    const renderCol = (items, kind, title) => `
      <div class="qpro-thesis-col ${kind}">
        <div class="qpro-thesis-head">
          <span class="qpro-thesis-title">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              ${kind === 'bull'
                ? '<path fill="currentColor" d="M8 2l5 6H10v6H6V8H3l5-6z"/>'
                : '<path fill="currentColor" d="M8 14l-5-6h3V2h4v6h3l-5 6z"/>'}
            </svg>
            ${title}
          </span>
          <span class="qpro-thesis-count">${items.length}</span>
        </div>
        ${items.length
          ? `<ul class="qpro-thesis-list">${items.slice(0, 6).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
          : `<div class="qpro-empty-line">No ${kind} arguments returned.</div>`}
      </div>`;
    return `
      <article class="qpro-card">
        <h3 class="qpro-kicker">Bull &amp; bear case
          <span class="qpro-kicker-tail">${data.decision ? 'the arguments on each side, weighed in the Decision' : esc(t.summary || '').slice(0, 140)}</span>
        </h3>
        <div class="qpro-thesis-grid">
          ${renderCol(bull, 'bull', 'Bull case')}
          ${renderCol(bear, 'bear', 'Bear case')}
        </div>
      </article>`;
  }

  // ----- 2. Signal conflicts --------------------------------------------------
  // The composite score, the Decision and the risk grade are stated once
  // elsewhere (the scores card, the rail, the rules list). What only this
  // block knows: momentum against the trend, and the engine's overrides. It
  // joins the scores card as a sub-block.
  function conflictCard(data) {
    const se = (data && data.signal_explanation) || {};
    const overrides = (se.overrides_triggered || []).filter(Boolean);
    const tech = (data && data.technical) || {};
    const techVsSig =
      tech.trend_direction && /UP|BULL/i.test(tech.trend_direction) &&
      tech.macd_signal && /BEAR/i.test(tech.macd_signal);

    const lines = [];
    if (techVsSig) {
      lines.push(['Momentum',
        `<span class="qpro-conflict-tag warn">Trend up · MACD bearish</span> Momentum fading: wait for re-cross`]);
    }
    const shownOverrides = consistentLines(overrides, data);
    if (shownOverrides.length) {
      lines.push(['Overrides triggered',
        shownOverrides.slice(0, 4).map((o) => `<span class="qpro-conflict-tag warn">${esc(o)}</span>`).join(' ')]);
    }
    if (!lines.length) return '';
    return `
      <section class="qpro-conflict-note" data-signal-conflicts>
        <h4 class="section-kicker">Signal conflicts <span class="section-kicker-tail">reconciled into ${esc(decisionLabel(data))}</span></h4>
        <div class="qpro-conflict-body">
          ${lines.map(([k, v]) => `
            <div class="qpro-conflict-line">
              <span class="k">${esc(k)}</span>
              <span class="v">${v}</span>
            </div>`).join('')}
        </div>
      </section>`;
  }

  // ----- 4. Insider Activity (with key transactions) ------------------------
  function insiderCard(data) {
    const ins = data && data.insider;
    if (!ins) return '';
    const buys = Number(ins.recent_buys || 0);
    const sells = Number(ins.recent_sells || 0);
    const net = num(ins.net_flow);
    const sentiment = ins.display_label || ins.insider_sentiment || 'NEUTRAL';
    const tx = Array.isArray(ins.key_transactions) ? ins.key_transactions.filter(Boolean) : [];
    if (!buys && !sells && !tx.length) return '';
    const sym = (data && data.currency_symbol) || '$';
    const sentCls = /BULL/i.test(sentiment) ? 'bull' : (/BEAR/i.test(sentiment) ? 'bear' : '');
    const netCls = isNum(net) ? (net >= 0 ? 'bull' : 'bear') : '';
    return `
      <article class="qpro-card">
        <h3 class="qpro-kicker">Insider activity
          <span class="qpro-kicker-tail">last 90 days</span>
        </h3>
        <div class="qpro-insider-summary">
          <div class="qpro-insider-cell"><div class="k">Sentiment</div><div class="v ${sentCls}">${esc(titleCase(sentiment))}</div></div>
          <div class="qpro-insider-cell"><div class="k">Buys</div><div class="v">${buys}</div></div>
          <div class="qpro-insider-cell"><div class="k">Sells</div><div class="v">${sells}</div></div>
          <div class="qpro-insider-cell"><div class="k">Net flow</div><div class="v ${netCls}">${isNum(net) ? fmtCur(net, sym, 0) : '--'}</div></div>
        </div>
        ${tx.length ? `
          <ul class="qpro-tx-list">
            ${tx.slice(0, 6).map((t) => {
              const lower = String(t).toLowerCase();
              const dot = /buy|purchase|acquired/.test(lower) ? 'bull'
                        : /sell|sold|dispose/.test(lower) ? 'bear' : '';
              return `<li class="qpro-tx-row">
                <span class="qpro-tx-dot ${dot}"></span>
                <span class="qpro-tx-text">${esc(t)}</span>
                <span></span>
              </li>`;
            }).join('')}
          </ul>` : ''}
      </article>`;
  }

  // ----- 5. Why HOLD / Key Risks side-by-side -------------------------------
  function whyHoldRisksCard(data) {
    const se = (data && data.signal_explanation) || {};
    const thesis = (data && data.thesis) || {};
    // Engine warnings arrive with emoji markers; the card states them as text.
    const plain = (s) => String(s == null ? '' : s)
      .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]/gu, '')
      .replace(/\s{2,}/g, ' ').trim();
    const support = consistentLines(se.supporting_factors || [], data).map(plain).filter(Boolean).slice(0, 5);
    const risks = [
      ...(se.risk_factors || []),
      ...(data.risk_warnings || []),
      ...(thesis.risks || []),
    ].map(plain).filter(Boolean);
    // de-dupe risks. An all-capitals engine echo ("SEVERE DRAWDOWN HISTORY:
    // -37%") restates a reading the risk-grade line already gives with its
    // context, so it is left out when that line is present.
    const seen = new Set();
    const hasGradeLine = risks.some((r) => /risk grade/i.test(r));
    const shouting = (r) => /[A-Z]{4}/.test(r) && r === r.toUpperCase();
    const dedupRisks = risks.filter((r) => {
      if (hasGradeLine && shouting(r)) return false;
      const k = String(r).toLowerCase().slice(0, 40);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    }).slice(0, 5);
    if (!support.length && !dedupRisks.length) return '';
    const supportTitle = 'Supporting factors';
    return `
      <article class="qpro-card">
        <h3 class="qpro-kicker">Supporting factors vs key risks
          <span class="qpro-kicker-tail">what argues for the name, and what could go wrong</span>
        </h3>
        <div class="qpro-hold-risk-grid">
          <div class="qpro-hold-risk-col support">
            <h4>${esc(supportTitle)}</h4>
            ${support.length ? `<ul>${support.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : '<div class="qpro-empty-line">No supporting factors returned.</div>'}
          </div>
          <div class="qpro-hold-risk-col risk">
            <h4>Key risks</h4>
            ${dedupRisks.length ? `<ul>${dedupRisks.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : '<div class="qpro-empty-line">No active risk warnings.</div>'}
          </div>
        </div>
      </article>`;
  }

  // ----- 6. Decorate pick cards with R:R sparkline + action chips ----------
  function decoratePickCards(data) {
    const recs = (data && data.recommendations) || [];
    if (!recs.length) return;
    const sym = data.currency || '$';
    document.querySelectorAll('#picks-results .pick-card').forEach((card, idx) => {
      const ticker = card.querySelector('.pick-ticker')?.textContent?.trim();
      const rec = recs.find((r) => String(r.ticker || '') === ticker);
      if (!rec || card.dataset.qproDecorated === '1') return;
      card.classList.add('qpro');
      card.dataset.qproDecorated = '1';

      // Insert rank badge
      const rank = document.createElement('span');
      rank.className = 'qpro-pick-rank';
      rank.textContent = `#${idx + 1}`;
      card.appendChild(rank);

      // [A12] Levels, R:R and allocation live once, in the card's decision
      // row; the ladder and chip strip that repeated them are gone.
    });
  }

  // ----- 7. Inject all analysis extras into the rendered container ---------
  function injectAnalysisExtras(data) {
    const container = document.getElementById('an-results');
    if (!container || !data) return;
    const stack = container.querySelector('.analysis-stack');
    if (!stack) return;
    // Each card joins its tear-sheet chapter. `first` puts it ahead of the
    // chapter's core cards (the thesis leads Thesis; the drivers lead Risks).
    const place = (html, chapter, { first = false, after = null, into = null } = {}) => {
      if (!html) return;
      const wrap = document.createElement('div');
      wrap.innerHTML = html.trim();
      const node = wrap.firstElementChild;
      if (!node) return;
      const body = stack.querySelector(`[data-ch-body="${chapter}"]`);
      if (!body) { stack.appendChild(node); return; }
      // `into` appends inside a card of the chapter (a sub-block, not a tile).
      const host = into ? (body.querySelector(into) || stack.querySelector(into)) : null;
      if (host) { host.appendChild(node); return; }
      const anchor = after ? body.querySelector(after) : null;
      // `after` wins when its anchor exists; otherwise `first` still applies.
      if (anchor) anchor.after(node);
      else if (first) body.prepend(node);
      else body.appendChild(node);
    };
    // The business description (a fact) leads; the argued thesis follows it.
    place(thesisCard(data), 'thesis', { first: true, after: '[data-business]' });
    place(whyHoldRisksCard(data), 'risks', { first: true });
    place(insiderCard(data), 'catalysts');
    place(conflictCard(data), 'decision', { into: '.ts-scores' });
    injectDeepDiveExtras(data);
  }

  // ----- 8. Inject picks extras (decorate cards) ---------------------------
  function injectPicksExtras(data) {
    decoratePickCards(data);
    injectPicksFunnel(data);
    injectBuilderExtras(data);
    injectAnalyzerWatchlist(data);
  }

  // ===== Tier 3 — Stock Deep-Dive missing panels =========================
  // Adds: Peer Comparison table, Signal Track Record, sticky Section Nav.

  function peerComparisonCard(data) {
    const pc = data && data.peer_comparison;
    if (!pc) return '';
    const hasNumbers = (Number(pc.pe_stock) || 0) > 0
                    || (Number(pc.roe_stock) || 0) !== 0
                    || (Number(pc.sector_rank) || 0) > 0;
    if (!hasNumbers) return '';
    const cell = (label, stock, sectorAvg, diff, formatter, positiveOnly = false) => {
      const sNum = Number(stock);
      const aNum = Number(sectorAvg);
      if (!Number.isFinite(sNum) || !Number.isFinite(aNum) || (sNum === 0 && aNum === 0)) return '';
      // A multiple at or below zero is not meaningful (n/m), and a gap of zero
      // between two different values is not a measurement: both read "--".
      const notMeaningful = positiveOnly && sNum <= 0;
      const rawD = Number(diff);
      const d = notMeaningful || (Number.isFinite(rawD) && Math.abs(rawD) < 0.05 && Math.abs(sNum - aNum) > 0.005 * Math.max(Math.abs(aNum), 1)) ? NaN : rawD;
      const gap = rThr('PEER_VALUATION_GAP_PCT');
      const dCls = !Number.isFinite(d) ? '' : (d > gap ? 'bear' : (d < -gap ? 'bull' : 'warn'));
      const dTxt = !Number.isFinite(d) ? '--' : `${d >= 0 ? '+' : ''}${d.toFixed(1)}%`;
      return `
        <tr>
          <td>${esc(label)}</td>
          <td class="mono">${notMeaningful ? 'n/m' : formatter(sNum)}</td>
          <td class="mono">${formatter(aNum)}</td>
          <td class="mono ${dCls}">${dTxt}</td>
        </tr>`;
    };
    const fmt = (n) => n.toFixed(2);
    const fmtPctV = (n) => `${n.toFixed(1)}%`;
    const rows = [
      cell('P/E', pc.pe_stock, pc.pe_sector_avg, pc.pe_vs_sector, fmt, true),
      cell('ROE', pc.roe_stock, pc.roe_sector_avg, pc.roe_vs_sector, fmtPctV),
      cell('P/B', pc.pb_stock, pc.pb_sector_avg, pc.pb_vs_sector, fmt, true),
    ].filter(Boolean).join('');

    const rank = Number(pc.sector_rank) || 0;
    const total = Number(pc.sector_total) || 0;
    const rankPct = (rank > 0 && total > 0) ? ((total - rank + 1) / total) * 100 : null;
    const assessment = String(pc.overall_assessment || '').trim();
    const assessCls = /out/i.test(assessment) ? 'bull' : /under/i.test(assessment) ? 'bear' : 'warn';

    return `
      <article class="qpro-card qpro-peer-card">
        <h3 class="qpro-kicker">Peer comparison
          <span class="qpro-kicker-tail">vs ${esc(pc.sector || 'sector')} average</span>
        </h3>
        <div class="qpro-peer-grid">
          ${rows ? `
          <div class="q-tablewrap" tabindex="0" role="region" aria-label="Peer comparison">
          <table class="qpro-peer-table">
            <thead>
              <tr><th>Metric</th><th>Stock</th><th>Sector avg</th><th>Δ vs sector</th></tr>
            </thead>
            <tbody>${rows}</tbody>
          </table></div>` : '<p class="qpro-empty-line">No comparable peer metrics returned.</p>'}
          <div class="qpro-peer-side">
            ${rank > 0 ? `
              <div class="qpro-peer-rank">
                <div class="qpro-peer-rank-label">Sector rank</div>
                <div class="qpro-peer-rank-val">#${rank}<span class="qpro-peer-rank-of">/ ${total || '--'}</span></div>
                ${rankPct != null ? `<div class="qpro-peer-rank-bar"><div class="qpro-peer-rank-fill" style="width:${rankPct.toFixed(1)}%"></div></div>` : ''}
                ${rankPct != null ? `<div class="qpro-peer-rank-sub">${rank / total <= 0.5 ? `Top ${((rank / total) * 100).toFixed(0)}%` : `Bottom ${(((total - rank + 1) / total) * 100).toFixed(0)}%`} of sector</div>` : ''}
              </div>` : ''}
            ${assessment ? `<div class="qpro-peer-verdict ${assessCls}">${esc(assessment)}</div>` : ''}
          </div>
        </div>
      </article>`;
  }

  // The legacy SignalTracker win-rate card was removed: outcomes are the
  // benchmarked ledger's, shown on the Evidence page.

  function injectDeepDiveExtras(data) {
    const container = document.getElementById('an-results');
    if (!container) return;
    const stack = container.querySelector('.analysis-stack');
    if (!stack) return;

    const cards = [
      { selector: '.qpro-peer-card', html: peerComparisonCard(data) },
    ];
    cards.forEach(({ selector, html }) => {
      if (!html) return;
      if (stack.querySelector(selector)) return;
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      const node = wrap.firstElementChild;
      if (!node) return;
      const body = stack.querySelector('[data-ch-body="valuation"]');
      if (body) body.appendChild(node);
      else stack.appendChild(node);
    });
  }

  // ===== Tier 2 — Portfolio Builder full mirror ===========================
  //   * Portfolio Risk Metrics   (max position, sector HHI, signal mix)
  //   * Capital Allocation table (per-pick $ + % cap + shares + cash row)
  //   * Execution Caveats banner (SKIP-IF rules)
  //   * TradingView Export       (copy-button watchlist string)

  function portfolioRiskCard(data) {
    const picks = ((data && data.recommendations) || []).filter(isFundedRec);
    if (!picks.length) return '';
    const sym = data.currency || '$';
    const totalDeployed = picks.reduce((a, p) => a + Number(p.allocation_after ?? p.allocation ?? 0), 0);
    if (totalDeployed <= 0) return '';

    // [P4] Weights are shares of TOTAL capital first (what the limits apply to),
    // of deployed capital second; colour comes only from the book's own limits.
    const totalCapital = num(data.capital) || Number((document.getElementById('sp-capital') || {}).value || 0) || totalDeployed;
    const lim = data.book_limits || {};
    const overCls = (pctOfCapital, limit) => (num(limit) > 0 && pctOfCapital > num(limit) * 100 ? 'bear' : '');
    const limitTxt = (limit) => (num(limit) > 0 ? ` · limit ${(num(limit) * 100).toFixed(1)}%` : '');

    // Max single position
    let maxVal = 0, maxPosTicker = '';
    picks.forEach((p) => {
      const v = Number(p.allocation_after ?? p.allocation ?? 0);
      if (v > maxVal) { maxVal = v; maxPosTicker = p.ticker || ''; }
    });
    const maxPosCap = totalCapital ? (maxVal / totalCapital) * 100 : 0;
    const maxPosDep = (maxVal / totalDeployed) * 100;

    const sectorMap = new Map();
    picks.forEach((p) => {
      const s = p.sector || 'Unknown';
      const v = Number(p.allocation_after ?? p.allocation ?? 0);
      sectorMap.set(s, (sectorMap.get(s) || 0) + v);
    });
    const topSectors = [];
    sectorMap.forEach((v, k) => {
      topSectors.push({ sector: k, cap: totalCapital ? (v / totalCapital) * 100 : 0, dep: (v / totalDeployed) * 100 });
    });
    topSectors.sort((a, b) => b.cap - a.cap);
    const topSector = topSectors[0] || { sector: '--', cap: 0, dep: 0 };

    // Total max-loss $ + %
    const totalMaxLoss = picks.reduce((acc, p) => {
      const e = Number(p.entry_price || p.current_price || 0);
      const s = Number(p.stop_loss || 0);
      const sh = Number(p.shares || 0);
      if (!e || !s || !sh) return acc;
      return acc + Math.abs((e - s) * sh);
    }, 0);
    const maxLossPctCap = totalCapital ? (totalMaxLoss / totalCapital) * 100 : 0;
    const maxLossCls = overCls(maxLossPctCap, lim.stop_risk_pct);

    // Signal mix — keyed off the RECONCILED final_action, never the raw
    // scanner signal (a deep-confirm downgrade must show in the mix).
    const sigMix = { 'STRONG BUY': 0, BUY: 0, ACCUMULATE: 0, WATCHLIST: 0, 'HOLD EXISTING': 0, OTHER: 0 };
    picks.forEach((p) => {
      const k = String((p.decision && p.decision.label) || p.display_action || p.final_action || '').toUpperCase().replace(/_/g, ' ');
      if (sigMix[k] != null) sigMix[k]++; else sigMix.OTHER++;
    });

    // Risk-grade mix
    const gradeMix = { A: 0, B: 0, C: 0, D: 0, F: 0 };
    picks.forEach((p) => {
      const g = String(p.risk_grade || '').toUpperCase();
      if (gradeMix[g] != null) gradeMix[g]++;
    });

    const mixHtml = Object.entries(sigMix)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `<span class="qpro-mix-chip ${/BUY|ACCUMULATE/.test(k) ? 'bull' : 'warn'}">${esc(k)} <b>${v}</b></span>`)
      .join('');
    const gradeHtml = Object.entries(gradeMix)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => {
        const cls = (k === 'A' || k === 'B') ? 'bull' : (k === 'C' ? 'warn' : 'bear');
        return `<span class="qpro-mix-chip ${cls}">Risk ${esc(k)} <b>${v}</b></span>`;
      })
      .join('');

    return `
      <article class="qpro-card qpro-risk-card">
        <h3 class="qpro-kicker">Proposed book risk
          <span class="qpro-kicker-tail">weights of total capital (deployed capital second) and planned stop-distance loss</span>
        </h3>
        <div class="qpro-risk-grid">
          <div class="qpro-risk-cell">
            <div class="qpro-risk-label">Largest position</div>
            <div class="qpro-risk-val ${overCls(maxPosCap, lim.position_pct)}">${maxPosCap.toFixed(1)}% of capital</div>
            <div class="qpro-risk-sub">${esc(maxPosTicker)} · ${maxPosDep.toFixed(1)}% of deployed${limitTxt(lim.position_pct)}</div>
          </div>
          <div class="qpro-risk-cell">
            <div class="qpro-risk-label">Top sector weight</div>
            <div class="qpro-risk-val ${overCls(topSector.cap, lim.sector_pct)}">${topSector.cap.toFixed(1)}% of capital</div>
            <div class="qpro-risk-sub">${esc(topSector.sector)} · ${topSector.dep.toFixed(1)}% of deployed${limitTxt(lim.sector_pct)}</div>
          </div>
          <div class="qpro-risk-cell">
            <div class="qpro-risk-label">Planned loss at stops</div>
            <div class="qpro-risk-val ${maxLossCls}">-${fmtCur(totalMaxLoss, sym, 0)}</div>
            <div class="qpro-risk-sub">${maxLossPctCap.toFixed(2)}% of capital${limitTxt(lim.stop_risk_pct)} · excludes gaps and costs</div>
          </div>
          <div class="qpro-risk-cell wide">
            <div class="qpro-risk-label">Signal &amp; risk mix</div>
            <div class="qpro-mix-row">${mixHtml}${gradeHtml}</div>
          </div>
        </div>
      </article>`;
  }

  function capitalAllocationTable(data) {
    const picks = (data && data.recommendations) || [];
    if (!picks.length) return '';
    const sym = data.currency || '$';
    const totalCapital = num(data.capital) || Number((document.getElementById('sp-capital') || {}).value || 0) || 0;
    const totalDeployed = picks.reduce((a, p) => a + Number(p.allocation_after ?? p.allocation ?? 0), 0);
    const cashLeft = totalCapital - totalDeployed;
    const cashPct = totalCapital ? (cashLeft / totalCapital) * 100 : 0;

    const dash = '<span class="qpro-na">--</span>';
    const rows = picks.map((p, i) => {
      const value = Number(p.allocation_after ?? p.allocation ?? 0);
      const pctCap = totalCapital ? (value / totalCapital) * 100 : 0;
      const pctPort = totalDeployed ? (value / totalDeployed) * 100 : 0;
      const entry = Number(p.entry_price || p.current_price || 0);
      const stop = Number(p.stop_loss || 0);
      const shares = Number(p.shares || 0);
      const risk$ = (entry && stop && shares) ? Math.abs((entry - stop) * shares) : 0;
      // Reconciled action, never the raw scanner signal.
      const sig = String((p.decision && p.decision.label) || p.display_action || p.final_action || '').toUpperCase().replace(/_/g, ' ');
      const sigCls = /BUY|ACCUMULATE/.test(sig) ? 'bull' : /SELL|REDUCE|AVOID|INVALID/.test(sig) ? 'bear' : 'warn';
      const qual = String((p.decision && p.decision.qualifier) || '');
      const noEdge = String(((p.decision && p.decision.evidence) || {}).status || '').toUpperCase() === 'NO_EDGE';
      return `
        <tr>
          <td class="mono">${i + 1}</td>
          <td class="ticker">${esc(p.ticker || '')}</td>
          <td>${esc(p.sector || '')}</td>
          <td><span class="qpro-conflict-tag ${sigCls}">${esc(sig || '--')}</span>${qual ? `<span class="qpro-qual" title="${esc(qual)}">${noEdge ? 'Tested: no edge found' : 'Model not yet validated'}</span>` : ''}</td>
          <td class="mono">${shares ? shares.toLocaleString() : dash}</td>
          <td class="mono">${entry ? fmtCur(entry, sym, 2) : dash}</td>
          <td class="mono">${value ? fmtCur(value, sym, 0) : dash}</td>
          <td class="mono">${value ? `${pctCap.toFixed(1)}%` : dash}</td>
          <td class="mono">${value ? `${pctPort.toFixed(1)}%` : dash}</td>
          <td class="mono ${risk$ ? 'bear' : ''}">${risk$ ? `-${fmtCur(risk$, sym, 0)}` : dash}</td>
        </tr>`;
    }).join('');

    return `
      <article class="qpro-card qpro-capalloc-card">
        <h3 class="qpro-kicker">Capital allocation
          <span class="qpro-kicker-tail">where every ${esc(sym)} is going</span>
        </h3>
        <div class="qpro-capalloc-wrap table-scroll" tabindex="0" role="region" aria-label="Capital allocation table">
          <table class="qpro-capalloc-table">
            <thead>
              <tr>
                <th>#</th><th>Ticker</th><th>Sector</th><th>Signal</th>
                <th>Shares</th><th>Entry</th><th>Value</th>
                <th>% Cap</th><th>% of deployed</th><th>Planned loss at stop</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
            <tfoot>
              <tr class="qpro-capalloc-foot">
                <td colspan="6" class="lbl">Deployed</td>
                <td class="mono"><b>${fmtCur(totalDeployed, sym, 0)}</b></td>
                <td class="mono"><b>${totalCapital ? ((totalDeployed / totalCapital) * 100).toFixed(1) : '--'}%</b></td>
                <td class="mono">${totalDeployed > 0 ? '100.0%' : '--'}</td>
                <td></td>
              </tr>
              <tr class="qpro-capalloc-foot cash">
                <td colspan="6" class="lbl">Cash reserve</td>
                <td class="mono"><b>${fmtCur(cashLeft, sym, 0)}</b></td>
                <td class="mono"><b>${cashPct.toFixed(1)}%</b></td>
                <td class="mono">--</td>
                <td></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </article>`;
  }

  // A pick carries new money only when the resolved action allocates and the
  // post-confirmation allocation is positive. Everything else is watch-only.
  function isFundedRec(rec) {
    if (!rec) return false;
    const action = String((rec.decision && rec.decision.action) || rec.final_action || '').toUpperCase();
    const alloc = num(rec.allocation_after != null ? rec.allocation_after : rec.allocation);
    return /^(STRONG_BUY|BUY|ACCUMULATE)$/.test(action) && isNum(alloc) && alloc > 0;
  }

  function executionCaveatsCard(data) {
    // Entry rules are only meaningful when something is being entered.
    if (!((data && data.recommendations) || []).some(isFundedRec)) return '';
    // [P7] Every line from config. "Applied by the scan" restates what the engine
    // already did; the manual checklist is what QUANTA does not check.
    const regime = (data && data.regime) || {};
    const vix = num(regime.vix);
    const S = proThr('SIZING') || {};
    const vixLimit = num(proThr('REGIME.VIX_VOLATILE'));
    const C = proThr('CHECKLIST') || {};
    const pc = (v) => `${Math.round(Number(v) * 100)}%`;
    const vixHot = isNum(vix) && isNum(vixLimit) && vix > vixLimit;
    const applied = [
      { k: 'Earnings sizing', note: `Earnings within ${S.EARNINGS_IMMINENT_DAYS} days: ${pc(S.EARNINGS_IMMINENT_SIZE_MULT)} of the planned size; within ${S.EARNINGS_NEAR_DAYS} days: ${pc(S.EARNINGS_NEAR_SIZE_MULT)}; within ${S.EARNINGS_ELEVATED_DAYS} days: ${pc(S.EARNINGS_ELEVATED_SIZE_MULT)}; date unknown: ${pc(S.EARNINGS_UNKNOWN_SIZE_MULT)}.` },
      { k: 'Volatility', live: vixHot, note: `${isNum(vix) ? `VIX ${fmtN(vix, 1)} now${regime.vix_is_proxy ? ' (estimate)' : ''}; ` : ''}above ${vixLimit} the market regime reads Volatile.` },
    ];
    const manual = [
      { k: 'Pre-market gap', note: `Pre-market gap above ${pc(C.PREMARKET_GAP_PCT)}: reset the entry, the planned stop no longer fits.` },
      { k: 'SPY at the open', note: `SPY down more than ${pc(C.SPY_OPEN_DROP_PCT)} at the open: wait for the market to settle.` },
      { k: 'Sector ETF', note: `The sector ETF down more than ${pc(C.SECTOR_ETF_DROP_PCT)}: sector weakness outweighs one name's setup.` },
    ];
    const row = (r) => `
        <li class="qpro-caveat-row ${r.live ? 'live' : ''}">
          <span class="qpro-caveat-k">${esc(r.k)}</span>
          <span class="qpro-caveat-note">${esc(r.note)}</span>
        </li>`;
    return `
      <article class="qpro-card qpro-caveats-card">
        <h3 class="qpro-kicker">Pre-trade review</h3>
        <p class="qpro-caveat-group">Applied by the scan</p>
        <ul class="qpro-caveat-list">${applied.map(row).join('')}</ul>
        <p class="qpro-caveat-group">Manual checklist at execution (QUANTA does not check these)</p>
        <ul class="qpro-caveat-list">${manual.map(row).join('')}</ul>
      </article>`;
  }

  function tradingViewExportCard(data) {
    const picks = (data && data.recommendations) || [];
    if (!picks.length) return '';
    // TradingView watchlist syntax: comma-separated TICKER strings (exchange prefix optional)
    const tickers = picks.map((p) => String(p.ticker || '').trim()).filter(Boolean);
    if (!tickers.length) return '';
    const tvString = tickers.join(',');
    return `
      <article class="qpro-card qpro-tvexport-card">
        <h3 class="qpro-kicker">Watchlist export
          <span class="qpro-kicker-tail">paste into TradingView \u2192 Watchlist \u2192 Import</span>
        </h3>
        <div class="qpro-tvexport-body">
          <code class="qpro-tvexport-code" id="qpro-tv-string" tabindex="0" role="region" aria-label="Watchlist tickers to copy">${esc(tvString)}</code>
          <button type="button" class="qpro-tvexport-btn" data-qpro-copy="${esc(tvString)}">Copy</button>
        </div>
      </article>`;
  }

  function injectBuilderExtras(data) {
    const container = document.getElementById('picks-results');
    if (!container) return;
    const stream = container.querySelector('.picks-stream');
    if (!stream) return;
    const picks = (data && data.recommendations) || [];
    if (!picks.length) return; // these sections only meaningful with picks

    const insertAfter = (selector, html) => {
      if (!html) return;
      const exists = stream.querySelector(selector);
      if (exists) return;
      const wrap = document.createElement('div');
      wrap.innerHTML = html;
      const node = wrap.firstElementChild;
      if (!node) return;
      // [Picks redesign] The ranked table owns the page; these cards live in
      // the collapsed "How this scan decided" section.
      const slot = stream.querySelector('[data-pro-slot]');
      if (slot) { slot.appendChild(node); return; }
      // Portfolio risk follows the ranked research book; diagnostic material
      // lives separately in the collapsed audit section.
      // [A12] After the shared evidence note, so summary and note stay together.
      const anchor = stream.querySelector('[data-shared-note]') || stream.querySelector('.portfolio-summary');
      if (anchor && anchor.parentNode === stream) {
        // Insert at end of the run of qpro-* cards already after the anchor
        let after = anchor;
        while (after.nextSibling
               && after.nextSibling.nodeType === 1
               && /qpro-(risk|capalloc|caveats|tvexport|funnel)-card/.test(after.nextSibling.className || '')) {
          after = after.nextSibling;
        }
        stream.insertBefore(node, after.nextSibling);
      } else {
        stream.insertBefore(node, stream.firstChild);
      }
    };

    insertAfter('.qpro-risk-card', portfolioRiskCard(data));
    insertAfter('.qpro-caveats-card', executionCaveatsCard(data));
    // The redesigned table has its own Copy tickers / Download CSV actions.
    if (!stream.matches('[data-picks-v2]')) insertAfter('.qpro-tvexport-card', tradingViewExportCard(data));

    // Wire copy button
    const btn = stream.querySelector('.qpro-tvexport-btn');
    if (btn && !btn.dataset.qproBound) {
      btn.dataset.qproBound = '1';
      btn.addEventListener('click', () => {
        const txt = btn.getAttribute('data-qpro-copy') || '';
        if (!txt) return;
        const fallback = () => {
          const ta = document.createElement('textarea');
          ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
          document.body.appendChild(ta); ta.select();
          let ok = false;
          try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
          document.body.removeChild(ta);
          return ok;
        };
        // "Copied" only when a copy went through; a blocked copy says so.
        const done = (ok) => {
          const orig = btn.dataset.qproLabel || (btn.dataset.qproLabel = btn.textContent);
          btn.textContent = ok ? 'Copied' : 'Copy blocked';
          btn.classList.toggle('copied', ok);
          setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1400);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).then(() => done(true)).catch(() => done(fallback()));
        } else { done(fallback()); }
      });
    }
  }

  // ----- 9. Scan Funnel ----------------------------------------------------
  /* [A04] Reason codes in words: app.js owns the wording (FILTER_WORDS). */
  const reasonWords = (k) => (typeof filterWords === 'function' ? filterWords(k) : String(k).replace(/_/g, ' '));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || `${one}s`)}`;

  /* [A04] One row per book stage, each rule stated from config or from the
     value the scan applied. A stage the scan never reached says so, with its
     configured baseline, instead of printing a placeholder. */
  function stageRule(s) {
    const t = s.threshold || {};
    const v = s.evaluated && t.value != null ? t.value : t.configured;
    const rr = (x) => proRR(x);
    switch (s.key) {
      case 'scan_mode': return { label: 'Pre-momentum setup', rule: 'Early-momentum setups not yet extended' };
      case 'actionable': return { label: 'Buy or Accumulate call', rule: 'Final action is Buy or Accumulate' };
      case 'event': return { label: 'Corporate events clear', rule: 'No pending acquisition or delisting; a check that could not run holds the name out' };
      case 'risk_grade': return { label: 'Risk grade allowed', rule: `Grade ${(t.configured || []).join(', ')} excluded` };
      case 'score': return { label: 'Score', rule: `At least ${s100(v)} of 100${s.evaluated && t.relaxed_to_floor ? ' (the configured floor; the pool was too small for a median)' : ''}` };
      case 'risk_reward': return { label: 'Reward to risk', rule: `Net reward-to-risk at least ${rr(v)} after costs` };
      case 'conviction': return { label: 'Conviction', rule: `At least ${v} of 5${s.evaluated && t.relaxed ? '; no name cleared it, so the names that passed every risk rule were kept and flagged' : ''}` };
      case 'cost': return { label: 'Covers trading costs', rule: `Expected return at least ${v}× the round-trip cost` };
      case 'selection': return { label: 'Fits portfolio limits', rule: 'Position, sector, correlation and stop-risk limits, up to the maximum positions' };
      case 'final_review': return { label: 'Confirmed by the Research check', rule: 'Research check and final decision' };
      case 'mandate': return { label: 'Kept under your mandate', rule: 'Risk profile sizing' };
      default: return { label: String(s.key || '').replace(/_/g, ' '), rule: '' };
    }
  }

  /* [I8] Tickers after a count: the first UI.NAME_LIST_SHOWN, the rest behind Show all. */
  function tickerTail(list) {
    const all = (list || []).map(String);
    if (!all.length) return '';
    const shown = Math.max(1, Number(proThr('UI.NAME_LIST_SHOWN')) || all.length);
    const head = esc(all.slice(0, shown).join(', '));
    const rest = all.slice(shown);
    return `: ${head}${rest.length ? ` <details class="name-list-more"><summary>Show all ${all.length}</summary>${esc(rest.join(', '))}</details>` : ''}`;
  }

  function reasonList(byReason, tickers) {
    return Object.entries(byReason || {})
      .filter(([, n]) => Number(n) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]) || String(a[0]).localeCompare(String(b[0])))
      .map(([k, n]) => `${esc(reasonWords(k))} ${Number(n)}${tickerTail((tickers || {})[k])}`)
      .join(' · ');
  }

  function stageTableCard(data) {
    const a = data && data.accounting;
    if (!a || !a.version) return '';
    const universe = Number(a.universe || 0);
    const failed = (a.could_not_analyse || {});
    const rejected = (a.rule_rejected || {});
    const reached = Number(a.reached_book || 0);
    const notAnalysed = Number(a.not_analysed || 0);
    const nad = a.not_analysed_detail || {};
    const notAnalysedWhy = [
      Number(nad.not_reached) ? `${Number(nad.not_reached)} not reached before the provider limit` : '',
      Number(nad.other) ? `${Number(nad.other)} with no recorded result (duplicate or invalid symbol)` : '',
    ].filter(Boolean).join(' · ');
    const outcomeRows = [
      { label: 'Could not be analysed', n: Number(failed.count || 0), detail: reasonList(failed.by_reason, failed.tickers) || 'Missing or unusable data and provider errors' },
      { label: 'Screened out', n: Number(rejected.count || 0), detail: reasonList(rejected.by_reason) || 'Price, liquidity, size, score and risk rules' },
      notAnalysed ? { label: 'Not analysed', n: notAnalysed, detail: notAnalysedWhy } : null,
      { label: 'Reached the book', n: reached, detail: 'Names that passed every per-name rule' },
    ].filter(Boolean);
    const stageRows = (a.book_stages || []).map((s) => {
      const r = stageRule(s);
      if (!s.evaluated) {
        const why = s.entered == null ? 'not reached' : 'no names reached this stage';
        return `<tr class="qpro-stage-ne" data-stage="${esc(s.key)}"><th scope="row">${esc(r.label)}</th>
          <td colspan="2" class="qpro-stage-muted">Not evaluated (${why})</td><td>${esc(r.rule)}</td></tr>`;
      }
      const out = Number(s.entered) - Number(s.passed);
      // [I2] The event stage holds names at Watch (published, never funded) or
      // marks them AVOID; say which, and name them.
      const named = (g, what) => (g && Number(g.count)
        ? `${Number(g.count)} ${what}${(g.tickers || []).length ? `: ${(g.tickers || []).join(', ')}` : ''}` : '');
      const eventOut = s.key === 'event' && (s.held || s.avoided)
        ? [named(s.held, 'held at Watch'), named(s.avoided, 'avoided')].filter(Boolean).join(' · ') : '';
      // [I7] A second share class of an issuer already in the book.
      const si = s.key === 'selection' && s.same_issuer && Number(s.same_issuer.count)
        ? `${Number(s.same_issuer.count)} skipped as the same issuer: ${(s.same_issuer.tickers || [])
          .map((t) => `${t}${(s.same_issuer.as || {})[t] ? ` (as ${(s.same_issuer.as || {})[t]})` : ''}`).join(', ')}` : '';
      const rest = si && out > Number(s.same_issuer.count) ? ` · ${out - Number(s.same_issuer.count)} held back by other limits` : '';
      const outText = eventOut || (si ? `${si}${rest}` : (out ? `${out} held back` : 'none held back'));
      return `<tr data-stage="${esc(s.key)}"><th scope="row">${esc(r.label)}</th>
        <td class="mono">${Number(s.passed)} of ${Number(s.entered)}</td>
        <td class="mono">${esc(outText)}</td><td>${esc(r.rule)}</td></tr>`;
    }).join('');
    const warnings = Object.entries(a.warnings || {}).filter(([, n]) => Number(n) > 0);
    const warnHtml = warnings.length
      ? `<p class="qpro-stage-warn" data-stage-warnings><strong>Warnings, counted separately:</strong> ${warnings.map(([k, n]) => `${esc(reasonWords(k))} ${Number(n)}`).join(' · ')}. A warning does not screen a name out on its own.</p>`
      : '';
    const watchTotal = Number(a.watch_total || 0);
    const watchShown = Number(a.watch_published ?? watchTotal);
    const capHtml = watchTotal > watchShown
      ? `<p class="qpro-stage-muted">${watchShown} of ${plural(watchTotal, 'watch row')} shown (limit ${Number(a.max_watch_rows || watchShown)}).</p>` : '';
    return `
      <article class="qpro-card qpro-funnel-card qpro-stage-card" data-accounting>
        <h3 class="qpro-kicker">Where the ${universe} names went</h3>
        <p class="qpro-stage-sum" data-reconcile>Each of the ${universe} scanned names has one outcome below; ${plural((data.stock_picks || []).filter((r) => ['buy', 'accumulate'].includes(r.call)).length, 'name')} got a Buy or Accumulate call.</p>
        <div class="table-scroll" tabindex="0" role="region" aria-label="Per-name outcomes">
          <table class="qpro-stage-table">
            <thead><tr><th scope="col">Outcome</th><th scope="col">Names</th><th scope="col">Why</th></tr></thead>
            <tbody>${outcomeRows.map((o) => `<tr><th scope="row">${esc(o.label)}</th><td class="mono">${o.n}</td><td>${o.detail}</td></tr>`).join('')}</tbody>
          </table>
        </div>
        ${Number(a.reached_book) === 0 && (a.book_stages || []).length
    ? `<p class="qpro-stage-muted" data-no-book>No name reached the book, so none of its ${(a.book_stages || []).length} stages ran.</p>`
    : `<div class="table-scroll" tabindex="0" role="region" aria-label="Book stages">
          <table class="qpro-stage-table">
            <thead><tr><th scope="col">Book stage</th><th scope="col">Passed</th><th scope="col">Held back</th><th scope="col">Rule</th></tr></thead>
            <tbody>${stageRows}</tbody>
          </table>
        </div>`}
        ${warnHtml}${capHtml}
      </article>`;
  }

  function funnelCard(data) {
    if (data && data.accounting && data.accounting.version) return stageTableCard(data);
    const f = data && data.funnel;
    if (!f) return '';
    const gs = f.gate_stats || {};
    const fs = f.filter_stats || {};
    const universe = Number(f.universe_size || 0);
    const opps = Number(f.opportunities || 0);
    const finalCount = Number(f.final || 0);
    const fundedCount = ((data && data.recommendations) || []).filter(isFundedRec).length;
    const analyzedOk = isNum(num(f.analyzed_success)) ? num(f.analyzed_success) : null;
    const analysisFailed = isNum(num(f.analysis_failed)) ? num(f.analysis_failed)
                          : (isNum(num(f.failed_tickers)) ? num(f.failed_tickers) : null);
    const excRate = isNum(num(f.exception_rate)) ? num(f.exception_rate) : null;

    // Build funnel stages
    const stages = [
      { k: 'Universe',         v: universe,                  note: 'Scanned tickers' },
      { k: 'Analyzed OK',      v: analyzedOk,                note: 'Completed analysis (no exception)' },
      { k: 'Failed analysis',  v: analysisFailed,            note: excRate != null ? `${(excRate * 100).toFixed(0)}% exception rate` : 'Exceptions / timeouts' },
      // "Passed every filter", not "passed analysis": with 250 analysed and 0 passing,
      // the old wording contradicted the line above it (audit U2).
      { k: 'Survived filters', v: opps,                      note: `Passed every per-name filter: ${opps} of ${analyzedOk != null ? analyzedOk : universe} analysed` },
      { k: 'Actionable',       v: gs.gate1_actionable,       note: 'BUY / STRONG BUY / ACCUMULATE only' },
      // The banned grades come from config (F7 lets grade D in at half size).
      { k: 'Risk A\u2013D',       v: gs.gate2_risk_safe,        note: `Grade ${(proThr('BOOK.BANNED_GRADES') || []).join(', ')} excluded` },
      { k: isNum(num(gs.dynamic_min_score)) ? `Score \u2265 ${s100(num(gs.dynamic_min_score))} of 100` : `Score (not evaluated; minimum ${s100(proThr('SCORE.ACCUMULATE'))} of 100)`,
                              v: gs.gate3_score_qualified,  note: gs.score_floor_relaxed ? 'Floor (tiny pool relax)' : 'Adaptive percentile' },
      { k: isNum(num(gs.min_rr)) ? `R:R \u2265 ${num(gs.min_rr).toFixed(1)}:1` : `R:R (not evaluated; minimum ${proRR(proThr('RR.SCANNER_MIN'))})`,
                              v: gs.gate4_rr_qualified,     note: 'After execution-cost hurdle' },
      { k: gs.min_conviction ? `Conviction \u2265 ${gs.min_conviction}/5` : `Conviction (not evaluated; minimum ${proThr('BOOK.CONVICTION_FLOOR_SMALL')}/5)`,
                              v: gs.gate5_conviction,       note: 'Adaptive star floor' },
      { k: 'Cost-viable',     v: gs.gate6_cost_viable,      note: `Return \u2265 ${proThr('BOOK.COST_VIABILITY_MULTIPLE') ?? '\u2014'}\u00d7 round-trip cost` },
      { k: 'Provisionally sized', v: gs.gate7_sector_cap, note: 'Before additional risk checks and client mandate' },
      { k: 'Sized', v: fundedCount, note: 'Suggested positions after all checks' },
      { k: 'Published',       v: finalCount,                note: finalCount === 0 ? 'No qualifying setups'
                                                                 : `${fundedCount} sized \u00b7 ${finalCount - fundedCount} Watchlist after deep review` },
    ].filter((s) => isNum(s.v));

    if (!stages.length) return '';
    const maxV = Math.max(...stages.map((s) => Number(s.v) || 0), 1);

    // Filter-stats top reasons
    const filterEntries = Object.entries(fs)
      .filter(([k, v]) => isNum(Number(v)) && Number(v) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .slice(0, 6);
    const rejectedCount = Math.max(0, universe - opps);

    return `
      <article class="qpro-card qpro-funnel-card">
        <h3 class="qpro-kicker">Scan funnel
          <span class="qpro-kicker-tail">where the ${universe} stocks went</span>
        </h3>
        <div class="qpro-funnel">
          ${stages.map((s) => {
            const v = Number(s.v) || 0;
            const pct = (v / maxV) * 100;
            const isFinal = s.k === 'Sized';
            const isFailed = s.k === 'Failed analysis';
            const cls = isFailed && v > 0 ? 'bear'
                      : (isFinal && v === 0 ? 'bear' : (isFinal ? 'bull' : ''));
            return `
              <div class="qpro-funnel-row ${cls}">
                <span class="qpro-funnel-label">${esc(s.k)}</span>
                <div class="qpro-funnel-bar-wrap">
                  <div class="qpro-funnel-bar" style="width:${v ? Math.max(2, pct) : 0}%"></div>
                </div>
                <span class="qpro-funnel-count">${v}</span>
                <span class="qpro-funnel-note">${esc(s.note || '')}</span>
              </div>`;
          }).join('')}
        </div>
        ${filterEntries.length ? `
          <div class="qpro-filter-breakdown">
            <div class="qpro-filter-title">Why ${rejectedCount} names were filtered out
              <span class="qpro-filter-note">Share of rejected tickers. A name can fail more than one reason.</span>
            </div>
            <div class="qpro-filter-bars">
              ${filterEntries.map(([k, v]) => {
                const pct = (Number(v) / (rejectedCount || 1)) * 100;
                return `
                  <div class="qpro-filter-bar">
                    <span class="qpro-filter-k">${esc(k.replace(/_/g, ' '))}</span>
                    <div class="qpro-filter-track"><div class="qpro-filter-fill" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div>
                    <span class="qpro-filter-v">${v} \u00b7 ${pct.toFixed(0)}%</span>
                  </div>`;
              }).join('')}
            </div>
          </div>` : ''}
      </article>`;
  }

  // ----- 10. Analyzer-Disagreement Watchlist -----------------------------
  function disagreementWatchlistCard(data) {
    const list = (data && (data.analyzer_disagreement_watchlist
                  || (data.deep_confirmation && data.deep_confirmation.rejected))) || [];
    if (!list.length) return '';
    const rows = list.slice(0, 10).map((r) => {
      const scanSig = String(r.scanner_signal || '').toUpperCase();
      // The analyzer column must NEVER read INVALID for a real ticker whose
      // history merely failed to load. row_kind drives honest labelling.
      const rowKind = String(r.row_kind || '').toLowerCase();
      const notConfirmed = rowKind === 'not_confirmed' || r.not_independently_confirmed === true;
      const anaSig = String(r.analyzer_signal || (notConfirmed ? 'NOT CONFIRMED' : '')).toUpperCase();
      const scanCls = /BUY|ACCUMULATE/.test(scanSig) ? 'bull' : /SELL|REDUCE/.test(scanSig) ? 'bear' : 'warn';
      const anaCls = notConfirmed ? 'warn'
        : /BUY|ACCUMULATE/.test(anaSig) ? 'bull' : /SELL|REDUCE/.test(anaSig) ? 'bear' : 'warn';
      const validation = r.validation_status
        || (notConfirmed ? 'Not independently confirmed'
            : rowKind === 'invalid' ? 'Invalid ticker' : 'Analyzer disagrees');
      // Score display: show the scanner score when available; never render a
      // bare "--/100". When the analyzer could not score, say N/A + why.
      const sScore = num(r.scanner_score);
      const aScore = num(r.analyzer_score);
      const scoreReason = r.score_reason || '';
      const scoreCell = isNum(aScore)
        ? `${s100(aScore)}/100`
        : (isNum(sScore)
            ? `<span title="Analyzer score N/A">${s100(sScore)}/100 <span class="qpro-tx-sub">scanner</span></span>`
            : `<span class="qpro-tx-sub" title="${esc(scoreReason)}">N/A</span>`);
      const reasonText = r.reason || '';
      const riskFactors = Array.isArray(r.risk_factors) ? r.risk_factors.filter(Boolean) : [];
      const reasonExtra = riskFactors.length
        ? `<div class="qpro-tx-sub">${esc(riskFactors[0])}</div>`
        : (scoreReason && !isNum(aScore) ? `<div class="qpro-tx-sub">${esc(scoreReason)}</div>` : '');
      return `
        <tr>
          <td class="ticker">${esc(r.ticker || '')}</td>
          <td>${esc(r.company || '')}</td>
          <td>${esc(r.sector || '')}</td>
          <td><span class="qpro-conflict-tag ${scanCls}">${esc(scanSig || '--')}</span></td>
          <td><span class="qpro-conflict-tag ${anaCls}">${esc(anaSig || 'N/A')}</span></td>
          <td><span class="qpro-conflict-tag ${notConfirmed ? 'warn' : (rowKind === 'invalid' ? 'bear' : 'warn')}">${esc(validation)}</span></td>
          <td class="mono">${scoreCell}</td>
          <td class="qpro-tx-text">${esc(reasonText)}${reasonExtra}</td>
        </tr>`;
    }).join('');
    return `
      <article class="qpro-card qpro-watchlist-card">
        <h3 class="qpro-kicker">Watchlist \u00b7 Scanner pick not confirmed
          <span class="qpro-kicker-tail">${list.length} flagged \u2014 not actionable, monitor only</span>
        </h3>
        <div class="qpro-watchlist-wrap table-scroll" tabindex="0" role="region" aria-label="Scanner picks not confirmed table">
          <table class="qpro-watchlist-table">
            <thead>
              <tr>
                <th>Ticker</th><th>Company</th><th>Sector</th>
                <th>Scanner</th><th>Analyzer Decision</th>
                <th>Validation Status</th><th>Score</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <p class="qpro-watchlist-foot">These names ranked well in the batch scanner but were not confirmed for new capital. Rows marked <strong>Not independently confirmed</strong> failed deep validation for a data reason (e.g. the analyzer could not fetch enough historical bars) \u2014 they are NOT invalid and NOT bearish disagreements. Rows marked <strong>Analyzer disagrees</strong> were reconciled to a non-buy action on risk/valuation grounds. Treat all as monitor-only until they confirm.</p>
      </article>`;
  }


  function injectPicksFunnel(data) {
    const container = document.getElementById('picks-results');
    if (!container) return;
    const stream = container.querySelector('.picks-stream');
    if (!stream) return;
    if (stream.querySelector('.qpro-funnel-card')) return; // already injected
    const html = funnelCard(data);
    if (!html) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const node = wrap.firstElementChild;
    if (!node) return;
    // Insert AFTER the portfolio-summary (or empty-state) header, before bucket tables
    const audit = stream.querySelector('.portfolio-diagnostics');
    const summary = stream.querySelector('.portfolio-summary, .empty-state');
    if (audit) {
      const heading = audit.querySelector('summary');
      audit.insertBefore(node, heading?.nextSibling || audit.firstChild);
    } else if (summary && summary.parentNode === stream) {
      stream.insertBefore(node, summary.nextSibling);
    } else {
      stream.insertBefore(node, stream.firstChild);
    }
  }

  function injectAnalyzerWatchlist(data) {
    const container = document.getElementById('picks-results');
    if (!container) return;
    const stream = container.querySelector('.picks-stream');
    if (!stream) return;
    if (stream.matches('[data-picks-v2]') || stream.querySelector('.pick-card, .pick-lazy')) return; // the rows already carry these names
    if (stream.querySelector('.qpro-watchlist-card')) return;
    const html = disagreementWatchlistCard(data);
    if (!html) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const node = wrap.firstElementChild;
    if (!node) return;
    stream.appendChild(node);
  }

  // Expose API
  const _root = (typeof window !== 'undefined') ? window
    : (typeof globalThis !== 'undefined' ? globalThis : this);
  _root.QuantaPro = {
    thesisCard,
    conflictCard,
    insiderCard,
    whyHoldRisksCard,
    decoratePickCards,
    injectAnalysisExtras,
    injectPicksExtras,
    injectPicksFunnel,
    injectAnalyzerWatchlist,
    injectBuilderExtras,
    funnelCard,
    stageTableCard,
    disagreementWatchlistCard,
    portfolioRiskCard,
    capitalAllocationTable,
    executionCaveatsCard,
    tradingViewExportCard,
    peerComparisonCard,
    injectDeepDiveExtras,
  };

  // [TEST] Expose pure render helpers for Node-based unit tests. Guarded so the
  // browser bundle is unaffected (module is undefined in the browser).
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { portfolioRiskCard, disagreementWatchlistCard, isFundedRec, executionCaveatsCard, funnelCard, stageTableCard, capitalAllocationTable, tradingViewExportCard };
  }
})();
