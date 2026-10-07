const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [], removals = [];
      const tracks = [
        { job_id: 'ready', title: 'Ready song', artist: 'Artist', provider: 'tiddl', state: 'done', streamable: true },
        { job_id: 'failed', title: 'Failed song', artist: 'Artist', provider: 'qobuz', state: 'failed' },
      ];
      const library = { tracks, playlists: [{ id: 'p', name: 'Set', folder_id: 'old-folder', track_ids: ['ready', 'failed'] }], imports: [], folders: [{id: 'old-folder', name: 'Old folder', collapsed: true}], tags: [], orders: {} };
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/playlists/p/tracks/failed' && route.request().method() === 'DELETE') {
          removals.push(path); library.playlists[0].track_ids = ['ready'];
          return route.fulfill({ json: { removed: true } });
        }
        errors.push(path); return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); clearInterval(queueTimer); });
      await page.locator('[data-playlist-id="p"]').click();
      await expect(page.locator('#create-folder, .playlist-folder, .folder-playlists')).toHaveCount(0);
      await expect(page.locator('.root-playlists [data-playlist-id="p"]')).toBeVisible();
      await page.locator('#collection-menu > summary').click();
      await expect(page.getByLabel('Déplacer la playlist dans un dossier')).toHaveCount(0);
      await page.locator('#collection-menu > summary').click();
      await expect(page.locator('.library-track')).toHaveCount(2);
      await expect(page.locator('.library-track .track-menu')).toHaveCount(0);
      await expect(page.locator('.library-track .track-delete')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Télécharger Ready song', exact: true })).toBeVisible();
      await expect(page.locator('.library-track .playlist-remove-track')).toHaveCount(2);
      await page.screenshot({ path: `/work/playlist-actions-${width}.png` });
      await page.getByRole('button', { name: 'Retirer de cette playlist : Failed song', exact: true }).click();
      await expect(page.locator('.library-track')).toHaveCount(1);
      expect(removals).toHaveLength(1);
      await page.evaluate(() => { selectedPlaylistId = ''; renderLibrary(); });
      await expect(page.locator('.library-track')).toHaveCount(2);
      await expect(page.locator('.library-track .track-delete')).toHaveCount(2);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: direct download, no settings, removal preserves library, permanent deletion outside playlists OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
