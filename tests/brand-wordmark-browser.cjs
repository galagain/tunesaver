const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.clock.install();
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: { tracks: [], playlists: [], imports: [], tags: [], orders: {} } });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        return route.fulfill({ json: { history: [], threads: [] } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8096');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); clearInterval(queueTimer); });
      const label = page.locator('.brand-wordmark-text');
      await expect(label).toHaveText('TuneSaver');
      const originalBox = await page.locator('.brand-wordmark').boundingBox();
      await page.clock.runFor(3700);
      expect((await label.textContent()).length).toBeLessThan(9);
      await page.clock.runFor(2100);
      await expect(label).toHaveText('ThuneSaver');
      expect(await page.locator('.brand-wordmark').boundingBox()).toEqual(originalBox);
      await page.screenshot({ path: `/work/wordmark-${width}.png` });
      await page.clock.runFor(6000);
      await expect(label).toHaveText('TuneSaver');
      await page.locator('#search-nav').click();
      await page.locator('.sidebar-brand').click();
      await expect(page.locator('#search-view')).toBeHidden();
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await expect(label).toHaveText('TuneSaver');
      await page.clock.runFor(15000);
      await expect(label).toHaveText('TuneSaver');
      expect(await label.evaluate(el => getComputedStyle(el, '::after').display)).toBe('none');
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: typing, deletion, stable layout, navigation and reduced motion OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
