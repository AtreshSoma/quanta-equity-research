import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('session extremes exposes every match with coverage, sorting and responsive tables', async ({ page }) => {
  const rows = Array.from({ length: 15 }, (_, i) => ({ symbol: `TEST${String(i).padStart(2, '0')}`, name: 'Test quote', price: 108, open: 100, day_low: 100, day_high: 110, prev_close: 99, change_pct: i }));
  const intel = { open_at_low: rows, open_at_high: [], open_extremes_scanned: 49,
    open_extremes_coverage: { received: 49, comparable: 45, missing_ohlc: 2, invalid_ohlc: 1, flat: 1, tracked_universe: 51 }, loading: false };
  await page.route('**/api/dashboard?*', route => route.fulfill({ json: { market: { quotes: [] }, news: { groups: {} }, market_intel: intel } }));
  await page.route('**/api/market-intel?*', route => route.fulfill({ json: intel }));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`/?monitor-test=${Date.now()}#discover`, { waitUntil: 'domcontentloaded' });
  await page.locator('#tab-open-extremes').click();
  const panel = page.locator('#open-extremes');
  await expect(panel.locator('.q-board-row')).toHaveCount(15);
  await expect(panel).toContainText('15 exact matches from 49 fetched quotes');
  await expect(panel).toContainText('not the whole exchange');
  await expect(panel).toContainText('No matching quotes in this snapshot');
  const symbolSort = panel.getByRole('button', { name: 'Sort by symbol' });
  await symbolSort.click();
  await expect(panel.locator('thead th').first()).toHaveAttribute('aria-sort', 'descending');
  await expect(panel.locator('.q-board-row').first()).toContainText('TEST14');
  await expect(panel.locator('.q-board-row').last()).toContainText('TEST00');
  await symbolSort.press('Enter');
  await expect(panel.locator('thead th').first()).toHaveAttribute('aria-sort', 'ascending');
  await expect(panel.locator('.q-board-row').first()).toContainText('TEST00');
  await expect(panel.locator('.q-board-row').last()).toContainText('TEST14');

  for (const width of [1920, 1440, 1280, 1024, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), `overflow at ${width}`).toBeLessThanOrEqual(1);
    await expect(panel.locator('.q-coverage')).toBeVisible();
    await expect(panel.locator('.q-board-row')).toHaveCount(15);
  }
  const audit = await new AxeBuilder({ page }).include('#security-monitor').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  expect(audit.violations.map(v => `${v.id}: ${v.nodes.map(n => n.target).join(', ')}`)).toEqual([]);
  expect(errors).toEqual([]);
});
