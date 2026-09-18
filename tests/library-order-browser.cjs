const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 }, hasTouch: engine === chromium && width <= 390 });
      const errors = [], writes = [];
      let reject = false;
      page.on('pageerror', error => errors.push(error.message));
      const library = {
        tracks: [
          { job_id: 'a', title: 'Zulu 10', artist: 'Alpha', mix_analysis: { bpm: 150, camelot: '10A' } },
          { job_id: 'b', title: 'Alpha', artist: 'Zulu', mix_analysis: { bpm: 98, camelot: '2A' } },
          { job_id: 'c', title: 'Zulu 2', artist: 'Beta', mix_analysis: { bpm: 140, camelot: '8B' } },
          { job_id: 'd', title: 'Missing', artist: '', state: 'failed', streamable: false },
        ].map(track => ({ streamable: true, tags: [], provider: 'tiddl', ...track })),
        playlists: [{ id: 'p', name: 'Set', track_ids: ['c', 'a', 'b', 'd'] }],
        tags: [{ id: 't', name: 'Techno', color: '#ffaa44', track_ids: ['b', 'c'] }], folders: [], orders: {}, imports: [],
      };
      await page.route('**/api/**', route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/library/order') {
          const payload = request.postDataJSON(); writes.push(payload);
          if (reject) return route.fulfill({ status: 409, json: { error: 'Liste modifiee' } });
          library.orders[payload.scope] = payload.track_ids;
          return route.fulfill({ json: { saved: true } });
        }
        errors.push(path); return route.fulfill({ status: 500 });
      });
      const login = async () => {
        await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
        await page.waitForFunction(() => publicConfig !== null);
        await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
        await expect(page.locator('.library-track')).toHaveCount(4);
      };
      const order = () => page.locator('.library-track').evaluateAll(rows => rows.map(row => row.dataset.jobId));
      const sort = async (by, ids) => {
        await page.locator(`[data-track-sort="${by}"]`).click();
        await expect.poll(order).toEqual(ids);
      };
      await login();
      await expect(page.locator('#library-sort, #library-sort-direction, .library-toolbar')).toHaveCount(0);
      await expect.poll(order).toEqual(['a', 'b', 'c', 'd']);
      await sort('title', ['b', 'd', 'c', 'a']);
      await sort('artist', ['a', 'c', 'b', 'd']);
      await sort('bpm', ['b', 'c', 'a', 'd']);
      await page.locator('[data-track-sort="bpm"]').click();
      await expect.poll(order).toEqual(['a', 'c', 'b', 'd']);
      await sort('key', ['b', 'c', 'a', 'd']);
      await sort('number', ['a', 'b', 'c', 'd']);
      const handle = page.locator('[data-job-id="a"] .track-number');
      await handle.scrollIntoViewIfNeeded();
      const from = await handle.boundingBox(), to = await page.locator('[data-job-id="c"]').boundingBox();
      await page.mouse.move(from.x + 15, from.y + 15); await page.mouse.down();
      await page.mouse.move(from.x + 15, to.y + to.height - 4, { steps: 8 });
      try {
        await expect(page.locator('[data-job-id="c"]')).toHaveAttribute('data-reorder', 'after');
      } catch (error) {
        await page.screenshot({ path: `/work/library-order-drag-failure-${engine.name()}-${width}.png` });
        throw error;
      }
      await page.evaluate(() => { window.heldRow = document.querySelector('[data-job-id="a"]'); renderLibrary(); });
      expect(await page.evaluate(() => heldRow === document.querySelector('[data-job-id="a"]'))).toBe(true);
      await page.mouse.up();
      await expect.poll(order).toEqual(['b', 'c', 'a', 'd']);
      expect(writes.at(-1)).toEqual({ scope: 'library:', track_ids: ['b', 'c', 'a', 'd'] });
      await expect(page.locator('.track-number')).toHaveText(['1', '2', '3', '4']);
      await page.locator('[data-job-id="d"] .track-number').press('Alt+ArrowUp');
      await expect.poll(order).toEqual(['b', 'c', 'd', 'a']);
      await login();
      await expect.poll(order).toEqual(['b', 'c', 'd', 'a']);
      await page.locator('[data-playlist-id="p"]').click();
      await expect.poll(order).toEqual(['c', 'a', 'b', 'd']);
      const search = await page.locator('#library-search').boundingBox();
      const settings = await page.locator('#collection-menu > summary').boundingBox();
      expect(search.x + search.width).toBeLessThanOrEqual(settings.x);
      expect(Math.abs(search.y + search.height / 2 - settings.y - settings.height / 2)).toBeLessThan(1);
      expect(await page.locator('#library-search').evaluate(el => Boolean(el.closest('.library-heading')))).toBe(true);
      await page.screenshot({ path: `/work/library-search-header-${engine.name()}-${width}.png` });
      await page.locator('#library-search').fill('Zulu');
      await expect.poll(order).toEqual(['c', 'a', 'b']);
      await page.locator('[data-job-id="a"] .track-number').press('Alt+ArrowUp');
      await expect.poll(order).toEqual(['a', 'c', 'b']);
      expect(writes.at(-1).track_ids).toEqual(['a', 'c', 'b', 'd']);
      await page.locator('#library-search').fill('');
      await expect.poll(order).toEqual(['a', 'c', 'b', 'd']);
      reject = true;
      await page.locator('[data-job-id="a"] .track-number').press('Alt+ArrowDown');
      await expect(page.locator('#toast')).toContainText('Liste modifiee');
      await expect.poll(order).toEqual(['a', 'c', 'b', 'd']);
      reject = false;
      await page.locator('[data-tag-id="t"]').click();
      await expect.poll(order).toEqual(['b', 'c']);
      await page.locator('[data-job-id="c"] .track-number').press('Alt+ArrowUp');
      await expect.poll(order).toEqual(['c', 'b']);
      expect(writes.at(-1).scope).toBe('tag:t');
      await page.locator('.playlist-filter[data-playlist-id=""]').click();
      await expect.poll(order).toEqual(['b', 'c', 'd', 'a']);
      await expect.poll(order).toEqual(['b', 'c', 'd', 'a']);
      if (engine === chromium && width === 390) {
        const touch = await page.context().newCDPSession(page);
        const source = page.locator('[data-job-id="d"] .track-number');
        await source.scrollIntoViewIfNeeded();
        const start = await source.boundingBox(), end = await page.locator('[data-job-id="b"]').boundingBox();
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: start.x + 15, y: start.y + 15 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x + 15, y: end.y + 5 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await expect.poll(order).toEqual(['d', 'b', 'c', 'a']);
        await touch.detach();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/library-order-${engine.name()}-${width}.png` });
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: sorting, numeric keys/BPM, missing values, drag, failure rollback, filtered moves, scoped persistence and mix OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
