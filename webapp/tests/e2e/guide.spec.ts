import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// "How QUANTA works": a secondary view. It must be reachable without taking a
// primary tab, read cleanly at every width and theme, keep every demo usable
// from the keyboard, show everything under reduced motion, and never leave a
// section blank in a tall (full-page) viewport.
const WIDTHS = [360, 640, 820, 1080, 1280, 1600];
const guide = () => `/?nocache=${Date.now()}#guide`;

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

test('GUIDE-001 opens from the help link without selecting a primary tab', async ({ page }) => {
  const errors = trackConsole(page);
  await page.goto(`/?nocache=${Date.now()}`, { waitUntil: 'domcontentloaded' });
  const help = page.locator('#guide-link');
  if (await help.isVisible()) await help.click();
  else await page.locator('.footer-links a[data-view-link="guide"]').click();
  await expect(page.locator('#view-guide')).toBeVisible();
  await expect(page).toHaveURL(/#guide$/);
  await expect(page.locator('#guide-title')).toBeFocused();
  await expect(page.locator('.nav-tab[aria-selected="true"]')).toHaveCount(0);
  await expect(page.locator('#guide-evidence-meta')).toHaveAttribute('data-state', /live|error/, { timeout: 30_000 });
  expect(errors, errors.join('\n')).toEqual([]);
});

test('GUIDE-002 no horizontal overflow at any breakpoint', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  for (const w of WIDTHS) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(120);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `guide @ ${w}px overflows by ${overflow}px`).toBeLessThanOrEqual(1);
  }
});

test('GUIDE-003 the path jumps to each page section and moves focus to it', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  for (const id of ['hw-picks', 'hw-holdings', 'hw-overview']) {
    await page.locator(`.hw-flow [data-hw-jump="${id}"]`).click();
    await expect(page.locator(`#${id} h2`)).toBeFocused();
    await expect.poll(() => page.locator(`#${id}`).evaluate((e) => {
      const r = e.getBoundingClientRect(); return r.top < window.innerHeight && r.bottom > 0;
    })).toBe(true);
  }
  await page.locator('#hw-research .hw-link').click();
  await expect(page.locator('#view-analyze')).toBeVisible();
  await expect(page.locator('#an-ticker')).toBeFocused();
});

test('GUIDE-004 the decision lab follows the rules from the keyboard', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  const slider = page.locator('#hw-score');
  await slider.fill('80');
  await expect(page.locator('#hw-verdict')).toHaveText('Strong Buy');
  await expect(page.locator('.hw-calls [data-call="strong"]')).toHaveClass(/is-active/);
  await page.locator('#view-guide input[name="hw-grade"][value="D"]').check();
  await expect(page.locator('#hw-verdict')).toHaveText('Accumulate');
  await expect(page.locator('#hw-why')).toContainText('Risk grade D caps a fresh buy at Accumulate');
  await page.locator('#view-guide input[name="hw-grade"][value="ok"]').check();
  await slider.focus();
  await page.keyboard.press('Home');
  await expect(page.locator('#hw-verdict')).toHaveText('Avoid');
  await expect(page.locator('#hw-newmoney')).toHaveText('Avoid');
  await slider.fill('50');
  await expect(page.locator('#hw-verdict')).toHaveText('Watchlist');
  await page.locator('#hw-owned').check();
  await expect(page.locator('#hw-verdict')).toHaveText('Hold');
});

test('GUIDE-005 FAQ answers open from the keyboard', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  await page.locator('.hw-faq summary').first().focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.hw-faq details').first()).toHaveAttribute('open', '');
  await expect(page.locator('.hw-faq details').first().locator('p')).toContainText('research tool');
});

