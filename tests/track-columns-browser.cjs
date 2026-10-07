const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 1000, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      const tracks = Array.from({ length: 60 }, (_, i) => ({job_id: `job-${i}`, title: `Track ${i + 1}`, artist: 'Artist', provider: 'tiddl', state: 'done',
        mix_analysis: i % 2 ? {} : {bpm: 128.5, camelot: '10A', duration: 408}}));
      const library = { tracks, playlists: [], imports: [], tags: [], folders: [], orders: {} };
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/feedback') return route.fulfill({json: {threads: []}});
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        if (path === '/api/history') return route.fulfill({json: {history: []}});
        errors.push(path); return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await expect(page.locator('.library-track')).toHaveCount(60);
      const check = async () => {
        for (const name of ['bpm', 'key', 'duration']) {
          const header = page.locator(`.library-table-head > .track-${name}`);
          const cell = page.locator(`.library-track > .track-${name}`).first();
          if (width <= 700 || name === 'duration' && width <= 1050) {
            await expect(header).toBeHidden(); await expect(cell).toBeHidden(); continue;
          }
          const a = await header.boundingBox(), b = await cell.boundingBox();
          expect(Math.abs(a.x + a.width / 2 - b.x - b.width / 2)).toBeLessThan(1);
          expect(await header.evaluate(el => getComputedStyle(el).textAlign)).toBe('center');
          expect(await cell.evaluate(el => getComputedStyle(el).textAlign)).toBe('center');
        }
      };
      await check();
      if (width > 700) { await page.locator('[data-track-sort="bpm"]').click(); await check(); }
      await page.screenshot({path: `/work/track-columns-${width}.png`});
      await page.evaluate(() => {libraryState.tracks = libraryState.tracks.slice(0, 2); renderLibrary();});
      await check();
      expect(errors).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.close();
      console.log(`${width}: header/value alignment with scrolling, sorting and short list OK`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
