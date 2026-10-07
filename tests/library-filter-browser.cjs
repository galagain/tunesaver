const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const errors = [];
      const library = {tracks: [
        {job_id: 'one', title: 'First song', artist: 'Artist', provider: 'tiddl', state: 'done'},
        {job_id: 'two', title: 'Second song', artist: 'Artist', provider: 'tiddl', state: 'done'}
      ], playlists: [{id: 'a', name: 'Playlist A', track_ids: ['one']}, {id: 'b', name: 'Playlist B', track_ids: ['two']}], imports: [], tags: [], folders: [], orders: {}};
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/feedback') return route.fulfill({json: {threads: []}});
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        if (path === '/api/history') return route.fulfill({json: {history: []}});
        errors.push(path); return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await page.locator('[data-playlist-id="a"]').click();
      const filter = page.locator('#library-search');
      await filter.fill('First');
      await page.evaluate(() => refreshLibrary());
      await expect(filter).toHaveValue('First');
      await page.locator('[data-track-sort="title"]').click();
      await expect(filter).toHaveValue('First');
      await page.locator('[data-playlist-id="b"]').click();
      await expect(filter).toHaveValue('');
      await expect(page.locator('.library-track')).toContainText('Second song');
      await filter.fill('Missing');
      await expect(page.locator('.library-track')).toHaveCount(0);
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await expect(filter).toHaveValue('');
      await expect(page.locator('.library-track')).toHaveCount(2);
      await page.locator('[data-playlist-id="a"]').click();
      await expect(filter).toHaveValue('');
      await expect(page.locator('.library-track')).toContainText('First song');
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: filter resets across playlists/library, stays during refresh/sort OK`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
