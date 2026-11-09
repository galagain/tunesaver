const { chromium, expect } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({ json: { tracks: [], playlists: [], imports: [], tags: [], orders: {} } });
        if (path === '/api/status') return route.fulfill({ json: { active: {}, counts: {}, recent: [] } });
        if (path === '/api/history') return route.fulfill({ json: { history: [] } });
        if (path === '/api/feedback') return route.fulfill({ json: { threads: [] } });
        if (path.startsWith('/api/search')) return route.fulfill({ json: { results: [] } });
        errors.push(path);
        return route.fulfill({ status: 500 });
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => { session = { access_token: 'fixture' }; showApp(); clearInterval(queueTimer); });
      await page.locator('#search-nav').click();
      await expect(page.locator('.search-source')).toHaveCount(6);
      await expect(page.locator('.search-input-formats > div')).toHaveCount(3);
      await expect(page.locator('#search-output-title')).toContainText('Lossless');
      await expect(page.locator('.search-output-platforms > div')).toHaveCount(3);
      await expect(page.locator('.search-output')).toContainText('Selon la disponibilité');
      await page.waitForFunction(() => [...document.querySelectorAll('.search-source img')].every(img => img.complete && img.naturalWidth > 0));
      await expect(page.locator('.search-sources summary svg')).toHaveCount(1);
      await page.screenshot({ path: `/work/search-sources-${width}.png` });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.locator('.search-input-formats').scrollIntoViewIfNeeded();
      await expect(page.locator('.search-input-formats')).toBeInViewport();
      if (width === 390) await page.screenshot({ path: '/work/search-sources-mobile-formats.png' });
      await page.locator('.search-output').scrollIntoViewIfNeeded();
      await expect(page.locator('.search-output-platforms')).toBeInViewport();
      if (width === 390) await page.screenshot({ path: '/work/search-sources-mobile-output.png' });
      await page.locator('#search-input').fill('Laurent Garnier');
      await page.locator('#search-button').click();
      await expect(page.locator('#results-section')).toBeVisible();
      await expect(page.locator('.search-sources')).not.toHaveAttribute('open', '');
      await page.locator('.search-sources summary').click();
      await expect(page.locator('.search-source').first()).toBeVisible();
      expect(errors).toEqual([]);
      await page.close();
      console.log(`${width}: all logos loaded, supported formats visible, collapse and search OK`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
