const { chromium, firefox, expect } = require('@playwright/test');
const fs = require('node:fs');

function audioFixture() {
  const rate = 44100, frames = rate * 12, buffer = Buffer.alloc(44 + frames * 4);
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
  const isFirefox = process.env.MIXER_BROWSER === 'firefox';
  const engine = isFirefox ? firefox : chromium;
  const folder = fs.readdirSync('/ms-playwright').find(n => n.startsWith(isFirefox ? 'firefox-' : 'chromium-'));
  const executablePath = process.env.CHROMIUM_PATH || `/ms-playwright/${folder}/${isFirefox ? 'firefox/firefox' : 'chrome-linux64/chrome'}`;
  const browser = await engine.launch({ executablePath, ...(isFirefox ? { firefoxUserPrefs: { 'media.autoplay.default': 0 } } : { args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }) });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(15000);
      const errors = [];
      page.on('pageerror', e => { errors.push(e.message); console.error('Browser error:', e.message); });
      page.on('console', msg => { if (msg.type() === 'error' && /Content Security|Refused to|TypeError|SyntaxError/.test(msg.text())) errors.push(msg.text()); });
      await page.addInitScript(() => {
        window.prompt = window.confirm = () => { throw Error('Native browser dialogs are disabled'); };
        const Original = window.AudioContext; window.__contexts = [];
        window.AudioContext = class extends Original {
          constructor(opts) { super(opts); this.sources = []; this.gains = []; this.analyser = this.createAnalyser(); window.__contexts.push(this); }
          createMediaElementSource(media) { const source = super.createMediaElementSource(media); this.sources.push({ media, source }); return source; }
          createGain() { const gain = super.createGain(); if (!this.gains.length) gain.connect(this.analyser); this.gains.push(gain); return gain; }
        };
        window.__midi = { inputs: new Map(), outputs: new Map() };
        window.__port = { name: 'Pioneer DDJ\u2011FLX4 MIDI 1', id: 'flx4', type: 'input', state: 'connected', connection: 'closed', async open() { this.connection = 'open'; }, async close() { this.connection = 'closed'; } };
        window.__sendMIDI = data => __port.onmidimessage?.({ data });
        Object.defineProperty(navigator, 'requestMIDIAccess', { configurable: true, value: async () => __midi });
      });
      await page.route('**/fixture/audio**', route => route.fulfill({ body: audioFixture(), contentType: 'audio/wav' }));
      await page.route('**/api/stream-ticket', route => route.fulfill({ json: { url: `/fixture/audio?id=${route.request().postDataJSON().job_id}` } }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8081');
      await page.waitForFunction(() => window.Mixer && window.TuneSaverMixerHost && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true; document.querySelector('#app-view').hidden = false;
        libraryState = { folders: [{ id: 'f', name: 'Sets' }], playlists: [{ id: 'p', folder_id: 'f', name: 'Soiree', track_ids: ['one'] }], tags: [{ id: 't', name: 'House', track_ids: ['two'] }], tracks: [
          { job_id: 'one', title: 'First light', artist: 'Studio Session', provider: 'qobuz', streamable: true },
          { job_id: 'two', title: 'Another very long track title to check mobile wrapping', artist: 'Long artist name and guest', provider: 'beatport', streamable: true },
        ] };
        const frames = 441 * 12, data = new Uint8Array(frames * 5);
        for (let i = 0; i < frames; i++) {
          const peak = Math.round(40 + 180 * Math.exp(-(i % 220) / 28));
          data.set([peak, Math.round(peak * .25), Math.round(peak * .15), peak, peak], i * 5);
        }
        const waveform = { encoding: 'u8-lmhpn', rate: 441, frames, data: btoa(Array.from(data, n => String.fromCharCode(n)).join('')) };
        const analysis = bpm => ({ version: 2, engine: 'Queen Mary DSP', bpm, duration: 12, grid_offset: 0, key: 'Am', camelot: '8A', grid_quality: 'stable', waveform });
        libraryState.tracks.forEach((t, i) => { t.mix_analysis = { status: 'done', bpm: i ? 128 : 120, duration: 12, key: 'Am', camelot: '8A' }; });
        window.__mixSettings = {};
        TuneSaverMixerHost.requestJson = async (path, options) => {
          if (path === '/api/stream-ticket') return { url: `/fixture/audio?id=${JSON.parse(options.body).job_id}` };
          if (!path.startsWith('/api/mixer/tracks/')) throw Error(`Unexpected request ${path}`);
          const id = path.split('/').pop(), settings = __mixSettings[id] ||= {};
          if (options.method === 'PATCH') for (const [key, value] of Object.entries(JSON.parse(options.body))) {
            if (key === 'hotcue') (settings.hotcues ||= Array(8).fill(null))[value.index] = value.time;
            else if (value === null) delete settings[key]; else settings[key] = value;
          }
          return { status: 'done', analysis: analysis(id === 'two' ? 128 : 120), settings: structuredClone(settings) };
        };
        renderLibrary();
      });
      const deck = async index => {
        if (width < 700) await page.locator('.mix-deck-tabs button').nth(index).click();
        return page.locator(`.mix-deck[data-deck="${index}"]`);
      };
      await page.evaluate(() => streamTrack(libraryState.tracks[0]));
      await page.waitForFunction(() => !audioPlayer.paused && audioPlayer.currentTime > .2);
      await expect(page.locator('#player-bar')).toBeVisible();
      await page.locator('#mixer-nav').click();
      const a = await deck(0);
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await expect(a.locator('.mix-deck-status')).toHaveText('En lecture', { timeout: 15000 });
      await page.locator('.mix-library-row[data-job-id="two"] button').first().click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.locator('#action-dialog-cancel').click();
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await a.locator('[data-action="eject"]').click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await page.locator('#mix-stop').click();
      await expect(page.locator('#player-bar')).toBeHidden();
      await expect(page.locator('#mix-performance')).toBeVisible();
      await page.locator('.mix-library-row[data-job-id="two"] button').last().click();
      const b = await deck(1);
      await expect(b.locator('.mix-deck-status')).toHaveText('Prêt');
      await (await deck(0)).locator('[data-action="play"]').click();
      await (await deck(1)).locator('[data-action="play"]').click();
      await page.waitForFunction(() => __contexts[0].sources.every(s => !s.media.paused && s.media.currentTime > .4));
      const rms = await page.evaluate(() => { const data = new Float32Array(__contexts[0].analyser.fftSize); __contexts[0].analyser.getFloatTimeDomainData(data); return Math.sqrt(data.reduce((sum, v) => sum + v * v, 0) / data.length); });
      if (rms < .005) throw Error(`Silent master: ${rms}`);
      const canvas = page.locator('#mix-timelines canvas').first();
      const before = await canvas.evaluate(c => c.toDataURL());
      const cursor = await page.locator('.mix-timeline-cursor').first().getAttribute('style');
      await page.waitForTimeout(120);
      if (before !== await canvas.evaluate(c => c.toDataURL())) throw Error('Waveform background animates within a page');
      if (cursor === await page.locator('.mix-timeline-cursor').first().getAttribute('style')) throw Error('Playback cursor is frozen');
      const colored = await canvas.evaluate(c => { const p = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < p.length; i += 4) if (p[i] > p[i + 1] + 30) n++; return n; });
      if (colored < 100) throw Error('Empty high-resolution waveform');
      await page.locator('#mix-stop').click();
      await deck(0);
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 1; });
      await a.locator('[data-action="loopIn"]').click();
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2; });
      await a.locator('[data-action="loopOut"]').click();
      await expect(a.locator('[data-action="loop"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => { const media = __contexts[0].sources[0].media; media.currentTime = 2.1; media.dispatchEvent(new Event('timeupdate')); });
      await page.waitForFunction(() => __contexts[0].sources[0].media.currentTime < 1.1);
      await a.locator('[data-action="loop"]').click();
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-midi-status')).toContainText('Aucune entrée MIDI');
      await page.evaluate(() => { __midi.inputs.set('flx4', __port); __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('connecté');
      await page.evaluate(() => { __sendMIDI([0x96, 0x41, 127]); __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0x96, 0x41, 127]); });
      await expect(page.locator('#mix-collection')).toHaveValue('playlists:p');
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await expect(page.locator('#mix-performance')).toBeVisible();
      await expect(page.locator('#mixer-nav')).toHaveAttribute('aria-pressed', 'true');
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await page.evaluate(() => { __sendMIDI([0xb0, 0, 96]); __sendMIDI([0xb0, 0x20, 0]); });
      await expect(a.locator('[data-control="tempo"]')).toHaveValue(/^8(?:\.0+)?$/);
      await expect(a.locator('.mix-current-bpm')).toHaveText('129.60');
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2.1; __contexts[0].sources[1].media.currentTime = .2; });
      await (await deck(0)).locator('[data-action="matchTempo"]').click();
      await expect(a.locator('.mix-current-bpm')).toHaveText('128.00');
      await (await deck(0)).locator('[data-pad="0"]').click();
      await expect(a.locator('.mix-cue-marker')).toHaveText('A');
      await page.waitForFunction(() => __mixSettings.one.hotcues?.[0] != null);
      await a.locator('[data-clear-pad="0"]').click();
      await expect(a.locator('.mix-cue-marker')).toHaveCount(0);
      const knob = a.locator('[data-control="low"]').locator('..');
      const bounds = await knob.boundingBox();
      await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 10); await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width / 2, bounds.y - 30); await page.mouse.up();
      if (Number(await a.locator('[data-control="low"]').inputValue()) <= 0) throw Error('Rotary drag failed');
      await a.locator('[data-control="low"]').focus(); await page.keyboard.press('ArrowDown');
      await a.locator('.mix-grid-editor summary').click();
      await a.locator('[data-control="bpm"]').fill('125'); await a.locator('[data-control="bpm"]').press('Tab');
      await page.waitForFunction(() => __mixSettings.one.bpm === 125);
      await a.locator('[data-action="gridForward"]').click();
      await page.waitForFunction(() => __mixSettings.one.grid_offset === .01);
      await a.locator('.mix-grid-editor summary').click();
      await page.evaluate(() => { __port.state = 'disconnected'; __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('Aucune entrée MIDI');
      await page.evaluate(() => { __port.name = 'USB MIDI Interface'; __port.state = 'connected'; __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('Sélectionne');
      await page.locator('#mix-midi-input').selectOption('flx4');
      await expect(page.locator('#mix-midi-status')).toHaveText('USB MIDI Interface connecté');
      await page.evaluate(() => __sendMIDI([0x90, 0x0b, 127]));
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.locator('#mix-stop').click();
      await page.locator('#mix-search').fill('first'); await expect(page.locator('.mix-library-row')).toHaveCount(1);
      await page.locator('#mix-search').fill(''); await expect(page.locator('.mix-library-row')).toHaveCount(2);
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const dock = await page.locator('#playback-dock').boundingBox();
      if (Math.abs(dock.y + dock.height - 1000) > 1) throw Error('Playback dock is not fixed to viewport bottom');
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Horizontal overflow');
      await page.screenshot({ path: `/work/mixer-${isFirefox ? 'firefox-' : ''}${width}.png`, fullPage: true });
      await page.locator('#mixer-nav').click();
      await expect(page.locator('#mix-performance')).toBeHidden(); await expect(page.locator('#player-bar')).toBeVisible();
      await expect(page.locator('#player-title')).toHaveText('First light');
      await page.locator('#mixer-nav').click(); await expect(a.locator('.mix-deck-status')).toHaveText('Prêt');
      await page.locator('#mix-collection').selectOption('unassigned');
      await expect(page.locator('.mix-library-row')).toHaveCount(1);
      await expect(page.locator('.mix-library-row')).toHaveAttribute('data-job-id', 'two');
      await page.route('**/fixture/audio**', async route => {
        await new Promise(resolve => setTimeout(resolve, 600));
        await route.fulfill({ body: audioFixture(), contentType: 'audio/wav' }).catch(() => {});
      });
      await page.locator('.mix-library-row button').first().click();
      await expect(a.locator('.mix-deck-status')).toHaveText('Chargement…');
      await page.evaluate(() => showAuth());
      await expect(page.locator('#playback-dock')).toBeHidden();
      await page.waitForFunction(() => __contexts[0].state === 'closed');
      await page.waitForTimeout(800);
      if (await page.evaluate(() => __contexts[0].sources.some(s => s.media.hasAttribute('src') || !s.media.paused))) throw Error('Audio retained after logout');
      if (errors.length) throw Error(errors.join('\n'));
      await page.close(); console.log(`${isFirefox ? 'Firefox' : 'Chromium'} ${width}: persistent dock, normal/mix handoff, static waveforms, knobs, MIDI discovery and cues OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
