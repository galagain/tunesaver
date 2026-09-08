const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
    await page.waitForFunction(() => window.Mixer && window.TuneSaverPlayer && publicConfig !== null);
    await page.evaluate(() => {
      session = { access_token: 'fixture' };
      document.querySelector('#auth-view').hidden = true; document.querySelector('#app-view').hidden = false;
      libraryState = { tracks: ['one', 'two'].map((id, i) => ({ job_id: id, title: id, artist: 'Fixture', streamable: true, mix_analysis: { bpm: 120 + i * 8 } })), playlists: [], tags: [] };
      const frames = 100, bytes = new Uint8Array(frames * 5);
      for (let i = 0; i < frames; i++) bytes.set([200, 30, 10, i % 2 ? 200 : 40, 90], i * 5);
      window.playerAnalysis = bpm => ({ status: 'done', analysis: { duration: 12, bpm, key: 'Am', camelot: '8A',
        waveform: { encoding: 'u8-lmhpn', rate: frames / 12, frames, data: btoa(String.fromCharCode(...bytes)) } } });
      TuneSaverMixerHost.requestJson = path => path.endsWith('one')
        ? new Promise(resolve => { window.finishOldAnalysis = () => resolve(playerAnalysis(90)); })
        : Promise.resolve(playerAnalysis(128));
      renderLibrary();
      setPlayerTrack(libraryState.tracks[0]);
      setPlayerTrack(libraryState.tracks[1]);
    });
    await expect(page.locator('#player-title')).toHaveText('two');
    await expect(page.locator('#player-metadata')).toContainText('128.00 BPM');
    await page.evaluate(() => finishOldAnalysis());
    await expect(page.locator('#player-metadata')).toContainText('128.00 BPM');
    await page.evaluate(() => {
      window.retryMethods = [];
      TuneSaverMixerHost.requestJson = (_path, options) => {
        retryMethods.push(options.method);
        return Promise.resolve(options.method === 'POST' ? playerAnalysis(122) : { status: 'failed' });
      };
      setPlayerTrack(libraryState.tracks[0]);
    });
    await expect(page.locator('#player-status')).toHaveText('Forme d’onde indisponible');
    await expect(page.locator('#player-retry')).toBeVisible();
    await page.locator('#player-retry').click();
    await expect(page.locator('#player-metadata')).toContainText('122.00 BPM');
    await expect(page.locator('#player-retry')).toBeHidden();
    expect(await page.evaluate(() => retryMethods)).toEqual(['GET', 'POST']);
    await page.evaluate(() => {
      TuneSaverMixerHost.requestJson = () => new Promise(resolve => { window.finishAfterReset = () => resolve(playerAnalysis(999)); });
      setPlayerTrack(libraryState.tracks[1]);
      resetPlayer(); finishAfterReset();
    });
    await expect(page.locator('#player-bar')).toBeHidden();
    await expect(page.locator('#player-metadata')).toBeEmpty();
    await expect(page.locator('#player-status')).toBeEmpty();
    expect(errors).toEqual([]);
    console.log(`${engine.name()}: stale analysis ignored, retry, reset and private waveform cleanup OK`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
