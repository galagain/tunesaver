const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      const history = Array.from({ length: 60 }, (_, i) => ({ job_id: `track-${i}`, title: `Night session ${i + 1}`, artist: `Studio ${i % 3 + 1}`,
        provider: ['tiddl', 'beatport', 'qobuz'][i % 3], state: ['done', 'failed', 'pending', 'running'][i % 4], created_at: '2026-09-10T12:30:00Z', estimated_savings_cents: 139 }));
      const recent = history.map(track => ({ ...track, id: track.job_id, download_ready: track.state === 'done' }));
      const library = { tracks: [], playlists: [], folders: [], tags: [] };
      let ticketRequested = false;
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/status') return route.fulfill({ json: { recent, counts: {}, active: { tiddl: true, beatport: true, qobuz: true } } });
        if (path === '/api/history') return route.fulfill({ json: { history } });
        if (path === '/api/library') return route.fulfill({ json: library });
        if (path.startsWith('/api/search')) return route.fulfill({ json: { results: [] } });
        if (path.startsWith('/api/admin/')) return route.fulfill({ json: { profiles: [], daily: [] } });
        if (path === '/api/logout') return route.fulfill({ json: {} });
        if (path === '/api/download-ticket') { ticketRequested = true; return route.fulfill({ json: { url: '#download' } }); }
        return route.fulfill({ json: {} });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture', user: { email: 'calvin.long.email@example.com' } };
        showApp();
      });
      await page.waitForFunction(() => queueRefreshPromise === null);
      await expect(page.locator('.site-header')).toBeHidden();
      await expect(page.locator('.sidebar-brand')).toBeVisible();
      await expect(page.locator('.sidebar-footer #user-email')).toHaveText('calvin.long.email@example.com');
      await expect(page.locator('#services')).toHaveCount(0);
      const dock = await page.locator('#playback-dock').boundingBox();
      await page.locator('#history-toggle').click();
      await expect(page.locator('#history-toggle')).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('.library-section')).toBeHidden();
      await expect(page.locator('#search-view')).toBeHidden();
      await expect(page.locator('#queue-section')).toBeVisible();
      await expect(page.locator('.recent-job')).toHaveCount(60);
      for (const state of ['done', 'pending', 'running', 'failed']) await expect(page.locator(`#${state}-count`)).toHaveText('15');
      expect(await page.locator('#playback-dock').boundingBox()).toEqual(dock);
      await page.locator('#history-search').fill('Studio 2');
      await expect(page.locator('.recent-job')).toHaveCount(20);
      await page.locator('#history-state').selectOption('failed');
      await expect(page.locator('.recent-job')).toHaveCount(5);
      await expect(page.locator('.recent-job .job-state').first()).toHaveText('Échec');
      await expect(page.locator('.recent-job .job-state').first()).toBeVisible();
      await page.locator('#history-search').fill('absent');
      await expect(page.locator('#recent-jobs')).toHaveText('Aucun téléchargement ne correspond.');
      await page.locator('#history-search').fill('');
      await page.locator('#history-state').selectOption('');
      await expect(page.locator('.recent-job')).toHaveCount(60);
      await page.screenshot({ path: `/work/history-workspace-${engine.name()}-${width}.png` });
      await page.evaluate(() => document.querySelector('#recent-jobs').scrollTop = 300);
      const scroll = await page.locator('#recent-jobs').evaluate(el => el.scrollTop);
      await page.evaluate(() => refreshQueue());
      expect(await page.locator('#recent-jobs').evaluate(el => el.scrollTop)).toBe(scroll);
      history[8].title = 'Updated title';
      await page.evaluate(() => document.querySelector('[data-job-id="track-8"] .track-delete').focus({ preventScroll: true }));
      await page.evaluate(() => refreshQueue());
      await expect(page.locator('[data-job-id="track-8"] .job-label strong')).toHaveText('Updated title');
      await expect(page.locator('[data-job-id="track-8"] .track-delete')).toBeFocused();
      expect(await page.locator('#recent-jobs').evaluate(el => el.scrollTop)).toBe(scroll);
      await page.locator('#mixer-nav').click();
      await expect(page.locator('#queue-section')).toBeVisible();
      expect(await page.locator('#recent-jobs').evaluate(el => el.scrollTop)).toBe(scroll);
      await page.locator('#mixer-nav').click();
      await page.locator('#recent-jobs .track-delete').first().click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.locator('#action-dialog-cancel').click();
      await page.locator('#recent-jobs .history-download').first().click();
      await expect.poll(() => ticketRequested).toBe(true);
      await expect(page.locator('#recent-jobs .history-download').first()).toBeEnabled();
      await page.locator('#search-nav').click();
      await expect(page.locator('#queue-section')).toBeHidden();
      await expect(page.locator('#search-view')).toBeVisible();
      await page.locator('.sidebar-brand').click();
      await expect(page.locator('.library-section')).toBeVisible();
      await expect(page.locator('#queue-section')).toBeHidden();
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      const account = await page.locator('.sidebar-footer').boundingBox();
      expect(account.y + account.height).toBeLessThanOrEqual(dock.y);
      await page.locator('#logout-button').click();
      await expect(page.locator('#auth-view')).toBeVisible();
      await expect(page.locator('.site-header')).toBeVisible();
      await expect(page.locator('#account-area')).toBeHidden();
      await expect(page.locator('#recent-jobs')).toHaveText('Aucun téléchargement dans ton historique.');
      await page.evaluate(() => { adminSession = { access_token: 'fixture' }; showAdmin(); });
      await expect(page.locator('.site-header #logout-button')).toBeVisible();
      await expect(page.locator('.site-header #user-email')).toHaveText('Admin');
      await page.locator('#logout-button').click();
      await expect(page.locator('#auth-view')).toBeVisible();
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: sidebar account, dedicated history, filters, stable scrolling, download, logout and admin OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
