const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      let tracks = ['done', 'failed', 'pending', 'running'].map((state, i) => ({
        id: `20260908T190000Z-0000000${i}`, job_id: `20260908T190000Z-0000000${i}`,
        title: `Morceau ${state}`, artist: 'Artiste test', provider: 'qobuz', state,
        streamable: state === 'done', source_url: 'https://play.qobuz.com/track/789', tags: [],
      }));
      const deleted = [];
      let fail = false;
      await page.route('**/api/status', route => route.fulfill({ json: { active: {}, counts: {}, recent: tracks } }));
      await page.route('**/api/history*', route => route.fulfill({ json: { history: tracks } }));
      await page.route('**/api/library', route => route.fulfill({ json: {
        tracks: tracks.filter(t => t.state === 'done'),
        playlists: [{ id: 'playlist', name: 'Playlist test', track_ids: tracks.map(t => t.id) }],
        tags: [{ id: 'tag', name: 'Tag test', color: '#00d79d', track_ids: tracks.map(t => t.id) }], folders: [], imports: [],
      } }));
      await page.route('**/api/library/*', route => {
        if (fail) return route.fulfill({ status: 500, json: { error: 'Suppression impossible' } });
        const id = route.request().url().split('/').pop();
        deleted.push(id);
        tracks = tracks.filter(t => t.id !== id);
        return route.fulfill({ json: { removed: true } });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture', user: { email: 'fixture@example.com' } }; showApp(); });
      await expect(page.locator('#library-tracks .track-delete')).toHaveCount(4);
      await page.waitForFunction(() => queueRefreshPromise === null);
      await page.locator('#history-toggle').click();
      await page.waitForFunction(() => queueRefreshPromise === null);
      await expect(page.locator('#recent-jobs .track-delete')).toHaveCount(4);
      await page.locator('#recent-jobs .track-delete').first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: `/work/track-delete-history-${engine.name()}-${width}.png`, fullPage: true });
      await page.locator('#recent-jobs .track-delete').first().click();
      await page.locator('#action-dialog-cancel').click();
      await page.locator('.library-nav-content [data-collection=""]').click();
      for (const view of ['playlist', 'tag']) {
        await page.evaluate(view => { selectedPlaylistId = view === 'playlist' ? 'playlist' : ''; selectedTagId = view === 'tag' ? 'tag' : ''; renderLibrary(); }, view);
        await expect(page.locator('#library-tracks .track-delete').first()).toBeVisible();
      }
      await page.evaluate(() => { selectedPlaylistId = selectedTagId = ''; renderLibrary(); });
      if (engine === chromium) {
        await expect(page.locator('#library-tracks .track-delete')).toHaveCount(4);
        await page.locator('.library-track[data-state="done"] .track-delete').click();
        await page.locator('#action-dialog-cancel').click();
        await page.screenshot({ path: `/work/track-delete-library-${width}.png`, fullPage: true });
      }
      await page.screenshot({ path: `/work/track-delete-${engine.name()}-${width}.png`, fullPage: true });
      const failed = page.locator('.library-track[data-state="failed"] .track-delete');
      await failed.click();
      await expect(page.locator('#action-dialog')).toBeVisible();
      await page.screenshot({ path: `/work/track-delete-dialog-${engine.name()}-${width}.png`, fullPage: true });
      await page.locator('#action-dialog-cancel').click();
      expect(deleted).toHaveLength(0);
      await failed.click();
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('.library-track[data-state="failed"]')).toHaveCount(0);
      await page.evaluate(() => refreshQueue());
      await expect(page.locator('.library-track[data-state="failed"]')).toHaveCount(0);
      fail = true;
      await page.locator('.library-track[data-state="pending"] .track-delete').click();
      await page.locator('#action-dialog-submit').click();
      await expect(page.locator('#toast')).toContainText('Suppression impossible');
      await expect(page.locator('.library-track[data-state="pending"] .track-delete')).toBeEnabled();
      fail = false;
      for (const state of ['pending', 'running', 'done']) {
        await page.locator(`.library-track[data-state="${state}"] .track-delete`).click();
        await page.locator('#action-dialog-submit').click();
        await expect(page.locator(`.library-track[data-state="${state}"]`)).toHaveCount(0);
      }
      expect(deleted).toHaveLength(4);
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${engine.name()} ${width}: all states, collections, cancel, failure and deletion OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
