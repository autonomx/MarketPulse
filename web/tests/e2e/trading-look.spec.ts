import { expect, test, type Page } from '@playwright/test';
import { installTradingMocks } from './support/tradingMocks';

/*
 * The trading terminal's look (W-3, DECISIONS "TradingView-like look"): screenshot baselines of Omnix's own UI for
 * the screens the look covers, in dark and light. A change to these screens updates the baselines on purpose
 * (`npx playwright test tests/e2e/trading-look.spec.ts --update-snapshots`), reviewed as a picture diff.
 * Baselines are recorded on Windows, where the gates run: rendering depends on the OS and fonts.
 */

const FROZEN_NOW = new Date('2026-10-19T14:30:00Z');

async function openTerminal(page: Page, appearance: 'dark' | 'light'): Promise<void> {
  await page.clock.setFixedTime(FROZEN_NOW);
  await page.addInitScript((mode) => window.localStorage.setItem('omnix.appearance.mode', mode), appearance);
  await installTradingMocks(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/trading');
  await expect(page.locator('.trading-chart-panel').first()).toBeVisible();
  // Let the chart render and fonts settle.
  await page.waitForTimeout(1_500);
}

const shot = { maxDiffPixelRatio: 0.02, animations: 'disabled' as const, caret: 'hide' as const };

for (const appearance of ['dark', 'light'] as const) {
  test.describe(`trading look (${appearance})`, () => {
    test('terminal with chart, toolbars and watchlist', async ({ page }) => {
      await openTerminal(page, appearance);
      await expect(page.locator('.trading-workspace')).toHaveScreenshot(`terminal-${appearance}.png`, shot);
    });

    test('symbol search', async ({ page }) => {
      await openTerminal(page, appearance);
      await page.getByRole('button', { name: 'Open symbol search' }).click();
      const dialog = page.getByRole('dialog', { name: 'Symbol search' });
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveScreenshot(`symbol-search-${appearance}.png`, shot);
    });

    test('indicators menu', async ({ page }) => {
      await openTerminal(page, appearance);
      await page.locator('.trading-indicator-manager').getByRole('button', { name: 'Indicators' }).click();
      const menu = page.getByRole('group', { name: 'Technical indicators' });
      await expect(menu).toBeVisible();
      await expect(menu).toHaveScreenshot(`indicators-${appearance}.png`, shot);
    });

    test('alert dialog', async ({ page }) => {
      await openTerminal(page, appearance);
      await page.getByRole('button', { name: 'Right panel', exact: true }).click();
      const side = page.getByRole('complementary', { name: 'Trading side panel', exact: true });
      await side.getByRole('tab', { name: 'Alerts', exact: true }).click();
      await side.getByRole('button', { name: 'Add alert' }).click();
      const dialog = page.getByRole('dialog', { name: /Create alert on BTCUSDT/ });
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveScreenshot(`alert-dialog-${appearance}.png`, shot);
    });
  });
}
