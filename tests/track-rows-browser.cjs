const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        if (new URL(route.request().url()).pathname === '/api/config') return route.continue();
        return route.fulfill({ json: {} });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        const track = { title: 'Get Lucky', artist: 'Daft Punk', duration: 369,
          matching: { status: 'matched', target: { provider: 'qobuz', url: 'https://play.qobuz.com/track/123' }, candidate: { bpm: 116, key: 'F#m' } } };
        libraryState = { tracks: [{ job_id: 'job', title: track.title, artist: track.artist, state: 'done',
          source_url: track.matching.target.url, provider: 'qobuz', mix_analysis: { bpm: 116, key: 'F#m', duration: 369 } }],
          playlists: [{ id: 'album', name: 'Album', track_ids: [] }, { id: 'playlist', name: 'Playlist', track_ids: ['job'] }],
          imports: [{ id: 'import', playlist_id: 'album', state: 'matched', total: 1, processed: 1 }], folders: [], tags: [], orders: {} };
        importPreviews.set('import', [track]);
        selectedPlaylistId = 'album'; renderLibrary();
      });
      const row = page.locator('#library-tracks .library-track').first();
      await expect(row.locator('.library-track-body')).toHaveText('Get LuckyDaft Punk');
      await expect(row.locator('.source-badge')).toHaveAttribute('href', 'https://play.qobuz.com/track/123');
      await expect(row).not.toContainText('Trouvé');
      const albumMetrics = await row.locator('.track-bpm, .track-key, .track-duration').allTextContents();
      const bodyStyle = await row.locator('.library-track-body').evaluate(el => [el.innerHTML, getComputedStyle(el).gap]);
      await page.screenshot({ path: `/work/album-row-${width}.png` });
      await page.evaluate(() => { selectedPlaylistId = 'playlist'; renderLibrary(); });
      expect(await row.locator('.track-bpm, .track-key, .track-duration').allTextContents()).toEqual(albumMetrics);
      expect(await row.locator('.library-track-body').evaluate(el => [el.innerHTML, getComputedStyle(el).gap])).toEqual(bodyStyle);
      await page.evaluate(() => {
        const item = importPreviews.get('import')[0];
        item.matching = { status: 'error', error: 'Service temporairement indisponible' };
        selectedPlaylistId = 'album'; libraryTracks.renderKey = null; renderLibrary();
      });
      await expect(row.locator('.library-track-body')).toHaveText('Get LuckyDaft Punk');
      await expect(row.locator('.download-status-button')).toHaveText('Erreur');
      await expect(row.locator('.download-status-button')).toHaveAttribute('title', 'Service temporairement indisponible');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: album and playlist share metadata rendering; source logo only; errors in actions`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
