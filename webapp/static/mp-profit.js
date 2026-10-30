/* Profit plan — "What do I book, what do I protect, and what pays the tax?"
 *
 * Reads GET /api/portfolio/profit-plan (core/portfolio/profit_plan.py) and draws:
 *   the gains split (locked under a profit stop vs exposed above it) and what booking now
 *   would realise, net of the tax-loss candidates;
 *   a gain map, one row per holding, each opening a six-month chart of the price, its
 *   profit stop, the cost and QUANTA's valuation target;
 *   two paths back inside a sector limit (tax-cheapest and pro rata).
 * Every rule is a stated rule of thumb with its parameter; none is validated or an order,
 * and the page says so where the numbers are. Amounts follow the hide-amounts switch.
 */
(function (root) {
  'use strict';

  const ACT = ['STOP_HIT', 'BOOK'];

  function attach(host) {
    const X = host.ext;
    if (!X || !X.ins) return null;
    const H = host._h;
    const { esc, num, NA, priv, money, signedMoney, price, pct, plural } = H;
    const state = host._state;
    const ins = X.ins;
    ins.profit = ins.profit || { s: 'idle' };
    let pathView = 'cheapest';
    let rules = {};

    // ------------------------------------------------------------ loading
    async function load(force) {
      const pf = state.view && state.view.portfolio;
      if (!pf) return;
      // A new read of the account, or more of the analysis done (funds, valuation targets).
      const a = (pf.freshness || {}).analysis || {};
      const stamp = `${pf.retrieved_at || ''}:${a.state || ''}:${a.done || 0}`;
      const cur = ins.profit;
      if (cur.s === 'loading' || (cur.s === 'ok' && cur.stamp === stamp && !force)) return;
      ins.profit = { s: 'loading', data: cur.data, stamp };
      X.paintSlot('profit-body');
      let res;
      try { res = await H.api('GET', '/profit-plan'); } catch (_) { res = { ok: false }; }
      ins.profit = res.ok ? { s: 'ok', data: res.data, stamp }
        : { s: res.status === 404 ? 'missing' : 'error', data: null, stamp };
      X.paintSlot('profit-body');
      refreshBadge();
    }
    // The tab's count (holdings to act on) arrives after the tabs are drawn: update it in place.
    function refreshBadge() {
      if (typeof document === 'undefined') return;
      const tab = document.getElementById('hp-tab-profit');
      if (!tab) return;
      const c = (X.counts(state.view && state.view.portfolio) || {}).profit;
      let n = tab.querySelector('.hp-tab-n');
      if (c) { if (!n) { n = document.createElement('span'); n.className = 'hp-tab-n'; tab.append(' ', n); } n.textContent = c; } else if (n) n.remove();
    }

    // ------------------------------------------------------------ pieces
    const chip = (sig) => `<span class="pp-chip pp-chip--${esc(String(sig.code || 'hold').toLowerCase())}">${esc(sig.label)}</span>`;
    const shares = (v) => (num(v) === null ? NA : priv(Number(v).toFixed(Math.abs(v) >= 10 ? 1 : 2)));
    const share = (part, whole) => (whole > 0 ? Math.max(0, Math.min(100, (part / whole) * 100)) : 0);

    function heroHtml(d) {
      const t = d.totals || {};
      const b = d.book || {};
      const hv = d.harvest || {};
      const st = d.stops || {};
      const gain = num(t.gain) || 0;
      const locked = num(t.locked) || 0;
      const exposed = (num(t.exposed) || 0) + (num(t.unprotected) || 0);
      const lw = share(locked, gain);
      const split = gain > 0
        ? `<div class="pp-split" role="img" aria-label="${esc(`Of the open gains, ${Math.round(lw)} percent is under a profit stop and ${Math.round(100 - lw)} percent is above it.`)}">
            <i class="pp-seg is-locked" style="--w:${lw.toFixed(2)}%"></i><i class="pp-seg is-exposed" style="--w:${(100 - lw).toFixed(2)}%"></i></div>
          <ul class="pp-legend">
            <li><i class="pp-key is-locked" aria-hidden="true"></i>Locked by stops <b>${money(locked)}</b></li>
            <li><i class="pp-key is-exposed" aria-hidden="true"></i>Exposed above stops <b>${money(exposed)}</b></li>
            ${num(t.losses) ? `<li><i class="pp-key is-loss" aria-hidden="true"></i>Open losses <b>${signedMoney(t.losses)}</b></li>` : ''}
          </ul>`
        : '<p class="hp-empty">No holding is above its cost, so there is no gain to protect.</p>';
      const lead = gain > 0
        ? `<b>${money(locked)}</b> of your <b>${money(gain)}</b> in open gains sits under a profit stop. <b class="pp-amber">${money(exposed)}</b> is exposed: a fall to the stops would give it back.`
        : 'Nothing to protect yet.';
      const net = num(hv.net_gain);
      const term = num(b.gain_long) !== null && num(b.gain_short) !== null
        ? `<span class="hp-sub">${money(b.gain_long)} long-term · ${money(b.gain_short)} short-term</span>` : '';
      const side = b.count
        ? `<dl class="pp-book">
            <div><dt>Sell</dt><dd>${money(b.value)}<span class="hp-sub">${b.count} ${plural(b.count, 'holding', 'holdings')}, at today’s prices</span></dd></div>
            <div><dt>Gain realised</dt><dd class="pp-up">${signedMoney(b.gain)}${term}</dd></div>
            ${hv.count ? `<div><dt>Tax-loss candidates</dt><dd class="pp-down">${signedMoney(hv.loss)}<span class="hp-sub">${hv.count} ${plural(hv.count, 'holding', 'holdings')}${hv.wash_risk && hv.wash_risk.length ? `; wash-sale window open on ${esc(hv.wash_risk.join(', '))}` : ''}</span></dd></div>` : ''}
            ${net !== null && hv.count ? `<div class="pp-net"><dt>Net gain if you do both</dt><dd>${signedMoney(net)}</dd></div>` : ''}
            ${num(b.cash_after_pct) !== null ? `<div><dt>Cash after</dt><dd>${pct(b.cash_after_pct, 1)}<span class="hp-sub">${money(b.cash_after)}</span></dd></div>` : ''}
          </dl>`
        : '<p class="pp-quiet">No booking rule fires today: nothing to sell for profit.</p>';
      const watch = st.count
        ? `<p class="pp-watch"><b>Watch the close.</b> ${esc(st.symbols.join(', '))} ${st.count === 1 ? 'is' : 'are'} trading below ${st.count === 1 ? 'its' : 'their'} profit stop. If ${st.count === 1 ? 'it closes' : 'they close'} there, the rule books ${pct(d.rules.stop_scale_out, 0)} of each (${money(st.value)} in all).</p>` : '';
      return `<section class="q-panel hp-panel pp-hero" aria-labelledby="pp-h">
        <div class="pp-hero-main">
          <h2 class="q-head-title" id="pp-h">Your gains, protected and exposed</h2>
          <p class="pp-lead">${lead}</p>
          ${split}
          <p class="hp-fine">A profit stop sits ${d.rules.trail_moves} average daily moves under the highest close of the last ${d.rules.trail_sessions} sessions (about two typical monthly swings), so it rises with the price and never falls.</p>
        </div>
        <div class="pp-hero-side"><h3 class="pp-h3">Book now</h3>${side}${watch}</div>
      </section>`;
    }

    // One bar per holding on a shared dollar scale: losses left, gains right, the gain split at the stop.
    function mapRow(h, scale) {
      const g = num(h.gain);
      const sig = h.signal || {};
      const lockedW = share(num(h.locked_gain) || 0, scale);
      const expW = share(g > 0 ? g - (num(h.locked_gain) || 0) : 0, scale);
      const lossW = share(g < 0 ? -g : 0, scale);
      const bar = `<span class="pp-bar" aria-hidden="true"><span class="pp-bar-l"><i class="is-loss" style="--w:${lossW.toFixed(2)}%"></i></span><span class="pp-bar-r"><i class="is-locked" style="--w:${lockedW.toFixed(2)}%"></i><i class="is-exposed" style="--w:${expW.toFixed(2)}%"></i></span></span>`;
      return `<details class="pp-row" data-hp-disclosure="pp-${esc(h.symbol)}"><summary class="pp-sum">
          <span class="pp-sym"><span class="hp-sym">${esc(h.symbol)}</span><span class="hp-sub">${num(h.weight) !== null ? `${pct(h.weight, 1)} of account` : ''}</span></span>
          ${chip(sig)}
          ${bar}
          <span class="pp-gain ${g > 0 ? 'pp-up' : g < 0 ? 'pp-down' : ''}">${g === null ? NA : signedMoney(g)}<span class="hp-sub">${num(h.gain_pct) !== null ? pct(h.gain_pct, 1, true, true) : ''}</span></span>
          <span class="pp-why">${esc(sig.text || '')}</span>
          <span class="q-caret" aria-hidden="true"></span>
        </summary>${detailHtml(h)}</details>`;
    }

    function detailHtml(h) {
      const facts = [];
      if (num(h.stop) !== null) facts.push(['Profit stop', `${price(h.stop)}<span class="hp-sub">price ${h.stop_distance_pct >= 0 ? `${pct(h.stop_distance_pct, 1)} above it` : `${pct(-h.stop_distance_pct, 1)} below it`}; highest close ${price(h.high)}, average daily move ${price(h.move)} (${pct(h.move_pct, 1)})</span>`]);
      if (num(h.locked_gain) !== null) facts.push(['Gain locked / exposed', `${money(h.locked_gain)} / ${money(h.exposed_gain)}`]);
      if (h.trend && h.trend.state) facts.push(['Trend', `${esc({ up: 'Up', down: 'Down', mixed: 'Mixed' }[h.trend.state])}<span class="hp-sub">50-session average ${price(h.trend.sma50)}, 200-session ${price(h.trend.sma200)}</span>`]);
      if (num(h.valuation_target) !== null) facts.push(['QUANTA 12-month valuation target', `${price(h.valuation_target)}<span class="hp-sub">model not validated</span>`]);
      if (h.earnings_date) facts.push(['Next earnings', `${esc(h.earnings_date)}<span class="hp-sub">decide before it, or accept the gap</span>`]);
      // The stop's conditional sale comes first when the price is under it; a booking rule
      // that fires as well keeps its own block, so neither hides the other.
      const saleBlock = (title, sale, also) => `<div class="pp-sale"><h4 class="pp-h4">${title}</h4>
          <p>${esc(sale.text)}.</p>
          <p class="pp-sale-n">Sell ${shares(sale.shares)} shares, about ${money(sale.value)}${num(sale.gain) !== null ? `, realising ${signedMoney(sale.gain)}${num(sale.gain_long) !== null ? ` (${money(sale.gain_long)} long-term, ${money(sale.gain_short)} short-term)` : ''}` : ''}${num(sale.weight_after) !== null ? `; ${pct(sale.weight_after, 1)} of the account after` : ''}.</p>
          ${also.length ? `<p class="hp-fine">Also fires: ${esc(also.map((t) => t.text).join('; '))}. The suggestion covers the largest.</p>` : ''}</div>`;
      const saleHtml = (h.stop_sale ? saleBlock('If it closes below the stop', h.stop_sale, []) : '')
        + (h.sale ? saleBlock('Booking rule', h.sale, (h.triggers || []).filter((t) => t.code !== h.sale.code)) : '');
      const hv = h.harvest;
      const harvestHtml = hv ? `<div class="pp-sale"><h4 class="pp-h4">Tax-loss candidate</h4>
          <p>Selling realises ${signedMoney(hv.loss)}${num(hv.loss_short) !== null ? ` (${signedMoney(hv.loss_short)} short-term, ${signedMoney(hv.loss_long)} long-term, net of any lot above cost)` : ''}.
          ${hv.wash_risk ? `<b>Wash-sale window open:</b> shares were bought on ${esc(hv.recent_buys.join(', '))}, within ${rules.wash_sale_days} days, so part of the loss may be disallowed.` : ''}
          To keep the loss, do not buy it back before ${esc(hv.no_rebuy_before)}.</p></div>` : '';
      return `<div class="pp-detail">
          ${chartHtml(h)}
          <div class="pp-detail-side">
            <dl class="pp-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
            ${saleHtml}${harvestHtml}
          </div>
        </div>`;
    }

    // ------------------------------------------------------------ the chart
    // Price, profit stop, cost and target over about six months. Shapes are SVG on a fixed
    // viewBox; text is HTML placed by percentage so it never stretches.
    const VW = 600;
    const VH = 220;
    function chartHtml(h) {
      const hs = h.history;
      if (!hs || !hs.closes || hs.closes.length < 2) return '<p class="pp-nochart">Not enough price history for a chart.</p>';
      const c = hs.closes;
      const s = hs.stops || [];
      const cost = num(h.average_cost);
      const tgt = num(h.valuation_target);
      const vals = c.concat(s.filter((v) => v !== null));
      if (cost !== null) vals.push(cost);
      if (tgt !== null) vals.push(tgt);
      let lo = Math.min(...vals);
      let hi = Math.max(...vals);
      const pad = (hi - lo) * 0.06 || hi * 0.05;
      lo -= pad; hi += pad;
      const n = c.length;
      const x = (i) => (i / (n - 1)) * VW;
      const y = (v) => VH - ((v - lo) / (hi - lo)) * VH;
      const line = (arr) => {
        let d = ''; let pen = false;
        arr.forEach((v, i) => { if (v === null || v === undefined) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
        return d;
      };
      // A filled band between two series wherever top is above bottom, one polygon per run.
      const band = (top, bot) => {
        const out = []; let run = [];
        const flush = () => {
          if (run.length > 1) out.push(`M${run.map((i) => `${x(i).toFixed(1)},${y(top[i]).toFixed(1)}`).join('L')}L${run.slice().reverse().map((i) => `${x(i).toFixed(1)},${y(bot[i]).toFixed(1)}`).join('L')}Z`);
          run = [];
        };
        for (let i = 0; i < n; i += 1) {
          if (top[i] !== null && bot[i] !== null && top[i] > bot[i]) run.push(i); else flush();
        }
        flush();
        return out.join('');
      };
      const stops = c.map((_, i) => (s[i] === undefined ? null : s[i]));
      const costs = c.map(() => cost);
      // Locked: from the cost up to the stop (or the price, if lower). Exposed: from the stop up to the price.
      const lockTop = c.map((v, i) => (stops[i] === null || cost === null ? null : Math.min(stops[i], v)));
      const locked = cost !== null ? band(lockTop, costs) : '';
      const exposed = band(c, c.map((v, i) => (stops[i] === null ? null : Math.max(stops[i], cost === null ? -Infinity : cost))));
      const lastStop = [...stops].reverse().find((v) => v !== null);
      const labels = [
        { k: 'Price', v: c[n - 1], cls: 'is-price' },
        lastStop !== undefined ? { k: 'Stop', v: lastStop, cls: 'is-stop' } : null,
        cost !== null ? { k: 'Cost', v: cost, cls: 'is-cost' } : null,
        tgt !== null ? { k: 'Target', v: tgt, cls: 'is-target' } : null,
      ].filter(Boolean).sort((a, b) => b.v - a.v);
      // Keep the end labels apart: at least 9% of the height between neighbours.
      let prev = -Infinity;
      labels.forEach((l) => { const top = Math.max((y(l.v) / VH) * 100, prev + 9); l.top = Math.min(top, 96); prev = l.top; });
      const day = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const aria = `${h.symbol} over ${n} sessions: price ${price(c[n - 1]).replace(/<[^>]+>/g, '')}`
        + `${lastStop !== undefined ? `, profit stop ${price(lastStop).replace(/<[^>]+>/g, '')}` : ''}`
        + `${cost !== null ? `, your average cost ${price(cost).replace(/<[^>]+>/g, '')}` : ''}`
        + `${tgt !== null ? `, QUANTA valuation target ${price(tgt).replace(/<[^>]+>/g, '')}` : ''}.`;
      return `<figure class="pp-chart" role="img" aria-label="${esc(aria)}">
          <div class="pp-plot">
            <svg viewBox="0 0 ${VW} ${VH}" preserveAspectRatio="none" aria-hidden="true">
              ${locked ? `<path class="pp-z-locked" d="${locked}"/>` : ''}
              ${exposed ? `<path class="pp-z-exposed" d="${exposed}"/>` : ''}
              ${cost !== null ? `<path class="pp-l-cost" d="M0,${y(cost).toFixed(1)}H${VW}"/>` : ''}
              ${tgt !== null ? `<path class="pp-l-target" d="M0,${y(tgt).toFixed(1)}H${VW}"/>` : ''}
              <path class="pp-l-stop" d="${line(stops)}"/>
              <path class="pp-l-price" d="${line(c)}"/>
            </svg>
            <div class="pp-ends" aria-hidden="true">${labels.map((l) => `<span class="pp-end ${l.cls}" style="top:${l.top.toFixed(1)}%"><b>${l.k}</b> ${price(l.v)}</span>`).join('')}</div>
          </div>
          <div class="pp-xaxis" aria-hidden="true"><span>${esc(day(hs.dates[0]))}</span><span>${esc(day(hs.dates[Math.floor(n / 2)]))}</span><span>${esc(day(hs.dates[n - 1]))}</span></div>
          <figcaption class="pp-cap"><span><i class="pp-key is-locked" aria-hidden="true"></i>Locked gain</span><span><i class="pp-key is-exposed" aria-hidden="true"></i>Exposed gain</span><span><i class="pp-key is-stopline" aria-hidden="true"></i>Profit stop</span></figcaption>
        </figure>`;
    }

    function mapHtml(d) {
      const rows = d.holdings || [];
      const scale = Math.max(1, ...rows.map((h) => Math.abs(num(h.gain) || 0)));
      const sig = d.signals || {};
      const counts = [['STOP_HIT', 'below a stop'], ['BOOK', 'to book'], ['PROTECT', 'near a stop'], ['HARVEST', 'tax-loss'], ['RUN', 'letting run']]
        .filter(([k]) => sig[k]).map(([k, w]) => `${sig[k]} ${w}`).join(' · ');
      return `<section class="q-panel hp-panel pp-map" aria-labelledby="pp-map-h">
        <header class="q-head"><div class="q-head-l"><h2 class="q-head-title" id="pp-map-h">Gain map</h2>
          <p class="q-head-sub">${esc(counts ? `${counts}.` : 'No rule fires on any holding.')} Open a holding for its chart, the rule that fired and the tax on acting.</p></div></header>
        <div class="q-body"><div class="pp-maphead" aria-hidden="true"><span>Holding</span><span>Signal</span><span class="pp-maphead-bar"><span>Loss</span><span>Gain: locked | exposed</span></span><span>Open gain</span><span>Why</span></div>
        ${rows.map((h) => mapRow(h, scale)).join('')}</div>
      </section>`;
    }

    function pathsHtml(d) {
      const paths = d.sector_paths || [];
      if (!paths.length) return '';
      return paths.map((p) => {
        const legs = (p[pathView] || {}).legs || [];
        const gain = (p[pathView] || {}).gain;
        const seg = [['cheapest', 'Tax-cheapest'], ['pro_rata', 'Pro rata']].map(([k, l]) => `<button type="button" class="pp-seg-btn${pathView === k ? ' is-on' : ''}" data-hp-x="pp-path:${k}" aria-pressed="${pathView === k}">${l}</button>`).join('');
        return `<section class="q-panel hp-panel pp-paths" aria-labelledby="pp-path-${esc(p.sector)}">
          <header class="q-head"><div class="q-head-l"><h2 class="q-head-title" id="pp-path-${esc(p.sector)}">${esc(p.sector)} back inside its ${pct(p.limit, 0)} limit</h2>
            <p class="q-head-sub">After the sales above, ${esc(p.sector)} is still ${pct(p.weight_after_sales, 1)} of the account: about ${money(p.excess_value)} over. Two ways to close the gap.</p></div>
            <div class="q-head-r pp-segs" role="group" aria-label="Path">${seg}</div></header>
          <div class="q-body"><div class="pp-legs-wrap"><table class="q-table pp-legs"><caption class="sr-only">${esc(p.sector)}: sales that bring it to the limit</caption>
            <thead><tr><th scope="col">Holding</th><th scope="col" class="num">Sell</th><th scope="col" class="num">Shares</th><th scope="col" class="num">Realised</th></tr></thead>
            <tbody>${legs.map((l) => `<tr><td><span class="hp-sym">${esc(l.symbol)}</span>${l.wash_risk ? ' <span class="pp-chip pp-chip--warn" title="Shares were bought within 30 days: part of a loss may be disallowed">Wash-sale window</span>' : ''}</td><td class="num">${money(l.value)}</td><td class="num">${shares(l.shares)}</td><td class="num ${num(l.gain) < 0 ? 'pp-down' : 'pp-up'}">${num(l.gain) === null ? NA : signedMoney(l.gain)}</td></tr>`).join('')}</tbody>
            <tfoot><tr><th scope="row">Total</th><td class="num">${money(legs.reduce((t, l) => t + l.value, 0))}</td><td></td><td class="num">${num(gain) === null ? NA : signedMoney(gain)}</td></tr></tfoot></table></div>
            <p class="hp-fine">${pathView === 'cheapest' ? 'Losses first, then the smallest gain per dollar sold: the order is about the tax bill, not the outlook for each name.' : 'Every holding in the sector shrinks by the same share: the mix inside the sector stays as it is.'}</p></div>
        </section>`;
      }).join('');
    }

    function rulesHtml(d, pf) {
      const r = d.rules;
      const pol = (pf && pf.policy) || {};
      return `<details class="q-panel hp-panel pp-rules hp-more" data-hp-disclosure="pp-rules"><summary>The rules, and what they are not</summary><div class="q-body">
        <ul class="pp-rulelist">
          <li><b>Profit stop.</b> The highest close of the last ${r.trail_sessions} sessions minus ${r.trail_moves} average daily moves (close to close). It only rises. Below it with a gain, the rule books ${pct(r.stop_scale_out, 0)} on the close and resets the stop on the rest.</li>
          <li><b>Near the stop.</b> Within ${r.protect_moves} average daily moves of it.</li>
          <li><b>Take your cost back.</b> Up ${pct(r.recover_cost_gain, 0)} or more: sell the original cost, so what stays rides on gains only.</li>
          <li><b>Valuation.</b> At or past QUANTA’s 12-month valuation target: book ${pct(r.valuation_scale_out, 0)}. The target comes from a model that is not validated.</li>
          <li><b>Size.</b> Past the ${pct(pol.trim_review_position, 0)} review level: sell down to the ${pct(pol.trim_target_position, 0)} trim target.</li>
          <li><b>Tax-loss candidate.</b> A loss of at least ${money(r.harvest_min_loss)} and ${pct(r.harvest_min_loss_pct, 0)}. Shares bought within ${r.wash_sale_days} days open a wash-sale window.</li>
          <li><b>Funds</b> are sized by allocation: only the size rule applies to them.</li>
        </ul>
        <p class="hp-fine">These are common rules of thumb, stated with their settings. QUANTA has not validated them as an edge, and none is an order. Gains use your broker’s tax lots, oldest first (Robinhood’s default); this is arithmetic, not tax advice. Sales smaller than ${money(r.min_sale_value)} are not suggested.</p>
      </div></details>`;
    }

    function body(pf) {
      const P = ins.profit;
      if (!P || P.s === 'idle' || (P.s === 'loading' && !P.data)) return '<section class="q-panel hp-panel pp-loading" aria-busy="true"><div class="q-body"><p class="hp-empty">Building the profit plan from your lots and price history…</p></div></section>';
      if (P.s === 'missing') return '<section class="q-panel hp-panel"><div class="q-body"><p class="hp-empty">This server predates the profit plan. Restart it to load the new route.</p></div></section>';
      if (P.s === 'error' || !P.data) return `<section class="q-panel hp-panel"><div class="q-body"><p class="hp-empty">The profit plan could not be built. <button type="button" class="q-btn q-btn--link hp-inline-link" data-hp-x="pp-retry">Try again</button></p></div></section>`;
      const d = P.data;
      rules = d.rules || {};
      if (!d.available) return `<section class="q-panel hp-panel"><div class="q-body"><p class="hp-empty">${esc(d.reason || 'No profit plan for this account.')}</p></div></section>`;
      return `${d.note ? `<p class="hp-dq-lead pp-note">${esc(d.note)}</p>` : ''}${heroHtml(d)}${mapHtml(d)}${pathsHtml(d)}${rulesHtml(d, pf)}`;
    }

    function profit(pf) {
      return `<div class="pp" data-mp-slot="profit-body">${body(pf)}</div>`;
    }
    X.slotRenderers['profit-body'] = body;

    Object.assign(X.actions, {
      'pp-path'(k) {
        if (k !== 'cheapest' && k !== 'pro_rata') return;
        pathView = k; X.paintSlot('profit-body');
        H.announce(`Showing the ${k === 'cheapest' ? 'tax-cheapest' : 'pro rata'} path.`);
      },
      'pp-retry'() { load(true); },
    });

    const prevTab = X.onTab;
    X.onTab = (key) => { if (prevTab) prevTab(key); if (key === 'profit') load(); };
    const prevCounts = X.counts;
    X.counts = (pf) => {
      const out = prevCounts ? prevCounts(pf) : {};
      const d = ins.profit && ins.profit.s === 'ok' ? ins.profit.data : null;
      if (d && d.signals) out.profit = ACT.reduce((t, k) => t + (d.signals[k] || 0), 0);
      return out;
    };

    Object.assign(X, { profit, profitBody: body, profitChart: chartHtml, loadProfit: load });
    return X;
  }

  const api = { attach };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.QuantaProfit = api;
    if (window.QuantaHoldings && window.QuantaHoldings.ext && window.QuantaHoldings.ext.ins) attach(window.QuantaHoldings);
  }
}(typeof window !== 'undefined' ? window : globalThis));
