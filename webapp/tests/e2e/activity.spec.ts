import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Activity tab interactions, on synthetic data only. Run against a scratch-port server
 * (BASE_URL), never the signed-in one: every /api/portfolio call is answered from the
 * fixtures (tests/fixtures/activity/<scenario>.json, built by the real server code), and
 * the page clock is pinned to the fixture's "now" so ranges are stable.
 *
 *   BASE_URL=http://127.0.0.1:<port> npx playwright test tests/e2e/activity.spec.ts --project=desktop-dark
 * Screenshots: SHOTS_DIR=<dir> also writes the visual-check images (synthetic data).
 */

const fx = (name: string) => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'activity', `${name}.json`), 'utf8'));
const view = () => JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'my_portfolio_view.json'), 'utf8'));

async function open(page: Page, scenario: string, hash = '', theme: 'dark' | 'light' = 'dark', mixed = false, crypto?: object) {
  const act = fx(scenario);
  if (crypto) act.records.crypto = crypto;
  if (mixed) {
    for (const key of ['closed_trades', 'fills', 'lots']) {
      act.records[key].forEach((r: any) => { r.instrument_type = r.symbol === 'SAB' ? 'crypto' : r.symbol === 'SAC' ? 'etf' : 'equity'; });
    }
  }
  const v = view();
  const status = { ...v.status, account: 'a'.repeat(16) };
  await page.clock.install({ time: new Date(act.now) });
  await page.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) { /* ignore */ } }, theme);
  await page.route('**/api/dashboard**', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await page.route('**/api/portfolio{,/**}', (route) => {
    const url = new URL(route.request().url());
    const last = url.pathname.split('/').pop();
    const json = (b: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (last === 'records') return json(act.records);
    if (last === 'activity') return json(act.activity[url.searchParams.get('period') || 'month']);
    if (last === 'status') return json(status);
    if (last === 'accounts') return json({ accounts: [{ handle: 'a'.repeat(16), label: 'Individual', can_trade_via_agent: false }] });
    if (['performance', 'intraday', 'exposure', 'events', 'outcomes', 'journal'].includes(last || '')) return json({ available: false, reason: 'Not part of this test.' });
    return json({ ...v, status });
  });
  await page.goto(`/?nocache=${Date.now()}#holdings/activity${hash}`);
  await expect(page.locator('#hp-root')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
  await expect(page.locator('#mpa-root .mpa-kpis')).toBeVisible();
  // The window-total response rebuilds the body after records first appear.
  await expect.poll(() => page.evaluate(() => {
    const ins = (window as any).QuantaHoldings.ext.ins;
    return ins.records.s === 'ok' && Object.values(ins.act).every((slot: any) => slot.s !== 'loading');
  })).toBe(true);
  return act;
}
const bar = (page: Page, i: number) => page.locator('#mpa-root .mpa-b').nth(i);
async function busyBars(page: Page) {
  return page.locator('#mpa-root .mpa-b').evaluateAll((els) => els.map((e, i) => ({ i, label: e.getAttribute('aria-label') || '' })).filter((x) => / closing trades?$/.test(x.label) || /closing trade,/.test(x.label)).map((x) => x.i));
}

test.describe('Activity', () => {
  test('records reload preserves every filter through loading, failure and retry', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const act = await open(page, 'one_trade', '/r=all');
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let requests = 0;
    await page.route('**/api/portfolio/records', async (route) => {
      requests += 1;
      if (requests === 1) {
        await gate;
        await route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
      } else {
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify(act.records) });
      }
    });
    const request = page.waitForRequest('**/api/portfolio/records');
    await page.evaluate(() => { void (window as any).QuantaHoldings.ext.load('records', true); });
    await request;
    const tools = page.locator('#mpa-root .mpa-ctl');
    for (const group of ['Range', 'Asset type', 'View']) {
      await expect(tools.getByRole('group', { name: group, exact: true })).toBeVisible();
    }
    await expect(tools.locator('[data-hp-x^="act-range:"]')).toHaveCount(act.records.config.ranges.length);
    await expect(tools.locator('[data-hp-x^="act-asset:"]')).toHaveCount(3);
    await expect(tools.locator('[data-hp-x^="act-view:"]')).toHaveCount(3);
    await expect(tools.locator('.mpa-ms-b')).toBeVisible();
    await expect(tools).toContainText('Refreshing activity');
    await expect(page.locator('.mpa-k')).toHaveCount(5);
    release();
    await expect(tools).toContainText('Activity refresh failed');
    await tools.getByRole('button', { name: 'Try again' }).click();
    await expect(tools.getByRole('button', { name: 'Try again' })).toHaveCount(0);
    await expect(tools.locator('[data-hp-x="act-range:all"]')).toHaveAttribute('aria-pressed', 'true');
    await tools.locator('[data-hp-x="act-view:buys"]').click();
    await expect(tools.locator('[data-hp-x="act-view:buys"]')).toHaveAttribute('aria-pressed', 'true');
    expect(errors).toEqual([]);
  });

  test('untyped legacy records cannot enter the Equities filter', async ({ page }) => {
    await open(page, 'one_trade', '/r=all');
    await expect(page.locator('.mpa-foot')).toContainText('no verified asset type');
    await page.locator('[data-hp-x="act-asset:equity"]').click();
    await page.locator('.mpa-ms-b').click();
    await expect(page.locator('.mpa-ms-o')).toHaveCount(0);
    await expect(page.locator('.mpa-t tbody tr[data-sym]')).toHaveCount(0);
    await page.locator('[data-hp-x="act-asset:all"]').click();
    await expect(page.locator('.mpa-t tbody tr[data-sym]')).toHaveCount(1);
  });

  test('separate crypto fills and searchable symbols compose with range and view', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await page.route('https://fonts.googleapis.com/**', (r) => r.fulfill({ contentType: 'text/css', body: '' }));
    await open(page, 'one_trade', '/r=all', 'dark', false, { available: true, rejected: 0,
      coverage: { complete: true, first: '2026-09-01T15:00:00Z', last: '2026-09-20T15:00:00Z' },
      fills: [
        { symbol: 'COIN', instrument_type: 'crypto', side: 'buy', quantity: 2, price: 10, at: '2026-09-01T15:00:00Z' },
        { symbol: 'COIN', instrument_type: 'crypto', side: 'sell', quantity: 1, price: 15, at: '2026-09-20T15:00:00Z' },
        { symbol: 'TOKEN', instrument_type: 'crypto', side: 'buy', quantity: 1, price: 50, at: '2026-01-01T15:00:00Z' },
      ] });
    await page.locator('[data-hp-x="act-asset:crypto"]').click();
    await expect(page.locator('.mpa-k').first()).toContainText('Realized P&L (est.)');
    await expect(page.locator('.mpa-k-v').first()).toHaveText('+$5.00');
    await page.locator('.mpa-ms-b').click();
    await page.getByRole('searchbox', { name: 'Search assets' }).fill('coin');
    await expect(page.locator('.mpa-ms-o:not([hidden])')).toHaveCount(1);
    await page.locator('.mpa-ms-o:not([hidden]) input').check();
    await page.locator('.mpa-ms-b').click();
    await expect(page).toHaveURL(/s=COIN&a=crypto/);
    await page.locator('[data-hp-x="act-range:month"]').click();
    await expect(page.locator('.mpa-k-v').first()).toHaveText('+$5.00');
    await page.locator('[data-hp-x="act-view:buys"]').click();
    await expect(page.locator('.mpa-k-v').first()).toHaveText('$20.00');
    await expect(page.locator('.mpa-t tbody tr[data-sym]')).toHaveCount(1);
    await page.reload();
    await expect(page.locator('[data-hp-x="act-asset:crypto"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.mpa-chip')).toContainText('COIN');
    await page.locator('[data-hp-x="act-view:lots"]').click();
    await expect(page.locator('.mp-limit')).toContainText('does not provide crypto tax lots');
    await page.locator('[data-hp-x="act-asset:equity"]').click();
    await expect(page.locator('.mpa-chip')).toHaveCount(0);
    await page.locator('.mpa-ms-b').click();
    await expect(page.locator('.mpa-ms-o')).not.toContainText(['COIN']);
    expect(errors).toEqual([]);
  });

  test('hover a bar: crosshair, tooltip with the period, and its table rows light up', async ({ page }) => {
    await open(page, 'many', '/r=year');
    const [i] = (await busyBars(page)).slice(-1);
    await bar(page, i).hover();
    const tip = page.locator('#mpa-root .mpa-tip');
    await expect(tip).toBeVisible();
    const label = await bar(page, i).getAttribute('aria-label');
    await expect(tip.locator('.mpa-tip-t')).toHaveText(String(label).split(':')[0]);
    await expect(tip).toContainText(/closing trades? · \d+ up, \d+ down/);
    const key = await bar(page, i).getAttribute('data-k');
    const rows = page.locator(`#mpa-root .mpa-t tbody tr[data-k="${key}"]`);
    if (await rows.count()) await expect(rows.first()).toHaveClass(/is-hot/);
    await page.mouse.move(5, 5);
    await expect(tip).toBeHidden();
  });

  test('click a bar to filter everything to that period; click again to clear', async ({ page }) => {
    await open(page, 'many', '/r=year');
    const [i] = (await busyBars(page)).slice(-1);
    const key = await bar(page, i).getAttribute('data-k');
    await bar(page, i).click();
    await expect(page.locator('#mpa-root .mpa-chip')).toHaveCount(1);
    await expect(bar(page, i)).toHaveAttribute('aria-pressed', 'true');
    const keys = await page.locator('#mpa-root .mpa-t tbody tr').evaluateAll((rs) => rs.map((r) => (r as HTMLElement).dataset.k));
    expect(new Set(keys)).toEqual(new Set([key]));
    // KPI total = ranking total = table total, on the filtered rows.
    const kpi = await page.locator('#mpa-root .mpa-k').first().locator('.mpa-k-v').innerText();
    await expect(page.locator('#mpa-root .mpa-rt b').first()).toHaveText(kpi);
    await expect(page.locator('#mpa-root .mpa-sum-t dd').first()).toHaveText(kpi);
    await bar(page, i).click();
    await expect(page.locator('#mpa-root .mpa-chip')).toHaveCount(0);
  });

  test('brush across bars sets a custom range, shown as a chip and kept in the URL; Clear all returns', async ({ page }) => {
    await open(page, 'many', '/r=3month');
    await page.locator('#mpa-root .mpa-plot').scrollIntoViewIfNeeded();
    const a = await bar(page, 2).boundingBox();
    const b = await bar(page, 6).boundingBox();
    if (!a || !b) throw new Error('bars not laid out');
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 6 });
    await page.mouse.up();
    const chip = page.locator('#mpa-root .mpa-chip');
    await expect(chip).toHaveCount(1);
    await expect(chip).toContainText(/^[A-Z][a-z]{2} \d{1,2}, \d{4} to [A-Z][a-z]{2} \d{1,2}, \d{4}/);
    await expect(page).toHaveURL(/r=\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}/);
    await expect(page.locator('#mpa-root [data-hp-x^="act-range:"][aria-pressed="true"]')).toHaveCount(0);
    await page.getByRole('button', { name: 'Clear all' }).click();
    await expect(page.getByRole('button', { name: 'the past 3 months' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('keyboard: arrows move between bars, Shift+arrow brushes, Enter applies', async ({ page }) => {
    await open(page, 'many', '/r=3month');
    const last = page.locator('#mpa-root .mpa-b[tabindex="0"]');
    await last.focus();
    await expect(page.locator('#mpa-root .mpa-tip')).toBeVisible();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('Shift+ArrowLeft');
    await page.keyboard.press('Shift+ArrowLeft');
    await expect(page.locator('#mpa-root .mpa-b.is-brush')).toHaveCount(3);
    await page.keyboard.press('Enter');
    await expect(page.locator('#mpa-root .mpa-chip')).toHaveCount(1);
  });

  test('asset segments compose with range and view and update every section without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    // The isolated test environment cannot fetch Google's font stylesheet.
    await page.route('https://fonts.googleapis.com/**', (r) => r.fulfill({ contentType: 'text/css', body: '' }));
    await open(page, 'many', '/r=all', 'dark', true);
    const group = page.getByRole('group', { name: 'Asset type', exact: true });
    await expect(group.getByRole('button')).toHaveText(['All', 'Equities', 'Crypto']);
    await expect(group.getByRole('button', { name: 'All', exact: true })).toHaveAttribute('aria-pressed', 'true');
    for (const range of ['all', 'month']) {
      await page.locator('[data-hp-x="act-range:' + range + '"]').click();
      for (const view of ['sales', 'buys', 'lots']) {
        await page.locator('[data-hp-x="act-view:' + view + '"]').click();
        for (const asset of ['equity', 'crypto', 'all']) {
          await page.locator('[data-hp-x="act-asset:' + asset + '"]').click();
          const syms = await page.locator('#mpa-root .mpa-t tbody tr[data-sym]').evaluateAll((rs) => rs.map((r) => (r as HTMLElement).dataset.sym));
          if (asset === 'crypto') expect(syms.every((s) => s === 'SAB')).toBe(true);
          if (asset === 'equity') expect(syms.every((s) => s !== 'SAB')).toBe(true);
          if (range === 'all' && asset === 'all') { expect(syms).toContain('SAB'); expect(syms.some((s) => s !== 'SAB')).toBe(true); }
          const total = await page.locator('#mpa-root .mpa-k').first().locator('.mpa-k-v').innerText();
          const tableTotal = page.locator('#mpa-root .mpa-sum-t dd').first();
          if (syms.length) await expect(tableTotal).toHaveText(total);
          else await expect(page.locator('#mpa-root .mpa-ch-empty')).toBeVisible();
          const rankingTotal = page.locator('#mpa-root .mpa-rt b').first();
          if (syms.length) await expect(rankingTotal).toHaveText(total);
        }
      }
    }
    await group.getByRole('button', { name: 'Crypto', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(group.getByRole('button', { name: 'Crypto', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.reload();
    await expect(group.getByRole('button', { name: 'Crypto', exact: true })).toHaveAttribute('aria-pressed', 'true');
    expect(errors).toEqual([]);
  });

  test('Crypto without crypto records uses the existing empty state in every view', async ({ page }) => {
    await open(page, 'many', '/r=all');
    await page.locator('[data-hp-x="act-asset:crypto"]').click();
    for (const view of ['sales', 'buys', 'lots']) {
      await page.locator('[data-hp-x="act-view:' + view + '"]').click();
      await expect(page.locator('#mpa-root .mpa-ch-empty')).toContainText('No ');
      await expect(page.locator('#mpa-root .mpa-t tbody tr[data-sym]')).toHaveCount(0);
    }
  });

  test('range, view and stocks survive a reload', async ({ page }) => {
    await open(page, 'many', '/r=all');
    await page.getByRole('button', { name: 'the past year' }).click();
    await page.locator('#mpa-root [data-hp-x="act-view:buys"]').click();
    await page.locator('#mpa-root .mpa-r[data-sym]').first().click();
    const url = page.url();
    expect(url).toMatch(/r=year&v=buys&s=S[A-Z]{2}$/);
    await page.reload();
    await expect(page.locator('#mpa-root .mpa-kpis')).toBeVisible();
    await expect(page.getByRole('button', { name: 'the past year' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#mpa-root [data-hp-x="act-view:buys"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#mpa-root .mpa-chip')).toHaveCount(1);
    expect(page.url()).toBe(url);
  });

  test('empty states keep the layout and say what is missing', async ({ page }) => {
    await open(page, 'empty', '/r=all');
    await expect(page.locator('#mpa-root .mpa-ch-empty')).toHaveText('No closing trades in this range');
    await expect(page.locator('#mpa-root .mpa-k')).toHaveCount(5);
    const h = await page.locator('#mpa-root .mpa-chart').boundingBox();
    expect(h && h.height).toBeGreaterThan(200);
  });

  test('accessibility (WCAG 2.1 AA) in each view', async ({ page }) => {
    await open(page, 'no_figure_bucket', '/r=3month');
    for (const v of ['sales', 'buys', 'lots']) {
      await page.locator(`#mpa-root [data-hp-x="act-view:${v}"]`).click();
      const results = await new AxeBuilder({ page }).include('#mpa-root').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
      expect(results.violations.map((x) => `${v} ${x.id}: ${x.nodes.map((n) => n.target).join(' ')}`)).toEqual([]);
    }
  });

  for (const theme of ['light', 'dark'] as const) {
    for (const width of [1280, 1440]) {
      test(`visual check ${theme} ${width}: no overflow, chart and ranking equal height, tiles fit`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await open(page, 'many', '/r=all', theme);
        const m = await page.evaluate(() => {
          const r = (s: string) => document.querySelector(s)!.getBoundingClientRect();
          const clipped = [...document.querySelectorAll('#mpa-root .mpa-k-v')].filter((e) => e.scrollWidth > e.clientWidth + 1).length;
          return { overflow: document.documentElement.scrollWidth - window.innerWidth, chart: r('#mpa-root .mpa-chart').height, rank: r('#mpa-root .mpa-rank').height, clipped };
        });
        expect(m.overflow).toBeLessThanOrEqual(0);
        expect(Math.abs(m.chart - m.rank)).toBeLessThanOrEqual(1);
        expect(m.clipped).toBe(0);
        if (process.env.SHOTS_DIR) await page.screenshot({ path: path.join(process.env.SHOTS_DIR, `after-activity-${width}-${theme}.png`), fullPage: true });
      });
    }
  }
  test('single column below 1000px', async ({ page }) => {
    await page.setViewportSize({ width: 960, height: 900 });
    await open(page, 'many', '/r=all');
    const [a, b] = await Promise.all(['#mpa-root .mpa-chart', '#mpa-root .mpa-rank'].map((s) => page.locator(s).boundingBox()));
    expect(b!.y).toBeGreaterThan(a!.y + a!.height - 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  });
});
