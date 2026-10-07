const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const requested = [], errors = [];
      const tracks = ['a', 'b', 'c', 'x'].map(job_id => ({job_id, title: job_id, artist: 'Artist', provider: 'tiddl', state: 'done', streamable: true}));
      const library = {tracks, playlists: [{id: 'first', name: 'First', track_ids: ['a', 'b', 'c']},
        {id: 'second', name: 'Second', track_ids: ['a', 'x']}], tags: [], imports: [], orders: {}};
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/stream-ticket') {
          requested.push(route.request().postDataJSON().job_id);
          return route.fulfill({json: {url: '/fixture/audio'}});
        }
        return route.fulfill({json: {active: {}, counts: {}, recent: [], history: [], threads: []}});
      });
      await page.route('**/fixture/audio', route => route.fulfill({status: 204}));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {
        playerAudio.play = async () => {};
        session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);
      });
      await page.locator('[data-playlist-id="first"]').click();
      await page.locator('#library-search').fill('a');
      await page.locator('.library-track .play-button').first().click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('a');
      await page.locator('[data-playlist-id="second"]').click();
      await page.evaluate(() => playerAudio.dispatchEvent(new Event('ended')));
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('b');
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await page.locator('[data-track-sort="title"]').click();
      await page.locator('#player-next').click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('c');
      await page.evaluate(() => playNextTrack());
      expect(requested).toEqual(['a', 'b', 'c']);
      await page.locator('#player-previous').click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('b');
      await page.locator('[data-playlist-id="second"]').click();
      await page.locator('.library-track .play-button').first().click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('a');
      await page.locator('#player-next').click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('x');
      await page.evaluate(async () => {
        selectedPlaylistId = 'first'; await streamTrack(libraryState.tracks[0]);
        libraryState.playlists[0].track_ids = ['a', 'c'];
        await playNextTrack();
      });
      expect(await page.evaluate(() => currentTrackId)).toBe('c');
      await page.evaluate(() => resetPlayer());
      expect(await page.evaluate(() => playbackQueue.length)).toBe(0);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: playlist-bound queue survives filters/navigation/sorting, stops at end and skips removed tracks OK`);
    }
  } finally {await browser.close();}
})().catch(e => {console.error(e); process.exitCode = 1;});
