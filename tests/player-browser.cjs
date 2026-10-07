const { chromium, firefox, expect } = require('@playwright/test');

function audioFixture() {
  const rate = 44100, frames = rate * 8, data = Buffer.alloc(44 + frames * 2);
  data.write('RIFF'); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34);
  data.write('data', 36); data.writeUInt32LE(frames * 2, 40);
  for (let i = 0; i < frames; i++) data.writeInt16LE(Math.round(4000 * Math.sin(i * 2 * Math.PI * 440 / rate)), 44 + i * 2);
  return data;
}

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [], requests = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => requests.push(new URL(request.url()).pathname));
      const tracks = Array.from({ length: 35 }, (_, i) => ({
        job_id: 'track-' + i, title: i === 0 ? 'First light' : 'A very long track title with a guest artist',
        artist: 'Studio Sessions', provider: 'qobuz', state: 'done', streamable: true, tags: [],
        cover_url: '/fixture/cover.png',
      }));
      let slowTicket;
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/feedback') return route.fulfill({json: {threads: []}});
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/library') return route.fulfill({ json: { tracks, playlists: [], folders: [], tags: [], imports: [] } });
        if (path === '/api/stream-ticket') {
          const id = route.request().postDataJSON().job_id;
          if (id === 'slow') { slowTicket = route; return; }
          return route.fulfill({ json: { url: '/fixture/audio?id=' + id } });
        }
        errors.push(path); return route.fulfill({ status: 500 });
      });
      await page.route('**/fixture/cover.png', route => route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#269d87"/></svg>',
      }));
      await page.route('**/fixture/audio*', route => {
        const body = audioFixture(), range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Number(range[2]) : body.length - 1;
        return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav', body: body.subarray(start, end + 1),
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${body.length}` } : {}) } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => publicConfig !== null);
      await expect(page.locator('#playback-dock')).toBeHidden();
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'studio@example.com' } }; showApp(); });
      await expect(page.locator('.library-track')).toHaveCount(35);
      await expect(page.locator('#mixer-nav, #mix-connect, .mix-deck')).toHaveCount(0);
      const dock = await page.locator('#playback-dock').boundingBox();
      await page.locator('.library-track .play-button').first().click();
      await expect(page.locator('#player-title')).toHaveText('First light');
      await expect.poll(() => page.locator('#player-audio').evaluate(audio => !audio.paused && audio.currentTime > 0)).toBe(true);
      await expect.poll(() => page.locator('#player-cover img').evaluate(image => image.naturalWidth)).toBeGreaterThan(0);
      expect(await page.locator('#playback-dock').boundingBox()).toEqual(dock);
      await page.locator('#player-next').click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('track-1');
      await page.locator('#player-previous').click();
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('track-0');
      await page.locator('#player-audio').evaluate(audio => { audio.currentTime = audio.duration - .15; });
      await expect.poll(() => page.evaluate(() => currentTrackId)).toBe('track-1');
      await page.locator('#player-audio').evaluate(audio => audio.pause());
      await expect.poll(() => page.locator('#player-audio').evaluate(audio => audio.paused)).toBe(true);
      await page.evaluate(() => { const rows = document.querySelectorAll('.library-track'); rows[rows.length - 1].scrollIntoView({ block: 'end' }); });
      expect(await page.locator('.library-track').last().evaluate(row => row.getBoundingClientRect().bottom <= document.querySelector('#playback-dock').getBoundingClientRect().top)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/simple-player-${engine.name()}-${width}.png`, fullPage: true });
      await page.evaluate(() => { window.pendingPlayback = streamTrack({ job_id: 'slow' }); });
      await expect.poll(() => Boolean(slowTicket)).toBe(true);
      await page.evaluate(() => resetPlayer());
      await slowTicket.fulfill({ json: { url: '/fixture/audio?id=slow' } });
      await page.evaluate(() => pendingPlayback);
      await expect(page.locator('#player-audio')).not.toHaveAttribute('src');
      await page.evaluate(() => streamTrack(libraryState.tracks[0]));
      await page.evaluate(() => showAuth());
      await expect(page.locator('#playback-dock')).toBeHidden();
      await expect(page.locator('#player-audio')).not.toHaveAttribute('src');
      expect(requests.filter(path => /mixer|mix-clock|Signalsmith|flx4|wavesurfer/.test(path) && path !== '/vendor/mixer-icons.js')).toEqual([]);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: playback, next/previous, covers, cancellation, logout and layout passed`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
