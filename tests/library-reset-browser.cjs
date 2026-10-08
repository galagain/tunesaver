const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      let resets = 0;
      const errors = [];
      page.on('pageerror', error => errors.push(error.stack));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library/reset') {
          expect(route.request().postDataJSON()).toEqual({confirm: 'RESET'});
          resets++;
          return route.fulfill({json: {reset: true}});
        }
        return route.fulfill({json: {tracks: [], playlists: [], tags: [], imports: [], orders: {}, active: {}, counts: {}, recent: [], history: [], threads: []}});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await page.locator('#history-toggle').click();
      await page.locator('#reset-library').click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.screenshot({path: `/work/reset-${width}.png`});
      await page.keyboard.press('Escape');
      expect(resets).toBe(0);
      await page.locator('#reset-library').click();
      await page.getByRole('button', {name: 'Tout effacer', exact: true}).click();
      await expect.poll(() => resets).toBe(1);
      await page.waitForLoadState('load');
      expect(errors).toEqual([]);
      console.log(`${width}: confirmation, cancellation and scoped reset request OK`);
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
