const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [], imports = [];
      const url = 'https://music.apple.com/fr/playlist/melodic-techno/pl.u-EdAVz8eCqqRzE2b';
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/library') return route.fulfill({ json: {
          tracks: [], playlists: [], tags: [], folders: [], imports: [],
        } });
        if (path === '/api/playlist-imports') {
          imports.push(route.request().postDataJSON());
          return route.fulfill({ status: 202, json: { id: 'import', playlist_id: 'playlist' } });
        }
        errors.push(path); return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
      await page.locator('#search-nav').click();
      await page.locator('#search-input').evaluate((input, text) => {
        const clipboardData = new DataTransfer();
        clipboardData.setData('text/plain', text);
        input.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
      }, url);
      await expect.poll(() => imports.length).toBe(1);
      await expect(page.locator('#search-view')).toBeHidden();
      await page.locator('#search-nav').click();
      await page.locator('#search-input').fill(url);
      await page.locator('#search-input').press('Enter');
      await expect.poll(() => imports.length).toBe(2);
      expect(imports).toEqual([{ url }, { url }]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/apple-import-${engine.name()}-${width}.png` });
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: Apple Music metadata import and search routing passed`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
