const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const submissions = [], errors = [];
      let searches = 0, fail = false;
      const library = { tracks: [], playlists: [], imports: [], folders: [], tags: [] };
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/search') {
          searches++;
          const { provider } = route.request().postDataJSON();
          const url = {tiddl: 'https://tidal.com/track/1', beatport: 'https://www.beatport.com/track/song/2', qobuz: 'https://play.qobuz.com/track/3'}[provider];
          return route.fulfill({ json: { results: [{ provider, kind: 'track', title: 'Song', artist: 'Artist', source_url: url, download_url: url }] } });
        }
        if (path === '/api/playlist-imports') {
          if (fail) return route.fulfill({status: 400, json: {error: 'Import impossible'}});
          const payload = route.request().postDataJSON(); submissions.push(payload);
          const id = String(submissions.length);
          library.playlists.push({ id, name: payload.name, track_ids: [] });
          library.imports.push({ id, playlist_id: id, name: payload.name, state: 'pending', total: 0 });
          return route.fulfill({status: 202, json: {id, playlist_id: id}});
        }
        errors.push(path); return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); clearInterval(queueTimer); });
      await page.locator('#search-nav').click();
      await expect(page.locator('#search-playlist-button svg')).toHaveCount(1);
      const urls = ['https://tidal.com/track/1', 'https://tidal.com/album/2'];
      await page.locator('#search-input').evaluate((input, text) => {
        const clipboardData = new DataTransfer(); clipboardData.setData('text/plain', text);
        input.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }));
      }, urls.join('\n'));
      expect(submissions).toHaveLength(0);
      await page.locator('#search-playlist-button').click();
      await page.locator('#action-dialog-cancel').click();
      expect(submissions).toHaveLength(0);
      await page.locator('#search-playlist-button').click();
      await page.locator('#action-dialog-input').fill('Mixed links');
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('#search-view')).toBeHidden();
      expect(submissions[0]).toEqual({ urls, name: 'Mixed links', auto_download: true });
      await page.locator('#search-nav').click();
      await page.locator('#search-input').fill('Artist');
      await page.locator('#search-button').click();
      await expect(page.locator('.result-card')).toHaveCount(3);
      await page.screenshot({ path: `/work/group-search-${width}.png` });
      await page.locator('#search-playlist-button').click();
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('#search-view')).toBeHidden();
      expect(searches).toBe(3);
      expect(submissions[1].urls).toHaveLength(3);
      expect(submissions[1].name).toBe('Artist');
      fail = true;
      await page.locator('#search-nav').click();
      await page.locator('#search-playlist-button').click();
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('#search-message')).toContainText('Import impossible');
      await expect(page.locator('#search-playlist-button')).toBeEnabled();
      await expect(page.locator('#search-button')).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: grouped links, all text results, cancellation, errors, cached search OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
