const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [];
      const tracks = Array.from({length: 70}, (_, i) => ({job_id: `job-${i}`, title: `Track ${i + 1}`, artist: 'Artist', provider: 'tiddl', state: 'done'}));
      const library = {tracks, playlists: ['a', 'b'].map(id => ({id, name: `Playlist ${id}`, track_ids: tracks.map(t => t.job_id)})), imports: [], tags: [], orders: {}};
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        if (path === '/api/history') return route.fulfill({json: {history: []}});
        errors.push(path); return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await expect(page.locator('.library-track')).toHaveCount(70);
      const scrolledSelection = async () => {
        await page.evaluate(() => {selectLibraryTrack('job-50'); libraryTracks.scrollTop = 1700;});
        await expect.poll(() => page.locator('#library-tracks').evaluate(el => el.scrollTop)).toBeGreaterThan(100);
      };
      for (const id of ['a', 'b', '', '']) {
        await scrolledSelection();
        await page.locator(`.playlist-filter[data-playlist-id="${id}"]`).click();
        await expect.poll(() => page.locator('#library-tracks').evaluate(el => el.scrollTop)).toBe(0);
        await expect(page.locator('.library-track[data-selected="true"]')).toHaveAttribute('data-job-id', 'job-0');
        await page.evaluate(() => refreshLibrary());
        expect(await page.locator('#library-tracks').evaluate(el => el.scrollTop)).toBe(0);
      }
      await scrolledSelection();
      await page.evaluate(() => setWorkspaceView('history'));
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await expect.poll(() => page.locator('#library-tracks').evaluate(el => el.scrollTop)).toBe(0);
      await page.evaluate(() => {selectLibraryTrack('job-50'); libraryTracks.scrollTop = 0;});
      await page.setViewportSize({width, height: 850});
      await page.waitForTimeout(100);
      expect(await page.locator('#library-tracks').evaluate(el => el.scrollTop)).toBe(0);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: playlist changes, repeated navigation, history return and resize do not jump to old selection`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
