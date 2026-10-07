const {chromium, expect} = require('@playwright/test');
(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const playlists = [], errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/playlists' && route.request().method() === 'POST') {
          const playlist = {id: 'new', name: route.request().postDataJSON().name, track_ids: []};
          playlists.push(playlist); return route.fulfill({json: {playlist}});
        }
        return route.fulfill({json: {tracks: [], playlists, imports: [], tags: [], orders: {}, active: {}, counts: {}, recent: [], history: [], threads: []}});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await expect(page.locator('#library-menu')).toHaveCount(0);
      const plus = page.getByRole('button', {name: 'Nouvelle playlist', exact: true});
      await expect(plus).toBeVisible();
      const title = await page.locator('#playlist-title').boundingBox(), button = await plus.boundingBox();
      expect(button.x).toBeGreaterThan(title.x + title.width - 1);
      expect(Math.abs(button.y + button.height / 2 - title.y - title.height / 2)).toBeLessThanOrEqual(1);
      await plus.click();
      await expect(page.locator('#action-dialog-input')).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(plus).toBeFocused();
      await plus.click();
      await page.locator('#action-dialog-input').fill('Mon nouveau set');
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('#playlist-list')).toContainText('Mon nouveau set');
      await page.screenshot({path: `/work/playlist-create-${width}.png`});
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: playlist plus aligned, cancel/focus and creation OK`);
    }
  } finally {await browser.close();}
})().catch(e => {console.error(e); process.exitCode = 1;});