test('GUIDE-006 the funnel follows the gates as they scroll by, and gates can be picked directly', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'load' });
  await expect(page.locator('#view-guide .hw')).toHaveClass(/hw-motion/);
  await expect(page.locator('#hw-funnel-count')).toContainText('12 example names');
  const wide = (page.viewportSize()?.width || 0) > 1080;
  if (wide) {
    // Beside the sticky funnel, the gate in the middle of the screen is the step.
    await page.locator('.hw-gate[data-gate="4"]').evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await expect(page.locator('#hw-funnel-count')).toContainText('6 of 12 left');
    await expect(page.locator('.hw-scrolly-stage')).toBeInViewport();
    await page.locator('.hw-gate[data-gate="8"]').evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await expect(page.locator('#hw-funnel-count')).toContainText('2 of 12 picked');
  } else {
    await page.locator('.hw-funnel-stage').scrollIntoViewIfNeeded();
    await expect(page.locator('#hw-funnel-count')).toContainText('2 of 12 picked', { timeout: 10_000 });
  }
  await expect(page.locator('.hw-row.is-funded')).toHaveCount(2);
  await page.locator('.hw-gate[data-gate="4"]').click();
  await expect(page.locator('#hw-funnel-count')).toContainText('6 of 12 left');
  await expect(page.locator('.hw-gate[data-gate="4"]')).toHaveAttribute('aria-current', 'step');
  await page.locator('#hw-funnel-reset').click();
  await expect(page.locator('.hw-row.is-cut')).toHaveCount(0);
});

test('GUIDE-006b the chapter rail marks the section being read', async ({ page }) => {
  test.skip((page.viewportSize()?.width || 0) <= 1180, 'the rail shows on wide screens only');
  await page.goto(guide(), { waitUntil: 'load' });
  await page.locator('#hw-picks').evaluate((e) => e.scrollIntoView({ block: 'start', behavior: 'instant' }));
  await expect(page.locator('.hw-rail-btn[data-hw-jump="hw-picks"]')).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('.hw-rail-in')).toBeInViewport();
  await page.locator('.hw-rail-btn[data-hw-jump="hw-trust"]').click();
  await expect(page.locator('#guide-trust-title')).toBeFocused();
});

test('GUIDE-007 the calculator recomputes the position as inputs change', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  const final = page.locator('#hw-receipt li.is-final .hw-rc-v');
  await expect(final).toHaveText('25 shares');
  await page.locator('#hw-c-mandate').selectOption('conservative');
  await expect(final).toHaveText('17 shares');
  await page.locator('#hw-c-stop').fill('90');
  await expect(page.locator('#hw-receipt')).toContainText('not binding');
  await page.locator('#hw-c-stop').fill('120');
  await expect(page.locator('#hw-c-stop')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.locator('#hw-receipt')).toContainText('The stop has to sit below the entry');
});

test('GUIDE-008 posture and holdings pickers work with arrow keys', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  await page.locator('.hw-posture-row.is-active').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.hw-posture-row[data-posture="neutral"]')).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('.hw-posture-row[data-posture="neutral"]')).toBeFocused();
  await expect(page.locator('#hw-posture-out')).toContainText('45-65% gross exposure');
  await page.locator('#view-guide input[name="hw-conf"][value="low"]').check();
  await expect(page.locator('#hw-posture-out')).toContainText('30-55% gross, in place of 45-65%');
  await page.locator('.hw-bar[data-hold="x"]').focus();
  await page.keyboard.press('ArrowUp');
  await expect(page.locator('[data-hold-panel="p"]')).toBeVisible();
  await expect(page.locator('[data-hold-panel="x"]')).toBeHidden();
  await page.locator('.hw-bar[data-hold="r"]').click();
  await expect(page.locator('[data-hold-panel="r"]')).toContainText('not an instruction to sell');
});

for (const theme of ['dark', 'light'] as const) {
  test(`GUIDE-A11Y-${theme.toUpperCase()} guide has zero WCAG 2.1 AA violations`, async ({ page }) => {
    await page.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) { /* ignore */ } }, theme);
    // Reduced motion draws every section at once, so axe sees all of it.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(guide(), { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#view-guide')).toBeVisible();
    const results = await new AxeBuilder({ page }).include('#view-guide')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
    expect(summary, summary.join('\n')).toEqual([]);
  });
}

