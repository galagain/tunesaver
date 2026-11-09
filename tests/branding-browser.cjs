const {chromium, expect} = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      await page.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/api/config') return route.continue();
        if (path === '/api/library') return route.fulfill({json: {tracks: [], playlists: [], imports: [], tags: [], folders: [], orders: {}}});
        if (path === '/api/status') return route.fulfill({json: {active: {}, counts: {}, recent: []}});
        return route.fulfill({json: {history: []}});
      });
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8095');
      const iconUrl = await page.locator('link[rel="icon"]').getAttribute('href');
      const icon = await page.request.get(new URL(iconUrl, page.url()).href);
      expect(icon.status()).toBe(200);
      expect(icon.headers()['content-type']).toContain('image/svg+xml');
      await expect(page.locator('.site-header .brand-icon')).toBeVisible();
      await page.waitForFunction(() => publicConfig !== null);
      await page.evaluate(() => {session = {access_token: 'fixture'}; showApp(); clearInterval(queueTimer);});
      const logo = page.locator('.sidebar-brand .brand-icon');
      await expect(logo).toBeVisible();
      expect(await logo.getAttribute('src')).toBe(iconUrl);
      expect(await logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
      const faviconPixels = await logo.evaluate(img => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 16;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, 16, 16);
        const pixels = ctx.getImageData(0, 0, 16, 16).data;
        return { cornerAlpha: pixels[3], visible: Array.from(pixels).filter((v, i) => i % 4 === 3 && v > 128).length };
      });
      expect(faviconPixels.cornerAlpha).toBe(0);
      expect(faviconPixels.visible).toBeGreaterThan(40);
      expect(faviconPixels.visible).toBeLessThan(180);
      const box = await logo.boundingBox();
      expect(box.width).toBe(box.height);
      await page.screenshot({path: `/work/branding-${width}.png`});
      await page.close();
      console.log(`${width}: public favicon and library logo OK`);
    }
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
