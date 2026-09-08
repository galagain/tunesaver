const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');

function audioFixture() {
  const rate = 44100, frames = rate * 12;
  const buffer = Buffer.alloc(44 + frames * 4);
  buffer.write('RIFF'); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(2, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 4, 28); buffer.writeUInt16LE(4, 32); buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36); buffer.writeUInt32LE(frames * 4, 40);
  for (let i = 0; i < frames; i++) {
    const value = Math.round(Math.sin(i / rate * Math.PI * 440) * (0.2 + 0.15 * Math.sin(i / rate * 7)) * 32767);
    buffer.writeInt16LE(value, 44 + i * 4); buffer.writeInt16LE(value, 46 + i * 4);
  }
  return buffer;
}

(async () => {
  const executablePath = process.env.CHROMIUM_PATH || `/ms-playwright/${fs.readdirSync('/ms-playwright').find(n => n.startsWith('chromium-'))}/chrome-linux64/chrome`;
  const browser = await chromium.launch({ executablePath, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(15000);
      page.setDefaultNavigationTimeout(15000);
      const errors = [];
      page.on('pageerror', e => { errors.push(e.message); console.error('Browser:', e.message); });
      page.on('console', msg => { if (msg.type() === 'error' && /Content Security|Refused to|TypeError|SyntaxError/.test(msg.text())) errors.push(msg.text()); });
      await page.addInitScript(() => {
        const Original = window.AudioContext;
        window.__contexts = [];
        window.AudioContext = class extends Original {
          constructor(opts) { super(opts); this.sources = []; this.gains = []; this.analyser = this.createAnalyser(); window.__contexts.push(this); }
          createMediaElementSource(media) { const source = super.createMediaElementSource(media); this.sources.push({ media, source }); return source; }
          createGain() { const gain = super.createGain(); if (!this.gains.length) gain.connect(this.analyser); this.gains.push(gain); return gain; }
        };
        const input = { name: 'DDJ-FLX4', id: 'flx4', type: 'input', state: 'connected', open: async () => {}, close: async () => {} };
        const output = { name: 'DDJ-FLX4', id: 'out', type: 'output', state: 'connected', open: async () => {}, close: async () => {}, send: data => window.__leds.push(data) };
        window.__leds = [];
        window.__midi = { inputs: new Map([['flx4', input]]), outputs: new Map([['out', output]]) };
        window.__sendMIDI = data => input.onmidimessage?.({ data });
        Object.defineProperty(navigator, 'requestMIDIAccess', { value: async () => window.__midi });
      });
      await page.route('**/fixture/audio**', route => route.fulfill({ body: audioFixture(), contentType: 'audio/wav' }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8081');
      console.log(width, 'page loaded');
      await page.waitForFunction(() => window.Mixer && window.TuneSaverMixerHost);
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = {
          folders: [{ id: 'f', name: 'Sets' }], playlists: [{ id: 'p', folder_id: 'f', name: 'Soiree', track_ids: ['one'] }], tags: [{ id: 't', name: 'House', track_ids: ['two'] }],
          tracks: [
            { job_id: 'one', title: 'First light', artist: 'Studio Session', provider: 'qobuz', bpm: 120, streamable: true },
            { job_id: 'two', title: 'Another very long track title to check mobile wrapping', artist: 'Long artist name and guest', provider: 'beatport', bpm: 128, streamable: true },
            { job_id: 'unavailable', title: 'Not ready', streamable: false },
          ],
        };
        window.__mixSettings = {};
        const analysis = bpm => ({ bpm, duration: 12, grid_offset: 0, key: 'Am', camelot: '8A', grid_quality: 'stable', sample_rate: 44100, codec: 'pcm_s16le',
          waveform: { rate: 40, bands: [0, 1, 2].map(b => Array.from({ length: 480 }, (_, i) => Math.round(40 + 160 * Math.abs(Math.sin(i * .1 + b))))) } });
        libraryState.tracks.forEach(t => { if (t.streamable) t.mix_analysis = { status: 'done', ...analysis(t.bpm) }; });
        TuneSaverMixerHost.requestJson = async (path, options) => {
          if (path === '/api/stream-ticket') return { url: `/fixture/audio?id=${JSON.parse(options.body).job_id}` };
          if (!path.startsWith('/api/mixer/tracks/')) throw Error(`Unexpected request ${path}`);
          const id = path.split('/').pop(), settings = __mixSettings[id] ||= {};
          if (options.method === 'PATCH') {
            const patch = JSON.parse(options.body);
            for (const [key, value] of Object.entries(patch)) {
              if (key === 'hotcue') (settings.hotcues ||= Array(8).fill(null))[value.index] = value.time;
              else if (value === null) delete settings[key];
              else settings[key] = value;
            }
          }
          return { status: 'done', analysis: analysis(id === 'two' ? 128 : 120), settings: structuredClone(settings) };
        };
        renderLibrary();
      });
      await page.locator('#mixer-nav').click();
      console.log(width, 'mixer opened');
      await expect(page.locator('#mixer-panel')).toBeVisible();
      await expect(page.locator('#library-content')).toBeHidden();
      await expect(page.locator('.mix-library-row')).toHaveCount(2);
      await page.locator('#mix-search').fill('first');
      await expect(page.locator('.mix-library-row')).toHaveCount(1);
      await page.locator('#mix-search').fill('');
      await expect(page.locator('.mix-library-row')).toHaveCount(2);
      await page.locator('#mix-collection').selectOption('tags:t');
      await expect(page.locator('.mix-library-row')).toHaveCount(1);
      await page.locator('#mix-collection').selectOption('');
      await page.locator('.mix-library-row[data-job-id="one"] button').first().click();
      await page.locator('.mix-library-row[data-job-id="two"] button').last().click();
      await expect(page.locator('.mix-deck-status').first()).toHaveText('Prêt');
      await expect(page.locator('.mix-deck-status').last()).toHaveText('Prêt');
      console.log(width, 'both tracks loaded');
      const a = page.locator('.mix-deck[data-deck="0"]');
      const b = page.locator('.mix-deck[data-deck="1"]');
      await a.locator('[data-action="play"]').click();
      await b.locator('[data-action="play"]').click();
      await page.waitForFunction(() => __contexts[0].sources.every(s => !s.media.paused && s.media.currentTime > 0.3));
      const timelineBefore = await page.locator('#mix-timelines canvas').first().evaluate(c => c.toDataURL());
      await page.waitForTimeout(200);
      const timelineAfter = await page.locator('#mix-timelines canvas').first().evaluate(c => {
        const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let colors = 0, grid = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 30) colors++;
          if (pixels[i] > 100 && pixels[i] === pixels[i + 1] && pixels[i] === pixels[i + 2]) grid++;
        }
        return { image: c.toDataURL(), colors, grid };
      });
      if (timelineBefore === timelineAfter.image || timelineAfter.colors < 100 || timelineAfter.grid < 100) throw Error('Timeline not moving, colored or gridded');
      const rms = await page.evaluate(() => {
        const data = new Float32Array(__contexts[0].analyser.fftSize);
        __contexts[0].analyser.getFloatTimeDomainData(data);
        return Math.sqrt(data.reduce((sum, v) => sum + v * v, 0) / data.length);
      });
      if (rms < 0.005) throw new Error(`Silent master output: ${rms}`);
      await page.locator('#mix-stop').click();
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 1; });
      await a.locator('[data-action="loopIn"]').click();
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2; });
      await a.locator('[data-action="loopOut"]').click();
      await expect(a.locator('[data-action="loop"]')).toHaveAttribute('aria-pressed', 'true');
      await a.locator('[data-action="play"]').click();
      await page.waitForTimeout(1800);
      const loopTime = await page.evaluate(() => __contexts[0].sources[0].media.currentTime);
      if (loopTime < 1 || loopTime > 2.2) throw Error(`Loop did not repeat: ${loopTime}`);
      await a.locator('[data-action="loop"]').click();
      await page.locator('#mix-stop').click();
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 3; });
      await a.locator('[data-action="cue"]').click();
      await a.locator('[data-action="play"]').click();
      await page.waitForTimeout(300);
      await a.locator('[data-action="cue"]').click();
      const cueTime = await page.evaluate(() => __contexts[0].sources[0].media.currentTime);
      if (Math.abs(cueTime - 3) > 0.1) throw Error(`Cue did not return: ${cueTime}`);
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-midi-status')).toHaveText('DDJ-FLX4 connecté');
      await page.evaluate(() => __sendMIDI([0x96, 0x41, 127]));
      await expect(page.locator('.mix-browser')).toHaveAttribute('data-focus', 'collections');
      await page.evaluate(() => { __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0x96, 0x41, 127]); });
      await expect(page.locator('#mix-collection')).toHaveValue('playlists:p');
      await expect(page.locator('.mix-browser')).toHaveAttribute('data-focus', 'tracks');
      await expect(page.locator('.mix-library-row')).toHaveCount(1);
      await page.evaluate(() => __sendMIDI([0x96, 0x42, 127]));
      await expect(page.locator('.mix-browser')).toHaveAttribute('data-focus', 'collections');
      await page.locator('#mix-collection').selectOption('');
      await page.evaluate(() => { __sendMIDI([0xb0, 0x00, 96]); __sendMIDI([0xb0, 0x20, 0]); __sendMIDI([0xb6, 0x1f, 127]); __sendMIDI([0xb6, 0x3f, 127]); });
      await expect(a.locator('[data-control="tempo"]')).toHaveValue('8');
      await expect(page.locator('#mix-cross')).toHaveValue('1');
      await page.evaluate(() => __sendMIDI([0x90, 0x0b, 127]));
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => __sendMIDI([0x90, 0x0b, 0]));
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => __sendMIDI([0x97, 0, 127]));
      await expect(a.locator('[data-pad="0"]')).toHaveAttribute('data-set', 'true');
      await expect(a.locator('.mix-cue-marker')).toHaveText('A');
      await page.evaluate(() => __sendMIDI([0x98, 0, 127]));
      await expect(a.locator('[data-pad="0"]')).toHaveAttribute('data-set', 'false');
      await expect(a.locator('.mix-cue-marker')).toHaveCount(0);
      await page.locator('#mix-stop').click();
      await a.locator('[data-control="tempo"]').evaluate(el => { el.value = '0'; el.dispatchEvent(new Event('input')); });
      await a.locator('[data-action="matchTempo"]').click();
      await expect(a.locator('[data-control="tempo"]')).toHaveValue('6.67');
      await expect(a.locator('.mix-value')).toContainText('128.00 BPM');
      const phaseError = await page.evaluate(() => {
        const a = __contexts[0].sources[0].media.currentTime * 120 / 60;
        const b = __contexts[0].sources[1].media.currentTime * 128 / 60;
        return Math.abs((a - b) - Math.round(a - b));
      });
      if (phaseError > .01) throw Error(`Beat phase not aligned: ${phaseError}`);
      await a.locator('[data-pad="1"]').click();
      await expect(a.locator('.mix-cue-marker')).toHaveText('B');
      await page.waitForFunction(() => __mixSettings.one.hotcues[1] !== null);
      await page.locator('.mix-library-row[data-job-id="one"] button').first().click();
      await expect(a.locator('.mix-deck-status')).toHaveText('Prêt');
      await expect(a.locator('[data-pad="1"]')).toHaveAttribute('data-set', 'true');
      await a.locator('[data-clear-pad="1"]').click();
      await expect(a.locator('.mix-cue-marker')).toHaveCount(0);
      await page.waitForFunction(() => __mixSettings.one.hotcues[1] === null);
      await a.locator('.mix-grid-editor summary').click();
      await a.locator('[data-control="bpm"]').fill('125');
      await a.locator('[data-control="bpm"]').press('Tab');
      await page.waitForFunction(() => __mixSettings.one.bpm === 125);
      await a.locator('[data-action="gridForward"]').click();
      await page.waitForFunction(() => Math.abs(__mixSettings.one.grid_offset - .01) < 1e-9);
      await a.locator('[data-control="key"]').selectOption('Dm');
      await expect(a.locator('.mix-key')).toHaveText('Dm');
      await a.locator('[data-action="analyze"]').click();
      await expect(a.locator('[data-control="bpm"]')).toHaveValue('125');
      await expect(a.locator('[data-control="grid"]')).toHaveValue('0.010');
      await a.locator('[data-control="bpm"]').fill('');
      await a.locator('[data-control="bpm"]').press('Tab');
      await expect(a.locator('[data-control="bpm"]')).toHaveValue('120');
      await page.screenshot({ path: `/work/mixer-grid-${width}.png`, fullPage: true });
      await a.locator('.mix-grid-editor summary').click();
      await page.locator('#mix-audio-settings').click();
      await expect(page.locator('#mix-settings')).toBeVisible();
      await expect(page.locator('#mix-audio-status')).not.toBeEmpty();
      await page.locator('#mix-audio-settings').click();
      const pixels = await a.locator('.mix-wave').evaluate(el => {
        const canvas = el.querySelector('div').shadowRoot.querySelector('canvas');
        if (!canvas) return 0;
        const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        return data.filter((_, i) => i % 4 === 3 && data[i] > 0).length;
      });
      if (pixels < 100) throw Error(`Blank waveform: ${pixels}`);
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Horizontal overflow');
      const playBox = await a.locator('[data-action="play"]').boundingBox();
      if (playBox.width !== playBox.height) throw Error('Play button not round');
      await page.screenshot({ path: `/work/mixer-${width}.png`, fullPage: true });
      await a.locator('[data-action="play"]').click();
      await page.evaluate(() => {
        const port = __midi.inputs.get('flx4');
        port.state = 'disconnected';
        __midi.onstatechange({ port });
      });
      await expect(page.locator('#mix-midi-status')).toContainText('introuvable');
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'false');
      await page.evaluate(() => {
        const port = __midi.inputs.get('flx4');
        port.state = 'connected';
        __midi.onstatechange({ port });
      });
      await expect(page.locator('#mix-midi-status')).toHaveText('DDJ-FLX4 connecté');
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await expect(page.locator('#mixer-panel')).toBeHidden();
      await expect(page.locator('#library-tracks')).toBeVisible();
      await page.locator('#mixer-nav').click();
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await page.route('**/fixture/audio?id=bad', route => route.fulfill({ status: 500, body: 'Fixture failure' }));
      let slowRoute;
      await page.route('**/fixture/audio?id=slow', route => { slowRoute = route; });
      await page.evaluate(() => {
        libraryState.tracks.push({ job_id: 'bad', title: 'Broken audio', streamable: true }, { job_id: 'slow', title: 'Slow audio', streamable: true });
        Mixer.sync();
      });
      await page.locator('.mix-library-row[data-job-id="bad"] button').first().click();
      await expect(a.locator('.mix-deck-status')).toContainText('500');
      await expect(a.locator('[data-action="play"]')).toBeDisabled();
      await page.locator('.mix-library-row[data-job-id="one"] button').first().click();
      await expect(a.locator('.mix-deck-status')).toHaveText('Prêt');
      await page.locator('.mix-library-row[data-job-id="slow"] button').first().click();
      await expect(a.locator('.mix-deck-status')).toHaveText('Chargement…');
      await expect.poll(() => Boolean(slowRoute)).toBe(true);
      await page.evaluate(() => showAuth());
      await slowRoute.abort().catch(() => {});
      await expect(page.locator('#app-view')).toBeHidden();
      await page.waitForFunction(() => __contexts[0].state === 'closed');
      if (await page.evaluate(() => __contexts[0].sources.some(s => s.media.hasAttribute('src') || !s.media.paused))) throw Error('Audio source not released at logout');
      await expect(page.locator('.mix-deck')).toHaveCount(0);
      if (errors.length) throw Error(errors.join('\n'));
      await page.close();
      console.log(`Mixer ${width}px: audio, MIDI, waveforms, filters, navigation and logout OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
