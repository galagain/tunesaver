const { chromium, firefox, expect } = require('@playwright/test');

function audioFixture() {
  const rate = 44100, frames = rate * 12, buffer = Buffer.alloc(44 + frames * 2);
  buffer.write('RIFF'); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36); buffer.writeUInt32LE(frames * 2, 40);
  for (let i = 0; i < frames; i++) buffer.writeInt16LE(Math.round(Math.sin(i / rate * Math.PI * 440) * 8000), 44 + i * 2);
  return buffer;
}

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [320, 390, 701, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.sources = []; window.mediaEvents = [];
        const Original = AudioContext;
        window.AudioContext = class extends Original {
          constructor(options) { super(options); window.audioContext = this; }
          createMediaElementSource(media) {
            const node = super.createMediaElementSource(media); sources.push(media);
            for (const name of ['play', 'pause', 'seeking', 'emptied', 'loadstart']) media.addEventListener(name, () => mediaEvents.push(name));
            return node;
          }
        };
      });
      await page.route('**/fixture/audio**', route => route.fulfill({ body: audioFixture(), contentType: 'audio/wav' }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = { tracks: ['one', 'two'].map(id => ({ job_id: id, title: `${id} - A long original mix title`,
          artist: 'Studio Session and another artist', streamable: true })), playlists: [], tags: [] };
        const frames = 441 * 12, bytes = new Uint8Array(frames * 5);
        for (let i = 0; i < frames; i++) { const p = Math.round(40 + 180 * Math.exp(-(i % 220) / 28)); bytes.set([p, 30, 10, p, p], i * 5); }
        window.analysis = bpm => ({ status: 'done', analysis: { duration: 12, bpm, key: 'Am', camelot: '8A',
          codec: 'flac', sample_rate: 192000, bit_rate: 9216000,
          waveform: { encoding: 'u8-lmhpn', rate: 441, frames, data: btoa(String.fromCharCode(...bytes)) } } });
        window.tickets = 0;
        window.fixtureRequest = async (path, options) => {
          if (path === '/api/stream-ticket') { tickets++; return { url: `/fixture/audio?id=${JSON.parse(options.body).job_id}` }; }
          return analysis(path.endsWith('one') ? 120 : 128);
        };
        TuneSaverMixerHost.requestJson = fixtureRequest;
        renderLibrary();
      });
      const a = page.locator('.mix-deck[data-deck="0"]'), b = page.locator('.mix-deck[data-deck="1"]');
      const dock = page.locator('#playback-dock');
      const initial = await a.boundingBox(), emptyDock = await dock.boundingBox();
      const overviewA = page.locator('.mix-overview[data-deck="0"]'), overviewB = page.locator('.mix-overview[data-deck="1"]');
      const overviewRow = await page.locator('#mix-overviews').boundingBox();
      const left = await overviewA.boundingBox(), right = await overviewB.boundingBox();
      expect(Math.abs(left.width - overviewRow.width / 2)).toBeLessThan(1);
      expect(Math.abs(right.width - overviewRow.width / 2)).toBeLessThan(1);
      expect(left.x + left.width).toBe(right.x);
      expect(left.y).toBe(right.y);
      expect(overviewRow.y + overviewRow.height).toBe(initial.y);
      await expect(page.locator('.mix-deck .mix-overview')).toHaveCount(0);
      expect(initial.height).toBe(width <= 700 ? 96 : 72);
      await page.locator('.library-track').first().locator('.play-button').click();
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await expect(a.locator('.deck-metadata')).toContainText('120.00');
      expect(await a.boundingBox()).toEqual(initial);
      expect(await dock.boundingBox()).toEqual(emptyDock);
      const canvas = a.locator('.mix-timeline-lane canvas'), canvasBox = await canvas.boundingBox();
      const play = await a.locator('[data-action="play"]').boundingBox();
      expect(Math.abs(canvasBox.y + canvasBox.height / 2 - play.y - play.height / 2)).toBeLessThan(1);
      await expect.poll(() => canvas.evaluate(c => {
        const data = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        return data.filter((value, i) => i % 4 === 0 && value > data[i + 1] + 30).length;
      })).toBeGreaterThan(50);
      expect(await canvas.evaluate(c => c.getContext('2d').getImageData(c.width - 1, 0, 1, 1).data[3])).toBe(0);
      await page.evaluate(() => { window.keptRow = document.querySelector('.mix-deck[data-deck="0"]'); window.keptCanvas = keptRow.querySelector('canvas'); mediaEvents.length = 0; });
      const before = await page.evaluate(() => Mixer.currentPlayback.position);
      for (let i = 0; i < 4; i++) {
        await page.locator('#mixer-nav').click();
        await expect(b).toBeVisible();
        expect(await a.boundingBox()).toEqual(initial);
        expect(await canvas.boundingBox()).toEqual(canvasBox);
        const second = await b.boundingBox();
        expect(second.y + second.height).toBe(initial.y);
        const upper = await page.locator('#mix-overviews').boundingBox();
        expect(upper.y + upper.height).toBe(second.y);
        expect(upper.width).toBe(overviewRow.width);
        await page.locator('#mixer-nav').click();
        await expect(b).toBeHidden();
        expect(await a.boundingBox()).toEqual(initial);
      }
      expect(await page.evaluate(() => ({ events: mediaEvents, sources: sources.length, tickets,
        same: keptRow === document.querySelector('.mix-deck[data-deck="0"]') && keptCanvas === keptRow.querySelector('canvas') })))
        .toEqual({ events: [], sources: 1, tickets: 1, same: true });
      expect(await page.evaluate(() => Mixer.currentPlayback.position)).toBeGreaterThan(before);
      await page.screenshot({ path: `/work/unified-player-${engine.name()}-${width}.png` });
      await a.locator('[data-action="play"]').click();
      const overview = page.locator('.mix-overview[data-deck="0"] .mix-wave');
      const box = await overview.boundingBox();
      await overview.click({ position: { x: box.width / 2, y: 16 } });
      await expect.poll(() => page.evaluate(() => Mixer.currentPlayback.position)).toBeCloseTo(6, 0);
      await page.waitForFunction(() => !sources[0].seeking);
      const pausedPosition = await page.evaluate(() => sources[0].currentTime);
      await expect(a.locator('[data-action="previous"]')).toBeEnabled();
      await page.evaluate(() => { sources[0].playbackRate = 1.04; mediaEvents.length = 0; });
      await page.locator('#mixer-nav').click(); await page.locator('#mixer-nav').click();
      expect(await page.evaluate(() => ({ events: mediaEvents, position: sources[0].currentTime, paused: sources[0].paused, rate: sources[0].playbackRate })))
        .toEqual({ events: [], position: pausedPosition, paused: true, rate: 1.04 });
      await page.evaluate(() => { sources[0].playbackRate = 1; });
      await page.evaluate(() => Mixer.loadTrack(libraryState.tracks[1], 1));
      await expect(overviewB.locator('.mix-overview-label')).toContainText('two -');
      await overviewB.press('ArrowRight');
      await expect.poll(() => page.evaluate(() => sources[1].currentTime)).toBe(5);
      expect(await page.evaluate(() => sources[0].currentTime)).toBe(pausedPosition);
      await overviewB.press('Home');
      await page.waitForFunction(() => !sources[1].seeking && sources[1].currentTime === 0);
      await b.locator('[data-action="play"]').click();
      await a.locator('[data-action="play"]').click();
      await page.waitForFunction(() => sources[0].currentTime > 6.1 && sources[1].currentTime > .1);
      await page.evaluate(() => { mediaEvents.length = 0; });
      await page.locator('#mixer-nav').click();
      expect(await page.evaluate(() => ({ events: mediaEvents, playing: sources.map(m => !m.paused) })))
        .toEqual({ events: [], playing: [true, true] });
      await page.locator('#mixer-nav').click();
      await page.screenshot({ path: `/work/unified-mix-${engine.name()}-${width}.png` });
      await page.evaluate(() => Mixer.pause());
      await page.locator('#mixer-nav').click();
      await page.evaluate(() => { sources[0].currentTime = 11.95; });
      await a.locator('[data-action="play"]').click();
      await expect(a.locator('.mix-track-info strong')).toContainText('two -');
      await expect(a.locator('.mix-current-bpm')).toHaveText('128.00');
      await expect(a.locator('[data-action="next"]')).toBeDisabled();
      await a.locator('[data-action="previous"]').click();
      await expect(a.locator('.mix-track-info strong')).toContainText('one -');
      expect(await a.boundingBox()).toEqual(initial);
      await page.evaluate(() => Mixer.pause());
      await page.evaluate(() => {
        libraryState.tracks.push(...Array.from({ length: 30 }, (_, i) => ({ job_id: `extra-${i}`, title: `Extra ${i}`, streamable: true })));
        renderLibrary(); window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
      });
      const bottomScroll = await page.evaluate(() => scrollY);
      const lastRow = await page.locator('.library-track').last().boundingBox();
      await page.locator('#mixer-nav').click(); await page.locator('#mixer-nav').click();
      expect(await page.evaluate(() => scrollY)).toBe(bottomScroll);
      expect(await page.locator('.library-track').last().boundingBox()).toEqual(lastRow);
      await page.evaluate(() => { libraryState.tracks = libraryState.tracks.slice(0, 2); renderLibrary(); window.scrollTo({ top: 0, behavior: 'instant' }); });
      // Analysis responses from an older track or a signed-out session cannot repopulate a deck.
      await page.evaluate(() => {
        TuneSaverMixerHost.requestJson = (path, options) => path.endsWith('/one')
          ? new Promise(resolve => { window.finishOld = () => resolve(analysis(90)); }) : fixtureRequest(path, options);
        void Mixer.playTrack(libraryState.tracks[0]);
      });
      await page.waitForFunction(() => window.finishOld);
      await page.evaluate(() => { void Mixer.playTrack(libraryState.tracks[1]); finishOld(); });
      await expect(a.locator('.mix-current-bpm')).toHaveText('128.00');
      await page.evaluate(() => {
        Mixer.pause();
        TuneSaverMixerHost.requestJson = (path, options) => path.endsWith('/one')
          ? new Promise(resolve => { window.finishLate = () => resolve(analysis(999)); }) : fixtureRequest(path, options);
        void Mixer.playTrack(libraryState.tracks[0]);
      });
      await page.waitForFunction(() => window.finishLate);
      await page.evaluate(() => { showAuth(); finishLate(); });
      await expect(dock).toBeHidden();
      await expect.poll(() => page.evaluate(() => sources.every(m => m.paused && !m.hasAttribute('src')))).toBe(true);
      await expect(a.locator('.mix-track-info strong')).toHaveText('Aucun morceau');
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: stable compact rows, shared audio without mode events/reloads, waveforms, next track and lifecycle OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
