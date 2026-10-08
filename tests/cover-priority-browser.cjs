const {chromium, firefox, expect} = require('@playwright/test');

function audio() {
  const data = Buffer.alloc(44 + 44100 * 4);
  data.write('RIFF'); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(44100, 24); data.writeUInt32LE(88200, 28);
  data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
  data.write('data', 36); data.writeUInt32LE(data.length - 44, 40);
  return data;
}

(async () => {
  for (const engine of [firefox, chromium]) {
    const browser = await engine.launch();
    try {
      const page = await browser.newPage({viewport: {width: 1440, height: 900}});
      const errors = [], covers = [];
      let holdCovers = true;
      const imageResponse = {contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="green"/></svg>'};
      page.on('pageerror', error => errors.push(error.message));
      const tracks = Array.from({length: 300}, (_, i) => ({job_id: `t${i}`, title: `Track ${i}`, artist: 'Artist',
        provider: 'qobuz', state: 'done', streamable: true, cover_url: `/api/covers/t${i}`}));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path.startsWith('/api/covers/')) {
          covers.push(route);
          if (!holdCovers) return route.fulfill(imageResponse);
          return;
        }
        if (path === '/api/library') return route.fulfill({json: {tracks, playlists: [{id: 'p', name: 'Large playlist', track_ids: tracks.map(t => t.job_id)}], tags: [], imports: [], orders: {}}});
        if (path === '/api/stream-ticket') return route.fulfill({json: {url: '/fixture/audio'}});
        return route.fulfill({json: {active: {}, counts: {}, recent: [], history: [], threads: []}});
      });
      await page.route('**/fixture/audio', route => route.fulfill({contentType: 'audio/wav', body: audio()}));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer); });
      await page.locator('[data-playlist-id="p"]').click();
      await expect.poll(() => page.evaluate(() => activeCoverRequests)).toBe(2);
      await page.waitForTimeout(250);
      expect(covers.length).toBeLessThanOrEqual(6);
      await page.locator('.library-track .play-button').first().click();
      await expect.poll(() => page.evaluate(() => !playerAudio.paused && playerAudio.currentTime > 0)).toBe(true);
      expect(await page.evaluate(() => activeCoverRequests)).toBeLessThanOrEqual(2);
      expect(covers.length).toBeLessThanOrEqual(6);
      holdCovers = false;
      for (const cover of [...covers]) await cover.fulfill(imageResponse).catch(() => {});
      await expect.poll(() => page.locator('#player-cover img').evaluate(img => img.naturalWidth)).toBe(32);
      await page.evaluate(() => showAuth());
      await expect.poll(() => page.evaluate(() => activeCoverRequests)).toBe(0);
      expect(await page.evaluate(() => coverRequests.size + coverUrlCache.size + observedCovers.size)).toBe(0);
      expect(errors).toEqual([]);
      console.log(`${engine.name()}: 300 tracks, only two blocked covers, playback starts, shared player cover, logout cancels requests`);
    } finally {await browser.close();}
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
