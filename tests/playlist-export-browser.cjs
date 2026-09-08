const { chromium, firefox, expect } = require('@playwright/test');
const fs = require('node:fs/promises');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, acceptDownloads: true });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let result = { name: 'Set: soir/2026', links: ['https://listen.tidal.com/track/1', 'https://open.qobuz.com/track/2'], skipped: 1 };
      await page.route('**/api/playlists/p/export', route => route.fulfill({ json: result }));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = { folders: [], tags: [], tracks: [], playlists: [{ id: 'p', name: 'Set: soir/2026', track_ids: ['one', 'two', 'three'] }] };
        renderLibrary();
      });
      const button = page.getByRole('button', { name: 'Exporter les liens téléchargés : Set: soir/2026', exact: true });
      await expect(button.locator('svg')).toBeVisible();
      const downloadEvent = page.waitForEvent('download');
      await button.click();
      const download = await downloadEvent;
      expect(download.suggestedFilename()).toBe('playlist-Set_ soir_2026.txt');
      expect(await fs.readFile(await download.path(), 'utf8')).toBe(result.links.join('\n') + '\n');
      await expect(page.locator('.toast').last()).toContainText('1 morceau(x)');
      result = { name: 'Vide', links: [], skipped: 0 };
      let extraDownloads = 0;
      page.on('download', () => extraDownloads++);
      await button.click();
      await expect(page.locator('.toast').last()).toContainText('Aucun lien');
      await expect(button).toBeEnabled();
      expect(extraDownloads).toBe(0);
      const controls = await page.locator('.playlist-entry > button').evaluateAll(buttons => buttons.map(b => { const r = b.getBoundingClientRect(); return { left: r.left, right: r.right }; }));
      for (let i = 1; i < controls.length; i++) expect(controls[i].left).toBeGreaterThanOrEqual(controls[i - 1].right);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/work/playlist-export-${engine.name()}-${width}.png`, fullPage: true });
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: TXT download, safe filename, empty playlist, layout OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
