const { chromium, firefox, expect } = require('@playwright/test');
const http = require('node:http');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="playlist.zip"' });
    response.end(Buffer.from('504b0506000000000000000000000000000000000000', 'hex'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const zipUrl = `http://127.0.0.1:${server.address().port}/playlist.zip`;
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, acceptDownloads: true });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let empty = false;
      await page.route('**/api/playlists/p/download-ticket', route => route.fulfill(empty
        ? { status: 400, json: { error: 'Aucun fichier disponible dans cette playlist.' } }
        : { json: { url: zipUrl, count: 2, skipped: 1 } }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = { folders: [], tags: [], tracks: [], playlists: [
          { id: 'p', name: 'My playlist', track_ids: ['one', 'two', 'three'] },
        ] };
        selectedPlaylistId = 'p'; renderLibrary();
      });
      const button = page.getByRole('button', { name: 'Télécharger toute la playlist', exact: true });
      await page.locator('#collection-menu > summary').click();
      await expect(button.locator('svg')).toBeVisible();
      await button.click();
      await expect(page.locator('#action-dialog')).toContainText('1 son(s)');
      await page.locator('#action-dialog-cancel').click();
      await page.locator('#collection-menu > summary').click();
      await button.click();
      const event = page.waitForEvent('download');
      await page.locator('#action-dialog-submit').click();
      const download = await event;
      expect(download.suggestedFilename()).toBe('playlist.zip');
      expect(await download.failure()).toBeNull();
      empty = true;
      await page.locator('#collection-menu > summary').click();
      await button.click();
      await expect(page.locator('.toast').last()).toContainText('Aucun fichier');
      await page.locator('#collection-menu > summary').click();
      await expect(button).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/playlist-download-${engine.name()}-${width}.png` });
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: ZIP menu, confirmation, cancellation, download and empty state passed`);
    }
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
