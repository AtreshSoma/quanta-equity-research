// Visual + a11y audit driver: screenshots at breakpoints x themes, axe WCAG 2.1 AA, console errors, overflow.
const { chromium } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5601';
const OUT = process.env.OUT_DIR || path.join(__dirname, 'shots');
const VIEWS = (process.env.VIEWS || 'dashboard,discover,analyze,picks,track').split(',');
const WIDTHS = (process.env.WIDTHS || '1280,1440,1920').split(',').map(Number);
const THEMES = (process.env.THEMES || 'dark,light').split(',');
const DO_AXE = process.env.AXE !== '0';
const ANALYZE = process.env.ANALYZE || '';   // ticker to run a real deep-dive
const SCAN = process.env.SCAN === '1';
fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const report = [];
  for (const theme of THEMES) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: theme });
    await ctx.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (e) {} }, theme);
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
    await page.goto(BASE + '/?nocache=' + Date.now(), { waitUntil: 'networkidle', timeout: 120000 });
    await page.waitForSelector('#decision-brief .q-regime-title', { timeout: 120000 }).catch(() => {});
    if (ANALYZE) {
      await page.locator('.nav-tab[data-view="analyze"]').dispatchEvent('click');
      await page.locator('#an-fast-mode').setChecked(process.env.FAST !== '0');
      await page.locator('#an-ticker').fill(ANALYZE);
      await page.locator('#btn-analyze').click();
      await page.waitForFunction(() => document.querySelector('#an-results .analysis-stack, #an-results .error-box') !== null, null, { timeout: 240000 }).catch(() => {});
    }
    if (SCAN) {
      await page.locator('.nav-tab[data-view="picks"]').dispatchEvent('click');
      await page.locator('#sp-scan').selectOption('quick');
      await page.locator('#btn-scan').click();
      await page.waitForFunction(() => document.querySelector('#picks-results .picks-stream, #picks-results .error-box') !== null, null, { timeout: 600000 }).catch(() => {});
    }
    for (const view of VIEWS) {
      // Route by hash: Model Evidence is a secondary view with no tab.
      await page.evaluate((v) => { window.location.hash = v; }, view);
      await page.waitForTimeout(600);
      for (const w of WIDTHS) {
        await page.setViewportSize({ width: w, height: 900 });
        await page.waitForTimeout(400);
        const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
          culprits: Array.from(document.querySelectorAll('body *')).filter((e) => { const r = e.getBoundingClientRect(); return r.right > document.documentElement.clientWidth + 1 && r.width > 0; }).slice(0, 8).map((e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.') : '')) }));
        const file = path.join(OUT, `${view}-${theme}-${w}.png`);
        await page.screenshot({ path: file, fullPage: true });
        let axe = null;
        if (DO_AXE && (w === 1600 || w === 360)) {
          const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
          axe = res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) , count: v.nodes.length}));
        }
        report.push({ view, theme, width: w, overflow: overflow.sw > overflow.cw + 1, overflowPx: overflow.sw - overflow.cw, culprits: overflow.culprits, axe, file });
      }
    }
    report.push({ theme, consoleErrors: errors });
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
  for (const r of report) {
    if (r.consoleErrors) { console.log(`[${r.theme}] console errors: ${r.consoleErrors.length}`); r.consoleErrors.slice(0, 10).forEach((e) => console.log('   ', e.slice(0, 200))); continue; }
    console.log(`${r.view.padEnd(9)} ${r.theme.padEnd(5)} ${String(r.width).padStart(4)}  overflow=${r.overflow ? 'YES(' + r.overflowPx + 'px) ' + r.culprits.join(', ') : 'no'}  axe=${r.axe ? r.axe.length + ' violations ' + r.axe.map((v) => v.id + '(' + v.count + ')').join(',') : '-'}`);
  }
})().catch((e) => { console.error(e); process.exit(1); });
