const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [320, 390, 700, 701, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && window.TuneSaverPlayer && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = { tracks: [{ job_id: 'one', title: 'A long track title with an extended original mix',
          artist: 'An artist with a long name', streamable: true }], playlists: [], tags: [] };
        renderLibrary(); resetPlayer();
        TuneSaverMixerHost.requestJson = () => new Promise(resolve => { window.finishAnalysis = resolve; });
      });
      const dock = page.locator('#playback-dock');
      const empty = await dock.boundingBox();
      const stable = async () => expect(await dock.boundingBox()).toEqual(empty);
      await page.evaluate(() => setPlayerTrack(libraryState.tracks[0]));
      await stable();
      const pendingWave = await page.locator('#player-waveform').boundingBox();
      await page.evaluate(() => {
        const frames = 100, bytes = new Uint8Array(frames * 5);
        for (let i = 0; i < frames; i++) bytes.set([200, 30, 10, 40 + i % 80, 50], i * 5);
        finishAnalysis({ status: 'done', analysis: { duration: 120, bpm: 128, key: 'Am', camelot: '8A',
          codec: 'pcm_s24le', sample_rate: 192000, bit_rate: 9216000,
          waveform: { encoding: 'u8-lmhpn', rate: frames / 120, frames, data: btoa(String.fromCharCode(...bytes)) } } });
      });
      await expect(page.locator('#player-metadata')).toContainText('128.00 BPM');
      await stable();
      expect(await page.locator('#player-waveform').boundingBox()).toEqual(pendingWave);
      const wave = pendingWave;
      const center = wave.y + wave.height / 2;
      for (const selector of ['#player-toggle', '#player-mute', ...(width > 700 ? ['#player-cover', '.player-info', '#mixer-nav'] : [])]) {
        const box = await page.locator(selector).boundingBox();
        expect(Math.abs(box.y + box.height / 2 - center), selector).toBeLessThan(1);
        expect(box.x + box.width, selector).toBeLessThanOrEqual(width);
      }
      const times = await page.locator('.player-time').boundingBox();
      expect(times.y).toBeGreaterThanOrEqual(wave.y + wave.height);
      expect(times.y + times.height).toBeLessThanOrEqual(empty.y + empty.height);
      expect(await page.locator('#player-waveform').evaluate(canvas => {
        const { width, height } = canvas, data = canvas.getContext('2d').getImageData(0, 0, width, height).data;
        let transparent = 0, colored = 0;
        for (let i = 0; i < data.length; i += 4) {
          if (!data[i + 3]) transparent++;
          else if (data[i] !== data[i + 1] || data[i + 1] !== data[i + 2]) colored++;
        }
        return getComputedStyle(canvas).backgroundColor === 'rgba(0, 0, 0, 0)'
          && transparent > width * height * .2 && colored > width;
      })).toBe(true);
      await page.evaluate(() => {
        const media = document.querySelector('#audio-player');
        media.currentTime = 60;
        media.dispatchEvent(new Event('timeupdate'));
      });
      // The previous playhead must be erased, including its transparent surroundings.
      expect(await page.locator('#player-waveform').evaluate(canvas =>
        canvas.getContext('2d').getImageData(0, 0, 1, 1).data[3])).toBe(0);
      await stable();
      await page.screenshot({ path: `/work/player-layout-${engine.name()}-${width}.png` });
      await page.evaluate(() => {
        setPlayerTrack(libraryState.tracks[0]);
        finishAnalysis({ status: 'failed' });
      });
      await expect(page.locator('#player-retry')).toBeVisible();
      await stable();
      expect(await page.locator('#player-waveform').boundingBox()).toEqual(pendingWave);
      const retry = await page.locator('#player-retry').boundingBox();
      const duration = await page.locator('#player-duration').boundingBox();
      expect(retry.x + retry.width).toBeLessThanOrEqual(duration.x);
      expect(duration.x + duration.width).toBeLessThanOrEqual(width);
      await page.evaluate(() => resetPlayer());
      await stable();
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: stable dock, centered transparent waveform, cleared playhead and error layout OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
