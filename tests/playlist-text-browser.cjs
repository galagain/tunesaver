const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs/promises');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}, acceptDownloads: true});
      const errors = [], submissions = [];
      const document = {format: 'tunesaver-playlist', version: 1, name: 'Mon set', tracks: [
        {title: 'Song', artist: 'Artist', duration_seconds: 185, url: 'https://tidal.com/track/1'}]};
      const library = {tracks: [], playlists: [{id: 'p', name: document.name, track_ids: []}], imports: [], tags: [], folders: [], orders: {}};
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({json: library});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        if (path === '/api/history') return route.fulfill({json: {history: []}});
        if (path === '/api/playlists/p/export') {
          expect(new URL(route.request().url()).searchParams.get('format')).toBe('tunesaver');
          return route.fulfill({json: {name: document.name, links: [document.tracks[0].url], skipped: 0, text: JSON.stringify(document)}});
        }
        if (path === '/api/playlist-imports') {
          const payload = route.request().postDataJSON(); submissions.push(payload);
          expect(payload.auto_download).toBe(true);
          return route.fulfill({status: 202, json: payload.document ? {id: 'import', playlist_id: 'p'} : {kind: 'track', job_id: 'job', playlist_id: null}});
        }
        errors.push(path); return route.fulfill({status: 500});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      await page.locator('[data-playlist-id="p"]').click();
      await page.locator('#collection-menu > summary').click();
      const downloadEvent = page.waitForEvent('download');
      await page.getByRole('button', {name: 'Exporter la playlist (.txt) : Mon set', exact: true}).click();
      const download = await downloadEvent;
      const contents = await fs.readFile(await download.path(), 'utf8');
      expect(JSON.parse(contents)).toEqual(document);
      const drop = async (text, name = 'playlist.txt') => page.evaluate(({text, name}) => {
        const transfer = new DataTransfer(); transfer.items.add(new File([text], name, {type: 'text/plain'}));
        window.dispatchEvent(new DragEvent('dragover', {dataTransfer: transfer, cancelable: true}));
        window.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, cancelable: true}));
      }, {text, name});
      await drop(contents);
      await expect.poll(() => submissions.length).toBe(1);
      await expect(page.locator('#search-button')).toBeEnabled();
      expect(submissions[0].document).toEqual(document);
      expect(await page.evaluate(() => selectedPlaylistId)).toBe('p');
      await drop('https://tidal.com/track/1\nhttps://play.qobuz.com/track/2\n');
      await expect.poll(() => submissions.length).toBe(3);
      await expect(page.locator('#search-button')).toBeEnabled();
      expect(await page.evaluate(() => selectedPlaylistId)).toBe('');
      await drop('not a playlist');
      await expect(page.locator('#toast')).toContainText('doit contenir un lien');
      expect(submissions.length).toBe(3);
      expect(await page.evaluate(() => document.body.classList.contains('file-drop-active'))).toBe(false);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: TXT export round trip, preserved playlist, bare links, auto download, bad file handled`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
