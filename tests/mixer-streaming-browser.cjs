const { chromium, firefox, expect } = require('@playwright/test');
const http = require('node:http');
const fs = require('node:fs');

// Hold most of a real WAV response open: a whole-file fetch can never pass.
const rate = 44100, duration = 60, audio = Buffer.alloc(44 + rate * duration * 4);
audio.write('RIFF'); audio.writeUInt32LE(audio.length - 8, 4); audio.write('WAVEfmt ', 8);
audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(2, 22);
audio.writeUInt32LE(rate, 24); audio.writeUInt32LE(rate * 4, 28); audio.writeUInt16LE(4, 32); audio.writeUInt16LE(16, 34);
audio.write('data', 36); audio.writeUInt32LE(audio.length - 44, 40);
for (let i = 0; i < rate * duration; i++) {
  const sample = Math.round(Math.sin(i / rate * Math.PI * 880) * 8000);
  audio.writeInt16LE(sample, 44 + i * 4); audio.writeInt16LE(sample, 46 + i * 4);
}

(async () => {
  const upstream = process.env.MIXER_URL || 'http://127.0.0.1:8082';
  let released = false;
  const pending = new Set(), ranges = [];
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/fixture/audio')) {
      if (new URL(req.url, upstream).searchParams.get('id') === 'bad') { res.writeHead(410); res.end(); return; }
      const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
      const start = range ? Number(range[1]) : 0;
      const end = range?.[2] ? Math.min(Number(range[2]), audio.length - 1) : audio.length - 1;
      ranges.push(start);
      res.writeHead(range ? 206 : 200, { 'Content-Type': 'audio/wav', 'Accept-Ranges': 'bytes',
        'Content-Length': end - start + 1, ...(range ? { 'Content-Range': `bytes ${start}-${end}/${audio.length}` } : {}) });
      if (released) { res.end(audio.subarray(start, end + 1)); return; }
      const split = Math.min(end + 1, start + 44 + rate * 8 * 4);
      res.write(audio.subarray(start, split));
      const finish = () => res.end(audio.subarray(split, end + 1));
      pending.add(finish); res.on('close', () => pending.delete(finish));
      return;
    }
    const proxy = http.request(new URL(req.url, upstream), { method: req.method, headers: req.headers }, reply => {
      res.writeHead(reply.statusCode, reply.headers); reply.pipe(res);
    });
    proxy.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
    req.pipe(proxy);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const isFirefox = process.env.MIXER_BROWSER === 'firefox';
  const engine = isFirefox ? firefox : chromium;
  const folder = fs.readdirSync('/ms-playwright').find(n => n.startsWith(isFirefox ? 'firefox-' : 'chromium-'));
  const browser = await engine.launch({ executablePath: `/ms-playwright/${folder}/${isFirefox ? 'firefox/firefox' : 'chrome-linux64/chrome'}`,
    ...(isFirefox ? { firefoxUserPrefs: { 'media.autoplay.default': 0 } } : { args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] }) });
  try {
    for (const width of [1440, 390]) {
      released = false; ranges.length = 0;
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      page.setDefaultTimeout(10000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        const Original = window.AudioContext;
        window.__contexts = [];
        window.AudioContext = class extends Original {
          constructor(opts) { super(opts); this.sources = []; this.analyser = this.createAnalyser(); window.__contexts.push(this); }
          createMediaElementSource(media) {
            const source = super.createMediaElementSource(media);
            source.connect(this.analyser); this.sources.push({ source, media }); return source;
          }
        };
      });
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.waitForFunction(() => window.Mixer && window.TuneSaverMixerHost && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true; document.querySelector('#app-view').hidden = false;
        libraryState = { folders: [], playlists: [], tags: [], tracks: [
          { job_id: 'one', title: 'Streaming test', artist: 'Studio', provider: 'qobuz', streamable: true,
            mix_analysis: { status: 'done', bpm: 120, duration: 60, key: 'Am' } },
          { job_id: 'two', title: 'Without cached analysis', artist: 'Studio', provider: 'tiddl', streamable: true },
        ] };
        window.__releaseAnalysis = [];
        window.__rejectAnalysis = [];
        window.__ticketCount = 0;
        TuneSaverMixerHost.requestJson = async (path, options) => {
          if (path === '/api/stream-ticket') return { url: `/fixture/audio?id=${JSON.parse(options.body).job_id}&ticket=${++__ticketCount}` };
          if (path.startsWith('/api/mixer/tracks/')) return new Promise((resolve, reject) => {
            __rejectAnalysis.push(() => reject(new Error('Analysis service unavailable')));
            options.signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true });
            __releaseAnalysis.push(() => {
              const frames = 441 * 60, data = new Uint8Array(frames * 5);
              for (let i = 0; i < frames; i++) {
                const peak = Math.round(40 + 180 * Math.exp(-(i % 220) / 28));
                data.set([peak, peak / 4, peak / 8, peak, peak], i * 5);
              }
              resolve({ status: 'done', settings: {}, analysis: { bpm: 122, key: 'Am', duration: 60,
                waveform: { encoding: 'u8-lmhpn', rate: 441, frames, data: btoa(Array.from(data, n => String.fromCharCode(n)).join('')) } } });
            });
          });
          throw Error(`Unexpected ${path}`);
        };
        renderLibrary();
        window.__started = performance.now();
        window.__load = streamTrack(libraryState.tracks[0]);
      });
      await page.waitForFunction(() => Mixer.currentPlayback.playing && Mixer.currentPlayback.position > .15);
      expect(pending.size).toBeGreaterThan(0);
      const startup = await page.evaluate(() => performance.now() - __started);
      const a = page.locator('.mix-deck[data-deck="0"]');
      await expect(a.locator('.mix-current-bpm')).toHaveText('120.00');
      await expect(page.locator('.mix-load-label, .mix-load-progress')).toHaveCount(0);
      expect(await page.evaluate(() => {
        const media = __contexts[0].sources[0].media;
        return media.buffered.end(0) / media.duration;
      })).toBeLessThan(.5);
      await page.waitForFunction(() => {
        const samples = new Float32Array(2048); __contexts[0].analyser.getFloatTimeDomainData(samples);
        return samples.some(n => Math.abs(n) > .01);
      });
      await page.screenshot({ path: `/work/streaming-pending-${engine.name()}-${width}.png` });
      const before = await page.locator('#playback-dock').boundingBox();
      await page.waitForFunction(() => __releaseAnalysis.length > 0);
      await page.evaluate(() => {
        window.__events = [];
        for (const event of ['pause', 'play', 'emptied', 'loadstart', 'seeking']) __contexts[0].sources[0].media.addEventListener(event, () => __events.push(event));
        __releaseAnalysis.splice(0).forEach(resolve => resolve());
      });
      await expect(a.locator('.mix-current-bpm')).toHaveText('122.00');
      expect(await page.locator('#playback-dock').boundingBox()).toEqual(before);
      await page.evaluate(() => streamTrack(libraryState.tracks[0]));
      expect(await page.evaluate(() => __ticketCount)).toBe(1);
      expect(await page.evaluate(() => __events)).toEqual([]);
      await page.locator('#mixer-nav').click();
      await page.locator('#mixer-nav').click();
      expect(await page.evaluate(() => __events)).toEqual([]);
      // Same track on both decks must get independent streams.
      await page.evaluate(() => Mixer.loadTrack(libraryState.tracks[0], 1));
      await expect(page.locator('.mix-deck[data-deck="1"]')).toHaveAttribute('data-ready', 'true');
      expect(await page.evaluate(() => __ticketCount)).toBe(2);
      await page.evaluate(() => Mixer.seekPrimary(40));
      await page.waitForFunction(() => Mixer.currentPlayback.position > 40.1 && !__contexts[0].sources[0].media.seeking);
      expect(ranges.some(start => start > rate * 4 * 30)).toBe(true);
      released = true; for (const finish of pending) finish(); pending.clear();
      // Unknown duration works from native metadata, without analysis or decoding.
      await page.evaluate(() => {
        __contexts[0].sources[0].media.pause();
        window.__nextLoad = streamTrack(libraryState.tracks[1]);
      });
      await page.waitForFunction(() => Mixer.currentPlayback.playing && document.querySelector('.mix-deck[data-deck="0"] .mix-track-info strong').textContent === 'Without cached analysis');
      await expect(page.locator('.mix-overview[data-deck="0"] [data-duration]')).toHaveText('1:00');
      await page.waitForFunction(() => __rejectAnalysis.length >= 3);
      await page.evaluate(() => __rejectAnalysis.splice(0).forEach(reject => reject()));
      await expect(a.locator('.mix-analysis-info')).toContainText('Analyse indisponible');
      await expect(page.locator('.mix-load-label, .mix-load-progress')).toHaveCount(0);
      expect(await page.evaluate(() => Mixer.currentPlayback.playing)).toBe(true);
      await page.evaluate(async () => {
        __contexts[0].sources.at(-1).media.pause();
        await streamTrack({ job_id: 'bad', title: 'Broken file', streamable: true });
      });
      await expect(a).toHaveAttribute('data-ready', 'false');
      await expect(a.locator('.mix-deck-status')).toContainText('illisible');
      await page.evaluate(() => streamTrack(libraryState.tracks[0]));
      await page.waitForFunction(() => Mixer.currentPlayback.playing);
      await page.evaluate(() => showAuth());
      await page.waitForFunction(() => __contexts[0].state === 'closed');
      expect(await page.evaluate(() => __contexts[0].sources.every(s => s.media.paused && !s.media.hasAttribute('src')))).toBe(true);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: audio audible in ${Math.round(startup)}ms before full transfer/analysis; background metadata, range seek, two decks and logout OK`);
    }
  } finally {
    await browser.close();
    for (const finish of pending) finish();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
