const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.route('**/api/**', route => new URL(route.request().url()).pathname === '/api/config'
        ? route.continue() : route.fulfill({ json: { tracks: [], playlists: [], imports: [], history: [], threads: [], counts: {} } }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8096');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'test@example.com' } }; showApp(); clearInterval(queueTimer); });
      await page.locator('#search-nav').click();
      const guide = page.locator('.search-sources');
      await expect(guide).toBeVisible();
      await expect(guide.locator('summary')).toHaveCount(0);
      await expect(guide.locator('.search-source')).toHaveCount(6);
      await expect(guide.locator('.search-flow-divider svg')).toBeVisible();
      await page.evaluate(() => { document.querySelector('#results-section').hidden = false; });
      await expect(guide).toBeVisible();
      await page.evaluate(() => {
        const row = document.createElement('div'); row.className = 'result-card'; row.id = 'fixture-result';
        document.querySelector('#tiddl-results').append(row);
      });
      await expect(guide).toBeHidden();
      await page.evaluate(() => document.querySelector('#fixture-result').remove());
      await expect(guide).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/search-flow-${width}.png` });
      await page.close();
      console.log(`${width}: guide visible initially and without results, hidden with tracks, no overflow`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