test('GUIDE-009 reduced motion: nothing hidden, nothing animating, demos finished', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.goto(guide(), { waitUntil: 'load' });
  await expect(page.locator('#view-guide .hw')).not.toHaveClass(/hw-motion/);
  const hidden = await page.locator('#view-guide [data-reveal], #view-guide .hw-ch-head, #view-guide .hw-checks-row li, #view-guide .hw-stamp, #view-guide .hw-gate')
    .evaluateAll((els) => els.filter((e) => getComputedStyle(e).opacity !== '1').length);
  expect(hidden).toBe(0);
  // Nothing runs by itself; the reel waits, paused on its closing frame.
  expect(await page.evaluate(() => document.getAnimations()
    .filter((a) => (a.effect as KeyframeEffect)?.target?.closest?.('#view-guide'))
    .filter((a) => a.playState === 'running').length)).toBe(0);
  await expect(page.locator('#hw-funnel-count')).toContainText('2 of 12 picked');
  await expect(page.locator('.hw-progress')).toBeHidden();
  await ctx.close();
});

test('GUIDE-010 a full-page capture shows every section fully drawn', async ({ page }) => {
  // The reported bug: blank screens between tour steps and empty blocks in a
  // full-page screenshot. A viewport tall enough for the whole page must show
  // everything at full opacity, with no section taller than its content.
  await page.goto(guide(), { waitUntil: 'load' });
  await expect(page.locator('#view-guide .hw')).toHaveClass(/hw-motion/);
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: 1440, height: Math.min(height + 200, 16000) });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const faded = await page.evaluate(() => [...document.querySelectorAll('#view-guide [data-reveal], #view-guide .hw-ch-head, #view-guide .hw-gate, #view-guide .hw-chapters li, #view-guide .hw-proof li, #view-guide .hw-flow-list li, #view-guide .hw-ledger li, #view-guide .hw-limits li, #view-guide .hw-toolchips li, #view-guide .hw-end')]
    .filter((el) => Number(getComputedStyle(el).opacity) < 0.99)
    .map((el) => `${el.className} ${getComputedStyle(el).opacity}`));
  expect(faded, faded.join('\n')).toEqual([]);
  // Page height stays the same in a tall viewport: no viewport-sized spacing.
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(height + 260);
});

test('GUIDE-011 jumping straight to a section shows it without waiting for a timer', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'load' });
  for (const sel of ['.hw-posture', '.hw-lab', '.hw-calc', '.hw-book', '.hw-trust', '.hw-end']) {
    await page.locator(sel).evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const op = await page.locator(sel).evaluate((e) => Number(getComputedStyle(e.closest('[data-reveal]') || e).opacity));
    expect(op, `${sel} opacity after an instant jump`).toBeGreaterThan(0.99);
  }
});

test('GUIDE-012 nothing loops, and the progress bar stays on the guide', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'load' });
  await page.waitForTimeout(2000);
  const loops = await page.evaluate(() => document.getAnimations()
    .filter((a) => (a.effect as KeyframeEffect)?.target?.closest?.('#view-guide'))
    .filter((a) => a.effect && a.effect.getComputedTiming().iterations === Infinity).map((a) => (a.effect as KeyframeEffect).target?.className));
  expect(loops).toEqual([]);
  await page.locator('.hw-hero .hw-btn--primary').click();
  await expect(page.locator('#view-dashboard')).toBeVisible();
  await expect(page.locator('.hw-progress')).toBeHidden();
});

test('GUIDE-013 a full-page capture that keeps the viewport shows every section', async ({ page }) => {
  // The reported capture: DevTools-style "full size" screenshots render past
  // the viewport without scrolling or resizing it. Only blocks in the band
  // just below the screen may wait for a reveal; everything further down is
  // drawn, and the capture itself has no empty stretches.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(guide(), { waitUntil: 'load' });
  await expect(page.locator('#view-guide .hw')).toHaveClass(/hw-motion/);
  await page.waitForTimeout(600);
  const faded = await page.evaluate(() => [...document.querySelectorAll('#view-guide [data-hw-fx]')]
    .filter((el) => el.getBoundingClientRect().top > innerHeight * 1.5)
    .filter((el) => el.classList.contains('hw-armed') || Number(getComputedStyle(el).opacity) < 0.99)
    .map((el) => el.className));
  expect(faded, faded.join('\n')).toEqual([]);
  expect(await page.locator('#view-guide [data-hw-fx]').count()).toBeGreaterThan(40);
});

