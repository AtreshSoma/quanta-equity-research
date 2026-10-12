import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Picks measured design gates (docs/PICKS_DESIGN_BRIEF.md, item 12), all five tabs.
 * A synthetic job (tests/fixtures/picks_synth_job.json, built through the real
 * _picks_view) is routed into the page; no market data is fetched.
 * At 1280, 1440 and 390 px in both themes:
 *   each panel or strip cell is at least 85% filled; side-by-side panels are
 *   within 15% of each other's height; section gaps are at most 16 px; no
 *   horizontal overflow or stray scroll box (a wide data table may scroll on a
 *   phone, inside a focusable labelled region); desktop gauges never wrap or clip;
 *   axe finds no WCAG 2.1 A/AA violation.
 * Run against a scratch server: QUANTA_PORT=5800 python app.py, then
 *   BASE_URL=http://127.0.0.1:5800 npx playwright test picks_gates
 */

const job = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'picks_synth_job.json'), 'utf8'));

async function openReport(page: Page) {
  await page.addInitScript(() => { localStorage.setItem('quanta_active_view', 'picks'); });
  await page.route('**/*', (route) => {
    const req = route.request();
    const u = new URL(req.url());
    if (!['127.0.0.1', 'localhost'].includes(u.hostname)) return route.abort('blockedbyclient');
    const p = u.pathname;
    if (p === '/api/stock-picks' && req.method() === 'POST') return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ job_id: job.job_id, status: 'running' }) });
    if (p === `/api/jobs/${job.job_id}/stream`) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `data: ${JSON.stringify(job)}\n\n` });
    if (p === `/api/jobs/${job.job_id}`) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(job) });
    if (p.startsWith('/api/') && !['/api/health', '/api/thresholds'].includes(p)) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"offline in this check"}' });
    return route.continue();
  });
  await page.goto(`/?nocache=${Date.now()}#picks`, { waitUntil: 'domcontentloaded' });
  await page.locator('.nav-tab[data-view="picks"]').click();
  await page.locator('#sp-capital').fill('2500');
  await page.locator('#sp-scan').selectOption('quick');
  await page.locator('#btn-scan').click();
  await page.waitForFunction(() => document.querySelector('#picks-results [data-picks-v2]') && (document.querySelector('#btn-scan') as HTMLButtonElement)?.disabled === false);
}

function measure() {
  const view = document.querySelector('#picks-results [data-picks-v2]') as HTMLElement;
  const vis = (el: Element) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const panels = [...view.querySelectorAll('.pkp, .pk-sc, .pk-keys > div, .bucket-card, .deep-confirm-summary, .qpro-card, .watchlist-group, .pk-method')].filter((p) => vis(p) && !p.closest('[hidden]')) as HTMLElement[];
  const fill = panels.map((p) => {
    const r = p.getBoundingClientRect();
    let bottom = r.top;
    const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (n.nodeType === 3 && (n.textContent || '').trim()) {
        const range = document.createRange(); range.selectNodeContents(n);
        for (const rr of range.getClientRects()) bottom = Math.max(bottom, rr.bottom);
      } else if (n.nodeType === 1 && ['svg', 'IMG', 'CANVAS', 'INPUT', 'BUTTON'].includes((n as Element).tagName) && vis(n as Element)) {
        bottom = Math.max(bottom, (n as Element).getBoundingClientRect().bottom);
      }
    }
    const pad = parseFloat(getComputedStyle(p.querySelector('.pkp-b') || p).paddingBottom) || 0;
    return { title: ((p.querySelector('.pkp-h h5, dt, header strong, .bucket-title, .qpro-kicker, h4') || {}) as HTMLElement).textContent || p.className, fill: Math.min(1, (bottom - r.top + pad) / r.height) };
  });
  const rows = new Map<string, { h: number; t: string }[]>();
  panels.forEach((p, i) => {
    const key = `${[...view.querySelectorAll('*')].indexOf(p.parentElement as Element)}:${Math.round(p.getBoundingClientRect().top / 3)}`;
    if (!rows.has(key)) rows.set(key, []);
    rows.get(key)!.push({ h: p.getBoundingClientRect().height, t: fill[i].title });
  });
  const spreads = [...rows.values()].filter((g) => g.length > 1).map((g) => ({ panels: g.map((x) => x.t).join('|'), spread: Math.max(...g.map((x) => x.h)) / Math.min(...g.map((x) => x.h)) - 1 }));
  const active = view.querySelector('[role="tabpanel"]:not([hidden])') || view;
  const blocks = [...view.children, ...active.children].filter(vis).map((el) => el.getBoundingClientRect()).sort((a, b) => a.top - b.top);
  let maxGap = 0;
  for (let i = 1; i < blocks.length; i++) { const g = blocks[i].top - blocks[i - 1].bottom; if (blocks[i].left < blocks[i - 1].right) maxGap = Math.max(maxGap, g); }
  const scrollers = [...view.querySelectorAll('*')].filter((el) => vis(el) && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)
    && /auto|scroll/.test(getComputedStyle(el).overflowY + getComputedStyle(el).overflowX)).map((el) => (el as HTMLElement).className);
  const wrapped = innerWidth >= 1000 ? [...view.querySelectorAll('.pk-grow dt, .pk-grow .pk-gread, .pk-grow .pk-gval, .pk-grow .pk-ins-line')]
    .filter((el) => vis(el) && el.getBoundingClientRect().height > parseFloat(getComputedStyle(el).fontSize) * 2.1).map((el) => el.textContent) : [];
  const clipped = [...view.querySelectorAll('.pk-gread, .pk-gval, .pk-ins-line')].filter((el) => vis(el) && el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent);
  return { fill, spreads, maxGap, scrollers, wrapped, clipped, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}

// Tab label -> the fewest panels it must measure (guards against measuring nothing).
const TABS: Record<string, number> = { 'Overview': 4, 'Analysis': 9, 'Momentum alerts': 2, 'Risk and execution': 3, 'Method': 4 };
for (const tab of Object.keys(TABS)) {
  for (const theme of ['light', 'dark']) {
    for (const width of [1280, 1440, 390]) {
      test(`@picks ${tab} meets the design gates at ${width} px, ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
        await openReport(page);
        await page.locator('#picks-results [role="tab"]', { hasText: tab }).first().click();
        await page.evaluate((th) => (window as any).applyTheme(th), theme);
        await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
        const m = await page.evaluate(measure);
        expect(m.fill.length, 'panels measured').toBeGreaterThanOrEqual(TABS[tab]);
        expect(m.fill.filter((f) => f.fill < 0.85), 'each panel at least 85% filled').toEqual([]);
        expect(m.spreads.filter((s) => s.spread > 0.15), 'side-by-side within 15% height').toEqual([]);
        expect(m.maxGap, 'section gap').toBeLessThanOrEqual(16);
        expect(m.overflow, 'no horizontal page scroll').toBe(0);
        expect(m.scrollers.filter((c) => !(width < 700 && /table-scroll/.test(c))), 'no stray scroll boxes').toEqual([]);
        expect(m.wrapped, 'gauges one line each').toEqual([]);
        expect(m.clipped, 'no clipped readings').toEqual([]);
        const axe = await new AxeBuilder({ page }).include('#view-picks').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
        expect(axe.violations.map((v) => v.id)).toEqual([]);
      });
    }
  }
}
