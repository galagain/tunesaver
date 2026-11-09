const { chromium, expect } = require('@playwright/test');
const fs = require('fs');

function wav() {
  const rate = 8000, frames = rate * 120, body = Buffer.alloc(44 + frames * 2);
  body.write('RIFF'); body.writeUInt32LE(body.length - 8, 4); body.write('WAVEfmt ', 8);
  body.writeUInt32LE(16, 16); body.writeUInt16LE(1, 20); body.writeUInt16LE(1, 22);
  body.writeUInt32LE(rate, 24); body.writeUInt32LE(rate * 2, 28);
  body.writeUInt16LE(2, 32); body.writeUInt16LE(16, 34); body.write('data', 36); body.writeUInt32LE(frames * 2, 40);
  return body;
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 1024, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const titles = ['The Light', 'Knights of the Jaguar', 'Heartbreak (Make Me A Dancer)', 'The Man With The Red Face', 'Music Sounds Better With You', 'I Want Your Soul', 'At Night', 'Our Happiness', 'So Flute', 'Passing By', 'Spanish Fly', 'Come Into My Room'];
      const artists = ['Michelle Weeks', 'DJ Rolando', 'Freemasons, Sophie Ellis-Bextor', 'Laurent Garnier'];
      const tracks = titles.map((title, i) => ({ job_id: `song-${i}`, title, artist: artists[i % 4], provider: ['tiddl', 'beatport', 'qobuz'][i % 3],
        state: i === 10 ? 'failed' : 'done', expired: i === 11, streamable: i < 10, download_ready: i < 10,
        cover_url: `/fixture/cover-${i % 4}`, mix_analysis: { bpm: 124 + i, camelot: ['8A', '9A', '10B', '4A'][i % 4], duration: [541, 397, 561, 550][i % 4] } }));
      const library = { tracks, playlists: [{ id: 'p', name: 'House essentials', track_ids: tracks.map(t => t.job_id) }, { id: 'empty', name: 'Nouvelle sélection', track_ids: [] }], imports: [], tags: [], orders: {} };
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/stream-ticket') return route.fulfill({ json: { url: '/fixture/audio' } });
        return route.fulfill({ json: { history: [], threads: [], recent: [], active: {}, counts: {} } });
      });
      await page.route('**/fixture/cover-*', route => {
        const index = new URL(route.request().url()).pathname.slice(-1);
        const file = `/work/design-cover-${index}.jpg`;
        const fallback = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
        return route.fulfill({ contentType: fs.existsSync(file) ? 'image/jpeg' : 'image/png', body: fs.existsSync(file) ? fs.readFileSync(file) : fallback });
      });
      await page.route('**/fixture/audio', route => {
        const body = wav(), range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Number(range[2]) : body.length - 1;
        return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav', body: body.subarray(start, end + 1),
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${body.length}` } : {}) } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8096');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'studio@example.com' } }; showApp(); clearInterval(queueTimer); });
      await page.locator('[data-playlist-id="p"]').click();
      await expect(page.locator('#library-count')).toHaveText('12 titres · 1 h 42 min');
      await expect(page.locator('#collection-artwork img')).toHaveCount(4);
      await page.waitForFunction(() => [...document.querySelectorAll('#collection-artwork img')].every(img => img.complete && img.naturalWidth));
      await page.evaluate(() => window.firstArtwork = document.querySelector('#collection-artwork img'));
      await page.locator('#library-search').fill('Knights');
      await expect(page.locator('#library-count')).toHaveText('1 sur 12 titres · 1 h 42 min');
      expect(await page.evaluate(() => firstArtwork === document.querySelector('#collection-artwork img'))).toBe(true);
      await page.locator('#library-search').clear();
      const before = await page.locator('#playback-dock').boundingBox();
      await page.locator('#collection-play').click();
      await expect(page.locator('#player-title')).toHaveText('The Light');
      await expect.poll(() => page.evaluate(() => !playerAudio.paused)).toBe(true);
      await expect(page.locator('.library-track[data-playing="true"]')).toHaveCount(1);
      await page.locator('#player-toggle').click();
      await expect.poll(() => page.evaluate(() => playerAudio.paused)).toBe(true);
      await expect(page.locator('.library-track[data-playing="true"]')).toHaveCount(0);
      await page.locator('#player-toggle').click();
      await expect.poll(() => page.evaluate(() => !playerAudio.paused)).toBe(true);
      await page.locator('#player-seek').evaluate(el => { el.value = '30'; el.dispatchEvent(new Event('input')); });
      await expect.poll(() => page.evaluate(() => playerAudio.currentTime)).toBeGreaterThanOrEqual(30);
      await page.locator('#player-mute').click();
      await expect.poll(() => page.evaluate(() => playerAudio.muted)).toBe(true);
      await page.locator('#player-mute').click();
      await expect.poll(() => page.evaluate(() => playerAudio.muted)).toBe(false);
      await page.locator('#player-volume').focus(); await page.keyboard.press('Home');
      await expect.poll(() => page.evaluate(() => playerAudio.volume)).toBe(0);
      await page.locator('#player-mute').click();
      await expect.poll(() => page.evaluate(() => playerAudio.volume > 0 && !playerAudio.muted)).toBe(true);
      expect(await page.locator('#playback-dock').boundingBox()).toEqual(before);
      if (width > 1050) {
        const offsets = await page.evaluate(() => ['track-bpm', 'track-key', 'track-duration'].map(name => {
          const header = document.querySelector(`.library-table-head > .${name}`).getBoundingClientRect();
          const cell = document.querySelector(`.library-track > .${name}`).getBoundingClientRect();
          return Math.abs((header.x + header.width / 2) - (cell.x + cell.width / 2));
        }));
        expect(offsets.every(offset => offset < 1)).toBe(true);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/library-design-${width}.png` });
      await page.locator('[data-playlist-id="empty"]').click();
      await expect(page.locator('#collection-play')).toBeDisabled();
      await expect(page.locator('#library-count')).toHaveText('0 titre');
      await expect(page.locator('#collection-artwork img')).toHaveCount(0);
      await page.locator('[data-collection=""]').click();
      await expect(page.locator('#collection-play')).toBeHidden();
      await expect(page.locator('.library-track')).toHaveCount(12);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: artwork, totals, filters, playback controls, alignment and empty states OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
