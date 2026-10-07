const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.prompt = window.confirm = window.alert = () => { throw Error('Native browser dialogs are disabled'); };
      });
      const library = { tracks: [], playlists: [], tags: [], folders: [], imports: [] };
      let writes = 0;
      await page.route('**/api/**', async route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        const method = request.method();
        const respond = json => route.fulfill({ json });
        if (path === '/api/config') return route.continue();
        if (path === '/api/feedback') return route.fulfill({json: {threads: []}});
        if (path === '/api/login') return respond({ access_token: 'fixture', refresh_token: 'fixture-refresh', user: { email: 'buttons@example.com' } });
        if (path === '/api/status') return respond({ counts: {}, active: {}, recent: [] });
        if (path === '/api/library') return respond(library);
        if (path === '/api/history') return respond({ history: [] });
        if (path === '/api/logout') return respond({ ok: true });
        const match = path.match(/^\/api\/(playlists|tags|playlist-folders)(?:\/([^/]+))?$/);
        if (match) {
          const kind = match[1] === 'playlist-folders' ? 'folders' : match[1], id = match[2];
          const singular = kind === 'playlists' ? 'playlist' : kind === 'tags' ? 'tag' : 'folder';
          writes++;
          if (method === 'POST') {
            const item = { id: `${kind}-${writes}`, name: request.postDataJSON().name, track_ids: [], color: '#00cc99' };
            library[kind].push(item);
            return respond({ id: item.id, [singular]: item });
          }
          if (method === 'PATCH') Object.assign(library[kind].find(item => item.id === id), request.postDataJSON());
          if (method === 'DELETE') library[kind] = library[kind].filter(item => item.id !== id);
          return respond({ ok: true });
        }
        errors.push(`Unexpected request: ${method} ${path}`);
        return route.fulfill({ status: 500, json: { error: 'Unexpected test request' } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.locator('#auth-email').fill('buttons@example.com');
      await page.locator('#auth-password').fill('Fixture-only-2026');
      await page.locator('#auth-submit').click();
      await expect(page.locator('#app-view')).toBeVisible();
      const dialog = page.locator('#action-dialog'), input = page.locator('#action-dialog-input');
      for (const [create, row, name] of [['#create-playlist', '.playlist-entry', 'Soiree'], ['#create-tag', '.tag-row', 'House'], ['#create-folder', '.folder-heading', 'Sets']]) {
        await page.locator('#library-menu > summary').click();
        await page.locator(create).click();
        await expect(dialog).toBeVisible();
        await expect(input).toBeFocused();
        await input.fill(name);
        await input.press('Enter');
        await expect(dialog).toBeHidden();
        await expect(page.locator(row)).toHaveCount(1);
        await page.locator(`${row} > button`).click();
        await page.locator('#collection-menu > summary').click();
        await page.locator('#collection-actions .collection-rename:not(.collection-export)').click();
        await expect(input).toHaveValue(name);
        await input.fill(name + ' 2026');
        await page.locator('#action-dialog-submit').click();
        await expect(page.locator(row)).toContainText(name + ' 2026');
      }
      const before = writes;
      await page.locator('.playlist-entry > button').click();
      await page.locator('#collection-menu > summary').click();
      await page.locator('#collection-actions .collection-delete').click();
      await expect(dialog).toBeVisible();
      await expect(input).toBeHidden();
      await expect(page.locator('#action-dialog-cancel')).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(page.locator('.playlist-entry')).toHaveCount(1);
      expect(writes).toBe(before);
      await page.locator('#collection-menu > summary').click();
      await page.locator('#collection-actions .collection-delete').click();
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('.playlist-entry')).toHaveCount(0);
      await page.locator('#library-menu > summary').click();
      await page.locator('#create-playlist').click();
      await input.fill('Un tres long nom de playlist pour verifier la largeur de la fenetre');
      const box = await dialog.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: `/work/dialogs-${engine.name()}-${width}.png`, fullPage: true });
      await page.locator('#action-dialog-cancel').click();
      await expect(page.locator('#library-menu > summary')).toBeFocused();
      await page.locator('#history-toggle').click();
      await expect(page.locator('#history-toggle')).toHaveAttribute('aria-current', 'page');
      await page.evaluate(() => { window.__dialogAnswer = undefined; TuneSaverDialogs.prompt('Mot de passe', '', { type: 'password' }).then(v => { __dialogAnswer = v; }); });
      await expect(input).toHaveAttribute('type', 'password');
      await input.fill('not-stored');
      await page.evaluate(() => showAuth('Session expiree.'));
      await expect(dialog).toBeHidden();
      await page.waitForFunction(() => __dialogAnswer === null);
      await expect(input).toHaveValue('');
      await expect(page.locator('#auth-view')).toBeVisible();
      await page.locator('#auth-toggle').click();
      await expect(page.locator('#auth-toggle')).toHaveText('J’ai déjà un compte');
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: login, create, rename, confirm/cancel, Escape, focus and session cleanup without native dialogs OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
