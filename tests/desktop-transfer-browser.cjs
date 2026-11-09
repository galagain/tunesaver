const { chromium, expect } = require('@playwright/test');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let imported = false;
      for (const asset of ['desktop.js', 'desktop.css']) {
        await page.route(`**/${asset}`, route => route.fulfill({ contentType: asset.endsWith('.js') ? 'application/javascript' : 'text/css', body: fs.readFileSync(`/repo/web/${asset}`) }));
      }
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/desktop/account-import') {
          expect(route.request().postDataJSON().format).toBe('tunesaver-accounts-v1');
          imported = true;
          return route.fulfill({ json: { imported: ['beatport'], skipped: [] } });
        }
        if (path === '/api/desktop/accounts') return route.fulfill({ json: { accounts: {
          tiddl: { connected: false }, beatport: { connected: imported }, qobuz: { connected: false }
        } } });
        return route.fulfill({ json: { tracks: [], playlists: [], imports: [], history: [], threads: [], counts: {} } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8096');
      await page.waitForFunction(() => publicConfig !== null);
      await page.addStyleTag({ url: '/desktop.css' });
      await page.addScriptTag({ url: '/desktop.js' });
      await page.evaluate(() => {
        publicConfig.desktop = true; document.body.classList.add('desktop');
        session = { access_token: 'fixture', user: { email: 'Sur ce Mac' } }; showApp(); clearInterval(queueTimer);
        window.dispatchEvent(new Event('desktop-ready'));
      });
      await expect(page.locator('#desktop-accounts')).toBeVisible();
      await expect(page.getByRole('link', { name: 'Récupérer mes connexions TuneSaver' })).toHaveAttribute('href', 'https://tunesaver.galagain.com/auth/');
      await page.locator('#desktop-account-file').setInputFiles({ name: 'accounts.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ format: 'tunesaver-accounts-v1', accounts: { beatport: { beatport_username: 'fixture', beatport_password: 'fixture' } } })) });
      await expect(page.locator('#desktop-import-result')).toContainText('1 compte(s) importé(s)');
      await expect(page.locator('[data-provider="beatport"] .desktop-account-state')).toHaveText('Connecté');
      await page.locator('#desktop-account-file').setInputFiles({ name: 'bad.json', mimeType: 'application/json', buffer: Buffer.from('invalid') });
      await expect(page.locator('#desktop-error')).not.toBeEmpty();
      await expect(page.locator('#desktop-import-result')).toBeEmpty();
      expect(await page.locator('#desktop-accounts').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      await page.screenshot({ path: `/work/desktop-transfer-${width}.png` });
      await page.locator('#desktop-close').click();
      await expect(page.locator('#playback-dock')).toBeVisible();
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: shared interface, quick transfer, import and invalid file states OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
