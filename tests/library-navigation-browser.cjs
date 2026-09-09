const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [2560, 1440, 1360, 1100, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const tracks = Array.from({ length: 70 }, (_, i) => ({ job_id: `track-${i}`, title: `Track ${i + 1}`, artist: 'Studio Session', provider: 'tiddl', streamable: true, tags: [], mix_analysis: { status: 'done', bpm: 150, camelot: '8A' } }));
      const library = { tracks, folders: [], imports: [], orders: {}, playlists: Array.from({ length: 45 }, (_, i) => ({ id: `p${i}`, name: `Playlist ${i + 1}`, track_ids: tracks.map(t => t.job_id) })), tags: [{ id: 't', name: 'Techno', color: '#aacc55', track_ids: tracks.map(t => t.job_id) }] };
      await page.addInitScript(() => {
        window.__port = { id: 'flx4', name: 'DDJ-FLX4', type: 'input', state: 'connected', connection: 'closed', async open() { this.connection = 'open'; }, async close() { this.connection = 'closed'; } };
        Object.defineProperty(navigator, 'requestMIDIAccess', { configurable: true, value: async () => ({ inputs: new Map([['flx4', __port]]), outputs: new Map() }) });
        window.__send = data => __port.onmidimessage?.({ data });
      });
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path.startsWith('/api/search')) return route.fulfill({ json: { results: [] } });
        errors.push(path); return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
      await expect(page.locator('.library-track')).toHaveCount(70);
      await expect(page.locator('#search-view')).toBeHidden();
      await page.locator('#search-nav').click();
      await expect(page.locator('#search-view')).toBeVisible();
      await expect(page.locator('.library-section')).toBeHidden();
      await page.locator('#search-input').fill('Test query');
      await page.locator('#search-input').press('Enter');
      await expect(page.locator('#search-button')).toBeEnabled();
      await expect(page.locator('#results-section')).toBeVisible();
      await page.screenshot({ path: `/work/search-view-${engine.name()}-${width}.png` });
      await page.locator('[data-playlist-id=""]').click();
      await expect(page.locator('#search-view')).toBeHidden();
      await expect(page.locator('.library-section')).toBeVisible();
      const geometry = () => page.evaluate(async () => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return Object.fromEntries(['.workspace', '.library-sidebar', '.workspace-main', '#library-search', '#library-tracks', '.library-nav-content', '.library-track', '#search-view', '#search-form', '#results-section', '#mixer-nav'].map(selector => {
          const element = document.querySelector(selector), box = element.getBoundingClientRect();
          return [selector, { x: box.x, y: box.y, width: box.width, height: box.height, scrollTop: element.scrollTop }];
        }));
      });
      await geometry();
      await page.evaluate(() => {
        document.querySelector('#library-tracks').scrollTop = 420;
        document.querySelector('.library-nav-content').scrollTop = 180;
      });
      const normal = await geometry();
      await page.screenshot({ path: `/work/stable-normal-${engine.name()}-${width}.png` });
      await page.locator('#mixer-nav').click();
      expect(await geometry()).toEqual(normal);
      await page.screenshot({ path: `/work/stable-mix-${engine.name()}-${width}.png` });
      await page.locator('#mixer-nav').click();
      expect(await geometry()).toEqual(normal);
      await page.locator('#mixer-nav').click();
      await page.evaluate(() => document.querySelector('#mix-controls').showPopover());
      await page.locator('#mix-connect').click();
      await expect(page.locator('#mix-midi-status')).toContainText('connect');
      await page.evaluate(() => document.querySelector('#mix-controls').hidePopover());

      const visible = async (selector, paneSelector) => {
        const geometry = await page.evaluate(({ selector, paneSelector }) => {
          const item = document.querySelector(selector).getBoundingClientRect();
          const pane = document.querySelector(paneSelector).getBoundingClientRect();
          return { itemTop: item.top, itemBottom: item.bottom, paneTop: pane.top, paneBottom: pane.bottom,
            header: document.querySelector('.site-header').getBoundingClientRect().bottom,
            dock: document.querySelector('#playback-dock').getBoundingClientRect().top, scroll: window.scrollY,
            overflow: document.documentElement.scrollWidth > innerWidth };
        }, { selector, paneSelector });
        expect(geometry.itemTop).toBeGreaterThanOrEqual(geometry.paneTop - 1);
        expect(geometry.itemBottom).toBeLessThanOrEqual(geometry.paneBottom + 1);
        expect(geometry.paneTop).toBeGreaterThanOrEqual(geometry.header);
        expect(geometry.paneBottom).toBeLessThanOrEqual(geometry.dock);
        expect(geometry.scroll).toBe(0);
        expect(geometry.overflow).toBe(false);
      };
      const turn = async (delta, times) => page.evaluate(({ delta, times }) => {
        for (let i = 0; i < times; i++) __send([0xb6, 0x40, delta > 0 ? 1 : 127]);
      }, { delta, times });
      await turn(1, 75);
      await expect(page.locator('.library-track[data-selected="true"]')).toHaveAttribute('data-job-id', 'track-69');
      await visible('.library-track[data-selected="true"]', '#library-tracks');
      await page.screenshot({ path: `/work/navigation-bottom-${engine.name()}-${width}.png` });
      await turn(-1, 75);
      await expect(page.locator('.library-track[data-selected="true"]')).toHaveAttribute('data-job-id', 'track-0');
      await visible('.library-track[data-selected="true"]', '#library-tracks');
      await page.locator('.library-track[data-selected="true"] .track-menu summary').click();
      const menu = page.locator('.track-menu[open] .workspace-menu-items');
      await expect(menu).toBeVisible();
      await expect.poll(() => menu.evaluate(panel => {
        const box = panel.getBoundingClientRect();
        return box.top >= document.querySelector('.site-header').getBoundingClientRect().bottom
          && box.bottom <= document.querySelector('#playback-dock').getBoundingClientRect().top
          && panel.contains(document.elementFromPoint(box.x + box.width / 2, box.y + 20));
      })).toBe(true);
      await page.keyboard.press('Escape');
      await page.evaluate(() => __send([0x96, 0x41, 127]));
      await turn(1, 50);
      await visible('[data-cursor="true"]', '.library-nav-content');
      await turn(-1, 50);
      await visible('[data-cursor="true"]', '.library-nav-content');
      await page.evaluate(() => __send([0x96, 0x41, 127]));
      await turn(1, 75);
      await page.locator('#mixer-nav').click();
      await page.locator('#mixer-nav').click();
      await page.waitForTimeout(100);
      await visible('.library-track[data-selected="true"]', '#library-tracks');
      await page.locator('#search-nav').click();
      await expect(page.locator('#search-input')).toHaveValue('Test query');
      await expect(page.locator('#results-section')).toBeVisible();
      const searchGeometry = await geometry();
      await page.locator('#mixer-nav').click();
      expect(await geometry()).toEqual(searchGeometry);
      await page.locator('#mixer-nav').click();
      expect(await geometry()).toEqual(searchGeometry);
      await turn(-1, 1);
      await expect(page.locator('#search-view')).toBeHidden();
      await visible('.library-track[data-selected="true"]', '#library-tracks');
      await page.screenshot({ path: `/work/navigation-final-${engine.name()}-${width}.png` });
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: identical normal/mix geometry and scroll, search navigation and FLX4 scrolling passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
