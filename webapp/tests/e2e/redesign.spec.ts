import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * QUANTA Elite — redesign guarantees (§2 ground rules of the review).
 *
 *   · axe-core WCAG 2.1 AA: zero violations on the dashboard in BOTH themes.
 *   · zero console errors, zero page errors.
 *   · zero horizontal page overflow at 360 / 640 / 820 / 1080 / 1280 / 1600.
 *   · the header never overlaps its own nav tabs (the 1280px regression that
 *     let the market toggle intercept clicks on "Stock Deep-Dive").
 *   · the maker's mark stays present in the footer.
 *
 * Structure-only assertions so the suite survives live market data.
 */

const NOCACHE = () => `/?nocache=${Date.now()}`;
const WIDTHS = [360, 640, 820, 1080, 1280, 1600];

function trackConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

async function setTheme(page: Page, theme: 'dark' | 'light') {
  await page.addInitScript((t) => { try { localStorage.setItem('quanta_theme', t); } catch (_) { /* ignore */ } }, theme);
}

async function pageOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/**
 * Wait until layout has actually settled after a viewport change.
 *
 * A fixed `waitForTimeout(150)` made this check intermittent: web fonts, async
 * data renders and image loads can each reflow the page after the timer, so an
 * overflow either appeared or did not depending on machine speed. This polls
 * the measurement itself and only returns once it has been stable across
 * consecutive frames — so a failure is a real layout fact, not a race.
 */
async function waitForLayoutSettled(page: Page, timeoutMs = 5000): Promise<void> {
  await page.evaluate(async (limit) => {
    const frame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));
    try { await (document as any).fonts?.ready; } catch { /* fonts API absent */ }
    const started = Date.now();
    let last = -1;
    let stable = 0;
    while (Date.now() - started < limit) {
      await frame();
      const w = document.documentElement.scrollWidth;
      stable = w === last ? stable + 1 : 0;
      last = w;
      if (stable >= 3) return;
    }
  }, timeoutMs);
}

/**
 * Name the elements whose box actually extends past the viewport.
 *
 * "@360px overflows by 24px" is not isolatable; a selector is. Elements inside
 * a scroll container of their own are excluded, because their width is
 * contained by design and is not what leaks to the root.
 */
async function overflowingElements(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const describe = (el: Element) => {
      const id = el.id ? `#${el.id}` : '';
      const cls = typeof el.className === 'string' && el.className
        ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
      return `${el.tagName.toLowerCase()}${id}${cls}`;
    };
    const scrolls = (el: Element) => {
      const o = getComputedStyle(el);
      return /auto|scroll|hidden/.test(o.overflowX);
    };
    const out: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll('*'))) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.right <= limit + 1) continue;
      let contained = false;
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (scrolls(p)) { contained = true; break; }
      }
      if (contained) continue;
      out.push(`${describe(el)} right=${Math.round(rect.right)} (viewport ${limit})`);
    }
    return out.slice(0, 8);
  });
}

for (const theme of ['dark', 'light'] as const) {
  test(`A11Y-${theme.toUpperCase()} dashboard has zero WCAG 2.1 AA violations`, async ({ page }) => {
    await setTheme(page, theme);
    const errors = trackConsole(page);
    await page.goto(NOCACHE(), { waitUntil: 'networkidle' });
    await expect(page.locator('#decision-brief .q-regime-title')).toBeVisible({ timeout: 60_000 });
    expect(await page.locator('html').getAttribute('data-theme')).toBe(theme);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`);
    expect(summary, summary.join('\n')).toEqual([]);
    expect(errors, errors.join('\n')).toEqual([]);
  });
}

test('UX-RESP no horizontal overflow on any view at every breakpoint', async ({ page }) => {
  const errors = trackConsole(page);
  await page.goto(NOCACHE(), { waitUntil: 'networkidle' });
  for (const view of ['dashboard', 'discover', 'analyze', 'picks', 'holdings']) {
    await page.locator(`.nav-tab[data-view="${view}"]`).dispatchEvent('click');
    for (const w of WIDTHS) {
      await page.setViewportSize({ width: w, height: 900 });
      await waitForLayoutSettled(page);
      const overflow = await pageOverflow(page);
      const culprits = overflow > 1 ? await overflowingElements(page) : [];
      expect(
        overflow,
        `${view} @ ${w}px overflows by ${overflow}px.
` +
        (culprits.length
          ? `Overflowing elements:
  ${culprits.join('\n  ')}`
          : 'No single element exceeded the viewport — suspect a scroll container ' +
            'leaking its content width, or a margin on the root.'),
      ).toBeLessThanOrEqual(1);
    }
  }
  expect(errors, errors.join('\n')).toEqual([]);
});

test('UX-NAV every nav tab is the top-most element at its centre (no header overlap)', async ({ page }) => {
  await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
  for (const w of WIDTHS) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(100);
    const obscured = await page.evaluate(() => {
      const out: string[] = [];
      document.querySelectorAll<HTMLElement>('.nav-tab').forEach((tab) => {
        const r = tab.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        if (!hit || !tab.contains(hit)) out.push(`${tab.dataset.view} hit ${hit ? hit.tagName + '.' + (hit as HTMLElement).className : 'nothing'}`);
      });
      return out;
    });
    expect(obscured, `@${w}px: ${obscured.join(', ')}`).toEqual([]);
    // And a real click switches the view.
    await page.locator('.nav-tab[data-view="analyze"]').click();
    await expect(page.locator('#view-analyze')).toHaveClass(/active/);
    await page.locator('.nav-tab[data-view="dashboard"]').click();
  }
});

test('BRAND product identity stays in the top bar and the maker is credited once in the footer', async ({ page }) => {
  await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.topbar .brand-name')).toContainText(/QUANTA/);
  await expect(page.locator('.app-footer .signature-name')).toHaveText(/Atresh Somayajula/);
  await expect(page.getByText('Atresh Somayajula', { exact: true })).toHaveCount(1);
});

test('CSS the shell loads exactly one design-system stylesheet plus the two component sheets', async ({ page }) => {
  await page.goto(NOCACHE(), { waitUntil: 'domcontentloaded' });
  const sheets = await page.evaluate(() => Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
    .map((l) => (l as HTMLLinkElement).href.split('/').pop()!.split('?')[0])
    .filter((n) => n.endsWith('.css')));
  expect(sheets).toEqual(['quanta.css', 'quote-board.css', 'signature.css']);
});
