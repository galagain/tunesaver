const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      let starts = 0;
      const item = { id: 'preview', playlist_id: 'playlist', name: 'Large playlist', state: 'ready',
        total: 1201, processed: 0, queued: 0, failures: [], error: '' };
      const tracks = Array.from({ length: 1201 }, (_, i) => ({ title: `Song ${i + 1}`, artist: `Artist ${i + 1}`, duration: 180 }));
      const library = { tracks: [], playlists: [{ id: 'playlist', name: item.name, track_ids: [] }],
        tags: [], folders: [], orders: {}, imports: [item] };
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/playlist-imports') return route.fulfill({ status: 202, json: { id: item.id, playlist_id: item.playlist_id } });
        if (path === '/api/playlist-imports/preview') return route.fulfill({ json: { tracks, state: item.state } });
        if (path === '/api/playlist-imports/preview/start') {
          starts++; item.state = 'running';
          return route.fulfill({ status: 202, json: { started: true } });
        }
        errors.push(`Unexpected request: ${path}`);
        return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8092');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
      await page.locator('#search-nav').click();
      await page.locator('#import-playlist-button').click();
      await page.locator('#import-links').fill('https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc');
      await page.locator('#import-submit').click();
      await expect(page.locator('.import-preview-track')).toHaveCount(1201);
      await expect(page.locator('.library-table-head')).toBeHidden();
      await expect(page.locator('.import-preview-head')).toBeVisible();
      expect(starts).toBe(0);
      await page.locator('.import-preview-track').last().scrollIntoViewIfNeeded();
      await expect(page.locator('.import-preview-track').last()).toContainText('Song 1201');
      await page.locator('#library-search').fill('Song 1201');
      await expect(page.locator('.import-preview-track')).toHaveCount(1);
      await page.locator('#library-search').fill('');
      await expect(page.locator('.import-preview-track')).toHaveCount(1201);
      await page.locator('.import-preview-track').first().scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/playlist-preview-${width}.png` });
      await page.getByRole('button', { name: 'Rechercher et télécharger', exact: true }).click();
      await expect.poll(() => starts).toBe(1);
      await expect(page.locator('.import-preview-track')).toHaveCount(0);
      await expect(page.locator('.library-table-head')).toBeVisible();
      await expect(page.locator('.import-preview-head')).toBeHidden();
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: complete metadata preview, filtering, explicit start, no automatic download`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
