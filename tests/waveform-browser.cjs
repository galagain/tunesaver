const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 800 }, deviceScaleFactor: 2 });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.evaluate(async () => {
        const { Timeline } = await import('/mix-waveforms.js');
        const container = document.createElement('div');
        container.id = 'wave-fixture';
        container.style.cssText = 'position:fixed;inset:20px 10px auto;z-index:9999;background:#101010';
        document.body.append(container);
        // Deliberately sparse extrema expose staircase rendering at high zoom.
        const rate = 4, frames = 400, bytes = new Uint8Array(frames * 5);
        for (let i = 0; i < frames; i++) {
          const amplitude = i % 2 ? 240 : 20;
          bytes.set([255, 40, 20, amplitude, amplitude / 2], i * 5);
        }
        const waveform = { encoding: 'u8-lmhpn', rate, frames, data: btoa(String.fromCharCode(...bytes)) };
        window.deckFixture = {
          letter: 'A', index: 0, ready: true, rate: 1, at: 1,
          analysisData: { analysis: { duration: 100, waveform }, settings: {} },
          position() { return this.at; }, grid() { return { bpm: null, offset: 0 }; },
          seek(at) { this.at = Math.min(100, Math.max(0, at)); },
          hotcues: Array(8).fill(null), loopStart: null, loopEnd: null, looping: false,
        };
        window.timelineFixture = new Timeline(container, deckFixture);
        timelineFixture.canvas.style.height = '160px';
        timelineFixture.draw(1);
      });
      const canvas = page.locator('#wave-fixture canvas');
      const contour = await canvas.evaluate(c => {
        const { data } = c.getContext('2d').getImageData(0, 0, c.width, c.height);
        const heights = new Set();
        for (let x = Math.ceil(c.width * .02); x < c.width * .23; x++) {
          for (let y = 38; y < c.height / 2; y++) {
            const i = (y * c.width + x) * 4;
            if (data[i] > data[i + 1] + 60) { heights.add(y); break; }
          }
        }
        return heights.size;
      });
      expect(contour).toBeGreaterThan(30);
      const before = await canvas.evaluate(c => c.toDataURL());
      const scrolling = await page.evaluate(() => {
        const oldStart = timelineFixture.start, oldTiles = new Map(timelineFixture.tiles);
        deckFixture.at += .01; timelineFixture.draw(1);
        return {
          movement: timelineFixture.start - oldStart,
          reused: [...timelineFixture.tiles].some(([key, tile]) => oldTiles.get(key) === tile),
          center: timelineFixture.start + timelineFixture.span / 2,
        };
      });
      expect(scrolling.movement).toBeCloseTo(.01, 8);
      expect(scrolling.center).toBeCloseTo(1.01, 8);
      expect(scrolling.reused).toBe(true);
      expect(await canvas.evaluate(c => c.toDataURL())).not.toBe(before);
      const box = await canvas.boundingBox();
      await canvas.click({ position: { x: box.width / 2, y: 80 } });
      expect(await page.evaluate(() => deckFixture.at)).toBeCloseTo(1.01, 2);
      await page.evaluate(() => { deckFixture.hotcues[0] = 1.2; timelineFixture.draw(1); });
      await canvas.click({ position: { x: box.width * .69, y: 80 } });
      expect(await page.evaluate(() => deckFixture.at)).toBe(1.2);
      const limits = await page.evaluate(() => {
        deckFixture.at = 0; timelineFixture.draw(4);
        const start = timelineFixture.start;
        deckFixture.at = 100; timelineFixture.draw(4);
        const end = timelineFixture.start;
        deckFixture.at = 5; deckFixture.rate = 1.25; timelineFixture.draw(4);
        for (let i = 0; i < 100; i++) { deckFixture.at = i; timelineFixture.draw(4); }
        return { start, end, span: timelineFixture.span, tiles: timelineFixture.tiles.size,
          maxTiles: Math.ceil(timelineFixture.canvas.width / 512) + 1 };
      });
      expect(limits.start).toBe(-2);
      expect(limits.end).toBe(98);
      expect(limits.span).toBe(5);
      expect(limits.tiles).toBeLessThanOrEqual(limits.maxTiles);
      await page.evaluate(() => { deckFixture.at = 1; deckFixture.rate = 1; timelineFixture.draw(1); });
      await canvas.screenshot({ path: `/work/waveform-${engine.name()}-${width}.png` });
      await page.close();
      console.log(`${engine.name()} ${width}: continuous contour, centered scroll, cue seeking, tempo zoom, bounded tile cache OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
