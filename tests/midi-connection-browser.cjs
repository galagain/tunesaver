const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const mode of ['normal', 'output-hangs', 'input-hangs', 'pending', 'late', 'close-hangs', 'denied']) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(mode => {
        window.midiMode = mode;
        window.midiAccess = { inputs: new Map(), outputs: new Map() };
        const emit = port => queueMicrotask(() => midiAccess.onstatechange?.({ port }));
        const port = type => ({
          id: type, type, name: 'DDJ-FLX4', state: 'connected', connection: 'closed', opens: 0, closes: 0,
          open() {
            this.opens++;
            if (midiMode === `${type}-hangs`) return new Promise(() => {});
            if (midiMode === 'late' && type === 'input') return new Promise(resolve => {
              window.resolveMIDI = () => { this.connection = 'open'; emit(this); resolve(this); };
            });
            this.connection = midiMode === 'pending' ? 'pending' : 'open'; emit(this);
            return Promise.resolve(this);
          },
          close() {
            this.closes++;
            this.connection = 'closed'; emit(this);
            return midiMode === 'close-hangs' ? new Promise(() => {}) : Promise.resolve(this);
          },
          send() {},
        });
        window.midiIn = port('input'); window.midiOut = port('output');
        midiAccess.inputs.set('input', midiIn); midiAccess.outputs.set('output', midiOut);
        Object.defineProperty(navigator, 'requestMIDIAccess', { configurable: true, value: async () => {
          if (midiMode === 'denied') throw new DOMException('Denied', 'NotAllowedError');
          return midiAccess;
        } });
      }, mode);
      await page.route('**/api/status', r => r.fulfill({ json: { active: {}, counts: {}, recent: [] } }));
      await page.route('**/api/history*', r => r.fulfill({ json: { history: [] } }));
      await page.route('**/api/library', r => r.fulfill({ json: { tracks: [], playlists: [], tags: [], folders: [], imports: [] } }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'fixture@example.com' } }; showApp(); });
      await page.locator('#mixer-nav').click();
      await page.locator('#mix-connect').click();
      const status = page.locator('#mix-midi-status');
      if (mode === 'denied') {
        await expect(status).toContainText('Accès MIDI refusé');
        await expect(page.locator('#mix-connect')).toBeEnabled();
      } else if (mode === 'input-hangs' || mode === 'pending') {
        await expect(status).toContainText('connexion impossible', { timeout: 7000 });
        await expect(status).toHaveAttribute('data-connected', 'false');
        await page.waitForTimeout(2100);
        expect(await page.evaluate(() => midiIn.opens)).toBe(1);
        await page.evaluate(() => { midiMode = 'normal'; });
        await page.locator('#mix-midi-refresh').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
      } else if (mode === 'late') {
        await expect(status).toContainText('ouverture');
        await expect(page.locator('#mix-connect')).toBeEnabled();
        await page.locator('#mix-connect').click();
        await page.evaluate(() => resolveMIDI());
        await expect(status).toHaveText('Non connecté');
        await expect.poll(() => page.evaluate(() => midiIn.connection)).toBe('closed');
        await page.evaluate(() => { midiMode = 'normal'; });
        await page.locator('#mix-connect').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
      } else {
        await expect(status).toHaveAttribute('data-connected', 'true', { timeout: 1200 });
        await expect(page.locator('#mix-connect')).toBeEnabled();
        await page.evaluate(() => midiIn.onmidimessage({ data: [0xb6, 0x1f, 127] }));
        await expect.poll(async () => Number(await page.locator('#mix-cross').inputValue())).toBeGreaterThan(0.98);
        if (mode === 'output-hangs') await expect(status).toContainText('voyants indisponibles');
        if (mode === 'close-hangs') {
          await page.locator('#mix-connect').click();
          await expect(status).toHaveText('Non connecté', { timeout: 1000 });
          await expect(page.locator('#mix-connect')).toBeEnabled();
        }
      }
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${mode}: OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
