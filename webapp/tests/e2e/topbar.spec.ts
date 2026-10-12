import { test, expect } from '@playwright/test';

test('topbar search reserves space and returns keyboard focus when closed', async ({ page }) => {
  await page.goto(`/?topbar-review=${Date.now()}`, { waitUntil: 'domcontentloaded' });
  const toggle = page.locator('#global-search-toggle');
  const input = page.locator('#global-search-input');

  // Include the clock and a populated status label in the width budget,
  // rather than relying on the narrower initial loading placeholders.
  await page.locator('#data-health-value').evaluate(el => { el.textContent = '100%'; });
  for (const width of [1920, 1600, 1440, 1280, 1200, 1101, 1024, 820, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(input).toBeFocused();
    await input.fill('AAPL');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const layout = await page.locator('.topbar').evaluate(header => {
      const controls = [...header.querySelectorAll('.brand-cluster, .nav-tab, .q-search, .market-toggle, .q-health, .guide-link, .theme-toggle, #refresh-dashboard')]
        .filter(el => el.getBoundingClientRect().width > 0)
        .map(el => ({ name: el.className, rect: el.getBoundingClientRect() }));
      const overlaps: string[] = [];
      for (let i = 0; i < controls.length; i++) {
        for (const b of controls.slice(i + 1)) {
          const a = controls[i];
          if (Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left) > 1 &&
              Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top) > 1) {
            overlaps.push(`${a.name} / ${b.name}`);
          }
        }
      }
      return { overlaps, overflow: document.documentElement.scrollWidth > innerWidth };
    });
    expect(layout, `expanded search at ${width}px`).toEqual({ overlaps: [], overflow: false });
    await input.press('Escape');
    await expect(toggle).toBeFocused();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(input).toHaveAttribute('tabindex', '-1');
  }

  await page.keyboard.press('Control+k');
  await expect(input).toBeFocused();
  await page.emulateMedia({ contrast: 'more', reducedMotion: 'reduce' });
  await expect(page.locator('.topbar')).toHaveCSS('backdrop-filter', 'none');
});

test('selected market and destination are marked by state, weight and underline — never colour alone', async ({ page }) => {
  await page.goto(`/?segment-review=${Date.now()}`, { waitUntil: 'domcontentloaded' });
  const india = page.locator('.mkt-btn[data-market="IN"]');
  const us = page.locator('.mkt-btn[data-market="US"]');
  await india.click();
  await expect(india).toHaveAttribute('aria-pressed', 'true');
  await expect(us).toHaveAttribute('aria-pressed', 'false');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(india).toHaveAttribute('aria-pressed', 'true');

  for (const view of ['discover', 'picks', 'holdings', 'analyze', 'dashboard']) {
    const tab = page.locator(`.nav-tab[data-view="${view}"]`);
    await tab.click();
    await expect(page.locator('.nav-tab[aria-selected="true"]')).toHaveCount(1);
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator(`#view-${view}`)).toBeVisible();
    // The underline is drawn on the bar's edge; weight changes too.
    await expect.poll(() => tab.evaluate(el => getComputedStyle(el, '::after').backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
    expect(Number(await tab.evaluate(el => getComputedStyle(el).fontWeight))).toBeGreaterThanOrEqual(600);
    // No decorative gradient on the selected destination.
    expect(await tab.evaluate(el => getComputedStyle(el).backgroundImage)).toBe('none');
  }

  // Model Evidence is secondary: reached by the header icon or the footer
  // link, it selects no primary step and both links mark it current.
  await expect(page.locator('.nav-tab[data-view="track"]')).toHaveCount(0);
  await page.locator('.app-footer [data-view-link="track"]').click();
  await expect(page.locator('#view-track')).toBeVisible();
  await expect(page.locator('.nav-tab[aria-selected="true"]')).toHaveCount(0);
  await expect(page.locator('.app-footer [data-view-link="track"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#evidence-link')).toHaveAttribute('aria-current', 'page');
  // Phones hide the header icons (as with the guide); the footer link remains.
  if (await page.locator('#evidence-link').isVisible()) {
    await page.locator('.nav-tab[data-view="dashboard"]').click();
    await page.locator('#evidence-link').click();
    await expect(page.locator('#view-track')).toBeVisible();
  }
  await page.locator('[data-back-view]').click();
  await expect(page.locator('#view-dashboard')).toBeVisible();

  // Arrow keys move between steps (WAI-ARIA tabs).
  await page.locator('.nav-tab[data-view="dashboard"]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.nav-tab[data-view="discover"]')).toBeFocused();
  await expect(page.locator('#view-discover')).toBeVisible();

  // A market change from the Portfolio view must update the header too.
  await page.locator('.nav-tab[data-view="picks"]').click();
  await page.locator('#sp-market').selectOption('US');
  await expect(us).toHaveAttribute('aria-pressed', 'true');
  await expect(india).toHaveAttribute('aria-pressed', 'false');
  await page.setViewportSize({ width: 320, height: 900 });
  await expect(india.locator('.mkt-label')).toBeVisible();
  await expect(us.locator('.mkt-label')).toBeVisible();
});
