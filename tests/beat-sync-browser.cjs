const { chromium, firefox, expect } = require('@playwright/test');
const fs = require('node:fs');

function audioFixture(bpm) {
  const rate = 22050, frames = rate * 60, buffer = Buffer.alloc(44 + frames * 2);
  buffer.write('RIFF'); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36); buffer.writeUInt32LE(frames * 2, 40);
  for (let i = 0; i < frames; i++) {
    const time = i / rate, pulse = Math.exp(-(time % (60 / bpm)) * 35);
    buffer.writeInt16LE(Math.round(Math.sin(time * Math.PI * 880) * (.05 + .3 * pulse) * 32767), 44 + i * 2);
  }
  return buffer;
}

(async () => {
  const isFirefox = process.env.MIXER_BROWSER === 'firefox', engine = isFirefox ? firefox : chromium;
  const folder = fs.readdirSync('/ms-playwright').find(n => n.startsWith(isFirefox ? 'firefox-' : 'chromium-'));
  const browser = await engine.launch({ executablePath: `/ms-playwright/${folder}/${isFirefox ? 'firefox/firefox' : 'chrome-linux64/chrome'}`,
    ...(isFirefox ? { firefoxUserPrefs: { 'media.autoplay.default': 0 } } : { args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } }), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const Original = window.AudioContext;
      window.__media = [];
      window.AudioContext = class extends Original {
        createMediaElementSource(media) { __media.push(media); return super.createMediaElementSource(media); }
      };
      window.__port = { name: 'DDJ-FLX4', id: 'flx4', state: 'connected', type: 'input', connection: 'closed', async open() { this.connection = 'open'; }, async close() { this.connection = 'closed'; } };
      Object.defineProperty(navigator, 'requestMIDIAccess', { configurable: true, value: async () => ({ inputs: new Map([['flx4', __port]]), outputs: new Map() }) });
      window.__send = data => __port.onmidimessage?.({ data });
    });
    const audio = [audioFixture(127.3), audioFixture(128.7)];
    await page.route('**/fixture/audio*', route => {
      const body = audio[Number(new URL(route.request().url()).searchParams.get('deck'))];
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
      const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]), body.length - 1) : body.length - 1;
      return route.fulfill({ status: range ? 206 : 200, body: body.subarray(start, end + 1), contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${body.length}` } : {}) } });
    });
    await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
    await page.waitForFunction(() => window.Mixer && publicConfig !== null);
    await page.evaluate(async () => {
      session = { access_token: 'fixture' };
      document.querySelector('#auth-view').hidden = true; document.querySelector('#app-view').hidden = false;
      libraryState = { folders: [], playlists: [], tags: [], tracks: [127.3, 128.7].map((bpm, i) => ({ job_id: String(i), title: `Sync ${i ? 'Master' : 'Follower'}`, artist: 'Studio Session', streamable: true, mix_analysis: { bpm, duration: 60 } })) };
      TuneSaverMixerHost.requestJson = async (path, options) => {
        if (path === '/api/stream-ticket') return { url: `/fixture/audio?deck=${JSON.parse(options.body).job_id}` };
        return { status: 'done', settings: {}, analysis: { bpm: path.endsWith('/0') ? 127.3 : 128.7, grid_offset: 0, duration: 60 } };
      };
      renderLibrary();
      await Mixer.loadTrack(libraryState.tracks[0], 0);
      await Mixer.loadTrack(libraryState.tracks[1], 1);
    });
    const a = page.locator('.mix-deck[data-deck="0"]'), b = page.locator('.mix-deck[data-deck="1"]');
    const syncA = a.locator('[data-action="matchTempo"]'), syncB = b.locator('[data-action="matchTempo"]');
    await expect(syncA).toBeEnabled();
    await expect(syncA).not.toHaveAttribute('aria-pressed');
    await page.evaluate(() => { __media[0].currentTime = 4.12; __media[1].currentTime = 6.28; });
    await page.waitForFunction(() => __media.every(media => !media.seeking));
    await syncA.click();
    await expect.poll(() => page.evaluate(() => __media[0].playbackRate)).toBeCloseTo(128.7 / 127.3, 6);
    const phase = await page.evaluate(() => (__media[1].currentTime * 128.7 - __media[0].currentTime * 127.3) / 60);
    expect(Math.abs(phase - Math.round(phase))).toBeLessThan(.003);
    await b.locator('[data-action="play"]').click();
    await a.locator('[data-action="play"]').click();
    await page.waitForFunction(() => __media.every(media => !media.paused && !media.seeking));
    await page.evaluate(() => {
      window.__seeks = 0; __media[0].addEventListener('seeking', () => __seeks++);
      window.__rates = [];
      window.__sample = setInterval(() => __rates.push(__media[0].playbackRate), 20);
    });
    await page.evaluate(() => { __media[0].currentTime += .06; });
    await page.waitForFunction(() => !__media[0].seeking);
    const seeks = await page.evaluate(() => __seeks);
    await b.locator('[data-control="tempo"]').evaluate(input => { input.value = '4.37'; input.dispatchEvent(new Event('input')); });
    await page.waitForTimeout(1500);
    expect(await page.evaluate(() => __rates.every(rate => Math.abs(rate - 128.7 / 127.3) < 1e-6))).toBe(true);
    expect(await page.evaluate(() => __seeks)).toBe(seeks);
    await expect(a.locator('.mix-current-bpm')).toHaveText('128.70');
    await a.locator('[data-action="play"]').click();
    const pausedAt = await page.evaluate(() => __media[0].currentTime);
    await page.waitForTimeout(150);
    expect(await page.evaluate(() => __media[0].currentTime)).toBe(pausedAt);
    await a.locator('[data-action="play"]').click();
    await page.waitForTimeout(150);
    expect(await page.evaluate(() => __seeks)).toBe(seeks);
    await syncA.click();
    await expect.poll(() => page.evaluate(() => __media[0].playbackRate)).toBeCloseTo(128.7 * 1.0437 / 127.3, 6);
    await syncB.click();
    await b.locator('[data-control="tempo"]').evaluate(input => { input.value = '2'; input.dispatchEvent(new Event('input')); });
    await page.locator('#mix-connect').click();
    await page.locator('#mix-midi-connect').click();
    await expect(page.locator('#mix-midi-status')).toContainText('connecté');
    await page.evaluate(() => __send([0x90, 88, 127]));
    const nominal = 128.7 * 1.02 / 127.3;
    await expect.poll(() => page.evaluate(() => __media[0].playbackRate)).toBeCloseTo(nominal, 6);
    await page.evaluate(() => { __rates.length = 0; __send([0xb0, 0x21, 74]); });
    await page.waitForTimeout(80);
    expect(await page.evaluate(rate => __rates.some(value => Math.abs(value - rate - .04) < 1e-6), nominal)).toBe(true);
    await expect.poll(() => page.evaluate(() => __media[0].playbackRate)).toBeCloseTo(nominal, 6);
    // Resetting a controller while holding a playing deck must release it, not strand it paused.
    await page.evaluate(() => __send([0x90, 0x36, 127]));
    expect(await page.evaluate(() => __media[0].paused)).toBe(true);
    await page.locator('#mix-midi-refresh').click();
    await page.waitForFunction(() => !__media[0].paused && __port.onmidimessage);
    await page.locator('#mix-connect').click();
    await page.evaluate(() => __send([0x90, 0x36, 127]));
    await page.locator('#mixer-nav').click();
    await page.waitForFunction(() => !__media[0].paused);
    await page.locator('#mixer-nav').click();
    await page.evaluate(() => __send([0x91, 0x36, 127]));
    await page.evaluate(() => Mixer.removeTrack('1'));
    await expect(syncA).toBeDisabled();
    await page.evaluate(() => __send([0x91, 0x36, 0]));
    expect(await page.evaluate(() => __media[1].paused)).toBe(true);
    await page.evaluate(() => __send([0x90, 0x36, 127]));
    await page.evaluate(() => { clearInterval(__sample); showAuth(); });
    expect(await page.evaluate(() => __media.every(media => media.paused))).toBe(true);
    expect(errors).toEqual([]);
    console.log(`${engine.name()}: one-shot sync, no automatic correction, live rim bend, held jog release on MIDI reset/mode change and eject OK`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
