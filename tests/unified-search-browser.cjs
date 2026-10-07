const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      const imports = [], errors = [];
      const library = {tracks: [], playlists: [], tags: [], folders: [], imports: []};
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/feedback') return route.fulfill({json: {threads: []}});
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        if (path === '/api/history') return route.fulfill({json: {history: []}});
        if (path === '/api/playlist-imports') {
          const {url} = route.request().postDataJSON();
          if (url.includes('invalid.test')) return route.fulfill({status: 400, json: {error: 'Lien non pris en charge.'}});
          imports.push(url);
          await new Promise(resolve => setTimeout(resolve, 150));
          const id = `import-${imports.length}`;
          if (url.includes('/track/') || url === 'https://soundcloud.com/artist/song') {
            return route.fulfill({status: 202, json: {kind: 'track', job_id: id, playlist_id: null}});
          }
          library.playlists.push({id, name: id, track_ids: []});
          library.imports.push({id, playlist_id: id, name: id, state: 'pending', total: 0, queued: 0, processed: 0});
          return route.fulfill({status: 202, json: {id, playlist_id: id}});
        }
        errors.push(path);
        return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await page.locator('#search-nav').click();
      await expect(page.locator('#search-view textarea')).toHaveCount(1);
      await expect(page.locator('#search-form button')).toHaveCount(2);
      await page.screenshot({path: `/work/unified-search-${width}.png`});
      const urls = [
        'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc',
        'https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa',
        'https://open.spotify.com/track/4m2880jivSbbyEGAKfITCa',
        'https://music.apple.com/us/album/random-access-memories/617154241',
        'https://soundcloud.com/delicieuse-musique/sets/jazz-house',
        'https://soundcloud.com/artist/song',
        'https://tidal.com/album/20115556', 'https://tidal.com/track/20115557',
        'https://www.beatport.com/release/album/123',
        'https://www.qobuz.com/fr-fr/album/random-access-memories-daft-punk/0886443927087'
      ];
      for (const [index, url] of urls.entries()) {
        await page.locator('#search-nav').click();
        await page.locator('#search-input').evaluate((input, text) => {
          const clipboardData = new DataTransfer(); clipboardData.setData('text/plain', text);
          input.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true}));
          document.querySelector('#search-form').requestSubmit();
        }, url);
        await expect(page.locator('#search-view')).toBeHidden();
        expect(imports).toHaveLength(index + 1);
        expect(imports[index]).toBe(url);
        if (url.includes('/track/') || url === 'https://soundcloud.com/artist/song') {
          expect(await page.evaluate(() => selectedPlaylistId)).toBe('');
          await expect(page.locator('#library-title')).toHaveText('Mes sons');
        }
      }
      await page.locator('#search-nav').click();
      await page.locator('#search-input').fill('https://invalid.test/playlist');
      await page.locator('#search-input').press('Enter');
      await expect(page.locator('#search-message')).toContainText('Lien non pris en charge');
      await expect(page.locator('#search-button')).toBeEnabled();
      expect(await page.locator('#search-input').evaluate(e => e.readOnly)).toBe(false);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(errors).toEqual([]);
      console.log(`${width}: single field, pasted links with explicit submit, duplicate guard, errors OK`);
      await page.close();
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
