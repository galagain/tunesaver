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
    for (const width of [2560, 1440, 390]) {
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
          createGain() {
            const gain = super.createGain(), connect = gain.connect.bind(gain);
            gain.targets = [];
            gain.connect = (...args) => { gain.targets.push(args[0]); return connect(...args); };
            if (!this.gains.length) gain.connect(this.analyser);
            this.gains.push(gain); return gain;
          }
        };
        window.__midi = { inputs: new Map(), outputs: new Map() };
        window.__port = { name: 'Pioneer DDJ\u2011FLX4 MIDI 1', id: 'flx4', type: 'input', state: 'connected', connection: 'closed', async open() { this.connection = 'open'; }, async close() { this.connection = 'closed'; } };
        window.__sendMIDI = data => __port.onmidimessage?.({ data });
        Object.defineProperty(navigator, 'requestMIDIAccess', { configurable: true, value: async () => __midi });
      });
      await page.route('**/fixture/audio**', route => {
        const body = audioFixture(), range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '');
        const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]), body.length - 1) : body.length - 1;
        return route.fulfill({ status: range ? 206 : 200, body: body.subarray(start, end + 1), contentType: 'audio/wav',
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${body.length}` } : {}) } });
      });
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
        const analysis = bpm => ({ version: 2, engine: 'Queen Mary DSP', bpm, duration: 12, grid_offset: 0, key: 'Am', camelot: '8A', grid_quality: 'variable', beats: Array.from({ length: 22 }, (_, i) => Math.floor(i / 2) * 1.1 + (i % 2 ? .5 : 0)), codec: 'pcm_s16le', sample_rate: 44100, bit_rate: 1411200, waveform });
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
        return page.locator(`.mix-deck[data-deck="${index}"]`);
      };
      await page.evaluate(() => streamTrack(libraryState.tracks[0]));
      await page.waitForFunction(() => Mixer.currentPlayback.playing && Mixer.currentPlayback.position > .2);
      await expect(page.locator('#mix-connect')).toBeHidden();
      const assertOverviewOrder = async () => {
        const order = await page.evaluate(() => ({
          tracks: [...document.querySelectorAll('.mix-deck')].sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map(el => el.dataset.deck),
          previews: [...document.querySelectorAll('#mix-overviews > .mix-overview')].sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(el => el.dataset.deck),
          keyboard: [...document.querySelectorAll('#mix-overviews > .mix-overview')].map(el => el.dataset.deck),
        }));
        expect(order.tracks).toEqual(['0', '1']);
        expect(order.previews).toEqual(order.tracks);
        expect(order.keyboard).toEqual(order.tracks);
        const connect = await page.locator('#mix-connect').boundingBox();
        const overviews = await page.locator('#mix-overviews').boundingBox();
        const upperDeck = await page.locator('.mix-deck[data-deck="0"]').boundingBox();
        expect(connect.x + connect.width).toBeLessThanOrEqual(overviews.x);
        expect(Math.abs(connect.y + connect.height / 2 - upperDeck.y - upperDeck.height / 2)).toBeLessThan(2);
        await expect(page.locator('#mix-controls-toggle')).toHaveCount(0);
      };
      await page.evaluate(() => {
        window.__handoffEvents = [];
        for (const event of ['play', 'pause', 'seeking', 'emptied', 'loadstart']) __contexts[0].sources[0].media.addEventListener(event, () => __handoffEvents.push(event));
      });
      await page.locator('#mixer-nav').click();
      await assertOverviewOrder();
      if (width >= 1360) {
        for (const index of [0, 1]) await expect(page.locator(`#deck-controls-${index}`)).toBeVisible();
        const panels = await page.evaluate(() => {
          const bounds = element => { const r = element.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
          return { left: bounds(document.querySelector('#deck-controls-0')), right: bounds(document.querySelector('#deck-controls-1')), center: bounds(document.querySelector('.workspace')), dock: bounds(document.querySelector('#playback-dock')) };
        });
        expect(panels.left.right).toBeLessThan(panels.center.left);
        expect(panels.right.left).toBeGreaterThan(panels.center.right);
        expect(panels.left.bottom).toBeLessThan(panels.dock.top);
        expect(panels.left.top).toBe(panels.right.top);
        expect(panels.left.bottom).toBe(panels.right.bottom);
        await page.locator('#library-search').click();
        await page.keyboard.press('Escape');
        for (const index of [0, 1]) await expect(page.locator(`#deck-controls-${index}`)).toBeVisible();
        await page.setViewportSize({ width: 1100, height: 1000 });
        await expect(page.locator('#deck-controls-0')).toBeHidden();
        await page.setViewportSize({ width: 1360, height: 720 });
        await expect(page.locator('#deck-controls-0')).toBeVisible();
        await page.locator('.mix-deck[data-deck="0"] [data-action="settings"]').click();
        await expect(page.locator('#deck-analysis-0')).toBeVisible();
        expect(await page.locator('#deck-controls-0').evaluate(panel => panel.scrollHeight <= panel.clientHeight)).toBe(true);
        expect(await page.locator('#deck-controls-0').evaluate(panel => panel.scrollWidth <= panel.clientWidth)).toBe(true);
        await page.locator('#deck-analysis-0 [data-action="closeAnalysis"]').click();
        await page.evaluate(() => document.querySelector('#deck-controls-0').scrollTop = 0);
        await page.setViewportSize({ width, height: 1000 });
        await expect(page.locator('#deck-controls-0')).toBeVisible();
      }
      for (let i = 0; i < 2; i++) {
        await page.locator('#mixer-nav').click();
        await expect(page.locator('.mix-deck[data-deck="0"] .mix-overview')).toBeVisible();
        await page.locator('#mixer-nav').click();
        await assertOverviewOrder();
      }
      expect(await page.evaluate(() => __handoffEvents)).toEqual([]);
      expect(await page.evaluate(() => Mixer.currentPlayback.playing)).toBe(true);
      const a = await deck(0);
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await expect(a.locator('.mix-deck-status')).toHaveText('En lecture', { timeout: 15000 });
      await page.locator('.library-track[data-job-id="two"] .play-button').click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.locator('#action-dialog-cancel').click();
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await a.locator('[data-action="settings"]').click();
      await expect(a.locator('.mix-grid-editor')).toBeVisible();
      if (width < 1360) await a.locator('[data-action="deckCommands"]').click();
      await a.locator('[data-action="eject"]').click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      const globalControls = async () => { await page.evaluate(() => document.querySelector('#mix-controls').showPopover()); };
      const deckControls = async index => { await page.evaluate(i => document.querySelector(`#deck-controls-${i}`).showPopover(), index); };
      const stop = async () => { await globalControls(); await page.locator('#mix-stop').click(); await page.evaluate(() => document.querySelector('#mix-controls').hidePopover()); };
      await stop();
      await expect(page.locator('#mix-performance')).toBeVisible();
      const geometry = () => page.locator('.workspace, #mix-overviews, .mix-deck').evaluateAll(elements => elements.map(el => {
        const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
      }));
      const beforeAnalysis = await geometry();
      await a.locator('[data-action="settings"]').click();
      await expect(a.locator('[data-action="settings"]')).toHaveAttribute('aria-expanded', 'true');
      await expect(a.locator('.mix-grid-tools')).toBeVisible();
      expect(await geometry()).toEqual(beforeAnalysis);
      const analysisBox = await a.locator('.mix-grid-editor').boundingBox();
      const previewsBox = await page.locator('#mix-overviews').boundingBox();
      expect(analysisBox.x).toBeGreaterThanOrEqual(0);
      expect(analysisBox.x + analysisBox.width).toBeLessThanOrEqual(width);
      expect(analysisBox.y).toBeGreaterThanOrEqual(0);
      expect(analysisBox.y + analysisBox.height).toBeLessThan(previewsBox.y);
      if (width >= 700) expect(Math.abs(analysisBox.x + analysisBox.width / 2 - previewsBox.x - previewsBox.width / 2)).toBeLessThan(1);
      await page.locator('.mix-deck[data-deck="1"] [data-action="settings"]').click();
      await expect(a.locator('.mix-grid-editor')).toBeHidden();
      await expect(page.locator('#deck-analysis-1')).toBeVisible();
      await expect(page.locator('#deck-analysis-1 [data-control="bpm"]')).toBeDisabled();
      await expect(page.locator('#deck-analysis-1 [data-action="closeAnalysis"]')).toBeEnabled();
      await page.keyboard.press('Escape');
      await expect(page.locator('#deck-analysis-1')).toBeHidden();
      await expect(a.locator('[data-action="settings"]')).toHaveAttribute('aria-expanded', 'false');
      await a.locator('[data-action="settings"]').click();
      const tempoMode = a.locator('[data-control="tempoMode"]');
      await expect(tempoMode).toHaveValue('fixed');
      for (const position of [.2, .8, 2.4]) {
        await page.evaluate(position => { __contexts[0].sources[0].media.currentTime = position; }, position);
        await page.waitForFunction(() => !__contexts[0].sources[0].media.seeking);
        await expect(a.locator('.mix-current-bpm')).toHaveText('120.00');
      }
      await a.locator('[data-control="bpm"]').fill('125'); await a.locator('[data-control="bpm"]').press('Tab');
      await expect(a.locator('.mix-current-bpm')).toHaveText('125.00');
      await page.evaluate(() => {
        window.__tempoEvents = [];
        for (const event of ['play', 'pause', 'seeking', 'emptied', 'loadstart']) __contexts[0].sources[0].media.addEventListener(event, () => __tempoEvents.push(event));
      });
      await tempoMode.selectOption('variable');
      await page.waitForFunction(() => __mixSettings.one.tempo_mode === 'variable');
      await expect(a.locator('[data-control="bpm"]')).toBeDisabled();
      expect(await page.evaluate(() => __tempoEvents)).toEqual([]);
      for (const [position, bpm] of [[.2, '120.00'], [.8, '100.00']]) {
        await page.evaluate(position => { __contexts[0].sources[0].media.currentTime = position; }, position);
        await expect(a.locator('.mix-current-bpm')).toHaveText(bpm);
      }
      await tempoMode.selectOption('fixed');
      await expect(a.locator('.mix-current-bpm')).toHaveText('125.00');
      await expect(a.locator('[data-control="bpm"]')).toBeEnabled();
      await page.screenshot({ path: `/work/tempo-mode-${engine.name()}-${width}.png` });
      await a.locator('[data-control="bpm"]').fill('120'); await a.locator('[data-control="bpm"]').press('Tab');
      await expect(a.locator('.mix-current-bpm')).toHaveText('120.00');
      await a.locator('[data-action="closeAnalysis"]').click();
      await page.evaluate(() => document.querySelector('#deck-controls-0').hidePopover());
      await page.locator('.library-track[data-job-id="two"] .track-menu > summary').click();
      await page.locator('.library-track[data-job-id="two"] button[title="Charger sur B"]').click();
      const b = await deck(1);
      await expect(b.locator('.mix-deck-status')).toHaveText('Prêt');
      await assertOverviewOrder();
      await expect(page.locator('#mix-overviews > .mix-overview').first().locator('.mix-overview-label span')).toHaveText('First light');
      await expect(page.locator('#mix-overviews > .mix-overview').last().locator('.mix-overview-label span')).toContainText('Another very long track title');
      const crossGains = () => page.evaluate(() => {
        const { gains } = __contexts[0];
        return gains.filter(g => g.targets.includes(gains[0])).map(g => Math.round(g.gain.value * 1000) / 1000);
      });
      const crossEnabled = page.locator('#mix-cross-enabled'), cross = page.locator('#mix-cross');
      await globalControls();
      await expect(crossEnabled).not.toBeChecked();
      await expect(cross).toBeDisabled();
      await expect.poll(crossGains).toEqual([1, 1]);
      await crossEnabled.check();
      await expect(cross).toBeEnabled();
      await expect.poll(crossGains).toEqual([.707, .707]);
      await cross.press('Home');
      await expect.poll(crossGains).toEqual([1, 0]);
      await cross.press('End');
      await expect.poll(crossGains).toEqual([0, 1]);
      await crossEnabled.uncheck();
      await expect.poll(crossGains).toEqual([1, 1]);
      await page.locator('#mix-master-close').click();
      await (await deck(0)).locator('[data-action="play"]').click();
      await (await deck(1)).locator('[data-action="play"]').click();
      await page.waitForFunction(() => __contexts[0].sources.every(s => !s.media.paused && s.media.currentTime > .4));
      const rms = await page.evaluate(() => { const data = new Float32Array(__contexts[0].analyser.fftSize); __contexts[0].analyser.getFloatTimeDomainData(data); return Math.sqrt(data.reduce((sum, v) => sum + v * v, 0) / data.length); });
      if (rms < .005) throw Error(`Silent master: ${rms}`);
      const canvas = page.locator('.mix-timeline-lane canvas').first();
      const before = await canvas.evaluate(c => c.toDataURL());
      const cursor = await page.locator('.mix-timeline-cursor').first().getAttribute('style');
      await page.waitForTimeout(120);
      if (before === await canvas.evaluate(c => c.toDataURL())) throw Error('Waveform does not scroll during playback');
      if (cursor !== await page.locator('.mix-timeline-cursor').first().getAttribute('style')) throw Error('Playback cursor must stay fixed');
      await expect(page.locator('.mix-timeline-cursor').first()).toHaveCSS('left', `${(await canvas.boundingBox()).width / 2}px`);
      const colored = await canvas.evaluate(c => { const p = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < p.length; i += 4) if (p[i] > p[i + 1] + 30) n++; return n; });
      if (colored < 100) throw Error('Empty high-resolution waveform');
      await stop();
      await page.waitForTimeout(100);
      const paused = await canvas.evaluate(c => c.toDataURL());
      await page.waitForTimeout(150);
      expect(await canvas.evaluate(c => c.toDataURL())).toBe(paused);
      const positions = await page.evaluate(() => __contexts[0].sources.map(s => s.media.currentTime));
      const curves = await page.locator('.mix-timeline-lane canvas').evaluateAll(cs => cs.map(c => c.toDataURL()));
      await canvas.hover();
      await page.keyboard.down('Shift');
      await page.mouse.wheel(0, -100);
      await page.keyboard.up('Shift');
      await expect(page.locator('#mix-wave-zoom')).toHaveValue('13');
      await page.waitForTimeout(100);
      const zoomed = await page.locator('.mix-timeline-lane canvas').evaluateAll(cs => cs.map(c => c.toDataURL()));
      curves.forEach((curve, i) => expect(zoomed[i]).not.toBe(curve));
      expect(await page.evaluate(() => __contexts[0].sources.map(s => s.media.currentTime))).toEqual(positions);
      await expect(page.locator('.mix-timeline-cursor').first()).toHaveCSS('left', `${(await canvas.boundingBox()).width / 2}px`);
      await globalControls();
      await page.locator('#mix-wave-zoom').fill('1');
      await page.waitForTimeout(100);
      expect(await canvas.evaluate(c => c.toDataURL())).not.toBe(paused);
      await page.locator('#mix-wave-zoom').fill('4');
      await deck(0);
      await deckControls(0);
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 1; });
      await a.locator('[data-action="loopIn"]').click();
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2; });
      await a.locator('[data-action="loopOut"]').click();
      await expect(a.locator('[data-action="loop"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => { const media = __contexts[0].sources[0].media; media.currentTime = 2.1; media.dispatchEvent(new Event('timeupdate')); });
      await page.waitForFunction(() => __contexts[0].sources[0].media.currentTime < 1.1);
      await a.locator('[data-action="loop"]').click();
      await page.evaluate(() => document.querySelector('#mix-controls').hidePopover());
      await expect(page.locator('#mix-connect')).toHaveAccessibleName('Connexion FLX4 et audio');
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-midi-connect')).toHaveAccessibleName('Connecter le FLX4');
      await page.locator('#mix-midi-connect').click();
      await expect(page.locator('#mix-midi-status')).toContainText('Aucune entrée MIDI');
      await page.evaluate(() => { __midi.inputs.set('flx4', __port); __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('connecté');
      await expect(page.locator('#mix-connect')).toHaveAttribute('data-connected', 'true');
      await expect(page.locator('#mix-midi-connect')).toHaveAccessibleName('Déconnecter le MIDI');
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-connection')).toBeHidden();
      await expect(page.locator('#mix-connect')).toHaveAttribute('data-connected', 'true');
      await page.evaluate(() => __sendMIDI([0x90, 0x0b, 127]));
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await expect(b.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'false');
      await page.evaluate(() => {
        window.__panelAudioEvents = [];
        for (const event of ['pause', 'seeking', 'emptied', 'loadstart']) __contexts[0].sources[0].media.addEventListener(event, () => __panelAudioEvents.push(event));
      });
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-output')).toBeVisible();
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-connection')).toBeHidden();
      for (const value of [0, 127]) {
        await page.evaluate(value => { __sendMIDI([0xb6, 0x1f, value]); __sendMIDI([0xb6, 0x3f, value]); }, value);
        await expect(cross).toHaveValue(value ? '1' : '0');
        await expect.poll(crossGains).toEqual([1, 1]);
      }
      await globalControls();
      await crossEnabled.check();
      await expect.poll(crossGains).toEqual([0, 1]);
      await page.evaluate(() => { __sendMIDI([0xb6, 0x1f, 0]); __sendMIDI([0xb6, 0x3f, 0]); });
      await expect.poll(crossGains).toEqual([1, 0]);
      await crossEnabled.uncheck();
      await expect.poll(crossGains).toEqual([1, 1]);
      await expect(cross).toBeDisabled();
      await page.locator('#mix-master-close').click();
      expect(await page.evaluate(() => __panelAudioEvents)).toEqual([]);
      expect(await page.evaluate(() => Mixer.currentPlayback.playing)).toBe(true);
      await page.evaluate(() => { __sendMIDI([0x90, 0x0b, 127]); __sendMIDI([0x91, 0x0b, 127]); });
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'false');
      await expect(b.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => __sendMIDI([0x91, 0x0b, 127]));
      await expect(b.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'false');
      await assertOverviewOrder();
      await page.evaluate(() => { __sendMIDI([0x96, 0x41, 127]); __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0xb6, 0x40, 1]); __sendMIDI([0x96, 0x41, 127]); });
      await expect(page.locator('[data-collection="playlists:p"]')).toHaveAttribute('data-selected', 'true');
      await page.keyboard.press('Escape');
      await page.locator('.library-nav-content [data-collection=""]').click();
      await expect(page.locator('#mix-performance')).toBeVisible();
      await expect(page.locator('#mixer-nav')).toHaveAttribute('aria-pressed', 'true');
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await page.evaluate(() => { __sendMIDI([0xb0, 0, 96]); __sendMIDI([0xb0, 0x20, 0]); });
      await expect(a.locator('[data-control="tempo"]')).toHaveValue(/^8(?:\.0+)?$/);
      await expect(a.locator('.mix-current-bpm')).toHaveText('129.60');
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2.1; __contexts[0].sources[1].media.currentTime = .2; });
      await deckControls(0);
      await (await deck(0)).locator('[data-action="matchTempo"]').click();
      await expect(a.locator('.mix-current-bpm')).toHaveText('128.00');
      await (await deck(0)).locator('[data-pad="0"]').click();
      await expect(page.locator('.mix-overview[data-deck="0"] .mix-cue-marker')).toHaveText('A');
      await page.waitForFunction(() => __mixSettings.one.hotcues?.[0] != null);
      await a.locator('[data-clear-pad="0"]').click();
      await expect(page.locator('.mix-overview[data-deck="0"] .mix-cue-marker')).toHaveCount(0);
      const knob = a.locator('[data-control="low"]').locator('..');
      for (const name of ['low', 'mid', 'high', 'filter', 'tempo']) {
        const angles = await a.locator(`[data-control="${name}"]`).evaluate(input => {
          const original = input.value;
          const angles = [input.min, 0, input.max].map(value => {
            input.value = value; input.dispatchEvent(new Event('input', { bubbles: true }));
            return input.parentElement.style.getPropertyValue('--angle');
          });
          input.value = original; input.dispatchEvent(new Event('input', { bubbles: true }));
          return angles;
        });
        expect(angles).toEqual(['-135deg', '0deg', '135deg']);
      }
      const bounds = await knob.boundingBox();
      await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 10); await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width / 2, bounds.y - 30); await page.mouse.up();
      if (Number(await a.locator('[data-control="low"]').inputValue()) <= 0) throw Error('Rotary drag failed');
      await a.locator('[data-control="low"]').focus(); await page.keyboard.press('ArrowDown');
      await a.locator('[data-action="settings"]').click();
      await a.locator('[data-control="bpm"]').fill('125'); await a.locator('[data-control="bpm"]').press('Tab');
      await page.waitForFunction(() => __mixSettings.one.bpm === 125);
      await a.locator('[data-action="gridForward"]').click();
      await page.waitForFunction(() => __mixSettings.one.grid_offset === .01);
      await page.evaluate(() => { __contexts[0].sources[0].media.currentTime = 2.345; });
      await page.waitForFunction(() => !__contexts[0].sources[0].media.seeking);
      await page.evaluate(() => { __tempoEvents.length = 0; });
      await a.locator('[data-action="anchor"]').click();
      await page.waitForFunction(() => Math.abs(__mixSettings.one.grid_offset - 2.345) < .001);
      await expect(a.locator('[data-control="grid"]')).toHaveValue('2.345');
      expect(await page.evaluate(() => __tempoEvents)).toEqual([]);
      await a.locator('[data-action="closeAnalysis"]').click();
      await a.locator('[data-action="play"]').click();
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.evaluate(() => { __port.state = 'disconnected'; __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('Aucune entrée MIDI');
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await page.waitForFunction(() => !__contexts[0].sources[0].media.paused);
      await page.evaluate(() => { __port.name = 'USB MIDI Interface'; __port.state = 'connected'; __midi.onstatechange({ port: __port }); });
      await expect(page.locator('#mix-midi-status')).toContainText('Sélectionne');
      await page.locator('#mix-connect').click();
      await page.locator('#mix-midi-input').selectOption('flx4');
      await expect(page.locator('#mix-midi-status')).toHaveText('USB MIDI Interface connecté');
      await page.locator('#mix-midi-refresh').click();
      await expect(page.locator('#mix-midi-status')).toHaveText('USB MIDI Interface connecté');
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await stop();
      await page.evaluate(() => __sendMIDI([0x90, 0x0b, 127]));
      await expect(a.locator('[data-action="play"]')).toHaveAttribute('aria-pressed', 'true');
      await stop();
      await page.locator('#library-search').fill('first'); await expect(page.locator('.library-track')).toHaveCount(1);
      await page.locator('#library-search').fill(''); await expect(page.locator('.library-track')).toHaveCount(2);
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const dock = await page.locator('#playback-dock').boundingBox();
      if (Math.abs(dock.y + dock.height - 1000) > 1) throw Error('Playback dock is not fixed to viewport bottom');
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Horizontal overflow');
      await page.screenshot({ path: `/work/mixer-${isFirefox ? 'firefox-' : ''}${width}.png`, fullPage: true });
      await globalControls();
      await crossEnabled.check();
      await page.locator('#mix-master-close').click();
      await page.locator('#mixer-nav').click();
      await expect(crossEnabled).toBeChecked();
      await expect(page.locator('#mix-connect')).toBeHidden();
      await expect(b).toBeHidden(); await expect(a).toBeVisible();
      await expect(a.locator('.mix-track-info strong')).toHaveText('First light');
      await a.locator('[data-action="settings"]').click();
      await expect(a.locator('.mix-grid-tools')).toBeVisible();
      await expect(a.locator('[data-action="deckCommands"]')).toBeVisible();
      await a.locator('[data-action="deckCommands"]').click();
      await expect(a.locator('.mix-grid-editor')).toBeHidden();
      await expect(page.locator('#deck-controls-0')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.locator('#deck-controls-0')).toBeHidden();
      await page.locator('#mixer-nav').click(); await expect(a.locator('.mix-deck-status')).toHaveText('Prêt');
      await expect(crossEnabled).toBeChecked();
      await page.locator('[data-collection="tags:t"]').click();
      await expect(page.locator('.library-track')).toHaveCount(1);
      await expect(page.locator('.library-track')).toHaveAttribute('data-job-id', 'two');
      await page.route('**/fixture/audio**', async route => {
        await new Promise(resolve => setTimeout(resolve, 600));
        await route.fulfill({ body: audioFixture(), contentType: 'audio/wav' }).catch(() => {});
      });
      await page.locator('.library-track .play-button').click();
      await expect(a.locator('.mix-deck-status')).toHaveText(/Chargement…|Préparation audio…/);
      await page.evaluate(() => showAuth());
      await expect(crossEnabled).not.toBeChecked();
      await expect(cross).toBeDisabled();
      await expect(page.locator('#playback-dock')).toBeHidden();
      await page.waitForFunction(() => __contexts[0].state === 'closed');
      await page.waitForTimeout(800);
      if (await page.evaluate(() => __contexts[0].sources.some(s => s.media.hasAttribute('src') || !s.media.paused))) throw Error('Audio retained after logout');
      if (errors.length) throw Error(errors.join('\n'));
      await page.close(); console.log(`${isFirefox ? 'Firefox' : 'Chromium'} ${width}: persistent dock, normal/mix handoff, scrolling waveforms, knobs, MIDI reset without audio interruption and cues OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
