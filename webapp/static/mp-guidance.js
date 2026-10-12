/* Guidance: "what if you act" and the hero decision's effect on risk.
 *
 * The preview is the server's own arithmetic (POST /api/portfolio/whatif): the same
 * compute_metrics and risk rule as the view, re-run with the chosen sales moved to cash.
 * Choices per decision are only the scenarios the server defines: keep, trim to the
 * single-name limit (where QUANTA sizes one), or sell in full. Nothing here invents a
 * size; an unsized trim shows the full sale as its outer bound and says so.
 */
(function (root) {
  'use strict';

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const H = host._h;
    const { esc, num, money, pct, plural } = H;
    const ins = X.ins;
    const state = host._state;
    const Q = () => root.QuantaCharts;
    ins.wi = { choice: {}, res: {}, s: 'idle', key: '', stamp: null, hero: null };

    const keyOf = (changes) => changes.slice().sort((a, b) => a.symbol.localeCompare(b.symbol))
      .map((c) => `${c.symbol}:${Math.round(c.delta_value * 100)}`).join(',');

    // The decisions a preview can act on, with the server's own scenario sizes.
    function options(pf) {
      return H.decisionOrder(pf).filter((r) => r.guidance.action === 'Trim' || r.guidance.action === 'Exit Review').map((r) => {
        const im = r.guidance.impact || {};
        const opts = [{ k: 'keep', label: 'Keep', delta: null }];
        if (im.trim_to_cap && num(im.trim_to_cap.delta_value) !== null) opts.push({ k: 'trim', label: `To ${pct(im.trim_to_cap.weight_after, 0)}`, delta: im.trim_to_cap.delta_value, long: `Trim to ${pct(im.trim_to_cap.weight_after, 0)}` });
        if (im.remove && num(im.remove.delta_value) !== null) opts.push({ k: 'all', label: 'Sell all', delta: im.remove.delta_value, long: 'Sell in full' });
        return { r, opts, sized: opts.some((o) => o.k === 'trim') };
      }).filter((x) => x.opts.length > 1);
    }
    const changesFor = (pf) => options(pf).map((x) => {
      const o = x.opts.find((y) => y.k === (ins.wi.choice[x.r.symbol] || 'keep'));
      return o && o.delta !== null ? { symbol: x.r.symbol, delta_value: o.delta } : null;
    }).filter(Boolean);

    async function fetchWhatif(changes) {
      const key = keyOf(changes);
      if (ins.wi.res[key]) return ins.wi.res[key];
      let res;
      try { res = await H.api('POST', '/whatif', { changes }); } catch (_) { res = { ok: false, status: 0, data: {} }; }
      const out = res.status === 404 ? { missing: true } : res.ok && res.data && res.data.available ? res.data : { error: (res.data && (res.data.reason || res.data.error)) || 'The preview could not be computed.' };
      if (!out.error) ins.wi.res[key] = out;
      return out;
    }

    let timer = 0;
    function preview() {
      const pf = state.view && state.view.portfolio;
      if (!pf) return;
      const changes = changesFor(pf);
      const key = keyOf(changes);
      ins.wi.key = key;
      if (ins.wi.res[key]) { ins.wi.s = 'ok'; X.paintSlot('gd-wi-prev'); return; }
      ins.wi.s = 'loading';
      X.paintSlot('gd-wi-prev');
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const out = await fetchWhatif(changes);
        if (ins.wi.key !== key) return;                 // a newer choice superseded this one
        ins.wi.s = out.missing ? 'missing' : out.error ? 'error' : 'ok';
        ins.wi.err = out.error || '';
        X.paintSlot('gd-wi-prev');
      }, 120);
    }

    // ------------------------------------------------------------ rendering
    const arrow = '<span aria-hidden="true">→</span><span class="sr-only">to</span>';
    const topOf = (m) => { const w = m.weights || {}; const k = Object.keys(w).sort((a, b) => w[b] - w[a])[0]; return k ? { symbol: k, w: w[k] } : null; };
    const topSector = (m) => (m.sectors || []).filter((s) => H.limitedSector(s.sector) && num(s.weight) !== null).sort((a, b) => b.weight - a.weight)[0];

    function donutAfter(pf, m) {
      const pol = pf.policy || {};
      const items = (m.sectors || []).filter((s) => s.sector !== 'Cash' && (s.weight || 0) > 0).map((s) => {
        const classified = H.limitedSector(s.sector);
        const limit = classified ? num(pol.max_sector) : null;
        return { name: classified ? s.sector : 'Funds and unclassified', weight: s.weight, symbols: s.symbols || [], classified, limit, over: limit !== null && s.weight >= limit };
      }).sort((a, b) => b.weight - a.weight);
      const segs = X.slices ? X.slices({ items }, 'sector', num(m.cash_weight) || 0) : [];
      const lead = segs.filter((x) => x.key !== 'cash').sort((a, b) => b.value - a.value)[0];
      return Q() && segs.length ? Q().donutHtml(segs, { centerValue: lead ? pct(lead.value, 0) : '', centerLabel: lead ? lead.label : '' }) : '';
    }

    function previewInner(pf) {
      const W = ins.wi;
      const res = W.res[W.key];
      if (W.s === 'missing') return '<p class="hp-fine">The preview appears after a QUANTA restart.</p>';
      if (W.s === 'error' && !res) return `<p class="mp-unavail" role="status">${esc(W.err || 'The preview could not be computed.')} <button type="button" class="q-btn q-btn--link" data-hp-x="wi-retry:">Try again</button></p>`;
      if (!res) return '<div class="hp-wi-grid" aria-busy="true"><span class="mp-skel mp-skel--ring"></span><span class="mp-skel mp-skel--legend"></span></div>';
      const b = res.before; const a = res.after;
      const t0 = topOf(b); const t1 = topOf(a);
      const s0 = topSector(b); const s1 = s0 ? (a.sectors || []).find((s) => s.sector === s0.sector) : null;
      const none = !res.changes.length;
      const row = (k, v0, v1) => `<div><dt>${k}</dt><dd>${none ? v0 : `${v0} ${arrow} <b>${v1}</b>`}</dd></div>`;
      const rb = b.risk || {}; const ra = a.risk || {};
      return `<div class="hp-wi-grid${W.s === 'loading' ? ' is-busy' : ''}"${W.s === 'loading' ? ' aria-busy="true"' : ''}>
        <div class="hp-wi-donut">${donutAfter(pf, a)}</div>
        <div class="hp-wi-risk">${X.riskGauge ? X.riskGauge(ra, { size: 'sm', ghost: none ? null : rb.points, ariaLabel: `Risk ${none ? '' : `after: `}${ra.level}, ${ra.points} of 9 points${none ? '' : `; now ${rb.level}, ${rb.points}`}.` }) : ''}
          <p class="hp-wi-lvl">${none ? `${esc(rb.level || '')}, ${rb.points} of 9 points` : rb.level === ra.level ? `${esc(ra.level || '')}, ${rb.points} ${arrow} <b>${ra.points}</b> of 9 points` : `${esc(rb.level || '')} ${arrow} <b>${esc(ra.level || '')}</b>`}</p></div>
        <dl class="hp-wi-dl">
          ${row('Cash', `${money(b.cash)} (${pct(b.cash_weight, 1)})`, `${money(a.cash)} (${pct(a.cash_weight, 1)})`)}
          ${t0 ? row('Top position', `<span class="hp-sym">${esc(t0.symbol)}</span> ${pct(t0.w, 1)}`, t1 ? `<span class="hp-sym">${esc(t1.symbol)}</span> ${pct(t1.w, 1)}` : '--') : ''}
          ${s0 ? row(esc(s0.sector), pct(s0.weight, 1), s1 ? pct(s1.weight, 1) : pct(0, 1)) : ''}
        </dl>
      </div>
      <p class="hp-wi-note" role="status">${none ? 'Choose a change above to preview it.' : `Frees ${money(res.freed)} to cash. ${esc(res.basis || '')}`}</p>`;
    }
    X.slotRenderers['gd-wi-prev'] = previewInner;

    function whatifPanel(pf) {
      const list = options(pf);
      if (!list.length) return '';
      const unsized = list.filter((x) => !x.sized).length;
      return `<section class="hp-wi" aria-labelledby="hp-wi-h">
        <header class="hp-gc-h"><h3 class="hp-gc-t" id="hp-wi-h">What if you act</h3><span class="hp-gc-n">Preview, not orders</span></header>
        <ul class="hp-wi-list">${list.map((x) => {
          const cur = ins.wi.choice[x.r.symbol] || 'keep';
          return `<li data-sym="${esc(x.r.symbol)}"><span class="hp-sym">${esc(x.r.symbol)}</span><span class="hp-num hp-wi-w">${pct(x.r.broker.weight, 1)}</span>
            <span class="mpc-seg hp-wi-seg" role="group" aria-label="${esc(`${x.r.symbol}: ${x.r.guidance.action}`)}">${x.opts.map((o) => `<button type="button" class="mpc-segb${cur === o.k ? ' is-on' : ''}" data-hp-x="wi:${esc(x.r.symbol)}:${o.k}" aria-pressed="${cur === o.k}"${o.long ? ` aria-label="${esc(o.long)}"` : ''}>${esc(o.label)}</button>`).join('')}</span></li>`;
        }).join('')}</ul>
        <div data-mp-slot="gd-wi-prev" class="hp-wi-prev">${previewInner(pf)}</div>
        ${unsized ? `<p class="hp-fine">QUANTA sizes a trim only above the ${pct((pf.policy || {}).max_single_position, 0)} single-name limit; for ${unsized === 1 ? 'the other holding' : `the other ${unsized}`}, selling in full is the outer bound, and the right size is yours.</p>` : ''}
      </section>`;
    }
    X.slotRenderers['gd-whatif'] = whatifPanel;

    // The hero's effect on the risk rule: before and after the sized scenario alone.
    function heroRisk(pf, r) {
      const x = H.sizedScenario(r.guidance);
      if (!x || num(x.delta_value) === null) return '';
      const W = ins.wi;
      const key = keyOf([{ symbol: r.symbol, delta_value: x.delta_value }]);
      W.hero = { key, symbol: r.symbol, delta_value: x.delta_value };
      const res = W.res[key];
      const wrap = (dd) => `<dl class="hp-hero-dl"><div><dt>Risk</dt>${dd}</div></dl>`;
      if (!res) return W.heroMissing ? '' : wrap('<dd><span class="mp-skel mpk-skel"></span></dd>');
      const b = res.before.risk || {}; const a = res.after.risk || {};
      // Same band: say how far the points moved, so an unchanged label is not read as no effect.
      const same = b.level === a.level;
      const after = same ? `${esc(a.level || '')}, ${num(b.points)} → ${num(a.points)} of 9 points` : esc(a.level || '');
      return wrap(`<dd class="hp-hero-gauges">${X.riskGauge(b, { size: 'xs', ariaLabel: `Risk now ${b.level}, ${b.points} of 9 points.` })}${same ? '' : `<span>${esc(b.level || '')}</span>${arrow}`}${X.riskGauge(a, { size: 'xs', ghost: b.points, ariaLabel: `Risk after this trim ${a.level}, ${a.points} of 9 points.` })}<b>${after}</b></dd>`);
    }
    X.slotRenderers['gd-hero-risk'] = (pf) => {
      const hero = H.heroOf(H.decisionOrder(pf));
      return hero ? heroRisk(pf, hero) : '';
    };

    async function loadHero() {
      const pf = state.view && state.view.portfolio;
      if (!pf) return;
      X.slotRenderers['gd-hero-risk'](pf);
      const h = ins.wi.hero;
      if (!h || ins.wi.res[h.key]) return;
      const out = await fetchWhatif([{ symbol: h.symbol, delta_value: h.delta_value }]);
      ins.wi.heroMissing = !!(out.missing || out.error);
      X.paintSlot('gd-hero-risk');
    }

    Object.assign(X.actions, {
      wi(arg) {
        const [sym, k] = String(arg).split(':');
        if (!sym || !['keep', 'trim', 'all'].includes(k)) return;
        ins.wi.choice[sym] = k;
        X.paintSlot('gd-whatif');
        const btn = typeof document !== 'undefined' && document.querySelector(`[data-hp-x="wi:${CSS.escape(sym)}:${k}"]`);
        if (btn) btn.focus({ preventScroll: true });
        preview();
      },
      'wi-retry'() { ins.wi.s = 'idle'; preview(); },
    });

    // New data invalidates every preview; load the hero's and the current choice's with it.
    const baseAfter = X.afterRender;
    X.afterRender = (pf) => {
      baseAfter(pf);
      if (!pf) return;
      const stamp = `${pf.retrieved_at}:${((pf.freshness || {}).analysis || {}).done || 0}`;
      if (ins.wi.stamp !== stamp) { ins.wi.stamp = stamp; ins.wi.res = {}; ins.wi.heroMissing = false; }
      loadHero();
      if (state.tab === 'guidance') preview();
    };
    const prevTab = X.onTab;
    X.onTab = (key) => { if (prevTab) prevTab(key); if (key === 'guidance') { loadHero(); preview(); } };
    const baseReset = X.reset;
    X.reset = (keep) => { baseReset(keep); ins.wi = { choice: {}, res: {}, s: 'idle', key: '', stamp: null, hero: null }; };
    if (typeof document !== 'undefined') {
      const view = document.getElementById('view-holdings');
      if (view && !view._mpGdBound) {
        view._mpGdBound = true;
        // "Review" links open Guidance without a tab click; load its previews too.
        view.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('[data-hp-review]')) setTimeout(() => X.onTab('guidance'), 0); });
      }
    }

    Object.assign(X, { whatifPanel, heroRisk, whatifKey: keyOf, whatifOptions: options });
    return X;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaGuidance = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