test('GUIDE-014 a block arriving from the bottom edge animates in, and ends fully drawn', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(guide(), { waitUntil: 'load' });
  await page.addStyleTag({ content: 'html { scroll-behavior: auto !important; }' });
  const lab = page.locator('#view-guide .hw-lab');
  const top = await lab.evaluate((e) => e.getBoundingClientRect().top + scrollY);
  await page.evaluate((y) => scrollTo(0, y), top - 900 - 100);
  await expect(lab).toHaveClass(/hw-armed/);
  for (let i = 0; i < 6; i += 1) { await page.mouse.wheel(0, 80); await page.waitForTimeout(30); }
  await expect(lab).toHaveClass(/hw-shown/);
  // The block, the ladder rungs and the verdict each run once.
  const running = await page.evaluate(() => document.getAnimations()
    .filter((a) => (a.effect as KeyframeEffect)?.target?.closest?.('.hw-lab')).length);
  expect(running).toBeGreaterThan(3);
  await expect.poll(() => lab.evaluate((e) => Number(getComputedStyle(e).opacity))).toBeGreaterThan(0.99);
  await expect.poll(() => page.locator('#hw-verdict').evaluate((e) => Number(getComputedStyle(e).opacity))).toBeGreaterThan(0.99);
});

test('REEL-001 the reel autoplays once in view, and pauses, scrubs and jumps by chapter', async ({ page }) => {
  await page.goto(guide(), { waitUntil: 'load' });
  await page.waitForFunction(() => (window as any).QuantaReel);
  await page.locator('#hw-reel').evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await expect(page.locator('.hw-reel-player')).toHaveClass(/is-playing/);
  await expect.poll(() => page.evaluate(() => (window as any).QuantaReel.time())).toBeGreaterThan(300);
  await page.locator('#hw-reel-play').click();
  await expect(page.locator('.hw-reel-player')).not.toHaveClass(/is-playing/);
  await expect(page.locator('#hw-reel-play')).toHaveAttribute('aria-label', 'Play reel');
  const held = await page.evaluate(() => (window as any).QuantaReel.time());
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => (window as any).QuantaReel.time())).toBe(held);
  await page.locator('#hw-reel-seek').fill('8500');
  await expect(page.locator('#rl-scene-k')).toHaveText('04 RESEARCH');
  await expect(page.locator('#hw-reel-seek')).toHaveAttribute('aria-valuetext', /0:09 of 0:15, research/);
  await expect(page.locator('[data-reel-t="7250"]')).toHaveAttribute('aria-current', 'true');
  await page.locator('[data-reel-t="9850"]').click();
  await expect(page.locator('.hw-reel-player')).toHaveClass(/is-playing/);
  await expect(page.locator('#rl-scene-k')).toHaveText('05 PICKS');
  // Scrolling away pauses it.
  await page.locator('.hw-faq-sec').evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await expect(page.locator('.hw-reel-player')).not.toHaveClass(/is-playing/);
});

test('REEL-002 reduced motion: no autoplay, the poster waits for a press', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.goto(guide(), { waitUntil: 'load' });
  await page.waitForFunction(() => (window as any).QuantaReel);
  await page.locator('#hw-reel').evaluate((e) => e.scrollIntoView({ block: 'center', behavior: 'instant' }));
  await page.waitForTimeout(600);
  await expect(page.locator('.hw-reel-player')).toHaveClass(/is-idle/);
  expect(await page.evaluate(() => (window as any).QuantaReel.time())).toBe(15000);
  await expect(page.locator('#rl-s7')).toHaveCSS('opacity', '1');
  await page.locator('#hw-reel-cover').click();
  await expect(page.locator('.hw-reel-player')).toHaveClass(/is-playing/);
  await expect.poll(() => page.evaluate(() => (window as any).QuantaReel.time())).toBeLessThan(15000);
  await ctx.close();
});

test('REEL-003 the WebM download is served', async ({ page, request }) => {
  await page.goto(guide(), { waitUntil: 'domcontentloaded' });
  const href = await page.locator('.hw-reel-dl').getAttribute('href');
  const res = await request.get(href!);
  expect(res.status()).toBe(200);
  expect((await res.body()).length).toBeGreaterThan(500_000);
});
