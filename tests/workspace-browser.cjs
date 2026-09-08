const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 768, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const library = {
        tracks: Array.from({ length: 10 }, (_, i) => ({ job_id: `track-${i}`, state: 'done', title: ['After Hours', 'Midnight Session', 'A Very Long Track Title That Must Stay Readable In The Library'][i % 3], artist: 'Studio Sessions', provider: ['tiddl', 'beatport', 'qobuz'][i % 3], streamable: true, tags: [], mix_analysis: { bpm: 125 + i, key: 'Am', camelot: '8A', duration: 286 + i } })),
        playlists: [{ id: 'p', name: 'Warm up', folder_id: 'f', track_ids: ['track-0', 'track-1'] }, { id: 'r', name: 'Une playlist avec un nom volontairement très long', track_ids: [] }],
        folders: [{ id: 'f', name: 'DJ Sets', collapsed: false }],
        tags: [{ id: 't', name: 'House', color: '#e5a467', track_ids: ['track-0'] }], imports: [],
      };
      await page.route('**/api/**', route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/playlist-folders/f') Object.assign(library.folders[0], request.postDataJSON());
        else if (path === '/api/playlists/r/folder') library.playlists[1].folder_id = request.postDataJSON().folder_id;
        else if (path === '/api/playlists/p/tracks') library.playlists[0].track_ids.push(request.postDataJSON().job_id);
        else if (path === '/api/tags/t/tracks') {
          const job = request.postDataJSON().job_id;
          library.tags[0].track_ids.push(job);
          library.tracks.find(track => track.job_id === job).tags = [{ id: 't', name: 'House', color: '#e5a467' }];
        } else { errors.push(path); return route.fulfill({ status: 500 }); }
        return route.fulfill({ json: { ok: true } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'studio@example.com' } }; showApp(); });
      await expect(page.locator('#library-tracks .library-track')).toHaveCount(10);
      await expect(page.locator('.playlist-entry .collection-rename, .tag-row .tag-delete')).toHaveCount(0);
      await page.evaluate(() => { window.savedRow = document.querySelector('.library-track'); });
      await page.evaluate(() => refreshQueue());
      expect(await page.evaluate(() => savedRow === document.querySelector('.library-track'))).toBe(true);
      await page.locator('.folder-toggle').click();
      await expect(page.locator('.folder-playlists')).toBeHidden();
      await expect(page.locator('#library-tracks .library-track')).toHaveCount(2);
      await page.locator('.folder-toggle').click();
      await expect(page.locator('.folder-playlists')).toBeVisible();
      await page.locator('.playlist-entry [data-playlist-id="r"]').click();
      await page.locator('#collection-menu > summary').click();
      await page.getByRole('combobox', { name: 'Déplacer la playlist dans un dossier' }).selectOption('f');
      await expect(page.locator('.folder-playlists .playlist-entry')).toHaveCount(2);
      await page.keyboard.press('Escape');
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await page.evaluate(() => {
        const event = new Event('drop', { bubbles: true });
        event.dataTransfer = new DataTransfer(); event.dataTransfer.setData('application/x-tunesaver-job', 'track-2');
        document.querySelector('[data-playlist-id="p"]').dispatchEvent(event);
      });
      await expect(page.locator('#library-tracks .library-track')).toHaveCount(3);
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await page.evaluate(() => {
        const event = new Event('drop', { bubbles: true });
        event.dataTransfer = new DataTransfer(); event.dataTransfer.setData('application/x-tunesaver-tag', 't');
        document.querySelector('.library-track[data-job-id="track-3"]').dispatchEvent(event);
      });
      await expect(page.locator('.library-track[data-job-id="track-3"] .track-tag')).toHaveText('House');
      await page.locator('[data-tag-id="t"]').click();
      await expect(page.locator('#library-tracks .library-track')).toHaveCount(2);
      await page.locator('[data-playlist-id="p"]').click();
      await page.locator('#collection-menu > summary').click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const menu = await page.locator('#collection-actions').boundingBox();
      expect(menu.x).toBeGreaterThanOrEqual(0);
      expect(menu.x + menu.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: `/work/workspace-${engine.name()}-${width}.png`, fullPage: true });
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: folders, menus, moves, tags, stable rows and responsive layout OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
