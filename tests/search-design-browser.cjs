const { chromium, firefox, expect } = require('@playwright/test');
const fs = require('node:fs');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [1440, 1100, 768, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [], downloads = [];
      page.on('pageerror', error => errors.push(error.message));
      const coverPath = process.env.COVER_FIXTURE || '/work/search-cover.jpg';
      const hasCover = fs.existsSync(coverPath);
      await page.route('**/fixture/cover.jpg', route => route.fulfill({ contentType: hasCover ? 'image/jpeg' : 'image/png', body: hasCover ? fs.readFileSync(coverPath) : Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: { tracks: [], playlists: [], tags: [], folders: [] } });
        if (path === '/api/status') return route.fulfill({ json: { active: { tiddl: true, beatport: true, qobuz: true }, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/download') { downloads.push(route.request().postDataJSON()); return route.fulfill({ json: { job_id: 'downloaded' } }); }
        if (path === '/api/search') {
          const { provider } = route.request().postDataJSON();
          return route.fulfill({ json: { results: Array.from({ length: 3 }, (_, i) => ({
            provider, platform: provider, kind: 'track', title: ['Forbidden Voices', 'A Very Long Title With An Extended Club Version And A Guest Remix', 'Gucci (Extended Mix)'][i],
            artist: i === 1 ? 'Artist With A Particularly Long Name & Guest Artist' : 'Odymel & Funk Tribu',
            album: 'Forbidden Voices', label: i === 1 ? '' : 'Disaster Records', mix: i === 2 ? 'Extended Mix' : '', quality: 'FLAC',
            bpm: provider === 'beatport' ? 150 : '', key: provider === 'beatport' ? 'Dm' : '', duration: '3:20',
            cover_url: i === 1 ? '' : '/fixture/cover.jpg', source_url: `https://example.com/${provider}/${i}`, download_url: `https://example.com/${provider}/${i}`,
          })) } });
        }
        errors.push(path); return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); });
      await page.locator('#search-nav').click();
      await page.locator('#search-input').fill('Odymel');
      await page.locator('#search-input').press('Enter');
      await expect(page.locator('.result-card')).toHaveCount(9);
      await expect(page.locator('.result-details .result-info')).toHaveCount(9);
      for (const label of await page.locator('.result-details .result-info > span').allTextContents()) expect(label).toBe('Durée');
      await expect(page.locator('#search-button svg')).toHaveCount(1);
      await expect(page.locator('.download-button svg')).toHaveCount(9);
      for (const cover of await page.locator('.result-cover:has(img)').all()) {
        await cover.scrollIntoViewIfNeeded();
        await expect(cover).toHaveAttribute('data-loaded', 'true');
      }
      await page.evaluate(() => { document.querySelector('#search-view').scrollTop = 0; });
      await expect(page.locator('.result-cover[data-loaded="true"]')).toHaveCount(6);
      expect(await page.locator('#results-title').count()).toBe(0);
      const layout = await page.evaluate(() => {
        const q = s => document.querySelector(s).getBoundingClientRect();
        return { gap: q('.platform-grid').top - q('.search-box').bottom,
          heights: [...document.querySelectorAll('.result-card')].map(el => el.getBoundingClientRect().height),
          overflow: document.documentElement.scrollWidth > innerWidth,
          actionsFit: [...document.querySelectorAll('.result-card')].every(card => {
            const bounds = card.getBoundingClientRect();
            return [...card.querySelectorAll('button, a, h4, .result-details')].every(el => {
              if (!el.getClientRects().length) return true;
              const box = el.getBoundingClientRect(); return box.left >= bounds.left && box.right <= bounds.right;
            });
          }) };
      });
      expect(layout.gap).toBeLessThanOrEqual(1);
      expect(layout.gap).toBeGreaterThanOrEqual(0);
      expect(Math.max(...layout.heights) - Math.min(...layout.heights)).toBeLessThan(1);
      expect(layout.overflow).toBe(false);
      expect(layout.actionsFit).toBe(true);
      await page.screenshot({ path: `/work/search-design-${engine.name()}-${width}.png` });
      const download = page.locator('#tiddl-results .download-button').first();
      await download.click();
      await expect.poll(() => downloads.length).toBe(1);
      await expect(download).toHaveText('Terminé');
      await page.evaluate(() => clearInterval(queueTimer));
      for (const [state, label] of [['pending', 'En attente'], ['running', 'En cours'], ['done', 'Terminé'], ['failed', 'Réessayer']]) {
        await page.evaluate(state => updateDownloadButtons([{ id: 'downloaded', state }]), state);
        await expect(download).toHaveText(label);
        await expect(download.locator('svg')).toHaveCount(1);
      }
      await expect(page.locator('#import-playlist-button')).toHaveCount(0);
      await expect(page.locator('#import-panel')).toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(page.locator('#results-section')).toBeHidden();
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: uniform search, covers, controls and statuses passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
