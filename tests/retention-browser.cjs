const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const track = { job_id: '20260908T190000Z-00000001', title: 'A retained track',
        artist: 'An artist', provider: 'tiddl', state: 'done', expired: true,
        streamable: false, source_url: 'https://tidal.com/track/123', tags: [] };
      let requests = 0;
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path.endsWith('/redownload')) {
          expect(route.request().method()).toBe('POST');
          expect(path).toBe(`/api/library/${track.job_id}/redownload`);
          requests++;
          track.state = 'pending';
          return route.fulfill({ status: 202, json: { job_id: track.job_id } });
        }
        if (path === '/api/library') return route.fulfill({ json: {
          tracks: track.state === 'done' ? [track] : [],
          playlists: [{ id: 'playlist', name: 'My playlist', track_ids: [track.job_id] }],
          folders: [], tags: [{ id: 'old-tag', name: 'Legacy tag' }], imports: [], orders: {},
        } });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [{ ...track, id: track.job_id }] } });
        if (path.startsWith('/api/history')) return route.fulfill({ json: { history: [track] } });
        errors.push(`Unexpected request: ${path}`);
        return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
      await expect(page.locator('.restore-audio')).toBeVisible();
      await expect(page.locator('#tag-list, #create-tag')).toHaveCount(0);
      await expect(page.getByText('Legacy tag', { exact: true })).toHaveCount(0);
      await page.evaluate(() => { selectedPlaylistId = 'playlist'; renderLibrary(); });
      await expect(page.locator('.restore-audio')).toBeVisible();
      await expect(page.locator('.library-track .source-badge')).toHaveAttribute('href', track.source_url);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/retention-${width}.png` });
      await page.locator('.restore-audio').click();
      await expect.poll(() => requests).toBe(1);
      await expect(page.locator('.library-track[data-state="pending"]')).toHaveCount(1);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: archived track, source link, playlist, redownload, no tags OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
