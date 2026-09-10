const { chromium, firefox, expect } = require('@playwright/test');

(async () => {
  const engine = process.env.MIXER_BROWSER === 'firefox' ? firefox : chromium;
  const browser = await engine.launch();
  try {
    for (const width of [320, 390, 701, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.MIXER_URL || 'http://127.0.0.1:8082');
      await page.waitForFunction(() => window.Mixer && publicConfig !== null);
      await page.evaluate(() => {
        session = { access_token: 'fixture' };
        document.querySelector('#auth-view').hidden = true;
        document.querySelector('#app-view').hidden = false;
        libraryState = { tracks: [], playlists: [], tags: [] }; renderLibrary();
      });
      const mixer = page.locator('#mixer-nav'), connection = page.locator('#mix-connect');
      const a = page.locator('.mix-deck[data-deck="0"]'), b = page.locator('.mix-deck[data-deck="1"]');
      const initial = await mixer.boundingBox(), first = await a.boundingBox();
      const center = box => box.y + box.height / 2;
      expect(center(initial)).toBe(center(first));
      await expect(page.locator('.dock-mode button:visible')).toHaveCount(1);
      await expect(page.locator('#mix-controls-toggle')).toHaveCount(0);
      await expect(connection).toBeHidden();
      for (let i = 0; i < 2; i++) {
        await mixer.click();
        await expect(b).toBeVisible(); await expect(connection).toBeVisible();
        await expect(page.locator('.dock-mode button:visible')).toHaveCount(2);
        expect(await mixer.boundingBox()).toEqual(initial);
        expect(await b.boundingBox()).toEqual(first);
        const gear = await connection.boundingBox(), upper = await a.boundingBox();
        expect(center(gear)).toBe(center(upper));
        expect(gear.x + gear.width / 2).toBe(initial.x + initial.width / 2);
        await connection.click();
        await expect(page.locator('#mix-connection')).toBeVisible();
        await mixer.click();
        await expect(page.locator('#mix-connection')).toBeHidden();
        await expect(connection).toBeHidden(); await expect(b).toBeHidden();
        expect(await mixer.boundingBox()).toEqual(initial);
      }
      await page.screenshot({ path: `/work/dock-buttons-normal-${engine.name()}-${width}.png` });
      await mixer.click();
      await page.screenshot({ path: `/work/dock-buttons-mix-${engine.name()}-${width}.png` });
      expect(errors).toEqual([]);
      console.log(`${engine.name()} ${width}: connection in former settings slot, stable Mixer button and panel dismissal OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
