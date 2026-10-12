// Research tear sheet capture: renders saved /api/analyze payloads into the
// running app and records full-page screenshots, page height, horizontal
// overflow and axe violations at each viewport and theme.
// Usage: node tests/tools/research_shots.cjs <baseUrl> <outDir> <payload.json>...
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

const [base, outDir, ...files] = process.argv.slice(2);
const sizes = (process.env.SHOT_WIDTHS || '1440,390').split(',').map(Number);
const themes = (process.env.SHOT_THEMES || 'dark,light').split(',');
const overflowWidths = [320, 390, 768, 1024, 1440, 1920];

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  const report = [];
  for (const file of files) {
    const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
    const name = path.basename(file, '.json');
    for (const theme of themes) {
      for (const width of sizes) {
        const ctx = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
        await ctx.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) {} }, theme);
        const page = await ctx.newPage();
        await page.route('**/api/analyze', (r) => r.abort());
        await page.goto(`${base}/#analyze`, { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => typeof window.renderAnalysis === 'function');
        await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
        await page.evaluate((p) => window.renderAnalysis(p), payload);
        await page.waitForTimeout(900);
        const height = await page.evaluate(() => document.documentElement.scrollHeight);
        const shot = path.join(outDir, `${name}__${width}-${theme}.png`);
        await page.screenshot({ path: shot, fullPage: true });
        await page.screenshot({ path: shot.replace('.png', '-fold.png') });
        let axe = null;
        if (width === 1440) {
          // Every tab panel is checked, not only the one open on load.
          axe = [];
          const tabs = await page.$$('#an-results .ts-tab');
          for (let i = 0; i < Math.max(1, tabs.length); i += 1) {
            if (tabs[i]) {
              await tabs[i].click();
              await page.waitForTimeout(150);
              if (process.env.SHOT_PANELS) {
                const key = await tabs[i].getAttribute('aria-controls');
                await page.screenshot({ path: shot.replace('.png', `-${key}.png`), fullPage: true });
              }
            }
            const res = await new AxeBuilder({ page }).include('#view-analyze').analyze();
            axe.push(...res.violations.map((v) => `${v.id}(${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`));
          }
          axe = [...new Set(axe)];
        }
        report.push({ name, theme, width, height, axe });
        await ctx.close();
      }
    }
    const ctx = await browser.newContext({ viewport: { width: 320, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(`${base}/#analyze`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.renderAnalysis === 'function');
    await page.evaluate((p) => window.renderAnalysis(p), payload);
    // Open every panel so the sweep covers the whole sheet.
    await page.waitForTimeout(600);
    await page.evaluate(() => document.querySelectorAll('#an-results .ts-panel').forEach((p) => p.removeAttribute('hidden')));
    const overflow = [];
    for (const w of overflowWidths) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.waitForTimeout(250);
      const o = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        // Content inside a horizontal scroller (tab strip, wide table) is meant to scroll.
        const inScroller = (el) => {
          for (let p = el.parentElement; p && p.id !== 'view-analyze'; p = p.parentElement) {
            if (/(auto|scroll|hidden)/.test(getComputedStyle(p).overflowX)) return true;
          }
          return false;
        };
        const wide = [...document.querySelectorAll('#view-analyze *')].filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width && r.right > vw + 1 && !el.closest('[hidden]') && !inScroller(el);
        }).slice(0, 3).map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join('.')}`);
        return { sw: document.documentElement.scrollWidth, vw, wide };
      });
      if (o.sw > o.vw || o.wide.length) overflow.push({ w, ...o });
    }
    report.push({ name, overflow });
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
