const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    const cases = ['normal', 'output-hangs', 'input-hangs', 'pending', 'late', 'close-hangs', 'denied', 'permission-change', 'access-hangs'].map(mode => ({ mode, width: 1440 }));
    cases.push({ mode: 'normal', width: 1440, height: 900 }, { mode: 'normal', width: 390 }, { mode: 'normal', width: 320 });
    for (const { mode, width, height = 1000 } of cases) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(mode => {
        window.midiMode = mode;
        window.midiRequests = 0;
        window.permission = { state: mode === 'denied' ? 'denied' : 'granted' };
        Object.defineProperty(navigator, 'permissions', { configurable: true, value: { query: async () => permission } });
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
          midiRequests++;
          if (midiMode === 'access-hangs') return new Promise(resolve => { window.resolveAccess = () => resolve(midiAccess); });
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
      await expect(page.locator('#mix-connection')).toBeHidden();
      const controlsGeometry = () => page.locator('.workspace, #playback-dock, .deck-controls-heading, .deck-controls-track, .mix-eq, .mix-levels, .mix-tempo, .mix-pads').evaluateAll(elements => elements.map(el => {
        const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
      }));
      const beforeConnection = await controlsGeometry();
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-connection')).toBeVisible();
      await expect(page.locator('#mix-connect')).toHaveAttribute('aria-expanded', 'true');
      expect(await page.evaluate(() => midiRequests)).toBe(0);
      if (width >= 1360) {
        expect(await page.locator('#mix-connection').evaluate(panel => panel.parentElement.id)).toBe('deck-controls-0');
        const pads = await page.locator('#deck-controls-0 .mix-pads').boundingBox();
        const panel = await page.locator('#mix-connection').boundingBox();
        expect(panel.y).toBeGreaterThan(pads.y + pads.height);
        expect(await page.locator('#mix-outputs').evaluate(panel => panel.parentElement.id)).toBe('deck-controls-1');
        const rightPads = await page.locator('#deck-controls-1 .mix-pads').boundingBox();
        const outputs = await page.locator('#mix-outputs').boundingBox();
        expect(outputs.y).toBeGreaterThan(rightPads.y + rightPads.height);
        expect(await controlsGeometry()).toEqual(beforeConnection);
        expect(await page.locator('.deck-controls').evaluateAll(panels => panels.map(panel => panel.scrollTop))).toEqual([0, 0]);
      }
      await expect(page.locator('#mix-output')).toBeVisible();
      await expect(page.locator('#mix-routing')).toBeVisible();
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-connection')).toBeHidden();
      await expect(page.locator('#mix-outputs')).toBeHidden();
      expect(await controlsGeometry()).toEqual(beforeConnection);
      await page.locator('#mix-connect').click();
      await page.locator('#mix-midi-connect').click();
      const status = page.locator('#mix-midi-status');
      if (mode === 'access-hangs') {
        await expect(status).toContainText('Autorisation MIDI en attente');
        await page.evaluate(() => { midiMode = 'normal'; });
        await page.locator('#mix-midi-refresh').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
        await page.evaluate(() => resolveAccess());
        await expect(status).toHaveAttribute('data-connected', 'true');
        expect(await page.evaluate(() => midiRequests)).toBe(2);
        expect(await page.evaluate(() => midiIn.opens)).toBe(1);
      } else if (mode === 'denied') {
        await expect(status).toContainText('Accès MIDI refusé');
        await expect(page.locator('#mix-midi-connect')).toBeEnabled();
        await expect(page.locator('#mix-midi-help')).toBeVisible();
        await page.locator('#mix-midi-refresh').click();
        await expect(status).toContainText('Accès MIDI refusé');
        expect(await page.evaluate(() => midiRequests)).toBe(2);
        await page.evaluate(() => { midiMode = 'normal'; permission.state = 'granted'; permission.onchange(); });
        await expect(status).toContainText('Autorisation MIDI modifiée');
        await page.locator('#mix-midi-refresh').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
        await expect(page.locator('#mix-midi-help')).toBeHidden();
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
        await expect(page.locator('#mix-midi-connect')).toBeEnabled();
        await page.locator('#mix-midi-connect').click();
        await page.evaluate(() => resolveMIDI());
        await expect(status).toHaveText('Non connecté');
        await expect.poll(() => page.evaluate(() => midiIn.connection)).toBe('closed');
        await page.evaluate(() => { midiMode = 'normal'; });
        await page.locator('#mix-midi-connect').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
      } else {
        await expect(status).toHaveAttribute('data-connected', 'true', { timeout: 1200 });
        await expect(page.locator('#mix-midi-connect')).toBeEnabled();
        await page.evaluate(() => midiIn.onmidimessage({ data: [0xb6, 0x1f, 127] }));
        await expect.poll(async () => Number(await page.locator('#mix-cross').inputValue())).toBeGreaterThan(0.98);
        if (mode === 'output-hangs') await expect(status).toContainText('voyants indisponibles');
        if (mode === 'close-hangs') {
          await page.locator('#mix-midi-connect').click();
          await expect(status).toHaveText('Non connecté', { timeout: 1000 });
          await expect(page.locator('#mix-midi-connect')).toBeEnabled();
        }
        if (mode === 'permission-change') {
          await page.evaluate(() => { midiMode = 'denied'; permission.state = 'denied'; permission.onchange(); });
          await expect(status).toContainText('Accès MIDI refusé');
          expect(await page.evaluate(() => midiIn.onmidimessage)).toBeNull();
          await page.evaluate(() => { midiMode = 'normal'; permission.state = 'granted'; permission.onchange(); });
          await expect(status).toContainText('Autorisation MIDI modifiée');
        }
        await page.locator('#mix-midi-refresh').click();
        await expect(status).toHaveAttribute('data-connected', 'true');
        expect(await page.evaluate(() => midiRequests)).toBe(2);
        expect(await page.evaluate(() => midiIn.opens)).toBe(2);
      }
      const requests = await page.evaluate(() => midiRequests);
      await page.locator('#mix-connection-close').click();
      await expect(page.locator('#mix-connection')).toBeHidden();
      await expect(page.locator('#mix-connect')).toHaveAttribute('aria-expanded', 'false');
      await page.locator('#mix-connect').click();
      await expect(status).toHaveAttribute('data-connected', 'true');
      expect(await page.evaluate(() => midiRequests)).toBe(requests);
      if (mode === 'normal') {
        await expect(page.locator('#mix-midi-output')).toHaveText('DDJ-FLX4');
        await page.screenshot({ path: `/work/midi-panel-${engine.name()}-${width}-${height}.png` });
        if (width >= 1360) {
          expect(await page.locator('#mix-connection, #mix-outputs').evaluateAll(panels => panels.every(panel => panel.scrollHeight <= panel.clientHeight + 1))).toBe(true);
          expect(await controlsGeometry()).toEqual(beforeConnection);
        }
        await page.locator('#mix-levels-toggle').click();
        await expect(page.locator('#mix-controls')).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(page.locator('#mix-controls')).toBeHidden();
        await page.setViewportSize({ width: width === 1440 ? 1100 : 1440, height: 1000 });
        await expect(page.locator('#mix-connection')).toBeVisible();
        await expect(status).toHaveAttribute('data-connected', 'true');
        await page.locator('#mix-connect').click();
        await expect(page.locator('#mix-connection')).toBeHidden();
      }
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${mode} ${width}: OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
